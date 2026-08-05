"""Embedding vectors as domain types: what is stored, and what a search returns.

Two types, deliberately not one. A `VectorRecord` is what a tenant *has*; a
`VectorMatch` is the answer to a question, and carries a score that only makes
sense relative to the query that produced it. Folding the score onto the
record would make "the score of this record" look like a stored property.

## The score scale is fixed here, not per adapter

`VectorMatch.score` is **cosine similarity mapped onto 0..1 by
`(1 + cosine) / 2`**, higher meaning more similar:

| cosine | meaning | score |
|---|---|---|
| `1.0` | identical direction | `1.0` |
| `0.0` | orthogonal | `0.5` |
| `-1.0` | opposite direction | `0.0` |

"Score" is ambiguous across vector databases -- some report a distance, where
lower is better -- so an adapter that inverted the sense would return
plausible nonsense rather than an error. Pinning the scale in the domain type,
with a `0..1` bound the model enforces, makes the inversion a validation
failure at the boundary instead of a silent quality regression.

The mapping is strictly monotone in cosine, so ranking is unaffected by the
choice; what it buys is that every adapter reports the *same number* for the
same pair of vectors, which is what makes `min_score` portable.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from collections.abc import Sequence

from redstring.domain.ids import EntityId, TenantId


def _reject_nul(value: object) -> None:
    """Raise if any string reachable from `value` contains U+0000.

    Metadata is stored as JSON by every adapter worth having, and Postgres
    `jsonb` **cannot hold a NUL in text** -- it rejects the write outright.
    Python dictionaries can, so without this check the in-memory adapter
    accepts metadata that pgvector refuses, which is precisely the silent
    divergence a shared compliance suite exists to prevent. Found by the
    round-trip property, and fixed here rather than in either adapter so that
    every adapter rejects it identically and for the same reason.

    Stripping or escaping the NUL instead was rejected: it would make the
    round-trip contract a lie, and a caller with a NUL in its metadata has a
    bug upstream that is better surfaced than smoothed over.
    """
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError("metadata must not contain a NUL character; JSON storage rejects it")
    elif isinstance(value, dict):
        for key, item in value.items():
            _reject_nul(key)
            _reject_nul(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_nul(item)


class _HasPortableMetadata(BaseModel):
    """Shared metadata validation; see `_reject_nul`."""

    metadata: dict[str, Any] = {}

    @field_validator("metadata")
    @classmethod
    def _metadata_is_storable_as_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        _reject_nul(value)
        return value


class VectorRecord(_HasPortableMetadata):
    """One entity's embedding under one tenant.

    `vector` is a `list[float]` and `metadata` a `dict`, both mutable on
    purpose: a store handing back its own object would let a caller corrupt
    stored state, and the port requires that it does not. Immutable containers
    here would make the compliance suite's mutation-isolation property
    unfalsifiable -- it would pass on an adapter that leaks, because there
    would be nothing to mutate.
    """

    entity_id: EntityId
    tenant_id: TenantId
    vector: list[float]


class VectorMatch(_HasPortableMetadata):
    """One result of a similarity search. See the module docstring on `score`."""

    entity_id: EntityId
    score: float = Field(ge=0.0, le=1.0)


def cosine_score(left: Sequence[float], right: Sequence[float]) -> float:
    """`(1 + cosine(left, right)) / 2`, clamped into `0..1`.

    Clamping is not defensive tidying. Accumulated rounding makes the dot
    product of a float vector with *itself* exceed its squared norm by an ulp
    or two, so the unclamped value for an identical pair can land marginally
    above 1.0 -- which the `le=1` bound on `VectorMatch.score` would reject.
    Slice 0 hit exactly this in the previous `cosine_similarity`.

    Both vectors must be non-zero: cosine is undefined at the origin, and
    every backend expresses that differently (pgvector yields NaN, which sorts
    unpredictably). The port rejects zero vectors on the way in, so this is
    a guard against a stored value that should not exist rather than a normal
    path.
    """
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    magnitude = _norm(left) * _norm(right)
    if magnitude == 0.0:
        raise ValueError("cosine is undefined for a zero vector")
    return min(1.0, max(0.0, (1.0 + dot / magnitude) / 2.0))


def _norm(vector: Sequence[float]) -> float:
    return float(math.sqrt(sum(value * value for value in vector)))


def is_zero_vector(vector: Sequence[float]) -> bool:
    """Whether every component is zero, so cosine against it is undefined."""
    return not any(vector)
