"""Shared compliance suite for the `ChunkStore` port.

**Every `ChunkStore` adapter must pass this suite unchanged.** It is the
executable definition of the port; the prose in `redstring.ports.chunk_store`
describes what these tests enforce. A Protocol body cannot enforce anything --
it states that `replace_source` raises on a provenance mismatch and that `get`
hands back an object the caller may mutate, and neither claim binds until
something asserts it against every adapter. This is that something.

## Consistency contract

Adapters are **read-your-writes**, exactly as `GraphStore` and `VectorStore`
adapters are: once a write has returned, its effect is visible to the next
read on the same store. There is no "eventually" inside a store. Lag belongs
between the event log and the projection, never here.

## Content addressing is why the composite key matters here more than elsewhere

`chunk_id(source_id, text)` is a hash of the source id and the text, so the
*same passage of the same document under two tenants has the same id*. A
`(tenant_id, id)` key compared on `id` alone is therefore a live defect for
this port rather than the astronomically-unlikely one `uuid4()` makes it for
`VectorStore`. `test_two_tenants_hold_the_same_chunk_id_independently` forces
that collision, and it is the single most important case in this file.

The same property is why ordering needs a tie-break at all: two chunks may
transiently claim `chunk_index` 3, so `get_by_source` orders by
`(chunk_index, id)` and a test has to produce a tie or the tie-break is
unobserved.

## How an adapter opts in

Subclass and supply `new_store`::

    class TestMemoryChunkStore(ChunkStoreCompliance):
        async def new_store(self) -> ChunkStore:
            return InMemoryChunkStore()

`new_store` must return an **empty** store, and each call must return one
isolated from every other. `dispose` is a no-op by default and must be
overridden by any adapter holding a connection or pool.

## If you add a read method to the port, add its isolation test here

Every method handing back an object a caller can mutate needs a test that
mutates the result -- including reaching into `entity_ids` and `metadata` --
and asserts a later read is unaffected, in the same edit that adds the method.
`tests/unit/chunks/test_compliance_coverage.py` enforces it by introspecting
the Protocol, so this is a gate rather than advice. Behavioural tests cannot
see the defect: handing back the live internal object is correct on every read
and wrong only afterwards.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest

from redstring.domain.chunk import StoredChunk, chunk_id
from redstring.ports.chunk_store import ChunkStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from redstring.domain.ids import SourceId, TenantId


def _mutate(chunk: StoredChunk) -> None:
    """Mutate a chunk in place, reaching into its nested containers.

    `entity_ids` and `metadata` are the two mutable fields, and both have to
    be reached: a **shallow** copy passes an assignment to `text` and fails
    only here, which is the whole point of the isolation tests.
    """
    chunk.text = "__tampered__"
    chunk.entity_ids.append(uuid4())
    chunk.metadata["__tampered__"] = True
    for value in chunk.metadata.values():
        if isinstance(value, dict):
            value["__nested_tamper__"] = True
        elif isinstance(value, list):
            value.append("__nested_tamper__")


class ChunkStoreCompliance:
    """Tests every `ChunkStore` implementation must pass."""

    async def new_store(self) -> ChunkStore:
        """Return a fresh, empty store. Adapters override."""
        raise NotImplementedError

    async def dispose(self, store: ChunkStore) -> None:
        """Release whatever `new_store` acquired. No-op by default."""

    @asynccontextmanager
    async def _store(self) -> AsyncIterator[ChunkStore]:
        store = await self.new_store()
        try:
            yield store
        finally:
            await self.dispose(store)

    @pytest.fixture
    async def store(self) -> AsyncIterator[ChunkStore]:
        async with self._store() as store:
            yield store

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _chunk(
        tenant_id: TenantId,
        source_id: SourceId,
        text: str,
        *,
        chunk_index: int = 0,
        entity_ids: list[UUID] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StoredChunk:
        """A chunk with its real content-addressed id.

        The id is `chunk_id(source_id, text)` rather than an arbitrary string,
        because that is what every caller of this port will store and it is
        what makes two tenants collide on the same passage.
        """
        return StoredChunk(
            id=chunk_id(source_id, text),
            tenant_id=tenant_id,
            source_id=source_id,
            text=text,
            chunk_index=chunk_index,
            start_char=0,
            end_char=len(text),
            entity_ids=[] if entity_ids is None else entity_ids,
            metadata={} if metadata is None else metadata,
        )

    # ------------------------------------------------------------------
    # The port itself
    # ------------------------------------------------------------------

    async def test_satisfies_the_chunk_store_protocol(self, store: ChunkStore) -> None:
        assert isinstance(store, ChunkStore)

    # ------------------------------------------------------------------
    # Round-trip, and the empty answers
    # ------------------------------------------------------------------

    async def test_upsert_many_then_get_round_trips(self, store: ChunkStore) -> None:
        tenant = uuid4()
        entity = uuid4()
        written = self._chunk(
            tenant,
            "doc-1",
            "a passage",
            chunk_index=2,
            entity_ids=[entity],
            metadata={"section": "intro", "nested": {"k": [1, "two", None]}},
        )

        await store.upsert_many([written])

        found = await store.get(written.id, tenant)
        assert found == written

    async def test_get_returns_none_for_an_unknown_id(self, store: ChunkStore) -> None:
        assert await store.get(chunk_id("doc-1", "never stored"), uuid4()) is None

    async def test_get_by_source_returns_empty_for_an_unknown_source(
        self, store: ChunkStore
    ) -> None:
        tenant = uuid4()
        await store.upsert_many([self._chunk(tenant, "doc-1", "a")])

        assert await store.get_by_source("doc-2", tenant) == []

    async def test_upsert_many_with_no_items_is_not_an_error(self, store: ChunkStore) -> None:
        await store.upsert_many([])

    async def test_upsert_many_writes_chunks_of_different_tenants(self, store: ChunkStore) -> None:
        """Each element is keyed by *its own* `tenant_id`, not by a batch-wide one."""
        left, right = uuid4(), uuid4()
        theirs = self._chunk(left, "doc-1", "left text")
        ours = self._chunk(right, "doc-2", "right text")

        await store.upsert_many([theirs, ours])

        assert await store.get(theirs.id, left) == theirs
        assert await store.get(ours.id, right) == ours
        assert await store.get(theirs.id, right) is None
        assert await store.get(ours.id, left) is None

    async def test_upsert_many_is_last_write_wins_within_one_call(self, store: ChunkStore) -> None:
        """One statement cannot touch a row twice, so the batch is deduplicated.

        Postgres raises outright on a repeated key in one `ON CONFLICT`
        statement, so an adapter has to collapse duplicates itself -- and the
        rule must be the same last-write-wins one that applies across calls.
        The two elements share an id because they share `(source_id, text)`,
        which is exactly how a re-delivered event arrives.
        """
        tenant = uuid4()
        first = self._chunk(tenant, "doc-1", "same text", chunk_index=0, metadata={"n": 1})
        second = self._chunk(tenant, "doc-1", "same text", chunk_index=7, metadata={"n": 2})
        assert first.id == second.id

        await store.upsert_many([first, second])

        found = await store.get(first.id, tenant)
        assert found is not None
        assert found.metadata == {"n": 2}
        assert found.chunk_index == 7
        # One row, not two: `get_by_source` would show a duplicate the single
        # `get` above cannot.
        assert len(await store.get_by_source("doc-1", tenant)) == 1

    async def test_upsert_many_is_last_write_wins_across_calls(self, store: ChunkStore) -> None:
        tenant = uuid4()
        first = self._chunk(tenant, "doc-1", "same text", metadata={"n": 1})
        second = self._chunk(tenant, "doc-1", "same text", metadata={"n": 2})

        await store.upsert_many([first])
        await store.upsert_many([second])

        found = await store.get(first.id, tenant)
        assert found is not None
        assert found.metadata == {"n": 2}
        assert len(await store.get_by_source("doc-1", tenant)) == 1

    # ------------------------------------------------------------------
    # The composite key
    # ------------------------------------------------------------------

    async def test_two_tenants_hold_the_same_chunk_id_independently(
        self, store: ChunkStore
    ) -> None:
        """The composite-key row, and the one this port is most exposed to.

        Content addressing makes this collision *ordinary*: the same passage of
        the same source id under two tenants hashes identically. A
        `(tenant_id, id)` key compared on `id` alone is a live defect here, not
        the astronomically-unlikely one `uuid4()` makes it elsewhere.
        """
        left, right = uuid4(), uuid4()
        shared = chunk_id("doc-1", "shared passage")
        await store.upsert_many(
            [
                StoredChunk(
                    id=shared,
                    tenant_id=left,
                    source_id="doc-1",
                    text="shared passage",
                    chunk_index=0,
                    start_char=0,
                    end_char=14,
                    metadata={"owner": "left"},
                ),
                StoredChunk(
                    id=shared,
                    tenant_id=right,
                    source_id="doc-1",
                    text="shared passage",
                    chunk_index=0,
                    start_char=0,
                    end_char=14,
                    metadata={"owner": "right"},
                ),
            ]
        )

        under_left = await store.get(shared, left)
        under_right = await store.get(shared, right)
        assert under_left is not None
        assert under_right is not None
        assert under_left.metadata == {"owner": "left"}
        assert under_right.metadata == {"owner": "right"}

        # And a bulk delete of one tenant leaves the other's row alone -- an
        # adapter keyed on `id` alone removes both and reports 1 either way.
        assert await store.delete_by_tenant(left) == 1
        survivor = await store.get(shared, right)
        assert survivor is not None
        assert survivor.metadata == {"owner": "right"}

    # ------------------------------------------------------------------
    # `replace_source`
    # ------------------------------------------------------------------

    async def test_replace_source_removes_every_orphan_not_only_the_first(
        self, store: ChunkStore
    ) -> None:
        """The loop row.

        On a one-element remainder `break` and `continue` are the same
        function, so a single orphan proves nothing. There are **two**, with a
        surviving chunk written between them: an implementation that stops at
        the first deletion returns 1 and leaves the second orphan behind,
        which no assertion about the survivor could see.
        """
        tenant = uuid4()
        first_orphan = self._chunk(tenant, "doc-1", "orphan one", chunk_index=0)
        survivor = self._chunk(tenant, "doc-1", "survivor", chunk_index=1)
        second_orphan = self._chunk(tenant, "doc-1", "orphan two", chunk_index=2)
        await store.upsert_many([first_orphan, survivor, second_orphan])

        removed = await store.replace_source("doc-1", tenant, [survivor])

        assert removed == 2
        assert await store.get(first_orphan.id, tenant) is None
        assert await store.get(second_orphan.id, tenant) is None
        assert await store.get(survivor.id, tenant) == survivor
        assert [chunk.id for chunk in await store.get_by_source("doc-1", tenant)] == [survivor.id]

    async def test_replace_source_with_an_empty_set_empties_the_source(
        self, store: ChunkStore
    ) -> None:
        """`if not chunks: return 0` is the guard that looks defensive and is wrong.

        An empty chunking is a legal statement about the source -- it now has
        no chunks -- and an adapter treating it as "nothing to do" leaves the
        old passages readable forever.
        """
        tenant = uuid4()
        held = [
            self._chunk(tenant, "doc-1", "one", chunk_index=0),
            self._chunk(tenant, "doc-1", "two", chunk_index=1),
        ]
        await store.upsert_many(held)

        removed = await store.replace_source("doc-1", tenant, [])

        assert removed == 2
        assert await store.get_by_source("doc-1", tenant) == []
        for chunk in held:
            assert await store.get(chunk.id, tenant) is None

    async def test_replace_source_writes_a_source_that_never_existed(
        self, store: ChunkStore
    ) -> None:
        """The fixture row: at least one path starts from genuinely nothing.

        Every other case here replaces a chunking a previous write left
        behind, so setup that quietly did nothing would go unnoticed. This one
        calls `replace_source` first, on an empty store.
        """
        tenant = uuid4()
        fresh = [
            self._chunk(tenant, "doc-new", "first", chunk_index=0),
            self._chunk(tenant, "doc-new", "second", chunk_index=1),
        ]

        removed = await store.replace_source("doc-new", tenant, fresh)

        assert removed == 0
        assert [chunk.id for chunk in await store.get_by_source("doc-new", tenant)] == [
            fresh[0].id,
            fresh[1].id,
        ]

    async def test_replace_source_leaves_another_source_alone(self, store: ChunkStore) -> None:
        """Two sources under one tenant; replacing one must not touch the other."""
        tenant = uuid4()
        replaced = self._chunk(tenant, "doc-1", "old", chunk_index=0)
        untouched = self._chunk(tenant, "doc-2", "kept", chunk_index=0)
        await store.upsert_many([replaced, untouched])
        fresh = self._chunk(tenant, "doc-1", "new", chunk_index=0)

        removed = await store.replace_source("doc-1", tenant, [fresh])

        assert removed == 1
        assert await store.get(untouched.id, tenant) == untouched
        assert [chunk.id for chunk in await store.get_by_source("doc-2", tenant)] == [untouched.id]
        assert [chunk.id for chunk in await store.get_by_source("doc-1", tenant)] == [fresh.id]

    async def test_replace_source_leaves_another_tenant_alone(self, store: ChunkStore) -> None:
        """The same source id under two tenants is two chunkings.

        Content addressing means the *ids* also coincide, so an adapter whose
        delete is scoped to `source_id` alone wipes a second tenant's document
        and still reports the right count for the first.
        """
        ours, theirs = uuid4(), uuid4()
        our_old = self._chunk(ours, "doc-1", "old", chunk_index=0)
        their_copy = self._chunk(theirs, "doc-1", "old", chunk_index=0)
        assert our_old.id == their_copy.id
        await store.upsert_many([our_old, their_copy])
        fresh = self._chunk(ours, "doc-1", "new", chunk_index=0)

        removed = await store.replace_source("doc-1", ours, [fresh])

        assert removed == 1
        assert await store.get(their_copy.id, theirs) == their_copy
        assert [chunk.id for chunk in await store.get_by_source("doc-1", theirs)] == [their_copy.id]

    async def test_replace_source_returns_the_orphan_count_not_the_write_count(
        self, store: ChunkStore
    ) -> None:
        """A counter needs a test asserting it non-zero *and* distinguishable.

        Two chunks replaced by three, one of which is carried over: the answer
        is 1, and it differs from every other count in the call -- 2 held
        before, 3 held after, 2 written new. Four counters all summed to the
        same number cannot tell you which line was wired to which field.
        """
        tenant = uuid4()
        carried = self._chunk(tenant, "doc-1", "carried over", chunk_index=0)
        dropped = self._chunk(tenant, "doc-1", "dropped", chunk_index=1)
        await store.upsert_many([carried, dropped])
        added = [
            self._chunk(tenant, "doc-1", "added one", chunk_index=1),
            self._chunk(tenant, "doc-1", "added two", chunk_index=2),
        ]

        removed = await store.replace_source("doc-1", tenant, [carried, *added])

        assert removed == 1
        assert len(await store.get_by_source("doc-1", tenant)) == 3
        assert await store.get(dropped.id, tenant) is None

    async def test_replace_source_returns_zero_on_a_redelivery(self, store: ChunkStore) -> None:
        """A plain re-delivery of the same event removes nothing.

        This is the counter's other side, and it is what makes the number
        readable: a caller distinguishing "the document was re-chunked" from
        "the event arrived twice" has only this return value to do it with.
        """
        tenant = uuid4()
        chunks = [
            self._chunk(tenant, "doc-1", "one", chunk_index=0),
            self._chunk(tenant, "doc-1", "two", chunk_index=1),
        ]
        await store.replace_source("doc-1", tenant, chunks)

        assert await store.replace_source("doc-1", tenant, chunks) == 0
        assert len(await store.get_by_source("doc-1", tenant)) == 2

    async def test_replace_source_rejects_a_chunk_from_another_source(
        self, store: ChunkStore
    ) -> None:
        """Rewriting a chunk's provenance silently is how one document's
        entity links end up on another's passage. The port says `ValueError`.

        The stray is second, after a well-formed chunk, so an adapter
        validating only the first element fails here.
        """
        tenant = uuid4()
        good = self._chunk(tenant, "doc-1", "belongs here", chunk_index=0)
        stray = self._chunk(tenant, "doc-2", "belongs elsewhere", chunk_index=1)

        with pytest.raises(ValueError, match="doc-1"):
            await store.replace_source("doc-1", tenant, [good, stray])

        # Rejected before anything was written: validation precedes the write.
        assert await store.get(good.id, tenant) is None
        assert await store.get_by_source("doc-1", tenant) == []

    async def test_replace_source_rejects_a_chunk_from_another_tenant(
        self, store: ChunkStore
    ) -> None:
        """The other half of provenance, and the one that is a confidentiality
        bug rather than a correctness one.

        The stray carries the right `source_id` and the wrong `tenant_id`, so
        an adapter checking only the source accepts it and writes another
        tenant's passage under this one.
        """
        ours, theirs = uuid4(), uuid4()
        good = self._chunk(ours, "doc-1", "ours", chunk_index=0)
        stray = self._chunk(theirs, "doc-1", "theirs", chunk_index=1)

        with pytest.raises(ValueError, match=str(ours)):
            await store.replace_source("doc-1", ours, [good, stray])

        assert await store.get(good.id, ours) is None
        assert await store.get(stray.id, theirs) is None

    async def test_replace_source_rejecting_a_batch_deletes_nothing(
        self, store: ChunkStore
    ) -> None:
        """A rejected replacement is not a partial one.

        The obvious implementation deletes the orphans and *then* writes, so a
        provenance check placed after the delete empties the source and
        raises. `replace_source` is one operation; a caller retrying after
        `ValueError` must find the old chunking intact.
        """
        tenant = uuid4()
        held = self._chunk(tenant, "doc-1", "held", chunk_index=0)
        await store.upsert_many([held])
        stray = self._chunk(tenant, "doc-2", "stray", chunk_index=0)

        with pytest.raises(ValueError, match="doc-1"):
            await store.replace_source("doc-1", tenant, [stray])

        assert await store.get(held.id, tenant) == held

    # ------------------------------------------------------------------
    # Ordering
    # ------------------------------------------------------------------

    async def test_get_by_source_orders_by_chunk_index(self, store: ChunkStore) -> None:
        """Written out of order, read back in index order.

        **`chunk_index` 10 is here on purpose, and it is the whole test.**
        Every other index in this suite is a single digit, and on single
        digits a numeric sort and a lexical one are the same function -- so
        `sort(key=lambda c: (str(c.chunk_index), c.id))` passes without it.
        That is not a hypothetical mutant: an adapter storing `chunk_index` in
        a text column returns chunk 10 before chunk 2, which is a real
        Postgres schema mistake and a silently reordered document.
        """
        tenant = uuid4()
        indices = [0, 1, 2, 3, 4, 10]
        chunks = [self._chunk(tenant, "doc-1", f"passage {i}", chunk_index=i) for i in indices]
        await store.upsert_many([chunks[5], chunks[3], chunks[0], chunks[4], chunks[1], chunks[2]])

        found = await store.get_by_source("doc-1", tenant)

        assert [chunk.chunk_index for chunk in found] == indices
        assert [chunk.id for chunk in found] == [chunk.id for chunk in chunks]

    async def test_get_by_source_orders_two_chunks_sharing_an_index_by_id(
        self, store: ChunkStore
    ) -> None:
        """Ties that never coincide are the failure shape this repo hit twice.

        The tie-break exists for exactly this input, so a test must produce
        it: two chunks share `chunk_index=3`, and a third takes index 0 while
        holding the id that sorts *last*. That third chunk is what stops a
        sort on `id` alone passing -- with only the tie present, ordering by
        id happens to give the same answer.

        The two tied chunks are written **higher id first**, because a stable
        sort on `chunk_index` alone preserves insertion order and would
        otherwise agree with the contract by accident.

        The texts are chosen so that the tied pair sorts by `id` in the
        *opposite* order to how it sorts by `text`. Ids are hashes of the
        text, so any pair picked for readability sorts about half the time the
        same way its texts do -- and while it does,
        `sort(key=(chunk_index, text))` is indistinguishable from the
        contract. Pinning the tie-break to a *field* takes a case where the
        candidate fields disagree.
        """
        tenant = uuid4()
        # Ids ascend gamma < alpha < beta; texts ascend alpha < beta < gamma.
        low_tie = self._chunk(tenant, "doc-1", "passage gamma", chunk_index=3)
        high_tie = self._chunk(tenant, "doc-1", "passage alpha", chunk_index=3)
        # The largest id takes index 0, so `(chunk_index, id)` and `id` alone
        # disagree; the two smaller ids share index 3.
        leader = self._chunk(tenant, "doc-1", "passage beta", chunk_index=0)
        assert low_tie.id < high_tie.id < leader.id
        # ... and the tied pair's texts run the other way, which is what makes
        # `text` a distinguishable wrong answer rather than a coincident one.
        assert high_tie.text < low_tie.text

        await store.upsert_many([high_tie, low_tie, leader])

        found = await store.get_by_source("doc-1", tenant)

        assert [chunk.id for chunk in found] == [leader.id, low_tie.id, high_tie.id]

    # ------------------------------------------------------------------
    # Tenant isolation
    # ------------------------------------------------------------------

    async def test_get_never_crosses_tenants(self, store: ChunkStore) -> None:
        """The **same chunk id** under two tenants, so a leak cannot hide
        behind ids that differ anyway -- and here they genuinely coincide."""
        ours, theirs, stranger = uuid4(), uuid4(), uuid4()
        mine = self._chunk(ours, "doc-1", "one passage", metadata={"owner": "ours"})
        yours = self._chunk(theirs, "doc-1", "one passage", metadata={"owner": "theirs"})
        await store.upsert_many([mine, yours])

        under_ours = await store.get(mine.id, ours)
        assert under_ours is not None
        assert under_ours.metadata == {"owner": "ours"}
        assert under_ours.tenant_id == ours
        # A third tenant, which stored nothing, sees nothing.
        assert await store.get(mine.id, stranger) is None

    async def test_get_by_source_never_crosses_tenants(self, store: ChunkStore) -> None:
        ours, theirs, stranger = uuid4(), uuid4(), uuid4()
        mine = self._chunk(ours, "doc-1", "ours only", chunk_index=0)
        yours = [
            self._chunk(theirs, "doc-1", "theirs one", chunk_index=0),
            self._chunk(theirs, "doc-1", "theirs two", chunk_index=1),
        ]
        await store.upsert_many([mine, *yours])

        assert [chunk.id for chunk in await store.get_by_source("doc-1", ours)] == [mine.id]
        assert {chunk.id for chunk in await store.get_by_source("doc-1", theirs)} == {
            chunk.id for chunk in yours
        }
        assert await store.get_by_source("doc-1", stranger) == []

    # ------------------------------------------------------------------
    # Mutation isolation
    # ------------------------------------------------------------------

    async def test_get_returns_copies(self, store: ChunkStore) -> None:
        """Mutate the result -- including appending to `entity_ids` -- and re-read.

        A shallow copy leaves `entity_ids` shared and passes every behavioural
        assertion, because handing back the stored object is correct on the
        read and wrong only afterwards.
        """
        tenant = uuid4()
        written = self._chunk(
            tenant,
            "doc-1",
            "a passage",
            entity_ids=[uuid4()],
            metadata={"nested": {"k": "v"}, "list": ["a"]},
        )
        pristine = written.model_copy(deep=True)
        await store.upsert_many([written])

        first = await store.get(written.id, tenant)
        assert first is not None
        _mutate(first)

        assert await store.get(written.id, tenant) == pristine

    async def test_get_by_source_returns_copies(self, store: ChunkStore) -> None:
        tenant = uuid4()
        written = self._chunk(
            tenant,
            "doc-1",
            "a passage",
            entity_ids=[uuid4()],
            metadata={"nested": {"k": "v"}, "list": ["a"]},
        )
        pristine = written.model_copy(deep=True)
        await store.upsert_many([written])

        for chunk in await store.get_by_source("doc-1", tenant):
            _mutate(chunk)

        assert await store.get_by_source("doc-1", tenant) == [pristine]
        assert await store.get(written.id, tenant) == pristine

    async def test_mutating_the_argument_after_a_write_does_not_change_the_store(
        self, store: ChunkStore
    ) -> None:
        """The other direction: the store must not keep the caller's objects."""
        tenant = uuid4()
        written = self._chunk(
            tenant,
            "doc-1",
            "a passage",
            entity_ids=[uuid4()],
            metadata={"nested": {"k": "v"}},
        )
        pristine = written.model_copy(deep=True)
        await store.upsert_many([written])

        _mutate(written)

        assert await store.get(pristine.id, tenant) == pristine

    async def test_mutating_the_argument_after_replace_source_does_not_change_the_store(
        self, store: ChunkStore
    ) -> None:
        """`replace_source` writes too, and it is a separate code path."""
        tenant = uuid4()
        written = self._chunk(
            tenant, "doc-1", "a passage", entity_ids=[uuid4()], metadata={"nested": {"k": "v"}}
        )
        pristine = written.model_copy(deep=True)
        await store.replace_source("doc-1", tenant, [written])

        _mutate(written)

        assert await store.get(pristine.id, tenant) == pristine

    # ------------------------------------------------------------------
    # Deletion
    # ------------------------------------------------------------------

    async def test_delete_by_source_removes_that_source_and_counts_it(
        self, store: ChunkStore
    ) -> None:
        tenant = uuid4()
        doomed = [
            self._chunk(tenant, "doc-1", "one", chunk_index=0),
            self._chunk(tenant, "doc-1", "two", chunk_index=1),
        ]
        spared = self._chunk(tenant, "doc-2", "kept", chunk_index=0)
        await store.upsert_many([*doomed, spared])

        assert await store.delete_by_source("doc-1", tenant) == 2
        assert await store.get_by_source("doc-1", tenant) == []
        for chunk in doomed:
            assert await store.get(chunk.id, tenant) is None
        assert await store.get(spared.id, tenant) == spared

    async def test_delete_by_source_is_idempotent_on_an_unknown_source(
        self, store: ChunkStore
    ) -> None:
        """Replaying a delete removes nothing and is not an error.

        Both flavours of "unknown": a source this tenant never held, and a
        source it held until the previous line.
        """
        tenant = uuid4()
        held = self._chunk(tenant, "doc-1", "one", chunk_index=0)
        await store.upsert_many([held])

        assert await store.delete_by_source("doc-never", tenant) == 0
        assert await store.delete_by_source("doc-1", tenant) == 1
        assert await store.delete_by_source("doc-1", tenant) == 0
        # The store is still usable, and the unknown-tenant case is 0 too.
        assert await store.delete_by_source("doc-1", uuid4()) == 0

    async def test_delete_by_source_never_crosses_tenants(self, store: ChunkStore) -> None:
        """Same source id, same chunk ids, two tenants."""
        ours, theirs = uuid4(), uuid4()
        mine = self._chunk(ours, "doc-1", "one passage")
        yours = self._chunk(theirs, "doc-1", "one passage")
        assert mine.id == yours.id
        await store.upsert_many([mine, yours])

        assert await store.delete_by_source("doc-1", ours) == 1

        assert await store.get(yours.id, theirs) == yours

    async def test_delete_by_tenant_touches_no_other_tenant(self, store: ChunkStore) -> None:
        doomed, spared = uuid4(), uuid4()
        # **Three chunks over two sources**, so the count cannot be mistaken
        # for a count of distinct sources -- with one chunk per source the two
        # numbers agree, which is `recurring-defects.md` §3's "four counters
        # summed to the same number".
        theirs = [
            self._chunk(doomed, "doc-1", "one", chunk_index=0),
            self._chunk(doomed, "doc-1", "one more", chunk_index=1),
            self._chunk(doomed, "doc-2", "two", chunk_index=0),
        ]
        ours = [
            self._chunk(spared, "doc-1", "one", chunk_index=0),
            self._chunk(spared, "doc-3", "three", chunk_index=0),
        ]
        await store.upsert_many([*theirs, *ours])

        assert await store.delete_by_tenant(doomed) == 3

        for chunk in theirs:
            assert await store.get(chunk.id, doomed) is None
        assert await store.get_by_source("doc-1", doomed) == []
        for chunk in ours:
            assert await store.get(chunk.id, spared) == chunk
        assert [chunk.id for chunk in await store.get_by_source("doc-1", spared)] == [ours[0].id]

    async def test_delete_by_tenant_on_an_unknown_tenant_removes_nothing(
        self, store: ChunkStore
    ) -> None:
        tenant = uuid4()
        await store.upsert_many([self._chunk(tenant, "doc-1", "one")])

        assert await store.delete_by_tenant(uuid4()) == 0
        assert await store.delete_by_tenant(tenant) == 1
        assert await store.delete_by_tenant(tenant) == 0
