"""In-memory `ChunkStore`: the reference adapter.

A real implementation, not a stub. It enforces every contract the port states
-- provenance validation on `replace_source`, the `(chunk_index, id)` ordering,
the orphan count -- because an adapter more permissive than its port is
useless as a reference: tests written against it would pass here and fail on
Postgres.

**Copy on write and on read**, as in `vector/adapters/memory.py`. Handing out
a reference lets a caller mutate stored state by accident, and keeping the
caller's object lets a caller mutate it afterwards. Both directions are closed
with a deep copy -- `entity_ids` is a list, so a shallow copy would leave it
shared.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Self

from redstring.chunks.provenance import reject_foreign_chunks
from redstring.domain.bm25 import CorpusStats
from redstring.domain.chunk_ranking import LexicalCandidate, LexicalCandidates
from redstring.domain.chunk_retrieval import SemanticCandidate
from redstring.domain.exceptions import DimensionMismatchError
from redstring.domain.tokenize import tokenize
from redstring.domain.vector import cosine_score, has_zero_norm

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from redstring.domain.chunk import ChunkId, StoredChunk
    from redstring.domain.ids import EntityId, SourceId, TenantId


class InMemoryChunkStore:
    """A `ChunkStore` backed by plain dictionaries."""

    def __init__(self, *, dimension: int) -> None:
        if dimension <= 0:
            raise ValueError(f"dimension must be positive, not {dimension}")
        self._dimension = dimension
        self._chunks: dict[TenantId, dict[ChunkId, StoredChunk]] = {}

    @property
    def dimension(self) -> int:
        return self._dimension

    async def upsert_many(self, chunks: Sequence[StoredChunk]) -> None:
        # Every element is validated before any is written, so a rejected
        # batch leaves no trace -- the same shape `InMemoryVectorStore.upsert_many`
        # uses for its own `_check`.
        self._reject_zero_norm(chunks)

        for chunk in chunks:
            tenant = self._chunks.setdefault(chunk.tenant_id, {})
            # The key is the *pair*: `chunk.tenant_id` selects the mapping and
            # `chunk.id` the slot, so two tenants holding the same
            # content-addressed id are two rows. Content addressing makes that
            # collision ordinary rather than astronomically unlikely.
            tenant[chunk.id] = chunk.model_copy(deep=True)

    @staticmethod
    def _reject_zero_norm(chunks: Sequence[StoredChunk]) -> None:
        """Cosine is undefined at zero magnitude.

        A stored zero vector would force every later `semantic_candidates`
        call to choose between a silent NaN and a per-row skip that hides a
        caller's bug, so it is rejected here instead -- the same choice
        `InMemoryVectorStore.upsert_many` already makes for `VectorStore`.
        Chunks with no embedding at all are unaffected; only a *stored* zero
        vector is a problem.
        """
        for chunk in chunks:
            if chunk.embedding is not None and has_zero_norm(chunk.embedding):
                raise ValueError(
                    f"chunk {chunk.id!r} has a zero vector; cosine is undefined for it"
                )

    async def get(self, chunk_id: ChunkId, tenant_id: TenantId) -> StoredChunk | None:
        chunk = self._chunks.get(tenant_id, {}).get(chunk_id)
        return None if chunk is None else chunk.model_copy(deep=True)

    async def get_by_source(self, source_id: SourceId, tenant_id: TenantId) -> list[StoredChunk]:
        found = [
            chunk
            for chunk in self._chunks.get(tenant_id, {}).values()
            if chunk.source_id == source_id
        ]
        # `chunk_index` then `id`: the index is not unique under content
        # addressing, and ordering on it alone would let two adapters disagree.
        found.sort(key=lambda chunk: (chunk.chunk_index, chunk.id))
        return [chunk.model_copy(deep=True) for chunk in found]

    async def replace_source(
        self,
        source_id: SourceId,
        tenant_id: TenantId,
        chunks: Sequence[StoredChunk],
    ) -> int:
        reject_foreign_chunks(chunks, source_id, tenant_id)
        # Same write-time guard as `upsert_many`, checked before the orphan
        # delete below -- otherwise a rejected zero-norm element would still
        # have emptied the old chunking first, and `replace_source` is one
        # operation, not a delete-then-maybe-write.
        self._reject_zero_norm(chunks)

        keep = {chunk.id for chunk in chunks}
        tenant = self._chunks.setdefault(tenant_id, {})
        orphans = [
            chunk_id
            for chunk_id, chunk in tenant.items()
            if chunk.source_id == source_id and chunk_id not in keep
        ]
        for chunk_id in orphans:
            del tenant[chunk_id]
        await self.upsert_many(chunks)
        return len(orphans)

    async def get_by_entity(self, entity_id: EntityId, tenant_id: TenantId) -> list[StoredChunk]:
        found = [
            chunk
            for chunk in self._chunks.get(tenant_id, {}).values()
            if entity_id in chunk.entity_ids
        ]
        # A total order: source, then index, then id. Two of the three are
        # not unique on their own, and the port's contract is all three.
        found.sort(key=lambda chunk: (chunk.source_id, chunk.chunk_index, chunk.id))
        return [chunk.model_copy(deep=True) for chunk in found]

    async def lexical_candidates(
        self,
        terms: Sequence[str],
        tenant_id: TenantId,
        limit: int,
    ) -> LexicalCandidates:
        # Rejected before anything else: a rejected call must not have
        # counted a corpus.
        if limit < 0:
            raise ValueError(f"limit must not be negative, got {limit}")

        # Empty `terms` returns zeroed statistics and no candidates without
        # scanning the corpus at all -- checked before anything below touches
        # `self._chunks`, so a rejected call has genuinely not touched the
        # store rather than merely discarded what it found.
        if not terms:
            stats = CorpusStats(n_docs=0, avg_doc_length=0.0, doc_frequencies={})
            return LexicalCandidates(stats=stats, candidates=[])

        tenant_chunks = self._chunks.get(tenant_id, {}).values()

        # Terms are derived from `text` at query time rather than stored as
        # an index. Tokenization is deterministic and ids are
        # content-addressed, so a chunk's terms are a pure function of data
        # already held; a stored index would be a second copy that can
        # drift. (Postgres stores one only because it needs something to
        # seek on -- both adapters compute the same thing by different
        # means, which is what makes their rankings identical.)
        tokenized: dict[ChunkId, tuple[StoredChunk, Counter[str], int]] = {}
        for chunk in tenant_chunks:
            tokens = tokenize(chunk.text)
            tokenized[chunk.id] = (chunk, Counter(tokens), len(tokens))

        n_docs = len(tokenized)
        avg_doc_length = (
            0.0 if n_docs == 0 else sum(length for _, _, length in tokenized.values()) / n_docs
        )

        distinct_terms = set(terms)
        # Built from the *requested* terms, never from the corpus's own
        # vocabulary -- a term no chunk contains must appear with `0` rather
        # than go missing from the mapping.
        doc_frequencies = {
            term: sum(1 for _, counts, _ in tokenized.values() if counts[term] > 0)
            for term in distinct_terms
        }
        stats = CorpusStats(
            n_docs=n_docs, avg_doc_length=avg_doc_length, doc_frequencies=doc_frequencies
        )

        matches = []
        for chunk, counts, length in tokenized.values():
            matched_terms = {term: counts[term] for term in distinct_terms if counts[term] > 0}
            if not matched_terms:
                continue
            matches.append((chunk, length, matched_terms))

        # Statistics were computed over the whole corpus, above; only the
        # candidate *list* is truncated. Ordered by the number of distinct
        # requested terms matched, descending, then by id ascending -- the
        # tie-break, without which two adapters would cut different chunks
        # from an equally-matching pair.
        matches.sort(key=lambda item: (-len(item[2]), item[0].id))
        candidates = [
            LexicalCandidate(
                chunk=chunk.model_copy(deep=True),
                doc_length=length,
                term_frequencies=matched_terms,
            )
            for chunk, length, matched_terms in matches[:limit]
        ]
        return LexicalCandidates(stats=stats, candidates=candidates)

    async def semantic_candidates(
        self,
        vector: Sequence[float],
        tenant_id: TenantId,
        limit: int,
        *,
        min_score: float | None = None,
    ) -> list[SemanticCandidate]:
        # All three guards run before anything touches `self._chunks`,
        # matching `lexical_candidates` and `InMemoryVectorStore._check` --
        # dimension has to be checked before zero-norm, since the latter is
        # only meaningful once the width is known to be right.
        if limit < 0:
            raise ValueError(f"limit must not be negative, got {limit}")
        if len(vector) != self._dimension:
            raise DimensionMismatchError(expected=self._dimension, actual=len(vector))
        if has_zero_norm(vector):
            raise ValueError("a zero vector has no direction; cosine is undefined for it")

        # Chunks with `embedding is None` are not candidates -- skipped
        # rather than scored zero, per the port's contract.
        scored = [
            SemanticCandidate(
                chunk=chunk.model_copy(deep=True), score=cosine_score(vector, chunk.embedding)
            )
            for chunk in self._chunks.get(tenant_id, {}).values()
            if chunk.embedding is not None
        ]

        if min_score is not None:
            scored = [candidate for candidate in scored if candidate.score >= min_score]

        # Total order: score descending, then id ascending -- without the
        # tie-break two adapters would cut different chunks from an equally
        # scoring pair.
        scored.sort(key=lambda candidate: (-candidate.score, candidate.chunk.id))
        return scored[:limit]

    async def delete_by_source(self, source_id: SourceId, tenant_id: TenantId) -> int:
        tenant = self._chunks.get(tenant_id, {})
        doomed = [chunk_id for chunk_id, chunk in tenant.items() if chunk.source_id == source_id]
        for chunk_id in doomed:
            del tenant[chunk_id]
        return len(doomed)

    async def delete_by_tenant(self, tenant_id: TenantId) -> int:
        return len(self._chunks.pop(tenant_id, {}))

    # ------------------------------------------------------------------
    # Lifetime
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Nothing to release: this adapter holds dictionaries the interpreter already owns.

        A no-op rather than an omission. `ChunkStore` declares the release half
        through `AsyncClosable` so a caller can write one lifetime discipline
        against the port whichever adapter is behind it; an adapter that owns
        no driver, pool or client satisfies "release what you hold" by doing
        nothing, and saying so here is more honest than making the caller
        find out by reading the class.
        """

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()
