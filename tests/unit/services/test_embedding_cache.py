"""
Unit tests for EmbeddingCache service.

Tests the Redis-based embedding cache with mocked Redis client.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import numpy as np
import pytest

from kg_builder.services.embedding_cache import (
    CACHE_KEY_PREFIX,
    DEFAULT_TTL_SECONDS,
    EmbeddingCache,
    get_embedding_cache,
)


class TestEmbeddingCacheInit:
    """Tests for EmbeddingCache initialization."""

    def test_default_configuration(self):
        """Test cache initializes with correct defaults."""
        mock_redis = AsyncMock()
        cache = EmbeddingCache(mock_redis)

        assert cache._ttl == DEFAULT_TTL_SECONDS
        assert cache._key_prefix == CACHE_KEY_PREFIX
        assert cache._hits == 0
        assert cache._misses == 0

    def test_custom_configuration(self):
        """Test cache initializes with custom configuration."""
        mock_redis = AsyncMock()
        cache = EmbeddingCache(
            mock_redis,
            ttl_seconds=3600,
            key_prefix="custom",
        )

        assert cache._ttl == 3600
        assert cache._key_prefix == "custom"


class TestEmbeddingCacheKeyGeneration:
    """Tests for cache key generation."""

    def test_cache_key_format(self):
        """Test cache key follows expected format."""
        mock_redis = AsyncMock()
        cache = EmbeddingCache(mock_redis)

        tenant_id = uuid4()
        entity_id = uuid4()

        key = cache._cache_key(tenant_id, entity_id)

        assert key == f"{CACHE_KEY_PREFIX}:{tenant_id}:{entity_id}"

    def test_cache_key_with_custom_prefix(self):
        """Test cache key with custom prefix."""
        mock_redis = AsyncMock()
        cache = EmbeddingCache(mock_redis, key_prefix="test")

        tenant_id = uuid4()
        entity_id = uuid4()

        key = cache._cache_key(tenant_id, entity_id)

        assert key.startswith("test:")


class TestEmbeddingEncoding:
    """Tests for embedding encoding/decoding."""

    def test_encode_decode_roundtrip(self):
        """Test embedding survives encode/decode roundtrip."""
        mock_redis = AsyncMock()
        cache = EmbeddingCache(mock_redis)

        original = np.random.randn(1024).astype(np.float32)
        encoded = cache._encode_embedding(original)
        decoded = cache._decode_embedding(encoded)

        assert np.allclose(original, decoded)

    def test_encode_produces_string(self):
        """Test encoding produces base64 string."""
        mock_redis = AsyncMock()
        cache = EmbeddingCache(mock_redis)

        embedding = np.zeros(1024, dtype=np.float32)
        encoded = cache._encode_embedding(embedding)

        assert isinstance(encoded, str)
        # Base64 only contains these characters
        assert all(c.isalnum() or c in "+/=" for c in encoded)

    def test_encode_normalizes_dtype(self):
        """Test encoding normalizes to float32."""
        mock_redis = AsyncMock()
        cache = EmbeddingCache(mock_redis)

        # Create float64 embedding
        embedding = np.random.randn(1024).astype(np.float64)
        encoded = cache._encode_embedding(embedding)
        decoded = cache._decode_embedding(encoded)

        # Should be float32 after roundtrip
        assert decoded.dtype == np.float32


class TestEmbeddingCacheGet:
    """Tests for single get operation."""

    @pytest.fixture
    def cache_with_mock(self):
        """Create cache with mock Redis."""
        mock_redis = AsyncMock()
        cache = EmbeddingCache(mock_redis)
        return cache, mock_redis

    @pytest.mark.asyncio
    async def test_get_cache_miss_returns_none(self, cache_with_mock):
        """Test cache miss returns None."""
        cache, mock_redis = cache_with_mock
        mock_redis.get.return_value = None

        tenant_id = uuid4()
        entity_id = uuid4()

        result = await cache.get(tenant_id, entity_id)

        assert result is None
        assert cache._misses == 1
        assert cache._hits == 0

    @pytest.mark.asyncio
    async def test_get_cache_hit_returns_embedding(self, cache_with_mock):
        """Test cache hit returns decoded embedding."""
        cache, mock_redis = cache_with_mock

        # Create and encode an embedding
        original = np.random.randn(1024).astype(np.float32)
        encoded = cache._encode_embedding(original)
        mock_redis.get.return_value = encoded

        tenant_id = uuid4()
        entity_id = uuid4()

        result = await cache.get(tenant_id, entity_id)

        assert result is not None
        assert np.allclose(result, original)
        assert cache._hits == 1
        assert cache._misses == 0

    @pytest.mark.asyncio
    async def test_get_redis_error_returns_none(self, cache_with_mock):
        """Test Redis error returns None gracefully."""
        cache, mock_redis = cache_with_mock
        mock_redis.get.side_effect = Exception("Redis error")

        result = await cache.get(uuid4(), uuid4())

        assert result is None
        assert cache._misses == 1


class TestEmbeddingCacheSet:
    """Tests for single set operation."""

    @pytest.fixture
    def cache_with_mock(self):
        """Create cache with mock Redis."""
        mock_redis = AsyncMock()
        cache = EmbeddingCache(mock_redis)
        return cache, mock_redis

    @pytest.mark.asyncio
    async def test_set_stores_embedding(self, cache_with_mock):
        """Test set stores encoded embedding with TTL."""
        cache, mock_redis = cache_with_mock

        tenant_id = uuid4()
        entity_id = uuid4()
        embedding = np.random.randn(1024).astype(np.float32)

        result = await cache.set(tenant_id, entity_id, embedding)

        assert result is True
        mock_redis.setex.assert_called_once()

        # Verify the key and TTL
        call_args = mock_redis.setex.call_args
        assert str(tenant_id) in call_args[0][0]
        assert str(entity_id) in call_args[0][0]
        assert call_args[0][1] == DEFAULT_TTL_SECONDS

    @pytest.mark.asyncio
    async def test_set_with_custom_ttl(self, cache_with_mock):
        """Test set uses custom TTL when provided."""
        cache, mock_redis = cache_with_mock

        embedding = np.zeros(1024, dtype=np.float32)
        custom_ttl = 3600

        await cache.set(uuid4(), uuid4(), embedding, ttl=custom_ttl)

        call_args = mock_redis.setex.call_args
        assert call_args[0][1] == custom_ttl

    @pytest.mark.asyncio
    async def test_set_redis_error_returns_false(self, cache_with_mock):
        """Test Redis error returns False."""
        cache, mock_redis = cache_with_mock
        mock_redis.setex.side_effect = Exception("Redis error")

        result = await cache.set(uuid4(), uuid4(), np.zeros(1024))

        assert result is False


class TestEmbeddingCacheBatchGet:
    """Tests for batch get operation."""

    @pytest.fixture
    def cache_with_mock(self):
        """Create cache with mock Redis."""
        mock_redis = AsyncMock()
        cache = EmbeddingCache(mock_redis)
        return cache, mock_redis

    @pytest.mark.asyncio
    async def test_batch_get_empty_list(self, cache_with_mock):
        """Test batch get with empty list returns empty dict."""
        cache, mock_redis = cache_with_mock

        result = await cache.get_batch(uuid4(), [])

        assert result == {}
        mock_redis.mget.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_get_all_hits(self, cache_with_mock):
        """Test batch get with all cache hits."""
        cache, mock_redis = cache_with_mock

        entity_ids = [uuid4(), uuid4(), uuid4()]
        embeddings = [np.random.randn(1024).astype(np.float32) for _ in range(3)]
        encoded = [cache._encode_embedding(e) for e in embeddings]

        mock_redis.mget.return_value = encoded

        tenant_id = uuid4()
        result = await cache.get_batch(tenant_id, entity_ids)

        assert len(result) == 3
        assert all(v is not None for v in result.values())
        assert cache._hits == 3
        assert cache._misses == 0

    @pytest.mark.asyncio
    async def test_batch_get_partial_hits(self, cache_with_mock):
        """Test batch get with some cache misses."""
        cache, mock_redis = cache_with_mock

        entity_ids = [uuid4(), uuid4(), uuid4()]
        embedding = np.random.randn(1024).astype(np.float32)

        # First hit, second miss, third hit
        mock_redis.mget.return_value = [
            cache._encode_embedding(embedding),
            None,
            cache._encode_embedding(embedding),
        ]

        result = await cache.get_batch(uuid4(), entity_ids)

        assert result[entity_ids[0]] is not None
        assert result[entity_ids[1]] is None
        assert result[entity_ids[2]] is not None
        assert cache._hits == 2
        assert cache._misses == 1

    @pytest.mark.asyncio
    async def test_batch_get_redis_error(self, cache_with_mock):
        """Test batch get handles Redis error."""
        cache, mock_redis = cache_with_mock
        mock_redis.mget.side_effect = Exception("Redis error")

        entity_ids = [uuid4(), uuid4()]
        result = await cache.get_batch(uuid4(), entity_ids)

        assert all(v is None for v in result.values())
        assert cache._misses == 2


class TestEmbeddingCacheBatchSet:
    """Tests for batch set operation."""

    @pytest.fixture
    def cache_with_mock(self):
        """Create cache with mock Redis."""
        mock_redis = AsyncMock()
        # `Redis.pipeline()` is a synchronous factory returning an awaitable
        # pipeline, so it must be a MagicMock, not an AsyncMock coroutine.
        mock_pipeline = AsyncMock()
        mock_redis.pipeline = MagicMock(return_value=mock_pipeline)
        cache = EmbeddingCache(mock_redis)
        return cache, mock_redis, mock_pipeline

    @pytest.mark.asyncio
    async def test_batch_set_empty_dict(self, cache_with_mock):
        """Test batch set with empty dict returns 0."""
        cache, _mock_redis, _ = cache_with_mock

        result = await cache.set_batch(uuid4(), {})

        assert result == 0

    @pytest.mark.asyncio
    async def test_batch_set_uses_pipeline(self, cache_with_mock):
        """Test batch set uses Redis pipeline."""
        cache, _mock_redis, mock_pipeline = cache_with_mock

        entity_ids = [uuid4(), uuid4()]
        embeddings = {eid: np.random.randn(1024).astype(np.float32) for eid in entity_ids}

        result = await cache.set_batch(uuid4(), embeddings)

        assert result == 2
        assert mock_pipeline.setex.call_count == 2
        mock_pipeline.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_batch_set_redis_error(self, cache_with_mock):
        """Test batch set handles Redis error."""
        cache, _mock_redis, mock_pipeline = cache_with_mock
        mock_pipeline.execute.side_effect = Exception("Redis error")

        embeddings = {uuid4(): np.zeros(1024)}
        result = await cache.set_batch(uuid4(), embeddings)

        assert result == 0


class TestEmbeddingCacheInvalidate:
    """Tests for cache invalidation."""

    @pytest.fixture
    def cache_with_mock(self):
        """Create cache with mock Redis."""
        mock_redis = AsyncMock()
        cache = EmbeddingCache(mock_redis)
        return cache, mock_redis

    @pytest.mark.asyncio
    async def test_invalidate_single(self, cache_with_mock):
        """Test single invalidation."""
        cache, mock_redis = cache_with_mock

        result = await cache.invalidate(uuid4(), uuid4())

        assert result is True
        mock_redis.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalidate_redis_error(self, cache_with_mock):
        """Test invalidation handles Redis error."""
        cache, mock_redis = cache_with_mock
        mock_redis.delete.side_effect = Exception("Redis error")

        result = await cache.invalidate(uuid4(), uuid4())

        assert result is False

    @pytest.mark.asyncio
    async def test_invalidate_batch_empty(self, cache_with_mock):
        """Test batch invalidation with empty list."""
        cache, mock_redis = cache_with_mock

        result = await cache.invalidate_batch(uuid4(), [])

        assert result == 0
        mock_redis.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalidate_batch(self, cache_with_mock):
        """Test batch invalidation."""
        cache, mock_redis = cache_with_mock
        mock_redis.delete.return_value = 3

        entity_ids = [uuid4(), uuid4(), uuid4()]
        result = await cache.invalidate_batch(uuid4(), entity_ids)

        assert result == 3


class TestEmbeddingCacheExists:
    """Tests for existence check."""

    @pytest.mark.asyncio
    async def test_exists_returns_true(self):
        """Test exists returns True when key exists."""
        mock_redis = AsyncMock()
        mock_redis.exists.return_value = 1
        cache = EmbeddingCache(mock_redis)

        result = await cache.exists(uuid4(), uuid4())

        assert result is True

    @pytest.mark.asyncio
    async def test_exists_returns_false(self):
        """Test exists returns False when key missing."""
        mock_redis = AsyncMock()
        mock_redis.exists.return_value = 0
        cache = EmbeddingCache(mock_redis)

        result = await cache.exists(uuid4(), uuid4())

        assert result is False

    @pytest.mark.asyncio
    async def test_exists_handles_error(self):
        """Test exists handles Redis error."""
        mock_redis = AsyncMock()
        mock_redis.exists.side_effect = Exception("Redis error")
        cache = EmbeddingCache(mock_redis)

        result = await cache.exists(uuid4(), uuid4())

        assert result is False


class TestEmbeddingCacheStats:
    """Tests for cache statistics."""

    @pytest.mark.asyncio
    async def test_get_stats_basic(self):
        """Test basic stats retrieval."""
        mock_redis = AsyncMock()
        cache = EmbeddingCache(mock_redis)

        # Simulate some hits and misses
        cache._hits = 10
        cache._misses = 5

        stats = await cache.get_cache_stats()

        assert stats["hits"] == 10
        assert stats["misses"] == 5
        assert stats["hit_rate"] == pytest.approx(10 / 15)

    @pytest.mark.asyncio
    async def test_get_stats_with_tenant(self):
        """Test stats with tenant-specific count."""
        mock_redis = AsyncMock()

        # Mock scan_iter to return some keys
        async def mock_scan():
            for key in ["key1", "key2", "key3"]:
                yield key

        # `Redis.scan_iter()` is a synchronous factory returning an async
        # iterator, so it must be a MagicMock, not an AsyncMock coroutine.
        mock_redis.scan_iter = MagicMock(return_value=mock_scan())
        cache = EmbeddingCache(mock_redis)

        tenant_id = uuid4()
        stats = await cache.get_cache_stats(tenant_id)

        assert "cached_embeddings" in stats
        assert stats["tenant_id"] == str(tenant_id)

    def test_reset_stats(self):
        """Test stats reset."""
        mock_redis = AsyncMock()
        cache = EmbeddingCache(mock_redis)

        cache._hits = 100
        cache._misses = 50
        cache.reset_stats()

        assert cache._hits == 0
        assert cache._misses == 0


class TestGetEmbeddingCache:
    """Tests for get_embedding_cache factory function."""

    @pytest.mark.asyncio
    async def test_returns_none_when_redis_unavailable(self):
        """Test returns None when Redis is unavailable."""
        with patch("kg_builder.services.embedding_cache.get_redis_client") as mock_get_redis:
            mock_get_redis.return_value = None

            result = await get_embedding_cache()

            assert result is None

    @pytest.mark.asyncio
    async def test_returns_cache_when_redis_available(self):
        """Test returns cache instance when Redis is available."""
        mock_redis = AsyncMock()

        with patch("kg_builder.services.embedding_cache.get_redis_client") as mock_get_redis:
            mock_get_redis.return_value = mock_redis

            with patch("kg_builder.services.embedding_cache.settings") as mock_settings:
                mock_settings.EMBEDDING_CACHE_TTL = 3600

                result = await get_embedding_cache()

                assert result is not None
                assert isinstance(result, EmbeddingCache)
                assert result._ttl == 3600
