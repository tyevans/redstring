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
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bench.config import SweepPoint
    from bench.metrics import RunMetrics


def should_stop_climbing(runs: Sequence[RunMetrics], point: SweepPoint) -> bool:
    """True when a completed run already showed higher concurrency is slower.

    `point.concurrency` at or below every completed concurrency is never
    skipped: the lowest column is the baseline the rest of the grid is read
    against.
    """
    comparable = sorted(
        (
            run
            for run in runs
            if run.point.document_id == point.document_id
            and run.point.chunk_size == point.chunk_size
            and run.point.concurrency < point.concurrency
        ),
        key=lambda run: run.point.concurrency,
    )
    if len(comparable) < 2:
        return False
    return comparable[-1].wall_clock_s > comparable[-2].wall_clock_s
