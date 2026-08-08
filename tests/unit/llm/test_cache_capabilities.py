"""Half a `Cache` is enough for each consumer, and the halves are disjoint.

`ports/cache.py` has argued "two capabilities, not one" in prose since it was
written, while declaring one flat eight-method protocol. Prose is not a
constraint. Nobody has yet been caught out by the gap -- both adapters in this
tree implement the whole port -- but that is the `recurring-defects.md` §3
state exactly: a rule that holds only because nobody has tested it is
indistinguishable from no rule, and the cost lands on the first author writing
an adapter to get *distributed circuit breaking*, who under the flat port owes
a sliding-window hit log they will never call.

These tests are what makes the paragraph binding. The two doubles below
implement **one half each and nothing of the other** -- `BreakerCache` has no
`record_hit`, `LimiterCache` has no `get` -- so a consumer that quietly
reached across the split would fail with `AttributeError` here rather than in
whatever deployment first swapped in a narrow adapter.

Neither double subclasses anything, which is the same reasoning as
`tests/unit/consolidation/test_substitution.py`: a double built by subclassing
`MemoryCache` would satisfy the whole port however the protocols were
declared, and could not tell you the split held.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self
from uuid import uuid4

import pytest

from redstring.llm.circuit_breaker import CircuitBreaker
from redstring.llm.rate_limiter import RateLimiter, RateLimitExceeded
from redstring.ports.cache import Cache, HitWindow, KeyValueCache

if TYPE_CHECKING:
    from types import TracebackType


class Lifetime:
    """The release half every capability inherits from `AsyncClosable`.

    A double claiming to *be* a capability has to satisfy all of it, including
    the part ADR 0028 added -- otherwise the `isinstance` assertions below stop
    saying anything about segregation and start reporting a missing `close`.
    These doubles hold nothing, so all three are no-ops.
    """

    async def close(self) -> None: ...

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


class BreakerCache(Lifetime):
    """`KeyValueCache` and not one method more."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.counters: dict[str, int] = {}
        self.closed = False

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ttl_seconds: float | None = None) -> None:
        self.values[key] = value

    async def increment(self, key: str, *, ttl_seconds: float | None = None) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)
        self.counters.pop(key, None)

    async def close(self) -> None:
        self.closed = True


class LimiterCache(Lifetime):
    """`HitWindow` and not one method more."""

    def __init__(self) -> None:
        self.hits: dict[str, list[float]] = {}
        self.closed = False

    async def record_hit(self, key: str, *, at: float, ttl_seconds: float) -> None:
        self.hits.setdefault(key, []).append(at)

    async def count_hits(self, key: str, *, since: float) -> int:
        return len([at for at in self.hits.get(key, []) if at >= since])

    async def oldest_hit(self, key: str, *, since: float) -> float | None:
        within = [at for at in self.hits.get(key, []) if at >= since]
        return min(within) if within else None

    async def close(self) -> None:
        self.closed = True


class TestTheHalvesAreDisjoint:
    def test_neither_double_satisfies_the_whole_port(self) -> None:
        # The point of the module. If either of these became a `Cache`, the
        # double would have grown the other half and every test below would
        # be back to exercising a full adapter.
        assert not isinstance(BreakerCache(), Cache)
        assert not isinstance(LimiterCache(), Cache)

    def test_each_double_satisfies_exactly_its_own_capability(self) -> None:
        assert isinstance(BreakerCache(), KeyValueCache)
        assert not isinstance(BreakerCache(), HitWindow)

        assert isinstance(LimiterCache(), HitWindow)
        assert not isinstance(LimiterCache(), KeyValueCache)


class TestEachConsumerNeedsOnlyItsHalf:
    async def test_the_breaker_trips_and_resets_on_a_key_value_cache_alone(self) -> None:
        cache = BreakerCache()
        breaker = CircuitBreaker(failure_threshold=2, cache=cache)

        assert await breaker.allow_request()
        await breaker.record_failure()
        await breaker.record_failure()

        # Non-vacuous in both directions: it must refuse *after* the
        # threshold and have allowed *before* it, or a breaker that never
        # opens and one that never closes both pass.
        assert not await breaker.allow_request()

        await breaker.reset()
        assert await breaker.allow_request()

    async def test_the_limiter_limits_on_a_hit_window_alone(self) -> None:
        cache = LimiterCache()
        limiter = RateLimiter(rpm=2, cache=cache)
        tenant = uuid4()

        await limiter.acquire(tenant)
        await limiter.acquire(tenant)
        with pytest.raises(RateLimitExceeded):
            await limiter.acquire(tenant)

        # Without this the refusal proves nothing -- a limiter that recorded
        # no hits and refused everything would pass the three lines above.
        assert len(cache.hits[limiter._key(tenant)]) == 2

    async def test_neither_consumer_closes_a_cache_it_was_given(self) -> None:
        """Inverted by B108, and the reason it existed still holds.

        This read `assert breaker_cache.closed` until `close()` narrowed to
        "release only a cache I created" -- the fix for one shared
        `RedisCache` being closed out from under whichever component had not
        finished yet. See `tests/unit/llm/test_resilience_cache_ownership.py`
        for the behavioural regression; what is asserted *here* is the half
        this module is about, which is that neither consumer reaches across
        the capability split to do it.

        `close` is still in both halves of the port rather than in a lifecycle
        protocol of its own, and that claim is now carried structurally by
        `test_each_double_satisfies_exactly_its_own_capability`: a double
        lacking `close` would fail its `isinstance` check.
        """
        breaker_cache, limiter_cache = BreakerCache(), LimiterCache()

        await CircuitBreaker(cache=breaker_cache).close()
        await RateLimiter(rpm=1, cache=limiter_cache).close()

        assert not breaker_cache.closed
        assert not limiter_cache.closed


class TestReachingAcrossTheSplitFails:
    @pytest.mark.parametrize(
        ("double", "absent"),
        [(BreakerCache(), "record_hit"), (LimiterCache(), "get")],
    )
    def test_the_other_half_is_genuinely_absent(self, double: object, absent: str) -> None:
        # Guards the guard. If a double quietly grew the method it is supposed
        # to lack, the consumer tests above would pass while proving nothing
        # about segregation.
        assert not hasattr(double, absent)
