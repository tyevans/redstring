"""
Unit tests for OllamaRateLimiter.

Tests rate limiting functionality including:
- RateLimitExceeded exception properties
- Acquiring rate limit slots
- Handling rate limit exceeded scenarios
- Getting remaining request counts
- Factory function and singleton pattern

Uses mocking to avoid external Redis dependency.
"""

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

from kg_builder.extraction.rate_limiter import (
    OllamaRateLimiter,
    RateLimitExceeded,
    get_rate_limiter,
    reset_rate_limiter,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def tenant_id() -> UUID:
    """Generate a test tenant ID."""
    return uuid4()


class MockPipeline:
    """Mock for Redis Pipeline that supports async context manager."""

    def __init__(self):
        self.execute_result = [None, 0, None, None]
        self.zremrangebyscore_calls = []
        self.zcard_calls = []
        self.zadd_calls = []
        self.expire_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None

    async def zremrangebyscore(self, key, min_score, max_score):
        self.zremrangebyscore_calls.append((key, min_score, max_score))

    async def zcard(self, key):
        self.zcard_calls.append(key)

    async def zadd(self, key, mapping):
        self.zadd_calls.append((key, mapping))

    async def expire(self, key, seconds):
        self.expire_calls.append((key, seconds))

    async def execute(self):
        return self.execute_result


class MockRedis:
    """Mock for async Redis client."""

    def __init__(self):
        self.mock_pipeline = MockPipeline()
        self.zremrangebyscore_result = None
        self.zcard_result = 0
        self.zrange_result = []
        self.close_called = False

    def pipeline(self, transaction=True):
        return self.mock_pipeline

    async def zremrangebyscore(self, key, min_score, max_score):
        return self.zremrangebyscore_result

    async def zcard(self, key):
        return self.zcard_result

    async def zrange(self, key, start, end, withscores=False):
        return self.zrange_result

    async def close(self):
        self.close_called = True


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    return MockRedis()


@pytest.fixture
def rate_limiter(mock_redis):
    """Create a rate limiter with mocked Redis."""
    limiter = OllamaRateLimiter(
        redis_url="redis://localhost:6379/0",
        rpm=10,
        window_seconds=60,
    )
    limiter._redis = mock_redis
    return limiter


@pytest.fixture(autouse=True)
def reset_global_limiter():
    """Reset the global rate limiter instance before each test."""
    reset_rate_limiter()
    yield
    reset_rate_limiter()


# =============================================================================
# RateLimitExceeded Exception Tests
# =============================================================================


class TestRateLimitExceeded:
    """Tests for the RateLimitExceeded exception class."""

    def test_exception_has_tenant_id(self, tenant_id):
        """Test RateLimitExceeded stores tenant_id."""
        exc = RateLimitExceeded(tenant_id, retry_after=30.0)
        assert exc.tenant_id == tenant_id

    def test_exception_has_retry_after(self, tenant_id):
        """Test RateLimitExceeded stores retry_after."""
        exc = RateLimitExceeded(tenant_id, retry_after=45.5)
        assert exc.retry_after == 45.5

    def test_exception_message_contains_tenant_id(self, tenant_id):
        """Test exception message includes tenant_id."""
        exc = RateLimitExceeded(tenant_id, retry_after=30.0)
        assert str(tenant_id) in str(exc)

    def test_exception_message_contains_retry_after(self, tenant_id):
        """Test exception message includes retry_after."""
        exc = RateLimitExceeded(tenant_id, retry_after=30.0)
        assert "30.0s" in str(exc)

    def test_exception_inherits_from_exception(self, tenant_id):
        """Test RateLimitExceeded is an Exception."""
        exc = RateLimitExceeded(tenant_id, retry_after=30.0)
        assert isinstance(exc, Exception)

    def test_exception_with_zero_retry_after(self, tenant_id):
        """Test exception with zero retry_after."""
        exc = RateLimitExceeded(tenant_id, retry_after=0.0)
        assert exc.retry_after == 0.0


# =============================================================================
# OllamaRateLimiter Initialization Tests
# =============================================================================


class TestRateLimiterInit:
    """Tests for OllamaRateLimiter initialization."""

    def test_init_with_custom_config(self):
        """Test initialization with custom configuration."""
        limiter = OllamaRateLimiter(
            redis_url="redis://custom:6379/1",
            rpm=20,
            window_seconds=120,
        )

        assert limiter._redis_url == "redis://custom:6379/1"
        assert limiter._rpm == 20
        assert limiter._window == 120

    def test_init_with_defaults(self):
        """Test initialization uses settings defaults."""
        with patch("kg_builder.extraction.rate_limiter.settings") as mock_settings:
            mock_settings.REDIS_URL = "redis://default:6379/0"
            mock_settings.OLLAMA_RATE_LIMIT_RPM = 30

            limiter = OllamaRateLimiter()

            assert limiter._redis_url == "redis://default:6379/0"
            assert limiter._rpm == 30
            assert limiter._window == 60  # Default value

    def test_redis_connection_not_created_on_init(self):
        """Test Redis connection is lazy-loaded."""
        limiter = OllamaRateLimiter(
            redis_url="redis://localhost:6379/0",
            rpm=10,
        )
        assert limiter._redis is None


# =============================================================================
# Acquire Rate Limit Tests
# =============================================================================


class TestAcquire:
    """Tests for the acquire method."""

    @pytest.mark.asyncio
    async def test_acquire_allows_requests_under_limit(self, rate_limiter, mock_redis, tenant_id):
        """Test acquire allows requests when under rate limit."""
        # Setup mock to return count under limit
        mock_redis.mock_pipeline.execute_result = [None, 5, None, None]  # 5 requests (under limit of 10)

        # Should not raise
        await rate_limiter.acquire(tenant_id)

    @pytest.mark.asyncio
    async def test_acquire_raises_when_limit_exceeded(self, rate_limiter, mock_redis, tenant_id):
        """Test acquire raises RateLimitExceeded when limit is reached."""
        import time

        # Setup mock to return count at limit
        mock_redis.mock_pipeline.execute_result = [None, 10, None, None]  # 10 requests (at limit)

        # Setup mock for oldest entry lookup
        oldest_time = time.time() - 30  # 30 seconds ago
        mock_redis.zrange_result = [(b"timestamp", oldest_time)]

        with pytest.raises(RateLimitExceeded) as exc_info:
            await rate_limiter.acquire(tenant_id)

        assert exc_info.value.tenant_id == tenant_id
        assert exc_info.value.retry_after >= 0

    @pytest.mark.asyncio
    async def test_acquire_uses_correct_redis_key(self, rate_limiter, mock_redis, tenant_id):
        """Test acquire uses correct Redis key format."""
        mock_redis.mock_pipeline.execute_result = [None, 0, None, None]

        await rate_limiter.acquire(tenant_id)

        # Verify key format was used
        expected_key = f"ollama_ratelimit:{tenant_id}"
        assert rate_limiter._get_key(tenant_id) == expected_key

    @pytest.mark.asyncio
    async def test_acquire_cleans_old_entries(self, rate_limiter, mock_redis, tenant_id):
        """Test acquire removes entries outside the window."""
        mock_redis.mock_pipeline.execute_result = [None, 0, None, None]

        await rate_limiter.acquire(tenant_id)

        # Verify zremrangebyscore was called to clean old entries
        assert len(mock_redis.mock_pipeline.zremrangebyscore_calls) == 1

    @pytest.mark.asyncio
    async def test_acquire_sets_key_expiry(self, rate_limiter, mock_redis, tenant_id):
        """Test acquire sets key expiry for cleanup."""
        mock_redis.mock_pipeline.execute_result = [None, 0, None, None]

        await rate_limiter.acquire(tenant_id)

        # Verify expire was called
        assert len(mock_redis.mock_pipeline.expire_calls) == 1
        key, seconds = mock_redis.mock_pipeline.expire_calls[0]
        assert seconds == rate_limiter._window

    @pytest.mark.asyncio
    async def test_acquire_retry_after_calculated_correctly(self, rate_limiter, mock_redis, tenant_id):
        """Test retry_after is calculated based on oldest entry."""
        import time

        mock_redis.mock_pipeline.execute_result = [None, 10, None, None]  # At limit

        current_time = time.time()
        oldest_time = current_time - 40  # 40 seconds ago
        mock_redis.zrange_result = [(b"timestamp", oldest_time)]

        with pytest.raises(RateLimitExceeded) as exc_info:
            await rate_limiter.acquire(tenant_id)

        # retry_after should be approximately 20 seconds (60 - 40)
        # Since oldest entry is 40s old and window is 60s
        assert exc_info.value.retry_after >= 0
        # Allow some tolerance for timing
        assert exc_info.value.retry_after <= 25

    @pytest.mark.asyncio
    async def test_acquire_retry_after_defaults_to_window_when_no_entries(
        self, rate_limiter, mock_redis, tenant_id
    ):
        """Test retry_after defaults to window when no oldest entry."""
        mock_redis.mock_pipeline.execute_result = [None, 10, None, None]
        mock_redis.zrange_result = []  # No entries (edge case)

        with pytest.raises(RateLimitExceeded) as exc_info:
            await rate_limiter.acquire(tenant_id)

        assert exc_info.value.retry_after == rate_limiter._window


# =============================================================================
# Get Remaining Tests
# =============================================================================


class TestGetRemaining:
    """Tests for the get_remaining method."""

    @pytest.mark.asyncio
    async def test_get_remaining_returns_correct_count(self, rate_limiter, mock_redis, tenant_id):
        """Test get_remaining returns correct remaining count."""
        mock_redis.zremrangebyscore_result = None
        mock_redis.zcard_result = 3  # 3 requests used

        remaining = await rate_limiter.get_remaining(tenant_id)

        assert remaining == 7  # 10 - 3 = 7

    @pytest.mark.asyncio
    async def test_get_remaining_returns_zero_at_limit(self, rate_limiter, mock_redis, tenant_id):
        """Test get_remaining returns zero when at limit."""
        mock_redis.zremrangebyscore_result = None
        mock_redis.zcard_result = 10  # At limit

        remaining = await rate_limiter.get_remaining(tenant_id)

        assert remaining == 0

    @pytest.mark.asyncio
    async def test_get_remaining_returns_full_limit_when_empty(
        self, rate_limiter, mock_redis, tenant_id
    ):
        """Test get_remaining returns full limit when no requests made."""
        mock_redis.zremrangebyscore_result = None
        mock_redis.zcard_result = 0

        remaining = await rate_limiter.get_remaining(tenant_id)

        assert remaining == 10

    @pytest.mark.asyncio
    async def test_get_remaining_never_negative(self, rate_limiter, mock_redis, tenant_id):
        """Test get_remaining never returns negative value."""
        mock_redis.zremrangebyscore_result = None
        mock_redis.zcard_result = 15  # Over limit (shouldn't happen normally)

        remaining = await rate_limiter.get_remaining(tenant_id)

        assert remaining == 0


# =============================================================================
# Close Connection Tests
# =============================================================================


class TestClose:
    """Tests for the close method."""

    @pytest.mark.asyncio
    async def test_close_closes_redis_connection(self, rate_limiter, mock_redis):
        """Test close closes the Redis connection."""
        await rate_limiter.close()

        assert mock_redis.close_called

    @pytest.mark.asyncio
    async def test_close_clears_redis_reference(self, rate_limiter, mock_redis):
        """Test close sets Redis reference to None."""
        await rate_limiter.close()

        assert rate_limiter._redis is None

    @pytest.mark.asyncio
    async def test_close_when_no_connection(self):
        """Test close works when no connection was made."""
        limiter = OllamaRateLimiter(
            redis_url="redis://localhost:6379/0",
            rpm=10,
        )

        # Should not raise
        await limiter.close()

        assert limiter._redis is None


# =============================================================================
# Factory Function Tests
# =============================================================================


class TestFactoryFunction:
    """Tests for the get_rate_limiter factory function."""

    def test_get_rate_limiter_creates_instance(self):
        """Test factory creates a rate limiter instance."""
        with patch("kg_builder.extraction.rate_limiter.settings") as mock_settings:
            mock_settings.REDIS_URL = "redis://test:6379/0"
            mock_settings.OLLAMA_RATE_LIMIT_RPM = 10

            limiter = get_rate_limiter()

            assert isinstance(limiter, OllamaRateLimiter)

    def test_get_rate_limiter_returns_singleton(self):
        """Test factory returns the same instance on multiple calls."""
        with patch("kg_builder.extraction.rate_limiter.settings") as mock_settings:
            mock_settings.REDIS_URL = "redis://test:6379/0"
            mock_settings.OLLAMA_RATE_LIMIT_RPM = 10

            limiter1 = get_rate_limiter()
            limiter2 = get_rate_limiter()

            assert limiter1 is limiter2

    def test_reset_rate_limiter_clears_singleton(self):
        """Test reset_rate_limiter clears the singleton."""
        with patch("kg_builder.extraction.rate_limiter.settings") as mock_settings:
            mock_settings.REDIS_URL = "redis://test:6379/0"
            mock_settings.OLLAMA_RATE_LIMIT_RPM = 10

            limiter1 = get_rate_limiter()
            reset_rate_limiter()
            limiter2 = get_rate_limiter()

            assert limiter1 is not limiter2


# =============================================================================
# Redis Key Format Tests
# =============================================================================


class TestRedisKeyFormat:
    """Tests for Redis key formatting."""

    def test_get_key_includes_tenant_id(self, rate_limiter, tenant_id):
        """Test key format includes tenant ID."""
        key = rate_limiter._get_key(tenant_id)

        assert str(tenant_id) in key

    def test_get_key_has_prefix(self, rate_limiter, tenant_id):
        """Test key format has ollama_ratelimit prefix."""
        key = rate_limiter._get_key(tenant_id)

        assert key.startswith("ollama_ratelimit:")

    def test_get_key_unique_per_tenant(self, rate_limiter):
        """Test different tenants get different keys."""
        tenant1 = uuid4()
        tenant2 = uuid4()

        key1 = rate_limiter._get_key(tenant1)
        key2 = rate_limiter._get_key(tenant2)

        assert key1 != key2


# =============================================================================
# Integration with Extraction Service Tests
# =============================================================================


class TestExtractionServiceIntegration:
    """Tests for integration with OllamaExtractionService."""

    @pytest.mark.asyncio
    async def test_extraction_service_calls_rate_limiter(self, tenant_id):
        """Test extraction service calls rate limiter when tenant_id provided."""
        from unittest.mock import MagicMock as SyncMagicMock

        from kg_builder.extraction.ollama_extractor import OllamaExtractionService
        from kg_builder.extraction.schemas import ExtractionResult

        # Create mock extraction result
        mock_result = ExtractionResult(
            entities=[],
            relationships=[],
            extraction_notes="Test",
        )

        mock_run_result = SyncMagicMock()
        mock_run_result.data = mock_result

        with patch("kg_builder.extraction.rate_limiter.get_rate_limiter") as mock_get_limiter:
            mock_limiter = AsyncMock()
            mock_get_limiter.return_value = mock_limiter

            service = OllamaExtractionService(
                base_url="http://localhost:11434",
                model="test-model",
                timeout=30,
            )

            with patch.object(service._agent, "run", new_callable=AsyncMock) as mock_run:
                mock_run.return_value = mock_run_result

                await service.extract(
                    content="test content",
                    page_url="http://example.com",
                    tenant_id=tenant_id,
                )

                mock_limiter.acquire.assert_called_once_with(tenant_id)

    @pytest.mark.asyncio
    async def test_extraction_service_skips_rate_limiter_without_tenant_id(self):
        """Test extraction service skips rate limiter when no tenant_id."""
        from unittest.mock import MagicMock as SyncMagicMock

        from kg_builder.extraction.ollama_extractor import OllamaExtractionService
        from kg_builder.extraction.schemas import ExtractionResult

        mock_result = ExtractionResult(
            entities=[],
            relationships=[],
            extraction_notes="Test",
        )

        mock_run_result = SyncMagicMock()
        mock_run_result.data = mock_result

        with patch("kg_builder.extraction.rate_limiter.get_rate_limiter") as mock_get_limiter:
            mock_limiter = AsyncMock()
            mock_get_limiter.return_value = mock_limiter

            service = OllamaExtractionService(
                base_url="http://localhost:11434",
                model="test-model",
                timeout=30,
            )

            with patch.object(service._agent, "run", new_callable=AsyncMock) as mock_run:
                mock_run.return_value = mock_run_result

                # Call without tenant_id
                await service.extract(
                    content="test content",
                    page_url="http://example.com",
                )

                # Rate limiter should not be called
                mock_get_limiter.assert_not_called()

    @pytest.mark.asyncio
    async def test_extraction_service_propagates_rate_limit_exceeded(self, tenant_id):
        """Test extraction service propagates RateLimitExceeded exception."""
        from kg_builder.extraction.ollama_extractor import OllamaExtractionService

        with patch("kg_builder.extraction.rate_limiter.get_rate_limiter") as mock_get_limiter:
            mock_limiter = AsyncMock()
            mock_limiter.acquire.side_effect = RateLimitExceeded(tenant_id, retry_after=30.0)
            mock_get_limiter.return_value = mock_limiter

            service = OllamaExtractionService(
                base_url="http://localhost:11434",
                model="test-model",
                timeout=30,
            )

            with pytest.raises(RateLimitExceeded) as exc_info:
                await service.extract(
                    content="test content",
                    page_url="http://example.com",
                    tenant_id=tenant_id,
                )

            assert exc_info.value.tenant_id == tenant_id
            assert exc_info.value.retry_after == 30.0
