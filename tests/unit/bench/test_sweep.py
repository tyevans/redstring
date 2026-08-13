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
