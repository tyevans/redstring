"""Reciprocal rank fusion."""

from __future__ import annotations

from uuid import UUID

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from redstring.domain.fusion import RRF_K, reciprocal_rank_fusion

# Fixed ids, ordered so the *string* order is knowable at a glance. The
# tie-break is on the canonical lowercase hyphenated form, so a test using
# random uuid4s would pass or fail depending on how they happened to sort --
# the exact shape CLAUDE.md's table records for a `<=` that meant `==`.
A = UUID("00000000-0000-4000-8000-00000000000a")
B = UUID("00000000-0000-4000-8000-00000000000b")
C = UUID("00000000-0000-4000-8000-00000000000c")


def test_an_entity_in_both_rankings_at_the_same_rank_beats_one_in_either_alone() -> None:
    """The collision case: without it, "sum" and "max" are the same function.

    A and B are both rank 0 -- A in both rankings, B in one. Under `max` they
    tie at `1/60` and the tie-break decides, which A wins anyway on id order.
    So the test asserts the *score*, not the order: only summing gives A twice
    B's score, and only a scored assertion can see the difference.
    """
    fused = dict(reciprocal_rank_fusion([[A], [A, B]]))
    assert fused[A] == pytest.approx(2 / (RRF_K + 1))
    assert fused[B] == pytest.approx(1 / (RRF_K + 2))


def test_rank_is_one_based_so_the_first_element_is_not_a_division_by_k() -> None:
    """`1/(k+rank)` with a 0-based rank makes the top of each list `1/60`.

    Stated as a literal rather than as an expression in RRF_K: writing the
    expectation in terms of the constant under test makes it true for any
    value of that constant, zero included.
    """
    fused = dict(reciprocal_rank_fusion([[A, B]]))
    assert fused[A] == pytest.approx(1 / 61)
    assert fused[B] == pytest.approx(1 / 62)


def test_ties_break_by_ascending_id_string() -> None:
    """Two entities at the same rank in different rankings score identically."""
    fused = reciprocal_rank_fusion([[B], [A]])
    assert [entity_id for entity_id, _ in fused] == [A, B]


def test_an_empty_ranking_contributes_nothing_and_does_not_shift_ranks() -> None:
    """An off channel is an empty list, not a missing argument.

    If an empty ranking shifted the others' ranks, turning a channel off would
    silently rescore the channel that stayed on.
    """
    with_empty = reciprocal_rank_fusion([[A, B], []])
    without = reciprocal_rank_fusion([[A, B]])
    assert with_empty == without


def test_no_rankings_at_all_is_empty() -> None:
    assert reciprocal_rank_fusion([]) == []


def test_a_repeated_id_within_one_ranking_counts_once_at_its_best_rank() -> None:
    """A malformed channel must not be able to inflate its own contribution."""
    fused = dict(reciprocal_rank_fusion([[A, B, A]]))
    assert fused[A] == pytest.approx(1 / 61)


@given(st.lists(st.sampled_from([A, B, C]), unique=True, max_size=3))
@example([])
@example([A])
def test_every_id_present_appears_exactly_once_in_the_output(ids: list[UUID]) -> None:
    """Boundary sizes pinned as examples.

    A sampler decides how often it draws 0 and 1, and mutation runs lower the
    example count to 5.
    """
    fused = reciprocal_rank_fusion([ids])
    assert sorted(entity_id for entity_id, _ in fused) == sorted(set(ids))


@given(st.permutations([A, B, C]))
def test_the_order_is_total_so_no_two_results_are_interchangeable(
    ranking: list[UUID],
) -> None:
    """A `>` widened to `>=` is only "equivalent" if the order is total.

    Asserting the totality is what makes that label honest rather than
    assumed.
    """
    fused = reciprocal_rank_fusion([ranking])
    keys = [(-score, str(entity_id)) for entity_id, score in fused]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)
