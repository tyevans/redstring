"""Perceived responsiveness is a distribution, so the summariser is tested
against a run whose mean hides its worst gap."""

from __future__ import annotations

import pytest

from bench.config import SweepPoint
from bench.metrics import RunMetrics, summarise_gaps

POINT = SweepPoint(document_id="d", chunk_size=3000, concurrency=1, repeat=0)


def metrics(**overrides: object) -> RunMetrics:
    """A run with every field set, so a test can vary exactly one."""
    fields: dict[str, object] = {
        "point": POINT,
        "wall_clock_s": 10.0,
        "time_to_first_entity_s": None,
        "event_gaps_s": (),
        "model_calls": 3,
        "extract_s": 8.0,
        "consolidate_s": 2.0,
        "chunks": 3,
        "entities": 12,
        "relationships": 7,
        "failed_chunks": 0,
        "unresolved_relationships": 1,
        "entity_names": ("ada lovelace",),
    }
    fields.update(overrides)
    return RunMetrics(**fields)  # type: ignore[arg-type]


def test_an_even_run_and_a_spiky_run_share_a_mean_and_differ_at_p95() -> None:
    """The whole reason the gap list is stored rather than averaged.

    3,3,3 and 1,1,7 both average 3. One of them stalls for seven seconds and
    the other never stalls, and a caller watching a progress bar can tell the
    difference immediately. A summary that reports only the mean cannot.
    """
    even = summarise_gaps([3.0, 3.0, 3.0])
    spiky = summarise_gaps([1.0, 1.0, 7.0])

    assert even is not None
    assert spiky is not None
    assert even.maximum == 3.0
    assert spiky.maximum == 7.0
    assert spiky.p95 > even.p95


def test_the_percentiles_are_the_values_they_name() -> None:
    """Literal expectations, not expectations phrased in terms of the input.

    Nearest-rank on ten sorted values: p50 is the 5th, p95 is the 10th.
    Written as literals so that an implementation using a different
    interpolation is a failure rather than a redefinition.
    """
    summary = summarise_gaps([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])

    assert summary is not None
    assert summary.p50 == 5.0
    assert summary.p95 == 10.0
    assert summary.maximum == 10.0
    assert summary.count == 10


def test_the_input_order_does_not_change_the_summary() -> None:
    """A summariser that forgets to sort passes every already-sorted case."""
    assert summarise_gaps([10.0, 1.0, 5.0, 2.0]) == summarise_gaps([1.0, 2.0, 5.0, 10.0])


@pytest.mark.parametrize("gaps", [[], [4.0]])
def test_fewer_than_two_gaps_is_no_summary_rather_than_zeroes(gaps: list[float]) -> None:
    """A single gap describes no rhythm, and reporting p95=4.0 from one
    sample invites reading it as one."""
    assert summarise_gaps(gaps) is None


def test_the_run_exposes_its_own_summary() -> None:
    assert metrics(event_gaps_s=(1.0, 1.0, 7.0)).gaps == summarise_gaps([1.0, 1.0, 7.0])


def test_a_baseline_run_carries_no_time_to_first_entity() -> None:
    """Recorded as absent, never approximated. See the plan's constraints."""
    assert metrics().time_to_first_entity_s is None
