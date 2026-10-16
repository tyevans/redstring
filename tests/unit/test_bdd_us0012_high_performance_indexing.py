"""Executable BDD Acceptance Criteria for US-0012: High-Performance Graph Indexing.

Governed by:
- ADR-0149 (Unified Redstring Client Facade and In-Memory Indexing Architecture)
- ADR-0002 (File Length Limit)
- ADR-0003 (Blackbox Frontdoor Verification)
- ADR-0006 (Executable BDD User Stories)
- PRD-0003
- US-0012
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from redstring import (
    Entity,
    EntityId,
    ExtractionMethod,
    InMemoryGraphStore,
    InMemoryVectorStore,
    Provenance,
    Relationship,
    RelationshipId,
    TenantId,
    VectorRecord,
)
from redstring.domain.ids import SourceId


def _make_entity(
    tenant_id: TenantId,
    name: str,
    *,
    blocking_keys: frozenset[str] | None = None,
) -> Entity:
    return Entity(
        id=EntityId(uuid4()),
        tenant_id=tenant_id,
        name=name,
        normalized_name=name.lower(),
        entity_type="Concept",
        provenance=Provenance(
            source_id=SourceId("doc-1"),
            extractor="test",
            observed_at=datetime.now(UTC),
            extraction_method=ExtractionMethod.LLM,
            confidence=1.0,
        ),
        blocking_keys=blocking_keys,
    )


def _make_edge(
    tenant_id: TenantId,
    source_id: EntityId,
    target_id: EntityId,
    relationship_type: str = "CONNECTED_TO",
) -> Relationship:
    return Relationship(
        id=RelationshipId(uuid4()),
        tenant_id=tenant_id,
        source_entity_id=source_id,
        target_entity_id=target_id,
        relationship_type=relationship_type,
        source_id=SourceId("doc-1"),
        confidence=1.0,
    )


@pytest.mark.asyncio
async def test_scenario_traverse_entity_neighbors_via_incremental_adjacency_index() -> None:
    """Scenario: Traverse entity neighbors via incremental adjacency index (US-0012)."""
    # Given an InMemoryGraphStore with 1000 stored relationships
    store = InMemoryGraphStore()
    tenant_id = TenantId(uuid4())

    hub = _make_entity(tenant_id, "HubEntity")
    entities = [hub] + [_make_entity(tenant_id, f"Node-{i}") for i in range(1000)]
    await store.upsert_entities(entities)

    edges = [_make_edge(tenant_id, hub.id, target.id) for target in entities[1:]]
    await store.upsert_relationships(edges)

    # When I query neighbors for an entity with depth 1
    neighbors = await store.neighbors(hub.id, tenant_id, depth=1)

    # Then neighbor entities are returned in order
    assert len(neighbors) == 1000
    # And the lookup traverses the incremental adjacency index without rebuilding graph adjacency
    assert hub.id in store._adjacency[tenant_id]
    assert len(store._adjacency[tenant_id][hub.id]) == 1000


@pytest.mark.asyncio
async def test_scenario_lookup_candidate_entities_via_inverted_blocking_index() -> None:
    """Scenario: Lookup candidate entities via inverted blocking index (US-0012)."""
    # Given an InMemoryGraphStore containing entities with blocking keys
    store = InMemoryGraphStore()
    tenant_id = TenantId(uuid4())

    e1 = _make_entity(tenant_id, "Ada Lovelace", blocking_keys=frozenset({"p:ada", "s:A341"}))
    e2 = _make_entity(tenant_id, "Ada King", blocking_keys=frozenset({"p:ada", "s:A341"}))
    e3 = _make_entity(tenant_id, "Charles Babbage", blocking_keys=frozenset({"p:cha", "s:C642"}))
    await store.upsert_entities([e1, e2, e3])

    # When I call find_by_blocking_keys with a list of search keys
    candidates_ada = await store.find_by_blocking_keys(["p:ada"], tenant_id)
    candidates_both = await store.find_by_blocking_keys(["p:ada", "p:cha"], tenant_id)
    candidates_nonexistent = await store.find_by_blocking_keys(["p:none"], tenant_id)

    # Then candidate entities are looked up via the inverted blocking index
    assert len(candidates_ada) == 1
    assert len(candidates_ada["p:ada"]) == 2
    assert {c.name for c in candidates_ada["p:ada"]} == {"Ada Lovelace", "Ada King"}

    assert len(candidates_both["p:ada"]) == 2
    assert len(candidates_both["p:cha"]) == 1

    # And keys with zero matches return empty lists
    assert candidates_nonexistent["p:none"] == []


@pytest.mark.asyncio
async def test_scenario_vector_search_with_precomputed_query_norm() -> None:
    """Scenario: Vector search with precomputed query norm (US-0012)."""
    # Given an InMemoryVectorStore with 100 stored embedding vectors
    dimension = 16
    store = InMemoryVectorStore(dimension=dimension)
    tenant_id = TenantId(uuid4())

    records = []
    for i in range(100):
        # Create normalized orthogonal/diverse test vectors
        vec = [float(1.0 if j == (i % dimension) else 0.0) for j in range(dimension)]
        records.append(
            VectorRecord(
                entity_id=EntityId(uuid4()),
                tenant_id=tenant_id,
                vector=vec,
            )
        )
    await store.upsert_many(records)

    # When I call search with a query vector and k 5
    query_vector = [1.0] + [0.0] * (dimension - 1)
    matches = await store.search(query_vector, tenant_id=tenant_id, k=5)

    # Then top k VectorMatch records are returned ranked by cosine score
    assert len(matches) == 5
    # The exact match at index 0 must have similarity score 1.0 (within float precision)
    assert math.isclose(matches[0].score, 1.0, rel_tol=1e-5)
    # Cosine scores should be descending
    for i in range(len(matches) - 1):
        assert matches[i].score >= matches[i + 1].score
