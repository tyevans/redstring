"""When to stop climbing concurrency.

If K=4 is slower than K=2, the backend is queueing and K=8 measures the queue.
That is worth recording once and not worth twenty more minutes, so the sweep
skips the remaining higher values -- for that document at that chunk size, and
nowhere else. The curve is per configuration: a backend that queues at K=4
with 12000-character chunks may not at 3000.

The comparison is deliberately the **two highest completed concurrencies**,
not a search for any earlier reversal in the climb. A recovered climb (K=2 at
100s, K=4 at 140s, K=8 at 90s) keeps climbing under this rule: only the last
step is read as a verdict on the next one, because a queueing state that
clears is exactly as real a result as one that does not, and only the most
recent comparable pair says anything about what K=8 would face.

With `policy.repeats` above 1 -- the shipped value is 3 -- "the two highest
completed concurrencies" has to mean two *concurrencies*, not two repeats of
one. Runs are grouped by concurrency first and each group is reduced to its
**median** wall clock before the same two-highest-values comparison runs, so
a noisy repeat at one K can no longer be mistaken for a reversal between two
different Ks.
"""

from __future__ import annotations

from statistics import median
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bench.config import SweepPoint
    from bench.metrics import RunMetrics


def should_stop_climbing(runs: Sequence[RunMetrics], point: SweepPoint) -> bool:
    """True when a completed concurrency already showed a higher one is slower.

    `point.concurrency` at or below every completed concurrency is never
    skipped: the lowest column is the baseline the rest of the grid is read
    against.
    """
    by_concurrency: dict[int, list[float]] = {}
    for run in runs:
        if run.point.document_id == point.document_id and run.point.chunk_size == point.chunk_size:
            by_concurrency.setdefault(run.point.concurrency, []).append(run.wall_clock_s)

    comparable = sorted(
        (
            (concurrency, median(walls))
            for concurrency, walls in by_concurrency.items()
            if concurrency < point.concurrency
        )
    )
    if len(comparable) < 2:
        return False
    (_, second_highest_wall), (_, highest_wall) = comparable[-2:]
    return highest_wall > second_highest_wall
