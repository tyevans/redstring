"""One ceiling over every call the library makes against the endpoint.

The operator's constraint is the backend's queue depth. Batch structure alone
bounds only the extraction calls, so gleaning or embedding overlapping the
next batch turns a stated ceiling of four into six in flight.
"""

from __future__ import annotations

import asyncio

import pytest

from redstring.extraction.limiter import CallLimiter


async def test_it_admits_no_more_than_its_limit_at_once() -> None:
    limiter = CallLimiter(2)
    in_flight = 0
    peak = 0

    async def call() -> None:
        nonlocal in_flight, peak
        async with limiter:
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1

    await asyncio.gather(*(call() for _ in range(6)))

    assert peak == 2


async def test_a_raising_call_releases_its_slot() -> None:
    """A slot leaked on failure turns a transient error into a deadlock that
    looks like a hung model."""
    limiter = CallLimiter(1)

    with pytest.raises(RuntimeError):
        async with limiter:
            raise RuntimeError("boom")

    async with asyncio.timeout(1):
        async with limiter:
            pass


@pytest.mark.parametrize("bad", [0, -1])
def test_a_limit_below_one_is_refused(bad: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        CallLimiter(bad)
