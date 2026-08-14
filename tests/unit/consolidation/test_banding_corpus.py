"""A labelled corpus of name pairs and the band each must land in.

## Why this is a unit test and not part of `tests/accuracy/`

`tests/accuracy/` grades *extraction* against a hand-graded document corpus,
needs an inference endpoint, and is deselected by default. The claim here
needs neither: which band a pair lands in is decided by `decide` over a
`combined_score`, both pure functions. So this runs on every commit, which is
what makes it a gate on the thresholds and weights rather than a periodic
measurement.

## Three claims, not two, and the middle one is the interesting one

A corpus of should-merge pairs alone is satisfied by a scorer returning `1.0`
for everything, so the precision half is what makes the recall half mean
anything. But "precision" here splits in two, and collapsing them writes a
test that fails against untouched behaviour:

- **`MUST_NOT_MERGE_UNASKED`** — the claim containment actually bears on.
  `{smith}` is a subset of `{john, smith}`, and so is `{york}` of
  `{new, york}`; the ceiling is what guarantees such a pair buys a model call
  rather than a merge.
- **`MUST_REJECT`** — no model call at all. Restricted to names sharing no
  whole token, because that is the behaviour this change must *preserve*.

Pairs like "John Smith"/"Jane Smith" belong to the first list and not the
second: Jaro-Winkler alone already scores them near 0.88, so they reach the
model today and should. Demanding `REJECT` there would be a test asserting
that the band should not exist.

## Names only, no embedding, no graph

Each pair is scored on the name feature alone -- `SimilarityFeatures(name=...)`
with the other two `None`. That is the *hardest* case for recall, since it is
what a freshly extracted entity with no vector and no edges looks like, and it
is the case the Voldemort defect was found in. A pair that reaches its band on
the name alone reaches it with corroborating features too.
"""

from __future__ import annotations

import pytest

from redstring.consolidation.policy import (
    HIGH_SIMILARITY,
    LOW_SIMILARITY,
    MergeDecision,
    decide,
)
from redstring.domain.similarity import (
    CONTAINMENT_CEILING,
    SimilarityFeatures,
    combined_score,
    string_similarity,
)


def band(left: str, right: str) -> MergeDecision:
    """The decision the pipeline reaches for two names and nothing else."""
    features = SimilarityFeatures(name=string_similarity(left, right))
    return decide(combined_score(features))


# Pairs a human resolves without hesitating. Each must at least reach the
# model; whether it merges outright is not asserted, because that is a
# threshold question and these are recall claims.
#
# Ordered roughly by how badly Jaro-Winkler alone handles them, because that
# ordering is the finding: the score collapses as the qualifier grows relative
# to the name it qualifies. The first three score 0.437, 0.519 and 0.578 today
# and are unreachable at any embedding; the rest clear the floor only by
# hundredths.
#
# Every pair here is a strict token subset in one direction, which is the
# limit of what a name-only feature can claim. "Ada Lovelace" against
# "Countess of Lovelace" shares one token of two -- arithmetically identical
# to "John Smith" against "Jane Smith", where one pair is the same person and
# the other is not, and nothing in the four strings says which. That case
# belongs to the embedding and graph features; see BACKLOG B-ALIAS-1.
MUST_REACH_THE_MODEL = [
    ("Dr. Grant", "Grant"),
    ("President Bartlet", "Bartlet"),
    ("Professor Albus Dumbledore", "Dumbledore"),
    ("Ada Lovelace", "Lovelace"),
    ("Lord Voldemort", "Voldemort"),
    ("Voldemort", "Lord Voldemort"),
    ("The Ministry of Magic", "Ministry of Magic"),
    ("Ada Lovelace", "Ada Lovelacce"),
    ("Ada  LOVELACE", "ada lovelace"),
]

# Pairs that must never merge *without being asked*. Each shares at least one
# token with its partner, so each is a case containment could have carried.
#
# The claim is `not MERGE`, not `REJECT`, and the difference is the point.
# Jaro-Winkler alone already scores these between 0.86 and 0.90 -- they reach
# the model today, before this branch, and that is the band working as
# designed: they are exactly the ambiguous middle it exists for. Asserting
# `REJECT` here would fail against untouched behaviour and invite someone to
# move a threshold to satisfy a test written after the design.
MUST_NOT_MERGE_UNASKED = [
    ("John Smith", "Jane Smith"),
    ("University of Oxford", "University of Cambridge"),
    ("New York Times", "New York Yankees"),
    ("Bank of England", "Bank of Japan"),
    ("Lord Voldemort", "Voldemort"),
    ("Ada Lovelace", "Lovelace"),
]

# Pairs that must still cost no model call at all. Restricted to names sharing
# no whole token and scoring low on Jaro-Winkler: this is behaviour the change
# must *preserve*, and it is the half that would catch containment firing where
# it has no business firing.
MUST_REJECT = [
    ("Tom Riddle", "Voldemort"),
    ("Ada Lovelace", "Charles Babbage"),
    ("Ministry of Magic", "Hogwarts"),
]


@pytest.mark.parametrize(("left", "right"), MUST_REACH_THE_MODEL)
def test_the_pair_at_least_reaches_the_adjudication_band(left: str, right: str):
    assert band(left, right) is not MergeDecision.REJECT


@pytest.mark.parametrize(("left", "right"), MUST_NOT_MERGE_UNASKED)
def test_the_pair_never_merges_without_a_model_call(left: str, right: str):
    assert band(left, right) is not MergeDecision.MERGE


@pytest.mark.parametrize(("left", "right"), MUST_REJECT)
def test_the_pair_costs_no_model_call(left: str, right: str):
    assert band(left, right) is MergeDecision.REJECT


def test_the_containment_ceiling_sits_between_the_two_thresholds():
    """The invariant that makes containment safe, pinned where it can be.

    `domain` is the bottom layer and cannot import `consolidation`, so the
    constant and the thresholds it is chosen against live in modules that
    cannot see each other. This test is the only place the three are in scope
    at once, which is why it lives here rather than beside the constant.

    Strictly below `HIGH` is the load-bearing half: at or above it, a
    token-subset match would merge without ever being asked about, and
    "Smith" is a subset of "John Smith".
    """
    assert LOW_SIMILARITY <= CONTAINMENT_CEILING < HIGH_SIMILARITY


def test_a_containment_match_alone_lands_in_the_band_rather_than_merging():
    """The end-to-end statement of the line above, through the real functions."""
    assert band("Lord Voldemort", "Voldemort") is MergeDecision.ADJUDICATE
