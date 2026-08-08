"""Turning candidates into a ranking, and what the ordering guarantees."""

from __future__ import annotations

import uuid

import pytest

from redstring.domain.bm25 import CorpusStats
from redstring.domain.chunk import StoredChunk, chunk_id
from redstring.domain.chunk_ranking import (
    LexicalCandidate,
    LexicalCandidates,
    rank_chunks,
)

TENANT = uuid.uuid4()
SOURCE = "doc-1"


def chunk(text: str, index: int = 0) -> StoredChunk:
    return StoredChunk(
        id=chunk_id(SOURCE, text),
        tenant_id=TENANT,
        source_id=SOURCE,
        text=text,
        chunk_index=index,
        start_char=0,
        end_char=len(text),
    )


def candidate(text: str, doc_length: int, **frequencies: int) -> LexicalCandidate:
    return LexicalCandidate(
        chunk=chunk(text), doc_length=doc_length, term_frequencies=dict(frequencies)
    )


def bundle(
    *candidates: LexicalCandidate, n_docs: int = 10, avg: float = 20.0, **df: int
) -> LexicalCandidates:
    return LexicalCandidates(
        stats=CorpusStats(n_docs=n_docs, avg_doc_length=avg, doc_frequencies=dict(df)),
        candidates=list(candidates),
    )


def test_ranks_by_score_descending() -> None:
    """Two query terms, and the two candidates differ on both counts.

    A single-term query would not distinguish a sum over terms from the
    first term alone, and equal document lengths would not distinguish
    length normalisation from none.
    """
    weak = candidate("weak", doc_length=50, alpha=1)
    strong = candidate("strong", doc_length=10, alpha=4, beta=3)
    ranked = rank_chunks(["alpha", "beta"], bundle(weak, strong, alpha=3, beta=2), k=5)
    assert [result.chunk.text for result in ranked] == ["strong", "weak"]
    assert ranked[0].score > ranked[1].score


def test_ties_break_on_chunk_id_ascending() -> None:
    """Equal scores must not order by arrival, or two adapters disagree.

    The two candidates are given identical statistics, so nothing but the
    tie-break can decide -- and they are passed in the order that makes a
    'preserve input order' implementation produce the wrong answer.
    """
    first = candidate("aaa", doc_length=20, alpha=2)
    second = candidate("bbb", doc_length=20, alpha=2)
    high, low = sorted([first, second], key=lambda c: c.chunk.id, reverse=True)
    ranked = rank_chunks(["alpha"], bundle(high, low, alpha=3), k=5)
    assert [result.chunk.id for result in ranked] == sorted([first.chunk.id, second.chunk.id])


def test_truncates_to_k() -> None:
    """`k` is 2 and there are 4 candidates, so `k` cannot be confused with
    the candidate count or with the length of the input."""
    candidates = [candidate(f"chunk {n}", doc_length=20, alpha=n + 1) for n in range(4)]
    assert len(rank_chunks(["alpha"], bundle(*candidates, alpha=3), k=2)) == 2


def test_a_candidate_matching_nothing_is_dropped_not_ranked_zero() -> None:
    """A zero-scoring candidate is not a result; it is a non-match.

    Returning it fills `k` with passages that do not contain a query term,
    which reads to a caller as the ranker being bad rather than as the
    candidate set being generous.
    """
    ranked = rank_chunks(
        ["alpha"], bundle(candidate("nothing", doc_length=20, beta=9), alpha=3), k=5
    )
    assert ranked == []


def test_no_candidates_yields_nothing() -> None:
    assert rank_chunks(["alpha"], bundle(alpha=3), k=5) == []


def test_no_terms_yields_nothing() -> None:
    assert rank_chunks([], bundle(candidate("x", doc_length=20, alpha=2)), k=5) == []


def test_k_of_zero_is_legal_and_empty() -> None:
    """Pinned as an example: a property drawing k from a range may or may not
    sample the boundary, and whether it does decides the mutation result."""
    assert rank_chunks(["alpha"], bundle(candidate("x", 20, alpha=2), alpha=3), k=0) == []


def test_a_negative_k_is_rejected() -> None:
    with pytest.raises(ValueError, match="k"):
        rank_chunks(["alpha"], bundle(), k=-1)
