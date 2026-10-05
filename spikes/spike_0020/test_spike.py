"""Empirical tests for SPIKE-0020.

Hypothesis: How to define entity identity across extraction runs and retract stale
entities without violating GraphStore port invariants?
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from redstring.domain.alias import Alias
from redstring.domain.entity import Entity
from redstring.domain.ids import EntityId, SourceId, TenantId
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.relationship import Relationship
from redstring.extraction.mapping import _relationship_id_for, entity_id_for
from redstring.graph.adapters.memory import InMemoryGraphStore

from .harness import (
    DocumentAggregateSim,
    project_extraction_with_retractions,
    run_benchmark,
)


@pytest.fixture
def sample_data():
    tenant_id = TenantId(uuid4())
    source_id = SourceId("doc-spike-0020")
    prov = Provenance(
        source_id=source_id,
        source_text="Ada and Babbage worked together.",
        extraction_method=ExtractionMethod.LLM,
        model="ollama/qwen3.6",
        confidence=0.9,
        observed_at=datetime(2026, 2, 8, 11, 7, tzinfo=UTC),
    )
    e1_id = entity_id_for(tenant_id=tenant_id, source_id=source_id, entity_type="person", name="Ada Lovelace")
    e2_id = entity_id_for(tenant_id=tenant_id, source_id=source_id, entity_type="person", name="Charles Babbage")
    e3_id = entity_id_for(tenant_id=tenant_id, source_id=source_id, entity_type="organization", name="Analytical Engine")

    e1 = Entity(id=e1_id, tenant_id=tenant_id, name="Ada Lovelace", normalized_name="ada lovelace", entity_type="person", provenance=prov)
    e2 = Entity(id=e2_id, tenant_id=tenant_id, name="Charles Babbage", normalized_name="charles babbage", entity_type="person", provenance=prov)
    e3 = Entity(id=e3_id, tenant_id=tenant_id, name="Analytical Engine", normalized_name="analytical engine", entity_type="organization", provenance=prov)

    edge_1_2 = Relationship(
        id=_relationship_id_for(source_entity_id=e1_id, target_entity_id=e2_id, relationship_type="collaborates_with"),
        tenant_id=tenant_id,
        source_entity_id=e1_id,
        target_entity_id=e2_id,
        relationship_type="collaborates_with",
        confidence=0.9,
        provenance=prov,
    )
    edge_2_3 = Relationship(
        id=_relationship_id_for(source_entity_id=e2_id, target_entity_id=e3_id, relationship_type="designed"),
        tenant_id=tenant_id,
        source_entity_id=e2_id,
        target_entity_id=e3_id,
        relationship_type="designed",
        confidence=0.95,
        provenance=prov,
    )

    return tenant_id, source_id, [e1, e2, e3], [edge_1_2, edge_2_3]


@pytest.mark.asyncio
async def test_entity_identity_stability(sample_data):
    """Entity ID is deterministically reproducible across different extraction runs with the same name and doc."""
    tenant_id, source_id, entities, _ = sample_data
    e1 = entities[0]

    rederived_id = entity_id_for(
        tenant_id=tenant_id,
        source_id=source_id,
        entity_type=e1.entity_type,
        name=e1.name,
    )
    assert rederived_id == e1.id


@pytest.mark.asyncio
async def test_aggregate_diff_computation(sample_data):
    """Document aggregate computes retracted entities when fewer entities are found."""
    tenant_id, source_id, entities, edges = sample_data
    agg = DocumentAggregateSim(tenant_id=tenant_id, source_id=source_id)

    # Run 1: e1, e2, e3
    ev1 = agg.record_extraction(model_version="v1", entities=entities, relationships=edges)
    assert ev1 is not None
    assert ev1["retracted_entity_ids"] == []

    # Run 2: Re-extraction only finds e1 (e2 and e3 are dropped)
    ev2 = agg.record_extraction(model_version="v2", entities=[entities[0]], relationships=[])
    assert ev2 is not None
    assert set(ev2["retracted_entity_ids"]) == {entities[1].id, entities[2].id}


@pytest.mark.asyncio
async def test_projection_retraction_preserves_adr0102_invariants(sample_data):
    """Projection retracts entities by tombstoning and deleting relationships without delete_entity."""
    tenant_id, source_id, entities, edges = sample_data
    store = InMemoryGraphStore()
    agg = DocumentAggregateSim(tenant_id=tenant_id, source_id=source_id)

    # Initial extraction
    ev1 = agg.record_extraction(model_version="v1", entities=entities, relationships=edges)
    await project_extraction_with_retractions(store, ev1)

    assert (await store.get_entity(entities[1].id, tenant_id)) is not None
    assert len(await store.get_relationships(entities[1].id, tenant_id)) > 0

    # Merge entity 2 into an alias to verify alias chains survive retraction
    canonical_id = EntityId(uuid4())
    await store.upsert_alias(
        Alias(
            id=uuid4(),
            tenant_id=tenant_id,
            alias_entity_id=entities[1].id,
            canonical_entity_id=canonical_id,
            alias_name="Charles Babbage",
            merged_at=datetime(2026, 2, 8, 12, 0, tzinfo=UTC),
            merge_reason="consolidation",
        )
    )

    # Re-extraction with e2 retracted
    ev2 = agg.record_extraction(model_version="v2", entities=[entities[0]], relationships=[])
    await project_extraction_with_retractions(store, ev2)

    # 1. Stale entity is tombstoned, NOT deleted from store (no delete_entity)
    stored_e2 = await store.get_entity(entities[1].id, tenant_id)
    assert stored_e2 is not None
    assert stored_e2.properties.get("_retracted") is True

    # 2. Incident edges to retracted entity are deleted
    assert await store.get_relationships(entities[1].id, tenant_id) == []

    # 3. Alias resolution is completely preserved
    resolved = await store.resolve_entity_ids([entities[1].id], tenant_id)
    assert resolved[entities[1].id] == canonical_id


def test_hypothesis_benchmark():
    """Empirical benchmark execution."""
    results = run_benchmark(iterations=50)
    assert results["status"] == "completed"
    assert results["p95_latency_ms"] < 20.0  # Must be fast (< 20ms per cycle)
