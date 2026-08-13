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
            return InMemoryChunkStore(dimension=self.DIMENSION)

`new_store` must return an **empty** store, and each call must return one
isolated from every other. A store built for `semantic_candidates` must be
built at width `DIMENSION` -- a plain class attribute, `4` here, read through
`self` inside each semantic test the way `VectorStoreCompliance.DIMENSION`
already is; an adapter whose backing store takes a configurable width
overrides it. `dispose` is a no-op by default and must be
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
from uuid import uuid4

import pytest

from redstring.domain.chunk import StoredChunk, chunk_id
from redstring.domain.chunk_ranking import rank_chunks
from redstring.domain.exceptions import DimensionMismatchError
from redstring.domain.ids import EntityId, SourceId, TenantId
from redstring.domain.vector import cosine_score
from redstring.ports.chunk_store import ChunkStore

SCORE_TOLERANCE = 1e-5

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _mutate(chunk: StoredChunk) -> None:
    """Mutate a chunk in place, reaching into its nested containers.

    `entity_ids` and `metadata` are the two mutable fields, and both have to
    be reached: a **shallow** copy passes an assignment to `text` and fails
    only here, which is the whole point of the isolation tests.
    """
    chunk.text = "__tampered__"
    chunk.entity_ids.append(EntityId(uuid4()))
    chunk.metadata["__tampered__"] = True
    for value in chunk.metadata.values():
        if isinstance(value, dict):
            value["__nested_tamper__"] = True
        elif isinstance(value, list):
            value.append("__nested_tamper__")


class ChunkStoreCompliance:
    """Tests every `ChunkStore` implementation must pass."""

    #: Width `new_store` must build its store at. `semantic_candidates` cases
    #: draw vectors of exactly this length; an adapter with a configurable
    #: embedding dimension overrides it, the same shape as
    #: `VectorStoreCompliance.DIMENSION`.
    DIMENSION = 4

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
        source_id: str,
        text: str,
        *,
        chunk_index: int = 0,
        entity_ids: list[EntityId] | None = None,
        metadata: dict[str, Any] | None = None,
        embedding: list[float] | None = None,
    ) -> StoredChunk:
        """A chunk with its real content-addressed id.

        The id is `chunk_id(source_id, text)` rather than an arbitrary string,
        because that is what every caller of this port will store and it is
        what makes two tenants collide on the same passage.
        """
        # `source_id` is taken as `str` and named here, once. Callers below
        # write `"doc-1"` a hundred times over; wrapping at each of them would
        # be ceremony, and this helper is the boundary where a test fixture
        # becomes a domain object -- the same place the adapters name theirs.
        source = SourceId(source_id)
        return StoredChunk(
            id=chunk_id(source, text),
            tenant_id=tenant_id,
            source_id=source,
            text=text,
            chunk_index=chunk_index,
            start_char=0,
            end_char=len(text),
            entity_ids=[] if entity_ids is None else entity_ids,
            metadata={} if metadata is None else metadata,
            embedding=embedding,
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
        tenant = TenantId(uuid4())
        entity = EntityId(uuid4())
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
        assert (
            await store.get(chunk_id(SourceId("doc-1"), "never stored"), TenantId(uuid4())) is None
        )

    async def test_get_by_source_returns_empty_for_an_unknown_source(
        self, store: ChunkStore
    ) -> None:
        tenant = TenantId(uuid4())
        await store.upsert_many([self._chunk(tenant, "doc-1", "a")])

        assert await store.get_by_source(SourceId("doc-2"), tenant) == []

    async def test_upsert_many_with_no_items_is_not_an_error(self, store: ChunkStore) -> None:
        await store.upsert_many([])

    async def test_upsert_many_writes_chunks_of_different_tenants(self, store: ChunkStore) -> None:
        """Each element is keyed by *its own* `tenant_id`, not by a batch-wide one."""
        left, right = TenantId(uuid4()), TenantId(uuid4())
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
        tenant = TenantId(uuid4())
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
        assert len(await store.get_by_source(SourceId("doc-1"), tenant)) == 1

    async def test_upsert_many_is_last_write_wins_across_calls(self, store: ChunkStore) -> None:
        tenant = TenantId(uuid4())
        first = self._chunk(tenant, "doc-1", "same text", metadata={"n": 1})
        second = self._chunk(tenant, "doc-1", "same text", metadata={"n": 2})

        await store.upsert_many([first])
        await store.upsert_many([second])

        found = await store.get(first.id, tenant)
        assert found is not None
        assert found.metadata == {"n": 2}
        assert len(await store.get_by_source(SourceId("doc-1"), tenant)) == 1

    async def test_upsert_many_rejects_a_zero_norm_embedding(self, store: ChunkStore) -> None:
        """Cosine is undefined at zero magnitude; the port rejects the write
        rather than let `semantic_candidates` discover it later as a NaN or a
        crash, matching `InMemoryVectorStore.upsert`.

        The zero vector is second, after a well-formed chunk, so an adapter
        validating only the first element fails here -- the same shape as
        the provenance-rejection tests for `replace_source`.
        """
        tenant = TenantId(uuid4())
        fine = self._chunk(tenant, "doc-1", "has a real vector", embedding=[1.0, 0.0, 0.0, 0.0])
        zero = self._chunk(
            tenant, "doc-1", "zero norm", chunk_index=1, embedding=[0.0, 0.0, 0.0, 0.0]
        )

        with pytest.raises(ValueError, match="zero"):
            await store.upsert_many([fine, zero])

        # Rejected before anything was written: the whole batch, not just the
        # offending element.
        assert await store.get(fine.id, tenant) is None
        assert await store.get(zero.id, tenant) is None

    async def test_upsert_many_rejects_a_stored_embedding_of_the_wrong_width(
        self, store: ChunkStore
    ) -> None:
        """A stored `embedding` must match the store's `dimension`, at write.

        Left unchecked this is a silent divergence between adapters rather
        than a loud one: nothing validated width on write before this case
        existed, so `InMemoryChunkStore` accepted a mis-sized vector and
        only failed later, from inside `semantic_candidates`, with a bare
        `ValueError` from `zip(..., strict=True)` that named neither the
        expected nor the actual width, while `PostgresChunkStore` rejected
        the write itself with a driver-specific `DataError`. Both adapters
        now raise the same `DimensionMismatchError` at the same point in the
        call -- the type `semantic_candidates` already raises for a
        wrong-width *query* vector, so a caller has one type to catch for
        one kind of mistake.

        The narrow vector is second, after a well-formed chunk, matching
        `test_upsert_many_rejects_a_zero_norm_embedding`'s shape: an adapter
        validating only the first element fails here.
        """
        tenant = TenantId(uuid4())
        fine = self._chunk(tenant, "doc-1", "has the right width", embedding=[1.0, 0.0, 0.0, 0.0])
        narrow = self._chunk(
            tenant, "doc-1", "too narrow", chunk_index=1, embedding=[1.0, 0.0, 0.0]
        )
        assert narrow.embedding is not None
        assert len(narrow.embedding) != self.DIMENSION

        with pytest.raises(DimensionMismatchError) as raised:
            await store.upsert_many([fine, narrow])

        assert raised.value.expected == self.DIMENSION
        assert raised.value.actual == len(narrow.embedding)
        # Rejected before anything was written: the whole batch, not just the
        # offending element.
        assert await store.get(fine.id, tenant) is None
        assert await store.get(narrow.id, tenant) is None

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
        left, right = TenantId(uuid4()), TenantId(uuid4())
        shared = chunk_id(SourceId("doc-1"), "shared passage")
        await store.upsert_many(
            [
                StoredChunk(
                    id=shared,
                    tenant_id=left,
                    source_id=SourceId("doc-1"),
                    text="shared passage",
                    chunk_index=0,
                    start_char=0,
                    end_char=14,
                    metadata={"owner": "left"},
                ),
                StoredChunk(
                    id=shared,
                    tenant_id=right,
                    source_id=SourceId("doc-1"),
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
        tenant = TenantId(uuid4())
        first_orphan = self._chunk(tenant, "doc-1", "orphan one", chunk_index=0)
        survivor = self._chunk(tenant, "doc-1", "survivor", chunk_index=1)
        second_orphan = self._chunk(tenant, "doc-1", "orphan two", chunk_index=2)
        await store.upsert_many([first_orphan, survivor, second_orphan])

        removed = await store.replace_source(SourceId("doc-1"), tenant, [survivor])

        assert removed == 2
        assert await store.get(first_orphan.id, tenant) is None
        assert await store.get(second_orphan.id, tenant) is None
        assert await store.get(survivor.id, tenant) == survivor
        assert [chunk.id for chunk in await store.get_by_source(SourceId("doc-1"), tenant)] == [
            survivor.id
        ]

    async def test_replace_source_leaves_no_orphan_in_the_term_index(
        self, store: ChunkStore
    ) -> None:
        """A term unique to a replaced chunk must stop being counted.

        `get`/`get_by_source` cannot see a stale term-index row at all -- a
        row an adapter forgot to clean up after `replace_source` never
        surfaces through either method, since both read the chunk table only.
        `lexical_candidates`'s document frequency is the one place a leftover
        row is visible: if the term index still carries a row for a chunk
        that `replace_source` removed, `doc_frequencies` keeps counting it
        even though the chunk itself is gone -- exactly the shape `ON DELETE
        CASCADE` exists to prevent.
        """
        tenant = TenantId(uuid4())
        replaced = self._chunk(tenant, "doc-1", "orphanterm passage", chunk_index=0)
        await store.upsert_many([replaced])
        assert (await store.lexical_candidates(["orphanterm"], tenant, 10)).stats.doc_frequencies[
            "orphanterm"
        ] == 1

        survivor = self._chunk(tenant, "doc-1", "replacement text", chunk_index=0)
        removed = await store.replace_source(SourceId("doc-1"), tenant, [survivor])
        assert removed == 1

        result = await store.lexical_candidates(["orphanterm"], tenant, 10)
        assert result.stats.doc_frequencies["orphanterm"] == 0
        assert result.candidates == []

    async def test_replace_source_with_an_empty_set_empties_the_source(
        self, store: ChunkStore
    ) -> None:
        """`if not chunks: return 0` is the guard that looks defensive and is wrong.

        An empty chunking is a legal statement about the source -- it now has
        no chunks -- and an adapter treating it as "nothing to do" leaves the
        old passages readable forever.
        """
        tenant = TenantId(uuid4())
        held = [
            self._chunk(tenant, "doc-1", "one", chunk_index=0),
            self._chunk(tenant, "doc-1", "two", chunk_index=1),
        ]
        await store.upsert_many(held)

        removed = await store.replace_source(SourceId("doc-1"), tenant, [])

        assert removed == 2
        assert await store.get_by_source(SourceId("doc-1"), tenant) == []
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
        tenant = TenantId(uuid4())
        fresh = [
            self._chunk(tenant, "doc-new", "first", chunk_index=0),
            self._chunk(tenant, "doc-new", "second", chunk_index=1),
        ]

        removed = await store.replace_source(SourceId("doc-new"), tenant, fresh)

        assert removed == 0
        assert [chunk.id for chunk in await store.get_by_source(SourceId("doc-new"), tenant)] == [
            fresh[0].id,
            fresh[1].id,
        ]

    async def test_replace_source_leaves_another_source_alone(self, store: ChunkStore) -> None:
        """Two sources under one tenant; replacing one must not touch the other."""
        tenant = TenantId(uuid4())
        replaced = self._chunk(tenant, "doc-1", "old", chunk_index=0)
        untouched = self._chunk(tenant, "doc-2", "kept", chunk_index=0)
        await store.upsert_many([replaced, untouched])
        fresh = self._chunk(tenant, "doc-1", "new", chunk_index=0)

        removed = await store.replace_source(SourceId("doc-1"), tenant, [fresh])

        assert removed == 1
        assert await store.get(untouched.id, tenant) == untouched
        assert [chunk.id for chunk in await store.get_by_source(SourceId("doc-2"), tenant)] == [
            untouched.id
        ]
        assert [chunk.id for chunk in await store.get_by_source(SourceId("doc-1"), tenant)] == [
            fresh.id
        ]

    async def test_replace_source_leaves_another_tenant_alone(self, store: ChunkStore) -> None:
        """The same source id under two tenants is two chunkings.

        Content addressing means the *ids* also coincide, so an adapter whose
        delete is scoped to `source_id` alone wipes a second tenant's document
        and still reports the right count for the first.
        """
        ours, theirs = TenantId(uuid4()), TenantId(uuid4())
        our_old = self._chunk(ours, "doc-1", "old", chunk_index=0)
        their_copy = self._chunk(theirs, "doc-1", "old", chunk_index=0)
        assert our_old.id == their_copy.id
        await store.upsert_many([our_old, their_copy])
        fresh = self._chunk(ours, "doc-1", "new", chunk_index=0)

        removed = await store.replace_source(SourceId("doc-1"), ours, [fresh])

        assert removed == 1
        assert await store.get(their_copy.id, theirs) == their_copy
        assert [chunk.id for chunk in await store.get_by_source(SourceId("doc-1"), theirs)] == [
            their_copy.id
        ]

    async def test_replace_source_returns_the_orphan_count_not_the_write_count(
        self, store: ChunkStore
    ) -> None:
        """A counter needs a test asserting it non-zero *and* distinguishable.

        Two chunks replaced by three, one of which is carried over: the answer
        is 1, and it differs from every other count in the call -- 2 held
        before, 3 held after, 2 written new. Four counters all summed to the
        same number cannot tell you which line was wired to which field.
        """
        tenant = TenantId(uuid4())
        carried = self._chunk(tenant, "doc-1", "carried over", chunk_index=0)
        dropped = self._chunk(tenant, "doc-1", "dropped", chunk_index=1)
        await store.upsert_many([carried, dropped])
        added = [
            self._chunk(tenant, "doc-1", "added one", chunk_index=1),
            self._chunk(tenant, "doc-1", "added two", chunk_index=2),
        ]

        removed = await store.replace_source(SourceId("doc-1"), tenant, [carried, *added])

        assert removed == 1
        assert len(await store.get_by_source(SourceId("doc-1"), tenant)) == 3
        assert await store.get(dropped.id, tenant) is None

    async def test_replace_source_returns_zero_on_a_redelivery(self, store: ChunkStore) -> None:
        """A plain re-delivery of the same event removes nothing.

        This is the counter's other side, and it is what makes the number
        readable: a caller distinguishing "the document was re-chunked" from
        "the event arrived twice" has only this return value to do it with.
        """
        tenant = TenantId(uuid4())
        chunks = [
            self._chunk(tenant, "doc-1", "one", chunk_index=0),
            self._chunk(tenant, "doc-1", "two", chunk_index=1),
        ]
        await store.replace_source(SourceId("doc-1"), tenant, chunks)

        assert await store.replace_source(SourceId("doc-1"), tenant, chunks) == 0
        assert len(await store.get_by_source(SourceId("doc-1"), tenant)) == 2

    async def test_replace_source_rejects_a_chunk_from_another_source(
        self, store: ChunkStore
    ) -> None:
        """Rewriting a chunk's provenance silently is how one document's
        entity links end up on another's passage. The port says `ValueError`.

        The stray is second, after a well-formed chunk, so an adapter
        validating only the first element fails here.
        """
        tenant = TenantId(uuid4())
        good = self._chunk(tenant, "doc-1", "belongs here", chunk_index=0)
        stray = self._chunk(tenant, "doc-2", "belongs elsewhere", chunk_index=1)

        with pytest.raises(ValueError, match="doc-1"):
            await store.replace_source(SourceId("doc-1"), tenant, [good, stray])

        # Rejected before anything was written: validation precedes the write.
        assert await store.get(good.id, tenant) is None
        assert await store.get_by_source(SourceId("doc-1"), tenant) == []

    async def test_replace_source_rejects_a_chunk_from_another_tenant(
        self, store: ChunkStore
    ) -> None:
        """The other half of provenance, and the one that is a confidentiality
        bug rather than a correctness one.

        The stray carries the right `source_id` and the wrong `tenant_id`, so
        an adapter checking only the source accepts it and writes another
        tenant's passage under this one.
        """
        ours, theirs = TenantId(uuid4()), TenantId(uuid4())
        good = self._chunk(ours, "doc-1", "ours", chunk_index=0)
        stray = self._chunk(theirs, "doc-1", "theirs", chunk_index=1)

        with pytest.raises(ValueError, match=str(ours)):
            await store.replace_source(SourceId("doc-1"), ours, [good, stray])

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
        tenant = TenantId(uuid4())
        held = self._chunk(tenant, "doc-1", "held", chunk_index=0)
        await store.upsert_many([held])
        stray = self._chunk(tenant, "doc-2", "stray", chunk_index=0)

        with pytest.raises(ValueError, match="doc-1"):
            await store.replace_source(SourceId("doc-1"), tenant, [stray])

        assert await store.get(held.id, tenant) == held

    async def test_replace_source_rejects_a_zero_norm_embedding(self, store: ChunkStore) -> None:
        """The same write-time guard `upsert_many` states applies to
        `replace_source`'s elements too -- it is the same write path."""
        tenant = TenantId(uuid4())
        held = self._chunk(tenant, "doc-1", "held", chunk_index=0, embedding=[1.0, 0.0, 0.0, 0.0])
        await store.upsert_many([held])
        zero = self._chunk(
            tenant, "doc-1", "zero norm", chunk_index=1, embedding=[0.0, 0.0, 0.0, 0.0]
        )

        with pytest.raises(ValueError, match="zero"):
            await store.replace_source(SourceId("doc-1"), tenant, [zero])

        # Rejected: the old chunking is untouched, exactly as a provenance
        # rejection leaves it.
        assert await store.get(held.id, tenant) == held

    async def test_replace_source_rejects_a_stored_embedding_of_the_wrong_width(
        self, store: ChunkStore
    ) -> None:
        """The same width guard `upsert_many` states applies to
        `replace_source`'s elements too -- it is the same write path."""
        tenant = TenantId(uuid4())
        held = self._chunk(tenant, "doc-1", "held", chunk_index=0, embedding=[1.0, 0.0, 0.0, 0.0])
        await store.upsert_many([held])
        narrow = self._chunk(
            tenant, "doc-1", "too narrow", chunk_index=1, embedding=[1.0, 0.0, 0.0]
        )
        assert narrow.embedding is not None
        assert len(narrow.embedding) != self.DIMENSION

        with pytest.raises(DimensionMismatchError) as raised:
            await store.replace_source(SourceId("doc-1"), tenant, [narrow])

        assert raised.value.expected == self.DIMENSION
        assert raised.value.actual == len(narrow.embedding)
        # Rejected: the old chunking is untouched, exactly as a provenance
        # rejection leaves it.
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
        tenant = TenantId(uuid4())
        indices = [0, 1, 2, 3, 4, 10]
        chunks = [self._chunk(tenant, "doc-1", f"passage {i}", chunk_index=i) for i in indices]
        await store.upsert_many([chunks[5], chunks[3], chunks[0], chunks[4], chunks[1], chunks[2]])

        found = await store.get_by_source(SourceId("doc-1"), tenant)

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
        tenant = TenantId(uuid4())
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

        found = await store.get_by_source(SourceId("doc-1"), tenant)

        assert [chunk.id for chunk in found] == [leader.id, low_tie.id, high_tie.id]

    # ------------------------------------------------------------------
    # Tenant isolation
    # ------------------------------------------------------------------

    async def test_get_never_crosses_tenants(self, store: ChunkStore) -> None:
        """The **same chunk id** under two tenants, so a leak cannot hide
        behind ids that differ anyway -- and here they genuinely coincide."""
        ours, theirs, stranger = TenantId(uuid4()), TenantId(uuid4()), TenantId(uuid4())
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
        ours, theirs, stranger = TenantId(uuid4()), TenantId(uuid4()), TenantId(uuid4())
        mine = self._chunk(ours, "doc-1", "ours only", chunk_index=0)
        yours = [
            self._chunk(theirs, "doc-1", "theirs one", chunk_index=0),
            self._chunk(theirs, "doc-1", "theirs two", chunk_index=1),
        ]
        await store.upsert_many([mine, *yours])

        assert [chunk.id for chunk in await store.get_by_source(SourceId("doc-1"), ours)] == [
            mine.id
        ]
        assert {chunk.id for chunk in await store.get_by_source(SourceId("doc-1"), theirs)} == {
            chunk.id for chunk in yours
        }
        assert await store.get_by_source(SourceId("doc-1"), stranger) == []

    # ------------------------------------------------------------------
    # Mutation isolation
    # ------------------------------------------------------------------

    async def test_get_returns_copies(self, store: ChunkStore) -> None:
        """Mutate the result -- including appending to `entity_ids` -- and re-read.

        A shallow copy leaves `entity_ids` shared and passes every behavioural
        assertion, because handing back the stored object is correct on the
        read and wrong only afterwards.
        """
        tenant = TenantId(uuid4())
        written = self._chunk(
            tenant,
            "doc-1",
            "a passage",
            entity_ids=[EntityId(uuid4())],
            metadata={"nested": {"k": "v"}, "list": ["a"]},
        )
        pristine = written.model_copy(deep=True)
        await store.upsert_many([written])

        first = await store.get(written.id, tenant)
        assert first is not None
        _mutate(first)

        assert await store.get(written.id, tenant) == pristine

    async def test_get_by_source_returns_copies(self, store: ChunkStore) -> None:
        tenant = TenantId(uuid4())
        written = self._chunk(
            tenant,
            "doc-1",
            "a passage",
            entity_ids=[EntityId(uuid4())],
            metadata={"nested": {"k": "v"}, "list": ["a"]},
        )
        pristine = written.model_copy(deep=True)
        await store.upsert_many([written])

        for chunk in await store.get_by_source(SourceId("doc-1"), tenant):
            _mutate(chunk)

        assert await store.get_by_source(SourceId("doc-1"), tenant) == [pristine]
        assert await store.get(written.id, tenant) == pristine

    async def test_mutating_the_argument_after_a_write_does_not_change_the_store(
        self, store: ChunkStore
    ) -> None:
        """The other direction: the store must not keep the caller's objects."""
        tenant = TenantId(uuid4())
        written = self._chunk(
            tenant,
            "doc-1",
            "a passage",
            entity_ids=[EntityId(uuid4())],
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
        tenant = TenantId(uuid4())
        written = self._chunk(
            tenant,
            "doc-1",
            "a passage",
            entity_ids=[EntityId(uuid4())],
            metadata={"nested": {"k": "v"}},
        )
        pristine = written.model_copy(deep=True)
        await store.replace_source(SourceId("doc-1"), tenant, [written])

        _mutate(written)

        assert await store.get(pristine.id, tenant) == pristine

    # ------------------------------------------------------------------
    # Deletion
    # ------------------------------------------------------------------

    async def test_delete_by_source_removes_that_source_and_counts_it(
        self, store: ChunkStore
    ) -> None:
        tenant = TenantId(uuid4())
        doomed = [
            self._chunk(tenant, "doc-1", "one", chunk_index=0),
            self._chunk(tenant, "doc-1", "two", chunk_index=1),
        ]
        spared = self._chunk(tenant, "doc-2", "kept", chunk_index=0)
        await store.upsert_many([*doomed, spared])

        assert await store.delete_by_source(SourceId("doc-1"), tenant) == 2
        assert await store.get_by_source(SourceId("doc-1"), tenant) == []
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
        tenant = TenantId(uuid4())
        held = self._chunk(tenant, "doc-1", "one", chunk_index=0)
        await store.upsert_many([held])

        assert await store.delete_by_source(SourceId("doc-never"), tenant) == 0
        assert await store.delete_by_source(SourceId("doc-1"), tenant) == 1
        assert await store.delete_by_source(SourceId("doc-1"), tenant) == 0
        # The store is still usable, and the unknown-tenant case is 0 too.
        assert await store.delete_by_source(SourceId("doc-1"), TenantId(uuid4())) == 0

    async def test_delete_by_source_never_crosses_tenants(self, store: ChunkStore) -> None:
        """Same source id, same chunk ids, two tenants."""
        ours, theirs = TenantId(uuid4()), TenantId(uuid4())
        mine = self._chunk(ours, "doc-1", "one passage")
        yours = self._chunk(theirs, "doc-1", "one passage")
        assert mine.id == yours.id
        await store.upsert_many([mine, yours])

        assert await store.delete_by_source(SourceId("doc-1"), ours) == 1

        assert await store.get(yours.id, theirs) == yours

    async def test_delete_by_tenant_touches_no_other_tenant(self, store: ChunkStore) -> None:
        doomed, spared = TenantId(uuid4()), TenantId(uuid4())
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
        assert await store.get_by_source(SourceId("doc-1"), doomed) == []
        for chunk in ours:
            assert await store.get(chunk.id, spared) == chunk
        assert [chunk.id for chunk in await store.get_by_source(SourceId("doc-1"), spared)] == [
            ours[0].id
        ]

    async def test_delete_by_tenant_on_an_unknown_tenant_removes_nothing(
        self, store: ChunkStore
    ) -> None:
        tenant = TenantId(uuid4())
        await store.upsert_many([self._chunk(tenant, "doc-1", "one")])

        assert await store.delete_by_tenant(TenantId(uuid4())) == 0
        assert await store.delete_by_tenant(tenant) == 1
        assert await store.delete_by_tenant(tenant) == 0

    # ------------------------------------------------------------------
    # `lexical_candidates`
    # ------------------------------------------------------------------

    async def _corpus(self, store: ChunkStore, tenant: TenantId) -> list[StoredChunk]:
        """Four chunks whose term statistics genuinely differ.

        `common` is in every chunk, `rare` in one, so IDF has something to
        distinguish. Lengths differ (4, 3, 5, 1 tokens), so length
        normalisation has something to do. Under `terms=["common", "rare",
        "alpha", "beta"]`, chunk 0 and chunk 1 each match 3 distinct terms --
        a genuine tie -- so the truncation tie-break has something to decide.
        """
        chunks = [
            self._chunk(tenant, "doc-1", "common rare alpha alpha", chunk_index=0),
            self._chunk(tenant, "doc-1", "common alpha beta", chunk_index=1),
            self._chunk(tenant, "doc-2", "common beta beta beta beta", chunk_index=0),
            self._chunk(tenant, "doc-2", "common", chunk_index=1),
        ]
        # Written with chunks[1] before chunks[0]: those two are the genuine
        # tie the truncation test decides between, and chunks[0].id sorts
        # below chunks[1].id -- so writing them in id order would leave a
        # stable sort on match count alone, with the tie-break removed
        # entirely, indistinguishable from the contract. Insertion order must
        # *disagree* with id order for the tie to prove anything.
        await store.upsert_many([chunks[1], chunks[0], chunks[2], chunks[3]])
        return chunks

    async def test_lexical_candidates_finds_chunks_containing_a_term(
        self, store: ChunkStore
    ) -> None:
        tenant = TenantId(uuid4())
        chunks = await self._corpus(store, tenant)

        result = await store.lexical_candidates(["rare"], tenant, 10)

        assert {candidate.chunk.id for candidate in result.candidates} == {chunks[0].id}

    async def test_lexical_candidates_reports_term_frequencies(self, store: ChunkStore) -> None:
        """A chunk repeating a term reports the count, not `1`."""
        tenant = TenantId(uuid4())
        chunks = await self._corpus(store, tenant)

        result = await store.lexical_candidates(["alpha"], tenant, 10)

        by_id = {candidate.chunk.id: candidate for candidate in result.candidates}
        assert by_id[chunks[0].id].term_frequencies["alpha"] == 2
        assert by_id[chunks[1].id].term_frequencies["alpha"] == 1

    async def test_lexical_candidates_reports_doc_length_in_tokens(self, store: ChunkStore) -> None:
        """A chunk whose text contains stopwords reports the post-tokenization
        length, so a store counting words or characters fails."""
        tenant = TenantId(uuid4())
        # "the" and "is" are stopwords: 4 words, 19 characters, 2 tokens.
        chunk = self._chunk(tenant, "doc-1", "the common is alpha")
        await store.upsert_many([chunk])

        result = await store.lexical_candidates(["common"], tenant, 10)

        assert len(result.candidates) == 1
        assert result.candidates[0].doc_length == 2

    async def test_lexical_candidates_reports_corpus_wide_statistics(
        self, store: ChunkStore
    ) -> None:
        """`n_docs` and `avg_doc_length` describe the whole corpus, asserted
        with a `limit` that truncates -- a store computing them over the
        survivors fails."""
        tenant = TenantId(uuid4())
        await self._corpus(store, tenant)

        result = await store.lexical_candidates(["common", "rare", "alpha", "beta"], tenant, 1)

        assert len(result.candidates) == 1
        assert result.stats.n_docs == 4
        assert result.stats.avg_doc_length == pytest.approx(3.25)

    async def test_lexical_candidates_reports_zero_for_an_absent_term(
        self, store: ChunkStore
    ) -> None:
        tenant = TenantId(uuid4())
        await self._corpus(store, tenant)

        result = await store.lexical_candidates(["zzz-absent"], tenant, 10)

        assert result.stats.doc_frequencies == {"zzz-absent": 0}
        assert result.candidates == []

    async def test_lexical_candidates_covers_exactly_the_requested_terms(
        self, store: ChunkStore
    ) -> None:
        tenant = TenantId(uuid4())
        await self._corpus(store, tenant)

        result = await store.lexical_candidates(["common", "zzz-absent"], tenant, 10)

        assert result.stats.doc_frequencies.keys() == {"common", "zzz-absent"}
        assert result.stats.doc_frequencies["common"] == 4
        assert result.stats.doc_frequencies["zzz-absent"] == 0

    async def test_lexical_candidates_truncates_by_match_count_then_id(
        self, store: ChunkStore
    ) -> None:
        """The ordering contract: chunks matching different numbers of terms,
        and a genuine tie between two of them.

        Under `terms=["common", "rare", "alpha", "beta"]`: chunk 0 matches
        {common, rare, alpha} (3), chunk 1 matches {common, alpha, beta} (3,
        tied with chunk 0), chunk 2 matches {common, beta} (2), chunk 3
        matches {common} (1). A `limit` of 3 keeps both tied chunks plus
        chunk 2 and drops chunk 3; a `limit` of 1 keeps only the
        lower-`id` member of the tied pair.
        """
        tenant = TenantId(uuid4())
        terms = ["common", "rare", "alpha", "beta"]
        chunks = await self._corpus(store, tenant)
        tied_low, tied_high = sorted((chunks[0].id, chunks[1].id))

        top_three = await store.lexical_candidates(terms, tenant, 3)
        assert {candidate.chunk.id for candidate in top_three.candidates} == {
            chunks[0].id,
            chunks[1].id,
            chunks[2].id,
        }

        top_one = await store.lexical_candidates(terms, tenant, 1)
        assert {candidate.chunk.id for candidate in top_one.candidates} == {tied_low}
        assert tied_high not in {candidate.chunk.id for candidate in top_one.candidates}

    async def test_lexical_candidates_with_an_empty_term_list_returns_nothing(
        self, store: ChunkStore
    ) -> None:
        """Empty `terms` returns zeroed statistics and no candidates, without
        touching the store -- a non-empty corpus must not leak into the
        statistics regardless."""
        tenant = TenantId(uuid4())
        await self._corpus(store, tenant)

        result = await store.lexical_candidates([], tenant, 10)

        assert result.candidates == []
        assert result.stats.n_docs == 0
        assert result.stats.avg_doc_length == 0.0
        assert result.stats.doc_frequencies == {}

    async def test_lexical_candidates_with_a_zero_limit_still_reports_statistics(
        self, store: ChunkStore
    ) -> None:
        tenant = TenantId(uuid4())
        await self._corpus(store, tenant)

        result = await store.lexical_candidates(["common"], tenant, 0)

        assert result.candidates == []
        assert result.stats.n_docs == 4
        assert result.stats.avg_doc_length == pytest.approx(3.25)
        assert result.stats.doc_frequencies == {"common": 4}

    async def test_lexical_candidates_rejects_a_negative_limit(self, store: ChunkStore) -> None:
        tenant = TenantId(uuid4())
        await self._corpus(store, tenant)

        with pytest.raises(ValueError, match="-1"):
            await store.lexical_candidates(["common"], tenant, -1)

    async def test_lexical_candidates_returns_copies(self, store: ChunkStore) -> None:
        tenant = TenantId(uuid4())
        chunk = self._chunk(
            tenant,
            "doc-1",
            "common alpha",
            entity_ids=[EntityId(uuid4())],
            metadata={"nested": {"k": "v"}, "list": ["a"]},
        )
        pristine = chunk.model_copy(deep=True)
        await store.upsert_many([chunk])

        result = await store.lexical_candidates(["common"], tenant, 10)
        assert len(result.candidates) == 1
        _mutate(result.candidates[0].chunk)

        again = await store.lexical_candidates(["common"], tenant, 10)
        assert again.candidates[0].chunk == pristine

    async def test_lexical_candidates_never_crosses_tenants(self, store: ChunkStore) -> None:
        """Two tenants holding the same content-addressed chunk id -- the
        case that catches a key compared on `id` alone."""
        left, right = TenantId(uuid4()), TenantId(uuid4())
        shared_text = "common rare alpha"
        left_chunk = self._chunk(left, "doc-1", shared_text, metadata={"owner": "left"})
        right_chunk = self._chunk(right, "doc-1", shared_text, metadata={"owner": "right"})
        assert left_chunk.id == right_chunk.id
        await store.upsert_many([left_chunk, right_chunk])

        result = await store.lexical_candidates(["common"], left, 10)

        assert result.stats.n_docs == 1
        assert len(result.candidates) == 1
        assert result.candidates[0].chunk.metadata == {"owner": "left"}
        assert result.candidates[0].chunk.tenant_id == left

    # ------------------------------------------------------------------
    # `semantic_candidates`
    #
    # Every store here must be built at `self.DIMENSION` -- 4 by default --
    # so a vector below is always a 4-tuple. `cosine_score` from
    # `redstring.domain.vector` is the documented formula behind the score
    # ("`VectorMatch` scale, cosine mapped onto 0..1") and is used as the
    # oracle the same way `VectorStoreCompliance` already does for `search`;
    # it is not the adapter's own arithmetic, so it does not check
    # determinism against itself.
    # ------------------------------------------------------------------

    async def test_semantic_candidates_orders_by_score_descending(self, store: ChunkStore) -> None:
        """Three genuinely different similarities -- a store returning them
        in insertion or storage order rather than by score fails here."""
        tenant = TenantId(uuid4())
        query = [1.0, 0.0, 0.0, 0.0]
        # Written low-then-high-then-mid, so neither insertion order nor a
        # stable no-op sort could pass by accident.
        low = self._chunk(tenant, "doc-1", "orthogonal", embedding=[0.0, 1.0, 0.0, 0.0])
        high = self._chunk(tenant, "doc-1", "identical", embedding=[1.0, 0.0, 0.0, 0.0])
        mid = self._chunk(tenant, "doc-1", "forty five degrees", embedding=[1.0, 1.0, 0.0, 0.0])
        await store.upsert_many([low, high, mid])

        result = await store.semantic_candidates(query, tenant, 10)

        assert [candidate.chunk.id for candidate in result] == [high.id, mid.id, low.id]
        scores = [candidate.score for candidate in result]
        assert scores == sorted(scores, reverse=True)
        assert scores[0] > scores[1] > scores[2]
        by_id = {high.id: high, mid.id: mid, low.id: low}
        for candidate in result:
            embedding = by_id[candidate.chunk.id].embedding
            assert embedding is not None
            expected = cosine_score(query, embedding)
            assert candidate.score == pytest.approx(expected, abs=SCORE_TOLERANCE)

    async def test_semantic_candidates_breaks_ties_on_id_ascending(self, store: ChunkStore) -> None:
        """Two chunks at an identical similarity order by id, not by insertion.

        The vectors are chosen so the similarities are *equal*, and the ids are
        the digests of two texts whose order is known -- a tie-break test whose
        scores merely differ tests nothing about the tie-break.

        `query = [1, 0, 0, 0]`, `alpha = [1, 1, 0, 0]`, `beta = [1, -1, 0, 0]`:
        both have dot product 1 and magnitude sqrt(2) against the unit query,
        so `cosine_score` is `(1 + 1/sqrt(2)) / 2` for each, bit-for-bit --
        this is not a rounded near-tie, the two computations are identical.
        `chunk_id(SourceId("doc-1"), "tie candidate beta")` sorts *below*
        `chunk_id(SourceId("doc-1"), "tie candidate alpha")`, so the correct
        order is `beta` then `alpha`. Written alpha-first so insertion order
        disagrees with the required id order instead of agreeing by accident.
        """
        tenant = TenantId(uuid4())
        query = [1.0, 0.0, 0.0, 0.0]
        alpha = self._chunk(tenant, "doc-1", "tie candidate alpha", embedding=[1.0, 1.0, 0.0, 0.0])
        beta = self._chunk(tenant, "doc-1", "tie candidate beta", embedding=[1.0, -1.0, 0.0, 0.0])
        assert beta.id < alpha.id
        await store.upsert_many([alpha, beta])

        result = await store.semantic_candidates(query, tenant, 10)

        assert [candidate.chunk.id for candidate in result] == [beta.id, alpha.id]
        assert result[0].score == result[1].score

    async def test_semantic_candidates_skips_unembedded_chunks(self, store: ChunkStore) -> None:
        """A chunk with no vector is absent, not present with score 0."""
        tenant = TenantId(uuid4())
        query = [1.0, 0.0, 0.0, 0.0]
        embedded = self._chunk(tenant, "doc-1", "has a vector", embedding=[1.0, 0.0, 0.0, 0.0])
        unembedded = self._chunk(tenant, "doc-1", "no vector at all", chunk_index=1)
        assert unembedded.embedding is None
        await store.upsert_many([embedded, unembedded])

        result = await store.semantic_candidates(query, tenant, 10)

        assert [candidate.chunk.id for candidate in result] == [embedded.id]

    async def test_semantic_candidates_applies_min_score_before_limit(
        self, store: ChunkStore
    ) -> None:
        """`min_score` genuinely shrinks the candidate set below `limit`.

        A `limit` larger than the number of chunks clearing `min_score`
        catches a store that never applies the filter at all -- it would
        return every chunk up to `limit` rather than only the ones
        qualifying on score, which is the same shape as
        `VectorStoreCompliance.test_min_score_drops_results_below_it`.
        """
        tenant = TenantId(uuid4())
        query = [1.0, 0.0, 0.0, 0.0]
        identical = self._chunk(tenant, "doc-1", "identical", embedding=[1.0, 0.0, 0.0, 0.0])
        orthogonal = self._chunk(
            tenant, "doc-1", "orthogonal", chunk_index=1, embedding=[0.0, 1.0, 0.0, 0.0]
        )
        opposite = self._chunk(
            tenant, "doc-1", "opposite", chunk_index=2, embedding=[-1.0, 0.0, 0.0, 0.0]
        )
        await store.upsert_many([identical, orthogonal, opposite])

        result = await store.semantic_candidates(query, tenant, 10, min_score=0.5)

        assert {candidate.chunk.id for candidate in result} == {identical.id, orthogonal.id}
        # Inclusive at the boundary: orthogonal scores exactly 0.5.
        result_strict = await store.semantic_candidates(query, tenant, 10, min_score=0.75)
        assert [candidate.chunk.id for candidate in result_strict] == [identical.id]

    async def test_semantic_candidates_with_a_zero_limit_returns_nothing(
        self, store: ChunkStore
    ) -> None:
        """Pinned as an example, not left to a sampler.

        `KG_COMPLIANCE_MAX_EXAMPLES` is environment-tunable and mutation runs
        lower it, so a boundary reachable only through a hypothesis draw is
        covered non-deterministically -- exactly the `InMemoryVectorStore.search`
        `k=0` incident CLAUDE.md's Testing notes record, where the same suite
        against unchanged source killed the mutant on one cosmic-ray run and
        not the next.
        """
        tenant = TenantId(uuid4())
        query = [1.0, 0.0, 0.0, 0.0]
        chunk = self._chunk(tenant, "doc-1", "has a vector", embedding=[1.0, 0.0, 0.0, 0.0])
        await store.upsert_many([chunk])

        result = await store.semantic_candidates(query, tenant, 0)

        assert result == []

    async def test_semantic_candidates_rejects_a_negative_limit(self, store: ChunkStore) -> None:
        tenant = TenantId(uuid4())
        query = [1.0, 0.0, 0.0, 0.0]

        with pytest.raises(ValueError, match="-1"):
            await store.semantic_candidates(query, tenant, -1)

    async def test_semantic_candidates_rejects_a_vector_of_the_wrong_width(
        self, store: ChunkStore
    ) -> None:
        tenant = TenantId(uuid4())
        narrow = [1.0, 0.0, 0.0]
        assert len(narrow) != self.DIMENSION

        with pytest.raises(DimensionMismatchError) as raised:
            await store.semantic_candidates(narrow, tenant, 10)

        assert raised.value.expected == self.DIMENSION
        assert raised.value.actual == len(narrow)

    async def test_semantic_candidates_rejects_a_zero_norm_query(self, store: ChunkStore) -> None:
        """Cosine is undefined at zero magnitude; `VectorStore.search` already
        rejects a zero-norm query and this port makes the same choice, per
        the module docstring."""
        tenant = TenantId(uuid4())
        chunk = self._chunk(tenant, "doc-1", "has a vector", embedding=[1.0, 0.0, 0.0, 0.0])
        await store.upsert_many([chunk])

        with pytest.raises(ValueError, match="zero"):
            await store.semantic_candidates([0.0, 0.0, 0.0, 0.0], tenant, 10)

    async def test_semantic_candidates_returns_copies(self, store: ChunkStore) -> None:
        """A shallow copy shares the `embedding` list with stored state and
        passes every behavioural assertion above -- `_mutate` covers
        `entity_ids` and `metadata`, and this test additionally mutates the
        returned `embedding` itself, which `_mutate` does not reach."""
        tenant = TenantId(uuid4())
        query = [1.0, 0.0, 0.0, 0.0]
        chunk = self._chunk(
            tenant,
            "doc-1",
            "has a vector",
            embedding=[1.0, 0.0, 0.0, 0.0],
            entity_ids=[EntityId(uuid4())],
            metadata={"nested": {"k": "v"}, "list": ["a"]},
        )
        pristine = chunk.model_copy(deep=True)
        await store.upsert_many([chunk])

        result = await store.semantic_candidates(query, tenant, 10)
        assert len(result) == 1
        returned = result[0].chunk
        _mutate(returned)
        assert returned.embedding is not None
        returned.embedding.append(999.0)
        returned.embedding[0] = -999.0

        again = await store.semantic_candidates(query, tenant, 10)
        assert len(again) == 1
        assert again[0].chunk == pristine
        assert again[0].chunk.embedding == pristine.embedding

    async def test_semantic_candidates_never_crosses_tenants(self, store: ChunkStore) -> None:
        """Two tenants holding the same content-addressed chunk id, each with
        its own embedding -- the case that catches a key compared on `id`
        alone."""
        left, right = TenantId(uuid4()), TenantId(uuid4())
        shared_text = "common rare alpha"
        query = [1.0, 0.0, 0.0, 0.0]
        left_chunk = self._chunk(
            left, "doc-1", shared_text, embedding=[1.0, 0.0, 0.0, 0.0], metadata={"owner": "left"}
        )
        right_chunk = self._chunk(
            right,
            "doc-1",
            shared_text,
            embedding=[1.0, 0.0, 0.0, 0.0],
            metadata={"owner": "right"},
        )
        assert left_chunk.id == right_chunk.id
        await store.upsert_many([left_chunk, right_chunk])

        result = await store.semantic_candidates(query, left, 10)

        assert len(result) == 1
        assert result[0].chunk.id == left_chunk.id
        assert result[0].chunk.metadata == {"owner": "left"}
        assert result[0].chunk.tenant_id == left

    # ------------------------------------------------------------------
    # `get_by_entity`
    # ------------------------------------------------------------------

    async def test_get_by_entity_finds_chunks_mentioning_the_entity(
        self, store: ChunkStore
    ) -> None:
        tenant = TenantId(uuid4())
        entity = EntityId(uuid4())
        mentioning = self._chunk(tenant, "doc-1", "mentions it", entity_ids=[entity])
        silent = self._chunk(tenant, "doc-1", "does not", chunk_index=1)
        await store.upsert_many([mentioning, silent])

        found = await store.get_by_entity(entity, tenant)

        assert [chunk.id for chunk in found] == [mentioning.id]

    async def test_get_by_entity_orders_by_source_then_index_then_id(
        self, store: ChunkStore
    ) -> None:
        """Two sources, with an index-10 case -- a text-typed index column
        fails here as it does for `get_by_source` -- and a genuine
        `(source_id, chunk_index)` tie, so the `id` tie-break decides
        something.

        Without the tie, no two chunks share `(source_id, chunk_index)`, so
        dropping `id` from the sort key would leave this case green: `id`
        never decides anything. `tied_a`/`tied_b` share `doc-1`, index 5, and
        different content-addressed ids -- written **higher id first**, so a
        stable sort on `(source_id, chunk_index)` alone (insertion order)
        disagrees with the contract instead of agreeing with it by accident.
        """
        tenant = TenantId(uuid4())
        entity = EntityId(uuid4())
        tied_a = self._chunk(tenant, "doc-1", "tie text alpha", chunk_index=5, entity_ids=[entity])
        tied_b = self._chunk(tenant, "doc-1", "tie text beta", chunk_index=5, entity_ids=[entity])
        tied_low, tied_high = sorted((tied_a, tied_b), key=lambda chunk: chunk.id)
        assert tied_low.id < tied_high.id
        first_source = [
            self._chunk(tenant, "doc-1", "a", chunk_index=0, entity_ids=[entity]),
            self._chunk(tenant, "doc-1", "b", chunk_index=1, entity_ids=[entity]),
            self._chunk(tenant, "doc-1", "c", chunk_index=10, entity_ids=[entity]),
        ]
        second_source = [
            self._chunk(tenant, "doc-2", "d", chunk_index=0, entity_ids=[entity]),
        ]
        await store.upsert_many([*second_source, *first_source[::-1], tied_high, tied_low])

        found = await store.get_by_entity(entity, tenant)

        assert [chunk.id for chunk in found] == [
            first_source[0].id,
            first_source[1].id,
            tied_low.id,
            tied_high.id,
            first_source[2].id,
            second_source[0].id,
        ]

    async def test_get_by_entity_ignores_other_entities(self, store: ChunkStore) -> None:
        tenant = TenantId(uuid4())
        wanted, other = EntityId(uuid4()), EntityId(uuid4())
        mentions_wanted = self._chunk(tenant, "doc-1", "wanted", entity_ids=[wanted])
        mentions_other = self._chunk(tenant, "doc-1", "other", chunk_index=1, entity_ids=[other])
        await store.upsert_many([mentions_wanted, mentions_other])

        found = await store.get_by_entity(wanted, tenant)

        assert [chunk.id for chunk in found] == [mentions_wanted.id]

    async def test_get_by_entity_of_an_unknown_entity_is_empty(self, store: ChunkStore) -> None:
        tenant = TenantId(uuid4())
        await store.upsert_many([self._chunk(tenant, "doc-1", "no mentions here")])

        assert await store.get_by_entity(EntityId(uuid4()), tenant) == []

    async def test_get_by_entity_returns_copies(self, store: ChunkStore) -> None:
        tenant = TenantId(uuid4())
        entity = EntityId(uuid4())
        written = self._chunk(
            tenant,
            "doc-1",
            "mentions it",
            entity_ids=[entity],
            metadata={"nested": {"k": "v"}, "list": ["a"]},
        )
        pristine = written.model_copy(deep=True)
        await store.upsert_many([written])

        for chunk in await store.get_by_entity(entity, tenant):
            _mutate(chunk)

        assert await store.get_by_entity(entity, tenant) == [pristine]
        assert await store.get(written.id, tenant) == pristine

    async def test_get_by_entity_never_crosses_tenants(self, store: ChunkStore) -> None:
        """Same content-addressed chunk id, two tenants, same entity id."""
        left, right = TenantId(uuid4()), TenantId(uuid4())
        entity = EntityId(uuid4())
        shared_text = "mentions it"
        left_chunk = self._chunk(left, "doc-1", shared_text, entity_ids=[entity])
        right_chunk = self._chunk(right, "doc-1", shared_text, entity_ids=[entity])
        assert left_chunk.id == right_chunk.id
        await store.upsert_many([left_chunk, right_chunk])

        found = await store.get_by_entity(entity, left)

        assert [chunk.id for chunk in found] == [left_chunk.id]
        assert found[0].tenant_id == left

    # ------------------------------------------------------------------
    # Ranking is identical across adapters
    # ------------------------------------------------------------------

    async def test_truncation_follows_the_ports_stated_tie_break(self, store: ChunkStore) -> None:
        """Which candidates survive `limit`, against a written-down oracle.

        The port states the rule outright: order by the number of distinct
        requested terms the chunk contains, descending, then by `id`
        ascending, and return the first `limit`. So the expected survivors can
        be written from the corpus, and this test is the place they are
        written -- `_MATCH_COUNTS` below is read off the four texts by hand.

        **This case used to compare the adapter under test against
        `InMemoryChunkStore`**, and its own docstring conceded the problem:
        "on the in-memory adapter this compares it with itself and is
        trivially true". That is two defects in one, and the second is worse
        than the first. Half the adapters running this suite got no assertion
        at all -- and for the other half, the *contract* was whatever the
        in-memory adapter happened to do, so a defect in the reference was a
        defect in the port for everyone. An in-memory reference that is more
        forgiving than a real backend is the exact failure this directory
        exists to prevent; using one as the oracle builds it in.

        It also could not ship. A suite an outside adapter runs cannot make
        that adapter agree with an implementation detail of this repository's
        in-memory store, and `lint-imports` now forbids the import that made
        it possible.

        Truncation is exercised at **two** limits, and the second is the one
        that matters. `limit=3` cuts the single one-match chunk, which proves
        a cut happens and says nothing about *ties* -- chunks 0 and 1 both
        match three terms, so both survive at 3 whatever the tie-break does.
        Only `limit=1` cuts through the tied pair, and which member survives
        is precisely the divergence this case exists to catch: Postgres offers
        candidates in whatever order the planner produces, and the port's `id`
        tie-break is the only thing making two adapters agree.

        `_corpus` writes the tied pair in reverse id order, so an adapter
        falling back on arrival order disagrees here rather than agreeing by
        coincidence.
        """
        tenant = TenantId(uuid4())
        chunks = await self._corpus(store, tenant)
        terms = ["common", "rare", "alpha", "beta"]

        # Read off `_corpus`'s four texts by hand, not computed by anything
        # under test: "common rare alpha alpha" matches common/rare/alpha,
        # "common alpha beta" matches common/alpha/beta, "common beta beta
        # beta beta" matches common/beta, and "common" matches common.
        distinct_matches = {
            chunks[0].id: 3,
            chunks[1].id: 3,
            chunks[2].id: 2,
            chunks[3].id: 1,
        }
        expected = sorted(
            distinct_matches, key=lambda chunk_id: (-distinct_matches[chunk_id], chunk_id)
        )

        # Guard the guard: the cut at limit=1 must fall *inside* a genuine
        # tie, or the loop below proves no more than a truncation test with
        # no tie-break in it at all.
        assert distinct_matches[expected[0]] == distinct_matches[expected[1]]
        assert expected[0] < expected[1]

        for limit in (3, 1):
            result = await store.lexical_candidates(terms, tenant, limit)
            assert {candidate.chunk.id for candidate in result.candidates} == set(
                expected[:limit]
            ), f"wrong candidates survived at limit={limit}"

    async def test_ranking_over_the_candidates_is_not_degenerate(self, store: ChunkStore) -> None:
        """`rank_chunks` separates this corpus, so the case above means something.

        Every assertion about truncation is vacuous against a store that
        returns everything at score zero -- the ids would still be right and
        the ranking would still be useless. `rank_chunks` is a pure domain
        function, so what is under test here is the *statistics* the adapter
        supplies to it: an adapter reporting flat term frequencies or a wrong
        `n_docs` produces scores that do not separate, and nothing about the
        returned chunks shows it.
        """
        tenant = TenantId(uuid4())
        await self._corpus(store, tenant)
        terms = ["common", "rare", "alpha", "beta"]

        result = await store.lexical_candidates(terms, tenant, 10)
        ranked = rank_chunks(terms, result, 10)

        assert len({ranked_chunk.score for ranked_chunk in ranked}) > 1, (
            "every candidate scored the same -- the adapter's corpus statistics "
            "cannot be distinguishing them"
        )
        assert [ranked_chunk.score for ranked_chunk in ranked] == sorted(
            (ranked_chunk.score for ranked_chunk in ranked), reverse=True
        )
