"""
Timeline Cache Service for Redis caching of timeline data.

This service provides caching for timeline-related data to improve
performance of frequently accessed timeline queries.

Features:
- Cache timeline summary responses (TTL: 5 minutes)
- Cache invalidation on entity updates
- Graceful degradation when Redis is unavailable

See ADR-025 for design decisions.
"""

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import redis.asyncio as redis

from kg_builder.cache import get_redis_client
from kg_builder.schemas.timeline import TimelineSummaryResponse, TimeRange

logger = logging.getLogger(__name__)

# Cache key prefixes
CACHE_PREFIX_SUMMARY = "timeline:summary"
CACHE_PREFIX_EXPORT_RATE = "timeline:export_rate"

# Default TTL values (in seconds)
DEFAULT_SUMMARY_TTL = 300  # 5 minutes
EXPORT_RATE_LIMIT_WINDOW = 60  # 1 minute


class TimelineCacheService:
    """
    Service for caching timeline data in Redis.

    Provides methods to cache and retrieve timeline summary responses,
    with automatic cache invalidation when timeline data changes.

    The service gracefully degrades when Redis is unavailable,
    returning None for cache misses instead of raising errors.
    """

    def __init__(
        self,
        redis_client: redis.Redis | None = None,
        summary_ttl: int = DEFAULT_SUMMARY_TTL,
    ):
        """
        Initialize the timeline cache service.

        Args:
            redis_client: Optional Redis client (will use global client if not provided)
            summary_ttl: TTL in seconds for summary cache entries (default: 5 minutes)
        """
        self._redis_client = redis_client
        self._summary_ttl = summary_ttl

    async def _get_client(self) -> redis.Redis | None:
        """Get Redis client, lazily initializing if needed."""
        if self._redis_client is not None:
            return self._redis_client
        return await get_redis_client()

    def _summary_cache_key(self, job_id: UUID, tenant_id: UUID) -> str:
        """
        Generate cache key for timeline summary.

        Args:
            job_id: UUID of the scraping job
            tenant_id: UUID of the tenant

        Returns:
            Cache key string
        """
        return f"{CACHE_PREFIX_SUMMARY}:{tenant_id}:{job_id}"

    def _export_rate_key(self, user_id: str) -> str:
        """
        Generate cache key for export rate limiting.

        Args:
            user_id: User identifier

        Returns:
            Cache key string
        """
        return f"{CACHE_PREFIX_EXPORT_RATE}:{user_id}"

    async def get_cached_summary(
        self,
        job_id: UUID,
        tenant_id: UUID,
    ) -> TimelineSummaryResponse | None:
        """
        Get cached timeline summary.

        Args:
            job_id: UUID of the scraping job
            tenant_id: UUID of the tenant

        Returns:
            Cached TimelineSummaryResponse or None if not cached/error
        """
        client = await self._get_client()
        if client is None:
            logger.debug("Redis not available, cache miss")
            return None

        cache_key = self._summary_cache_key(job_id, tenant_id)

        try:
            cached_data = await client.get(cache_key)
            if cached_data is None:
                logger.debug(
                    "Cache miss for timeline summary",
                    extra={
                        "job_id": str(job_id),
                        "tenant_id": str(tenant_id),
                        "cache_key": cache_key,
                    },
                )
                return None

            # Parse cached JSON
            data = json.loads(cached_data)

            # Reconstruct TimeRange if present
            time_range = None
            if data.get("time_range"):
                time_range = TimeRange(
                    start=datetime.fromisoformat(data["time_range"]["start"]),
                    end=datetime.fromisoformat(data["time_range"]["end"]),
                )

            summary = TimelineSummaryResponse(
                total_events=data["total_events"],
                dated_events=data["dated_events"],
                undated_events=data["undated_events"],
                sequence_only_events=data["sequence_only_events"],
                time_range=time_range,
                entity_type_counts=data["entity_type_counts"],
                precision_distribution=data["precision_distribution"],
                uncertainty_distribution=data["uncertainty_distribution"],
            )

            logger.debug(
                "Cache hit for timeline summary",
                extra={
                    "job_id": str(job_id),
                    "tenant_id": str(tenant_id),
                    "cache_key": cache_key,
                },
            )

            return summary

        except redis.RedisError as e:
            logger.warning(
                "Redis error getting cached summary",
                extra={
                    "job_id": str(job_id),
                    "tenant_id": str(tenant_id),
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
            )
            return None
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            logger.warning(
                "Error parsing cached summary",
                extra={
                    "job_id": str(job_id),
                    "tenant_id": str(tenant_id),
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
            )
            # Invalid cache entry, try to delete it
            try:
                await client.delete(cache_key)
            except redis.RedisError:
                pass
            return None

    async def set_cached_summary(
        self,
        job_id: UUID,
        tenant_id: UUID,
        summary: TimelineSummaryResponse,
    ) -> bool:
        """
        Cache timeline summary.

        Args:
            job_id: UUID of the scraping job
            tenant_id: UUID of the tenant
            summary: TimelineSummaryResponse to cache

        Returns:
            True if cached successfully, False otherwise
        """
        client = await self._get_client()
        if client is None:
            logger.debug("Redis not available, skipping cache")
            return False

        cache_key = self._summary_cache_key(job_id, tenant_id)

        try:
            # Serialize summary to JSON
            data = self._serialize_summary(summary)

            await client.setex(
                cache_key,
                self._summary_ttl,
                json.dumps(data),
            )

            logger.debug(
                "Cached timeline summary",
                extra={
                    "job_id": str(job_id),
                    "tenant_id": str(tenant_id),
                    "cache_key": cache_key,
                    "ttl": self._summary_ttl,
                },
            )

            return True

        except redis.RedisError as e:
            logger.warning(
                "Redis error caching summary",
                extra={
                    "job_id": str(job_id),
                    "tenant_id": str(tenant_id),
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
            )
            return False

    def _serialize_summary(self, summary: TimelineSummaryResponse) -> dict[str, Any]:
        """
        Serialize TimelineSummaryResponse to JSON-compatible dict.

        Args:
            summary: TimelineSummaryResponse to serialize

        Returns:
            Dictionary suitable for JSON serialization
        """
        data: dict[str, Any] = {
            "total_events": summary.total_events,
            "dated_events": summary.dated_events,
            "undated_events": summary.undated_events,
            "sequence_only_events": summary.sequence_only_events,
            "time_range": None,
            "entity_type_counts": summary.entity_type_counts,
            "precision_distribution": summary.precision_distribution,
            "uncertainty_distribution": summary.uncertainty_distribution,
        }

        if summary.time_range:
            data["time_range"] = {
                "start": summary.time_range.start.isoformat(),
                "end": summary.time_range.end.isoformat(),
            }

        return data

    async def invalidate_job_cache(self, job_id: UUID) -> int:
        """
        Invalidate all cache entries for a job.

        This should be called when entities are added, updated, or deleted
        from a job to ensure the cache reflects the current state.

        Args:
            job_id: UUID of the scraping job

        Returns:
            Number of keys deleted (0 if Redis unavailable or error)
        """
        client = await self._get_client()
        if client is None:
            logger.debug("Redis not available, skipping cache invalidation")
            return 0

        # Pattern to match all cache keys for this job
        # Note: This will match keys across all tenants for this job,
        # which is intentional for thorough invalidation
        pattern = f"{CACHE_PREFIX_SUMMARY}:*:{job_id}"

        try:
            # Use SCAN to find matching keys (safer than KEYS for large datasets)
            deleted_count = 0
            cursor = 0

            while True:
                cursor, keys = await client.scan(cursor=cursor, match=pattern, count=100)
                if keys:
                    deleted_count += await client.delete(*keys)
                if cursor == 0:
                    break

            if deleted_count > 0:
                logger.info(
                    "Invalidated timeline cache",
                    extra={
                        "job_id": str(job_id),
                        "deleted_count": deleted_count,
                    },
                )

            return deleted_count

        except redis.RedisError as e:
            logger.warning(
                "Redis error invalidating cache",
                extra={
                    "job_id": str(job_id),
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
            )
            return 0

    async def check_export_rate_limit(
        self,
        user_id: str,
        limit: int = 10,
        window_seconds: int = EXPORT_RATE_LIMIT_WINDOW,
    ) -> tuple[bool, int]:
        """
        Check if user is within export rate limit.

        Implements a sliding window rate limiter for export requests.

        Args:
            user_id: User identifier
            limit: Maximum exports per window (default: 10)
            window_seconds: Window size in seconds (default: 60)

        Returns:
            Tuple of (is_allowed, retry_after_seconds)
            - is_allowed: True if request is allowed
            - retry_after_seconds: Seconds until next request allowed (0 if allowed)
        """
        client = await self._get_client()
        if client is None:
            # Graceful degradation: allow request if Redis unavailable
            logger.debug("Redis not available, allowing export request")
            return (True, 0)

        rate_key = self._export_rate_key(user_id)

        try:
            # Increment counter and get current value
            count = await client.incr(rate_key)

            # Set TTL on first request
            if count == 1:
                await client.expire(rate_key, window_seconds)

            if count > limit:
                # Get remaining TTL
                ttl = await client.ttl(rate_key)
                retry_after = max(ttl, 1)  # At least 1 second

                logger.warning(
                    "Export rate limit exceeded",
                    extra={
                        "user_id": user_id,
                        "count": count,
                        "limit": limit,
                        "retry_after": retry_after,
                    },
                )

                return (False, retry_after)

            logger.debug(
                "Export rate limit check passed",
                extra={
                    "user_id": user_id,
                    "count": count,
                    "limit": limit,
                },
            )

            return (True, 0)

        except redis.RedisError as e:
            logger.warning(
                "Redis error checking export rate limit",
                extra={
                    "user_id": user_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
            )
            # Graceful degradation: allow request
            return (True, 0)


# Factory function for creating cache service
_cache_service: TimelineCacheService | None = None


async def get_timeline_cache_service() -> TimelineCacheService:
    """
    Get singleton TimelineCacheService instance.

    Returns:
        TimelineCacheService instance
    """
    global _cache_service

    if _cache_service is None:
        _cache_service = TimelineCacheService()

    return _cache_service
