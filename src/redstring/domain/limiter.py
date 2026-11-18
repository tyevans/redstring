"""A ceiling on calls in flight against the inference endpoint.

Not owned by any one pipeline, and that is the point. The operator's
constraint is the backend's queue depth -- a single-GPU llama.cpp server
processes one request at a time and converts ten concurrent requests into ten
timeouts -- and the queue does not care which code path issued a request. So
the ceiling has to be one object every call passes through, shared across
callers that cannot import each other.

It lives in `domain` for exactly that reason: `extraction` and `consolidation`
are siblings in the layer contract and forbidden from importing each other,
and two limiters would be two ceilings, which is no ceiling. Nothing here does
I/O or depends on anything above `domain` -- it is a semaphore with a name and
a refusal, which is the same test every other module in this layer passes.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import TracebackType

__all__ = ["MAX_CONCURRENCY", "CallLimiter", "check_concurrency"]

#: The most calls one caller may hold in flight, and the most documents or
#: chunks it may run at once. Every `concurrency` and `max_concurrency`
#: parameter in the library is refused above this value.
#:
#: The number is a backstop, not a tuning target. Past a few dozen concurrent
#: requests an inference backend is queue-bound, and each in-flight task
#: holds a prompt, a response and a connection. A caller who genuinely needs
#: more should shard the work across processes, not raise one process's
#: ceiling without limit.
MAX_CONCURRENCY = 32


def check_concurrency(name: str, value: int) -> None:
    """Refuse a concurrency value outside `1..MAX_CONCURRENCY`.

    Raises:
        ValueError: `value` is below 1 or above `MAX_CONCURRENCY`.
    """
    if value < 1:
        raise ValueError(f"{name} must be at least 1, got {value}")
    if value > MAX_CONCURRENCY:
        raise ValueError(f"{name} must be at most {MAX_CONCURRENCY}, got {value}")


class CallLimiter:
    """Admits at most `limit` callers at once."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
        self._limit = limit
        self._semaphore = asyncio.Semaphore(limit)

    @property
    def limit(self) -> int:
        return self._limit

    async def __aenter__(self) -> None:
        await self._semaphore.acquire()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Release on the way out, whatever happened.

        A slot leaked on failure turns one transient error into a permanent
        deadlock that presents as a hung model.
        """
        self._semaphore.release()
