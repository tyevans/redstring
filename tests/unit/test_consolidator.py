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

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from eventsource.adapters.memory import InMemoryEventStore, InMemorySnapshotStore

from redstring import (
    Consolidator,
    Entity,
    ExtractionMethod,
    InMemoryGraphStore,
    Provenance,
    Relationship,
    UnknownMergeError,
)
from redstring.domain.blocking import blocking_keys_for

#: A fixed observation instant. Never `datetime.now(UTC)`: a fixture that
#: varies per run makes any comparison on `observed_at` non-deterministic.
OBSERVED = datetime(2026, 2, 5, 11, 7, tzinfo=UTC)


def entity(tenant_id, name, *, entity_id=None):
    built = Entity(
        id=entity_id or uuid4(),
        tenant_id=tenant_id,
        name=name,
        normalized_name=name.lower(),
        entity_type="person",
        provenance=Provenance(
            observed_at=OBSERVED,
            extraction_method=ExtractionMethod.MANUAL,
            confidence=1.0,
            source_id="doc-1",
        ),
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


class TestResolveMany:
    async def test_resolve_many_returns_a_report_per_merge_and_folds_each_into_the_store(
        self, store, tenant_id
    ):
        """The composed guarantee: events emitted *and* the graph updated.

        Assert the store, not just the reports -- `Consolidator`'s whole
        reason to exist over `ConsolidationService` is that it runs the
        projection, and a report list is identical whether or not it did.
        """
        ada = entity(tenant_id, "Ada Lovelace")
        ada_twin = entity(tenant_id, "Ada Lovelace")
        babbage = entity(tenant_id, "Charles Babbage")
        babbage_twin = entity(tenant_id, "Charles Babbage")
        await store.upsert_entities([ada, ada_twin, babbage, babbage_twin])

        reports = await Consolidator(store).resolve_many([ada, babbage])

        assert len(reports) == 2
        assert await store.resolve_entity_ids([ada_twin.id], tenant_id) == {ada_twin.id: ada.id}
        assert await store.resolve_entity_ids([babbage_twin.id], tenant_id) == {
            babbage_twin.id: babbage.id
        }

    async def test_resolve_many_with_one_subject_emits_the_expected_single_merge(
        self, store, tenant_id
    ):
        """A single-subject call produces exactly the one merge expected."""
        ada = entity(tenant_id, "Ada Lovelace")
        twin = entity(tenant_id, "Ada Lovelace")
        await store.upsert_entities([ada, twin])

        reports = await Consolidator(store).resolve_many([ada])

        assert len(reports) == 1
        assert reports[0].affected_entity_ids == (twin.id,)
        assert await store.resolve_entity_ids([twin.id], tenant_id) == {twin.id: ada.id}


class TestTheStoresTheCallerSupplies:
    """A store passed to `Consolidator` must be the one it uses.

    Found by mutation testing, not by review. `snapshot_store if snapshot_store
    is not None else InMemorySnapshotStore()` negated to
    `if not snapshot_store is not None` **survived the entire unit suite** —
    1913 tests — because nothing asserted the caller's snapshot store was
    reached. Under that mutant a supplied store is silently swapped for a
    fresh in-memory one, and `None` is passed straight through where a real
    store was meant to be constructed.

    That is the worst shape a wiring defect can take: every merge still
    succeeds, every existing assertion still holds, and the only symptom is
    that snapshots accumulate somewhere the caller cannot see. A deployment
    that supplied a durable snapshot store would be silently running without
    one, and would find out at the first slow replay.

    `event_store` had a witness already — `remembers_merges_across_restarts`
    is derived from it, so its mutant dies. `snapshot_store` had none, which is
    exactly why only one of the two symmetric lines survived.

    **The conditional's other branch is still unwitnessed, deliberately.** Under
    the mutant, omitting `snapshot_store` passes `None` straight through, and
    `AggregateRepository` accepts that and simply runs without snapshots — so a
    merge succeeds either way. A first draft of this class asserted "omitting
    the snapshot store still merges" and passed against the mutant, which is
    the failure shape CLAUDE.md catalogues: an input on which both
    implementations agree. It was deleted rather than kept, because every other
    test in this module already constructs `Consolidator(store)` and merges, so
    it added a false claim and no coverage. Witnessing that branch needs an
    observable for "snapshots are being taken at all", which the 100-merge
    cadence makes expensive; it is not worth a slow test for a branch whose
    failure mode is a missing optimisation.
    """

    async def test_the_supplied_snapshot_store_is_the_one_consulted(self, store, tenant_id):
        """One merge is enough: loading the aggregate consults the snapshot store.

        Asserted through a recording wrapper rather than by reading a private
        attribute, so the test states "the caller's store is used" rather than
        "the constructor assigns this field" — the second would pass against a
        rewrite that assigned it and then ignored it.
        """
        consulted: list[str] = []

        class RecordingSnapshotStore(InMemorySnapshotStore):
            async def get_snapshot(self, *args, **kwargs):
                consulted.append("get")
                return await super().get_snapshot(*args, **kwargs)

        ada = entity(tenant_id, "Ada Lovelace")
        alt = entity(tenant_id, "Ada King")
        await store.upsert_entities([ada, alt])

        mine = RecordingSnapshotStore()
        await Consolidator(store, snapshot_store=mine).merge(
            tenant_id=tenant_id, canonical_entity_id=ada.id, merged_entity_ids=[alt.id]
        )

        assert consulted, (
            "the snapshot store passed to Consolidator was never consulted, so "
            "the constructor is not using the one the caller supplied"
        )

    async def test_the_report_cannot_be_edited_after_the_fact(self, store, tenant_id):
        """`frozen=True` mutated to `frozen=False` survived.

        `test_composition.py` already pins this for `GraphBuildReport`, from an
        earlier mutation round. `ConsolidationReport` never got the equivalent,
        which is the whole reason its mutant lived: the decision had been made
        and written down for one report type and not carried to the other.

        A report is a record of what happened. A caller that can rewrite
        `affected_entity_ids` can make an audit line disagree with the graph it
        describes, and the event it names is already in the log.
        """
        ada = entity(tenant_id, "Ada Lovelace")
        alt = entity(tenant_id, "Ada King")
        await store.upsert_entities([ada, alt])

        report = await Consolidator(store).merge(
            tenant_id=tenant_id, canonical_entity_id=ada.id, merged_entity_ids=[alt.id]
        )

        with pytest.raises(AttributeError):
            report.canonical_entity_id = uuid4()  # type: ignore[misc]

    async def test_the_collaborators_must_be_passed_by_keyword(self, store):
        """`*` mutated to `/` survived, making every collaborator positional.

        Same shape as the `build_graph` case in `test_composition.py`, and the
        same argument: `event_store`, `snapshot_store` and `vector_store` are
        three optional collaborators of three unrelated types whose positional
        order nothing would remind a caller of. The `*` is what guarantees
        there is no order to get wrong, and without a test it is a comment.
        """
        with pytest.raises(TypeError):
            Consolidator(store, InMemoryEventStore())  # type: ignore[misc]

    async def test_undo_takes_its_arguments_by_keyword(self, store, tenant_id):
        """`undo`'s `*` mutated to `/` survived, same as the constructor's.

        `tenant_id` and `merge_event_id` are both UUIDs. Positionally they are
        indistinguishable, so a caller who swapped them would get a lookup
        against the wrong tenant rather than a `TypeError` — which is the
        precise reason the `*` is there and the reason it needs a witness.
        """
        with pytest.raises(TypeError):
            await Consolidator(store).undo(tenant_id, uuid4())  # type: ignore[misc]

    async def test_the_graph_signal_is_on_by_default(self, tenant_id):
        """`use_graph_signal: bool = True` mutated to `False` survived.

        The default is a public API decision — the graph feature is the
        expensive one, and turning it off is documented as "a stated trade
        rather than a silent degradation". A flipped default *is* the silent
        degradation, and every existing consolidation test passes
        `use_graph_signal=False` explicitly, so the default was never executed
        as a default. That is CLAUDE.md's factory row exactly: a value nothing
        constructs the normal way is a value nothing checks.

        Observed through the store rather than by reading the flag back:
        `CandidateFinder._neighbour_names` short-circuits before touching the graph
        when the signal is off, so a call to `get_relationships` is the
        behaviour the default actually buys.
        """
        asked: list[object] = []

        class RecordingStore(InMemoryGraphStore):
            async def get_relationships(self, entity_id, tenant, *args, **kwargs):
                asked.append(entity_id)
                return await super().get_relationships(entity_id, tenant, *args, **kwargs)

        store = RecordingStore()
        ada = entity(tenant_id, "Ada Lovelace")
        twin = entity(tenant_id, "Ada Lovelace")
        await store.upsert_entities([ada, twin])

        await Consolidator(store).resolve(ada)

        assert asked, (
            "the graph signal was never consulted, so `use_graph_signal` no longer defaults to True"
        )

    async def test_merge_takes_its_arguments_by_keyword(self, store, tenant_id):
        """`merge`'s `*` mutated to `/` survived too.

        `tenant_id` and `canonical_entity_id` are both UUIDs and adjacent. A
        caller who swapped them positionally would merge into a nonexistent
        entity under a tenant that is really an entity id — a lookup failure
        far from the call, rather than a `TypeError` at it.
        """
        ada = entity(tenant_id, "Ada Lovelace")
        alt = entity(tenant_id, "Ada King")
        await store.upsert_entities([ada, alt])

        with pytest.raises(TypeError):
            await Consolidator(store).merge(tenant_id, ada.id, [alt.id])  # type: ignore[misc]

    async def test_resolve_takes_its_options_by_keyword(self, store, tenant_id):
        """And `resolve`'s.

        `subject` is deliberately positional — it is what the call is *about*.
        Everything after it is a collaborator or a threshold, and `finder`,
        `adjudicator`, `high` and `low` have no order a caller would recall.
        Two of them are floats, so a swapped pair is silent: `high=0.75,
        low=0.92` inverts the bands rather than raising.
        """
        ada = entity(tenant_id, "Ada Lovelace")
        await store.upsert_entities([ada])

        with pytest.raises(TypeError):
            await Consolidator(store).resolve(ada, None, None, 0.92)  # type: ignore[misc]
