"""Merge, undo, and the graph is exactly what it was. The slice's headline.

This is slice 7's equivalent of 5b's replay equivalence: the test that shows
the design works rather than that a function returns what it was told to.

The claim has three parts and all three matter:

1. the merge *did something* -- without this the round trip could pass because
   nothing happened;
2. the undo restores the graph **exactly**, compared field by field against a
   snapshot taken before the merge;
3. the restoration comes from the **log**, not from the caller. `undo` is given
   an event id and nothing else.

Everything is real: an in-memory event store, an in-memory graph store, the
real `GraphProjection`, the real `ConsolidationLog`. Nothing is mocked, because
a mocked store cannot fail a round-trip test, which would make the test
worthless.

The comparison uses `oracle.snapshot`, which shares no code with the service or
the projection -- see that module for why `log_builder.py` could not be reused.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from eventsource.adapters.memory import (
    InMemoryCheckpointRepository,
    InMemoryDLQRepository,
    InMemoryEventStore,
    InMemorySnapshotStore,
)
from eventsource.domain.tenant_context import tenant_scope
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from redstring.aggregates.repositories import document_repository
from redstring.consolidation.service import ConsolidationService
from redstring.domain.exceptions import (
    DoubleMergeError,
    MergeIntoAliasError,
    UnknownMergeError,
)
from redstring.events.streams import document_stream
from redstring.graph.adapters.memory import InMemoryGraphStore
from redstring.projections import GraphProjection, project

from .conftest import edge, entity
from .oracle import snapshot


class Rig:
    """One log, one store, one projection, one service. All real."""

    def __init__(self) -> None:
        self.event_store = InMemoryEventStore()
        self.graph_store = InMemoryGraphStore()
        self.snapshots = InMemorySnapshotStore()
        self.projection = GraphProjection(
            self.graph_store,
            checkpoint_repo=InMemoryCheckpointRepository(),
            dlq_repo=InMemoryDLQRepository(),
        )
        self.service = ConsolidationService(
            event_store=self.event_store,
            snapshot_store=self.snapshots,
            graph_store=self.graph_store,
        )

    async def extract(self, tenant_id, source_id, entities, relationships):
        """Put a document's worth of graph into the log, through the aggregate."""
        documents = document_repository(self.event_store)
        stream = document_stream(tenant_id=tenant_id, source_id=source_id)
        async with tenant_scope(tenant_id):
            document = await documents.load_or_create(stream.aggregate_id)
            document.record_extraction(
                tenant_id=tenant_id,
                source_id=source_id,
                model_version="ollama/qwen3.6-27b",
                entities=entities,
                relationships=relationships,
            )
            await documents.save(document)

    async def catch_up(self):
        report = await project(self.event_store, [self.projection])
        assert report.failed == 0, "an event went to the DLQ; the fold failed"

    async def snapshot(self, tenant_id):
        return await snapshot(self.graph_store, tenant_id)


async def _diamond(rig, tenant_id):
    """A graph with every case the plan has to handle.

    Deliberately not a chain. A chain makes "move this edge" and "move every
    edge of this group" the same operation, which is the failure shape
    `CLAUDE.md` tabulates -- so this has an edge that moves, an edge that
    collapses into a self-loop, an edge that becomes a duplicate, and an edge
    that has nothing to do with the merge and must not move.
    """
    canonical = entity(tenant_id, name="Ada Lovelace")
    absorbed = entity(tenant_id, name="A. Lovelace")
    outsider = entity(tenant_id, name="Analytical Engine")
    bystander = entity(tenant_id, name="Charles Babbage")
    entities = [canonical, absorbed, outsider, bystander]

    relationships = [
        # moves onto the canonical entity
        edge(tenant_id, source=absorbed.id, target=outsider.id, kind="worked_on"),
        # collapses to a self-loop and is dropped
        edge(tenant_id, source=canonical.id, target=absorbed.id, kind="same_as"),
        # duplicates an edge the canonical entity already has, and is dropped
        edge(tenant_id, source=canonical.id, target=bystander.id, kind="knew", confidence=0.9),
        edge(tenant_id, source=absorbed.id, target=bystander.id, kind="knew", confidence=0.1),
        # nothing to do with the merge
        edge(tenant_id, source=bystander.id, target=outsider.id, kind="built"),
    ]

    await rig.extract(tenant_id, "doc-1", entities, relationships)
    await rig.catch_up()
    return canonical, absorbed, outsider, bystander


class TestTheRoundTrip:
    async def test_the_merge_changes_the_graph(self):
        """The control. Without it, every assertion below could hold because
        the merge did nothing at all."""
        rig, tenant_id = Rig(), uuid4()
        canonical, absorbed, _outsider, _bystander = await _diamond(rig, tenant_id)
        before = await rig.snapshot(tenant_id)

        await rig.service.merge(
            tenant_id=tenant_id,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
        )
        await rig.catch_up()

        after = await rig.snapshot(tenant_id)
        assert after != before
        assert len(after["relationships"]) < len(before["relationships"]), (
            "the merge dropped no edge, so the self-loop and duplicate cases were never exercised"
        )
        assert after["aliases"], "the merge recorded no alias"

    async def test_undo_reproduces_the_pre_merge_graph_exactly(self):
        """The headline. Field by field, against a snapshot taken before."""
        rig, tenant_id = Rig(), uuid4()
        canonical, absorbed, _outsider, _bystander = await _diamond(rig, tenant_id)
        before = await rig.snapshot(tenant_id)

        merged = await rig.service.merge(
            tenant_id=tenant_id,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
        )
        await rig.catch_up()
        await rig.service.undo(tenant_id=tenant_id, merge_event_id=merged.event_id)
        await rig.catch_up()

        assert await rig.snapshot(tenant_id) == before

    async def test_undo_needs_only_the_event_id(self):
        """The restoration is derived from the replayed log. If a caller had to
        supply it, a caller could restore something that never happened."""
        rig, tenant_id = Rig(), uuid4()
        canonical, absorbed, outsider, _bystander = await _diamond(rig, tenant_id)

        merged = await rig.service.merge(
            tenant_id=tenant_id,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
        )
        undone = await rig.service.undo(tenant_id=tenant_id, merge_event_id=merged.event_id)

        assert undone.unmerged_entity_ids == [absorbed.id]
        restored = {edge.id for edge in undone.restored_relationships}
        assert restored == {r.before.id for r in merged.redirections}
        assert outsider.id in {
            endpoint
            for edge in undone.restored_relationships
            for endpoint in (edge.source_entity_id, edge.target_entity_id)
        }

    async def test_the_absorbed_entity_survives_the_merge(self):
        """A merge is not a delete, so the entity set is unchanged by it --
        which is also why the round trip can compare entities at all."""
        rig, tenant_id = Rig(), uuid4()
        canonical, absorbed, _outsider, _bystander = await _diamond(rig, tenant_id)
        before = await rig.snapshot(tenant_id)

        await rig.service.merge(
            tenant_id=tenant_id,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
        )
        await rig.catch_up()

        assert (await rig.snapshot(tenant_id))["entities"] == before["entities"]

    async def test_a_multi_entity_merge_round_trips(self):
        """Two absorbed at once, so an implementation handling only the first
        cannot pass."""
        rig, tenant_id = Rig(), uuid4()
        canonical, absorbed, outsider, _bystander = await _diamond(rig, tenant_id)
        before = await rig.snapshot(tenant_id)

        merged = await rig.service.merge(
            tenant_id=tenant_id,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id, outsider.id],
        )
        await rig.catch_up()
        mid = await rig.snapshot(tenant_id)
        await rig.service.undo(tenant_id=tenant_id, merge_event_id=merged.event_id)
        await rig.catch_up()

        assert mid != before
        assert await rig.snapshot(tenant_id) == before

    async def test_a_merge_with_no_edges_at_all_round_trips(self):
        """The boundary. Two isolated entities produce an empty redirection
        list, and an undo of nothing must still clear the aliases."""
        rig, tenant_id = Rig(), uuid4()
        canonical = entity(tenant_id, name="Ada")
        absorbed = entity(tenant_id, name="A.")
        await rig.extract(tenant_id, "doc-1", [canonical, absorbed], [])
        await rig.catch_up()
        before = await rig.snapshot(tenant_id)

        merged = await rig.service.merge(
            tenant_id=tenant_id,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
        )
        await rig.catch_up()
        assert merged.redirections == []
        assert (await rig.snapshot(tenant_id))["aliases"], "no alias was recorded"

        await rig.service.undo(tenant_id=tenant_id, merge_event_id=merged.event_id)
        await rig.catch_up()

        assert await rig.snapshot(tenant_id) == before

    async def test_a_rebuild_after_the_round_trip_agrees(self):
        """Replay equivalence over a log that holds a merge and an undo.

        Separate from the round trip: "the live store came back" and "a rebuild
        of this log produces the same store" are different claims, and a merge
        followed by an undo is the log shape most likely to separate them.
        """
        rig, tenant_id = Rig(), uuid4()
        canonical, absorbed, _outsider, _bystander = await _diamond(rig, tenant_id)
        merged = await rig.service.merge(
            tenant_id=tenant_id,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
        )
        await rig.catch_up()
        await rig.service.undo(tenant_id=tenant_id, merge_event_id=merged.event_id)
        await rig.catch_up()
        live = await rig.snapshot(tenant_id)

        await rig.graph_store.delete_by_tenant(tenant_id)
        rebuilt = GraphProjection(
            rig.graph_store,
            checkpoint_repo=InMemoryCheckpointRepository(),
            dlq_repo=InMemoryDLQRepository(),
        )
        report = await project(rig.event_store, [rebuilt])

        assert report.failed == 0
        assert await rig.snapshot(tenant_id) == live


class TestTheRoundTripCanFail:
    """Proof the round trip is not vacuous.

    `CLAUDE.md` requires breaking the implementation on purpose before
    trusting a test like this. That is done by hand against the source (see the
    slice report); what is checked *here* is the cheaper half -- that a
    snapshot notices each kind of change, so a round trip comparing snapshots
    can fail at all.
    """

    async def test_the_snapshot_notices_a_moved_edge(self):
        rig, tenant_id = Rig(), uuid4()
        canonical, absorbed, _outsider, _bystander = await _diamond(rig, tenant_id)
        before = await rig.snapshot(tenant_id)

        moved = next(
            edge
            for edge in await rig.graph_store.get_relationships(absorbed.id, tenant_id)
            if edge.relationship_type == "worked_on"
        )
        await rig.graph_store.upsert_relationship(
            moved.model_copy(update={"source_entity_id": canonical.id})
        )

        assert await rig.snapshot(tenant_id) != before

    async def test_the_snapshot_notices_a_dropped_edge(self):
        rig, tenant_id = Rig(), uuid4()
        _canonical, absorbed, _outsider, _bystander = await _diamond(rig, tenant_id)
        before = await rig.snapshot(tenant_id)

        [any_edge, *_] = await rig.graph_store.get_relationships(absorbed.id, tenant_id)
        await rig.graph_store.delete_relationship(any_edge.id, tenant_id)

        assert await rig.snapshot(tenant_id) != before

    async def test_the_snapshot_notices_an_alias(self):
        """Without this the round trip would pass on an implementation that
        merged the edges and never recorded the merge."""
        rig, tenant_id = Rig(), uuid4()
        canonical, absorbed, _outsider, _bystander = await _diamond(rig, tenant_id)
        before = await rig.snapshot(tenant_id)

        await rig.service.merge(
            tenant_id=tenant_id,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
        )
        await rig.catch_up()

        # Compared on the alias key alone, so this fails if the snapshot omits
        # aliases even though the edges also changed.
        assert (await rig.snapshot(tenant_id))["aliases"] != before["aliases"]

    async def test_the_snapshot_pages_past_its_window(self):
        """The oracle's own cursor loop, which everything else here trusts."""
        rig, tenant_id = Rig(), uuid4()
        many = [entity(tenant_id, name=f"Entity {index}") for index in range(11)]
        await rig.extract(tenant_id, "doc-1", many, [])
        await rig.catch_up()

        assert len((await rig.snapshot(tenant_id))["entities"]) == 11


class TestTheInvariantsInAnger:
    """The three rules `ConsolidationLog` owns, exercised through the service.

    The aggregate has its own unit tests. These are here because an invariant
    enforced by an aggregate nothing calls is an invariant nobody has checked,
    and slice 7 is the first caller.
    """

    async def test_an_entity_cannot_be_merged_into_an_alias(self):
        rig, tenant_id = Rig(), uuid4()
        canonical, absorbed, outsider, _bystander = await _diamond(rig, tenant_id)
        await rig.service.merge(
            tenant_id=tenant_id,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
        )
        await rig.catch_up()

        with pytest.raises(MergeIntoAliasError) as raised:
            await rig.service.merge(
                tenant_id=tenant_id,
                canonical_entity_id=absorbed.id,
                merged_entity_ids=[outsider.id],
            )

        assert raised.value.alias_entity_id == absorbed.id
        assert raised.value.canonical_entity_id == canonical.id

    async def test_a_refused_merge_writes_nothing(self):
        """A refusal is not a partial write. The graph and the log must both be
        exactly as they were, or "raises" is not the same as "did not happen".
        """
        rig, tenant_id = Rig(), uuid4()
        canonical, absorbed, outsider, _bystander = await _diamond(rig, tenant_id)
        await rig.service.merge(
            tenant_id=tenant_id,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
        )
        await rig.catch_up()
        before = await rig.snapshot(tenant_id)
        events_before = len([_ async for _ in rig.event_store.read_all()])

        with pytest.raises(MergeIntoAliasError):
            await rig.service.merge(
                tenant_id=tenant_id,
                canonical_entity_id=absorbed.id,
                merged_entity_ids=[outsider.id],
            )

        await rig.catch_up()
        assert await rig.snapshot(tenant_id) == before
        assert len([_ async for _ in rig.event_store.read_all()]) == events_before

    async def test_an_entity_cannot_be_merged_twice(self):
        rig, tenant_id = Rig(), uuid4()
        canonical, absorbed, outsider, _bystander = await _diamond(rig, tenant_id)
        await rig.service.merge(
            tenant_id=tenant_id,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
        )

        with pytest.raises(DoubleMergeError) as raised:
            await rig.service.merge(
                tenant_id=tenant_id,
                canonical_entity_id=outsider.id,
                merged_entity_ids=[absorbed.id],
            )

        assert raised.value.entity_id == absorbed.id

    async def test_the_double_merge_check_looks_past_the_first_id(self):
        """A check that stopped at the first element would let anything behind
        a legal id through, and the resulting graph gives one entity two
        canonical parents."""
        rig, tenant_id = Rig(), uuid4()
        canonical, absorbed, outsider, bystander = await _diamond(rig, tenant_id)
        await rig.service.merge(
            tenant_id=tenant_id,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
        )

        with pytest.raises(DoubleMergeError) as raised:
            await rig.service.merge(
                tenant_id=tenant_id,
                canonical_entity_id=outsider.id,
                merged_entity_ids=[bystander.id, absorbed.id],
            )

        assert raised.value.entity_id == absorbed.id

    async def test_an_undo_must_name_a_merge_that_happened(self):
        rig, tenant_id = Rig(), uuid4()
        await _diamond(rig, tenant_id)

        with pytest.raises(UnknownMergeError):
            await rig.service.undo(tenant_id=tenant_id, merge_event_id=uuid4())

    async def test_a_merge_cannot_be_undone_twice(self):
        """ "Already undone" and "never happened" are one case from here: there
        is nothing to reverse either way, and a second undo would restore edges
        over a graph that is already the pre-merge one."""
        rig, tenant_id = Rig(), uuid4()
        canonical, absorbed, _outsider, _bystander = await _diamond(rig, tenant_id)
        merged = await rig.service.merge(
            tenant_id=tenant_id,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
        )
        await rig.service.undo(tenant_id=tenant_id, merge_event_id=merged.event_id)

        with pytest.raises(UnknownMergeError):
            await rig.service.undo(tenant_id=tenant_id, merge_event_id=merged.event_id)

    async def test_an_undone_merge_can_be_made_again(self):
        """The point of undo: it corrects a bad merge rather than merely
        recording that one happened. If the entity stayed an alias, a
        mistakenly-merged entity could never be merged correctly."""
        rig, tenant_id = Rig(), uuid4()
        canonical, absorbed, outsider, _bystander = await _diamond(rig, tenant_id)
        merged = await rig.service.merge(
            tenant_id=tenant_id,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
        )
        await rig.catch_up()
        await rig.service.undo(tenant_id=tenant_id, merge_event_id=merged.event_id)
        await rig.catch_up()

        again = await rig.service.merge(
            tenant_id=tenant_id,
            canonical_entity_id=outsider.id,
            merged_entity_ids=[absorbed.id],
        )
        await rig.catch_up()

        assert again.canonical_entity_id == outsider.id
        assert await rig.graph_store.resolve_entity_ids([absorbed.id], tenant_id) == {
            absorbed.id: outsider.id
        }


class TestChainsAndTenants:
    async def test_a_chain_of_merges_resolves_to_the_end(self):
        """`B -> A` then `A -> C` is two legal merges, and the log permits it:
        it refuses merging *into* an alias, not merging a canonical away."""
        rig, tenant_id = Rig(), uuid4()
        canonical, absorbed, outsider, _bystander = await _diamond(rig, tenant_id)

        await rig.service.merge(
            tenant_id=tenant_id,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
        )
        await rig.catch_up()
        await rig.service.merge(
            tenant_id=tenant_id,
            canonical_entity_id=outsider.id,
            merged_entity_ids=[canonical.id],
        )
        await rig.catch_up()

        assert await rig.graph_store.resolve_entity_ids([absorbed.id, canonical.id], tenant_id) == {
            absorbed.id: outsider.id,
            canonical.id: outsider.id,
        }

    async def test_one_tenants_merge_does_not_touch_another(self):
        rig = Rig()
        first, second = uuid4(), uuid4()
        canonical, absorbed, _o, _b = await _diamond(rig, first)
        await _diamond(rig, second)
        untouched = await rig.snapshot(second)

        await rig.service.merge(
            tenant_id=first,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
        )
        await rig.catch_up()

        assert await rig.snapshot(second) == untouched


class TestTheRoundTripAsAProperty:
    @settings(
        max_examples=25,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        absorbed_count=st.integers(min_value=1, max_value=3),
        confidences=st.lists(st.floats(0.0, 1.0), min_size=6, max_size=6),
        pairs=st.lists(
            st.tuples(st.integers(0, 4), st.integers(0, 4)).filter(lambda pair: pair[0] != pair[1]),
            min_size=1,
            max_size=6,
        ),
    )
    async def test_merge_then_undo_is_the_identity_on_the_graph(
        self, absorbed_count, confidences, pairs
    ):
        """Over generated graphs, not one hand-built shape.

        The generated edges may duplicate each other, may run between two
        entities that are both absorbed, and may point either way -- which is
        how the three cases the plan distinguishes get hit in combination
        rather than one at a time.
        """
        rig, tenant_id = Rig(), uuid4()
        nodes = [entity(tenant_id, name=f"Entity {index}") for index in range(5)]
        relationships = [
            edge(
                tenant_id,
                source=nodes[source].id,
                target=nodes[target].id,
                kind="knows",
                confidence=confidences[index % len(confidences)],
            )
            for index, (source, target) in enumerate(pairs)
        ]
        await rig.extract(tenant_id, "doc-1", nodes, relationships)
        await rig.catch_up()
        before = await rig.snapshot(tenant_id)

        merged = await rig.service.merge(
            tenant_id=tenant_id,
            canonical_entity_id=nodes[0].id,
            merged_entity_ids=[node.id for node in nodes[1 : 1 + absorbed_count]],
        )
        await rig.catch_up()
        await rig.service.undo(tenant_id=tenant_id, merge_event_id=merged.event_id)
        await rig.catch_up()

        assert await rig.snapshot(tenant_id) == before
