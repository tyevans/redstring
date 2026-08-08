"""The in-process `Cache`. The default, so the library needs no infrastructure.

Correct for one process, and one process is what most callers of a library
have. It is not a stand-in that "works well enough for tests": a rate limiter
backed by this genuinely limits, and a circuit breaker backed by this
genuinely opens. What it cannot do is let two processes agree, which is the
one thing `RedisCache` adds.

## Expiry is lazy, and that is a real design choice

Nothing sweeps. A key expires the moment someone asks for it after its
deadline. The alternative is a background task, which a library must not start
on a caller's event loop uninvited -- and which would then have to be shut
down, on a path callers forget.

The cost is that keys nobody asks about again occupy memory until the cache
is dropped. For the two callers here -- one entry per tenant, one per circuit
-- that is bounded by things the deployment already has a bounded number of.
`record_hit` is the exception and does prune, because its series grows with
*traffic* rather than with tenants.
"""

from __future__ import annotations

import bisect
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from collections.abc import MutableMapping
    from types import TracebackType


class MemoryCache:
    """A `Cache` in a dict. Single-process, no dependencies, no I/O."""

    def __init__(self, *, clock: float | None = None) -> None:
        """Build an empty cache.

        Args:
            clock: Ignored, and present only to make the constructor's
                signature honest about what this class does *not* do -- it
                never reads a clock of its own. Expiry deadlines come from
                the `ttl_seconds` the caller supplies, measured against
                `time.monotonic` at write time; window times come from the
                caller's `at`/`since`. Kept so a reader who expects a clock
                parameter finds this note instead of a hidden `time.time()`.
        """
        self._values: MutableMapping[str, tuple[str, float | None]] = {}
        self._hits: MutableMapping[str, tuple[list[float], float | None]] = {}
        self._clock = clock

    @staticmethod
    def _now() -> float:
        from time import monotonic

        return monotonic()

    def _deadline(self, ttl_seconds: float | None) -> float | None:
        return None if ttl_seconds is None else self._now() + ttl_seconds

    def _live(self, key: str) -> str | None:
        found = self._values.get(key)
        if found is None:
            return None
        value, expires_at = found
        if expires_at is not None and expires_at <= self._now():
            del self._values[key]
            return None
        return value

    async def get(self, key: str) -> str | None:
        return self._live(key)

    async def set(self, key: str, value: str, *, ttl_seconds: float | None = None) -> None:
        self._values[key] = (value, self._deadline(ttl_seconds))

    async def increment(self, key: str, *, ttl_seconds: float | None = None) -> int:
        """Add one, keeping the existing deadline when the key already exists.

        Refreshing the TTL on every increment would mean a counter under
        continuous load never expires -- and a circuit breaker's failure count
        that never decays eventually opens the circuit on failures that are
        minutes apart.
        """
        current = self._live(key)
        if current is None:
            self._values[key] = ("1", self._deadline(ttl_seconds))
            return 1
        # A non-numeric value here is a caller mixing `set` and `increment` on
        # one key; Redis raises for it, so raising keeps the two adapters
        # agreeing rather than silently resetting the count to 1.
        total = int(current) + 1
        self._values[key] = (str(total), self._values[key][1])
        return total

    async def delete(self, key: str) -> None:
        self._values.pop(key, None)
        self._hits.pop(key, None)

    async def record_hit(self, key: str, *, at: float, ttl_seconds: float) -> None:
        series, _ = self._hits.get(key, ([], None))
        # Insertion-sorted rather than appended: callers may record slightly
        # out of order under concurrency, and `count_hits` binary-searches.
        bisect.insort(series, at)
        self._hits[key] = (series, self._deadline(ttl_seconds))

    def _live_series(self, key: str) -> list[float]:
        found = self._hits.get(key)
        if found is None:
            return []
        series, expires_at = found
        if expires_at is not None and expires_at <= self._now():
            del self._hits[key]
            return []
        return series

    async def count_hits(self, key: str, *, since: float) -> int:
        series = self._live_series(key)
        if not series:
            return 0
        # Prune in place: this series grows with traffic rather than with the
        # number of tenants, so it is the one thing lazy expiry cannot bound.
        first = bisect.bisect_left(series, since)
        if first:
            del series[:first]
        return len(series)

    async def oldest_hit(self, key: str, *, since: float) -> float | None:
        series = self._live_series(key)
        first = bisect.bisect_left(series, since)
        return series[first] if first < len(series) else None

    async def close(self) -> None:
        """Drop everything. Nothing to release, but the port promises it."""
        self._values.clear()
        self._hits.clear()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()
