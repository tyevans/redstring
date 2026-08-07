"""What a retrieval asks for and what it answers with.

## `score` is not on `VectorMatch`'s scale, and that is the point

`VectorMatch.score` is cosine mapped onto `0..1`, and `domain/vector.py`
explains at length why pinning that scale in one place matters: "score" is
ambiguous across vector databases, and an adapter that inverted the sense
would return plausible nonsense rather than an error.

`ScoredEntity.score` is a **reciprocal-rank-fusion** score. It is *ordinal*:
comparable within one result set, meaningless across queries, and never
interpretable as a similarity. It carries no `0..1` bound because it has
none -- two channels agreeing at rank 0 give `2/60`, and nothing caps the sum
as channels are added. Reusing the bare name `score` for a differently-scaled
number is exactly the trap `domain/vector.py` warns about, so the scale is
stated here, where the type is defined, rather than left to a how-to.

## `None` and `0.0` are different facts

`semantic` and `lexical` are `None` when that channel did not rank the entity
at all, and a float when it did. "The lexical channel was off" and "the name
did not match" are different things and a caller acts on them differently.
Both are retained after fusion rather than discarded: without them nobody can
distinguish an entity that matched strongly on both channels from one that
matched on its name alone, and that distinction is the entire reason for
being hybrid.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from redstring.domain.entity import Entity


class RetrievalMode(StrEnum):
    """Which channels a retrieval runs."""

    SEMANTIC = "semantic"
    LEXICAL = "lexical"
    HYBRID = "hybrid"


class ScoredEntity(BaseModel):
    """One entity a retrieval returned, with the scores that put it there."""

    entity: Entity
    #: Fused, ordinal, unbounded. See the module docstring.
    score: float
    #: `VectorMatch` scale (cosine mapped onto 0..1), or `None` if the
    #: semantic channel did not rank this entity.
    semantic: float | None = Field(default=None, ge=0.0, le=1.0)
    #: Jaro-Winkler on 0..1, or `None` if the lexical channel did not rank it.
    lexical: float | None = Field(default=None, ge=0.0, le=1.0)


class RetrievalResult(BaseModel):
    """The answer to one query, best first."""

    query: str
    matches: list[ScoredEntity] = []
