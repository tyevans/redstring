"""Two strategies resolve; three refuse, and refuse loudly."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kg_builder.domain.merge_strategy import (
    IMPLEMENTED,
    PropertyMergeStrategy,
    resolve,
)


class TestPreferCanonical:
    def test_the_canonical_value_wins(self):
        assert (
            resolve(
                PropertyMergeStrategy.PREFER_CANONICAL, canonical="Ada", others=["A.", "Augusta"]
            )
            == "Ada"
        )

    def test_a_canonical_none_is_a_value_not_an_absence(self):
        """The case a "prefer canonical unless it's empty" implementation gets
        wrong. A description deliberately cleared on the canonical entity must
        not be refilled from an entity being absorbed."""
        assert (
            resolve(PropertyMergeStrategy.PREFER_CANONICAL, canonical=None, others=["a bio"])
            is None
        )

    def test_it_does_not_consult_others_at_all(self):
        assert resolve(PropertyMergeStrategy.PREFER_CANONICAL, canonical=1, others=[]) == 1


class TestUnion:
    def test_it_accumulates_distinct_values_canonical_first(self):
        assert resolve(PropertyMergeStrategy.UNION, canonical="Ada", others=["A.", "Augusta"]) == [
            "Ada",
            "A.",
            "Augusta",
        ]

    def test_duplicates_collapse_and_first_seen_order_survives(self):
        assert resolve(
            PropertyMergeStrategy.UNION, canonical="Ada", others=["Augusta", "Ada", "A."]
        ) == ["Ada", "Augusta", "A."]

    def test_it_flattens_one_level(self):
        """Applying `UNION` to an already-unioned value must not nest. A
        projection replays, so the second application has to be a no-op."""
        once = resolve(PropertyMergeStrategy.UNION, canonical="Ada", others=["A."])
        twice = resolve(PropertyMergeStrategy.UNION, canonical=once, others=["A."])

        assert twice == ["Ada", "A."]

    def test_unhashable_values_survive(self):
        """A `set` would raise here, on exactly the nested values `UNION`
        exists to accumulate."""
        assert resolve(
            PropertyMergeStrategy.UNION,
            canonical={"wikidata": "Q7259"},
            others=[{"viaf": "12345"}, {"wikidata": "Q7259"}],
        ) == [{"wikidata": "Q7259"}, {"viaf": "12345"}]

    def test_values_that_compare_equal_across_types_collapse_once(self):
        """`1 == True` in Python, and `==` is what deduplicates. Pinned so the
        behaviour is a decision on the record rather than a surprise: the
        first-seen value is the one kept."""
        assert resolve(PropertyMergeStrategy.UNION, canonical=1, others=[True]) == [1]

    @given(
        canonical=st.integers(),
        others=st.lists(st.integers(), max_size=6),
    )
    def test_the_union_holds_every_input_exactly_once(self, canonical, others):
        merged = resolve(PropertyMergeStrategy.UNION, canonical=canonical, others=others)

        assert set(merged) == {canonical, *others}
        assert len(merged) == len(set(merged))

    @given(canonical=st.integers(), others=st.lists(st.integers(), max_size=6))
    def test_it_is_idempotent(self, canonical, others):
        """The property a replaying projection needs. Stated over generated
        input because the flattening is what makes it true, and flattening is
        easy to get right for one example and wrong in general."""
        once = resolve(PropertyMergeStrategy.UNION, canonical=canonical, others=others)
        twice = resolve(PropertyMergeStrategy.UNION, canonical=once, others=others)

        assert twice == once


class TestTheDeferredStrategies:
    @pytest.mark.parametrize(
        "strategy",
        [
            PropertyMergeStrategy.PREFER_MERGED,
            PropertyMergeStrategy.LATEST,
            PropertyMergeStrategy.DEEP_MERGE,
        ],
    )
    def test_it_raises_rather_than_falling_back(self, strategy):
        """The failure mode this prevents is silent, not loud: a fallback
        writes the canonical value while the caller believes it asked for
        something else, and leaves nothing in the result to show it."""
        with pytest.raises(NotImplementedError) as raised:
            resolve(strategy, canonical="Ada", others=["A."])

        assert strategy.name in str(raised.value)
        assert "B28" in str(raised.value)

    def test_the_deferred_set_is_exactly_what_is_not_implemented(self):
        """Derived rather than listed, so adding a strategy to the enum and
        forgetting `resolve` fails here instead of at some caller."""
        for strategy in PropertyMergeStrategy:
            if strategy in IMPLEMENTED:
                resolve(strategy, canonical="x", others=[])
            else:
                with pytest.raises(NotImplementedError):
                    resolve(strategy, canonical="x", others=[])

    def test_implemented_names_the_two_the_docstring_claims(self):
        assert {
            PropertyMergeStrategy.PREFER_CANONICAL,
            PropertyMergeStrategy.UNION,
        } == IMPLEMENTED
