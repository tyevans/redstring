"""An adapter behind an extra must say which extra, not `ModuleNotFoundError`.

`.claude/rules/definition-of-done.md` requires it of every adapter pulling an
optional package: *"Optional dependency guarded with `try`/`except
ImportError` ..., re-raised with a message naming the extra to install."*

The rule is worth a test rather than a review note because the failure is
invisible to everyone who has the package installed -- which is everyone
working on this repository, since `uv sync --all-extras` installs all of them.
A missing guard is only ever observed by a user, at the moment they are
already stuck.

The absence is simulated by putting `None` in `sys.modules`, which makes
`import x` raise `ImportError` the way a missing distribution does. That is
much cheaper than a venv per case and tests the same branch -- the guards
catch `ImportError`, and `ModuleNotFoundError` is a subclass of it.

`RedisCache.__init__` and `PgVectorStore.__init__` are deliberately not
covered: both take an already-constructed client or pool, so a caller who
reaches them has the package by construction. Only the `from_url`/`connect`
constructors import anything.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from redstring.llm.cache.redis import RedisCache
from redstring.vector.adapters.pgvector import PgVectorStore


class TestTheGuardsNameTheirExtra:
    async def test_pgvector_connect_without_asyncpg(self):
        with (
            patch.dict(sys.modules, {"asyncpg": None}),
            pytest.raises(ImportError) as caught,
        ):
            await PgVectorStore.connect("postgresql://localhost/x", dimension=3)

        message = str(caught.value)
        assert "pgvector" in message, message
        assert "asyncpg" in message, message

    async def test_redis_cache_from_url_without_redis(self):
        with (
            patch.dict(sys.modules, {"redis": None, "redis.asyncio": None}),
            pytest.raises(ImportError) as caught,
        ):
            RedisCache.from_url("redis://localhost:6379")

        message = str(caught.value)
        assert "redis" in message, message


class TestTheModulesImportWithoutTheirPackage:
    """Importing the adapter module must not require the optional package.

    This is the property that makes an extra an extra. Both modules keep their
    third-party import inside `if TYPE_CHECKING:` and inside the one function
    that needs it, so `redstring.vector.adapters.pgvector` is importable on a
    base install -- and `redstring/__init__.py` can therefore keep exporting
    the store types without dragging a Postgres driver in.

    A regression here would not fail any other test in this repository: every
    other module reaches these classes with the packages installed.
    """

    @pytest.mark.parametrize(
        ("module", "absent"),
        [
            ("redstring.vector.adapters.pgvector", {"asyncpg": None}),
            ("redstring.llm.cache.redis", {"redis": None, "redis.asyncio": None}),
        ],
    )
    def test_the_module_reimports_cleanly(self, module: str, absent: dict[str, None]):
        import importlib

        with patch.dict(sys.modules, absent):
            reloaded = importlib.reload(importlib.import_module(module))

        assert reloaded is not None
