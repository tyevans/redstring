"""One method is enough to drive `ChunkProjection`, and the port says so now.

`ChunkStore` is nine methods. Its only first-party consumer is
`ChunkProjection`, which calls `replace_source`. One of nine -- worse than the
three-of-eighteen `ports/graph_store.py` records about itself, and paid in the
same currency: `tests/compliance/chunk_store.py` is over a thousand lines, so
an author writing a store to serve only the corpus-write path owed a read,
rank and delete surface they would never call.

`WriteOnlyChunkStore` below is that author's adapter. It has `upsert_many` and
`replace_source` and **nothing else** -- no `get`, no `lexical_candidates`, no
deletes -- and it drives a projection to completion. Before the split it could
not have been annotated as anything the projection accepted.

The other direction matters as much and is easier to forget: an adapter that
implements the whole port must still satisfy every capability, or the split
has quietly become a fork. `InMemoryChunkStore` is checked against all four.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self
from uuid import uuid4

from eventsource.adapters.memory import InMemoryCheckpointRepository, InMemoryDLQRepository

from redstring.chunks.adapters.memory import InMemoryChunkStore
from redstring.domain.chunk import StoredChunk, chunk_id
from redstring.events.document import DocumentChunked
from redstring.ports.chunk_store import (
    ChunkPurge,
    ChunkReader,
    ChunkStore,
    ChunkWriter,
    LexicalCandidateSource,
)
from redstring.projections.chunk import ChunkProjection

if TYPE_CHECKING:
    from types import TracebackType


class Lifetime:
    """The release half every capability inherits from `AsyncClosable`.

    A double claiming to *be* a capability has to satisfy all of it, including
    the part ADR 0028 added -- otherwise the `isinstance` assertions below stop
    saying anything about segregation and start reporting a missing `close`.
    These doubles hold nothing, so all three are no-ops.
    """

    async def close(self) -> None: ...

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


class WriteOnlyChunkStore(Lifetime):
    """`ChunkWriter` and not one method more."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], StoredChunk] = {}

    async def upsert_many(self, chunks) -> None:
        for chunk in chunks:
            self.rows[(str(chunk.tenant_id), chunk.id)] = chunk

    async def replace_source(self, source_id, tenant_id, chunks) -> int:
        keep = {chunk.id for chunk in chunks}
        orphans = [
            key
            for key, held in self.rows.items()
            if key[0] == str(tenant_id) and held.source_id == source_id and held.id not in keep
        ]
        for key in orphans:
            del self.rows[key]
        await self.upsert_many(chunks)
        return len(orphans)


def stored(tenant_id, *, source_id: str, index: int, text: str) -> StoredChunk:
    return StoredChunk(
        id=chunk_id(source_id, text),
        tenant_id=tenant_id,
        source_id=source_id,
        chunk_index=index,
        text=text,
        start_char=0,
        end_char=len(text),
    )


def projection(store) -> ChunkProjection:
    return ChunkProjection(
        store,
        checkpoint_repo=InMemoryCheckpointRepository(),
        dlq_repo=InMemoryDLQRepository(),
    )


class TestTheProjectionNeedsOnlyTheWriter:
    def test_the_write_only_store_is_not_a_chunk_store(self) -> None:
        # The whole point. If this ever became a `ChunkStore`, the double had
        # grown the other seven methods and the test below would be back to
        # exercising a full adapter.
        store = WriteOnlyChunkStore()

        assert isinstance(store, ChunkWriter)
        assert not isinstance(store, ChunkStore)
        assert not isinstance(store, ChunkReader)
        assert not isinstance(store, LexicalCandidateSource)
        assert not isinstance(store, ChunkPurge)

    async def test_a_write_only_store_folds_a_chunking_event(self) -> None:
        tenant_id = uuid4()
        store = WriteOnlyChunkStore()
        chunks = [
            stored(tenant_id, source_id="doc-1", index=0, text="Ada Lovelace wrote notes."),
            stored(tenant_id, source_id="doc-1", index=1, text="Charles Babbage built engines."),
        ]

        await projection(store)._apply_chunking(
            None,
            DocumentChunked(
                tenant_id=tenant_id,
                aggregate_id=uuid4(),
                source_id="doc-1",
                chunks=chunks,
                chunking_signature="fixed/512",
            ),
        )

        assert len(store.rows) == 2

    async def test_a_re_chunk_removes_the_orphans_it_replaced(self) -> None:
        # A projection that only ever inserted would pass the test above.
        # This is the assertion that distinguishes `replace_source` from
        # `upsert_many`, and it is why the projection needs the former.
        tenant_id = uuid4()
        store = WriteOnlyChunkStore()
        first = [stored(tenant_id, source_id="doc-1", index=0, text="one passage, whole")]
        second = [
            stored(tenant_id, source_id="doc-1", index=0, text="one passage,"),
            stored(tenant_id, source_id="doc-1", index=1, text="split in two"),
        ]

        folder = projection(store)
        for chunking in (first, second):
            await folder._apply_chunking(
                None,
                DocumentChunked(
                    tenant_id=tenant_id,
                    aggregate_id=uuid4(),
                    source_id="doc-1",
                    chunks=chunking,
                    chunking_signature="fixed/512",
                ),
            )

        held = {chunk.text for chunk in store.rows.values()}
        assert held == {"one passage,", "split in two"}


class TestTheComposedPortStillBindsEveryCapability:
    def test_the_real_adapter_satisfies_all_four(self) -> None:
        # Guards against the split becoming a fork: a capability the composed
        # port stopped naming would leave this assertion the only thing that
        # noticed.
        store = InMemoryChunkStore()

        for capability in (ChunkWriter, ChunkReader, LexicalCandidateSource, ChunkPurge):
            assert isinstance(store, capability), capability.__name__
        assert isinstance(store, ChunkStore)
