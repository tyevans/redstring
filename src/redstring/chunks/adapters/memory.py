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
from typing import TYPE_CHECKING

from redstring.chunks.provenance import reject_foreign_chunks
from redstring.domain.bm25 import CorpusStats
from redstring.domain.chunk_ranking import LexicalCandidate, LexicalCandidates
from redstring.domain.tokenize import tokenize

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redstring.domain.chunk import ChunkId, StoredChunk
    from redstring.domain.ids import EntityId, SourceId, TenantId


class InMemoryChunkStore:
    """A `ChunkStore` backed by plain dictionaries."""

    def __init__(self) -> None:
        self._chunks: dict[TenantId, dict[ChunkId, StoredChunk]] = {}

    async def upsert_many(self, chunks: Sequence[StoredChunk]) -> None:
        for chunk in chunks:
            tenant = self._chunks.setdefault(chunk.tenant_id, {})
            # The key is the *pair*: `chunk.tenant_id` selects the mapping and
            # `chunk.id` the slot, so two tenants holding the same
            # content-addressed id are two rows. Content addressing makes that
            # collision ordinary rather than astronomically unlikely.
            tenant[chunk.id] = chunk.model_copy(deep=True)

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

    async def delete_by_source(self, source_id: SourceId, tenant_id: TenantId) -> int:
        tenant = self._chunks.get(tenant_id, {})
        doomed = [chunk_id for chunk_id, chunk in tenant.items() if chunk.source_id == source_id]
        for chunk_id in doomed:
            del tenant[chunk_id]
        return len(doomed)

    async def delete_by_tenant(self, tenant_id: TenantId) -> int:
        return len(self._chunks.pop(tenant_id, {}))
