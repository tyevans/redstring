"""`Consolidator` -- the composed entry point for merging duplicates.

`ConsolidationService` decides and emits; `GraphProjection` writes. Everything
here is about the seam between them, because that seam is the whole reason
this class exists and is the only part `tests/unit/consolidation/` does not
already cover.

The distinction matters for what these tests assert. The service's own suite
proves the *events* are right -- which entities merge, what the redirections
are, what `undo` restores. Repeating that here would be a second copy of a
tested claim. What is untested until this module is that the emitted event
**reaches the store**: a `Consolidator` that emitted a perfect
`EntitiesMerged` and forgot to project it would pass every test in the
consolidation package and leave the graph exactly as wrong as doing nothing.

So each test below reads the *store* after the call, not the returned event.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from eventsource.adapters.memory import InMemoryEventStore, InMemorySnapshotStore

from redstring import (
    Consolidator,
    Entity,
    ExtractionMethod,
    InMemoryGraphStore,
    Relationship,
    UnknownMergeError,
)
from redstring.domain.blocking import blocking_keys_for


def entity(tenant_id, name, *, entity_id=None):
    built = Entity(
        id=entity_id or uuid4(),
        tenant_id=tenant_id,
        source_id="doc-1",
        name=name,
        normalized_name=name.lower(),
        entity_type="person",
        extraction_method=ExtractionMethod.MANUAL,
        confidence=1.0,
    )
    return built.model_copy(update={"blocking_keys": blocking_keys_for(built)})


def edge(tenant_id, source, target, kind="knows"):
    return Relationship(
        id=uuid4(),
        tenant_id=tenant_id,
        source_entity_id=source,
        target_entity_id=target,
        relationship_type=kind,
        confidence=0.9,
    )


@pytest.fixture
def tenant_id():
    return uuid4()


@pytest.fixture
def store():
    return InMemoryGraphStore()


class TestMergeReachesTheStore:
    async def test_the_absorbed_entity_becomes_an_alias_of_the_canonical_one(
        self, store, tenant_id
    ):
        """The claim the whole class exists for: emit *and* write.

        Asserted by resolving the merged id through the store rather than by
        reading the returned event -- a `Consolidator` that emitted a correct
        event and never projected it would satisfy any assertion about the
        event.
        """
        ada = entity(tenant_id, "Ada Lovelace")
        alt = entity(tenant_id, "Ada King")
        await store.upsert_entities([ada, alt])

        await Consolidator(store).merge(
            tenant_id=tenant_id,
            canonical_entity_id=ada.id,
            merged_entity_ids=[alt.id],
            merge_reason="same person",
        )

        resolved = await store.resolve_entity_ids([alt.id], tenant_id)
        assert resolved == {alt.id: ada.id}

    async def test_an_edge_on_the_absorbed_entity_moves_to_the_canonical_one(
        self, store, tenant_id
    ):
        """Redirection is the expensive half of a merge and the half a caller
        notices when it is missing: the alias resolves, the graph still answers
        the old question wrong."""
        ada = entity(tenant_id, "Ada Lovelace")
        alt = entity(tenant_id, "Ada King")
        babbage = entity(tenant_id, "Charles Babbage")
        await store.upsert_entities([ada, alt, babbage])
        await store.upsert_relationships([edge(tenant_id, alt.id, babbage.id)])

        report = await Consolidator(store).merge(
            tenant_id=tenant_id, canonical_entity_id=ada.id, merged_entity_ids=[alt.id]
        )

        assert report.relationships_changed == 1
        neighbours = await store.neighbors(ada.id, tenant_id)
        assert [n.id for n in neighbours] == [babbage.id]

    async def test_the_report_describes_what_happened(self, store, tenant_id):
        ada = entity(tenant_id, "Ada Lovelace")
        alt = entity(tenant_id, "Ada King")
        await store.upsert_entities([ada, alt])

        report = await Consolidator(store).merge(
            tenant_id=tenant_id,
            canonical_entity_id=ada.id,
            merged_entity_ids=[alt.id],
            merge_reason="same person",
        )

        assert report.canonical_entity_id == ada.id
        assert report.affected_entity_ids == (alt.id,)
        assert report.reason == "same person"
        assert report.event.canonical_entity_id == ada.id


class TestUndoReachesTheStore:
    async def test_undo_restores_the_entity_and_its_edge(self, store, tenant_id):
        """The round trip, observed through the store on both sides."""
        ada = entity(tenant_id, "Ada Lovelace")
        alt = entity(tenant_id, "Ada King")
        babbage = entity(tenant_id, "Charles Babbage")
        await store.upsert_entities([ada, alt, babbage])
        await store.upsert_relationships([edge(tenant_id, alt.id, babbage.id)])

        consolidator = Consolidator(store)
        merged = await consolidator.merge(
            tenant_id=tenant_id, canonical_entity_id=ada.id, merged_entity_ids=[alt.id]
        )
        assert await store.resolve_entity_ids([alt.id], tenant_id) == {alt.id: ada.id}

        undone = await consolidator.undo(tenant_id=tenant_id, merge_event_id=merged.event.event_id)

        assert undone.affected_entity_ids == (alt.id,)
        assert await store.resolve_entity_ids([alt.id], tenant_id) == {alt.id: alt.id}
        assert [n.id for n in await store.neighbors(alt.id, tenant_id)] == [babbage.id]

    async def test_undoing_a_merge_this_consolidator_did_not_make_raises(self, store, tenant_id):
        with pytest.raises(UnknownMergeError):
            await Consolidator(store).undo(tenant_id=tenant_id, merge_event_id=uuid4())


class TestTheLogIsWhereUndoLooks:
    """The one property a caller has to understand before relying on `undo`."""

    async def test_a_fresh_consolidator_cannot_undo_an_in_memory_merge(self, store, tenant_id):
        """The documented cost of the default log, asserted rather than promised.

        This is the failure mode that would otherwise be discovered after a
        restart, as an `UnknownMergeError` indistinguishable from "that merge
        never happened".
        """
        ada = entity(tenant_id, "Ada Lovelace")
        alt = entity(tenant_id, "Ada King")
        await store.upsert_entities([ada, alt])

        merged = await Consolidator(store).merge(
            tenant_id=tenant_id, canonical_entity_id=ada.id, merged_entity_ids=[alt.id]
        )

        with pytest.raises(UnknownMergeError):
            await Consolidator(store).undo(
                tenant_id=tenant_id, merge_event_id=merged.event.event_id
            )

    async def test_a_shared_event_store_survives_a_new_consolidator(self, store, tenant_id):
        """And the fix, so the docstring's advice is executable rather than
        aspirational. Two `Consolidator`s, one log, undo works across them."""
        ada = entity(tenant_id, "Ada Lovelace")
        alt = entity(tenant_id, "Ada King")
        await store.upsert_entities([ada, alt])

        event_store = InMemoryEventStore()
        snapshots = InMemorySnapshotStore()

        merged = await Consolidator(store, event_store=event_store, snapshot_store=snapshots).merge(
            tenant_id=tenant_id, canonical_entity_id=ada.id, merged_entity_ids=[alt.id]
        )

        await Consolidator(store, event_store=event_store, snapshot_store=snapshots).undo(
            tenant_id=tenant_id, merge_event_id=merged.event.event_id
        )

        assert await store.resolve_entity_ids([alt.id], tenant_id) == {alt.id: alt.id}

    async def test_the_flag_reports_which_arrangement_is_in_use(self, store):
        assert Consolidator(store).remembers_merges_across_restarts is False
        assert (
            Consolidator(store, event_store=InMemoryEventStore()).remembers_merges_across_restarts
            is True
        )


class TestResolve:
    async def test_it_finds_and_merges_a_duplicate_without_a_finder_argument(
        self, store, tenant_id
    ):
        """The default `CandidateFinder` is the point of the convenience: a
        caller who has not chosen weights should not have to build one."""
        ada = entity(tenant_id, "Ada Lovelace")
        twin = entity(tenant_id, "Ada Lovelace")
        await store.upsert_entities([ada, twin])

        report = await Consolidator(store).resolve(ada)

        assert report is not None
        assert report.affected_entity_ids == (twin.id,)
        assert await store.resolve_entity_ids([twin.id], tenant_id) == {twin.id: ada.id}

    async def test_nothing_to_merge_returns_none_and_leaves_the_store_alone(self, store, tenant_id):
        """`None` is an ordinary outcome, not a failure -- and the store must
        be untouched, which a test asserting only the return value cannot see."""
        ada = entity(tenant_id, "Ada Lovelace")
        babbage = entity(tenant_id, "Charles Babbage")
        await store.upsert_entities([ada, babbage])

        assert await Consolidator(store).resolve(ada) is None

        assert await store.resolve_entity_ids([babbage.id], tenant_id) == {babbage.id: babbage.id}

    async def test_the_middle_band_is_rejected_when_no_adjudicator_is_given(self, store, tenant_id):
        """Stated in the docstring and easy to get backwards: without a model
        to ask, an ambiguous pair is *not* merged.

        `high=1.01` puts the identical pair below the merge threshold and
        above `low`, which is the band -- so the only thing that could merge
        them is an adjudicator, and there is none.
        """
        ada = entity(tenant_id, "Ada Lovelace")
        twin = entity(tenant_id, "Ada Lovelace")
        await store.upsert_entities([ada, twin])

        assert await Consolidator(store).resolve(ada, high=1.01, low=0.1) is None
        assert await store.resolve_entity_ids([twin.id], tenant_id) == {twin.id: twin.id}
