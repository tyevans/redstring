"""What a store hands back for ranking, and the ranking itself.

## The candidate carries the whole chunk

An id would mean a second round trip per query to fetch the passages that
ranked, and every field of the chunk is wanted by whoever is going to rank
it. The cost is a wider row over the wire for candidates that will be cut.

## `doc_length` and `term_frequencies` are derived, so they are not on
## `StoredChunk`

Both are functions of `text` through `domain.tokenize`. Storing them on the
domain type would create a second place the truth lives and a way for the two
to disagree; carrying them beside the chunk, only where ranking needs them,
cannot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from redstring.domain.bm25 import CorpusStats, bm25_score
from redstring.domain.chunk import StoredChunk

if TYPE_CHECKING:
    from collections.abc import Sequence


class LexicalCandidate(BaseModel):
    """One chunk a store offers for ranking, with the numbers to rank it."""

    chunk: StoredChunk
    #: Total tokens in the chunk, repeats included.
    doc_length: int = Field(ge=0)
    #: Occurrences of each *requested* term. Terms the chunk does not contain
    #: may be absent or present as `0`; the scorer treats both as no match.
    term_frequencies: dict[str, int] = Field(default_factory=dict)


class LexicalCandidates(BaseModel):
    """A store's answer to "which chunks contain these terms"."""

    stats: CorpusStats
    candidates: list[LexicalCandidate] = Field(default_factory=list)


class RankedChunk(BaseModel):
    """One chunk a ranking returned, with its BM25 score.

    The score is **unbounded above and ordinal**: comparable within one
    result set and meaningless across queries or corpora. See
    `domain/bm25.py`.
    """

    chunk: StoredChunk
    score: float


def rank_chunks(
    terms: Sequence[str],
    candidates: LexicalCandidates,
    k: int,
) -> list[RankedChunk]:
    """The best `k` candidates for `terms`, best first.

    Ordered by score descending, ties broken by `chunk.id` ascending. The
    tie-break is not a nicety: without it two stores offering the same
    candidates in different orders return different results, which is
    precisely the divergence putting the scorer in the domain removes.

    Candidates scoring zero are **dropped rather than returned**. A zero score
    means the chunk contains no requested term, and padding `k` with
    non-matches reads as a bad ranker rather than as a generous candidate set.
    """
    if k < 0:
        raise ValueError(f"k must not be negative, got {k}")

    scored = [
        RankedChunk(
            chunk=candidate.chunk,
            score=bm25_score(
                terms, candidate.term_frequencies, candidate.doc_length, candidates.stats
            ),
        )
        for candidate in candidates.candidates
    ]
    ranked = [result for result in scored if result.score > 0.0]
    ranked.sort(key=lambda result: (-result.score, result.chunk.id))
    return ranked[:k]
