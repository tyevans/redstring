"""Ergonomic container dunders and single-event append on InMemoryEventStore.

Asserts eventsource-py 0.15.0 container protocols:
- `len(store)` and `bool(store)`
- `event in store` and `event_id in store`
- `store.clear()`
- `store.stream_exists(stream_id)`
- `store.append(event)` with default `ExpectedVersion.ANY`
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from eventsource.adapters.memory import InMemoryEventStore
from eventsource.domain.tenant_context import tenant_scope

from redstring.events.document import DocumentExtracted
from redstring.events.streams import document_stream


@pytest.fixture
def tenant_id():
    return uuid4()


@pytest.fixture
def store():
    return InMemoryEventStore()


async def test_in_memory_event_store_container_dunders(store, tenant_id):
    """Verify len, bool, clear, and membership dunders on InMemoryEventStore."""
    # An empty store is always truthy in 0.15.0 (for `store or default_store` safety)
    assert bool(store) is True
    assert len(store) == 0

    stream = document_stream(tenant_id=tenant_id, source_id="doc-1")
    assert not await store.stream_exists(stream)

    event = DocumentExtracted(
        aggregate_id=stream.aggregate_id,
        tenant_id=tenant_id,
        source_id="doc-1",
        model_version="ollama/qwen3.6-27b",
    )

    async with tenant_scope(tenant_id):
        # 0.15.0 single-event append with default expected_version
        await store.append(stream, [event])

    assert len(store) == 1
    assert await store.stream_exists(stream)
    assert event in store
    assert event.event_id in store

    # clear() resets the store
    store.clear()
    assert len(store) == 0
    assert not await store.stream_exists(stream)
    assert event not in store
