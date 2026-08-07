"""The `ChunkStore` port: stored passages, in domain terms.

Like `GraphStore` and `VectorStore`, a `ChunkStore` is a **projection**. The
event log is the authority; every write here is idempotent because projection
handlers replay.

Every method is tenant-scoped. There is no cross-tenant read, ever.

## There is no search method, and that is deliberate

Retrieval over this corpus -- embeddings, a term-weighted ranker, a public
result type -- is a separate piece of work with its own design. Every one of
its decisions is downstream of what a stored chunk *is*, and guessing a search
signature before the corpus exists is how a port acquires a method its
adapters cannot implement the same way. Adding a method to our own port later
costs nothing.

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
    from redstring.domain.ids import SourceId, TenantId


@runtime_checkable
class ChunkStore(Protocol):
    """Storage for the passages a document was split into."""

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
