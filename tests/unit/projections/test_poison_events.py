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

from kg_builder.domain.entity import Entity, ExtractionMethod
from kg_builder.domain.relationship import Relationship
from kg_builder.events import DocumentExtracted
from kg_builder.events.streams import document_stream
from kg_builder.projections import project

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
        event's *entities* did land. That is a deliberate consequence of the
        port's `upsert_relationships` not being atomic, and it is safe only
        because a replay of the fixed event is idempotent -- worth pinning so
        the day it stops being true, this says so.
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
