"""The three merge invariants, and what happens when each is violated.

These rules are enforced by nothing in the current codebase, which is the
whole reason they belong in an aggregate: each one, violated, corrupts a graph
silently. An entity merged into an alias produces a chain nothing resolves; an
entity merged twice ends up with two canonical parents; an undo of a merge
that never happened restores edges that were never displaced.
"""

from uuid import UUID, uuid4

import pytest

from kg_builder.aggregates.consolidation_log import ConsolidationLog
from kg_builder.domain.consolidation import RelationshipRedirection
from kg_builder.domain.exceptions import (
    DoubleMergeError,
    MergeIntoAliasError,
    UnknownMergeError,
)
from kg_builder.domain.relationship import Relationship
from kg_builder.events import EntitiesMerged, MergeUndone


@pytest.fixture
def tenant_id():
    return uuid4()


@pytest.fixture
def log(tenant_id):
    return ConsolidationLog(tenant_id)


def _relationship(tenant_id, source_entity_id, target_entity_id):
    return Relationship(
        id=uuid4(),
        tenant_id=tenant_id,
        source_entity_id=source_entity_id,
        target_entity_id=target_entity_id,
        relationship_type="works_for",
        confidence=0.8,
    )


class TestMerge:
    def test_a_merge_emits_one_event(self, log, tenant_id):
        canonical, absorbed = uuid4(), uuid4()
        log.merge(
            tenant_id=tenant_id,
            canonical_entity_id=canonical,
            merged_entity_ids=[absorbed],
            merge_reason="exact name match",
        )
        (event,) = log.uncommitted_events
        assert isinstance(event, EntitiesMerged)
        assert event.canonical_entity_id == canonical
        assert event.merged_entity_ids == [absorbed]
        assert event.tenant_id == tenant_id

    def test_an_entity_cannot_be_merged_into_an_alias(self, log, tenant_id):
        """Merging B into A then C into B would leave C pointing at an entity
        that is not canonical, and nothing in the graph resolves the chain."""
        a, b, c = uuid4(), uuid4(), uuid4()
        log.merge(tenant_id=tenant_id, canonical_entity_id=a, merged_entity_ids=[b])
        with pytest.raises(MergeIntoAliasError) as excinfo:
            log.merge(tenant_id=tenant_id, canonical_entity_id=b, merged_entity_ids=[c])
        assert excinfo.value.alias_entity_id == b
        assert excinfo.value.canonical_entity_id == a

    def test_an_entity_cannot_be_merged_twice(self, log, tenant_id):
        """B absorbed by A and then by C would give B two canonical parents,
        and which one wins depends on the order the projection folded them."""
        a, b, c = uuid4(), uuid4(), uuid4()
        log.merge(tenant_id=tenant_id, canonical_entity_id=a, merged_entity_ids=[b])
        with pytest.raises(DoubleMergeError) as excinfo:
            log.merge(tenant_id=tenant_id, canonical_entity_id=c, merged_entity_ids=[b])
        assert excinfo.value.entity_id == b

    def test_the_check_sees_every_entity_in_the_batch_not_just_the_first(self, log, tenant_id):
        """A merge absorbs a *list*, and a check that stopped at the first
        element would pass anything hidden behind a legal one."""
        a, b, c, d = uuid4(), uuid4(), uuid4(), uuid4()
        log.merge(tenant_id=tenant_id, canonical_entity_id=a, merged_entity_ids=[b])
        with pytest.raises(DoubleMergeError) as excinfo:
            log.merge(tenant_id=tenant_id, canonical_entity_id=c, merged_entity_ids=[d, b])
        assert excinfo.value.entity_id == b

    def test_a_canonical_entity_may_absorb_more_entities_later(self, log, tenant_id):
        """Being canonical is not being merged. A second merge into the same
        canonical entity is the normal case, and a check that confused the two
        would forbid it."""
        a, b, c = uuid4(), uuid4(), uuid4()
        log.merge(tenant_id=tenant_id, canonical_entity_id=a, merged_entity_ids=[b])
        log.merge(tenant_id=tenant_id, canonical_entity_id=a, merged_entity_ids=[c])
        assert len(log.uncommitted_events) == 2


class TestUndo:
    def test_an_undo_must_reference_a_merge_that_happened(self, log, tenant_id):
        with pytest.raises(UnknownMergeError):
            log.undo_merge(tenant_id=tenant_id, merge_event_id=uuid4())

    def test_an_undo_cannot_be_applied_twice(self, log, tenant_id):
        """After the first undo the merge is no longer in effect, so a second
        one would restore edges that are already restored -- and, worse, would
        re-emit a `MergeUndone` whose entities are no longer aliases."""
        a, b = uuid4(), uuid4()
        log.merge(tenant_id=tenant_id, canonical_entity_id=a, merged_entity_ids=[b])
        merge_event_id = log.uncommitted_events[0].event_id
        log.undo_merge(tenant_id=tenant_id, merge_event_id=merge_event_id)
        with pytest.raises(UnknownMergeError):
            log.undo_merge(tenant_id=tenant_id, merge_event_id=merge_event_id)

    def test_an_undo_matches_a_merge_id_that_arrived_as_a_string(self, log, tenant_id):
        """The id a real caller passes was parsed from a request or a row, so
        it is a *different object* from the one the aggregate emitted.

        Every other test here hands back `uncommitted_events[0].event_id`
        itself, so `is` and `==` agree and the lookup could be comparing
        identity without a single test noticing -- which it was, until a
        cosmic-ray mutant rewrote `==` as `is` and survived. Round-tripping
        through `str` is what a caller does and what the mutant cannot pass.
        """
        a, b = uuid4(), uuid4()
        log.merge(tenant_id=tenant_id, canonical_entity_id=a, merged_entity_ids=[b])
        as_a_caller_would_have_it = UUID(str(log.uncommitted_events[0].event_id))
        assert as_a_caller_would_have_it is not log.uncommitted_events[0].event_id

        log.undo_merge(tenant_id=tenant_id, merge_event_id=as_a_caller_would_have_it)

        assert isinstance(log.uncommitted_events[1], MergeUndone)

    def test_an_undo_restores_the_edges_the_merge_displaced(self, log, tenant_id):
        """This is the pre-merge recovery path: the aggregate replayed its own
        history to know what the merge moved, and writes that into the event so
        the projection never has to read the log."""
        a, b, outsider = uuid4(), uuid4(), uuid4()
        before = _relationship(tenant_id, b, outsider)
        after = before.model_copy(update={"source_entity_id": a})
        log.merge(
            tenant_id=tenant_id,
            canonical_entity_id=a,
            merged_entity_ids=[b],
            redirections=[RelationshipRedirection(before=before, after=after)],
        )
        merge_event_id = log.uncommitted_events[0].event_id

        log.undo_merge(tenant_id=tenant_id, merge_event_id=merge_event_id)

        undone = log.uncommitted_events[1]
        assert isinstance(undone, MergeUndone)
        assert undone.restored_relationships == [before]
        assert undone.canonical_entity_id == a
        assert undone.unmerged_entity_ids == [b]

    def test_an_undo_restores_an_edge_the_merge_dropped(self, log, tenant_id):
        """A merge drops an edge whose endpoints were both absorbed, because it
        would become a self-loop. `before` is the whole `Relationship` for
        exactly this case: the undo has to recreate it, not just move it."""
        a, b, c = uuid4(), uuid4(), uuid4()
        before = _relationship(tenant_id, b, c)
        log.merge(
            tenant_id=tenant_id,
            canonical_entity_id=a,
            merged_entity_ids=[b, c],
            redirections=[RelationshipRedirection(before=before, after=None)],
        )
        merge_event_id = log.uncommitted_events[0].event_id
        log.undo_merge(tenant_id=tenant_id, merge_event_id=merge_event_id)
        assert log.uncommitted_events[1].restored_relationships == [before]

    def test_an_undone_merge_frees_its_entities_to_be_merged_again(self, log, tenant_id):
        """Undo that left the entity marked as an alias would make the mistake
        permanent -- correcting a bad merge is the whole point."""
        a, b, c = uuid4(), uuid4(), uuid4()
        log.merge(tenant_id=tenant_id, canonical_entity_id=a, merged_entity_ids=[b])
        log.undo_merge(tenant_id=tenant_id, merge_event_id=log.uncommitted_events[0].event_id)
        log.merge(tenant_id=tenant_id, canonical_entity_id=c, merged_entity_ids=[b])
        assert len(log.uncommitted_events) == 3

    def test_an_undone_merge_frees_its_canonical_entity_to_be_absorbed(self, log, tenant_id):
        a, b, c = uuid4(), uuid4(), uuid4()
        log.merge(tenant_id=tenant_id, canonical_entity_id=a, merged_entity_ids=[b])
        log.undo_merge(tenant_id=tenant_id, merge_event_id=log.uncommitted_events[0].event_id)
        log.merge(tenant_id=tenant_id, canonical_entity_id=c, merged_entity_ids=[a])
        assert len(log.uncommitted_events) == 3


class TestRehydration:
    def test_state_replayed_from_history_enforces_the_same_invariants(self, tenant_id):
        """The invariants live in state, and state comes from replay. A rule
        that held only for the aggregate that emitted the event would be no
        rule at all -- every real command runs against a rehydrated instance.
        """
        a, b, c = uuid4(), uuid4(), uuid4()
        emitter = ConsolidationLog(tenant_id)
        emitter.merge(tenant_id=tenant_id, canonical_entity_id=a, merged_entity_ids=[b])

        rehydrated = ConsolidationLog(tenant_id)
        rehydrated.load_from_history(emitter.uncommitted_events)

        assert rehydrated.uncommitted_events == []
        with pytest.raises(DoubleMergeError):
            rehydrated.merge(tenant_id=tenant_id, canonical_entity_id=c, merged_entity_ids=[b])

    def test_replay_from_genuinely_nothing_leaves_an_empty_log(self, tenant_id):
        """The zero case: an aggregate that has never seen an event must permit
        any merge and know of no merge to undo. Every other test here starts
        from state a prior command left behind."""
        log = ConsolidationLog(tenant_id)
        log.load_from_history([])
        assert log.version == 0
        with pytest.raises(UnknownMergeError):
            log.undo_merge(tenant_id=tenant_id, merge_event_id=uuid4())


class TestRehydrationFromSerialisedEvents:
    """Replay from events that went through JSON, which is what a stored log
    is.

    `load_from_history` with the objects the aggregate just emitted shares
    every id, so `==` and `is` agree throughout `_apply`. Round-tripping
    through `model_dump(mode="json")` and back gives equal-but-distinct UUIDs,
    which is the only way these comparisons are exercised as comparisons.
    """

    @staticmethod
    def _round_trip(events):
        return [type(event).model_validate(event.model_dump(mode="json")) for event in events]

    def test_an_undo_replayed_from_json_still_marks_its_merge_undone(self, tenant_id):
        emitter = ConsolidationLog(tenant_id)
        a, b = uuid4(), uuid4()
        emitter.merge(tenant_id=tenant_id, canonical_entity_id=a, merged_entity_ids=[b])
        emitter.undo_merge(
            tenant_id=tenant_id, merge_event_id=emitter.uncommitted_events[0].event_id
        )

        replayed = ConsolidationLog(tenant_id)
        replayed.load_from_history(self._round_trip(emitter.uncommitted_events))

        assert [record.undone for record in replayed.state.merges] == [True]

    def test_an_undo_replayed_from_json_still_frees_its_entities(self, tenant_id):
        emitter = ConsolidationLog(tenant_id)
        a, b, c = uuid4(), uuid4(), uuid4()
        emitter.merge(tenant_id=tenant_id, canonical_entity_id=a, merged_entity_ids=[b])
        emitter.undo_merge(
            tenant_id=tenant_id, merge_event_id=emitter.uncommitted_events[0].event_id
        )

        replayed = ConsolidationLog(tenant_id)
        replayed.load_from_history(self._round_trip(emitter.uncommitted_events))

        assert replayed.state.alias_of == {}
        replayed.merge(tenant_id=tenant_id, canonical_entity_id=c, merged_entity_ids=[b])
