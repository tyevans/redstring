"""Circuit breaker for Ollama service.

Implements the circuit breaker pattern to prevent cascading failures
when the Ollama service is unavailable or experiencing issues.

The circuit breaker has three states:
- CLOSED: Normal operation, requests are allowed. Failures are tracked.
- OPEN: Service is failing, requests are rejected immediately.
- HALF_OPEN: Testing recovery, limited requests are allowed.

State transitions:
- CLOSED -> OPEN: When failure count reaches threshold
- OPEN -> HALF_OPEN: When recovery timeout has passed
- HALF_OPEN -> CLOSED: When a request succeeds
- HALF_OPEN -> OPEN: When a request fails

Uses Redis for distributed state storage, allowing multiple workers
to share circuit breaker state.
"""

import logging
from datetime import UTC, datetime
from enum import Enum

import redis.asyncio as redis

from kg_builder.config import settings

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    """Circuit breaker states.

    Attributes:
        CLOSED: Normal operation, requests are allowed
        OPEN: Service failing, requests are rejected
        HALF_OPEN: Testing recovery, limited requests allowed
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpen(Exception):
    """Raised when the circuit breaker is open.

    This exception indicates that the circuit breaker has tripped
    and requests should not be made to the Ollama service until
    the recovery timeout has passed.

    Attributes:
        message: Human-readable error message
        retry_after: Estimated seconds until circuit may close
    """

    def __init__(self, message: str, retry_after: float = 0.0):
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


class OllamaCircuitBreaker:
    """Circuit breaker for Ollama service.

    Prevents cascading failures by stopping requests when
    Ollama is consistently failing. Uses Redis for distributed
    state storage to work across multiple workers.

    The circuit breaker follows this pattern:
    1. In CLOSED state, track failures. When threshold reached, open circuit.
    2. In OPEN state, reject all requests. After recovery_timeout, transition to HALF_OPEN.
    3. In HALF_OPEN state, allow limited requests. If successful, close circuit.
       If failure occurs, reopen circuit.

    Example:
        breaker = get_circuit_breaker()

        if await breaker.allow_request():
            try:
                result = await ollama_service.extract(...)
                await breaker.record_success()
            except Exception as e:
                await breaker.record_failure()
                raise
        else:
            raise CircuitOpen("Circuit breaker is open")
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        half_open_max_calls: int = 1,
        key_prefix: str = "ollama_circuit",
        redis_url: str | None = None,
    ):
        """Initialize the circuit breaker.

        Args:
            failure_threshold: Number of failures before opening circuit (default: 5)
            recovery_timeout: Seconds to wait before testing recovery (default: 60)
            half_open_max_calls: Max concurrent requests in half-open state (default: 1)
            key_prefix: Prefix for Redis keys (default: "ollama_circuit")
            redis_url: Redis connection URL (defaults to settings.REDIS_URL)
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self._key_prefix = key_prefix
        self._redis_url = redis_url or settings.REDIS_URL
        self._redis: redis.Redis | None = None

        logger.info(
            "OllamaCircuitBreaker initialized",
            extra={
                "failure_threshold": failure_threshold,
                "recovery_timeout": recovery_timeout,
                "half_open_max_calls": half_open_max_calls,
                "key_prefix": key_prefix,
            },
        )

    async def _get_redis(self) -> redis.Redis:
        """Get or create Redis connection.

        Returns:
            Redis client instance
        """
        if self._redis is None:
            self._redis = redis.from_url(self._redis_url)
        return self._redis

    def _state_key(self) -> str:
        """Get Redis key for circuit state."""
        return f"{self._key_prefix}:state"

    def _failures_key(self) -> str:
        """Get Redis key for failure count."""
        return f"{self._key_prefix}:failures"

    def _opened_at_key(self) -> str:
        """Get Redis key for opened_at timestamp."""
        return f"{self._key_prefix}:opened_at"

    def _half_open_calls_key(self) -> str:
        """Get Redis key for half-open call count."""
        return f"{self._key_prefix}:half_open_calls"

    async def get_state(self) -> CircuitState:
        """Get current circuit state.

        Returns:
            Current CircuitState (CLOSED if no state set in Redis)
        """
        r = await self._get_redis()
        state = await r.get(self._state_key())

        if state is None:
            return CircuitState.CLOSED

        # Handle both bytes and string responses
        state_value = state.decode() if isinstance(state, bytes) else state
        try:
            return CircuitState(state_value)
        except ValueError:
            logger.warning(
                "Invalid circuit state in Redis, defaulting to CLOSED",
                extra={"stored_state": state_value},
            )
            return CircuitState.CLOSED

    async def allow_request(self) -> bool:
        """Check if a request should be allowed.

        This method should be called before making an Ollama request.
        It handles automatic state transitions based on timeouts.

        Returns:
            True if request is allowed, False if circuit is open

        State behavior:
        - CLOSED: Always returns True
        - OPEN: Returns False unless recovery_timeout has passed,
                in which case transitions to HALF_OPEN and returns True
        - HALF_OPEN: Returns True if under half_open_max_calls limit
        """
        state = await self.get_state()

        if state == CircuitState.CLOSED:
            return True

        elif state == CircuitState.OPEN:
            # Check if recovery timeout has passed
            r = await self._get_redis()
            opened_at = await r.get(self._opened_at_key())

            if opened_at is not None:
                opened_at_value = opened_at.decode() if isinstance(opened_at, bytes) else opened_at
                elapsed = datetime.now(UTC).timestamp() - float(opened_at_value)

                if elapsed >= self.recovery_timeout:
                    await self._transition_to_half_open()
                    return True

            logger.debug(
                "Circuit is OPEN, rejecting request",
                extra={"state": state.value},
            )
            return False

        else:  # HALF_OPEN
            r = await self._get_redis()
            calls = await r.incr(self._half_open_calls_key())

            if calls <= self.half_open_max_calls:
                logger.debug(
                    "Circuit is HALF_OPEN, allowing limited request",
                    extra={"half_open_calls": calls, "max_calls": self.half_open_max_calls},
                )
                return True
            else:
                logger.debug(
                    "Circuit is HALF_OPEN but max calls reached, rejecting request",
                    extra={"half_open_calls": calls, "max_calls": self.half_open_max_calls},
                )
                return False

    async def record_success(self) -> None:
        """Record a successful request.

        Should be called after a successful Ollama request.

        State behavior:
        - CLOSED: Resets failure count
        - HALF_OPEN: Transitions to CLOSED (recovery confirmed)
        - OPEN: No effect (shouldn't happen in normal flow)
        """
        state = await self.get_state()

        if state == CircuitState.HALF_OPEN:
            logger.info(
                "Successful request in HALF_OPEN state, closing circuit",
                extra={"previous_state": state.value},
            )
            await self._transition_to_closed()

        elif state == CircuitState.CLOSED:
            # Reset failures on success in closed state
            await self._reset_failures()
            logger.debug("Reset failure count on success")

    async def record_failure(self) -> None:
        """Record a failed request.

        Should be called after a failed Ollama request.

        State behavior:
        - CLOSED: Increments failure count. Opens circuit if threshold reached.
        - HALF_OPEN: Transitions back to OPEN (recovery failed)
        - OPEN: No effect (shouldn't happen in normal flow)
        """
        state = await self.get_state()
        r = await self._get_redis()

        if state == CircuitState.HALF_OPEN:
            logger.warning(
                "Failed request in HALF_OPEN state, reopening circuit",
                extra={"previous_state": state.value},
            )
            await self._transition_to_open()

        elif state == CircuitState.CLOSED:
            failures = await r.incr(self._failures_key())
            logger.debug(
                "Recording failure",
                extra={"failure_count": failures, "threshold": self.failure_threshold},
            )

            if failures >= self.failure_threshold:
                logger.warning(
                    "Failure threshold reached, opening circuit",
                    extra={"failure_count": failures, "threshold": self.failure_threshold},
                )
                await self._transition_to_open()

    async def _transition_to_open(self) -> None:
        """Transition circuit to OPEN state.

        Sets the state to OPEN and records the timestamp for recovery timeout.
        """
        r = await self._get_redis()
        now = datetime.now(UTC).timestamp()

        async with r.pipeline(transaction=True) as pipe:
            await pipe.set(self._state_key(), CircuitState.OPEN.value)
            await pipe.set(self._opened_at_key(), str(now))
            await pipe.execute()

        logger.warning(
            "Circuit breaker OPENED",
            extra={"opened_at": now, "recovery_timeout": self.recovery_timeout},
        )

    async def _transition_to_half_open(self) -> None:
        """Transition circuit to HALF_OPEN state.

        Sets the state to HALF_OPEN and resets the half-open call counter.
        """
        r = await self._get_redis()

        async with r.pipeline(transaction=True) as pipe:
            await pipe.set(self._state_key(), CircuitState.HALF_OPEN.value)
            await pipe.set(self._half_open_calls_key(), "0")
            await pipe.execute()

        logger.info("Circuit breaker HALF-OPEN, allowing test requests")

    async def _transition_to_closed(self) -> None:
        """Transition circuit to CLOSED state.

        Sets the state to CLOSED and resets all counters.
        """
        r = await self._get_redis()

        async with r.pipeline(transaction=True) as pipe:
            await pipe.set(self._state_key(), CircuitState.CLOSED.value)
            await pipe.set(self._failures_key(), "0")
            await pipe.delete(self._opened_at_key())
            await pipe.delete(self._half_open_calls_key())
            await pipe.execute()

        logger.info("Circuit breaker CLOSED, normal operation resumed")

    async def _reset_failures(self) -> None:
        """Reset the failure counter.

        Called on successful requests in CLOSED state to prevent
        stale failures from accumulating.
        """
        r = await self._get_redis()
        await r.set(self._failures_key(), "0")

    async def get_retry_after(self) -> float:
        """Get estimated time until circuit may close.

        Returns:
            Seconds until recovery timeout expires, or 0 if not in OPEN state
        """
        state = await self.get_state()

        if state != CircuitState.OPEN:
            return 0.0

        r = await self._get_redis()
        opened_at = await r.get(self._opened_at_key())

        if opened_at is None:
            return 0.0

        opened_at_value = opened_at.decode() if isinstance(opened_at, bytes) else opened_at
        elapsed = datetime.now(UTC).timestamp() - float(opened_at_value)
        remaining = self.recovery_timeout - elapsed

        return max(0.0, remaining)

    async def reset(self) -> None:
        """Reset the circuit breaker to initial state.

        Clears all Redis keys and returns to CLOSED state.
        Primarily useful for testing.
        """
        r = await self._get_redis()

        async with r.pipeline(transaction=True) as pipe:
            await pipe.delete(self._state_key())
            await pipe.delete(self._failures_key())
            await pipe.delete(self._opened_at_key())
            await pipe.delete(self._half_open_calls_key())
            await pipe.execute()

        logger.info("Circuit breaker reset to initial state")

    async def close(self) -> None:
        """Close Redis connection.

        Should be called during application shutdown.
        """
        if self._redis is not None:
            await self._redis.close()
            self._redis = None
            logger.debug("Circuit breaker Redis connection closed")


# Global singleton instance
_circuit_breaker: OllamaCircuitBreaker | None = None


def get_circuit_breaker(
    failure_threshold: int | None = None,
    recovery_timeout: int | None = None,
    half_open_max_calls: int | None = None,
) -> OllamaCircuitBreaker:
    """Get the global circuit breaker instance.

    Creates a new instance on first call with the provided configuration,
    then returns the same instance on subsequent calls (singleton pattern).

    Args:
        failure_threshold: Number of failures before opening circuit (default: 5)
        recovery_timeout: Seconds to wait before testing recovery (default: 60)
        half_open_max_calls: Max concurrent requests in half-open state (default: 1)

    Returns:
        The global OllamaCircuitBreaker instance
    """
    global _circuit_breaker

    if _circuit_breaker is None:
        _circuit_breaker = OllamaCircuitBreaker(
            failure_threshold=failure_threshold or 5,
            recovery_timeout=recovery_timeout or 60,
            half_open_max_calls=half_open_max_calls or 1,
        )

    return _circuit_breaker


def reset_circuit_breaker() -> None:
    """Reset the global circuit breaker instance.

    Clears the singleton, causing the next call to get_circuit_breaker()
    to create a fresh instance. Primarily useful for testing.

    Note: This does NOT close the Redis connection. Call close()
    on the instance first if needed.
    """
    global _circuit_breaker
    _circuit_breaker = None
