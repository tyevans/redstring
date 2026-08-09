"""`MemoryCache` against the shared `Cache` compliance suite.

Everything about behaviour lives in `redstring.testing.cache`. This module
supplies a cache and adds only what is specific to the in-memory adapter:
that it is the default a caller gets with no infrastructure, and that its
lazy expiry does not leak the one series that grows with traffic.
"""

from __future__ import annotations

import pytest

from redstring.llm.cache.memory import MemoryCache
from redstring.ports.cache import Cache
from redstring.testing.cache import NOW, CacheCompliance


@pytest.fixture
def cache() -> MemoryCache:
    return MemoryCache()


class TestMemoryCache(CacheCompliance):
    """The compliance suite, run against the in-process adapter."""


def test_the_memory_cache_satisfies_the_port():
    assert isinstance(MemoryCache(), Cache)


async def test_it_needs_no_arguments_at_all(cache):
    """The claim that makes it a usable default rather than a test double.

    A caller with no Redis must get a working rate limiter and circuit
    breaker without configuring anything, or "the library runs with no
    infrastructure" is not true.
    """
    await cache.set("k", "v")

    assert await cache.get("k") == "v"


async def test_counting_hits_discards_the_ones_that_have_aged_out(cache):
    """Lazy expiry cannot bound this series, so `count_hits` prunes it.

    Values and counters are bounded by the number of tenants and circuits.
    A hit series grows with *traffic*, so without pruning a busy tenant's
    window would hold every request ever made until the TTL fired.

    Asserted through `oldest_hit` rather than by reading a private list: what
    matters is that the aged-out hits are gone, not which attribute holds
    them.
    """
    for offset in range(50):
        await cache.record_hit("tenant", at=NOW - 600 + offset, ttl_seconds=3600)
    await cache.record_hit("tenant", at=NOW, ttl_seconds=3600)

    assert await cache.count_hits("tenant", since=NOW - 60) == 1
    assert await cache.oldest_hit("tenant", since=0) == pytest.approx(NOW)


async def test_incrementing_a_key_holding_something_unnumeric_raises(cache):
    """Redis raises here, so this one must too or the adapters disagree.

    Silently resetting to 1 would be the tempting alternative and would hide
    a caller that had mixed `set` and `increment` on one key -- as a failure
    count that quietly restarts.
    """
    await cache.set("failures", "not a number")

    with pytest.raises(ValueError, match="not a number"):
        await cache.increment("failures")
