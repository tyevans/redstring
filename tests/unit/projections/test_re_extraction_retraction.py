"""Re-extraction retracts stale entities and incident relationships.

Governed by:
- ADR-0001 (Specification as Code)
- ADR-0102 (Two store ports, absence of delete_entity)
- ADR-0148 (Entity Retraction on Re-extraction without delete_entity)
- BACKLOG B32
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from eventsource.application.projections import replay
from eventsource.domain.tenant_context import tenant_scope

from redstring.aggregates.repositories import document_repository
from redstring.domain.alias import Alias
from redstring.domain.entity import Entity
from redstring.domain.ids import EntityId
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.relationship import Relationship
from redstring.events.streams import document_stream

from .conftest import fresh_rig

OBSERVED = datetime(2026, 2, 8, 11, 7, tzinfo=UTC)
SOURCE_ID = "doc-retract-1"
FIRST_MODEL = "ollama/qwen3.6-27b"
SECOND_MODEL = "ollama/qwen4-30b"


@pytest.fixture
def tenant_id():
    return uuid4()


def _entity(tenant_id, name):
    return Entity(
        id=uuid4(),
        tenant_id=tenant_id,
        name=name,
        normalized_name=name.lower(),
        entity_type="person",
        provenance=Provenance(
            observed_at=OBSERVED,
            extraction_method=ExtractionMethod.PATTERN,
            confidence=0.9,
            source_id=SOURCE_ID,
        ),
    )


def _relationship(tenant_id, source_id, target_id, rel_type="knows"):
    return Relationship(
        id=uuid4(),
        tenant_id=tenant_id,
        source_entity_id=source_id,
        target_entity_id=target_id,
        relationship_type=rel_type,
        confidence=0.85,
        provenance=Provenance(
            observed_at=OBSERVED,
            extraction_method=ExtractionMethod.PATTERN,
            confidence=0.85,
            source_id=SOURCE_ID,
        ),
    )


class TestReExtractionRetraction:
    async def test_re_extraction_retracts_dropped_entities_and_incident_edges(self, tenant_id):
        rig = fresh_rig()
        documents = document_repository(rig.event_store)
        stream = document_stream(tenant_id=tenant_id, source_id=SOURCE_ID)

        e1 = _entity(tenant_id, "Ada")
        e2 = _entity(tenant_id, "Charles")
        edge = _relationship(tenant_id, e1.id, e2.id)

        async with tenant_scope(tenant_id):
            doc = await documents.load_or_create(stream.aggregate_id)
            doc.record_extraction(
                tenant_id=tenant_id,
                source_id=SOURCE_ID,
                model_version=FIRST_MODEL,
                entities=[e1, e2],
                relationships=[edge],
            )
            await documents.save(doc)

        report = await replay(rig.event_store, rig.projections)
        assert report.failed == 0

        # Assert initial state
        assert await rig.graph_store.get_entity(e1.id, tenant_id) is not None
        assert await rig.graph_store.get_entity(e2.id, tenant_id) is not None
        assert len(await rig.graph_store.get_relationships(e1.id, tenant_id)) == 1

        # Re-extraction discovering only e1 (e2 dropped)
        async with tenant_scope(tenant_id):
            doc = await documents.load_or_create(stream.aggregate_id)
            doc.record_extraction(
                tenant_id=tenant_id,
                source_id=SOURCE_ID,
                model_version=SECOND_MODEL,
                entities=[e1],
                relationships=[],
            )
            await documents.save(doc)

        report2 = await replay(rig.event_store, rig.projections)
        assert report2.failed == 0

        # Verify e1 remains active
        stored_e1 = await rig.graph_store.get_entity(e1.id, tenant_id)
        assert stored_e1 is not None
        assert stored_e1.properties.get("_retracted") is None

        # Verify e2 is tombstoned, NOT deleted (no delete_entity per ADR-0102)
        stored_e2 = await rig.graph_store.get_entity(e2.id, tenant_id)
        assert stored_e2 is not None
        assert stored_e2.properties.get("_retracted") is True

        # Verify incident edges were deleted
        assert await rig.graph_store.get_relationships(e1.id, tenant_id) == []
        assert await rig.graph_store.get_relationships(e2.id, tenant_id) == []

    async def test_retracted_entities_preserve_alias_resolution(self, tenant_id):
        rig = fresh_rig()
        documents = document_repository(rig.event_store)
        stream = document_stream(tenant_id=tenant_id, source_id=SOURCE_ID)

        e1 = _entity(tenant_id, "Ada")
        e2 = _entity(tenant_id, "Charles")

        async with tenant_scope(tenant_id):
            doc = await documents.load_or_create(stream.aggregate_id)
            doc.record_extraction(
                tenant_id=tenant_id,
                source_id=SOURCE_ID,
                model_version=FIRST_MODEL,
                entities=[e1, e2],
                relationships=[],
            )
            await documents.save(doc)

        await replay(rig.event_store, rig.projections)

        # Merge e2 into canonical
        canonical_id = EntityId(uuid4())
        await rig.graph_store.upsert_alias(
            Alias(
                id=uuid4(),
                tenant_id=tenant_id,
                alias_entity_id=e2.id,
                canonical_entity_id=canonical_id,
                alias_name="Charles",
                merged_at=OBSERVED,
                merge_reason="consolidation",
            )
        )

        # Re-extract with e2 retracted
        async with tenant_scope(tenant_id):
            doc = await documents.load_or_create(stream.aggregate_id)
            doc.record_extraction(
                tenant_id=tenant_id,
                source_id=SOURCE_ID,
                model_version=SECOND_MODEL,
                entities=[e1],
                relationships=[],
            )
            await documents.save(doc)

        report = await replay(rig.event_store, rig.projections)
        assert report.failed == 0

        # Alias resolution still resolves e2 -> canonical_id
        resolved = await rig.graph_store.resolve_entity_ids([e2.id], tenant_id)
        assert resolved[e2.id] == canonical_id

    async def test_replay_is_idempotent(self, tenant_id):
        rig = fresh_rig()
        documents = document_repository(rig.event_store)
        stream = document_stream(tenant_id=tenant_id, source_id=SOURCE_ID)

        e1 = _entity(tenant_id, "Ada")
        e2 = _entity(tenant_id, "Charles")

        async with tenant_scope(tenant_id):
            doc = await documents.load_or_create(stream.aggregate_id)
            doc.record_extraction(
                tenant_id=tenant_id,
                source_id=SOURCE_ID,
                model_version=FIRST_MODEL,
                entities=[e1, e2],
                relationships=[],
            )
            await documents.save(doc)

            doc = await documents.load_or_create(stream.aggregate_id)
            doc.record_extraction(
                tenant_id=tenant_id,
                source_id=SOURCE_ID,
                model_version=SECOND_MODEL,
                entities=[e1],
                relationships=[],
            )
            await documents.save(doc)

        # Replay twice
        report1 = await replay(rig.event_store, rig.projections)
        assert report1.failed == 0
        state1 = await rig.shape([tenant_id])

        report2 = await replay(rig.event_store, rig.projections)
        assert report2.failed == 0
        state2 = await rig.shape([tenant_id])

        assert state1 == state2
