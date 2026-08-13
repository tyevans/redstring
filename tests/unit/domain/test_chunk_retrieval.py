"""The chunk-retrieval result types."""

from __future__ import annotations

from uuid import uuid4

from redstring.domain.chunk import StoredChunk
from redstring.domain.chunk_retrieval import (
    ChunkRetrievalResult,
    ScoredChunk,
    SemanticCandidate,
)
from redstring.domain.ids import SourceId, TenantId


def _chunk(**overrides: object) -> StoredChunk:
    fields: dict[str, object] = {
        "id": "a" * 64,
        "tenant_id": TenantId(uuid4()),
        "source_id": SourceId("doc-1"),
        "text": "Ada Lovelace wrote the first algorithm.",
        "chunk_index": 0,
        "start_char": 0,
        "end_char": 39,
    }
    fields.update(overrides)
    return StoredChunk(**fields)


def test_a_scored_chunk_distinguishes_unranked_from_zero() -> None:
    """`None` means the channel did not rank it; 0.0 means it ranked it last."""
    unranked = ScoredChunk(chunk=_chunk(), score=0.5, semantic=0.9)
    assert unranked.lexical is None
    scored_zero = ScoredChunk(chunk=_chunk(), score=0.5, semantic=0.9, lexical=0.0)
    assert scored_zero.lexical == 0.0
    assert scored_zero != unranked


def test_component_scores_default_to_none() -> None:
    """Constructed directly -- the defaults are what a caller actually gets."""
    scored = ScoredChunk(chunk=_chunk(), score=0.5)
    assert scored.semantic is None
    assert scored.lexical is None


def test_a_semantic_candidate_pairs_a_chunk_with_its_score() -> None:
    candidate = SemanticCandidate(chunk=_chunk(), score=0.75)
    assert candidate.score == 0.75
    assert candidate.chunk.id == "a" * 64


def test_a_result_keeps_the_query_it_answered() -> None:
    result = ChunkRetrievalResult(query="ada lovelace", matches=[])
    assert result.query == "ada lovelace"
    assert result.matches == []
