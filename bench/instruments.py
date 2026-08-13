"""The only seam into `build_graph` before the progress port exists.

`LlmProvider` is a single-method protocol, so wrapping it is how the harness
learns anything about a run in progress. That buys a call count and the
model time spent under whichever phase label is current; it does **not** buy
a genuine per-phase *split*, and it does **not** buy time-to-first-entity,
because a returned completion is not a mapped entity and the wrapper cannot
see the merge. That field stays `None` until deliverable B rather than being
estimated here -- an estimate recorded in the field B will fill makes B's
improvement unreadable.

The clock is a parameter so the tests assert literal durations against a
clock they advance by hand, rather than sleeping and asserting a tolerance.
"""

from __future__ import annotations

from collections import defaultdict
from time import perf_counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

    from redstring import LlmProvider


class TimingProvider:
    """Count and time the calls an extraction makes, attributing each to a phase.

    Satisfies `LlmProvider` structurally; the pipeline cannot tell it apart
    from the adapter it wraps, which is the point -- an instrument that
    changes the run measures itself.
    """

    def __init__(self, inner: LlmProvider, *, clock: Callable[[], float] = perf_counter) -> None:
        self._inner = inner
        self._clock = clock
        self._elapsed: defaultdict[str, float] = defaultdict(float)
        self.calls = 0
        #: Which phase model time accumulates under -- a key for
        #: `elapsed_in`, nothing more. Nothing outside this class ever sets
        #: it to anything but its default: consolidation happens *inside*
        #: `build_graph`, and per ADR 0015 `build_graph` makes no adjudicator
        #: calls at all, so there is no call in the runner during which
        #: setting `phase = "consolidate"` would attribute anything real.
        #: That is why `RunMetrics.consolidate_s` is `None` rather than a
        #: number this attribute could ever produce.
        self.phase = "extract"

    @property
    def model(self) -> str:
        """The wrapped provider's model, so provenance is unaffected."""
        return self._inner.model

    def elapsed_in(self, phase: str) -> float:
        """Time inside model calls made while `phase` was set."""
        return self._elapsed[phase]

    async def extract[S: BaseModel](
        self, text: str, schema: type[S], *, system_prompt: str | None = None
    ) -> S:
        """Forward the call, recording what it cost even when it raises.

        The bookkeeping is in a `finally` deliberately: a failed call is model
        time that was spent, and a wrapper that records only successes reports
        an idle model for the run that most needs explaining.
        """
        started = self._clock()
        self.calls += 1
        current = self.phase
        try:
            return await self._inner.extract(text, schema, system_prompt=system_prompt)
        finally:
            self._elapsed[current] += self._clock() - started
