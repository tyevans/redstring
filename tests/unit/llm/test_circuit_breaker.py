"""The circuit breaker's state machine, over a real `MemoryCache`.

Rewritten in slice 6 for the reason the rate limiter's suite was: the previous
one installed a fake into `sys.modules["redis.asyncio"]` and asserted against
its internal dict, so a large part of it described how that fake stored bytes
rather than what the breaker does. Those encoding tests are gone -- the `Cache`
port defines the type, and the compliance suite asserts it for every adapter.

What is kept and strengthened is every state transition, and above all the
half-open behaviour, which is the only part that is hard to get right.
"""

from __future__ import annotations

import asyncio

import pytest

from kg_builder.llm.cache.memory import MemoryCache
from kg_builder.llm.circuit_breaker import CircuitBreaker, CircuitOpen, CircuitState


async def open_the_circuit(breaker: CircuitBreaker) -> None:
    for _ in range(breaker.failure_threshold):
        await breaker.record_failure()


class TestConfiguration:
    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"failure_threshold": 0}, "failure_threshold"),
            ({"recovery_timeout": 0}, "recovery_timeout"),
            ({"half_open_max_calls": 0}, "half_open_max_calls"),
        ],
    )
    def test_non_positive_settings_are_refused(self, kwargs, message):
        with pytest.raises(ValueError, match=message):
            CircuitBreaker(**kwargs)

    def test_it_needs_no_cache_argument(self):
        assert CircuitBreaker() is not None


class TestClosed:
    async def test_a_new_breaker_is_closed(self):
        assert await CircuitBreaker().state() is CircuitState.CLOSED

    async def test_a_closed_breaker_allows_requests(self):
        assert await CircuitBreaker().allow_request() is True

    async def test_failures_below_the_threshold_leave_it_closed(self):
        breaker = CircuitBreaker(failure_threshold=3)

        await breaker.record_failure()
        await breaker.record_failure()

        assert await breaker.state() is CircuitState.CLOSED

    async def test_reaching_the_threshold_opens_it(self):
        breaker = CircuitBreaker(failure_threshold=3)

        await open_the_circuit(breaker)

        assert await breaker.state() is CircuitState.OPEN

    async def test_a_success_clears_the_accumulated_failures(self):
        """Otherwise the threshold counts failures over all time.

        Two failures now plus two failures an hour from now would open a
        circuit in front of a service that was working the whole time.
        """
        breaker = CircuitBreaker(failure_threshold=3)
        await breaker.record_failure()
        await breaker.record_failure()

        await breaker.record_success()
        await breaker.record_failure()
        await breaker.record_failure()

        assert await breaker.state() is CircuitState.CLOSED


class TestOpen:
    async def test_an_open_breaker_refuses_requests(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=60)
        await open_the_circuit(breaker)

        assert await breaker.allow_request() is False

    async def test_it_reports_how_long_until_it_will_probe(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=60)
        await open_the_circuit(breaker)

        assert 0 < await breaker.retry_after() <= 60

    async def test_after_the_recovery_timeout_it_probes(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0.05)
        await open_the_circuit(breaker)
        assert await breaker.allow_request() is False

        await asyncio.sleep(0.1)

        assert await breaker.allow_request() is True
        assert await breaker.state() is CircuitState.HALF_OPEN

    async def test_a_lost_opened_at_probes_rather_than_staying_open_forever(self):
        """The safe reading of a half-missing state.

        The state entry has no TTL and the timestamp could be evicted
        independently. Staying open on a lost key is an outage nothing would
        ever recover from, so the breaker probes instead.
        """
        cache = MemoryCache()
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=3600, cache=cache)
        await open_the_circuit(breaker)
        await cache.delete("kg:circuit:opened_at")

        assert await breaker.allow_request() is True


class TestHalfOpen:
    async def test_a_probe_that_succeeds_closes_the_circuit(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0.05)
        await open_the_circuit(breaker)
        await asyncio.sleep(0.1)
        await breaker.allow_request()

        await breaker.record_success()

        assert await breaker.state() is CircuitState.CLOSED

    async def test_a_probe_that_fails_reopens_the_circuit(self):
        breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=0.05)
        await open_the_circuit(breaker)
        await asyncio.sleep(0.1)
        await breaker.allow_request()

        await breaker.record_failure()

        assert await breaker.state() is CircuitState.OPEN

    async def test_one_failed_probe_reopens_without_needing_the_threshold_again(self):
        """The distinction the state exists for.

        Counting probe failures toward the threshold would send
        `failure_threshold` requests at a service that has just told us it is
        still down -- which is the load the breaker was opened to stop.
        """
        breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=0.05)
        await open_the_circuit(breaker)
        await asyncio.sleep(0.1)
        await breaker.allow_request()

        await breaker.record_failure()

        assert await breaker.allow_request() is False

    async def test_only_the_permitted_number_of_probes_get_through(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0.05, half_open_max_calls=2)
        await open_the_circuit(breaker)
        await asyncio.sleep(0.1)

        assert [await breaker.allow_request() for _ in range(4)] == [True, True, False, False]

    async def test_reopening_and_probing_again_gets_a_fresh_probe_allowance(self):
        """The ordering bug this is here to catch, which is silent and fatal.

        If the probe counter is cleared *after* the state becomes half-open
        rather than before, the second recovery attempt starts with the first
        attempt's exhausted counter -- every probe is refused, and the circuit
        never closes again however healthy the service becomes.
        """
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0.05, half_open_max_calls=1)
        await open_the_circuit(breaker)
        await asyncio.sleep(0.1)
        assert await breaker.allow_request() is True
        await breaker.record_failure()

        await asyncio.sleep(0.1)

        assert await breaker.allow_request() is True


class TestCorruptState:
    async def test_an_unrecognised_stored_state_reads_as_closed(self):
        """Raising would turn one bad cache entry into a total outage.

        Which is the opposite of the pattern's purpose, so the breaker fails
        toward letting traffic through and warns.
        """
        cache = MemoryCache()
        await cache.set("kg:circuit:state", "banana")

        assert await CircuitBreaker(cache=cache).state() is CircuitState.CLOSED


class TestSharing:
    async def test_two_breakers_on_one_cache_trip_together(self):
        """What a shared cache buys, shown without needing Redis.

        Otherwise each worker discovers the outage separately, and a
        twenty-worker deployment sends twenty times the threshold at a
        service that is already down.
        """
        cache = MemoryCache()
        first = CircuitBreaker(failure_threshold=1, recovery_timeout=60, cache=cache)
        second = CircuitBreaker(failure_threshold=1, recovery_timeout=60, cache=cache)

        await first.record_failure()

        assert await second.allow_request() is False

    async def test_two_breakers_on_separate_caches_do_not(self):
        """The other half, so the test above is not vacuously true."""
        first = CircuitBreaker(failure_threshold=1, recovery_timeout=60)
        second = CircuitBreaker(failure_threshold=1, recovery_timeout=60)

        await first.record_failure()

        assert await second.allow_request() is True

    async def test_different_prefixes_are_different_circuits_on_one_cache(self):
        cache = MemoryCache()
        extraction = CircuitBreaker(failure_threshold=1, cache=cache, key_prefix="kg:extract")
        embedding = CircuitBreaker(failure_threshold=1, cache=cache, key_prefix="kg:embed")

        await extraction.record_failure()

        assert await embedding.allow_request() is True


class TestReset:
    async def test_reset_closes_an_open_circuit(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=3600)
        await open_the_circuit(breaker)

        await breaker.reset()

        assert await breaker.state() is CircuitState.CLOSED
        assert await breaker.allow_request() is True

    async def test_reset_also_clears_the_failure_count(self):
        """Otherwise a reset circuit reopens on the next single failure."""
        breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=3600)
        await breaker.record_failure()
        await breaker.record_failure()

        await breaker.reset()
        await breaker.record_failure()

        assert await breaker.state() is CircuitState.CLOSED


def test_circuit_open_carries_a_retry_hint():
    error = CircuitOpen("down", retry_after=12.5)

    assert error.retry_after == 12.5
