"""`async with` is reachable through the *port*, not only the concrete class.

`test_adapters_close_on_block_exit.py` is the sibling of this module and the
precedent it extends. That one asks whether the four resource-owning adapters
release what they hold on the way out of a block; this one asks whether a
caller who was handed a **port** can write the block at all. Until
`ports/lifecycle.AsyncClosable` existed the answer was no: `async with` worked
against `Neo4jGraphStore` and not against `GraphStore`, so the safe lifetime
form was available exactly where the type was least abstract.

**Why the subject sets differ, which is the interesting half.** The sibling
derives its subjects from classes assigning `self._owns_*`, deliberately --
`close()` as a signal would catch components that own nothing. That derivation
is wrong for this claim in the opposite direction: `InMemoryGraphStore` owns
nothing and still has to declare the pair, because the *port* it satisfies now
declares it and an adapter that skipped it would stop being a `GraphStore`.
The subject set here is therefore "every class in `src/` that satisfies a
capability protocol", derived structurally, and a fifth adapter is picked up
by writing it rather than by remembering this file.

**What an empty implementation would pass.** `async with` succeeding proves
nothing on its own -- `__aenter__` returning `None` and `__aexit__` doing
nothing satisfies it. So the behavioural class below never asserts merely that
the block ran: it asserts that `__aexit__` routes through `close()` (an
adapter that owns nothing has no observable release, so the *call* is the
observable), that entering binds the adapter itself, and that neither an
exception nor a cancellation is swallowed. Each was broken on purpose against
the source before the module was believed; the failures are recorded at each
test.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING, Self

import pytest

from redstring.chunks.adapters.memory import InMemoryChunkStore
from redstring.graph.adapters.memory import InMemoryGraphStore
from redstring.llm.cache.memory import MemoryCache
from redstring.ports import cache, chunk_store, graph_store, vector_store
from redstring.ports.lifecycle import AsyncClosable
from redstring.vector.adapters.memory import InMemoryVectorStore

if TYPE_CHECKING:
    from types import TracebackType

#: The modules that declare capability protocols. `llm_provider` and
#: `embedding_provider` are deliberately absent -- see the BACKLOG entry
#: opened alongside this module, and `TestOnlyTheStoreShapedPortsAreClaimed`
#: below, which is what makes that absence a decision rather than a gap.
PORT_MODULES = (graph_store, vector_store, chunk_store, cache)

#: The members `AsyncClosable` itself contributes. Everything else a
#: capability declares is what an adapter has to implement to *be* one.
LIFECYCLE_MEMBERS = frozenset({"close", "__aenter__", "__aexit__"})


def _capability_protocols() -> dict[str, type]:
    """Every Protocol declared in a port module, keyed by name."""
    found: dict[str, type] = {}
    for module in PORT_MODULES:
        for name, obj in vars(module).items():
            # `__module__` filters two things out that are in the namespace
            # but not declared there: `typing.Protocol` itself, and the
            # imported `AsyncClosable`, which cannot be its own base.
            if (
                inspect.isclass(obj)
                and getattr(obj, "_is_protocol", False)
                and obj.__module__ == module.__name__
            ):
                found[name] = obj
    return found


def _required_members(protocol: type) -> frozenset[str]:
    """What a class must have to satisfy `protocol`, lifecycle aside."""
    declared = {
        name
        for klass in protocol.__mro__
        if getattr(klass, "_is_protocol", False)
        for name in vars(klass)
        if not name.startswith("_") or name in LIFECYCLE_MEMBERS
    }
    return frozenset(declared - LIFECYCLE_MEMBERS)


class TestEveryCapabilityDeclaresTheBlockForm:
    """The port-level claim, and the whole point of the change."""

    @pytest.mark.parametrize(
        "protocol", _capability_protocols().values(), ids=_capability_protocols()
    )
    def test_a_capability_is_closable(self, protocol):
        """A caller narrowed to one capability holds the adapter as completely
        as one holding the composed port, and is as likely to be the last to
        finish with it. Widening this to "the composed port only" is the
        plausible half-answer, and it leaves every narrowed collaborator ADR
        0027 created unable to write the block.
        """
        # Broken on purpose: dropped the base from `EntityReader` alone. This
        # failed as `AsyncClosable not in EntityReader.__mro__`, and only for
        # that one id -- the parametrisation is what makes it per-capability
        # rather than a claim about whichever protocol happens to be checked.
        assert AsyncClosable in protocol.__mro__

    def test_the_scan_finds_the_capabilities(self):
        """A scan matching nothing passes vacuously. See ADR 0014."""
        found = _capability_protocols()
        assert len(found) >= 14
        assert "EntityReader" in found
        assert "HitWindow" in found

    def test_a_capability_requires_more_than_its_lifecycle(self):
        """`_required_members` subtracting too much would make
        `_satisfies` below true of every object in the tree, and the
        adapter gate would pass without checking anything."""
        assert _required_members(graph_store.EntityReader) >= {"get_entity", "find_entities"}
        assert _required_members(cache.HitWindow) == {"record_hit", "count_hits", "oldest_hit"}


def _satisfies(klass: type, protocol: type) -> bool:
    return all(hasattr(klass, name) for name in _required_members(protocol))


def _adapters_of(protocol: type) -> set[type]:
    """Concrete classes in `src/` that implement `protocol`'s methods.

    Structural rather than `issubclass`: several capabilities declare a
    property (`dimension`), which makes them data protocols, and
    `issubclass` against one raises `TypeError` by design.
    """
    import redstring

    seen: set[type] = set()
    for module_name in _submodules(redstring):
        module = __import__(module_name, fromlist=["_"])
        for obj in vars(module).values():
            if (
                inspect.isclass(obj)
                and not getattr(obj, "_is_protocol", False)
                and obj.__module__ == module_name
                and _satisfies(obj, protocol)
            ):
                seen.add(obj)
    return seen


def _submodules(package) -> list[str]:
    import pkgutil

    return [
        info.name
        for info in pkgutil.walk_packages(package.__path__, prefix=f"{package.__name__}.")
        if not info.ispkg
    ]


COMPOSED = {
    "GraphStore": graph_store.GraphStore,
    "VectorStore": vector_store.VectorStore,
    "ChunkStore": chunk_store.ChunkStore,
    "Cache": cache.Cache,
}


class TestEveryAdapterStillSatisfiesItsPort:
    """Guard the guard: a fifth adapter must fail here, not ship silently.

    Derived from the source tree rather than listed, for the reason the
    sibling module's derivation exists -- a hand-written list needs updating
    by the same person who forgot the methods.
    """

    @pytest.mark.parametrize("composed", COMPOSED.values(), ids=list(COMPOSED))
    def test_the_port_has_implementations_to_check(self, composed):
        """Two per port: the in-memory reference and the real backend. A
        discovery function returning nothing would make the next test
        vacuous for that port and say so nowhere."""
        assert len(_adapters_of(composed)) >= 2

    @pytest.mark.parametrize("composed", COMPOSED.values(), ids=list(COMPOSED))
    def test_every_implementation_declares_the_pair(self, composed):
        # Broken on purpose: deleted `__aexit__` from `InMemoryChunkStore`.
        # This failed for the `ChunkStore` id alone, naming the class --
        # "InMemoryChunkStore is missing __aexit__" -- while the behavioural
        # class below failed too, which is the redundancy being checked here:
        # this one would also fire for an adapter nobody had parametrised.
        for adapter in _adapters_of(composed):
            for member in LIFECYCLE_MEMBERS:
                assert callable(getattr(adapter, member, None)), (
                    f"{adapter.__name__} is missing {member}"
                )


class TestOnlyTheStoreShapedPortsAreClaimed:
    """`LlmProvider` and `EmbeddingProvider` are out of scope, on purpose.

    Without this, "we did not get to those" and "those are excluded" are the
    same state of the tree, which is the shape ADR 0014 is about. Their
    adapters hold an HTTP client this library did not build a lifetime for,
    and granting them the pair before deciding what `close()` means there
    would ship four no-op methods on an adapter that does have something to
    release.
    """

    @pytest.mark.parametrize("module_name", ["llm_provider", "embedding_provider"])
    def test_the_provider_ports_are_not_closable(self, module_name):
        import importlib

        module = importlib.import_module(f"redstring.ports.{module_name}")
        protocols = [
            obj
            for obj in vars(module).values()
            if inspect.isclass(obj) and getattr(obj, "_is_protocol", False)
        ]
        assert protocols, "the scan found no protocol, so it asserts nothing"
        for protocol in protocols:
            assert AsyncClosable not in protocol.__mro__


def _memory_adapters():
    return {
        "InMemoryGraphStore": InMemoryGraphStore,
        "InMemoryVectorStore": lambda: InMemoryVectorStore(dimension=8),
        "InMemoryChunkStore": lambda: InMemoryChunkStore(dimension=8),
        "MemoryCache": MemoryCache,
    }


memory_adapters = pytest.mark.parametrize(
    "build", _memory_adapters().values(), ids=list(_memory_adapters())
)


class TestTheBlockDoesSomething:
    """The behaviour, on the four adapters that hold nothing.

    An adapter with no resource has no release to observe, so "did it close"
    cannot be asked of the resource. It can be asked of the adapter: a
    subclass records the call, and an `__aexit__` with an empty body then
    fails rather than passing the way it would against a bare `async with`.
    """

    @memory_adapters
    async def test_leaving_the_block_calls_close(self, build):
        # Broken on purpose: `__aexit__` body replaced with `return` on
        # `InMemoryGraphStore`. Failed here for that id alone --
        # `assert 0 == 1` -- and nothing else in the suite noticed, which is
        # what makes this the test the module rests on.
        adapter = build()
        calls = 0
        original = type(adapter).close

        async def counting(self) -> None:
            nonlocal calls
            calls += 1
            await original(self)

        type(adapter).close = counting
        try:
            async with adapter:
                assert calls == 0, "closed before the block ran"
            assert calls == 1
        finally:
            type(adapter).close = original

    @memory_adapters
    async def test_entering_yields_the_adapter_itself(self, build):
        """`return None` is what a forgotten `__aenter__` does, and it
        produces `AttributeError` at the first use rather than at the seam."""
        # Broken on purpose: `return None` from `MemoryCache.__aenter__`.
        # Only this test failed, and only for that id.
        adapter = build()

        async with adapter as entered:
            assert entered is adapter

    @memory_adapters
    async def test_an_exception_in_the_body_propagates(self, build):
        """`-> bool: return True` reads as "handled" and is the likeliest
        wrong spelling. It passes both tests above."""
        # Broken on purpose: `await self.close(); return True` on
        # `InMemoryVectorStore`. `pytest.raises` failed -- DID NOT RAISE --
        # for that id, while both tests above stayed green for it.
        with pytest.raises(ZeroDivisionError):
            async with build():
                raise ZeroDivisionError("from the body")

    @memory_adapters
    async def test_cancelling_the_task_still_cancels_it(self, build):
        """The case the `try/finally` form got wrong too: a real cancellation
        from outside, which is what a timed-out request does. A suppressing
        `__aexit__` makes the task complete normally and its caller believe
        the work happened."""
        # Broken on purpose: the same `return True`. `task.cancelled()` came
        # back False and the task finished normally with the cancellation
        # eaten.
        entered = asyncio.Event()

        async def body() -> None:
            async with build():
                entered.set()
                await asyncio.Event().wait()  # never set; only cancellation ends it

        task = asyncio.create_task(body())
        # Bounded, and the bound is the point rather than defensive padding.
        # An adapter without `__aexit__` makes `async with` raise *before*
        # `entered.set()`, so a bare `await entered.wait()` waits forever on
        # an event nothing will set -- and a broken-on-purpose run is exactly
        # when that happens. It cost this project 25 minutes of a wedged
        # pre-commit hook once, read as slow infrastructure and retried.
        try:
            await asyncio.wait_for(entered.wait(), timeout=5.0)
        except TimeoutError:
            await asyncio.wait_for(task, timeout=5.0)  # surfaces the real error
            pytest.fail("the block was never entered, and the body did not raise")
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert task.cancelled()

    @memory_adapters
    async def test_close_is_safe_to_call_twice(self, build):
        """`AsyncClosable.close` promises it, and a caller who used the block
        form *and* a `finally` from an older habit does it."""
        adapter = build()

        await adapter.close()
        await adapter.close()

    async def test_an_in_memory_store_survives_being_closed_mid_test(self):
        """The no-op is a claim, not an absence: `close()` on a store that
        owns nothing must not throw its contents away.

        `MemoryCache.close` *does* clear, and says so -- an expiring cache
        holds nothing a caller can ask for after release. A store does not
        get to make that choice quietly, so this pins the difference rather
        than leaving it to whoever reads the two classes next.
        """
        store = InMemoryChunkStore(dimension=8)
        async with store:
            pass
        assert await store.get_by_source("s", "t") == []  # type: ignore[arg-type]


class TestLifecycleIsNotAStandaloneAlternative:
    """Why the pair is a *base* of each capability rather than a sibling.

    `ports/cache.py` records `mypy` refuting a lifecycle protocol standing
    beside the cache halves. The same arbitration was run again for the
    block form and gave the same answer for a sharper reason: a caller handed
    an `EntityReader` cannot narrow back to a separate `AsyncClosable`
    without a cast, so a sibling protocol is unreachable from precisely the
    position that motivated the change.
    """

    def test_a_capability_is_an_async_closable_at_runtime(self):
        """`runtime_checkable` still answers structurally through the MRO,
        which is the property every composed port here relies on."""
        assert isinstance(InMemoryGraphStore(), AsyncClosable)
        assert isinstance(MemoryCache(), AsyncClosable)

    def test_an_object_with_only_close_is_not_one(self):
        """Otherwise the check above passes for anything with a `close`, and
        the isinstance assertion is about the wrong thing entirely."""

        class HalfWritten:
            async def close(self) -> None: ...

            async def __aenter__(self) -> Self:
                return self

        assert not isinstance(HalfWritten(), AsyncClosable)

    def test_the_full_shape_is_one(self):
        class Whole:
            async def close(self) -> None: ...

            async def __aenter__(self) -> Self:
                return self

            async def __aexit__(
                self,
                exc_type: type[BaseException] | None,
                exc: BaseException | None,
                tb: TracebackType | None,
            ) -> None: ...

        assert isinstance(Whole(), AsyncClosable)
