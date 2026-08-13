"""Folding `DocumentChunked` into a `ChunkStore`.

**Every expectation here is built independently of the fold.** `_corpus` walks
the events with a plain dict keyed the way the port is -- `(tenant_id, id)` --
and is the oracle the assertions compare against. That is not ceremony: a
handler that only ever upserted would agree with itself on both sides of any
"fold it twice, get the same thing" property, and three such mutants survived
the replay-equivalence suite in slice 5b before it grew an oracle. The
re-chunk test below is the one that dies under that mutant, and it was watched
dying before this file was trusted.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from eventsource.adapters.memory import InMemoryDLQRepository

from redstring.chunks.adapters.memory import InMemoryChunkStore
from redstring.domain.chunk import StoredChunk, chunk_id
from redstring.events import DocumentChunked
from redstring.projections import ChunkProjection

from .conftest import NO_RETRIES

SOURCE_ID = "doc-1"
OTHER_SOURCE_ID = "doc-2"
SIGNATURE = "recursive:abc123"


def _chunk(
    tenant_id: UUID, text: str, *, source_id: str = SOURCE_ID, index: int = 0
) -> StoredChunk:
    return StoredChunk(
        id=chunk_id(source_id, text),
        tenant_id=tenant_id,
        source_id=source_id,
        text=text,
        chunk_index=index,
        start_char=0,
        end_char=len(text),
    )


def _chunking(
    tenant_id: UUID,
    chunks: list[StoredChunk],
    *,
    source_id: str = SOURCE_ID,
    signature: str = SIGNATURE,
) -> DocumentChunked:
    return DocumentChunked(
        aggregate_id=uuid4(),
        tenant_id=tenant_id,
        source_id=source_id,
        chunking_signature=signature,
        chunks=chunks,
    )


def _corpus(*events: DocumentChunked) -> dict[tuple[UUID, str], StoredChunk]:
    """What the store should hold after folding `events`, built here.

    A plain dict keyed `(tenant_id, id)` -- the key the port names -- with the
    replacement rule written out rather than delegated: an event drops this
    tenant's other chunks of *its* source, and writes the ones it carries.
    Nothing in `src/` is called, so an assertion against this cannot agree
    with a handler that drops work.
    """
    expected: dict[tuple[UUID, str], StoredChunk] = {}
    for event in events:
        for key in list(expected):
            tenant_id, _ = key
            if tenant_id == event.tenant_id and expected[key].source_id == event.source_id:
                del expected[key]
        for chunk in event.chunks:
            expected[chunk.tenant_id, chunk.id] = chunk
    return expected


async def _stored(store: InMemoryChunkStore, expected_keys) -> dict[tuple[UUID, str], StoredChunk]:
    """Everything the store holds under any key the oracle has ever named.

    Read through `get`, so a chunk the handler failed to delete shows up as a
    surviving entry rather than as an absence nothing looks for.
    """
    found = {}
    for tenant_id, id_ in expected_keys:
        chunk = await store.get(id_, tenant_id)
        if chunk is not None:
            found[tenant_id, id_] = chunk
    return found


class _Rig:
    def __init__(self) -> None:
        self.store = InMemoryChunkStore(dimension=4)
        self.dlq = InMemoryDLQRepository()
        self.projection = ChunkProjection(self.store, dlq_repo=self.dlq, retry_policy=NO_RETRIES)

    async def fold(self, *events: DocumentChunked) -> None:
        for event in events:
            await self.projection.handle(event)
        # `handle` routes a failure to the DLQ rather than raising, so a
        # handler that blew up on every event would leave every assertion
        # below comparing two empty things.
        assert await self.dlq.get_failed_events() == []


def _keys(*chunk_sets) -> set[tuple[UUID, str]]:
    return {(c.tenant_id, c.id) for chunks in chunk_sets for c in chunks}


async def test_folding_one_event_writes_the_whole_chunking() -> None:
    rig = _Rig()
    tenant_id = uuid4()
    chunks = [_chunk(tenant_id, "first passage", index=0), _chunk(tenant_id, "second", index=1)]
    event = _chunking(tenant_id, chunks)

    await rig.fold(event)

    expected = _corpus(event)
    assert await _stored(rig.store, _keys(chunks)) == expected
    assert await rig.store.get_by_source(SOURCE_ID, tenant_id) == chunks


async def test_folding_the_same_event_twice_leaves_the_same_corpus() -> None:
    """Idempotent redelivery, asserted against the independently-built
    expectation rather than against the result of the first fold -- which is
    the comparison a handler dropping work would satisfy trivially."""
    rig = _Rig()
    tenant_id = uuid4()
    chunks = [_chunk(tenant_id, "first passage"), _chunk(tenant_id, "second", index=1)]
    event = _chunking(tenant_id, chunks)

    await rig.fold(event, event)

    assert await _stored(rig.store, _keys(chunks)) == _corpus(event, event)
    assert await rig.store.get_by_source(SOURCE_ID, tenant_id) == chunks


async def test_folding_a_re_chunk_removes_the_orphans() -> None:
    """The event carries the new chunking; the old passages must be gone.

    This is the test that dies when the handler upserts instead of replacing,
    and the exact id set is written out here so it cannot be satisfied by a
    corpus that merely *contains* the new chunks.
    """
    rig = _Rig()
    tenant_id = uuid4()
    old = [_chunk(tenant_id, "first passage"), _chunk(tenant_id, "second", index=1)]
    # One text is carried over, so the assertion distinguishes "replaced the
    # source" from "deleted everything and rewrote it".
    new = [_chunk(tenant_id, "first passage"), _chunk(tenant_id, "third", index=1)]
    first, second = _chunking(tenant_id, old), _chunking(tenant_id, new, signature="fixed:def456")

    await rig.fold(first, second)

    every_key = _keys(old, new)
    assert await _stored(rig.store, every_key) == _corpus(first, second)
    assert {key for key in every_key if key in await _stored(rig.store, every_key)} == _keys(new)
    assert await rig.store.get_by_source(SOURCE_ID, tenant_id) == new


async def test_folding_an_empty_chunking_empties_the_source() -> None:
    rig = _Rig()
    tenant_id = uuid4()
    chunks = [_chunk(tenant_id, "first passage")]
    first = _chunking(tenant_id, chunks)
    emptied = _chunking(tenant_id, [], signature="fixed:def456")

    await rig.fold(first, emptied)

    assert _corpus(first, emptied) == {}
    assert await _stored(rig.store, _keys(chunks)) == {}
    assert await rig.store.get_by_source(SOURCE_ID, tenant_id) == []


async def test_folding_two_sources_leaves_each_intact() -> None:
    """A re-chunk of one source must not touch the other. The second source
    is re-chunked *after* the first, so a handler scoping its deletion to the
    tenant rather than to the source loses the first source's passages."""
    rig = _Rig()
    tenant_id = uuid4()
    first_source = [_chunk(tenant_id, "first passage")]
    other_source = [_chunk(tenant_id, "elsewhere", source_id=OTHER_SOURCE_ID)]
    other_again = [_chunk(tenant_id, "elsewhere, revised", source_id=OTHER_SOURCE_ID)]

    events = (
        _chunking(tenant_id, first_source),
        _chunking(tenant_id, other_source, source_id=OTHER_SOURCE_ID),
        _chunking(tenant_id, other_again, source_id=OTHER_SOURCE_ID, signature="fixed:def456"),
    )
    await rig.fold(*events)

    assert await _stored(rig.store, _keys(first_source, other_source, other_again)) == _corpus(
        *events
    )
    assert await rig.store.get_by_source(SOURCE_ID, tenant_id) == first_source
    assert await rig.store.get_by_source(OTHER_SOURCE_ID, tenant_id) == other_again


async def test_folding_two_tenants_leaves_each_intact() -> None:
    """Same source id, same text, two tenants -- so the *same* `ChunkId`.

    Content addressing makes that collision ordinary rather than unlikely, and
    a store keyed on the id alone would let one tenant's re-chunk delete the
    other's passage. The second tenant re-chunks, so the deletion runs.
    """
    rig = _Rig()
    left, right = uuid4(), uuid4()
    shared_text = "first passage"
    left_chunks = [_chunk(left, shared_text)]
    right_chunks = [_chunk(right, shared_text)]
    right_again = [_chunk(right, "rewritten")]
    assert left_chunks[0].id == right_chunks[0].id

    events = (
        _chunking(left, left_chunks),
        _chunking(right, right_chunks),
        _chunking(right, right_again, signature="fixed:def456"),
    )
    await rig.fold(*events)

    assert await _stored(rig.store, _keys(left_chunks, right_chunks, right_again)) == _corpus(
        *events
    )
    assert await rig.store.get_by_source(SOURCE_ID, left) == left_chunks
    assert await rig.store.get_by_source(SOURCE_ID, right) == right_again


async def test_truncate_read_models_refuses_and_names_the_alternative() -> None:
    """Nothing here spans tenants, so the rebuild driver must be told what to
    call instead rather than being handed a silent no-op."""
    rig = _Rig()
    with pytest.raises(NotImplementedError, match="delete_by_tenant"):
        await rig.projection._truncate_read_models()


async def test_the_projection_subscribes_to_the_chunking_event() -> None:
    """An event no projection handles is a fact the read models never learn,
    and `@handles` is a decorator a mutation run can delete."""
    assert DocumentChunked in _Rig().projection.subscribed_to()
