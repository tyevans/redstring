"""Explainable hybrid retrieval diagnostics and RRF channel attribution."""

from __future__ import annotations

import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from redstring.domain.blocking import blocking_keys_for, query_blocking_keys
from redstring.domain.bm25 import BM25_B, BM25_K1, CorpusStats, inverse_document_frequency
from redstring.domain.chunk_ranking import rank_chunks
from redstring.domain.exceptions import DimensionMismatchError
from redstring.domain.fusion import RRF_K, reciprocal_rank_fusion
from redstring.domain.lexical import PROPERTY_WEIGHT, lexical_score
from redstring.domain.retrieval import RetrievalMode
from redstring.domain.similarity import string_similarity
from redstring.domain.tokenize import tokenize

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from redstring.domain.chunk import ChunkId, StoredChunk
    from redstring.domain.entity import Entity
    from redstring.domain.ids import EntityId, TenantId
    from redstring.ports.chunk_store import ChunkStore
    from redstring.ports.embedding_provider import EmbeddingProvider
    from redstring.ports.graph_store import EntityReader
    from redstring.ports.vector_store import VectorReader

__all__ = [
    "ChunkRetrievalExplanation",
    "EntityRetrievalExplanation",
    "ExplainedChunkMatch",
    "ExplainedEntityMatch",
    "explain_chunk_retrieval",
    "explain_entity_retrieval",
]


def _dominant(sem: float, lex: float) -> str:
    return "semantic" if sem > lex else ("lexical" if lex > sem else "tie")


@dataclass(frozen=True)
class ExplainedEntityMatch:
    """Per-channel rank, raw score, RRF contribution, and field attribution for an entity match."""

    entity: Entity
    fused_rank: int
    fused_score: float
    semantic_rank: int | None
    semantic_score: float | None
    semantic_rrf_contribution: float
    lexical_rank: int | None
    lexical_score: float | None
    lexical_rrf_contribution: float
    matched_blocking_keys: tuple[str, ...]
    best_lexical_field: str | None

    @property
    def dominant_channel(self) -> str:
        """Return ``'semantic'``, ``'lexical'``, or ``'tie'`` based on RRF contribution."""
        return _dominant(self.semantic_rrf_contribution, self.lexical_rrf_contribution)


@dataclass(frozen=True)
class EntityRetrievalExplanation:
    """Diagnostic breakdown for an entity retrieval query."""

    query: str
    mode: RetrievalMode
    query_blocking_keys: tuple[str, ...]
    semantic_candidate_count: int
    lexical_candidate_count: int
    dangling_vector_ids: tuple[EntityId, ...]
    matches: tuple[ExplainedEntityMatch, ...]
    elapsed_ms: float


@dataclass(frozen=True)
class ExplainedChunkMatch:
    """Per-channel rank, raw score, RRF contribution, and BM25 term attribution for a chunk."""

    chunk: StoredChunk
    fused_rank: int
    fused_score: float
    semantic_rank: int | None
    semantic_score: float | None
    semantic_rrf_contribution: float
    lexical_rank: int | None
    lexical_score: float | None
    lexical_rrf_contribution: float
    matched_terms: Mapping[str, int]
    term_bm25_contributions: Mapping[str, float]
    doc_length: int | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "matched_terms", MappingProxyType(dict(self.matched_terms)))
        object.__setattr__(
            self,
            "term_bm25_contributions",
            MappingProxyType(dict(self.term_bm25_contributions)),
        )

    @property
    def dominant_channel(self) -> str:
        """Return ``'semantic'``, ``'lexical'``, or ``'tie'`` based on RRF contribution."""
        return _dominant(self.semantic_rrf_contribution, self.lexical_rrf_contribution)


@dataclass(frozen=True)
class ChunkRetrievalExplanation:
    """Diagnostic breakdown for a chunk retrieval query."""

    query: str
    mode: RetrievalMode
    query_terms: tuple[str, ...]
    corpus_stats: CorpusStats | None
    semantic_candidate_count: int
    lexical_candidate_count: int
    matches: tuple[ExplainedChunkMatch, ...]
    elapsed_ms: float

    @property
    def semantic_candidates_considered(self) -> int:
        """Alias for ``semantic_candidate_count``."""
        return self.semantic_candidate_count


def _best_lexical_field(query: str, entity: Entity) -> str:
    best_field, best_val = "name", string_similarity(query, entity.name)
    norm_val = string_similarity(query, entity.normalized_name)
    if norm_val > best_val:
        best_field, best_val = "normalized_name", norm_val
    for key, value in entity.properties.items():
        if isinstance(value, str):
            prop_val = PROPERTY_WEIGHT * string_similarity(query, value)
            if prop_val > best_val:
                best_field, best_val = f"property:{key}", prop_val
    return best_field


def _term_bm25_breakdown(
    terms: Sequence[str],
    term_frequencies: Mapping[str, int],
    doc_length: int,
    stats: CorpusStats,
) -> dict[str, float]:
    if stats.n_docs == 0:
        return {}
    ratio = 0.0 if stats.avg_doc_length == 0 else doc_length / stats.avg_doc_length
    contribs: dict[str, float] = {}
    for term in sorted(set(terms)):
        freq = term_frequencies.get(term, 0)
        if freq > 0:
            sat = freq * (BM25_K1 + 1)
            norm = freq + BM25_K1 * (1 - BM25_B + BM25_B * ratio)
            contribs[term] = inverse_document_frequency(term, stats) * sat / norm
    return contribs


def _resolve_retrieval_mode(
    mode: RetrievalMode | None, has_semantic: bool, error_context: str
) -> RetrievalMode:
    resolved = mode or (RetrievalMode.HYBRID if has_semantic else RetrievalMode.LEXICAL)
    if resolved is not RetrievalMode.LEXICAL and not has_semantic:
        raise ValueError(f"cannot serve mode {resolved.value!r} without {error_context}")
    return resolved


def _validate_params(query: str, k: int, overfetch: int) -> None:
    if not query.strip():
        raise ValueError("query must not be blank")
    if k < 0:
        raise ValueError(f"k must not be negative, got {k}")
    if overfetch < 1:
        raise ValueError(f"overfetch must be at least 1, got {overfetch}")


async def explain_entity_retrieval(
    query: str,
    tenant_id: TenantId,
    *,
    graph: EntityReader,
    embeddings: EmbeddingProvider | None = None,
    vectors: VectorReader | None = None,
    k: int = 10,
    overfetch: int = 3,
    entity_types: Sequence[str] | None = None,
    mode: RetrievalMode | None = None,
) -> EntityRetrievalExplanation:
    """Run entity retrieval and return channel attribution and RRF diagnostic breakdown."""
    _validate_params(query, k, overfetch)
    if embeddings is not None and vectors is not None and embeddings.dimension != vectors.dimension:
        raise DimensionMismatchError(expected=vectors.dimension, actual=embeddings.dimension)
    resolved_mode = _resolve_retrieval_mode(
        mode, embeddings is not None and vectors is not None, "embeddings and vectors"
    )
    started = time.perf_counter()
    q_keys = tuple(sorted(query_blocking_keys(query)))
    if k == 0:
        return EntityRetrievalExplanation(
            query, resolved_mode, q_keys, 0, 0, (), (), (time.perf_counter() - started) * 1000.0
        )

    per_channel = k * overfetch
    semantic_scores: dict[EntityId, float] = {}
    if (
        resolved_mode in (RetrievalMode.SEMANTIC, RetrievalMode.HYBRID)
        and embeddings is not None
        and vectors is not None
    ):
        [vector] = await embeddings.embed_query([query])
        vec_matches = await vectors.search(
            vector, tenant_id, k=per_channel, entity_types=entity_types
        )
        semantic_scores = {m.entity_id: m.score for m in vec_matches}

    lexical_scores: dict[EntityId, float] = {}
    lexical_entities: dict[EntityId, Entity] = {}
    if resolved_mode in (RetrievalMode.LEXICAL, RetrievalMode.HYBRID):
        groups = await graph.find_by_blocking_keys(q_keys, tenant_id)
        candidates: dict[EntityId, Entity] = {}
        for found in groups.values():
            for entity in found:
                if entity.id in candidates:
                    continue
                if entity_types is not None and entity.entity_type not in entity_types:
                    continue
                candidates[entity.id] = entity
        ranked = sorted(
            ((entity, lexical_score(query, entity)) for entity in candidates.values()),
            key=lambda item: (-item[1], str(item[0].id)),
        )[:per_channel]
        lexical_scores = {entity.id: score for entity, score in ranked}
        lexical_entities = {entity.id: entity for entity, _ in ranked}

    semantic_ranks = {eid: idx + 1 for idx, eid in enumerate(semantic_scores.keys())}
    lexical_ranks = {eid: idx + 1 for idx, eid in enumerate(lexical_scores.keys())}
    fused = reciprocal_rank_fusion([list(semantic_scores), list(lexical_scores)])[:k]
    fused_ids = [eid for eid, _ in fused]
    resolved: dict[EntityId, Entity] = {
        eid: lexical_entities[eid] for eid in fused_ids if eid in lexical_entities
    }
    missing = [eid for eid in fused_ids if eid not in resolved]
    if missing:
        for entity in await graph.get_entities(missing, tenant_id):
            resolved[entity.id] = entity

    dangling_ids = tuple(eid for eid in fused_ids if eid not in resolved)
    q_key_set = set(q_keys)
    explained_matches: list[ExplainedEntityMatch] = []
    for eid, fused_score in fused:
        if eid not in resolved:
            continue
        entity = resolved[eid]
        s_rank, l_rank = semantic_ranks.get(eid), lexical_ranks.get(eid)
        entity_keys = entity.blocking_keys or blocking_keys_for(entity)
        best_field = _best_lexical_field(query, entity) if l_rank is not None else None
        explained_matches.append(
            ExplainedEntityMatch(
                entity=entity,
                fused_rank=len(explained_matches),
                fused_score=fused_score,
                semantic_rank=s_rank,
                semantic_score=semantic_scores.get(eid),
                semantic_rrf_contribution=(1.0 / (RRF_K + s_rank) if s_rank is not None else 0.0),
                lexical_rank=l_rank,
                lexical_score=lexical_scores.get(eid),
                lexical_rrf_contribution=(1.0 / (RRF_K + l_rank) if l_rank is not None else 0.0),
                matched_blocking_keys=tuple(sorted(q_key_set & set(entity_keys))),
                best_lexical_field=best_field,
            )
        )

    return EntityRetrievalExplanation(
        query=query,
        mode=resolved_mode,
        query_blocking_keys=q_keys,
        semantic_candidate_count=len(semantic_scores),
        lexical_candidate_count=len(lexical_scores),
        dangling_vector_ids=dangling_ids,
        matches=tuple(explained_matches),
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
    )


async def explain_chunk_retrieval(
    query: str,
    tenant_id: TenantId,
    *,
    chunks: ChunkStore,
    embeddings: EmbeddingProvider | None = None,
    k: int = 10,
    overfetch: int = 3,
    mode: RetrievalMode | None = None,
) -> ChunkRetrievalExplanation:
    """Run chunk retrieval and return channel attribution and BM25/RRF diagnostic breakdown."""
    _validate_params(query, k, overfetch)
    if embeddings is not None and embeddings.dimension != chunks.dimension:
        raise DimensionMismatchError(expected=chunks.dimension, actual=embeddings.dimension)
    resolved_mode = _resolve_retrieval_mode(mode, embeddings is not None, "embeddings")
    started = time.perf_counter()
    terms = tuple(tokenize(query))
    if k == 0:
        return ChunkRetrievalExplanation(
            query=query,
            mode=resolved_mode,
            query_terms=terms,
            corpus_stats=None,
            semantic_candidate_count=0,
            lexical_candidate_count=0,
            matches=(),
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )

    per_channel = k * overfetch
    semantic_scores: dict[ChunkId, float] = {}
    semantic_chunks: dict[ChunkId, StoredChunk] = {}
    if resolved_mode in (RetrievalMode.SEMANTIC, RetrievalMode.HYBRID) and embeddings is not None:
        [vector] = await embeddings.embed_query([query])
        sem_matches = await chunks.semantic_candidates(vector, tenant_id, per_channel)
        semantic_scores = {m.chunk.id: m.score for m in sem_matches}
        semantic_chunks = {m.chunk.id: m.chunk for m in sem_matches}

    lexical_scores: dict[ChunkId, float] = {}
    lexical_chunks: dict[ChunkId, StoredChunk] = {}
    term_freqs_by_id: dict[ChunkId, dict[str, int]] = {}
    term_bm25_by_id: dict[ChunkId, dict[str, float]] = {}
    doc_len_by_id: dict[ChunkId, int] = {}
    corpus_stats: CorpusStats | None = None
    if resolved_mode in (RetrievalMode.LEXICAL, RetrievalMode.HYBRID):
        candidates = await chunks.lexical_candidates(list(terms), tenant_id, per_channel)
        corpus_stats = candidates.stats
        for cand in candidates.candidates:
            term_freqs_by_id[cand.chunk.id] = {
                t: freq for t, freq in cand.term_frequencies.items() if freq > 0
            }
            term_bm25_by_id[cand.chunk.id] = _term_bm25_breakdown(
                terms, cand.term_frequencies, cand.doc_length, corpus_stats
            )
            doc_len_by_id[cand.chunk.id] = cand.doc_length
        ranked = rank_chunks(list(terms), candidates, per_channel)
        lexical_scores = {r.chunk.id: r.score for r in ranked}
        lexical_chunks = {r.chunk.id: r.chunk for r in ranked}

    semantic_ranks = {cid: idx + 1 for idx, cid in enumerate(semantic_scores.keys())}
    lexical_ranks = {cid: idx + 1 for idx, cid in enumerate(lexical_scores.keys())}
    fused = reciprocal_rank_fusion([list(semantic_scores), list(lexical_scores)])[:k]
    known = {**semantic_chunks, **lexical_chunks}
    explained_matches: list[ExplainedChunkMatch] = []
    for cid, fused_score in fused:
        if cid not in known:
            continue
        s_rank, l_rank = semantic_ranks.get(cid), lexical_ranks.get(cid)
        explained_matches.append(
            ExplainedChunkMatch(
                chunk=known[cid],
                fused_rank=len(explained_matches),
                fused_score=fused_score,
                semantic_rank=s_rank,
                semantic_score=semantic_scores.get(cid),
                semantic_rrf_contribution=(1.0 / (RRF_K + s_rank) if s_rank is not None else 0.0),
                lexical_rank=l_rank,
                lexical_score=lexical_scores.get(cid),
                lexical_rrf_contribution=(1.0 / (RRF_K + l_rank) if l_rank is not None else 0.0),
                matched_terms=term_freqs_by_id.get(cid, {}),
                term_bm25_contributions=term_bm25_by_id.get(cid, {}),
                doc_length=doc_len_by_id.get(cid),
            )
        )

    return ChunkRetrievalExplanation(
        query=query,
        mode=resolved_mode,
        query_terms=terms,
        corpus_stats=corpus_stats,
        semantic_candidate_count=len(semantic_scores),
        lexical_candidate_count=len(lexical_scores),
        matches=tuple(explained_matches),
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
    )
