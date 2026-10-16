"""Tests verifying incremental indexing and performance.

Covers InMemoryGraphStore and InMemoryVectorStore.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from redstring.domain.entity import Entity
from redstring.domain.ids import EntityId, RelationshipId, SourceId, TenantId
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.relationship import Relationship
from redstring.domain.vector import VectorRecord
from redstring.graph.adapters.memory import InMemoryGraphStore
from redstring.vector.adapters.memory import InMemoryVectorStore

OBSERVED = datetime(2026, 2, 12, 11, 7, tzinfo=UTC)


def _entity(name: str, tenant_id: TenantId, blocking_keys: list[str] | None = None) -> Entity:
    return Entity(
        id=EntityId(uuid4()),
        name=name,
        normalized_name=name.lower(),
        entity_type="Concept",
        tenant_id=tenant_id,
        blocking_keys=blocking_keys,
        provenance=Provenance(
            source_id=SourceId("src-1"),
            extractor="test",
            observed_at=OBSERVED,
            extraction_method=ExtractionMethod.LLM,
            confidence=1.0,
        ),
    )


def _relationship(
    source_id: EntityId,
    target_id: EntityId,
    tenant_id: TenantId,
    rel_type: str = "RELATES_TO",
) -> Relationship:
    return Relationship(
        id=RelationshipId(uuid4()),
        source_entity_id=source_id,
        target_entity_id=target_id,
        relationship_type=rel_type,
        tenant_id=tenant_id,
        confidence=1.0,
    )


@pytest.mark.asyncio
async def test_blocking_index_updates_on_entity_mutation():
    store = InMemoryGraphStore()
    tenant_id = TenantId(uuid4())

    e1 = _entity("Alice", tenant_id, blocking_keys=["ALICE", "PERSON"])
    await store.upsert_entity(e1)

    matches = await store.find_by_blocking_key("ALICE", tenant_id)
    assert len(matches) == 1
    assert matches[0].name == "Alice"

    grouped = await store.find_by_blocking_keys(["ALICE", "UNKNOWN"], tenant_id)
    assert len(grouped["ALICE"]) == 1
    assert grouped["UNKNOWN"] == []

    # Update entity with new blocking keys
    e1_updated = e1.model_copy(update={"blocking_keys": ["ALICE_V2"]})
    await store.upsert_entity(e1_updated)

    assert await store.find_by_blocking_key("ALICE", tenant_id) == []
    matches_v2 = await store.find_by_blocking_key("ALICE_V2", tenant_id)
    assert len(matches_v2) == 1
    assert matches_v2[0].id == e1.id


@pytest.mark.asyncio
async def test_adjacency_index_updates_on_relationship_mutation():
    store = InMemoryGraphStore()
    tenant_id = TenantId(uuid4())

    e1 = _entity("A", tenant_id)
    e2 = _entity("B", tenant_id)
    e3 = _entity("C", tenant_id)
    await store.upsert_entities([e1, e2, e3])

    r1 = _relationship(e1.id, e2.id, tenant_id, "CONNECTED")
    r2 = _relationship(e2.id, e3.id, tenant_id, "CONNECTED")
    await store.upsert_relationships([r1, r2])

    # Neighbors traversal
    n1 = await store.neighbors(e1.id, tenant_id, depth=1)
    assert [n.id for n in n1] == [e2.id]

    n2 = await store.neighbors(e1.id, tenant_id, depth=2)
    assert [n.id for n in n2] == [e2.id, e3.id]

    # Delete relationship removes from index
    deleted = await store.delete_relationship(r1.id, tenant_id)
    assert deleted is True

    n1_after = await store.neighbors(e1.id, tenant_id, depth=1)
    assert n1_after == []


@pytest.mark.asyncio
async def test_delete_by_tenant_cleans_indexes():
    store = InMemoryGraphStore()
    tenant_id = TenantId(uuid4())

    e1 = _entity("A", tenant_id, blocking_keys=["KEY_A"])
    e2 = _entity("B", tenant_id, blocking_keys=["KEY_B"])
    await store.upsert_entities([e1, e2])
    r1 = _relationship(e1.id, e2.id, tenant_id)
    await store.upsert_relationship(r1)

    assert len(await store.find_by_blocking_key("KEY_A", tenant_id)) == 1
    assert len(await store.neighbors(e1.id, tenant_id)) == 1

    removed = await store.delete_by_tenant(tenant_id)
    assert removed == 2

    assert await store.find_by_blocking_key("KEY_A", tenant_id) == []
    assert await store.neighbors(e1.id, tenant_id) == []


@pytest.mark.asyncio
async def test_vector_search_single_pass_norm():
    store = InMemoryVectorStore(dimension=3)
    tenant_id = TenantId(uuid4())

    records = [
        VectorRecord(entity_id=EntityId(uuid4()), tenant_id=tenant_id, vector=[1.0, 0.0, 0.0]),
        VectorRecord(entity_id=EntityId(uuid4()), tenant_id=tenant_id, vector=[0.0, 1.0, 0.0]),
        VectorRecord(entity_id=EntityId(uuid4()), tenant_id=tenant_id, vector=[0.5, 0.5, 0.0]),
    ]
    await store.upsert_many(records)

    matches = await store.search([1.0, 0.0, 0.0], tenant_id, k=2)
    assert len(matches) == 2
    assert matches[0].entity_id == records[0].entity_id
    assert matches[0].score == pytest.approx(1.0)
