"""The sliding-window rate limiter, over a real `MemoryCache`.

Rewritten in slice 6. The previous suite installed a hand-rolled fake into
`sys.modules["redis.asyncio"]` and asserted against its internal `.data` dict,
so it tested the fake's idea of Redis rather than the limiter -- and it could
not run at all without that patching. `MemoryCache` is a real implementation
that passes the shared `Cache` compliance suite, so these tests exercise the
limiter against something a caller can actually use.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from kg_builder.llm.cache.memory import MemoryCache
from kg_builder.llm.rate_limiter import RateLimiter, RateLimitExceeded


@pytest.fixture
def tenant():
    return uuid4()


@pytest.fixture
def other_tenant():
    return uuid4()


class TestConfiguration:
    def test_a_zero_allowance_is_refused(self):
        """`rpm=0` refuses every call forever, which nobody means to ask for."""
        with pytest.raises(ValueError, match="rpm must be positive"):
            RateLimiter(rpm=0)

    def test_a_negative_allowance_is_refused(self):
        with pytest.raises(ValueError, match="rpm must be positive"):
            RateLimiter(rpm=-1)

    def test_a_non_positive_window_is_refused(self):
        with pytest.raises(ValueError, match="window_seconds must be positive"):
            RateLimiter(rpm=10, window_seconds=0)

    def test_it_needs_no_cache_argument(self, tenant):
        """The claim that the library rate-limits with no infrastructure."""
        assert RateLimiter(rpm=10) is not None


class TestLimiting:
    async def test_calls_within_the_allowance_are_permitted(self, tenant):
        limiter = RateLimiter(rpm=3)

        for _ in range(3):
            await limiter.acquire(tenant)

    async def test_the_call_past_the_allowance_is_refused(self, tenant):
        limiter = RateLimiter(rpm=2)
        await limiter.acquire(tenant)
        await limiter.acquire(tenant)

        with pytest.raises(RateLimitExceeded):
            await limiter.acquire(tenant)

    async def test_a_refusal_says_how_long_to_wait(self, tenant):
        """Without it every caller busy-waits, which is worse than no limiter."""
        limiter = RateLimiter(rpm=1, window_seconds=60)
        await limiter.acquire(tenant)

        with pytest.raises(RateLimitExceeded) as caught:
            await limiter.acquire(tenant)

        assert 0 < caught.value.retry_after <= 60

    async def test_a_refused_call_is_not_itself_counted(self, tenant):
        """Otherwise a client in a retry loop locks itself out indefinitely.

        Each rejected retry would extend the window it is waiting on, so the
        limiter would never let it back in -- the failure mode is a caller
        that is permanently blocked while making no successful calls at all.
        """
        limiter = RateLimiter(rpm=1, window_seconds=60)
        await limiter.acquire(tenant)

        first_wait = None
        for _ in range(5):
            with pytest.raises(RateLimitExceeded) as caught:
                await limiter.acquire(tenant)
            if first_wait is None:
                first_wait = caught.value.retry_after

        assert caught.value.retry_after <= first_wait

    async def test_tenants_have_separate_allowances(self, tenant, other_tenant):
        """The point of it being per-tenant, and easy to get wrong with one key."""
        limiter = RateLimiter(rpm=1)
        await limiter.acquire(tenant)

        await limiter.acquire(other_tenant)

    async def test_exhausting_one_tenant_does_not_refuse_another(self, tenant, other_tenant):
        limiter = RateLimiter(rpm=1)
        await limiter.acquire(tenant)
        with pytest.raises(RateLimitExceeded):
            await limiter.acquire(tenant)

        await limiter.acquire(other_tenant)

    async def test_a_hit_that_has_aged_out_of_the_window_frees_a_slot(self, tenant):
        """The sliding half of "sliding window".

        The window is 0.15s here rather than 60, so the test observes the
        real clock without a long sleep -- and a fixed-window counter would
        fail this only at a bucket boundary, which is why the compliance
        suite pins the window semantics rather than this test.
        """
        import asyncio

        limiter = RateLimiter(rpm=1, window_seconds=0.15)
        await limiter.acquire(tenant)
        with pytest.raises(RateLimitExceeded):
            await limiter.acquire(tenant)

        await asyncio.sleep(0.2)

        await limiter.acquire(tenant)


class TestRemaining:
    async def test_a_fresh_tenant_has_its_whole_allowance(self, tenant):
        assert await RateLimiter(rpm=5).remaining(tenant) == 5

    async def test_each_call_uses_one(self, tenant):
        limiter = RateLimiter(rpm=5)

        await limiter.acquire(tenant)
        await limiter.acquire(tenant)

        assert await limiter.remaining(tenant) == 3

    async def test_it_never_reports_a_negative_allowance(self, tenant):
        """A negative here would read as an allowance and be added to.

        Reachable when several workers share one cache and race past the
        limit together, which is exactly the case a shared cache exists for.
        """
        cache = MemoryCache()
        limiter = RateLimiter(rpm=1, cache=cache)
        for offset in range(5):
            from datetime import UTC, datetime

            await cache.record_hit(
                f"kg:ratelimit:{tenant}",
                at=datetime.now(UTC).timestamp() - offset * 0.001,
                ttl_seconds=120,
            )

        assert await limiter.remaining(tenant) == 0


class TestSharing:
    async def test_two_limiters_on_one_cache_share_an_allowance(self, tenant):
        """What a `RedisCache` buys, demonstrated without needing Redis.

        Two limiters on separate caches would each grant the full allowance,
        so a four-worker deployment would permit four times the configured
        rate -- silently, and only in production.
        """
        cache = MemoryCache()
        first = RateLimiter(rpm=1, cache=cache)
        second = RateLimiter(rpm=1, cache=cache)

        await first.acquire(tenant)

        with pytest.raises(RateLimitExceeded):
            await second.acquire(tenant)

    async def test_two_limiters_on_separate_caches_do_not(self, tenant):
        """The other half of the claim, so the test above is not vacuous."""
        await RateLimiter(rpm=1).acquire(tenant)

        await RateLimiter(rpm=1).acquire(tenant)

    async def test_a_key_prefix_separates_two_limiters_on_one_cache(self, tenant):
        cache = MemoryCache()
        extraction = RateLimiter(rpm=1, cache=cache, key_prefix="kg:extract")
        embedding = RateLimiter(rpm=1, cache=cache, key_prefix="kg:embed")

        await extraction.acquire(tenant)

        await embedding.acquire(tenant)


async def test_closing_releases_the_cache(tenant):
    limiter = RateLimiter(rpm=1)

    await limiter.close()
