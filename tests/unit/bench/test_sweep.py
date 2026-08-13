"""The climb-stop is arithmetic over completed runs, so it is a pure
function rather than a branch buried in the CLI."""

from __future__ import annotations

from bench.config import SweepPoint
from bench.metrics import RunMetrics
from bench.sweep import should_stop_climbing


def run(*, concurrency: int, wall: float, document: str = "hp1", chunk: int = 3000) -> RunMetrics:
    return RunMetrics(
        point=SweepPoint(document_id=document, chunk_size=chunk, concurrency=concurrency, repeat=0),
        wall_clock_s=wall,
        time_to_first_entity_s=None,
        event_gaps_s=(),
        model_calls=3,
        extract_s=wall,
        consolidate_s=0.0,
        chunks=3,
        entities=2,
        relationships=1,
        failed_chunks=0,
        unresolved_relationships=0,
        entity_names=("a", "b"),
    )


def point(concurrency: int, *, document: str = "hp1", chunk: int = 3000) -> SweepPoint:
    return SweepPoint(document_id=document, chunk_size=chunk, concurrency=concurrency, repeat=0)


def test_a_slower_higher_concurrency_stops_the_climb() -> None:
    """K=4 slower than K=2 is the backend queueing. K=8 would measure the
    queue for twenty minutes and report it as a benchmark."""
    completed = [run(concurrency=2, wall=100.0), run(concurrency=4, wall=140.0)]

    assert should_stop_climbing(completed, point(8)) is True


def test_a_faster_higher_concurrency_keeps_climbing() -> None:
    completed = [run(concurrency=2, wall=100.0), run(concurrency=4, wall=60.0)]

    assert should_stop_climbing(completed, point(8)) is False


def test_a_regression_at_another_chunk_size_does_not_stop_this_one() -> None:
    """The curve is per configuration. A backend that queues at K=4 with
    12000-character chunks may not at 3000."""
    completed = [
        run(concurrency=2, wall=100.0, chunk=12000),
        run(concurrency=4, wall=140.0, chunk=12000),
    ]

    assert should_stop_climbing(completed, point(8, chunk=3000)) is False


def test_a_regression_on_another_document_does_not_stop_this_one() -> None:
    completed = [
        run(concurrency=2, wall=100.0, document="other"),
        run(concurrency=4, wall=140.0, document="other"),
    ]

    assert should_stop_climbing(completed, point(8)) is False


def test_fewer_than_two_concurrencies_cannot_show_a_reversal() -> None:
    assert should_stop_climbing([run(concurrency=1, wall=100.0)], point(2)) is False
    assert should_stop_climbing([], point(2)) is False


def test_the_lowest_concurrency_is_never_skipped() -> None:
    """K=1 is the baseline column of every grid, so it runs whatever the
    completed runs say."""
    completed = [run(concurrency=2, wall=100.0), run(concurrency=4, wall=140.0)]

    assert should_stop_climbing(completed, point(1)) is False


def test_a_noisy_repeat_at_one_concurrency_does_not_read_as_a_reversal() -> None:
    """I3: with repeats > 1 -- 3 is the shipped value -- "the two highest
    completed concurrencies" used to mean the two highest completed *runs*,
    which for a document/chunk-size pair with more than one repeat at the
    top concurrency are usually two repeats of the *same* K, not two
    different Ks.

    K=2 repeats three times at 100s each (median 100). K=4 repeats three
    times at 60, 60 and 200s (median 60 -- one noisy repeat, two fast ones).
    Sorted by concurrency with ties in insertion order, the old
    implementation's `comparable[-2]` and `comparable[-1]` are K=4's own
    60s and 200s runs: 200 > 60 stopped the climb without ever comparing to
    K=2. Grouped by concurrency and reduced to a median, K=4 (60) is faster
    than K=2 (100), so the climb correctly continues.
    """
    completed = [
        run(concurrency=2, wall=100.0),
        run(concurrency=2, wall=100.0),
        run(concurrency=2, wall=100.0),
        run(concurrency=4, wall=60.0),
        run(concurrency=4, wall=60.0),
        run(concurrency=4, wall=200.0),
    ]

    assert should_stop_climbing(completed, point(8)) is False
