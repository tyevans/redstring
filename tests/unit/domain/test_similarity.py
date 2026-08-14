"""Similarity is bounded, symmetric where it claims to be, and maximal at identity."""

from __future__ import annotations

import math
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from redstring.domain.similarity import (
    CONTAINMENT_CEILING,
    FeatureWeights,
    SimilarityFeatures,
    combined_score,
    graph_similarity,
    name_tokens,
    overlap_coefficient,
    string_similarity,
)

#: Non-empty text, since a name on an `Entity` cannot be blank.
names = st.text(min_size=1, max_size=30).filter(lambda value: value.strip())


def test_name_tokens_splits_the_normalized_name_on_whitespace():
    assert name_tokens("Lord  VOLDEMORT") == frozenset({"lord", "voldemort"})


def test_name_tokens_of_a_single_word_is_one_token():
    assert name_tokens("Voldemort") == frozenset({"voldemort"})


def test_name_tokens_deduplicates_repeated_words():
    """A set, not a list: "New York, New York" is two distinct tokens.

    Stated because the overlap coefficient divides by `min(|A|, |B|)`, so a
    repeated token in a multiset would inflate the denominator and quietly
    lower every score involving a name that repeats a word.
    """
    assert name_tokens("New York New York") == frozenset({"new", "york"})


def test_overlap_coefficient_of_a_subset_is_one():
    assert overlap_coefficient({"voldemort"}, {"lord", "voldemort"}) == 1.0


def test_overlap_coefficient_is_symmetric():
    assert overlap_coefficient({"lord", "voldemort"}, {"voldemort"}) == 1.0


def test_overlap_coefficient_of_disjoint_sets_is_zero():
    assert overlap_coefficient({"tom", "riddle"}, {"voldemort"}) == 0.0


def test_overlap_coefficient_divides_by_the_smaller_set():
    """`2/3`, not `2/4`: the divisor is `min`, which is what makes a subset 1.0."""
    assert overlap_coefficient(
        {"university", "of", "oxford"}, {"university", "of", "cambridge", "college"}
    ) == pytest.approx(2 / 3)


def test_overlap_coefficient_of_an_empty_set_is_zero():
    """Nothing is known about one side, which must not read as perfect agreement.

    The same reasoning as `graph_similarity`'s two-empty-sets case in this
    module's docstring: the mathematically conventional answer for a vacuous
    containment is 1.0, and 1.0 here would drag a merge over a threshold on
    the strength of an unparseable name.
    """
    assert overlap_coefficient(set(), {"voldemort"}) == 0.0
    assert overlap_coefficient({"voldemort"}, set()) == 0.0
    assert overlap_coefficient(set(), set()) == 0.0


class TestStringSimilarity:
    def test_identical_names_score_one(self):
        assert string_similarity("Ada Lovelace", "Ada Lovelace") == 1.0

    def test_normalization_happens_before_comparison(self):
        assert string_similarity("  ADA   Lovelace ", "ada lovelace") == 1.0

    def test_a_near_miss_scores_high_but_not_one(self):
        score = string_similarity("Ada Lovelace", "Ada Lovelacé")

        assert 0.9 < score < 1.0

    def test_unrelated_names_score_low(self):
        assert string_similarity("Ada Lovelace", "Charles Babbage") < 0.6

    @given(left=names, right=names)
    def test_it_is_symmetric(self, left, right):
        """Not free: Jaro-Winkler's prefix bonus is applied to one argument,
        and an implementation that took the prefix of `left` only would pass
        every equal-length example and fail here."""
        assert string_similarity(left, right) == string_similarity(right, left)

    @given(left=names, right=names)
    def test_it_is_bounded(self, left, right):
        assert 0.0 <= string_similarity(left, right) <= 1.0

    @given(name=names)
    def test_identity_scores_highest(self, name):
        assert string_similarity(name, name) == 1.0

    @given(left=names, right=names)
    def test_nothing_beats_identity(self, left, right):
        """The bound that matters for a threshold: no pair may outscore a pair
        of equal names, or a threshold tuned on exact matches admits
        non-matches."""
        assert string_similarity(left, right) <= string_similarity(left, left)


class TestGraphSimilarity:
    def test_identical_neighbourhoods_score_one(self):
        shared = [uuid4(), uuid4(), uuid4()]

        assert graph_similarity(shared, list(shared)) == 1.0

    def test_disjoint_neighbourhoods_score_zero(self):
        assert graph_similarity([uuid4()], [uuid4()]) == 0.0

    def test_it_is_jaccard_and_not_mere_overlap(self):
        """Three shared out of four distinct is 0.75, and an implementation
        dividing by either side's size alone would say 1.0 or 0.75 depending
        on which -- so the sets are deliberately different sizes."""
        a, b, c, d = (uuid4() for _ in range(4))

        assert graph_similarity([a, b, c], [a, b, c, d]) == pytest.approx(0.75)

    def test_two_empty_neighbourhoods_score_zero(self):
        """Jaccard convention says 1.0 for two empty sets. That convention is
        wrong here: two freshly-extracted entities nothing points at yet would
        get a perfect graph match on the strength of knowing nothing."""
        assert graph_similarity([], []) == 0.0

    def test_one_empty_neighbourhood_scores_zero(self):
        assert graph_similarity([], [uuid4()]) == 0.0

    def test_repeated_neighbours_do_not_inflate_the_score(self):
        """The input is a collection, not a set: `get_relationships_for` can
        hand back the same neighbour twice for two edges between one pair."""
        a, b = uuid4(), uuid4()

        assert graph_similarity([a, a, a], [a, b]) == pytest.approx(0.5)

    @given(
        left=st.lists(st.uuids(), max_size=6),
        right=st.lists(st.uuids(), max_size=6),
    )
    def test_it_is_symmetric_and_bounded(self, left, right):
        forward, backward = graph_similarity(left, right), graph_similarity(right, left)

        assert forward == backward
        assert 0.0 <= forward <= 1.0

    @given(neighbours=st.lists(st.uuids(), min_size=1, max_size=6))
    def test_identity_scores_one(self, neighbours):
        assert graph_similarity(neighbours, list(neighbours)) == 1.0


class TestFeatureWeights:
    def test_all_zero_weights_are_refused(self):
        """A scorer returning 0.0 for everything looks exactly like a corpus
        with no duplicates -- a plausible answer, so not one anybody
        investigates."""
        with pytest.raises(ValidationError, match="at least one feature weight"):
            FeatureWeights(name=0.0, embedding=0.0, graph=0.0)

    def test_a_negative_weight_is_refused(self):
        with pytest.raises(ValidationError):
            FeatureWeights(name=-0.1)

    def test_weights_are_frozen(self):
        weights = FeatureWeights()

        with pytest.raises(ValidationError):
            weights.name = 0.9


class TestCombinedScore:
    def test_one_feature_is_its_own_score(self):
        """The renormalization, at its simplest: a caller with only a name
        must get the name score, not the name score times its weight."""
        assert combined_score(SimilarityFeatures(name=0.8)) == pytest.approx(0.8)

    def test_features_are_weighted_against_each_other(self):
        score = combined_score(
            SimilarityFeatures(name=1.0, graph=0.0),
            FeatureWeights(name=3.0, embedding=1.0, graph=1.0),
        )

        # 3:1 between the two present features, and the absent embedding must
        # not appear in the divisor -- which is the difference between 0.75
        # and 0.6.
        assert score == pytest.approx(0.75)

    def test_a_missing_feature_is_not_a_zero(self):
        """The whole reason `None` and `0.0` stay apart. A provider outage must
        not push a pair below the merge threshold."""
        missing = combined_score(SimilarityFeatures(name=0.9))
        zeroed = combined_score(SimilarityFeatures(name=0.9, embedding=0.0))

        assert missing == pytest.approx(0.9)
        assert zeroed < missing

    def test_a_zero_weight_is_the_same_as_an_absent_feature(self):
        """True by arithmetic, not by a filter. Kept because it is a property
        a caller relies on when it tunes a signal out, and because the
        arithmetic is only obvious once someone has checked it -- an explicit
        `weight > 0` filter was here until a mutant removing it survived every
        test in this file."""
        weighted_out = combined_score(
            SimilarityFeatures(name=0.9, graph=0.1),
            FeatureWeights(name=1.0, embedding=1.0, graph=0.0),
        )
        omitted = combined_score(
            SimilarityFeatures(name=0.9), FeatureWeights(name=1.0, embedding=1.0, graph=1.0)
        )

        assert weighted_out == pytest.approx(omitted)

    def test_no_features_score_zero(self):
        assert combined_score(SimilarityFeatures()) == 0.0

    @given(
        name=st.one_of(st.none(), st.floats(0.0, 1.0)),
        embedding=st.one_of(st.none(), st.floats(0.0, 1.0)),
        graph=st.one_of(st.none(), st.floats(0.0, 1.0)),
        weights=st.tuples(st.floats(0.0, 10.0), st.floats(0.0, 10.0), st.floats(0.0, 10.0)).filter(
            lambda triple: any(value > 0 for value in triple)
        ),
    )
    def test_the_result_is_always_inside_the_unit_interval(self, name, embedding, graph, weights):
        """The bound slice 0 found broken elsewhere. A weighted mean of values
        each within 1.0 can still land a hair above once divided by a
        floating-point sum of the same weights."""
        score = combined_score(
            SimilarityFeatures(name=name, embedding=embedding, graph=graph),
            FeatureWeights(name=weights[0], embedding=weights[1], graph=weights[2]),
        )

        assert 0.0 <= score <= 1.0
        assert not math.isnan(score)

    @given(
        score=st.floats(0.0, 1.0),
        weights=st.tuples(st.floats(0.1, 10.0), st.floats(0.1, 10.0), st.floats(0.1, 10.0)),
    )
    def test_features_that_all_agree_give_that_value_back(self, score, weights):
        """Whatever the weights: a weighted mean of equal values is that value.
        This is what pins the renormalization -- an implementation that divided
        by a constant instead would fail for every weight vector that does not
        happen to sum to it."""
        combined = combined_score(
            SimilarityFeatures(name=score, embedding=score, graph=score),
            FeatureWeights(name=weights[0], embedding=weights[1], graph=weights[2]),
        )

        assert combined == pytest.approx(score)

    @given(
        low=st.floats(0.0, 1.0),
        high=st.floats(0.0, 1.0),
    )
    def test_raising_a_feature_never_lowers_the_score(self, low, high):
        """Monotone in each feature, which is what makes a threshold mean
        anything. A sign error or a `1 - x` anywhere in the weighting breaks
        this and nothing else."""
        if low > high:
            low, high = high, low
        features = {"embedding": 0.5, "graph": 0.5}

        lower = combined_score(SimilarityFeatures(name=low, **features))
        higher = combined_score(SimilarityFeatures(name=high, **features))

        assert lower <= higher


def test_a_name_qualified_by_a_title_scores_at_the_containment_ceiling():
    """Jaro-Winkler scores this 0.770 -- inside the band, but only just.

    The margin is the problem rather than the score: 0.770 clears
    LOW_SIMILARITY by 0.02, so an embedding below 0.717 drags the pair out of
    the band entirely. At the ceiling it survives an embedding down to 0.50.
    """
    assert string_similarity("Lord Voldemort", "Voldemort") == pytest.approx(CONTAINMENT_CEILING)


def test_containment_is_symmetric():
    assert string_similarity("Voldemort", "Lord Voldemort") == pytest.approx(
        string_similarity("Lord Voldemort", "Voldemort")
    )


def test_a_surname_only_mention_also_reaches_the_ceiling():
    assert string_similarity("Ada Lovelace", "Lovelace") == pytest.approx(CONTAINMENT_CEILING)


def test_names_sharing_no_tokens_are_unchanged():
    """`max` must leave Jaro-Winkler alone where containment says nothing.

    Asserted against the literal Jaro-Winkler value rather than against
    `string_similarity` itself -- an expectation written in terms of the
    function under test would hold for any implementation, including one
    where containment had swallowed the other branch entirely.
    """
    import jellyfish

    assert string_similarity("Tom Riddle", "Voldemort") == pytest.approx(
        jellyfish.jaro_winkler_similarity("tom riddle", "voldemort")
    )


def test_jaro_winkler_still_wins_when_it_is_the_higher_signal():
    """A near-typo shares no whole token, so containment is 0.0 and JW carries it."""
    score = string_similarity("Voldemort", "Voldemorte")
    assert score > CONTAINMENT_CEILING


def test_identical_names_still_score_exactly_one():
    """The ceiling is strictly below 1.0, so only Jaro-Winkler can reach it."""
    assert string_similarity("Ada  LOVELACE", "ada lovelace") == 1.0


def test_a_containment_match_can_never_reach_one():
    """Otherwise a subset name could merge without ever being asked about."""
    assert CONTAINMENT_CEILING < 1.0
    assert string_similarity("Lord Voldemort", "Voldemort") < 1.0


def test_partial_token_overlap_contributes_nothing():
    """The precision half. A shared "University of" must not carry the pair.

    Asserted as "containment did not raise the score" rather than as a bound
    on the score itself. Jaro-Winkler already scores this pair 0.899, which is
    *above* the ceiling and has nothing to do with this change; a test written
    as `< CONTAINMENT_CEILING` would fail on pre-existing behaviour and invite
    someone to move a threshold to satisfy a test written after the design.
    """
    import jellyfish

    assert string_similarity("University of Oxford", "University of Cambridge") == pytest.approx(
        jellyfish.jaro_winkler_similarity("university of oxford", "university of cambridge")
    )


def test_containment_can_never_produce_an_unasked_merge():
    """The invariant that makes the term safe, stated over every input at once.

    The containment branch is capped at `CONTAINMENT_CEILING`, so no input
    whatever can reach `HIGH_SIMILARITY` through it. A property of the
    arithmetic rather than of any corpus, so it is asserted as one rather than
    sampled -- see the banding corpus for why the corpus states the weaker,
    per-pair form of this.
    """
    assert CONTAINMENT_CEILING * 1.0 < 0.92
