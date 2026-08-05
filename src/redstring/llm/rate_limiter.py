"""Per-tenant rate limiting for model calls, over the `Cache` port.

Rewritten in slice 6 from `extraction/rate_limiter.py`, which spoke Redis
sorted sets directly. The algorithm is unchanged -- a sliding window, counting
the hits in the last `window_seconds` -- but the sorted set now lives behind
`Cache`, so the default is `MemoryCache` and the library rate-limits correctly
with no infrastructure at all.

`OllamaRateLimiter` is gone with the vendor name. A rate limiter is not
Ollama-specific and never was; the class was named after the only thing that
happened to call it. Slice 3 made the same removal in `ExtractionMethod` and
for the same reason -- vendor names outlive the vendor's presence in the code.

## What a fixed-window counter would have cost

`Cache.increment` alone would support a much simpler limiter: one counter per
minute-bucket, incremented and expired. It is rejected because it lets through
twice the limit across a bucket boundary -- `rpm` calls at 11:59:59.9 and
`rpm` more at 12:00:00.1 -- and the reason to rate-limit a single-GPU local
model is precisely that twice the limit is what knocks it over.

## The clock is read once per call and passed down

`Cache`'s window methods take epoch floats rather than reading a clock. So
this class reads `datetime.now(UTC)` once and hands the same instant to every
call it makes, which keeps "count the window" and "when does the oldest hit
expire" consistent with each other -- two reads could disagree, and the
disagreement would surface as an occasional negative `retry_after`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from redstring.domain.exceptions import RedstringError
from redstring.llm.cache.memory import MemoryCache

if TYPE_CHECKING:
    from uuid import UUID

    from redstring.ports.cache import Cache

logger = logging.getLogger(__name__)

#: How long a tenant's hit series is kept after its last hit.
#:
#: A multiple of the window rather than the window itself: a series that
#: expired exactly at the window edge could drop hits that are still inside
#: it, which would silently raise the effective limit.
_SERIES_TTL_MULTIPLE = 2


class RateLimitExceeded(RedstringError):
    """The tenant has used its allowance for the current window.

    `retry_after` is the wait until the oldest hit in the window ages out,
    which is when a slot genuinely frees. A caller sleeping for it and
    retrying will succeed; a caller retrying immediately will not.
    """

    def __init__(self, message: str, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class RateLimiter:
    """A sliding-window, per-tenant limit on model calls."""

    def __init__(
        self,
        *,
        rpm: int,
        window_seconds: float = 60.0,
        cache: Cache | None = None,
        key_prefix: str = "kg:ratelimit",
    ) -> None:
        """Build a limiter.

        Args:
            rpm: Calls allowed per tenant per window. Required rather than
                read from settings: a library that silently limits a caller
                to a number they never chose is worse than one that asks.
            window_seconds: The window's width. Named `rpm` for the common
                case of 60; the two are independent so a caller can say "10
                per second".
            cache: Where the windows live. `MemoryCache` when None, which
                limits correctly for one process. Pass a `RedisCache` when
                several workers must share one allowance -- otherwise each
                gets its own, and the effective limit is `rpm` times the
                worker count.
            key_prefix: Namespace for the cache keys.

        Raises:
            ValueError: A non-positive `rpm` or window. `rpm=0` would refuse
                every call forever, which is never what a caller means.
        """
        if rpm <= 0:
            raise ValueError(f"rpm must be positive, got {rpm}")
        if window_seconds <= 0:
            raise ValueError(f"window_seconds must be positive, got {window_seconds}")
        self._rpm = rpm
        self._window = window_seconds
        self._cache: Cache = cache if cache is not None else MemoryCache()
        self._key_prefix = key_prefix

    def _key(self, tenant_id: UUID) -> str:
        return f"{self._key_prefix}:{tenant_id}"

    async def acquire(self, tenant_id: UUID) -> None:
        """Take a slot for `tenant_id`, or raise.

        Raises:
            RateLimitExceeded: The window is full. Nothing is recorded when
                it raises, so a caller that retries after `retry_after` is
                not penalised for having asked -- counting refused calls
                would make a client in a retry loop lock itself out
                indefinitely.
        """
        now = datetime.now(UTC).timestamp()
        key = self._key(tenant_id)
        since = now - self._window

        if await self._cache.count_hits(key, since=since) >= self._rpm:
            oldest = await self._cache.oldest_hit(key, since=since)
            retry_after = self._window if oldest is None else (oldest + self._window) - now
            logger.info(
                "Rate limit reached",
                extra={"tenant_id": str(tenant_id), "rpm": self._rpm},
            )
            raise RateLimitExceeded(
                f"tenant {tenant_id} has used {self._rpm} calls in "
                f"{self._window:g}s; retry in {max(retry_after, 0.0):.2f}s",
                retry_after=max(retry_after, 0.0),
            )

        await self._cache.record_hit(key, at=now, ttl_seconds=self._window * _SERIES_TTL_MULTIPLE)

    async def remaining(self, tenant_id: UUID) -> int:
        """Slots left in the current window. Never negative."""
        now = datetime.now(UTC).timestamp()
        used = await self._cache.count_hits(self._key(tenant_id), since=now - self._window)
        return max(0, self._rpm - used)

    async def close(self) -> None:
        """Release the cache. Only meaningful for one this limiter created."""
        await self._cache.close()
