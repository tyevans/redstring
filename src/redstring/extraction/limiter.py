"""A ceiling on calls in flight against the inference endpoint.

`ExtractionPipeline`'s batch size bounds how many *chunks* are extracted at
once. It does not bound gleaning, which fires a further call per chunk, or
embedding, which `build_graph` runs after the extraction it does not own. The
operator's constraint is the backend's queue depth -- a single-GPU llama.cpp
server processes one request at a time and converts ten concurrent requests
into ten timeouts -- so the ceiling has to be one object every call passes
through, not a property of any one loop.

Deliberately thinner than `asyncio.Semaphore`: it refuses a limit below one,
and it is a named type so a caller can see what it is holding.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import TracebackType


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
