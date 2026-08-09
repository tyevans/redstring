"""The release half a capability double has to satisfy, written once.

ADR 0028 made every capability protocol compose `ports.lifecycle.AsyncClosable`,
so a hand-written double claiming to *be* a capability now owes
`close`/`__aenter__`/`__aexit__` as well as the methods the capability is named
for. Without them the `isinstance` assertions in the segregation modules stop
reporting segregation and start reporting a missing `close` -- the double fails
for the wrong reason, and the test reads as if the split had broken.

Three modules grew the same nine lines when 0028 landed
(`tests/unit/llm/test_cache_capabilities.py`,
`tests/unit/chunks/test_capability_segregation.py`,
`tests/unit/vector/test_capability_segregation.py`), which is
`.claude/rules/recurring-defects.md` §2: one fact in three places with nothing
that fails when the copies disagree.

**Why here and not `tests/conftest.py`.** `.claude/rules/testing.md` records
that the root conftest deliberately defines nothing shared, because a root
fixture is visible to every tree including `tests/integration/`. This directory
is the opposite shape and already the home for exactly this kind of thing: it
is a *library* rather than a suite -- no `test_*.py` module, no `Test*` class,
so pytest walks past it -- whose subject is what a port contract requires of an
implementation. `NoOpLifetime` is imported by name where it is wanted and
reaches nothing that does not ask for it. `BACKLOG.md` B107c predicted this
home ("`redstring/testing/` is the likelier home than `conftest.py`") and said
to hoist when a fourth port grew capability doubles. The fourth already
existed: `tests/unit/consolidation/test_graph_capability_segregation.py` spells
the same three members inline on `BlockingGraph`, which is why the entry's
"three copies" undercounted and why hoisting happened now rather than later.

**What it deliberately does not supply.** `close()` here is a no-op that
records nothing. Two doubles in `test_cache_capabilities.py` override it to set
`self.closed`, and `test_neither_consumer_closes_a_cache_it_was_given` asserts
that flag stays False -- so a mixin whose `close()` quietly *worked* would
leave that assertion green while it had stopped meaning anything. A double that
needs the flag overrides `close`; the guard in that module asserts the override
is still live, so an accidental un-override fails rather than passing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from types import TracebackType


class NoOpLifetime:
    """`AsyncClosable`, for a double that holds nothing to release.

    Mix in ahead of the capability methods. All three members are no-ops
    because these doubles own no driver, pool or client -- the point of
    inheriting them is to satisfy the protocol, not to model a lifetime.
    """

    async def close(self) -> None:
        """Release nothing. Override where a test needs to observe the call."""

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Route through `close`, and deliberately return `None`, never `True`.

        `-> bool: return True` is the plausible wrong spelling and it swallows
        every exception raised in the block, including a cancellation. See
        `tests/unit/test_ports_declare_the_block_form.py`, which broke that on
        purpose against the real adapters.

        Calling `close()` here rather than doing nothing is what makes the
        block form mean the same thing on a double as on an adapter: a double
        that overrides `close` to record the call records it either way, so a
        test can be written with `async with` without discovering that the
        mixin is the one shape where leaving the block releases nothing.
        """
        await self.close()
