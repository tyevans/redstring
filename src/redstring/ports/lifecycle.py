"""`AsyncClosable`: the release half of every store-shaped port.

An adapter behind one of this library's store ports may hold a driver, a
connection pool or a client, and the caller who built it is the only one who
knows when it is finished with. Four adapters -- `Neo4jGraphStore`,
`PgVectorStore`, `PostgresChunkStore` and `RedisCache` -- grew
`__aenter__`/`__aexit__` for exactly that, and until this protocol existed the
block form was reachable **only by naming the concrete class**. A caller
holding a `GraphStore` could not write `async with` at all, so the safe
lifetime form was available precisely where the type was least abstract, which
is backwards.

## Why a base rather than a protocol standing beside the others

`ports/cache.py` records the first attempt at this question and how it was
settled: `close` was given a protocol of its own, on the reasoning that neither
consumer called it, and `mypy` refuted that within a minute because both
consumers forwarded it. The same arbitration decided the shape here. A separate
`AsyncClosable` that adapters *also* satisfy is unusable by a caller who was
handed a `GraphStore` -- narrowing back to it needs a cast or an intersection
type Python does not have -- so every capability protocol **inherits** this
one, and the pair arrives through the MRO the way every other composed method
here already does.

Releasing what an adapter holds is a property of *holding* one, and every
capability is a handle on the same adapter. That is why this is a base of each
capability rather than a member of the composed port: a caller narrowed to
`VectorWriter` holds the store just as completely as one holding `VectorStore`,
and is just as capable of being the last to finish with it.

## What it costs an adapter that holds nothing

Four no-op methods, and they are honest rather than apologetic. An
`InMemoryGraphStore` holds dictionaries the garbage collector already owns, so
"release what you hold" is genuinely satisfied by doing nothing; the caller
still gets one lifetime discipline that works against the port whichever
adapter is behind it, which is the entire point of a port.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Self, runtime_checkable

if TYPE_CHECKING:
    from types import TracebackType


@runtime_checkable
class AsyncClosable(Protocol):
    """Something holding a resource that a caller must be able to release."""

    async def close(self) -> None:
        """Release whatever the adapter holds. Safe to call twice.

        An adapter that was *handed* its driver or pool leaves it alone -- see
        `RedisCache.owns_client`. Closing a resource you did not create takes
        it down for everyone sharing it, and "whoever finishes first wins" is
        not a lifetime.
        """
        ...

    async def __aenter__(self) -> Self:
        """Return the adapter itself, so `async with await X.connect(...) as s`
        binds the store rather than `None`."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close on the way out, and **never suppress**.

        The return type is `None` on purpose. `bool` invites `return True`,
        which reads as "handled" and silently eats every exception raised in
        the body -- including the `CancelledError` of a request that timed
        out, whose caller would then learn the work completed.
        """
        ...
