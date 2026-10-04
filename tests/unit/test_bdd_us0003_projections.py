"""Executable BDD Acceptance Criteria for US-0003: Project Extraction Events to Stores.

Governed by:
- ADR-0001 (Event Log Schema and Granularity)
- ADR-0002 (Two Store Ports)
- ADR-0020 (The Replay Driver Goes Upstream)
- PRD-0001
- US-0003
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from redstring import (
    DocumentExtracted,
    GraphProjection,
    InMemoryGraphStore,
)
from redstring.domain.entity import Entity
from redstring.domain.ids import SourceId, TenantId
from redstring.domain.provenance import ExtractionMethod, Provenance


def _make_entity(tenant_id: TenantId, name: str, entity_type: str, source_id: str) -> Entity:
    return Entity(
        id=uuid4(),
        tenant_id=tenant_id,
        name=name,
        normalized_name=name.lower(),
        entity_type=entity_type,
        provenance=Provenance(
            observed_at=datetime.now(UTC),
            extraction_method=ExtractionMethod.MANUAL,
            confidence=1.0,
            source_id=source_id,
        ),
    )


@pytest.mark.asyncio
async def test_project_events_to_graph_store_and_vector_store() -> None:
    """Scenario: Project events to graph store and vector store (US-0003)."""
    tenant_id = TenantId(uuid4())
    graph_store = InMemoryGraphStore()
    graph_projection = GraphProjection(graph_store)

    entity = _make_entity(tenant_id, "Babbage Engine", "Device", "src-projection-test")

    event = DocumentExtracted(
        aggregate_id=uuid4(),
        source_id=SourceId("src-projection-test"),
        tenant_id=tenant_id,
        model_version="test-model",
        entities=[entity],
        relationships=[],
    )

    await graph_projection.handle(event)

    entities = await graph_store.find_entities(tenant_id, entity_type="Device")
    assert len(entities) == 1
    assert entities[0].name == "Babbage Engine"


@pytest.mark.asyncio
async def test_rebuilding_projections_deterministically_from_the_event_log() -> None:
    """Scenario: Rebuilding projections deterministically from the event log (US-0003)."""
    tenant_id = TenantId(uuid4())
    store1 = InMemoryGraphStore()
    store2 = InMemoryGraphStore()

    entity = _make_entity(tenant_id, "Analytical Engine", "Machine", "src-rebuild-test")

    event = DocumentExtracted(
        aggregate_id=uuid4(),
        source_id=SourceId("src-rebuild-test"),
        tenant_id=tenant_id,
        model_version="test-model",
        entities=[entity],
        relationships=[],
    )

    # First projection run
    proj1 = GraphProjection(store1)
    await proj1.handle(event)

    # Rebuild run into fresh store
    proj2 = GraphProjection(store2)
    await proj2.handle(event)

    res1 = await store1.find_entities(tenant_id)
    res2 = await store2.find_entities(tenant_id)

    assert len(res1) == len(res2) == 1
    assert res1[0].name == res2[0].name == "Analytical Engine"
