"""What one timed run produced, and the one piece of arithmetic in the harness.

`event_gaps_s` is stored as the whole list and summarised on the way out.
Perceived responsiveness is a distribution: a run whose reports arrive at
3s, 3s, 3s and one that arrives at 1s, 1s, 7s share a mean and feel nothing
alike, so the mean is the one summary that must not be the only one kept.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bench.config import SweepPoint


@dataclass(frozen=True, slots=True)
class GapSummary:
    """Nearest-rank percentiles over the intervals between progress reports."""

    p50: float
    p95: float
    maximum: float
    count: int


def _nearest_rank(ordered: Sequence[float], percentile: float) -> float:
    """The value at the nearest rank, with no interpolation.

    Interpolating between two samples invents a gap that no report actually
    took, which is the wrong shape for a measurement whose whole purpose is
    "how long did a human wait".
    """
    rank = max(1, ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def summarise_gaps(gaps: Sequence[float]) -> GapSummary | None:
    """Summarise inter-report intervals, or `None` when there is no rhythm.

    Fewer than two gaps is reported as absent rather than as zeroes: one
    interval describes no cadence, and a `p95` derived from a single sample
    reads exactly like one derived from a hundred.
    """
    if len(gaps) < 2:
        return None
    ordered = sorted(gaps)
    return GapSummary(
        p50=_nearest_rank(ordered, 0.50),
        p95=_nearest_rank(ordered, 0.95),
        maximum=ordered[-1],
        count=len(ordered),
    )


@dataclass(frozen=True, slots=True)
class RunMetrics:
    """One timed run of one document at one point in the sweep."""

    point: SweepPoint
    wall_clock_s: float
    #: `None` until the progress port lands (deliverable B). Unmeasurable from
    #: outside `build_graph`, and deliberately not approximated from provider
    #: call counts -- an estimate recorded here would make B's improvement
    #: unreadable against it.
    time_to_first_entity_s: float | None
    event_gaps_s: tuple[float, ...]
    model_calls: int
    extract_s: float
    consolidate_s: float
    chunks: int
    entities: int
    relationships: int
    failed_chunks: int
    unresolved_relationships: int
    #: Normalised entity names, kept for the stability comparison across
    #: repeats. Sorted by the runner so two runs are comparable directly.
    entity_names: tuple[str, ...]

    @property
    def gaps(self) -> GapSummary | None:
        return summarise_gaps(self.event_gaps_s)
