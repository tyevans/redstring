"""
Retry logic for Ollama extraction operations.

Provides exponential backoff with jitter for resilient extraction requests.
The retry mechanism helps handle transient failures from the Ollama API
such as connection issues, timeouts, or temporary server errors.

Example:
    from kg_builder.extraction.retry import with_retry, ExtractionRetryPolicy

    # Use the default policy
    @with_retry(retryable_exceptions=(httpx.ConnectError, httpx.TimeoutException))
    async def extract_with_ollama(content: str) -> ExtractionResult:
        ...

    # Use custom policy
    custom_policy = ExtractionRetryPolicy(max_retries=5, initial_delay=2.0)

    @with_retry(
        retryable_exceptions=(httpx.ConnectError,),
        policy=custom_policy,
    )
    async def extract_with_custom_retry(content: str) -> ExtractionResult:
        ...
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Coroutine
from functools import wraps
from typing import Any, ParamSpec, TypeVar

logger = logging.getLogger(__name__)

# Type variables for generic decorator typing
P = ParamSpec("P")
T = TypeVar("T")


#: Attempts after the first, when the caller does not say.
DEFAULT_MAX_RETRIES = 3


class RetryExhausted(Exception):
    """Raised when all retry attempts are exhausted.

    This exception indicates that the maximum number of retry attempts
    has been reached without a successful result. The original exception
    that caused the final failure is chained as the __cause__.

    Attributes:
        message: Human-readable description of the retry exhaustion
        attempts: Number of attempts made before exhaustion
    """

    def __init__(self, message: str, attempts: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.attempts = attempts


class ExtractionRetryPolicy:
    """Retry policy configuration for extraction operations.

    Implements exponential backoff with configurable jitter to prevent
    thundering herd problems when multiple clients retry simultaneously.

    The delay for attempt N is calculated as:
        base_delay = min(initial_delay * (multiplier ** attempt), max_delay)
        jitter_range = base_delay * jitter
        final_delay = base_delay + random.uniform(-jitter_range, jitter_range)

    Attributes:
        max_retries: Maximum number of retry attempts (default `DEFAULT_MAX_RETRIES`)
        initial_delay: Base delay in seconds for first retry (default: 1.0)
        max_delay: Maximum delay cap in seconds (default: 60.0)
        multiplier: Exponential multiplier for backoff (default: 2.0)
        jitter: Jitter factor as fraction of delay (default: 0.1, meaning +/- 10%)

    Example:
        policy = ExtractionRetryPolicy()

        # Custom policy with more aggressive backoff
        policy = ExtractionRetryPolicy(
            max_retries=5,
            initial_delay=0.5,
            max_delay=30.0,
            multiplier=3.0,
            jitter=0.2,
        )

        # Get delay for attempt 2 (0-indexed)
        delay = policy.get_delay(2)  # ~4.0 seconds with jitter
    """

    def __init__(
        self,
        max_retries: int | None = None,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        multiplier: float = 2.0,
        jitter: float = 0.1,
    ) -> None:
        """Initialize the retry policy.

        Args:
            max_retries: Maximum retry attempts. Defaults to `DEFAULT_MAX_RETRIES`.
            initial_delay: Initial delay in seconds before first retry. Defaults to 1.0.
            max_delay: Maximum delay cap in seconds. Defaults to 60.0.
            multiplier: Exponential backoff multiplier. Defaults to 2.0.
            jitter: Jitter factor (0.0 to 1.0). Defaults to 0.1 (10% variation).

        Raises:
            ValueError: If parameters are invalid (negative delays, etc.)
        """
        if max_retries is not None and max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if initial_delay < 0:
            raise ValueError("initial_delay must be non-negative")
        if max_delay < 0:
            raise ValueError("max_delay must be non-negative")
        if multiplier < 1:
            raise ValueError("multiplier must be at least 1.0")
        if not 0.0 <= jitter <= 1.0:
            raise ValueError("jitter must be between 0.0 and 1.0")

        # Plain default rather than `settings.OLLAMA_MAX_RETRIES`. A library
        # reading a process-wide settings object makes its behaviour depend on
        # a file the caller may not know exists, and it was the reason this
        # module's tests had to poison `sys.modules["kg_builder.config"]`
        # before importing it -- part of BACKLOG B10d.
        self.max_retries = max_retries if max_retries is not None else DEFAULT_MAX_RETRIES
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.multiplier = multiplier
        self.jitter = jitter

    def get_delay(self, attempt: int) -> float:
        """Calculate the delay for a given attempt number.

        Uses exponential backoff with jitter. The attempt number is 0-indexed,
        so attempt=0 is the first retry (after the initial failed attempt).

        Args:
            attempt: The retry attempt number (0-indexed)

        Returns:
            Delay in seconds, with jitter applied

        Example:
            policy = ExtractionRetryPolicy(initial_delay=1.0, multiplier=2.0, jitter=0.0)
            policy.get_delay(0)  # 1.0 seconds (1.0 * 2^0)
            policy.get_delay(1)  # 2.0 seconds (1.0 * 2^1)
            policy.get_delay(2)  # 4.0 seconds (1.0 * 2^2)
        """
        # Calculate base delay with exponential backoff
        base_delay = min(
            self.initial_delay * (self.multiplier**attempt),
            self.max_delay,
        )

        # Apply jitter: random variation within +/- (jitter * base_delay)
        if self.jitter > 0:
            jitter_range = base_delay * self.jitter
            jittered_delay = base_delay + random.uniform(-jitter_range, jitter_range)
            # Ensure delay is non-negative
            return max(0.0, jittered_delay)

        return base_delay

    def __repr__(self) -> str:
        return (
            f"ExtractionRetryPolicy("
            f"max_retries={self.max_retries}, "
            f"initial_delay={self.initial_delay}, "
            f"max_delay={self.max_delay}, "
            f"multiplier={self.multiplier}, "
            f"jitter={self.jitter})"
        )


def with_retry(
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
    policy: ExtractionRetryPolicy | None = None,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Coroutine[Any, Any, T]]]:
    """Decorator for retry with exponential backoff.

    Wraps an async function to automatically retry on specified exceptions.
    Uses exponential backoff with jitter between retry attempts.

    Args:
        retryable_exceptions: Tuple of exception types that should trigger retry.
            Only these exceptions will be caught and retried; others will
            propagate immediately. Defaults to (Exception,) which retries all.
        policy: Retry policy configuration. If not provided, uses default
            ExtractionRetryPolicy with settings from configuration.

    Returns:
        Decorated async function with retry behavior

    Raises:
        RetryExhausted: When all retry attempts are exhausted. The original
            exception is available as __cause__.

    Example:
        import httpx
        from kg_builder.extraction.retry import with_retry, ExtractionRetryPolicy

        # Retry on connection errors with default policy
        @with_retry(retryable_exceptions=(httpx.ConnectError, httpx.TimeoutException))
        async def fetch_data(url: str) -> dict:
            async with httpx.AsyncClient() as client:
                response = await client.get(url)
                return response.json()

        # Custom policy
        @with_retry(
            retryable_exceptions=(ConnectionError,),
            policy=ExtractionRetryPolicy(max_retries=5, initial_delay=0.5),
        )
        async def fetch_with_custom_retry(url: str) -> dict:
            ...
    """
    retry_policy = policy or ExtractionRetryPolicy()

    # `Callable[P, Awaitable[T]]`, not `Callable[P, T]`. This decorator only
    # works on coroutine functions -- it awaits the result -- and the looser
    # annotation let it be applied to a sync one, where `await` on a plain
    # value raises at the first *retryable* failure rather than at the call.
    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Coroutine[Any, Any, T]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            last_exception: Exception | None = None
            total_attempts = retry_policy.max_retries + 1

            for attempt in range(total_attempts):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e
                    current_attempt = attempt + 1

                    if attempt < retry_policy.max_retries:
                        delay = retry_policy.get_delay(attempt)
                        logger.warning(
                            "Retry attempt %d/%d failed for %s: %s. Retrying in %.2fs...",
                            current_attempt,
                            total_attempts,
                            func.__name__,
                            str(e),
                            delay,
                            extra={
                                "function": func.__name__,
                                "attempt": current_attempt,
                                "total_attempts": total_attempts,
                                "delay_seconds": delay,
                                "error": str(e),
                                "error_type": type(e).__name__,
                            },
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            "All %d retry attempts exhausted for %s",
                            total_attempts,
                            func.__name__,
                            extra={
                                "function": func.__name__,
                                "total_attempts": total_attempts,
                                "final_error": str(e),
                                "error_type": type(e).__name__,
                            },
                        )

            # All retries exhausted
            raise RetryExhausted(
                f"Exhausted {retry_policy.max_retries} retries for {func.__name__}",
                attempts=total_attempts,
            ) from last_exception

        return wrapper

    return decorator
