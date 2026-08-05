"""`RedisCache` against the shared `Cache` compliance suite.

**This adapter shipped without ever running its port's contract**, which made
it the one implementation in this repository whose agreement with its sibling
was assumed rather than measured. `BACKLOG.md` B41 tracked that; this module
closes it.

The gap mattered more here than it would elsewhere, because
`tests/compliance/cache.py` was written *about this adapter*. Its docstring
names the divergence it exists to catch — a Redis client left at its defaults
returns `bytes`, so a caller comparing `await cache.get(k) == "open"` matches
in every `MemoryCache` test and never matches in production — and until now
nothing ran that assertion against Redis. An in-memory reference more
forgiving than the real backend is the failure mode the whole directory
exists to prevent, and the one adapter that could demonstrate it was excused.

Start the backend deliberately::

    docker compose -f docker-compose.test.yml up -d redis
    uv run pytest -m integration tests/integration/llm/test_redis_cache.py

Port 6381, not 6379: a compliance run **flushes the database between tests**,
so it must not be able to reach a Redis anyone is using for anything else.
Same reasoning as neo4j's 7688 and postgres's 5434. Override with
`KG_TEST_REDIS_URL`.
"""

from __future__ import annotations

import os

import pytest

from redstring.llm.cache.redis import RedisCache
from redstring.ports.cache import Cache
from tests.compliance.cache import CacheCompliance

pytestmark = pytest.mark.integration

REDIS_URL = os.environ.get("KG_TEST_REDIS_URL", "redis://localhost:6381/0")


async def _probe() -> RedisCache | None:
    """A connected cache, or `None` if Redis cannot serve a round trip.

    A `PING` is not enough, for the same reason the Neo4j probe runs
    `RETURN 1` rather than connecting: this suite is about what values come
    back, so the probe writes one and reads it. A server that accepts
    connections but rejects writes (a replica, a full instance) would
    otherwise fail every test instead of skipping.
    """
    try:
        cache = RedisCache.from_url(REDIS_URL)
        await cache.set("__probe__", "ok", ttl_seconds=5)
        if await cache.get("__probe__") != "ok":
            await cache.close()
            return None
    except Exception:
        return None
    return cache


@pytest.fixture
async def cache():
    """A cache over an empty database, per test.

    Flushed rather than namespaced by key prefix, because the suite asserts
    on `increment` counters and TTLs whose keys it chooses itself -- a prefix
    would have to be threaded through the contract, which is exactly the
    "edit the shared body to make one adapter pass" move
    `recurring-defects.md` §1 calls the defect rather than the fix.
    """
    probe = await _probe()
    if probe is None:
        pytest.skip(
            f"Redis at {REDIS_URL} did not round-trip a value. Start it with "
            f"`docker compose -f docker-compose.test.yml up -d redis`."
        )
    await probe._redis.flushdb()
    try:
        yield probe
    finally:
        await probe.close()


class TestRedisCache(CacheCompliance):
    """The whole compliance suite, unchanged, against real Redis."""


async def test_the_redis_cache_satisfies_the_port(cache: RedisCache):
    assert isinstance(cache, Cache)
