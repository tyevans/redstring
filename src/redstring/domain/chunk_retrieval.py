"""What a chunk retrieval asks for and what it answers with.

Mirrors `domain/retrieval.py`'s `ScoredEntity` / `RetrievalResult` shape for
the chunk corpus rather than the entity graph.

## `None` and `0.0` are different facts

As `domain/retrieval.py` and ADR 0022 state for the entity equivalent:
`semantic` and `lexical` on `ScoredChunk` are `None` when that channel did not
rank the chunk at all, and a float when it did. Reciprocal rank fusion
discards magnitude, so these component scores are the only way a caller can
see what fusion threw away -- collapsing "the channel did not run" into
"the channel scored it zero" would make that unreadable, and `semantic is
None` unaskable.
"""

from __future__ import annotations

from pydantic import BaseModel

from redstring.domain.chunk import StoredChunk


class SemanticCandidate(BaseModel):
    """One chunk the semantic channel ranked, with its similarity score."""

    chunk: StoredChunk
    #: `VectorMatch` scale (cosine mapped onto 0..1).
    score: float


class ScoredChunk(BaseModel):
    """One chunk a retrieval returned, with the scores that put it there."""

    chunk: StoredChunk
    #: Fused, ordinal, unbounded -- see `domain/retrieval.py`'s module
    #: docstring for why this scale carries no `0..1` bound.
    score: float
    #: `VectorMatch` scale (cosine mapped onto 0..1), or `None` if the
    #: semantic channel did not rank this chunk. See the module docstring.
    semantic: float | None = None
    #: Lexical channel's own scale, or `None` if it did not rank this chunk.
    lexical: float | None = None


class ChunkRetrievalResult(BaseModel):
    """The answer to one query over the chunk corpus, best first."""

    query: str
    matches: list[ScoredChunk] = []
