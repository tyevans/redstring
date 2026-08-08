"""A circuit breaker for model calls, over the `Cache` port.

Rewritten in slice 6 from `extraction/circuit_breaker.py`, which spoke Redis
directly. The state machine is unchanged; what changed is that the state now
lives behind `Cache`, so the default is `MemoryCache` and the breaker works
with no infrastructure. Pass a `RedisCache` when several workers must trip
together -- otherwise each holds its own opinion, and a failing model is
discovered separately by every one of them.

`OllamaCircuitBreaker` is gone with the vendor name, for the reason slice 3
gave when removing vendors from `ExtractionMethod`: nothing about this is
Ollama-specific, and a vendor name outlives the vendor's presence in the code.

## The states, and the one that earns the pattern

- `CLOSED`  -- normal. Failures are counted; a success resets the count.
- `OPEN`    -- rejecting immediately, without calling the model at all.
- `HALF_OPEN` -- letting a *strictly limited* number of calls through to find
  out whether the model has recovered.

`HALF_OPEN` is the reason to use a breaker rather than a timeout. Going
straight from `OPEN` back to `CLOSED` sends the full load at a service that
has just come back, which knocks it over again -- so recovery is probed by at
most `half_open_max_calls` requests, and the rest keep being rejected until
one of them answers.

## Failure counting decays, deliberately

The failure counter carries a TTL of `recovery_timeout`, and `Cache.increment`
does **not** refresh a TTL on later increments. Without that decay, five
failures spread over an hour would open the circuit exactly as five failures
in a second do -- and the first is a healthy service having a bad day while
the second is an outage.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Self

from redstring.domain.exceptions import RedstringError
from redstring.llm.cache.memory import MemoryCache

if TYPE_CHECKING:
    from types import TracebackType

    from redstring.ports.cache import KeyValueCache

logger = logging.getLogger(__name__)


class CircuitState(StrEnum):
    """Where the breaker currently is. See the module docstring."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpen(RedstringError):
    """The circuit is open, so the call was refused without being made.

    `retry_after` is the wait until the recovery timeout elapses. It is an
    estimate: another worker may probe first and either close the circuit
    early or push the timeout out by failing.
    """

    def __init__(self, message: str, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


class CircuitBreaker:
    """Refuses calls to a model that has been failing."""

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 1,
        cache: KeyValueCache | None = None,
        key_prefix: str = "kg:circuit",
    ) -> None:
        """Build a breaker.

        Args:
            failure_threshold: Consecutive-ish failures that open the circuit.
                "Ish" because the count decays: see the module docstring.
            recovery_timeout: Seconds before an open circuit will probe.
            half_open_max_calls: Probes allowed at once while half-open.
                One by default -- the point of the state is to send *less*
                than normal load at a service that has just returned.
            cache: Where the state lives. `MemoryCache` when None, which is
                correct for one process.
            key_prefix: Namespace for the cache keys. Give two breakers
                different prefixes, or they are one breaker.

        Raises:
            ValueError: A non-positive threshold, timeout or probe count.
        """
        if failure_threshold <= 0:
            raise ValueError(f"failure_threshold must be positive, got {failure_threshold}")
        if recovery_timeout <= 0:
            raise ValueError(f"recovery_timeout must be positive, got {recovery_timeout}")
        if half_open_max_calls <= 0:
            raise ValueError(f"half_open_max_calls must be positive, got {half_open_max_calls}")
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        # Ownership is decided here and nowhere else. A breaker handed a cache
        # did not create it and must not close it: passing one `RedisCache` to
        # a breaker *and* a limiter is the documented way to make several
        # processes agree, and an unconditional `close()` then kills the other
        # component's state store from under it. Same reasoning, and same
        # spelling, as `RedisCache.owns_client`.
        self._owns_cache = cache is None
        self._cache: KeyValueCache = cache if cache is not None else MemoryCache()
        self._key_prefix = key_prefix

    def _key(self, name: str) -> str:
        return f"{self._key_prefix}:{name}"

    async def state(self) -> CircuitState:
        """The stored state, or `CLOSED` if there is none.

        An unrecognised stored value also reads as `CLOSED`, with a warning.
        The alternative is raising, which would turn one corrupt cache entry
        into a total outage -- the opposite of what a breaker is for.
        """
        stored = await self._cache.get(self._key("state"))
        if stored is None:
            return CircuitState.CLOSED
        try:
            return CircuitState(stored)
        except ValueError:
            logger.warning(
                "Unrecognised circuit state; treating as CLOSED", extra={"stored_state": stored}
            )
            return CircuitState.CLOSED

    async def allow_request(self) -> bool:
        """Whether to make the call. Handles the timeout-driven transition."""
        state = await self.state()

        if state is CircuitState.CLOSED:
            return True

        if state is CircuitState.OPEN:
            if not await self._recovery_is_due():
                return False
            await self._to_half_open()
            # Falls through to the half-open branch rather than returning
            # True here. Returning directly would admit this call *without*
            # counting it as a probe, so `half_open_max_calls=2` would let
            # three requests through -- the transitioning one plus two
            # counted ones. Found by
            # `test_only_the_permitted_number_of_probes_get_through`, and
            # inherited from the Redis implementation this replaced.

        # HALF_OPEN: admit up to the probe limit, refuse the rest.
        probes = await self._cache.increment(self._key("half_open_calls"))
        return probes <= self.half_open_max_calls

    async def _recovery_is_due(self) -> bool:
        """True once `recovery_timeout` has passed since the circuit opened.

        A missing `opened_at` counts as due. It means the state entry
        outlived its timestamp, and the safe reading is to probe: staying
        open forever on a lost key is an outage nothing would recover from.
        """
        opened_at = await self._cache.get(self._key("opened_at"))
        if opened_at is None:
            return True
        return datetime.now(UTC).timestamp() - float(opened_at) >= self.recovery_timeout

    async def retry_after(self) -> float:
        """Estimated seconds until the circuit will probe. Never negative."""
        opened_at = await self._cache.get(self._key("opened_at"))
        if opened_at is None:
            return 0.0
        elapsed = datetime.now(UTC).timestamp() - float(opened_at)
        return max(0.0, self.recovery_timeout - elapsed)

    async def record_success(self) -> None:
        """Report that a call succeeded."""
        state = await self.state()
        if state is CircuitState.HALF_OPEN:
            logger.info("Probe succeeded; closing the circuit")
            await self._to_closed()
        elif state is CircuitState.CLOSED:
            await self._cache.delete(self._key("failures"))

    async def record_failure(self) -> None:
        """Report that a call failed."""
        state = await self.state()

        if state is CircuitState.HALF_OPEN:
            logger.warning("Probe failed; reopening the circuit")
            await self._to_open()
            return

        if state is CircuitState.CLOSED:
            failures = await self._cache.increment(
                self._key("failures"), ttl_seconds=self.recovery_timeout
            )
            if failures >= self.failure_threshold:
                logger.warning(
                    "Failure threshold reached; opening the circuit",
                    extra={"failures": failures, "threshold": self.failure_threshold},
                )
                await self._to_open()

    async def _to_open(self) -> None:
        now = datetime.now(UTC).timestamp()
        await self._cache.set(self._key("state"), CircuitState.OPEN.value)
        await self._cache.set(self._key("opened_at"), str(now))
        await self._cache.delete(self._key("half_open_calls"))

    async def _to_half_open(self) -> None:
        """Enter the probing state with a fresh probe count.

        The count is cleared *before* the state changes. The other order
        leaves a window in which the circuit is half-open with the previous
        attempt's exhausted counter, so every probe is refused and the
        circuit never recovers.
        """
        await self._cache.delete(self._key("half_open_calls"))
        await self._cache.set(self._key("state"), CircuitState.HALF_OPEN.value)

    async def _to_closed(self) -> None:
        await self._cache.set(self._key("state"), CircuitState.CLOSED.value)
        await self._cache.delete(self._key("failures"))
        await self._cache.delete(self._key("opened_at"))
        await self._cache.delete(self._key("half_open_calls"))

    async def reset(self) -> None:
        """Forget everything and start closed. For operators and for tests."""
        await self._to_closed()

    async def close(self) -> None:
        """Release the cache, if this breaker created it.

        A cache passed in by the caller is left open. The caller built it,
        knows what else holds it, and is the only party who can say when it
        is finished with -- which is exactly the argument
        `RedisCache.owns_client` makes one layer down.
        """
        if self._owns_cache:
            await self._cache.close()

    async def __aenter__(self) -> Self:
        """Enter a block whose exit closes this breaker. See `__aexit__`."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close on the way out, and **never suppress**.

        The `None` return is the decision, not an omission: `__aexit__` is
        read for truthiness, so any truthy value would swallow whatever the
        body raised -- including `CancelledError`, which would break task
        cancellation for the caller. `None` is falsy, so the exception
        propagates and this is a resource-release block rather than an
        exception handler.

        Closing goes through `close()`, so ownership still decides: a breaker
        given a cache leaves it open here exactly as it does there.
        """
        await self.close()
