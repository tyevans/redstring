"""The in-memory `ChunkStore` adapter against the shared compliance suite.

Everything asserted about behaviour lives in `redstring.testing.chunk_store`.
This module supplies a store and adds only what is specific to *this*
adapter -- namely that it holds no state outside itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from redstring.chunks.adapters.memory import InMemoryChunkStore
from redstring.domain.chunk import StoredChunk, chunk_id
from redstring.testing.chunk_store import ChunkStoreCompliance

if TYPE_CHECKING:
    from redstring.ports.chunk_store import ChunkStore


class TestMemoryChunkStore(ChunkStoreCompliance):
    async def new_store(self) -> ChunkStore:
        return InMemoryChunkStore(dimension=self.DIMENSION)


@pytest.mark.unit
async def test_dimension_must_be_positive() -> None:
    """Mirrors `tests/unit/vector/test_memory_store.py`'s case of the same
    name: a zero-dimension store accepts only the zero-length vector, which
    is also a zero vector, so nothing could ever be written to it."""
    for bad in (0, -1):
        with pytest.raises(ValueError, match="dimension"):
            InMemoryChunkStore(dimension=bad)


@pytest.mark.unit
async def test_a_fresh_store_holds_nothing() -> None:
    store = InMemoryChunkStore(dimension=4)
    assert await store.get_by_source("doc-1", uuid4()) == []


@pytest.mark.unit
async def test_it_holds_no_state_outside_itself() -> None:
    """Two stores are independent; nothing is class-level or module-level."""
    tenant = uuid4()
    first, second = InMemoryChunkStore(dimension=4), InMemoryChunkStore(dimension=4)
    ident = chunk_id("doc-1", "t")
    await first.upsert_many(
        [
            StoredChunk(
                id=ident,
                tenant_id=tenant,
                source_id="doc-1",
                text="t",
                chunk_index=0,
                start_char=0,
                end_char=1,
            )
        ]
    )
    assert await second.get(ident, tenant) is None
