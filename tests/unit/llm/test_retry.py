"""Retry with exponential backoff and jitter.

The import preamble this module used to carry is gone. It inserted a
`MagicMock` at `sys.modules["redstring.config"]` and then loaded
`retry.py` by absolute filesystem path, because the real module read
`settings.OLLAMA_MAX_RETRIES` at construction and importing it dragged in the
whole config chain. Slice 6 replaced that read with `DEFAULT_MAX_RETRIES`, so
a plain import works and the module no longer poisons `sys.modules` for every
test that runs after it -- part of BACKLOG B10d.
"""

import logging

import pytest

from redstring.llm.retry import (
    ExtractionRetryPolicy,
    RetryExhausted,
    with_retry,
)


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
    async def test_logging_on_retry(self, caplog: pytest.LogCaptureFixture):
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

        with caplog.at_level(logging.WARNING):
            result = await fails_then_succeeds()

        assert result == "success"
        # Should have logged one warning for the failed attempt
        assert any(record.levelno == logging.WARNING for record in caplog.records)

    @pytest.mark.asyncio
    async def test_logging_on_exhaustion(self, caplog: pytest.LogCaptureFixture):
        """Test that exhaustion is logged as error."""

        @with_retry(
            retryable_exceptions=(ValueError,),
            policy=ExtractionRetryPolicy(max_retries=1, initial_delay=0.01, jitter=0.0),
        )
        async def always_fails():
            raise ValueError("Always fails")

        with caplog.at_level(logging.ERROR), pytest.raises(RetryExhausted):
            await always_fails()

        # Should have logged error for exhaustion
        assert any(record.levelno == logging.ERROR for record in caplog.records)


class TestRetryTiming:
    """Tests for retry timing behavior."""

    @pytest.mark.asyncio
    async def test_the_delay_multiplies_between_successive_retries(self):
        """Backoff grows: 0.1s, then 0.2s.

        Asserted on the values handed to `asyncio.sleep`, not on elapsed wall
        clock. The previous version measured `event_loop.time()` around each
        attempt and required the second gap to be `<= 0.25`, which **cannot be
        made reliable**: `asyncio.sleep(d)` promises to sleep *at least* `d`
        and says nothing about the upper bound, so any ceiling is an assertion
        about machine load. It duly failed the commit gate at 0.276s on a busy
        machine.

        That is the same defect as the hypothesis deadline in
        `tests/conftest.py` -- a wall-clock upper bound used as a proxy for a
        property that is not about wall clock -- and it fails the same way,
        by naming the retry code when the problem is the laptop.

        The distinction from `test_async_sleep_called` below is the multiplier:
        that test has one retry and so cannot see growth at all. This one is
        the only place the *sequence* is pinned.
        """
        attempts = 0

        delays: list[float] = []

        async def fake_sleep(delay: float) -> None:
            delays.append(delay)

        @with_retry(
            retryable_exceptions=(ValueError,),
            policy=ExtractionRetryPolicy(
                max_retries=2,
                initial_delay=0.1,
                multiplier=2.0,
                jitter=0.0,
            ),
            sleeper=fake_sleep,
        )
        async def flaky():
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise ValueError("Fail")
            return "success"

        assert await flaky() == "success"
        assert attempts == 3
        assert delays == pytest.approx([0.1, 0.2]), delays

    @pytest.mark.asyncio
    async def test_async_sleep_called(self):
        """Test that asyncio.sleep is used for delays."""
        sleep_calls: list[float] = []

        async def record_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        @with_retry(
            retryable_exceptions=(ValueError,),
            policy=ExtractionRetryPolicy(max_retries=1, initial_delay=0.5, jitter=0.0),
            sleeper=record_sleep,
        )
        async def fails_once():
            if not hasattr(fails_once, "called"):
                fails_once.called = True
                raise ValueError("First fail")
            return "success"

        await fails_once()
        assert len(sleep_calls) == 1
        assert 0.45 <= sleep_calls[0] <= 0.55
