"""The Redis `Cache`, for callers whose processes must agree with each other.

The only thing this adds over `MemoryCache` is that several processes share
one view, which is exactly when a rate limit or a circuit breaker stops being
per-worker and starts being real.

## The hit window is a sorted set, and the port hides that

`record_hit`/`count_hits`/`oldest_hit` map onto `ZADD`/`ZCOUNT`/`ZRANGE` with
the epoch time as the score. That mapping is why the port is phrased as
"events in a time window" rather than as those commands: an in-memory
implementation of `zadd` would be reimplementing Redis, whereas an in-memory
implementation of "how many events since t" is a list and a binary search.

`ZREMRANGEBYSCORE` prunes on read rather than on write, so a series that
nobody asks about is bounded by its TTL rather than by a sweep.

## `decode_responses=True`, deliberately

The port says `get` returns `str | None`. A client left at its default returns
`bytes`, and a caller comparing against a `str` literal would then silently
never match -- passing every test against `MemoryCache` and failing only in
the deployment that has Redis.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    import redis.asyncio


class RedisCache:
    """A `Cache` over Redis. Construct with a URL, or with a client you own."""

    def __init__(self, client: redis.asyncio.Redis, *, owns_client: bool = False) -> None:
        """Wrap an existing client.

        Args:
            client: Must be configured with `decode_responses=True`; see the
                module docstring for what happens otherwise.
            owns_client: Whether `close` should close it. False by default,
                because a shared client closed by whichever component
                finished first is a bug that only appears under shutdown.
        """
        self._redis = client
        self._owns_client = owns_client

    @classmethod
    def from_url(cls, url: str) -> RedisCache:
        """Build a cache and the client it owns.

        Raises:
            ImportError: `redis` is not installed.
        """
        try:
            import redis.asyncio as redis_asyncio
        except ImportError as error:  # pragma: no cover -- needs redis absent
            raise ImportError(
                "RedisCache.from_url needs redis: install `redstring[redis]`"
            ) from error

        # redis-py ships no inline annotations for `from_url`.
        client: Any = redis_asyncio.from_url(  # type: ignore[no-untyped-call]
            url, encoding="utf-8", decode_responses=True
        )
        return cls(client, owns_client=True)

    async def get(self, key: str) -> str | None:
        value: str | None = await self._redis.get(key)
        return value

    async def set(self, key: str, value: str, *, ttl_seconds: float | None = None) -> None:
        if ttl_seconds is None:
            await self._redis.set(key, value)
        else:
            # `px` rather than `ex`: the port's TTL is a float, and `ex`
            # truncates to whole seconds -- a 0.5s TTL would become no TTL.
            await self._redis.set(key, value, px=max(1, int(ttl_seconds * 1000)))

    async def increment(self, key: str, *, ttl_seconds: float | None = None) -> int:
        total: int = await self._redis.incr(key)
        # Only when the key was just created. `EXPIRE` on every increment is a
        # counter that never expires under load, which is when it matters.
        if ttl_seconds is not None and total == 1:
            await self._redis.pexpire(key, max(1, int(ttl_seconds * 1000)))
        return total

    async def delete(self, key: str) -> None:
        await self._redis.delete(key, _hits(key))

    async def record_hit(self, key: str, *, at: float, ttl_seconds: float) -> None:
        window = _hits(key)
        async with self._redis.pipeline(transaction=True) as pipe:
            # The member must be unique or two hits at the same instant
            # collapse into one, which under-counts exactly when a burst is
            # what the caller is trying to detect.
            #
            # `uuid4()` rather than anything derived from `self`. This was
            # `f"{at!r}:{id(self):x}"`, which is constant for the life of the
            # cache object -- so it told two *instances* apart and never two
            # hits, which is the only thing it was there to do. The comment
            # above was already correct and the code under it was not;
            # `CacheCompliance` had simply never been run against this
            # adapter (BACKLOG B41), and `MemoryCache` cannot exhibit the bug
            # because it appends to a list.
            #
            # `id()` would have been wrong across processes as well: it is a
            # memory address, so two callers sharing one Redis can collide on
            # it outright.
            pipe.zadd(window, {f"{at!r}:{uuid4().hex}": at})
            pipe.pexpire(window, max(1, int(ttl_seconds * 1000)))
            await pipe.execute()

    async def count_hits(self, key: str, *, since: float) -> int:
        window = _hits(key)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(window, "-inf", f"({since}")
            pipe.zcount(window, since, "+inf")
            _, count = await pipe.execute()
        return int(count)

    async def oldest_hit(self, key: str, *, since: float) -> float | None:
        found = await self._redis.zrangebyscore(
            _hits(key), since, "+inf", start=0, num=1, withscores=True
        )
        return float(found[0][1]) if found else None

    async def close(self) -> None:
        if self._owns_client:
            await self._redis.aclose()


def _hits(key: str) -> str:
    """The sorted-set key for `key`'s hit window.

    Namespaced apart from the plain value so that `set` and `record_hit` on
    one key cannot collide -- Redis would reject the second with a WRONGTYPE
    error, which `MemoryCache` has no way to reproduce.
    """
    return f"{key}:hits"
