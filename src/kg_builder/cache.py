"""
Redis cache client initialization.

Provides shared Redis client for caching across services. Uses lazy initialization
to avoid connecting to Redis until first use. Implements graceful degradation if
Redis is unavailable - services will fall back to database queries.
"""

import logging

import redis.asyncio as redis

from kg_builder.config import settings

logger = logging.getLogger(__name__)

# Global Redis client instance (singleton)
_redis_client: redis.Redis | None = None


async def get_redis_client() -> redis.Redis | None:
    """
    Get Redis client with lazy initialization.

    Creates a singleton Redis client on first call and reuses it for subsequent
    calls. This ensures we have a single Redis connection pool across the
    application.

    The client uses UTF-8 encoding and automatically decodes responses to strings.
    On initialization, it performs a ping test to verify connectivity.

    Returns:
        Redis client or None if Redis not configured or connection fails

    Example:
        >>> redis = await get_redis_client()
        >>> if redis:
        ...     await redis.set("key", "value", ex=60)
        ...     value = await redis.get("key")

    Note:
        Redis connection failures are logged as warnings, not errors. Services
        should gracefully degrade to database queries if Redis is unavailable.
    """
    global _redis_client

    # Return existing client if already initialized
    if _redis_client is not None:
        return _redis_client

    # Check if Redis is configured
    if not settings.REDIS_URL:
        logger.warning("Redis URL not set, caching disabled")
        return None

    # Initialize Redis client
    try:
        _redis_client = redis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

        # Test connection with ping
        await _redis_client.ping()

        logger.info(
            f"Redis connected: redis_url={settings.REDIS_URL.split('@')[-1]}"  # Hide credentials
        )

        return _redis_client

    except Exception as e:
        logger.error(
            f"Redis connection failed: redis_url={settings.REDIS_URL.split('@')[-1]}, "  # Hide credentials
            f"error={e!s}, error_type={type(e).__name__}"
        )
        # Don't raise - allow graceful degradation
        return None


async def close_redis_client():
    """
    Close Redis client connection.

    Should be called when the application shuts down to properly close the
    Redis connection pool. After calling this, get_redis_client() will
    re-initialize the client on next call.

    Example:
        >>> # In application shutdown handler
        >>> await close_redis_client()
    """
    global _redis_client

    if _redis_client:
        await _redis_client.close()
        _redis_client = None
        logger.info("redis_connection_closed")
