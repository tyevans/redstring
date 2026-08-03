"""
Unit tests for retry logic with exponential backoff.

Tests cover:
- ExtractionRetryPolicy configuration and defaults
- get_delay method exponential backoff calculation
- Jitter application and variation
- with_retry decorator success and failure scenarios
- RetryExhausted exception behavior
"""

import asyncio
import importlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Create a mock settings module before importing retry
# This avoids the full import chain through __init__.py
_mock_settings = MagicMock()
_mock_settings.OLLAMA_MAX_RETRIES = 3

# Create mock config module
_mock_config = MagicMock()
_mock_config.settings = _mock_settings

# Insert our mock before importing retry module
sys.modules["kg_builder.config"] = _mock_config

# Now we need to import retry module directly without going through __init__.py
# We use importlib to load just the retry module
import importlib.util

spec = importlib.util.spec_from_file_location(
    "kg_builder.extraction.retry",
    "/home/ty/workspace/kg-builder/src/kg_builder/extraction/retry.py"
)
retry_module = importlib.util.module_from_spec(spec)
sys.modules["kg_builder.extraction.retry"] = retry_module
spec.loader.exec_module(retry_module)

# Import the classes from the loaded module
ExtractionRetryPolicy = retry_module.ExtractionRetryPolicy
RetryExhausted = retry_module.RetryExhausted
with_retry = retry_module.with_retry


class TestExtractionRetryPolicy:
    """Tests for ExtractionRetryPolicy class."""

    def test_default_values(self):
        """Test that default values are set correctly from settings."""
        # Settings are mocked at module level with OLLAMA_MAX_RETRIES = 3
        policy = ExtractionRetryPolicy()

        assert policy.max_retries == 3
        assert policy.initial_delay == 1.0
        assert policy.max_delay == 60.0
        assert policy.multiplier == 2.0
        assert policy.jitter == 0.1

    def test_custom_values(self):
        """Test that custom values override defaults."""
        policy = ExtractionRetryPolicy(
            max_retries=5,
            initial_delay=0.5,
            max_delay=30.0,
            multiplier=3.0,
            jitter=0.2,
        )

        assert policy.max_retries == 5
        assert policy.initial_delay == 0.5
        assert policy.max_delay == 30.0
        assert policy.multiplier == 3.0
        assert policy.jitter == 0.2

    def test_validation_negative_max_retries(self):
        """Test that negative max_retries raises ValueError."""
        with pytest.raises(ValueError, match="max_retries must be non-negative"):
            ExtractionRetryPolicy(max_retries=-1)

    def test_validation_negative_initial_delay(self):
        """Test that negative initial_delay raises ValueError."""
        with pytest.raises(ValueError, match="initial_delay must be non-negative"):
            ExtractionRetryPolicy(initial_delay=-1.0)

    def test_validation_negative_max_delay(self):
        """Test that negative max_delay raises ValueError."""
        with pytest.raises(ValueError, match="max_delay must be non-negative"):
            ExtractionRetryPolicy(max_delay=-1.0)

    def test_validation_multiplier_less_than_one(self):
        """Test that multiplier less than 1 raises ValueError."""
        with pytest.raises(ValueError, match="multiplier must be at least 1.0"):
            ExtractionRetryPolicy(multiplier=0.5)

    def test_validation_jitter_out_of_range(self):
        """Test that jitter outside 0-1 range raises ValueError."""
        with pytest.raises(ValueError, match="jitter must be between 0.0 and 1.0"):
            ExtractionRetryPolicy(jitter=1.5)

        with pytest.raises(ValueError, match="jitter must be between 0.0 and 1.0"):
            ExtractionRetryPolicy(jitter=-0.1)

    def test_repr(self):
        """Test string representation of policy."""
        policy = ExtractionRetryPolicy(
            max_retries=3,
            initial_delay=1.0,
            max_delay=60.0,
            multiplier=2.0,
            jitter=0.1,
        )

        repr_str = repr(policy)
        assert "ExtractionRetryPolicy" in repr_str
        assert "max_retries=3" in repr_str
        assert "initial_delay=1.0" in repr_str


class TestGetDelay:
    """Tests for ExtractionRetryPolicy.get_delay method."""

    def test_exponential_backoff_no_jitter(self):
        """Test exponential backoff calculation without jitter."""
        policy = ExtractionRetryPolicy(
            max_retries=5,
            initial_delay=1.0,
            multiplier=2.0,
            jitter=0.0,  # No jitter for deterministic testing
        )

        # Attempt 0: 1.0 * 2^0 = 1.0
        assert policy.get_delay(0) == 1.0

        # Attempt 1: 1.0 * 2^1 = 2.0
        assert policy.get_delay(1) == 2.0

        # Attempt 2: 1.0 * 2^2 = 4.0
        assert policy.get_delay(2) == 4.0

        # Attempt 3: 1.0 * 2^3 = 8.0
        assert policy.get_delay(3) == 8.0

    def test_max_delay_cap(self):
        """Test that delay is capped at max_delay."""
        policy = ExtractionRetryPolicy(
            max_retries=10,
            initial_delay=1.0,
            max_delay=10.0,
            multiplier=2.0,
            jitter=0.0,
        )

        # Attempt 4: 1.0 * 2^4 = 16.0, but capped at 10.0
        assert policy.get_delay(4) == 10.0

        # Attempt 5: still capped at 10.0
        assert policy.get_delay(5) == 10.0

    def test_custom_multiplier(self):
        """Test exponential backoff with custom multiplier."""
        policy = ExtractionRetryPolicy(
            max_retries=5,
            initial_delay=0.5,
            multiplier=3.0,
            max_delay=100.0,
            jitter=0.0,
        )

        # Attempt 0: 0.5 * 3^0 = 0.5
        assert policy.get_delay(0) == 0.5

        # Attempt 1: 0.5 * 3^1 = 1.5
        assert policy.get_delay(1) == 1.5

        # Attempt 2: 0.5 * 3^2 = 4.5
        assert policy.get_delay(2) == 4.5

    def test_jitter_adds_variation(self):
        """Test that jitter adds variation to delays."""
        policy = ExtractionRetryPolicy(
            max_retries=5,
            initial_delay=10.0,
            multiplier=1.0,  # No exponential growth for easier testing
            max_delay=100.0,
            jitter=0.1,  # 10% variation
        )

        # Collect multiple delay values
        delays = [policy.get_delay(0) for _ in range(100)]

        # All delays should be within +/- 10% of base (10.0)
        # So between 9.0 and 11.0
        assert all(9.0 <= d <= 11.0 for d in delays)

        # With 100 samples, we should see some variation
        unique_delays = set(delays)
        assert len(unique_delays) > 1, "Jitter should produce varied delays"

    def test_jitter_range(self):
        """Test that jitter stays within expected range."""
        policy = ExtractionRetryPolicy(
            max_retries=5,
            initial_delay=1.0,
            multiplier=2.0,
            max_delay=60.0,
            jitter=0.2,  # 20% variation
        )

        # Test multiple attempts
        for attempt in range(5):
            base_delay = min(1.0 * (2.0 ** attempt), 60.0)
            jitter_range = base_delay * 0.2

            # Collect samples
            delays = [policy.get_delay(attempt) for _ in range(50)]

            # All should be within range
            min_expected = base_delay - jitter_range
            max_expected = base_delay + jitter_range

            assert all(min_expected <= d <= max_expected for d in delays), (
                f"Delay for attempt {attempt} should be between "
                f"{min_expected} and {max_expected}"
            )

    def test_zero_jitter(self):
        """Test that zero jitter produces deterministic delays."""
        policy = ExtractionRetryPolicy(
            max_retries=5,
            initial_delay=1.0,
            multiplier=2.0,
            jitter=0.0,
        )

        # Multiple calls should return the same value
        delays = [policy.get_delay(0) for _ in range(10)]
        assert all(d == 1.0 for d in delays)


class TestRetryExhausted:
    """Tests for RetryExhausted exception."""

    def test_exception_message(self):
        """Test exception message is set correctly."""
        exc = RetryExhausted("All retries exhausted")
        assert str(exc) == "All retries exhausted"
        assert exc.message == "All retries exhausted"

    def test_exception_attempts(self):
        """Test exception records attempt count."""
        exc = RetryExhausted("Exhausted 5 retries", attempts=5)
        assert exc.attempts == 5

    def test_exception_default_attempts(self):
        """Test exception has default attempts of 0."""
        exc = RetryExhausted("Exhausted")
        assert exc.attempts == 0

    def test_exception_inheritance(self):
        """Test that RetryExhausted is an Exception."""
        exc = RetryExhausted("Test")
        assert isinstance(exc, Exception)


class TestWithRetryDecorator:
    """Tests for with_retry decorator."""

    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        """Test that successful calls return immediately without retry."""
        call_count = 0

        @with_retry(retryable_exceptions=(ValueError,))
        async def successful_func():
            nonlocal call_count
            call_count += 1
            return "success"

        result = await successful_func()

        assert result == "success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_failure_then_success(self):
        """Test that function retries on failure and eventually succeeds."""
        call_count = 0

        @with_retry(
            retryable_exceptions=(ValueError,),
            policy=ExtractionRetryPolicy(max_retries=3, initial_delay=0.01, jitter=0.0),
        )
        async def eventually_succeeds():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("Temporary failure")
            return "success"

        result = await eventually_succeeds()

        assert result == "success"
        assert call_count == 3  # Failed twice, succeeded on third

    @pytest.mark.asyncio
    async def test_raises_retry_exhausted_after_max_attempts(self):
        """Test that RetryExhausted is raised after all attempts fail."""
        call_count = 0

        @with_retry(
            retryable_exceptions=(ValueError,),
            policy=ExtractionRetryPolicy(max_retries=2, initial_delay=0.01, jitter=0.0),
        )
        async def always_fails():
            nonlocal call_count
            call_count += 1
            raise ValueError("Always fails")

        with pytest.raises(RetryExhausted) as exc_info:
            await always_fails()

        assert call_count == 3  # Initial attempt + 2 retries
        assert "Exhausted 2 retries" in str(exc_info.value)
        assert exc_info.value.attempts == 3
        assert isinstance(exc_info.value.__cause__, ValueError)

    @pytest.mark.asyncio
    async def test_non_retryable_exception_propagates(self):
        """Test that non-retryable exceptions propagate immediately."""
        call_count = 0

        @with_retry(
            retryable_exceptions=(ValueError,),
            policy=ExtractionRetryPolicy(max_retries=3, initial_delay=0.01),
        )
        async def raises_type_error():
            nonlocal call_count
            call_count += 1
            raise TypeError("Not retryable")

        with pytest.raises(TypeError, match="Not retryable"):
            await raises_type_error()

        assert call_count == 1  # Only called once, no retry

    @pytest.mark.asyncio
    async def test_multiple_retryable_exceptions(self):
        """Test retry on multiple exception types."""
        call_count = 0
        exceptions = [ValueError("val"), TypeError("type"), ValueError("val2")]

        @with_retry(
            retryable_exceptions=(ValueError, TypeError),
            policy=ExtractionRetryPolicy(max_retries=3, initial_delay=0.01, jitter=0.0),
        )
        async def mixed_errors():
            nonlocal call_count
            if call_count < len(exceptions):
                exc = exceptions[call_count]
                call_count += 1
                raise exc
            call_count += 1
            return "success"

        result = await mixed_errors()

        assert result == "success"
        assert call_count == 4  # 3 failures + 1 success

    @pytest.mark.asyncio
    async def test_preserves_function_metadata(self):
        """Test that decorator preserves function name and docstring."""

        @with_retry(retryable_exceptions=(Exception,))
        async def my_documented_function():
            """This is a docstring."""
            return "result"

        assert my_documented_function.__name__ == "my_documented_function"
        assert my_documented_function.__doc__ == "This is a docstring."

    @pytest.mark.asyncio
    async def test_passes_arguments_correctly(self):
        """Test that positional and keyword arguments are passed correctly."""

        @with_retry(retryable_exceptions=(Exception,))
        async def func_with_args(a, b, c=None):
            return f"a={a}, b={b}, c={c}"

        result = await func_with_args("x", "y", c="z")
        assert result == "a=x, b=y, c=z"

    @pytest.mark.asyncio
    async def test_zero_retries(self):
        """Test behavior with zero max_retries (only initial attempt)."""
        call_count = 0

        @with_retry(
            retryable_exceptions=(ValueError,),
            policy=ExtractionRetryPolicy(max_retries=0, initial_delay=0.01),
        )
        async def single_attempt():
            nonlocal call_count
            call_count += 1
            raise ValueError("Fail")

        with pytest.raises(RetryExhausted):
            await single_attempt()

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_default_policy_used_when_none_provided(self):
        """Test that default policy is used when none is provided."""
        # Settings are mocked at module level with OLLAMA_MAX_RETRIES = 3
        call_count = 0

        @with_retry(retryable_exceptions=(ValueError,))
        async def uses_default_policy():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("Fail")
            return "success"

        result = await uses_default_policy()
        assert result == "success"
        # Should have been called 3 times (2 failures + 1 success)
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_logging_on_retry(self):
        """Test that retries are logged appropriately."""
        call_count = 0

        @with_retry(
            retryable_exceptions=(ValueError,),
            policy=ExtractionRetryPolicy(max_retries=2, initial_delay=0.01, jitter=0.0),
        )
        async def fails_then_succeeds():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ValueError("Temporary failure")
            return "success"

        # Patch the logger on the already-loaded module
        with patch.object(retry_module, "logger") as mock_logger:
            result = await fails_then_succeeds()

            assert result == "success"
            # Should have logged one warning for the failed attempt
            assert mock_logger.warning.called

    @pytest.mark.asyncio
    async def test_logging_on_exhaustion(self):
        """Test that exhaustion is logged as error."""

        @with_retry(
            retryable_exceptions=(ValueError,),
            policy=ExtractionRetryPolicy(max_retries=1, initial_delay=0.01, jitter=0.0),
        )
        async def always_fails():
            raise ValueError("Always fails")

        # Patch the logger on the already-loaded module
        with patch.object(retry_module, "logger") as mock_logger:
            with pytest.raises(RetryExhausted):
                await always_fails()

            # Should have logged error for exhaustion
            assert mock_logger.error.called


class TestRetryTiming:
    """Tests for retry timing behavior."""

    @pytest.mark.asyncio
    async def test_delay_between_retries(self):
        """Test that delays occur between retry attempts."""
        call_times = []

        @with_retry(
            retryable_exceptions=(ValueError,),
            policy=ExtractionRetryPolicy(
                max_retries=2,
                initial_delay=0.1,
                multiplier=2.0,
                jitter=0.0,
            ),
        )
        async def timed_func():
            call_times.append(asyncio.get_event_loop().time())
            if len(call_times) < 3:
                raise ValueError("Fail")
            return "success"

        await timed_func()

        assert len(call_times) == 3

        # First retry delay should be ~0.1s
        first_delay = call_times[1] - call_times[0]
        assert 0.08 <= first_delay <= 0.15, f"First delay was {first_delay}"

        # Second retry delay should be ~0.2s
        second_delay = call_times[2] - call_times[1]
        assert 0.15 <= second_delay <= 0.25, f"Second delay was {second_delay}"

    @pytest.mark.asyncio
    async def test_async_sleep_called(self):
        """Test that asyncio.sleep is used for delays."""

        @with_retry(
            retryable_exceptions=(ValueError,),
            policy=ExtractionRetryPolicy(max_retries=1, initial_delay=0.5, jitter=0.0),
        )
        async def fails_once():
            if not hasattr(fails_once, "called"):
                fails_once.called = True
                raise ValueError("First fail")
            return "success"

        # Patch asyncio.sleep on the already-loaded module
        with patch.object(retry_module.asyncio, "sleep", new_callable=AsyncMock) as mock_sleep:
            await fails_once()

            mock_sleep.assert_called_once()
            # Check that sleep was called with approximately 0.5 seconds
            call_arg = mock_sleep.call_args[0][0]
            assert 0.45 <= call_arg <= 0.55
