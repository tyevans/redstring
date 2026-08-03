"""
Embedding cache service using Redis.

This module provides caching for entity embeddings to avoid
repeated computation during similarity matching. Embeddings are
expensive to compute (require Ollama API calls), so caching
significantly improves performance.

Architecture:
    - Primary storage: PostgreSQL pgvector column on ExtractedEntity
    - Hot cache: Redis for frequently accessed embeddings during batch operations
    - Cache key pattern: embedding:{tenant_id}:{entity_id}

Example usage:
    >>> cache = EmbeddingCache(redis_client)
    >>>
    >>> # Single operations
    >>> await cache.set(tenant_id, entity_id, embedding)
    >>> embedding = await cache.get(tenant_id, entity_id)
    >>>
    >>> # Batch operations
    >>> await cache.set_batch(tenant_id, {id1: emb1, id2: emb2})
    >>> results = await cache.get_batch(tenant_id, [id1, id2, id3])
"""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING
from uuid import UUID

import numpy as np

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

# Cache key pattern: embedding:{tenant_id}:{entity_id}
CACHE_KEY_PREFIX = "embedding"

# Default TTL: 7 days (embeddings don't change often)
DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60


class EmbeddingCache:
    """
    Redis-based cache for entity embeddings.

    Stores embedding vectors as base64-encoded binary data with
    configurable TTL. Supports batch operations for efficient
    bulk processing during consolidation.

    The cache uses base64 encoding because the main Redis client
    is configured with decode_responses=True for string operations.

    Attributes:
        redis_client: Async Redis client
        ttl_seconds: Time-to-live for cached embeddings
        key_prefix: Prefix for cache keys
    """

    def __init__(
        self,
        redis_client: Redis,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        key_prefix: str = CACHE_KEY_PREFIX,
    ):
        """
        Initialize embedding cache.

        Args:
            redis_client: Async Redis client
            ttl_seconds: Time-to-live for cached embeddings (default: 7 days)
            key_prefix: Prefix for cache keys (default: "embedding")
        """
        self._redis = redis_client
        self._ttl = ttl_seconds
        self._key_prefix = key_prefix

        # Metrics
        self._hits = 0
        self._misses = 0

    def _cache_key(self, tenant_id: UUID, entity_id: UUID) -> str:
        """
        Generate cache key for entity embedding.

        Args:
            tenant_id: Tenant ID
            entity_id: Entity ID

        Returns:
            Cache key string
        """
        return f"{self._key_prefix}:{tenant_id}:{entity_id}"

    def _encode_embedding(self, embedding: np.ndarray) -> str:
        """
        Encode numpy embedding to base64 string for Redis storage.

        Args:
            embedding: Numpy array embedding

        Returns:
            Base64-encoded string
        """
        # Ensure float32 for consistent storage
        embedding_bytes = embedding.astype(np.float32).tobytes()
        return base64.b64encode(embedding_bytes).decode("ascii")

    def _decode_embedding(self, data: str) -> np.ndarray:
        """
        Decode base64 string back to numpy embedding.

        Args:
            data: Base64-encoded string

        Returns:
            Numpy array embedding
        """
        embedding_bytes = base64.b64decode(data)
        return np.frombuffer(embedding_bytes, dtype=np.float32)

    async def get(
        self,
        tenant_id: UUID,
        entity_id: UUID,
    ) -> np.ndarray | None:
        """
        Get cached embedding for entity.

        Args:
            tenant_id: Tenant ID
            entity_id: Entity ID

        Returns:
            Numpy array if cached, None otherwise
        """
        key = self._cache_key(tenant_id, entity_id)

        try:
            data = await self._redis.get(key)

            if data is None:
                self._misses += 1
                logger.debug(f"Cache miss for {key}")
                return None

            self._hits += 1
            embedding = self._decode_embedding(data)
            logger.debug(f"Cache hit for {key}, dimension={len(embedding)}")
            return embedding

        except Exception as e:
            logger.error(f"Error getting embedding from cache: {e}")
            self._misses += 1
            return None

    async def set(
        self,
        tenant_id: UUID,
        entity_id: UUID,
        embedding: np.ndarray,
        ttl: int | None = None,
    ) -> bool:
        """
        Cache embedding for entity.

        Args:
            tenant_id: Tenant ID
            entity_id: Entity ID
            embedding: Numpy array to cache
            ttl: Optional custom TTL in seconds (uses default if not provided)

        Returns:
            True if cached successfully, False otherwise
        """
        key = self._cache_key(tenant_id, entity_id)
        ttl = ttl or self._ttl

        try:
            # Encode embedding to base64 string
            data = self._encode_embedding(embedding)
            await self._redis.setex(key, ttl, data)
            logger.debug(f"Cached embedding for {key} (dim={len(embedding)}, ttl={ttl}s)")
            return True

        except Exception as e:
            logger.error(f"Error caching embedding: {e}")
            return False

    async def get_batch(
        self,
        tenant_id: UUID,
        entity_ids: list[UUID],
    ) -> dict[UUID, np.ndarray | None]:
        """
        Get cached embeddings for multiple entities.

        Args:
            tenant_id: Tenant ID
            entity_ids: List of entity IDs

        Returns:
            Dict mapping entity_id to embedding (or None if not cached)
        """
        if not entity_ids:
            return {}

        keys = [self._cache_key(tenant_id, eid) for eid in entity_ids]

        try:
            # MGET for batch retrieval
            values = await self._redis.mget(keys)

            result = {}
            hits = 0
            for entity_id, data in zip(entity_ids, values):
                if data is not None:
                    try:
                        result[entity_id] = self._decode_embedding(data)
                        hits += 1
                    except Exception as e:
                        logger.warning(f"Failed to decode embedding for {entity_id}: {e}")
                        result[entity_id] = None
                else:
                    result[entity_id] = None

            self._hits += hits
            self._misses += len(entity_ids) - hits
            logger.debug(f"Batch get: {hits}/{len(entity_ids)} cache hits")
            return result

        except Exception as e:
            logger.error(f"Error in batch get: {e}")
            self._misses += len(entity_ids)
            return dict.fromkeys(entity_ids)

    async def set_batch(
        self,
        tenant_id: UUID,
        embeddings: dict[UUID, np.ndarray],
        ttl: int | None = None,
    ) -> int:
        """
        Cache embeddings for multiple entities.

        Uses Redis pipeline for efficient batch write.

        Args:
            tenant_id: Tenant ID
            embeddings: Dict mapping entity_id to embedding
            ttl: Optional custom TTL in seconds

        Returns:
            Number of embeddings cached successfully
        """
        if not embeddings:
            return 0

        ttl = ttl or self._ttl

        try:
            # Use pipeline for batch set
            pipe = self._redis.pipeline()

            for entity_id, embedding in embeddings.items():
                key = self._cache_key(tenant_id, entity_id)
                data = self._encode_embedding(embedding)
                pipe.setex(key, ttl, data)

            await pipe.execute()

            logger.debug(f"Batch cached {len(embeddings)} embeddings (ttl={ttl}s)")
            return len(embeddings)

        except Exception as e:
            logger.error(f"Error in batch set: {e}")
            return 0

    async def invalidate(
        self,
        tenant_id: UUID,
        entity_id: UUID,
    ) -> bool:
        """
        Invalidate cached embedding for entity.

        Should be called when entity is updated or merged.

        Args:
            tenant_id: Tenant ID
            entity_id: Entity ID

        Returns:
            True if invalidated (or didn't exist), False on error
        """
        key = self._cache_key(tenant_id, entity_id)

        try:
            await self._redis.delete(key)
            logger.debug(f"Invalidated cache for {key}")
            return True

        except Exception as e:
            logger.error(f"Error invalidating cache: {e}")
            return False

    async def invalidate_batch(
        self,
        tenant_id: UUID,
        entity_ids: list[UUID],
    ) -> int:
        """
        Invalidate cached embeddings for multiple entities.

        Args:
            tenant_id: Tenant ID
            entity_ids: List of entity IDs

        Returns:
            Number of keys deleted
        """
        if not entity_ids:
            return 0

        keys = [self._cache_key(tenant_id, eid) for eid in entity_ids]

        try:
            deleted = await self._redis.delete(*keys)
            logger.debug(f"Invalidated {deleted} cache entries")
            return deleted

        except Exception as e:
            logger.error(f"Error in batch invalidate: {e}")
            return 0

    async def exists(
        self,
        tenant_id: UUID,
        entity_id: UUID,
    ) -> bool:
        """
        Check if embedding is cached.

        Args:
            tenant_id: Tenant ID
            entity_id: Entity ID

        Returns:
            True if cached, False otherwise
        """
        key = self._cache_key(tenant_id, entity_id)
        try:
            return await self._redis.exists(key) > 0
        except Exception:
            return False

    async def get_cache_stats(self, tenant_id: UUID | None = None) -> dict:
        """
        Get cache statistics.

        Args:
            tenant_id: Optional tenant ID to filter by

        Returns:
            Dict with cache stats
        """
        stats = {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / (self._hits + self._misses) if (self._hits + self._misses) > 0 else 0.0,
        }

        if tenant_id:
            pattern = f"{self._key_prefix}:{tenant_id}:*"
            try:
                keys = []
                async for key in self._redis.scan_iter(pattern):
                    keys.append(key)
                stats["tenant_id"] = str(tenant_id)
                stats["cached_embeddings"] = len(keys)
            except Exception as e:
                logger.error(f"Error getting cache stats: {e}")
                stats["error"] = str(e)

        return stats

    def reset_stats(self) -> None:
        """Reset cache hit/miss statistics."""
        self._hits = 0
        self._misses = 0


async def get_embedding_cache() -> EmbeddingCache | None:
    """
    Get embedding cache instance.

    Uses the shared Redis client from kg_builder.cache.

    Returns:
        EmbeddingCache instance or None if Redis unavailable
    """
    from kg_builder.cache import get_redis_client
    from kg_builder.config import settings

    redis_client = await get_redis_client()
    if redis_client is None:
        logger.warning("Redis unavailable, embedding cache disabled")
        return None

    return EmbeddingCache(
        redis_client=redis_client,
        ttl_seconds=settings.EMBEDDING_CACHE_TTL,
    )
