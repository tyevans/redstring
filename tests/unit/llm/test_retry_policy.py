"""Tests for LlmRetryPolicy and RetryExhausted exception classes."""

from __future__ import annotations

import pytest

from redstring.llm.retry import (
    DEFAULT_MAX_RETRIES,
    ExtractionRetryPolicy,
    LlmRetryPolicy,
    RetryExhausted,
)


class TestLlmRetryPolicy:
    """Tests for LlmRetryPolicy class and ExtractionRetryPolicy alias."""

    def test_alias_identity(self) -> None:
        """ExtractionRetryPolicy is an exact alias for LlmRetryPolicy."""
        assert ExtractionRetryPolicy is LlmRetryPolicy

    def test_default_values(self) -> None:
        """The defaults a caller gets without configuring anything."""
        policy = LlmRetryPolicy()

        assert policy.max_retries == DEFAULT_MAX_RETRIES
        assert policy.initial_delay == 1.0
        assert policy.max_delay == 60.0
        assert policy.multiplier == 2.0
        assert policy.jitter == 0.1

    def test_custom_values(self) -> None:
        """Test that custom values override defaults."""
        policy = LlmRetryPolicy(
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

    def test_validation_negative_max_retries(self) -> None:
        """Test that negative max_retries raises ValueError."""
        with pytest.raises(ValueError, match="max_retries must be non-negative"):
            LlmRetryPolicy(max_retries=-1)

    def test_validation_negative_initial_delay(self) -> None:
        """Test that negative initial_delay raises ValueError."""
        with pytest.raises(ValueError, match="initial_delay must be non-negative"):
            LlmRetryPolicy(initial_delay=-1.0)

    def test_validation_negative_max_delay(self) -> None:
        """Test that negative max_delay raises ValueError."""
        with pytest.raises(ValueError, match="max_delay must be non-negative"):
            LlmRetryPolicy(max_delay=-1.0)

    def test_validation_multiplier_less_than_one(self) -> None:
        """Test that multiplier less than 1 raises ValueError."""
        with pytest.raises(ValueError, match=r"multiplier must be at least 1\.0"):
            LlmRetryPolicy(multiplier=0.5)

    def test_validation_jitter_out_of_range(self) -> None:
        """Test that jitter outside 0-1 range raises ValueError."""
        with pytest.raises(ValueError, match=r"jitter must be between 0\.0 and 1\.0"):
            LlmRetryPolicy(jitter=1.5)

        with pytest.raises(ValueError, match=r"jitter must be between 0\.0 and 1\.0"):
            LlmRetryPolicy(jitter=-0.1)

    def test_repr(self) -> None:
        """Test string representation of policy."""
        policy = LlmRetryPolicy(
            max_retries=3,
            initial_delay=1.0,
            max_delay=60.0,
            multiplier=2.0,
            jitter=0.1,
        )

        repr_str = repr(policy)
        assert "LlmRetryPolicy" in repr_str
        assert "max_retries=3" in repr_str
        assert "initial_delay=1.0" in repr_str


class TestGetDelay:
    """Tests for LlmRetryPolicy.get_delay method."""

    def test_exponential_backoff_no_jitter(self) -> None:
        """Test exponential backoff calculation without jitter."""
        policy = LlmRetryPolicy(
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

    def test_max_delay_cap(self) -> None:
        """Test that delay is capped at max_delay."""
        policy = LlmRetryPolicy(
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

    def test_custom_multiplier(self) -> None:
        """Test exponential backoff with custom multiplier."""
        policy = LlmRetryPolicy(
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

    def test_jitter_adds_variation(self) -> None:
        """Test that jitter adds variation to delays."""
        policy = LlmRetryPolicy(
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

    def test_jitter_range(self) -> None:
        """Test that jitter stays within expected range."""
        policy = LlmRetryPolicy(
            max_retries=5,
            initial_delay=1.0,
            multiplier=2.0,
            max_delay=60.0,
            jitter=0.2,  # 20% variation
        )

        # Test multiple attempts
        for attempt in range(5):
            base_delay = min(1.0 * (2.0**attempt), 60.0)
            jitter_range = base_delay * 0.2

            # Collect samples
            delays = [policy.get_delay(attempt) for _ in range(50)]

            # All should be within range
            min_expected = base_delay - jitter_range
            max_expected = base_delay + jitter_range

            assert all(min_expected <= d <= max_expected for d in delays), (
                f"Delay for attempt {attempt} should be between {min_expected} and {max_expected}"
            )

    def test_zero_jitter(self) -> None:
        """Test that zero jitter produces deterministic delays."""
        policy = LlmRetryPolicy(
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

    def test_exception_message(self) -> None:
        """Test exception message is set correctly."""
        exc = RetryExhausted("All retries exhausted")
        assert str(exc) == "All retries exhausted"
        assert exc.message == "All retries exhausted"

    def test_exception_attempts(self) -> None:
        """Test exception records attempt count."""
        exc = RetryExhausted("Exhausted 5 retries", attempts=5)
        assert exc.attempts == 5

    def test_exception_default_attempts(self) -> None:
        """Test exception has default attempts of 0."""
        exc = RetryExhausted("Exhausted")
        assert exc.attempts == 0

    def test_exception_inheritance(self) -> None:
        """Test that RetryExhausted is an Exception."""
        exc = RetryExhausted("Test")
        assert isinstance(exc, Exception)
