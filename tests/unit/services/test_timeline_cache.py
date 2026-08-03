"""Unit tests for TimelineCacheService."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import redis.asyncio as redis

from kg_builder.schemas.timeline import TimelineSummaryResponse, TimeRange
from kg_builder.services.timeline_cache import (
    CACHE_PREFIX_EXPORT_RATE,
    CACHE_PREFIX_SUMMARY,
    DEFAULT_SUMMARY_TTL,
    TimelineCacheService,
    get_timeline_cache_service,
)


@pytest.fixture
def mock_redis_client():
    """Create a mock Redis client with async methods."""
    client = AsyncMock(spec=redis.Redis)
    # Ensure all methods return AsyncMock for proper await behavior
    client.get = AsyncMock()
    client.setex = AsyncMock()
    client.delete = AsyncMock()
    client.scan = AsyncMock()
    client.incr = AsyncMock()
    client.expire = AsyncMock()
    client.ttl = AsyncMock()
    return client


@pytest.fixture
def sample_tenant_id():
    """Generate a sample tenant ID."""
    return uuid4()


@pytest.fixture
def sample_job_id():
    """Generate a sample job ID."""
    return uuid4()


@pytest.fixture
def sample_summary():
    """Create a sample TimelineSummaryResponse."""
    return TimelineSummaryResponse(
        total_events=100,
        dated_events=75,
        undated_events=25,
        sequence_only_events=10,
        time_range=TimeRange(
            start=datetime(2023, 1, 1, tzinfo=UTC),
            end=datetime(2023, 12, 31, tzinfo=UTC),
        ),
        entity_type_counts={"event": 50, "person": 30, "organization": 20},
        precision_distribution={"day": 40, "month": 25, "year": 10},
        uncertainty_distribution={"exact": 60, "approximate": 15},
    )


@pytest.fixture
def sample_summary_no_time_range():
    """Create a sample TimelineSummaryResponse without time range."""
    return TimelineSummaryResponse(
        total_events=10,
        dated_events=0,
        undated_events=10,
        sequence_only_events=10,
        time_range=None,
        entity_type_counts={"event": 10},
        precision_distribution={},
        uncertainty_distribution={},
    )


class TestTimelineCacheServiceInit:
    """Tests for TimelineCacheService initialization."""

    def test_init_default_ttl(self):
        """Test initialization with default TTL."""
        service = TimelineCacheService()
        assert service._summary_ttl == DEFAULT_SUMMARY_TTL

    def test_init_custom_ttl(self):
        """Test initialization with custom TTL."""
        service = TimelineCacheService(summary_ttl=600)
        assert service._summary_ttl == 600

    def test_init_with_redis_client(self, mock_redis_client):
        """Test initialization with provided Redis client."""
        service = TimelineCacheService(redis_client=mock_redis_client)
        assert service._redis_client is mock_redis_client


class TestTimelineCacheServiceCacheKey:
    """Tests for cache key generation."""

    def test_summary_cache_key(self, sample_job_id, sample_tenant_id):
        """Test summary cache key generation."""
        service = TimelineCacheService()
        key = service._summary_cache_key(sample_job_id, sample_tenant_id)

        assert key == f"{CACHE_PREFIX_SUMMARY}:{sample_tenant_id}:{sample_job_id}"

    def test_export_rate_key(self):
        """Test export rate limit key generation."""
        service = TimelineCacheService()
        key = service._export_rate_key("user-123")

        assert key == f"{CACHE_PREFIX_EXPORT_RATE}:user-123"


class TestTimelineCacheServiceGetCachedSummary:
    """Tests for getting cached summaries."""

    @pytest.mark.asyncio
    async def test_get_cached_summary_cache_hit(
        self,
        mock_redis_client,
        sample_job_id,
        sample_tenant_id,
        sample_summary,
    ):
        """Test getting cached summary when data exists."""
        service = TimelineCacheService(redis_client=mock_redis_client)

        # Mock cached data
        cached_data = {
            "total_events": sample_summary.total_events,
            "dated_events": sample_summary.dated_events,
            "undated_events": sample_summary.undated_events,
            "sequence_only_events": sample_summary.sequence_only_events,
            "time_range": {
                "start": sample_summary.time_range.start.isoformat(),
                "end": sample_summary.time_range.end.isoformat(),
            },
            "entity_type_counts": sample_summary.entity_type_counts,
            "precision_distribution": sample_summary.precision_distribution,
            "uncertainty_distribution": sample_summary.uncertainty_distribution,
        }
        mock_redis_client.get.return_value = json.dumps(cached_data)

        result = await service.get_cached_summary(sample_job_id, sample_tenant_id)

        assert result is not None
        assert result.total_events == sample_summary.total_events
        assert result.dated_events == sample_summary.dated_events
        assert result.time_range is not None
        assert result.time_range.start == sample_summary.time_range.start

    @pytest.mark.asyncio
    async def test_get_cached_summary_cache_miss(
        self,
        mock_redis_client,
        sample_job_id,
        sample_tenant_id,
    ):
        """Test getting cached summary when no data exists."""
        service = TimelineCacheService(redis_client=mock_redis_client)
        mock_redis_client.get.return_value = None

        result = await service.get_cached_summary(sample_job_id, sample_tenant_id)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_cached_summary_no_time_range(
        self,
        mock_redis_client,
        sample_job_id,
        sample_tenant_id,
        sample_summary_no_time_range,
    ):
        """Test getting cached summary without time range."""
        service = TimelineCacheService(redis_client=mock_redis_client)

        cached_data = {
            "total_events": 10,
            "dated_events": 0,
            "undated_events": 10,
            "sequence_only_events": 10,
            "time_range": None,
            "entity_type_counts": {"event": 10},
            "precision_distribution": {},
            "uncertainty_distribution": {},
        }
        mock_redis_client.get.return_value = json.dumps(cached_data)

        result = await service.get_cached_summary(sample_job_id, sample_tenant_id)

        assert result is not None
        assert result.time_range is None

    @pytest.mark.asyncio
    async def test_get_cached_summary_redis_error(
        self,
        mock_redis_client,
        sample_job_id,
        sample_tenant_id,
    ):
        """Test graceful degradation on Redis error."""
        service = TimelineCacheService(redis_client=mock_redis_client)
        mock_redis_client.get.side_effect = redis.RedisError("Connection refused")

        result = await service.get_cached_summary(sample_job_id, sample_tenant_id)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_cached_summary_invalid_json(
        self,
        mock_redis_client,
        sample_job_id,
        sample_tenant_id,
    ):
        """Test handling invalid JSON in cache."""
        service = TimelineCacheService(redis_client=mock_redis_client)
        mock_redis_client.get.return_value = "invalid json"
        mock_redis_client.delete.return_value = 1

        result = await service.get_cached_summary(sample_job_id, sample_tenant_id)

        assert result is None
        mock_redis_client.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_cached_summary_no_redis(
        self,
        sample_job_id,
        sample_tenant_id,
    ):
        """Test when Redis client is not available."""
        with patch(
            "kg_builder.services.timeline_cache.get_redis_client",
            return_value=None,
        ):
            service = TimelineCacheService()
            result = await service.get_cached_summary(sample_job_id, sample_tenant_id)
            assert result is None


class TestTimelineCacheServiceSetCachedSummary:
    """Tests for setting cached summaries."""

    @pytest.mark.asyncio
    async def test_set_cached_summary_success(
        self,
        mock_redis_client,
        sample_job_id,
        sample_tenant_id,
        sample_summary,
    ):
        """Test successfully caching a summary."""
        service = TimelineCacheService(redis_client=mock_redis_client)
        mock_redis_client.setex.return_value = True

        result = await service.set_cached_summary(
            sample_job_id,
            sample_tenant_id,
            sample_summary,
        )

        assert result is True
        mock_redis_client.setex.assert_called_once()
        call_args = mock_redis_client.setex.call_args
        assert call_args[0][1] == DEFAULT_SUMMARY_TTL

    @pytest.mark.asyncio
    async def test_set_cached_summary_redis_error(
        self,
        mock_redis_client,
        sample_job_id,
        sample_tenant_id,
        sample_summary,
    ):
        """Test graceful degradation on Redis error during set."""
        service = TimelineCacheService(redis_client=mock_redis_client)
        mock_redis_client.setex.side_effect = redis.RedisError("Connection refused")

        result = await service.set_cached_summary(
            sample_job_id,
            sample_tenant_id,
            sample_summary,
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_set_cached_summary_no_redis(
        self,
        sample_job_id,
        sample_tenant_id,
        sample_summary,
    ):
        """Test when Redis client is not available."""
        with patch(
            "kg_builder.services.timeline_cache.get_redis_client",
            return_value=None,
        ):
            service = TimelineCacheService()
            result = await service.set_cached_summary(
                sample_job_id,
                sample_tenant_id,
                sample_summary,
            )
            assert result is False


class TestTimelineCacheServiceSerializeSummary:
    """Tests for summary serialization."""

    def test_serialize_summary_with_time_range(self, sample_summary):
        """Test serializing summary with time range."""
        service = TimelineCacheService()
        data = service._serialize_summary(sample_summary)

        assert data["total_events"] == sample_summary.total_events
        assert data["time_range"] is not None
        assert "start" in data["time_range"]
        assert "end" in data["time_range"]

    def test_serialize_summary_without_time_range(self, sample_summary_no_time_range):
        """Test serializing summary without time range."""
        service = TimelineCacheService()
        data = service._serialize_summary(sample_summary_no_time_range)

        assert data["total_events"] == sample_summary_no_time_range.total_events
        assert data["time_range"] is None


class TestTimelineCacheServiceInvalidateJobCache:
    """Tests for cache invalidation."""

    @pytest.mark.asyncio
    async def test_invalidate_job_cache_success(
        self,
        mock_redis_client,
        sample_job_id,
    ):
        """Test successfully invalidating job cache."""
        service = TimelineCacheService(redis_client=mock_redis_client)

        # Mock SCAN returning some keys, then no more
        mock_redis_client.scan.side_effect = [
            (0, [f"timeline:summary:tenant1:{sample_job_id}"]),
        ]
        mock_redis_client.delete.return_value = 1

        result = await service.invalidate_job_cache(sample_job_id)

        assert result == 1
        mock_redis_client.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalidate_job_cache_no_keys(
        self,
        mock_redis_client,
        sample_job_id,
    ):
        """Test invalidation when no keys exist."""
        service = TimelineCacheService(redis_client=mock_redis_client)
        mock_redis_client.scan.return_value = (0, [])

        result = await service.invalidate_job_cache(sample_job_id)

        assert result == 0
        mock_redis_client.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalidate_job_cache_redis_error(
        self,
        mock_redis_client,
        sample_job_id,
    ):
        """Test graceful degradation on Redis error during invalidation."""
        service = TimelineCacheService(redis_client=mock_redis_client)
        mock_redis_client.scan.side_effect = redis.RedisError("Connection refused")

        result = await service.invalidate_job_cache(sample_job_id)

        assert result == 0

    @pytest.mark.asyncio
    async def test_invalidate_job_cache_no_redis(self, sample_job_id):
        """Test when Redis client is not available."""
        with patch(
            "kg_builder.services.timeline_cache.get_redis_client",
            return_value=None,
        ):
            service = TimelineCacheService()
            result = await service.invalidate_job_cache(sample_job_id)
            assert result == 0


class TestTimelineCacheServiceExportRateLimit:
    """Tests for export rate limiting."""

    @pytest.mark.asyncio
    async def test_check_export_rate_limit_allowed(
        self,
        mock_redis_client,
    ):
        """Test rate limit check when within limits."""
        service = TimelineCacheService(redis_client=mock_redis_client)
        mock_redis_client.incr.return_value = 5
        mock_redis_client.expire.return_value = True

        is_allowed, retry_after = await service.check_export_rate_limit("user-123")

        assert is_allowed is True
        assert retry_after == 0

    @pytest.mark.asyncio
    async def test_check_export_rate_limit_exceeded(
        self,
        mock_redis_client,
    ):
        """Test rate limit check when limit exceeded."""
        service = TimelineCacheService(redis_client=mock_redis_client)
        mock_redis_client.incr.return_value = 15
        mock_redis_client.ttl.return_value = 30

        is_allowed, retry_after = await service.check_export_rate_limit(
            "user-123",
            limit=10,
        )

        assert is_allowed is False
        assert retry_after == 30

    @pytest.mark.asyncio
    async def test_check_export_rate_limit_first_request(
        self,
        mock_redis_client,
    ):
        """Test rate limit check for first request in window."""
        service = TimelineCacheService(redis_client=mock_redis_client)
        mock_redis_client.incr.return_value = 1
        mock_redis_client.expire.return_value = True

        is_allowed, retry_after = await service.check_export_rate_limit("user-123")

        assert is_allowed is True
        mock_redis_client.expire.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_export_rate_limit_redis_error(
        self,
        mock_redis_client,
    ):
        """Test graceful degradation on Redis error."""
        service = TimelineCacheService(redis_client=mock_redis_client)
        mock_redis_client.incr.side_effect = redis.RedisError("Connection refused")

        is_allowed, retry_after = await service.check_export_rate_limit("user-123")

        # Should allow request on Redis error
        assert is_allowed is True
        assert retry_after == 0

    @pytest.mark.asyncio
    async def test_check_export_rate_limit_no_redis(self):
        """Test when Redis client is not available."""
        with patch(
            "kg_builder.services.timeline_cache.get_redis_client",
            return_value=None,
        ):
            service = TimelineCacheService()
            is_allowed, retry_after = await service.check_export_rate_limit("user-123")

            # Should allow request when no Redis
            assert is_allowed is True
            assert retry_after == 0


class TestGetTimelineCacheService:
    """Tests for the factory function."""

    @pytest.mark.asyncio
    async def test_get_timeline_cache_service_returns_singleton(self):
        """Test that factory returns singleton instance."""
        # Reset singleton
        import kg_builder.services.timeline_cache as cache_module

        cache_module._cache_service = None

        service1 = await get_timeline_cache_service()
        service2 = await get_timeline_cache_service()

        assert service1 is service2

        # Cleanup
        cache_module._cache_service = None

    @pytest.mark.asyncio
    async def test_get_timeline_cache_service_returns_instance(self):
        """Test that factory returns TimelineCacheService instance."""
        # Reset singleton
        import kg_builder.services.timeline_cache as cache_module

        cache_module._cache_service = None

        service = await get_timeline_cache_service()
        assert isinstance(service, TimelineCacheService)

        # Cleanup
        cache_module._cache_service = None
