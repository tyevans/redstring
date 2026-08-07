"""`InMemoryChunkStore`: the tests true of this adapter and no other."""

from __future__ import annotations

from uuid import uuid4

import pytest

from redstring.chunks.adapters.memory import InMemoryChunkStore
from redstring.domain.chunk import StoredChunk


@pytest.mark.unit
async def test_a_fresh_store_holds_nothing() -> None:
    store = InMemoryChunkStore()
    assert await store.get_by_source("doc-1", uuid4()) == []


@pytest.mark.unit
async def test_it_holds_no_state_outside_itself() -> None:
    """Two stores are independent; nothing is class-level or module-level."""
    tenant = uuid4()
    first, second = InMemoryChunkStore(), InMemoryChunkStore()
    await first.upsert_many(
        [
            StoredChunk(
                id="a",
                tenant_id=tenant,
                source_id="doc-1",
                text="t",
                chunk_index=0,
                start_char=0,
                end_char=1,
            )
        ]
    )
    assert await second.get("a", tenant) is None
