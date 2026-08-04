"""What every `Cache` adapter must do, whatever is behind it.

Subclass from an adapter's own test module and supply a `cache` fixture.
Nothing here is collected directly -- the module name does not match
`test_*.py`.

The suite exists for one reason, learned twice in this project already: an
in-memory reference that is *more forgiving* than the real backend lets a
caller pass its tests on behaviour production does not have. Everything below
is therefore stated in port terms and asserted identically for both adapters,
including the awkward cases -- `increment` on a key that `set` wrote,
`ttl_seconds` on an increment that is not the first, a hit window with two
events at the same instant.

Time is supplied by the test rather than slept through. Windows take
caller-supplied epoch floats precisely so that "an event 90 seconds ago" is a
number, not a 90-second test.
"""

from __future__ import annotations

import pytest

NOW = 1_700_000_000.0


class CacheCompliance:
    """Behaviour every `Cache` adapter must exhibit."""

    class TestKeyValue:
        async def test_a_missing_key_is_none_not_an_error(self, cache):
            assert await cache.get("nothing here") is None

        async def test_what_was_set_comes_back(self, cache):
            await cache.set("state", "open")

            assert await cache.get("state") == "open"

        async def test_a_value_comes_back_as_str_not_bytes(self, cache):
            """The port says `str`, and the two adapters disagree by default.

            A Redis client left at its defaults returns `bytes`, so a caller
            comparing against a string literal would match in every
            `MemoryCache` test and never match in production.
            """
            await cache.set("state", "open")

            assert isinstance(await cache.get("state"), str)

        async def test_setting_again_replaces(self, cache):
            await cache.set("state", "open")
            await cache.set("state", "closed")

            assert await cache.get("state") == "closed"

        async def test_delete_removes_the_key(self, cache):
            await cache.set("state", "open")
            await cache.delete("state")

            assert await cache.get("state") is None

        async def test_deleting_a_key_that_is_not_there_is_not_an_error(self, cache):
            await cache.delete("never existed")

    class TestCounters:
        async def test_the_first_increment_returns_one(self, cache):
            """A missing counter is zero, so the first hit is 1, not None or 0."""
            assert await cache.increment("failures") == 1

        async def test_increments_accumulate(self, cache):
            for expected in (1, 2, 3):
                assert await cache.increment("failures") == expected

        async def test_a_counter_reads_back_as_its_decimal_string(self, cache):
            """`get` and `increment` share a key space, so they must agree.

            A circuit breaker increments the failure count and elsewhere reads
            it; an adapter storing a counter in some private encoding would
            make that read return nonsense.
            """
            await cache.increment("failures")
            await cache.increment("failures")

            assert await cache.get("failures") == "2"

        async def test_a_deleted_counter_starts_again_from_one(self, cache):
            await cache.increment("failures")
            await cache.delete("failures")

            assert await cache.increment("failures") == 1

        async def test_a_counter_set_directly_continues_from_there(self, cache):
            """`set` then `increment` is how a breaker resets to a known count."""
            await cache.set("failures", "4")

            assert await cache.increment("failures") == 5

    class TestHitWindows:
        async def test_an_untouched_window_is_empty(self, cache):
            assert await cache.count_hits("tenant", since=NOW - 60) == 0
            assert await cache.oldest_hit("tenant", since=NOW - 60) is None

        async def test_hits_inside_the_window_are_counted(self, cache):
            for offset in (0, 1, 2):
                await cache.record_hit("tenant", at=NOW + offset, ttl_seconds=300)

            assert await cache.count_hits("tenant", since=NOW - 60) == 3

        async def test_hits_before_the_window_are_not_counted(self, cache):
            """The whole point of a *sliding* window, and the fixed-window bug.

            A fixed-window counter cannot express this: it would keep counting
            the old hit until its bucket rolled over, letting twice the limit
            through at the boundary.
            """
            await cache.record_hit("tenant", at=NOW - 600, ttl_seconds=3600)
            await cache.record_hit("tenant", at=NOW, ttl_seconds=3600)

            assert await cache.count_hits("tenant", since=NOW - 60) == 1

        async def test_a_hit_exactly_at_the_boundary_is_inside_the_window(self, cache):
            """Stated because the two natural implementations disagree here.

            Redis `ZCOUNT min max` is inclusive; a naive `>` comparison is
            not. One of them counts the boundary hit and one does not, and the
            difference shows up only as a rate limiter that is off by one
            under sustained exact-rate load.
            """
            await cache.record_hit("tenant", at=NOW - 60, ttl_seconds=3600)

            assert await cache.count_hits("tenant", since=NOW - 60) == 1

        async def test_two_hits_at_the_very_same_instant_are_two_hits(self, cache):
            """Collapsing them under-counts exactly when a burst is the thing to catch.

            The obvious sorted-set encoding keys on the timestamp, which makes
            the second write an update rather than an insert.
            """
            await cache.record_hit("tenant", at=NOW, ttl_seconds=300)
            await cache.record_hit("tenant", at=NOW, ttl_seconds=300)

            assert await cache.count_hits("tenant", since=NOW - 60) == 2

        async def test_the_oldest_hit_in_the_window_is_reported(self, cache):
            """It is the one whose expiry frees the next slot.

            Without it a rate limiter can say "no" but not "try again in 0.4
            seconds", and every caller busy-waits.
            """
            for offset in (5, 1, 3):
                await cache.record_hit("tenant", at=NOW + offset, ttl_seconds=300)

            assert await cache.oldest_hit("tenant", since=NOW) == pytest.approx(NOW + 1)

        async def test_the_oldest_hit_ignores_hits_that_have_aged_out(self, cache):
            await cache.record_hit("tenant", at=NOW - 600, ttl_seconds=3600)
            await cache.record_hit("tenant", at=NOW, ttl_seconds=3600)

            assert await cache.oldest_hit("tenant", since=NOW - 60) == pytest.approx(NOW)

        async def test_windows_are_kept_apart_by_key(self, cache):
            await cache.record_hit("tenant-a", at=NOW, ttl_seconds=300)

            assert await cache.count_hits("tenant-b", since=NOW - 60) == 0

        async def test_a_window_and_a_value_can_share_a_key_without_colliding(self, cache):
            """They do not, in either adapter, and it must stay that way.

            Redis would reject a `ZADD` onto a string key with WRONGTYPE,
            which `MemoryCache` cannot reproduce -- so the two would diverge
            on an error path nothing tests.
            """
            await cache.set("tenant", "some value")
            await cache.record_hit("tenant", at=NOW, ttl_seconds=300)

            assert await cache.get("tenant") == "some value"
            assert await cache.count_hits("tenant", since=NOW - 60) == 1

        async def test_deleting_a_key_clears_its_window_too(self, cache):
            await cache.record_hit("tenant", at=NOW, ttl_seconds=300)
            await cache.delete("tenant")

            assert await cache.count_hits("tenant", since=NOW - 60) == 0

    class TestExpiry:
        async def test_a_value_with_a_ttl_is_gone_once_it_elapses(self, cache):
            """Sub-second, so the suite stays fast and the assertion stays real.

            A `ttl_seconds` below one second is also the case a Redis adapter
            using `EX` silently turns into *no* expiry at all, by truncating
            to zero whole seconds.
            """
            import asyncio

            await cache.set("brief", "value", ttl_seconds=0.05)
            await asyncio.sleep(0.15)

            assert await cache.get("brief") is None

        async def test_a_value_without_a_ttl_survives(self, cache):
            import asyncio

            await cache.set("permanent", "value")
            await asyncio.sleep(0.15)

            assert await cache.get("permanent") == "value"

        async def test_a_counters_ttl_is_not_refreshed_by_later_increments(self, cache):
            """A counter under load must still expire, or it never decays.

            `EXPIRE` on every increment gives a failure count that only ever
            grows, so a circuit breaker eventually opens on failures that are
            minutes apart -- which reads as flapping infrastructure rather
            than as a bug in the breaker.
            """
            import asyncio

            await cache.increment("failures", ttl_seconds=0.12)
            await asyncio.sleep(0.06)
            await cache.increment("failures", ttl_seconds=0.12)
            await asyncio.sleep(0.10)

            assert await cache.get("failures") is None

    class TestLifecycle:
        async def test_close_is_safe_to_call_twice(self, cache):
            await cache.close()
            await cache.close()
