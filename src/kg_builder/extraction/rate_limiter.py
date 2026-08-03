"""
Rate limiter for Ollama extraction requests.

Implements per-tenant rate limiting using Redis to prevent
overloading the local LLM and ensure fair resource sharing.
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

import redis.asyncio as redis

from kg_builder.config import settings

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded.

    Attributes:
        tenant_id: The tenant that exceeded the rate limit
        retry_after: Seconds until the rate limit resets
    """

    def __init__(self, tenant_id: UUID, retry_after: float):
        self.tenant_id = tenant_id
        self.retry_after = retry_after
        super().__init__(
            f"Rate limit exceeded for tenant {tenant_id}. Retry after {retry_after:.1f}s"
        )


class OllamaRateLimiter:
    """Per-tenant rate limiter for Ollama requests.

    Uses Redis sliding window algorithm to track request counts
    per tenant and enforce rate limits. This ensures fair resource
    sharing across tenants and prevents any single tenant from
    monopolizing the Ollama service.

    The sliding window algorithm provides smoother rate limiting
    compared to fixed windows by considering the full time window
    from the current moment.

    Example:
        limiter = OllamaRateLimiter()
        try:
            await limiter.acquire(tenant_id)  # Raises if rate limited
            # Make Ollama request
        except RateLimitExceeded as e:
            # Handle rate limiting
            await asyncio.sleep(e.retry_after)
    """

    def __init__(
        self,
        redis_url: str | None = None,
        rpm: int | None = None,
        window_seconds: int = 60,
    ):
        """Initialize rate limiter.

        Args:
            redis_url: Redis connection URL (defaults to settings.REDIS_URL)
            rpm: Requests per minute per tenant (defaults to settings.OLLAMA_RATE_LIMIT_RPM)
            window_seconds: Time window for rate limiting (default 60 seconds)
        """
        self._redis_url = redis_url or settings.REDIS_URL
        self._rpm = rpm or settings.OLLAMA_RATE_LIMIT_RPM
        self._window = window_seconds
        self._redis: redis.Redis | None = None

        logger.info(
            "OllamaRateLimiter initialized",
            extra={
                "redis_url": self._redis_url.split("@")[-1]
                if "@" in self._redis_url
                else self._redis_url,
                "rpm": self._rpm,
                "window_seconds": self._window,
            },
        )

    async def _get_redis(self) -> redis.Redis:
        """Get or create Redis connection.

        Returns:
            Redis client instance
        """
        if self._redis is None:
            self._redis = redis.from_url(self._redis_url)
        return self._redis

    def _get_key(self, tenant_id: UUID) -> str:
        """Get Redis key for tenant rate limit.

        Args:
            tenant_id: Tenant identifier

        Returns:
            Redis key string for the tenant's rate limit sorted set
        """
        return f"ollama_ratelimit:{tenant_id}"

    async def acquire(self, tenant_id: UUID) -> None:
        """Acquire rate limit slot.

        This method must be called before each Ollama request to enforce
        rate limiting. It uses a sliding window algorithm implemented
        with Redis sorted sets.

        Args:
            tenant_id: Tenant requesting extraction

        Raises:
            RateLimitExceeded: If rate limit is exceeded
        """
        r = await self._get_redis()
        key = self._get_key(tenant_id)
        now = datetime.now(UTC).timestamp()
        window_start = now - self._window

        async with r.pipeline(transaction=True) as pipe:
            # Remove old entries outside window
            await pipe.zremrangebyscore(key, 0, window_start)
            # Count requests in window
            await pipe.zcard(key)
            # Add current request (use timestamp as both score and member for uniqueness)
            await pipe.zadd(key, {str(now): now})
            # Set expiry to clean up old keys
            await pipe.expire(key, self._window)

            results = await pipe.execute()
            request_count = results[1]

        if request_count >= self._rpm:
            # Get oldest request to calculate retry time
            oldest = await r.zrange(key, 0, 0, withscores=True)
            if oldest:
                oldest_time = oldest[0][1]
                retry_after = (oldest_time + self._window) - now
            else:
                retry_after = self._window

            logger.warning(
                "Rate limit exceeded",
                extra={
                    "tenant_id": str(tenant_id),
                    "request_count": request_count,
                    "limit": self._rpm,
                    "window_seconds": self._window,
                    "retry_after": retry_after,
                },
            )
            raise RateLimitExceeded(tenant_id, max(0, retry_after))

        logger.debug(
            "Rate limit check passed",
            extra={
                "tenant_id": str(tenant_id),
                "request_count": request_count + 1,
                "limit": self._rpm,
            },
        )

    async def get_remaining(self, tenant_id: UUID) -> int:
        """Get remaining requests for tenant.

        Returns the number of requests the tenant can still make
        within the current sliding window.

        Args:
            tenant_id: Tenant to check

        Returns:
            Number of remaining requests in current window
        """
        r = await self._get_redis()
        key = self._get_key(tenant_id)
        now = datetime.now(UTC).timestamp()
        window_start = now - self._window

        # Clean old entries and count
        await r.zremrangebyscore(key, 0, window_start)
        count = await r.zcard(key)

        remaining = max(0, self._rpm - count)

        logger.debug(
            "Rate limit remaining check",
            extra={
                "tenant_id": str(tenant_id),
                "current_count": count,
                "remaining": remaining,
                "limit": self._rpm,
            },
        )

        return remaining

    async def close(self) -> None:
        """Close Redis connection.

        Should be called during application shutdown to cleanly
        close the Redis connection.
        """
        if self._redis:
            await self._redis.close()
            self._redis = None
            logger.debug("Rate limiter Redis connection closed")


# Global instance
_rate_limiter: OllamaRateLimiter | None = None


def get_rate_limiter() -> OllamaRateLimiter:
    """Get global rate limiter instance.

    Creates a new instance on first call, then returns the same
    instance on subsequent calls (singleton pattern).

    Returns:
        The global OllamaRateLimiter instance
    """
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = OllamaRateLimiter()
    return _rate_limiter


def reset_rate_limiter() -> None:
    """Reset the global rate limiter instance.

    Primarily useful for testing to ensure a fresh instance.
    This does NOT close the Redis connection - call close()
    first if needed.
    """
    global _rate_limiter
    _rate_limiter = None
