"""A poisoned event goes to the DLQ, and the projection carries on.

The failure used here is real rather than injected: a relationship whose
endpoint this tenant does not have. `GraphStore` refuses dangling edges, and a
document that references an entity from a document the projection has not
folded yet produces exactly this. Injecting a synthetic exception would test
the library's retry wrapper; this tests the fold.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from eventsource.domain.tenant_context import tenant_scope
from eventsource.ports.positions import ExpectedVersion

from redstring.domain.entity import Entity, ExtractionMethod
from redstring.domain.exceptions import MissingEntityError, ReplayFailedError
from redstring.domain.relationship import Relationship
from redstring.events import DocumentExtracted
from redstring.events.streams import document_stream
from redstring.projections import project

TENANT_ID = uuid4()
MODEL = "ollama/qwen3.6-27b"


def _entity(source_id, name):
    return Entity(
        id=uuid4(),
        tenant_id=TENANT_ID,
        name=name,
        normalized_name=name.lower(),
        entity_type="thing",
        source_id=source_id,
        extraction_method=ExtractionMethod.PATTERN,
        confidence=0.5,
    )


async def _append(event_store, source_id, entities, relationships):
    stream = document_stream(tenant_id=TENANT_ID, source_id=source_id)
    async with tenant_scope(TENANT_ID):
        await event_store.append(
            stream,
            [
                DocumentExtracted(
                    aggregate_id=stream.aggregate_id,
                    tenant_id=TENANT_ID,
                    source_id=source_id,
                    model_version=MODEL,
                    entities=entities,
                    relationships=relationships,
                )
            ],
            ExpectedVersion.no_stream(),
        )


@pytest.fixture
async def poisoned_log(rig):
    """Three documents: good, poison, good. The poison is in the middle so a
    projection that stopped on it would visibly drop the third."""
    first = _entity("doc-1", "Ada")
    await _append(rig.event_store, "doc-1", [first], [])

    dangling = _entity("doc-2", "Grace")
    await _append(
        rig.event_store,
        "doc-2",
        [dangling],
        [
            Relationship(
                id=uuid4(),
                tenant_id=TENANT_ID,
                source_entity_id=dangling.id,
                target_entity_id=uuid4(),  # never extracted by any document
                relationship_type="knows",
                confidence=0.5,
            )
        ],
    )

    last = _entity("doc-3", "Barbara")
    await _append(rig.event_store, "doc-3", [last], [])
    return rig, [first, dangling, last]


class TestAPoisonEventDoesNotWedgeTheProjection:
    async def test_the_events_after_it_are_still_applied(self, poisoned_log):
        rig, entities = poisoned_log
        report = await project(rig.event_store, rig.projections)

        assert report.applied == 2
        assert report.failed == 1

        shape = await rig.shape([TENANT_ID])
        assert entities[2].id.__str__() in shape[str(TENANT_ID)]["entity_ids"]

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
        event's *entities* did land.

        This is a property of the fold making **two calls**, not of either
        one being partial: `upsert_relationships` is atomic (ADR 0018), so the
        relationship half wrote nothing at all rather than a prefix. Making
        that batch atomic therefore did not change this assertion, which is
        worth saying because the docstring here used to cite the old
        non-atomic contract as the cause and would now be quietly wrong.

        It is safe only because a replay of the fixed event is idempotent --
        pinned so the day that stops being true, this says so.
        """
        rig, entities = poisoned_log
        await project(rig.event_store, rig.projections)

        shape = await rig.shape([TENANT_ID])
        assert str(entities[1].id) in shape[str(TENANT_ID)]["entity_ids"]
        assert shape[str(TENANT_ID)]["edges"] == {}

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

        await _append(rig.event_store, "doc-4", [_entity("doc-4", "Missing")], [])
        # The missing endpoint, added under the id the edge points at.
        await rig.graph_store.upsert_entity(
            _entity("doc-4", "Missing").model_copy(update={"id": edge.target_entity_id})
        )

        report = await project(rig.event_store, rig.projections)
        assert report.failed == 0
        shape = await rig.shape([TENANT_ID])
        assert str(edge.id) in shape[str(TENANT_ID)]["edges"]


class TestStrictStopsInsteadOfCounting:
    """`strict=True` for the caller who would rather have no rebuild than a
    partial one -- a test, or a first deployment. Reported downstream as
    BACKLOG B69: without it a replay that dropped *every* event still returns
    a report and exits successfully.
    """

    async def test_it_raises_on_the_first_rejection(self, poisoned_log):
        rig, _ = poisoned_log

        with pytest.raises(ReplayFailedError):
            await project(rig.event_store, rig.projections, strict=True)

    async def test_the_error_names_the_event_rather_than_counting_it(self, poisoned_log):
        """The entry's actual requirement. An exception reading "replay
        failed" with a count is the same silent-partial-rebuild problem in a
        louder voice: an operator needs to know *which* record to go and look
        at, and the position is what makes it findable in the log."""
        rig, entities = poisoned_log

        with pytest.raises(ReplayFailedError) as caught:
            await project(rig.event_store, rig.projections, strict=True)

        assert caught.value.event.source_id == "doc-2"
        assert entities[1].id in {e.id for e in caught.value.event.entities}
        assert caught.value.position is not None
        assert "DocumentExtracted" in str(caught.value)

    async def test_the_rejecting_exception_is_the_cause(self, poisoned_log):
        """Chained rather than replaced. Knowing an event was refused without
        knowing why sends the reader back to the DLQ to find out something the
        traceback could have told them."""
        rig, _ = poisoned_log

        with pytest.raises(ReplayFailedError) as caught:
            await project(rig.event_store, rig.projections, strict=True)

        assert isinstance(caught.value.__cause__, MissingEntityError)

    async def test_it_stops_rather_than_finishing_the_log(self, poisoned_log):
        """The point of stopping. `doc-3` is *after* the poison, so a strict
        run that carried on and raised at the end would leave it applied and
        be indistinguishable from the tolerant mode plus an exception."""
        rig, entities = poisoned_log

        with pytest.raises(ReplayFailedError):
            await project(rig.event_store, rig.projections, strict=True)

        shape = await rig.shape([TENANT_ID])
        assert str(entities[2].id) not in shape[str(TENANT_ID)]["entity_ids"]

    async def test_a_clean_log_is_unaffected_by_strict(self, rig):
        """Otherwise the flag could be raising on something other than a
        rejection and every test above would still pass."""
        good = _entity("doc-1", "Ada")
        await _append(rig.event_store, "doc-1", [good], [])

        report = await project(rig.event_store, rig.projections, strict=True)

        assert (report.applied, report.failed) == (1, 0)

    async def test_the_default_is_still_tolerant(self, poisoned_log):
        """Stated as its own test rather than left implicit in the class
        above: flipping the default would be a breaking change for the rebuild
        case this module was written for, and nothing else here would fail."""
        rig, _ = poisoned_log

        report = await project(rig.event_store, rig.projections)

        assert report.failed == 1


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
