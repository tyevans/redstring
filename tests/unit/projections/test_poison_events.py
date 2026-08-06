"""A poisoned event goes to the DLQ, and the projection carries on.

The failure used here is real rather than injected: a relationship whose
endpoint this tenant does not have. `GraphStore` refuses dangling edges, and a
document that references an entity from a document the projection has not
folded yet produces exactly this. Injecting a synthetic exception would test
the library's retry wrapper; this tests the fold.

The log itself is `poisoned_log` in `conftest.py` -- it is shared with
`test_replay_failures.py`, which asserts what the report says about it.
"""

from __future__ import annotations

import pytest

from redstring.projections import project

from .conftest import POISON_TENANT_ID, append_document, poison_entity


class TestAPoisonEventDoesNotWedgeTheProjection:
    async def test_the_events_after_it_are_still_applied(self, poisoned_log):
        rig, entities = poisoned_log
        report = await project(rig.event_store, rig.projections)

        assert report.applied == 2
        assert report.failed == 1

        shape = await rig.shape([POISON_TENANT_ID])
        assert entities[2].id.__str__() in shape[str(POISON_TENANT_ID)]["entity_ids"]

    async def test_the_failure_is_recorded_rather_than_swallowed(self, poisoned_log):
        """`failed` counting up is not enough on its own. An operator needs the
        event itself to decide whether to fix the data and replay it."""
        rig, _ = poisoned_log
        await project(rig.event_store, rig.projections)

        (entry,) = await rig.dlq.get_failed_events()
        assert entry.event_type == "DocumentExtracted"
        assert "does not exist" in entry.error_message

    async def test_the_poisoned_event_left_no_partial_state(self, poisoned_log):
        """The handler writes entities and then relationships, so the failing
        event's *entities* did land. That is a deliberate consequence of the
        port's `upsert_relationships` not being atomic, and it is safe only
        because a replay of the fixed event is idempotent -- worth pinning so
        the day it stops being true, this says so.
        """
        rig, entities = poisoned_log
        await project(rig.event_store, rig.projections)

        shape = await rig.shape([POISON_TENANT_ID])
        assert str(entities[1].id) in shape[str(POISON_TENANT_ID)]["entity_ids"]
        assert shape[str(POISON_TENANT_ID)]["edges"] == {}

    async def test_a_rerun_after_the_missing_entity_arrives_applies_it(self, poisoned_log):
        """The DLQ is not a graveyard. Once the endpoint exists -- because the
        document that holds it was folded -- replaying the whole log applies
        the previously poisoned event with no special handling."""
        rig, _entities = poisoned_log
        await project(rig.event_store, rig.projections)
        assert (await project(rig.event_store, rig.projections)).failed == 1

        edge = None
        async for envelope in rig.event_store.read_all():
            if envelope.event.relationships:
                edge = envelope.event.relationships[0]
        assert edge is not None

        await append_document(rig.event_store, "doc-4", [poison_entity("doc-4", "Missing")], [])
        # The missing endpoint, added under the id the edge points at.
        await rig.graph_store.upsert_entity(
            poison_entity("doc-4", "Missing").model_copy(update={"id": edge.target_entity_id})
        )

        report = await project(rig.event_store, rig.projections)
        assert report.failed == 0
        shape = await rig.shape([POISON_TENANT_ID])
        assert str(edge.id) in shape[str(POISON_TENANT_ID)]["edges"]


class TestResetIsRefusedRatherThanSilentlyDoingNothing:
    """Both projections refuse `reset()`. See BACKLOG B35.

    Pinned because the alternative -- the library's default no-op
    `_truncate_read_models` -- is what an inattentive change would restore,
    and a rebuild over a store still holding the old rows looks successful
    while carrying stale entities nothing will ever remove.
    """

    async def test_the_graph_projection_says_how_to_wipe_instead(self, rig):
        with pytest.raises(NotImplementedError, match="delete_by_tenant"):
            await rig.projections[0].reset()

    async def test_the_vector_projection_says_how_to_wipe_instead(self, rig):
        with pytest.raises(NotImplementedError, match="delete_by_tenant"):
            await rig.projections[1].reset()
