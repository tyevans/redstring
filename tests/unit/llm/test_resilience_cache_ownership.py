"""Closing a breaker or a limiter must not close a cache it was handed.

`docs/how-to/harden-model-calls.md` documents one `RedisCache` passed to both a
`CircuitBreaker` and a `RateLimiter` as *the* way to make several processes
agree about a failing model. Both classes construct a `MemoryCache` when the
caller passes nothing, so each one *sometimes* owns its cache -- and before
this module neither tracked which case it was in, so `close()` closed
unconditionally and the documented arrangement was a bug: whichever component
shut down first took the other's state with it.

That is the same defect `RedisCache.owns_client` exists to prevent one layer
down, and it is fixed the same way, with `_owns_cache`.

**Why the shared cache is the whole test.** A version of
`test_closing_one_leaves_the_other_working` that gave each component its own
cache would pass against the broken code and the fixed code alike -- there
would be nothing for the wrong `close()` to damage. The two components have
to be looking at one object, which is `.claude/rules/recurring-defects.md` §4
and CLAUDE.md's failure-shapes table: an input on which both candidate
implementations agree is not testing the difference.

**Why `MemoryCache` and not a double.** `MemoryCache.close()` genuinely drops
every key, so the damage is observable as *behaviour* -- a limiter that
forgets a tenant's window, a breaker that forgets it was open -- rather than
as a flag some double set. The assertion is then independent of the mechanism
under test, which a `self.closed = True` double is not.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol, Self
from uuid import uuid4

import pytest

from redstring.llm.cache.memory import MemoryCache
from redstring.llm.circuit_breaker import CircuitBreaker, CircuitState
from redstring.llm.rate_limiter import RateLimiter, RateLimitExceeded

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType


class _Component(Protocol):
    """What both resilience components share, and all these tests need."""

    async def close(self) -> None: ...
    async def __aenter__(self) -> Self: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


#: Built with an injected cache, and with none, by the same call. Both classes
#: take `cache=None` to mean "make your own", so one signature covers them --
#: which is what lets every test below assert the same thing of both.
COMPONENTS: dict[str, Callable[[MemoryCache | None], _Component]] = {
    "CircuitBreaker": lambda cache: CircuitBreaker(failure_threshold=1, cache=cache),
    "RateLimiter": lambda cache: RateLimiter(rpm=1, cache=cache),
}

components = pytest.mark.parametrize("build", COMPONENTS.values(), ids=list(COMPONENTS))


class TestOneCacheSharedByBoth:
    """The B108 regression, stated as the arrangement the how-to recommends."""

    async def test_closing_the_breaker_leaves_the_limiters_window_intact(self) -> None:
        """Close one component; the other must still be looking at its state."""
        cache = MemoryCache()
        breaker = CircuitBreaker(failure_threshold=5, cache=cache)
        limiter = RateLimiter(rpm=1, cache=cache)
        tenant = uuid4()

        await limiter.acquire(tenant)
        await breaker.close()

        # The window is full, so the limiter must refuse. Against the
        # unconditional `close()` this cache had been emptied and the call
        # was admitted -- the tenant's allowance silently doubled because
        # something else shut down.
        with pytest.raises(RateLimitExceeded):
            await limiter.acquire(tenant)

    async def test_closing_the_limiter_leaves_the_breaker_open(self) -> None:
        """The mirror image, because the two halves of the cache are disjoint.

        `count_hits` and `get` read different structures inside `MemoryCache`,
        so a `close()` that cleared only one of them would pass the test above
        and fail this one. Neither direction implies the other.
        """
        cache = MemoryCache()
        breaker = CircuitBreaker(failure_threshold=1, cache=cache)
        limiter = RateLimiter(rpm=1, cache=cache)

        await breaker.record_failure()
        assert await breaker.state() is CircuitState.OPEN, "arrange failed: never opened"

        await limiter.close()

        # Against the unconditional `close()` the breaker read `CLOSED` here
        # and admitted traffic to a model it had just decided was down.
        assert await breaker.state() is CircuitState.OPEN
        assert not await breaker.allow_request()

    async def test_a_cache_of_its_own_is_still_closed(self) -> None:
        """The other half of the claim, or "never close" would pass too.

        `close()` narrowing to "release only what I created" is worthless if
        it narrowed all the way to nothing: a component that built its own
        `MemoryCache` still has to release it, and `MemoryCache.close()`
        dropping every key is what makes that observable without a double.
        """
        breaker = CircuitBreaker(failure_threshold=1)
        await breaker.record_failure()
        assert await breaker.state() is CircuitState.OPEN, "arrange failed: never opened"

        await breaker.close()

        assert await breaker.state() is CircuitState.CLOSED

    async def test_a_limiters_own_cache_is_still_closed(self) -> None:
        limiter = RateLimiter(rpm=1)
        tenant = uuid4()
        await limiter.acquire(tenant)
        assert await limiter.remaining(tenant) == 0, "arrange failed: nothing recorded"

        await limiter.close()

        assert await limiter.remaining(tenant) == 1


class TestOwnershipIsDecidedByTheConstructor:
    """One declaration site, and it is `cache is None`."""

    @components
    def test_a_component_given_a_cache_does_not_own_it(self, build) -> None:
        assert build(MemoryCache())._owns_cache is False

    @components
    def test_a_component_given_nothing_owns_what_it_built(self, build) -> None:
        assert build(None)._owns_cache is True


class TestTheBlockForm:
    """`async with`, matching the four adapters exactly.

    These mirror `tests/unit/test_adapters_close_on_block_exit.py`, which
    covers these two classes as well now that they carry an ownership flag.
    The cases here are the ones that need a *cache* rather than that module's
    generic resource double: what the block does to state a caller can still
    observe afterwards.
    """

    @components
    async def test_entering_yields_the_component_itself(self, build) -> None:
        component = build(None)

        async with component as entered:
            assert entered is component

    async def test_leaving_the_block_releases_a_cache_it_owns(self) -> None:
        async with CircuitBreaker(failure_threshold=1) as breaker:
            await breaker.record_failure()
            assert await breaker.state() is CircuitState.OPEN

        assert await breaker.state() is CircuitState.CLOSED

    async def test_leaving_the_block_leaves_an_injected_cache_alone(self) -> None:
        """The over-implementation this rejects is an `__aexit__` that closes
        the cache directly instead of going through `close()`."""
        cache = MemoryCache()
        limiter = RateLimiter(rpm=1, cache=cache)
        tenant = uuid4()

        async with CircuitBreaker(failure_threshold=5, cache=cache):
            await limiter.acquire(tenant)

        with pytest.raises(RateLimitExceeded):
            await limiter.acquire(tenant)

    @components
    async def test_an_exception_in_the_body_propagates(self, build) -> None:
        """An `__aexit__` returning a truthy value passes every test above and
        swallows whatever the body raised. This is what rejects it."""
        with pytest.raises(ZeroDivisionError):
            async with build(None):
                raise ZeroDivisionError("from the body")

    @components
    async def test_cancelling_the_task_still_cancels_it(self, build) -> None:
        """The suppression case that matters: a suppressing `__aexit__` makes
        a cancelled task complete normally, and the caller awaiting it never
        learns the work did not happen."""
        entered = asyncio.Event()

        async def body() -> None:
            async with build(None):
                entered.set()
                await asyncio.Event().wait()  # never set; only cancellation ends it

        task = asyncio.create_task(body())
        # Bounded for the reason the adapter module gives: a class with no
        # `__aexit__` raises before `entered.set()` runs, and a bare wait then
        # hangs the suite rather than failing it.
        try:
            await asyncio.wait_for(entered.wait(), timeout=5.0)
        except TimeoutError:
            await asyncio.wait_for(task, timeout=5.0)  # surfaces the real error
            pytest.fail("the block was never entered, and the body did not raise")
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert task.cancelled()
