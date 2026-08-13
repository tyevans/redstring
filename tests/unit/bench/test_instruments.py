"""The wrapper is the only thing that can see inside `build_graph` before the
progress port exists, so what it measures has to be pinned exactly."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from bench.instruments import TimingProvider


class Answer(BaseModel):
    value: str = "x"


class FakeClock:
    """A clock that advances only when a test says so."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class ScriptedProvider:
    """An `LlmProvider` that advances a clock instead of doing work."""

    def __init__(self, clock: FakeClock, *, takes: float) -> None:
        self._clock = clock
        self._takes = takes
        self.seen: list[str] = []

    @property
    def model(self) -> str:
        return "scripted-model"

    async def extract(self, text: str, schema: Any, *, system_prompt: str | None = None) -> Any:
        self.seen.append(text)
        self._clock.now += self._takes
        return schema()


async def test_it_counts_the_calls_it_forwards() -> None:
    clock = FakeClock()
    provider = TimingProvider(ScriptedProvider(clock, takes=1.0), clock=clock)

    await provider.extract("a", Answer)
    await provider.extract("b", Answer)

    assert provider.calls == 2


async def test_it_reports_the_time_the_calls_took() -> None:
    """Two calls of 1.5s each is 3.0s. A literal, not `2 * takes`."""
    clock = FakeClock()
    provider = TimingProvider(ScriptedProvider(clock, takes=1.5), clock=clock)

    await provider.extract("a", Answer)
    await provider.extract("b", Answer)

    assert provider.elapsed_in("extract") == 3.0


async def test_time_between_calls_is_not_counted_as_model_time() -> None:
    """The wrapper measures the model, not the run.

    An implementation timing from the first call to the last would report
    5.0s here and be indistinguishable from the correct one on any test where
    the calls are back to back.
    """
    clock = FakeClock()
    provider = TimingProvider(ScriptedProvider(clock, takes=1.0), clock=clock)

    await provider.extract("a", Answer)
    clock.now += 3.0  # merging, mapping, whatever the pipeline does between calls
    await provider.extract("b", Answer)

    assert provider.elapsed_in("extract") == 2.0


async def test_calls_are_attributed_to_the_phase_that_was_running() -> None:
    """Extraction and consolidation both call the same provider. Without the
    phase label the sixteen minutes cannot be split, which is the first
    question to ask of a slow run."""
    clock = FakeClock()
    provider = TimingProvider(ScriptedProvider(clock, takes=2.0), clock=clock)

    provider.phase = "extract"
    await provider.extract("a", Answer)
    provider.phase = "consolidate"
    await provider.extract("b", Answer)

    assert provider.elapsed_in("extract") == 2.0
    assert provider.elapsed_in("consolidate") == 2.0
    assert provider.elapsed_in("embed") == 0.0


async def test_a_call_is_attributed_to_the_phase_it_started_in() -> None:
    """The phase is captured before the await, not read after it.

    A wrapper reading `self.phase` in its `finally` block passes every test
    where the caller sets the phase once and leaves it alone -- which is every
    other test in this file. Here the provider changes the phase mid-call, so
    the two implementations disagree: capture-first attributes the 2.0s to
    `extract`, read-after attributes it to `consolidate`.
    """

    class PhaseFlippingProvider:
        @property
        def model(self) -> str:
            return "flipping"

        async def extract(self, text: str, schema: Any, *, system_prompt: str | None = None) -> Any:
            clock.now += 2.0
            provider.phase = "consolidate"
            return schema()

    clock = FakeClock()
    provider = TimingProvider(PhaseFlippingProvider(), clock=clock)
    provider.phase = "extract"

    await provider.extract("a", Answer)

    assert provider.elapsed_in("extract") == 2.0
    assert provider.elapsed_in("consolidate") == 0.0


async def test_a_failing_call_is_still_counted_and_still_timed() -> None:
    """A run that fails halfway must not report that the model was idle.

    A wrapper recording after the await never sees a failure, and every test
    with a healthy provider passes against it.
    """

    class Failing:
        @property
        def model(self) -> str:
            return "failing"

        async def extract(self, text: str, schema: Any, *, system_prompt: str | None = None) -> Any:
            clock.now += 4.0
            raise RuntimeError("boom")

    clock = FakeClock()
    provider = TimingProvider(Failing(), clock=clock)

    with pytest.raises(RuntimeError):
        await provider.extract("a", Answer)

    assert provider.calls == 1
    assert provider.elapsed_in("extract") == 4.0


async def test_the_wrapped_model_name_shows_through() -> None:
    """`Entity.provenance.model` comes from here. A wrapper reporting its own
    name would stamp every benchmarked entity with the wrong provenance."""
    clock = FakeClock()

    assert TimingProvider(ScriptedProvider(clock, takes=0.0), clock=clock).model == "scripted-model"


async def test_the_arguments_reach_the_inner_provider_unchanged() -> None:
    clock = FakeClock()
    inner = ScriptedProvider(clock, takes=0.0)

    await TimingProvider(inner, clock=clock).extract("the text", Answer, system_prompt="sys")

    assert inner.seen == ["the text"]
