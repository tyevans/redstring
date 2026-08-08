"""The `ChunkStore` port: stored passages, in domain terms.

Like `GraphStore` and `VectorStore`, a `ChunkStore` is a **projection**. The
event log is the authority; every write here is idempotent because projection
handlers replay.

Every method is tenant-scoped. There is no cross-tenant read, ever.

## There is a candidate method and no ranked one

The port offers `lexical_candidates`, which answers "which chunks contain
these terms, how often, how long are they, and how many chunks contain each
term". It does **not** rank. Ranking is `domain/chunk_ranking.py`.

The split is by responsibility. Recall and corpus statistics are storage
questions and a database is uniquely good at all of them; relevance is a
domain rule. The obvious alternative -- `ts_rank_cd` over a Postgres
`tsvector` -- was rejected because two adapters ranking by different formulas
mean retrieval quality changes when a caller swaps their store, and the
compliance suite could then no longer assert that two adapters agree. It would
be reduced to checking contracts while the answers diverged silently.

The same reasoning is why the method takes **terms rather than a query
string**: tokenization decides what a term is, so a string argument would hand
that decision back to each adapter and let two stores disagree about it before
any score was computed. See `domain/tokenize.py`.

Semantic search over this corpus -- chunk embeddings, a fused public result
type -- is still a separate piece of work and still has no method here.

## `replace_source` is one operation, not an upsert and a delete

Folding one `DocumentChunked` event must be atomic. Split into an
`upsert_many` followed by a `delete`, a crash between them leaves a corpus
that is neither the old chunking nor the new one -- and once term statistics
are computed over it, leaves them computed over a set that never existed.

An empty `chunks` argument is legal and means "this source now has no
chunks". It is not a no-op guard.

## `chunk_index` is not unique, so ordering needs a tie-break

Content-addressed ids mean a re-chunk landing mid-replay can transiently
produce two chunks claiming index 3. `get_by_source` therefore orders by
`chunk_index` ascending **and then by `id` ascending**; ordering on the index
alone would let two adapters disagree about which comes first, which is
exactly the divergence the compliance suite exists to prevent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redstring.domain.chunk import ChunkId, StoredChunk
    from redstring.domain.chunk_ranking import LexicalCandidates
    from redstring.domain.ids import EntityId, SourceId, TenantId


@runtime_checkable
class ChunkWriter(Protocol):
    """Putting passages in. What a projection needs, and all of it."""

    async def upsert_many(self, chunks: Sequence[StoredChunk]) -> None:
        """Insert or replace chunks, keyed by `(tenant_id, id)`.

        Idempotent, last-write-wins. Chunks may belong to different tenants;
        each is keyed by its own `tenant_id`. Two chunks with the same
        `(tenant_id, id)` in one call leave one row holding the later value --
        the same rule that applies across calls.

        A document's chunking is thousands of rows, so an adapter over a
        database must send this as one statement, not a loop.
        """
        ...

    async def replace_source(
        self,
        source_id: SourceId,
        tenant_id: TenantId,
        chunks: Sequence[StoredChunk],
    ) -> int:
        """Make `chunks` this source's whole chunking; return orphans removed.

        Writes every element and deletes this tenant's chunks of `source_id`
        that are absent from it, as one operation. The return value counts
        only the deletions, so a plain re-delivery of the same event returns
        `0` while a genuine re-chunk returns however many passages the new
        settings replaced.

        Every element must carry this `source_id` and `tenant_id`; a mismatch
        raises `ValueError` rather than being written under the argument's
        values, because silently rewriting a chunk's provenance is how one
        document's entity links end up on another's passage.

        An empty `chunks` empties the source. That is legal.
        """
        ...


@runtime_checkable
class ChunkReader(Protocol):
    """Getting passages back by id, by source, or by entity."""

    async def get(self, chunk_id: ChunkId, tenant_id: TenantId) -> StoredChunk | None:
        """Return the stored chunk, or `None` if this tenant has no such id.

        An unknown id is not an error. The returned chunk is the caller's:
        mutating it -- including appending to `entity_ids` -- cannot change
        stored state.
        """
        ...

    async def get_by_source(self, source_id: SourceId, tenant_id: TenantId) -> list[StoredChunk]:
        """This tenant's chunks of one source, ordered.

        Ordered by `chunk_index` ascending, ties broken by `id` ascending; see
        the module docstring for why the tie-break is not optional. An unknown
        source yields `[]`. The returned chunks are the caller's.
        """
        ...

    async def get_by_entity(self, entity_id: EntityId, tenant_id: TenantId) -> list[StoredChunk]:
        """This tenant's chunks whose `entity_ids` contain `entity_id`.

        A plain read, and deliberately not a filter on the ranked path.
        "Which passages mention this entity" is graph navigation rather than
        relevance, and folding it into a search signature makes one method
        answer two questions under one `k` -- so a caller asking for the top
        five passages about a topic that also mention Ada gets neither
        question answered well.

        Ordered by `source_id`, then `chunk_index`, then `id` ascending: a
        total order, so two adapters cannot disagree. None of the three is
        unique on its own. An unknown entity yields `[]`. The returned chunks
        are the caller's.
        """
        ...


@runtime_checkable
class LexicalCandidateSource(Protocol):
    """Recall and corpus statistics for BM25. One method, by design."""

    async def lexical_candidates(
        self,
        terms: Sequence[str],
        tenant_id: TenantId,
        limit: int,
    ) -> LexicalCandidates:
        """Chunks containing any of `terms`, with the statistics to rank them.

        Takes **terms and not a query string**; see the module docstring for
        why tokenization stays on this side of the port.

        `stats.doc_frequencies` covers **exactly** `terms`. A term no chunk
        contains appears with `0` rather than being omitted -- an absent key
        and a zero are different facts, and a scorer that has to guess which
        it received is a scorer with a latent bug.

        `stats.n_docs` and `stats.avg_doc_length` describe the tenant's whole
        corpus, **not the candidate set**. Statistics computed over the
        survivors of a truncation are statistics of a corpus that never
        existed, and every score derived from them is wrong in a way no
        assertion about the returned chunks can see.

        **Which candidates survive `limit` is contract, not discretion.**
        Ordered by the number of distinct requested terms the chunk contains,
        descending, then by `id` ascending; the first `limit` are returned.
        Without the tie-break two adapters cut different chunks from an
        equally-matching pair, which is a divergence in results.

        The cost is bounded recall, and it is real: a chunk matching one rare
        and highly informative term can be cut before a chunk matching two
        common ones, so a passage that would have ranked first can be absent
        entirely. This is the same shape as the blocking-bounded recall of the
        entity lexical channel, and it belongs in the caller's documentation
        as well as here -- a missing result reads as a bug rather than as a
        declared limit.

        Empty `terms` returns no candidates and zeroed statistics without
        touching the store. A `limit` of `0` returns no candidates but **still
        populates the statistics**. A negative `limit` raises `ValueError`.

        The returned chunks are the caller's; mutating them -- including
        appending to `entity_ids` -- cannot change stored state.
        """
        ...


@runtime_checkable
class ChunkPurge(Protocol):
    """Removing passages wholesale, by source or by tenant."""

    async def delete_by_source(self, source_id: SourceId, tenant_id: TenantId) -> int:
        """Delete every chunk of one source; return how many were removed.

        Idempotent: an unknown source returns `0` rather than raising, so
        replaying a delete is not an error.
        """
        ...

    async def delete_by_tenant(self, tenant_id: TenantId) -> int:
        """Delete every chunk of `tenant_id`; return how many were removed.

        No other tenant is touched.
        """
        ...


@runtime_checkable
class ChunkStore(ChunkWriter, ChunkReader, LexicalCandidateSource, ChunkPurge, Protocol):
    """Storage for the passages a document was split into.

    The whole port, composed from the four capabilities above. Adapters
    implement this and `tests/compliance/chunk_store.py` runs against it.

    **Collaborators should not**, and the number here is worse than the one
    `GraphStore` records about itself. Nine methods; the only first-party
    consumer is `ChunkProjection`, which calls `replace_source` and nothing
    else. One of nine. The other eight exist for library users, which is a
    good reason for the *port* to have them and no reason at all for the
    projection to depend on them -- so `ChunkProjection` is a
    `StoreProjection[ChunkWriter]`.

    The cost was concrete rather than stylistic: `tests/compliance/chunk_store.py`
    is over a thousand lines, so anyone writing a chunk store to serve only the
    corpus-write path owed a read, rank and delete surface they would never
    call.

    Splitting changes nothing for an adapter. `ChunkStore` still names every
    method through its bases, `runtime_checkable` still works, and
    `tests/unit/chunks/test_compliance_coverage.py` still finds every read
    method, because `inspect.getmembers` and `typing.get_type_hints` both walk
    the MRO. See `ports/graph_store.py`, which made this move first.

    `LexicalCandidateSource` is the one to annotate against when writing a
    ranked-retrieval caller: `rank_chunks` needs recall and corpus statistics
    and nothing else, and a caller who has those from an index that is not a
    chunk store at all can supply them.
    """
