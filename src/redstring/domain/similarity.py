"""How alike two entities are, as pure functions on values.

Three signals, deliberately independent, because they fail in different places:

- **name** -- `string_similarity`, Jaro-Winkler over normalized names. Catches
  typos and inflections, and is fooled by two different people with the same
  name.
- **embedding** -- not computed here. It comes from `VectorStore.search`,
  already on the port's `0..1` scale, because computing it needs an embedding
  provider and a store; `combined_score` takes it as a number.
- **graph** -- `graph_similarity`, Jaccard over neighbour sets. Catches the
  case the other two cannot: two records that barely look alike but sit in the
  same part of the graph.

`combined_score` weighs whichever are available. Everything in this module is
a function of its arguments, with no store, no provider, and no I/O -- which
is what lets the properties below be stated at all.

## Every score is on 0..1, and the bound is enforced, not assumed

Floating-point similarity functions overshoot. Slice 0 found the previous
`cosine_similarity` returning marginally more than `1.0` for two *identical*
float32 vectors, which broke a `le=1` bound downstream; that fix lives in
`domain/vector.py::cosine_score` and its boundary test with it. The same care
applies here: `combined_score` clamps, because a weighted mean of values each
within `1.0` can still land a hair above it once the weights are normalized.

## No evidence is not perfect agreement

`graph_similarity` of two entities with no neighbours at all is `0.0`, not
`1.0`. Jaccard of two empty sets is conventionally defined as 1 and that
convention is wrong here: two freshly-extracted entities that nothing points
at yet would score a perfect graph match and drag a merge decision over the
threshold on the strength of knowing nothing about either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import jellyfish
from pydantic import BaseModel, Field, model_validator

from redstring.domain.normalization import normalize_name

if TYPE_CHECKING:
    from collections.abc import Collection

    from redstring.domain.ids import EntityId


def string_similarity(left: str, right: str) -> float:
    """Jaro-Winkler similarity of two names, on `0..1`.

    Both sides are normalized first, so casing and whitespace do not count as
    differences -- `"Ada  LOVELACE"` and `"ada lovelace"` are the same name and
    score `1.0`.

    Symmetric, and `1.0` exactly when the normalized names are equal. Both
    properties are checked in the test module; neither is free, because
    Jaro-Winkler's prefix bonus is applied to whichever string is passed first
    in some implementations.
    """
    return jellyfish.jaro_winkler_similarity(normalize_name(left), normalize_name(right))


def graph_similarity(left: Collection[EntityId], right: Collection[EntityId]) -> float:
    """Jaccard overlap of two entities' neighbour sets, on `0..1`.

    Takes the neighbours rather than the entities and a store: this is a
    function of two sets, and giving it a `GraphStore` would make it untestable
    without one and unusable inside a scoring loop that has already fetched
    them.

    **Two empty sets score `0.0`, not the conventional `1.0`.** See the module
    docstring: "nothing is known about either" must not read as "these agree
    perfectly".
    """
    left_set, right_set = set(left), set(right)
    union = left_set | right_set
    if not union:
        return 0.0
    return len(left_set & right_set) / len(union)


class FeatureWeights(BaseModel):
    """How much each signal counts toward a combined score.

    Frozen, because a weight vector mutated between two comparisons makes the
    scores incomparable and there is nothing in a score that would show it.

    The values need not sum to anything: `combined_score` normalizes over the
    features actually present, so a caller with no embedding gets a
    name-and-graph score on the same `0..1` scale rather than a smaller number.
    That is the whole reason weights are renormalized rather than applied raw
    -- otherwise "the embedding provider was down" and "these entities are
    unalike" would produce the same number.
    """

    model_config = {"frozen": True}

    name: float = Field(default=0.5, ge=0.0)
    embedding: float = Field(default=0.3, ge=0.0)
    graph: float = Field(default=0.2, ge=0.0)

    @model_validator(mode="after")
    def _at_least_one_weight_is_positive(self) -> FeatureWeights:
        """All-zero weights make every pair score identically.

        Rejected at construction rather than at scoring time, because a scorer
        that returned `0.0` for everything looks exactly like a corpus with no
        duplicates in it -- which is a plausible answer, and so not one anybody
        investigates.
        """
        if self.name == 0.0 and self.embedding == 0.0 and self.graph == 0.0:
            raise ValueError("at least one feature weight must be positive")
        return self


class SimilarityFeatures(BaseModel):
    """The per-signal scores for one pair. `None` means "not computed".

    `None` and `0.0` are different and must stay so: the first drops the
    feature out of the weighting, the second is positive evidence that the
    entities disagree on it. Collapsing them would let a missing embedding
    push a pair below the merge threshold.
    """

    model_config = {"frozen": True}

    name: float | None = Field(default=None, ge=0.0, le=1.0)
    embedding: float | None = Field(default=None, ge=0.0, le=1.0)
    graph: float | None = Field(default=None, ge=0.0, le=1.0)


def combined_score(features: SimilarityFeatures, weights: FeatureWeights | None = None) -> float:
    """A weighted mean of whichever features are present, on `0..1`.

    Returns `0.0` when nothing was computed at all -- there is no evidence, and
    the alternative readings ("perfectly alike", "raise") are both worse: the
    first merges on no evidence, and the second turns a provider outage into a
    crash in the middle of a consolidation run rather than a run that merges
    nothing.

    A weight of zero is exactly equivalent to not supplying the feature, and
    that falls out of the arithmetic rather than being arranged: the term
    leaves the numerator and the weight leaves the divisor together. An
    earlier version filtered zero-weight features out explicitly and a
    hand-applied mutant removing the filter survived every test -- which is
    what an equivalent branch looks like from outside. The filter is gone;
    `test_a_zero_weight_is_the_same_as_an_absent_feature` now pins the
    property rather than the implementation of it.
    """
    weights = weights or FeatureWeights()
    present = [
        (score, weight)
        for score, weight in (
            (features.name, weights.name),
            (features.embedding, weights.embedding),
            (features.graph, weights.graph),
        )
        if score is not None
    ]
    total_weight = sum(weight for _, weight in present)
    if not total_weight:
        return 0.0
    combined = sum(score * weight for score, weight in present) / total_weight
    # Clamped, not asserted. Each term is within `0..1` and the divisor is the
    # exact sum of the same weights, but the division is floating-point and the
    # result can land a hair outside -- which is how slice 0's `le=1` bound
    # broke on two identical vectors.
    return min(1.0, max(0.0, combined))
