"""`async with` on every class that may hold a resource it created.

`Neo4jGraphStore`, `PgVectorStore`, `PostgresChunkStore` and `RedisCache` each
own a driver, a connection pool or a client, and each exposes `close()`. Before
this module they exposed *only* `close()`, so the shipped usage was `connect()`
plus a `try/finally` the caller had to remember, and a caller who forgot -- or
who was cancelled inside the body -- leaked the pool.

`CircuitBreaker` and `RateLimiter` joined them when B108 was fixed, and how
they joined is the point of the derivation below. They were correctly *out* of
scope while their `close()` closed unconditionally, because a class that always
closes owns nothing conditionally and has nothing to be careful about. Giving
each an `_owns_cache` flag -- so a cache the caller passed in is left open --
made them resource owners in exactly the sense this module scans for, and the
scan picked them up on its own. Nobody had to remember to add them; the run
failed until the builders below existed. That is the property the derivation
was written for, and this is the first time it fired.

**Why these are unit tests when three of the four adapters need a server.**
The semantics under test are properties of `__aexit__`, not of any backend:
that it closes, that it closes on the way out of a raising body, that it does
not swallow what the body raised, and that a cancelled task stays cancelled.
None of that needs Postgres to be running -- it needs an object that records
whether it was closed. The behavioural suites in `tests/integration/` still
own everything that requires the backend to answer.

**What each test would fail against, which is the point of writing them this
way.** An `__aexit__` with an empty body passes any test that merely asserts
`async with` works; the resource-closed assertions are what reject it. An
`__aexit__` returning `True` -- the single most likely wrong spelling, since
it reads as "handled" -- passes *both* of those and silently swallows every
exception raised in the body, so `test_an_exception_in_the_body_propagates`
and its cancellation twin are the ones carrying that weight. All four
deliberate defects were applied to the source and each failed here before
this module was believed; see the `# Broken on purpose` notes below. The two
defects are cleanly separated by which tests reject them, which is the
evidence that neither set is redundant: an empty `__aexit__` fails only the
three close-assertions (12 of 26), and `return True` fails only the two
suppression tests (8 of 26) while every close-assertion stays green.

Ownership is not re-litigated by the context manager: `__aexit__` calls
`close()`, and `close()` on all four leaves an *injected* resource alone.
`test_the_block_does_not_close_a_resource_it_does_not_own` pins that, because
"exiting the block closes the pool" is the obvious over-implementation and it
would take a shared pool down with the first store to finish.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, Self, cast

import pytest

import redstring
from redstring.chunks.adapters.postgres import PostgresChunkStore
from redstring.graph.adapters.neo4j import Neo4jGraphStore
from redstring.llm.cache.redis import RedisCache
from redstring.llm.circuit_breaker import CircuitBreaker
from redstring.llm.rate_limiter import RateLimiter
from redstring.vector.adapters.pgvector import PgVectorStore

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType


class _Resource:
    """A driver, pool or client that records being closed.

    Both spellings are here because the adapters do not agree: the neo4j
    driver and an asyncpg pool close with `close()`, and `redis.asyncio`
    deprecated `close()` in favour of `aclose()`. Recording both onto one
    counter means the test asserts "this resource was released" rather than
    "this particular method was called", which is what the adapter's
    docstrings actually promise.
    """

    def __init__(self) -> None:
        self.closes = 0

    async def close(self) -> None:
        self.closes += 1

    async def aclose(self) -> None:
        self.closes += 1


class _Closeable(Protocol):
    """The shape all four adapters now share."""

    async def close(self) -> None: ...
    async def __aenter__(self) -> Self: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


def _neo4j(resource: _Resource, *, owned: bool) -> _Closeable:
    store = Neo4jGraphStore(cast("Any", resource))
    # `connect()` is the public path that sets this, and it takes a URI --
    # so an owning store cannot otherwise be built without reaching a server.
    store._owns_driver = owned
    return store


def _pgvector(resource: _Resource, *, owned: bool) -> _Closeable:
    store = PgVectorStore(cast("Any", resource), dimension=8)
    store._owns_pool = owned
    return store


def _chunks(resource: _Resource, *, owned: bool) -> _Closeable:
    store = PostgresChunkStore(cast("Any", resource), dimension=8)
    store._owns_pool = owned
    return store


def _redis(resource: _Resource, *, owned: bool) -> _Closeable:
    return RedisCache(cast("Any", resource), owns_client=owned)


def _breaker(resource: _Resource, *, owned: bool) -> _Closeable:
    # The resource stands in for the cache. Passing one sets `_owns_cache`
    # False, which is the case the constructor decides -- so the flag is set
    # afterwards for the owning case, exactly as the three stores above do it,
    # rather than constructing with `cache=None` and losing the double.
    breaker = CircuitBreaker(cache=cast("Any", resource))
    breaker._owns_cache = owned
    return breaker


def _limiter(resource: _Resource, *, owned: bool) -> _Closeable:
    limiter = RateLimiter(rpm=1, cache=cast("Any", resource))
    limiter._owns_cache = owned
    return limiter


#: Every class in `src/` that may own the resource it holds.
#: `TestEveryResourceHoldingAdapterIsCovered` below derives that set from the
#: source tree and fails if this dict has drifted from it.
BUILDERS: dict[str, Callable[[_Resource, bool], _Closeable]] = {
    "Neo4jGraphStore": lambda resource, owned: _neo4j(resource, owned=owned),
    "PgVectorStore": lambda resource, owned: _pgvector(resource, owned=owned),
    "PostgresChunkStore": lambda resource, owned: _chunks(resource, owned=owned),
    "RedisCache": lambda resource, owned: _redis(resource, owned=owned),
    "CircuitBreaker": lambda resource, owned: _breaker(resource, owned=owned),
    "RateLimiter": lambda resource, owned: _limiter(resource, owned=owned),
}

adapters = pytest.mark.parametrize("build", BUILDERS.values(), ids=list(BUILDERS))


class TestTheBlockCloses:
    """The reason the methods exist: exiting releases the resource."""

    @adapters
    async def test_leaving_the_block_normally_closes_the_resource(self, build):
        # Broken on purpose: an `__aexit__` whose body is `return` -- this
        # failed on all four adapters, `assert 0 == 1`. It is the test the
        # whole finding rests on; the rest of the module exists because this
        # one is also passed by an `__aexit__` that closes and then swallows.
        resource = _Resource()
        adapter = build(resource, True)

        async with adapter:
            assert resource.closes == 0, "closed before the block ran"

        assert resource.closes == 1

    @adapters
    async def test_entering_yields_the_adapter_itself(self, build):
        """`async with await X.connect(...) as store` must bind the store.

        Returning `None` from `__aenter__` is the default for a method
        someone forgot to write, and it produces `AttributeError` on the
        first use rather than at the seam.
        """
        # Broken on purpose: `return None` from `PgVectorStore.__aenter__`.
        # Only this test failed, and only for that adapter.
        adapter = build(_Resource(), True)

        async with adapter as entered:
            assert entered is adapter

    @adapters
    async def test_the_block_does_not_close_a_resource_it_does_not_own(self, build):
        """Ownership decides, exactly as it does for a bare `close()` call.

        An `__aexit__` closing the underlying resource directly rather than
        through `close()` passes every other test here and takes an injected,
        shared pool down with the first store to leave a block.
        """
        # Broken on purpose: `await self._driver.close()` in place of
        # `await self.close()` on the Neo4j adapter. Only this test failed,
        # `assert 1 == 0`, and only for that adapter.
        resource = _Resource()

        async with build(resource, False):
            pass

        assert resource.closes == 0


class TestTheBlockNeverSuppresses:
    """`__aexit__` returns `None`, and these are what say so.

    Both tests assert two separate claims -- that the resource was released
    *and* that the exception still reached the caller -- because an
    `__aexit__` returning a truthy value satisfies the first and destroys the
    second, and it is the wrong spelling most likely to be written.
    """

    @adapters
    async def test_an_exception_in_the_body_propagates_and_still_closes(self, build):
        # Broken on purpose: `-> bool: await self.close(); return True`.
        # `pytest.raises` failed here -- DID NOT RAISE -- on all four, while
        # every test in `TestTheBlockCloses` stayed green.
        resource = _Resource()

        with pytest.raises(ZeroDivisionError):
            async with build(resource, True):
                raise ZeroDivisionError("from the body")

        assert resource.closes == 1

    @adapters
    async def test_cancelling_the_task_still_cancels_it_and_still_closes(self, build):
        """A cancelled body is the case the `try/finally` form got wrong too.

        Real cancellation rather than a hand-raised `CancelledError`: the
        task is cancelled from outside while suspended inside the block,
        which is what actually happens to a request that times out. A
        suppressing `__aexit__` would make the task complete normally, and a
        caller awaiting it would never learn the work did not happen.
        """
        # Broken on purpose: the same `return True`. `task.cancelled()` came
        # back False and the `pytest.raises` failed -- the task finished
        # normally with the cancellation eaten.
        resource = _Resource()
        entered = asyncio.Event()

        async def body() -> None:
            async with build(resource, True):
                entered.set()
                await asyncio.Event().wait()  # never set; only cancellation ends it

        task = asyncio.create_task(body())
        # Bounded, and the bound is load-bearing rather than defensive. An
        # adapter with no `__aexit__` makes `async with` raise before
        # `entered.set()` runs, so a bare `await entered.wait()` waits on an
        # event nothing will ever set -- and this suite is exactly where that
        # happens, since a broken-on-purpose run is the point of the module.
        # It cost 25 minutes of a wedged pre-commit hook once, read as slow
        # infrastructure and retried rather than investigated, which is the
        # failure CLAUDE.md's "bound any loop whose exit depends on
        # adapter-supplied data" is about. Fail naming the cause instead.
        try:
            await asyncio.wait_for(entered.wait(), timeout=5.0)
        except TimeoutError:
            await asyncio.wait_for(task, timeout=5.0)  # surfaces the real error
            pytest.fail("the block was never entered, and the body did not raise")
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert task.cancelled()
        assert resource.closes == 1


class TestEveryResourceHoldingAdapterIsCovered:
    """Guard the guard: a parametrisation that misses an adapter is silent.

    A fifth adapter owning a pool would ship with none of the above and
    nothing here would have failed. A hand-written list cannot catch that --
    it needs updating by the same person who forgot the methods -- so the
    subject set is **derived from the source tree** the way the compliance
    coverage gates derive their read-method lists.

    The derivation is the ownership flag, not `close()`, and B108 is the
    argument for that choice rather than a complication of it. Every subject
    here keeps an `_owns_*` attribute because it may or may not have created
    the thing it holds, and that flag is what makes it a resource owner.
    `close()` is the wrong signal in both directions: it would have demanded
    these methods of `CircuitBreaker` and `RateLimiter` back when their
    `close()` closed unconditionally, which was the wrong fix for the wrong
    problem -- the bug there was the unconditional close, not the missing
    block form. Fixing the ownership made the flag appear, and the flag is
    what brought them in here.
    """

    def test_the_builders_cover_every_resource_owning_class_in_src(self):
        assert _resource_owning_classes() == set(BUILDERS)

    def test_the_detector_finds_something(self):
        """A scan matching nothing passes vacuously, which is indistinguishable
        from a scan that works. See `docs/adr/0014-...`."""
        assert len(_resource_owning_classes()) >= 4

    @pytest.mark.parametrize(
        "adapter_class",
        [
            Neo4jGraphStore,
            PgVectorStore,
            PostgresChunkStore,
            RedisCache,
            CircuitBreaker,
            RateLimiter,
        ],
        ids=lambda cls: cls.__name__,
    )
    def test_each_one_declares_both_halves_of_the_pair(self, adapter_class):
        """A half-written pair is the realistic omission: `__aenter__` alone
        raises `AttributeError` on the way out, after the body has run."""
        assert adapter_class.__name__ in BUILDERS
        assert callable(adapter_class.__aenter__)
        assert callable(adapter_class.__aexit__)


def _resource_owning_classes() -> set[str]:
    """Class names under `src/redstring` that assign a `self._owns_*` flag."""
    root = Path(redstring.__file__).parent
    owners: set[str] = set()
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ClassDef) and _assigns_an_ownership_flag(node):
                owners.add(node.name)
    return owners


def _assigns_an_ownership_flag(class_def: ast.ClassDef) -> bool:
    return any(
        isinstance(target, ast.Attribute)
        and target.attr.startswith("_owns_")
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
        for node in ast.walk(class_def)
        if isinstance(node, ast.Assign)
        for target in node.targets
    )
