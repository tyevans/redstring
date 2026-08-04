"""The `Cache` port: shared, expiring state for the LLM transport.

Small on purpose. This is not a general-purpose cache abstraction -- it is
exactly what a rate limiter and a circuit breaker need in order to coordinate
several processes without either of them naming Redis.

## Why the library must run without it

`kg_builder` is a library. A caller who wants extraction and has no Redis must
still get extraction, so `kg_builder.llm.cache.memory.MemoryCache` is the
**default** rather than an option -- one process, no coordination, correct
behaviour for the single-process case that is most of them. Redis is the
upgrade a caller chooses when several processes must agree, not the price of
entry.

## Two capabilities, not one

The key/value half (`get`, `set`, `increment`, `delete`) is what a circuit
breaker needs: a state name, a failure count, a timestamp.

The **hit-window** half (`record_hit`, `count_hits`, `oldest_hit`) is what a
*sliding-window* rate limiter needs, and it is deliberately expressed as
"events in a time window" rather than as the sorted-set operations Redis would
offer. A port that said `zadd`/`zcard`/`zremrangebyscore` would be a Redis
port wearing a different name, and no in-memory implementation could satisfy
it without reimplementing Redis.

The alternative was a fixed-window counter, which `increment` alone would
support. Rejected: it lets twice the limit through across a window boundary,
and the whole reason for rate-limiting a local model is that twice the limit
is what knocks it over.

## Time is passed in, never read

Every window method takes `at`/`since` as a caller-supplied epoch float. An
adapter that called `time.time()` itself would put the clock inside the thing
under test, so every window test would have to sleep -- and a Redis adapter
would read a *different* clock from the caller's, which is a real bug on a
cluster with drift, not merely an awkward test.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Cache(Protocol):
    """Expiring shared state, in the terms the LLM transport needs."""

    async def get(self, key: str) -> str | None:
        """The value at `key`, or None if absent or expired.

        `str` rather than `bytes`: the port defines the type so adapters
        cannot disagree about it. A Redis adapter that returned `bytes` while
        the in-memory one returned `str` would make every comparison in a
        caller work in tests and fail in production.
        """
        ...

    async def set(self, key: str, value: str, *, ttl_seconds: float | None = None) -> None:
        """Store `value` at `key`, expiring after `ttl_seconds` if given."""
        ...

    async def increment(self, key: str, *, ttl_seconds: float | None = None) -> int:
        """Add one to the counter at `key` and return the new value.

        A missing key counts as zero, so the first call returns 1. `ttl_seconds`
        is applied when the key is *created*, not on every increment -- a TTL
        refreshed on each hit is a counter that never expires under load,
        which is exactly when it needs to.
        """
        ...

    async def delete(self, key: str) -> None:
        """Remove `key`. Absent keys are not an error."""
        ...

    async def record_hit(self, key: str, *, at: float, ttl_seconds: float) -> None:
        """Record one event against `key` at epoch time `at`.

        `ttl_seconds` bounds how long the whole series is kept, so a key
        nobody asks about again does not leak. It must exceed the widest
        window the caller will ask for, or hits will vanish before they age
        out of it.
        """
        ...

    async def count_hits(self, key: str, *, since: float) -> int:
        """How many events were recorded against `key` at or after `since`.

        Implementations may discard older events as a side effect. Callers
        must not depend on them surviving.
        """
        ...

    async def oldest_hit(self, key: str, *, since: float) -> float | None:
        """When the earliest event at or after `since` happened, or None.

        This is what turns "you are over the limit" into "try again in 0.4
        seconds": the oldest hit in the window is the one whose expiry frees a
        slot.
        """
        ...

    async def close(self) -> None:
        """Release whatever the adapter holds. Safe to call twice."""
        ...
