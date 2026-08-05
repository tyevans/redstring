"""`RedisCache.get` returns `str` whatever the client hands it.

The port says `str`. `RedisCache.from_url` builds a client with
`decode_responses=True` so that client returns `str` already -- but
`__init__` accepts a client the caller built, with whatever settings the
caller chose, and that is a documented entry point ("or with a client you
own").

Left to the client, `get` returned `b"open"` through one door and `"open"`
through the other. That is not two behaviours of one adapter, it is two
adapters, and it is exactly the divergence `tests/compliance/cache.py` exists
to catch -- reached through the constructor the compliance suite does not
use.

These are unit tests, with no Redis, because the decoding is this class's
own logic rather than the server's. The integration suite covers the same
guarantee against a real client in
`tests/integration/llm/test_redis_cache.py`; this is the half that can run on
the commit gate.

The fake below is deliberately **less** forgiving than production, not more:
it returns `bytes`, which is what a default-configured redis client does. A
fake that returned `str` would be the "in-memory reference more forgiving
than the real backend" trap the compliance directory was built to prevent.
"""

from __future__ import annotations

from typing import Any

import pytest

from redstring.llm.cache.redis import RedisCache


class _ClientReturning:
    """The narrowest possible stand-in: `get` answers, nothing else works."""

    def __init__(self, value: Any) -> None:
        self._value = value

    async def get(self, key: str) -> Any:
        return self._value


def _cache(value: Any) -> RedisCache:
    return RedisCache(_ClientReturning(value))  # type: ignore[arg-type]


class TestGetDecodes:
    async def test_bytes_become_str(self):
        """What a client left at its defaults returns."""
        assert await _cache(b"open").get("state") == "open"

    async def test_str_is_passed_through_unchanged(self):
        """What a `decode_responses=True` client returns. Decoding must not
        double-handle it."""
        assert await _cache("open").get("state") == "open"

    async def test_missing_stays_none(self):
        """`None` is a hit that missed, not a value to decode -- and
        `bytes(None)` would raise rather than return."""
        assert await _cache(None).get("state") is None

    async def test_non_ascii_survives_the_round_trip(self):
        """UTF-8 rather than a default codec, asserted with a value that tells
        them apart: `latin-1` would give three mojibake characters here."""
        assert await _cache("Ada Lovelace — 1843".encode()).get("state") == "Ada Lovelace — 1843"

    @pytest.mark.parametrize("value", [b"", ""])
    async def test_an_empty_value_is_a_value_not_a_miss(self, value: Any):
        """`b""` is falsy, so a truthiness check where a `None` check belongs
        would turn a stored empty string into a cache miss."""
        assert await _cache(value).get("state") == ""
