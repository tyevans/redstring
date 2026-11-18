"""Executable BDD Acceptance Criteria for US-0020, US-0021, US-0022, and US-0023.

Governed by:
- ADR-0149 (Unified Redstring Client Facade and In-Memory Indexing Architecture)
- ADR-0002 (File Length Limit)
- ADR-0003 (Blackbox Frontdoor Verification)
- ADR-0006 (Executable BDD User Stories)
- ADR-0009 (Property-Based Testing)
- PRD-0008, PRD-0009
- US-0020, US-0021, US-0022, US-0023
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from redstring import (
    DatePrecision,
    Entity,
    EntityId,
    ExtractionMethod,
    FakeLlmProvider,
    InMemoryGraphStore,
    Provenance,
    Redstring,
    Relationship,
    RelationshipId,
    SourceId,
    TemporalExtent,
    TenantId,
    UncertaintyMarker,
    analyze_topology,
    diff_graphs,
    format_graphml,
    format_json_ld,
    parse_graphml,
    parse_json_ld,
)


def _make_entity(
    tenant_id: TenantId,
    name: str,
    entity_type: str = "Person",
    *,
    description: str | None = None,
    properties: dict[str, Any] | None = None,
    temporal: TemporalExtent | None = None,
) -> Entity:
    return Entity(
        id=EntityId(uuid4()),
        tenant_id=tenant_id,
        name=name,
        normalized_name=name.lower().strip(),
        entity_type=entity_type,
        description=description,
        properties=properties or {},
        provenance=Provenance(
            observed_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
            extraction_method=ExtractionMethod.LLM,
            confidence=0.95,
            source_id=SourceId("doc-1"),
            source_text=f"{name} mentioned in doc-1",
            model="gpt-4o",
        ),
        temporal=temporal,
    )


def _make_rel(
    tenant_id: TenantId,
    src: EntityId,
    tgt: EntityId,
    rel_type: str = "COLLABORATED_WITH",
) -> Relationship:
    return Relationship(
        id=RelationshipId(uuid4()),
        tenant_id=tenant_id,
        source_entity_id=src,
        target_entity_id=tgt,
        relationship_type=rel_type,
        source_id=SourceId("doc-1"),
        properties={"note": "verified"},
        confidence=0.9,
    )


@pytest.mark.asyncio
async def test_scenario_round_trip_json_ld_export_and_import_preserves_graph() -> None:
    """US-0020: Round-trip JSON-LD export and import into a fresh tenant preserves graph."""
    client = Redstring.in_memory(provider=FakeLlmProvider(by_substring={}))
    t1 = client.tenant_id
    ada = _make_entity(
        t1,
        "Ada Lovelace",
        "Person",
        description="Mathematician",
        properties={"title": "Countess"},
        temporal=TemporalExtent(
            start_date=datetime(1815, 12, 10, tzinfo=UTC),
            end_date=datetime(1852, 11, 27, tzinfo=UTC),
            precision=DatePrecision.DAY,
            uncertainty=UncertaintyMarker.EXACT,
        ),
    )
    babbage = _make_entity(t1, "Charles Babbage", "Person", description="Inventor")
    rel = _make_rel(t1, ada.id, babbage.id, "COLLABORATED_WITH")
    await client.graph_store.upsert_entities([ada, babbage])
    await client.graph_store.upsert_relationships([rel])

    exported = await client.export_graph(format="json-ld", tenant_id=t1)
    t2 = TenantId(uuid4())
    report = await client.import_graph(exported, format="json-ld", tenant_id=t2)

    assert report.entities_imported == 2
    assert report.relationships_imported == 1
    assert report.elapsed_ms >= 0.0

    re_exported = await client.export_graph(format="json-ld", tenant_id=t2)
    assert re_exported == exported


@pytest.mark.asyncio
async def test_scenario_round_trip_graphml_export_and_idempotent_reimport() -> None:
    """US-0020: Round-trip GraphML export and idempotent re-import into a tenant store."""
    client = Redstring.in_memory(provider=FakeLlmProvider(by_substring={}))
    t1 = client.tenant_id
    ada = _make_entity(t1, "Ada Lovelace", "Person", description="Mathematician")
    babbage = _make_entity(t1, "Charles Babbage", "Person", description="Inventor")
    rel = _make_rel(t1, ada.id, babbage.id, "COLLABORATED_WITH")
    await client.graph_store.upsert_entities([ada, babbage])
    await client.graph_store.upsert_relationships([rel])

    graphml_payload = await client.export_graph(format="graphml", tenant_id=t1)
    t2 = TenantId(uuid4())
    r1 = await client.import_graph(graphml_payload, format="graphml", tenant_id=t2)
    r2 = await client.import_graph(graphml_payload, format="graphml", tenant_id=t2)

    assert r1.entities_imported == 2
    assert r2.entities_imported == 2
    topo = await client.analyze_topology(tenant_id=t2)
    assert topo.entity_count == 2
    assert topo.relationship_count == 1


@pytest.mark.asyncio
async def test_scenario_reject_malformed_or_dangling_edge_import_payloads() -> None:
    """US-0020: Reject malformed or dangling-edge import payloads without corruption."""
    client = Redstring.in_memory(provider=FakeLlmProvider(by_substring={}))
    e1_id = uuid4()
    missing_id = uuid4()
    rel_id = uuid4()
    bad_payload = (
        '{"@context": {}, "@graph": [{"@id": "urn:redstring:entity:'
        f'{e1_id}", "@type": "Person", "name": "Ada", "relationships": ['
        f'{{"@id": "urn:redstring:relationship:{rel_id}", "@type": "KNOWS", '
        f'"target": "urn:redstring:entity:{missing_id}"}}]}}]}}'
    )

    with pytest.raises(ValueError, match="unknown target entity"):
        await client.import_graph(bad_payload, format="json-ld")

    topo = await client.analyze_topology()
    assert topo.entity_count == 0
    assert topo.relationship_count == 0


@pytest.mark.asyncio
async def test_scenario_export_focused_khop_ego_network_subgraph() -> None:
    """Scenario: Export focused k-hop ego-network subgraph around a center entity (US-0021)."""
    client = Redstring.in_memory(provider=FakeLlmProvider(by_substring={}))
    t = client.tenant_id
    ada = _make_entity(t, "Ada Lovelace", "Person")
    babbage = _make_entity(t, "Charles Babbage", "Person")
    london = _make_entity(t, "London", "Location")
    r1 = _make_rel(t, ada.id, babbage.id, "COLLABORATED_WITH")
    r2 = _make_rel(t, babbage.id, london.id, "LIVED_IN")
    await client.graph_store.upsert_entities([ada, babbage, london])
    await client.graph_store.upsert_relationships([r1, r2])

    diagram = await client.export_graph(
        format="mermaid", center_entity_id="Ada Lovelace", max_depth=1
    )
    assert "Ada Lovelace" in diagram
    assert "Charles Babbage" in diagram
    assert "COLLABORATED_WITH" in diagram
    assert "London" not in diagram


@pytest.mark.asyncio
async def test_scenario_filter_exported_subgraph_by_entity_and_relationship_types() -> None:
    """Scenario: Filter exported subgraph by entity_types and relationship_types (US-0021)."""
    client = Redstring.in_memory(provider=FakeLlmProvider(by_substring={}))
    t = client.tenant_id
    ada = _make_entity(t, "Ada Lovelace", "Person")
    babbage = _make_entity(t, "Charles Babbage", "Person")
    engine = _make_entity(t, "Analytical Engine", "Machine")
    r1 = _make_rel(t, ada.id, babbage.id, "COLLABORATED_WITH")
    r2 = _make_rel(t, babbage.id, engine.id, "DESIGNED")
    await client.graph_store.upsert_entities([ada, babbage, engine])
    await client.graph_store.upsert_relationships([r1, r2])

    diagram = await client.export_graph(
        format="mermaid",
        entity_types=["Person"],
        relationship_types=["COLLABORATED_WITH"],
    )
    assert "Ada Lovelace" in diagram
    assert "Charles Babbage" in diagram
    assert "COLLABORATED_WITH" in diagram
    assert "Analytical Engine" not in diagram
    assert "DESIGNED" not in diagram


@pytest.mark.asyncio
async def test_scenario_compute_comprehensive_topology_diagnostics_and_hubs() -> None:
    """Scenario: Compute comprehensive topology diagnostics and top-k hub entities (US-0022)."""
    client = Redstring.in_memory(provider=FakeLlmProvider(by_substring={}))
    t = client.tenant_id
    hub = _make_entity(t, "Ada Lovelace", "Person")
    n1 = _make_entity(t, "Charles Babbage", "Person")
    n2 = _make_entity(t, "Analytical Engine", "Machine")
    c1 = _make_entity(t, "Paris", "Location")
    c2 = _make_entity(t, "France", "Location")
    iso = _make_entity(t, "Isolated Node", "Concept")
    rels = [
        _make_rel(t, hub.id, n1.id, "COLLABORATED_WITH"),
        _make_rel(t, hub.id, n2.id, "PROGRAMMED"),
        _make_rel(t, n1.id, hub.id, "CORRESPONDED_WITH"),
        _make_rel(t, c1.id, c2.id, "LOCATED_IN"),
    ]
    await client.graph_store.upsert_entities([hub, n1, n2, c1, c2, iso])
    await client.graph_store.upsert_relationships(rels)

    topo = await client.analyze_topology(top_k_hubs=3)
    assert topo.entity_count == 6
    assert topo.relationship_count == 4
    assert dict(topo.entity_type_counts) == {
        "Concept": 1,
        "Location": 2,
        "Machine": 1,
        "Person": 2,
    }
    assert topo.connected_components == 3
    assert topo.isolated_entities == 1
    assert topo.top_hubs[0].entity.name == "Ada Lovelace"
    assert topo.top_hubs[0].out_degree == 2
    assert topo.top_hubs[0].in_degree == 1
    assert topo.top_hubs[0].degree == 3


@pytest.mark.asyncio
async def test_scenario_sub_500ms_performance_bound_on_topology_analysis() -> None:
    """Scenario: Sub-500ms performance bound on multi-hundred-node topology analysis (US-0022)."""
    store = InMemoryGraphStore()
    t = TenantId(uuid4())
    entities = [_make_entity(t, f"Entity-{i}", "Node") for i in range(200)]
    rels = [
        _make_rel(t, entities[i % 200].id, entities[(i * 7 + 1) % 200].id, "LINK")
        for i in range(300)
        if (i % 200) != ((i * 7 + 1) % 200)
    ]
    await store.upsert_entities(entities)
    await store.upsert_relationships(rels)

    topo = await analyze_topology(t, graph=store, relationships=store)
    assert topo.entity_count == 200
    assert topo.elapsed_ms < 500.0


@pytest.mark.asyncio
async def test_scenario_detect_added_removed_modified_and_verify_roundtrip_identity() -> None:
    """US-0023: Detect added/removed/modified records and round-trip identity via diff_graphs."""
    client = Redstring.in_memory(provider=FakeLlmProvider(by_substring={}))
    base_tenant = client.tenant_id
    ada = _make_entity(base_tenant, "Ada Lovelace", "Person", description="Original bio")
    babbage = _make_entity(base_tenant, "Charles Babbage", "Person", description="Inventor")
    rel = _make_rel(base_tenant, ada.id, babbage.id, "COLLABORATED_WITH")
    await client.graph_store.upsert_entities([ada, babbage])
    await client.graph_store.upsert_relationships([rel])

    exported = await client.export_graph(format="json-ld", tenant_id=base_tenant)
    target_tenant = TenantId(uuid4())
    await client.import_graph(exported, format="json-ld", tenant_id=target_tenant)

    identical_diff = await client.diff_graphs(target_tenant, base_tenant_id=base_tenant)
    assert identical_diff.is_identical is True
    assert len(identical_diff.entities_added) == 0
    assert len(identical_diff.entities_removed) == 0
    assert len(identical_diff.entities_modified) == 0

    existing = await client.graph_store.find_entities(target_tenant, limit=10)
    modified = existing[0].model_copy(update={"description": "Updated biographical note"})
    added_ent = _make_entity(target_tenant, "Analytical Engine", "Machine")
    added_rel = _make_rel(target_tenant, modified.id, added_ent.id, "DESIGNED")
    await client.graph_store.upsert_entities([modified, added_ent])
    await client.graph_store.upsert_relationships([added_rel])

    diff = await client.diff_graphs(target_tenant, base_tenant_id=base_tenant)
    assert diff.is_identical is False
    assert len(diff.entities_added) == 1
    assert len(diff.relationships_added) == 1
    assert len(diff.entities_modified) == 1
    assert diff.entities_modified[0].changed_fields == ("description",)
    assert len(diff.entities_removed) == 0


@settings(max_examples=20)
@given(
    names=st.lists(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz ", min_size=3, max_size=15).filter(
            lambda s: bool(s.strip())
        ),
        min_size=1,
        max_size=6,
        unique=True,
    )
)
def test_property_json_ld_and_graphml_roundtrip_and_diff_symmetry(names: list[str]) -> None:
    t1 = TenantId(uuid4())
    t2 = TenantId(uuid4())
    entities = [_make_entity(t1, n, "Person") for n in names]
    rels = [_make_rel(t1, entities[0].id, entities[1].id, "KNOWS")] if len(entities) >= 2 else []
    json_ld = format_json_ld(entities, rels)
    parsed_e, parsed_r = parse_json_ld(json_ld, tenant_id=t2)
    assert len(parsed_e) == len(entities)
    assert len(parsed_r) == len(rels)

    gml = format_graphml(entities, rels)
    gml_e, gml_r = parse_graphml(gml, tenant_id=t2)
    assert len(gml_e) == len(entities)
    assert len(gml_r) == len(rels)

    store = InMemoryGraphStore()
    asyncio.run(store.upsert_entities(entities + parsed_e))
    asyncio.run(store.upsert_relationships(rels + parsed_r))
    diff = asyncio.run(diff_graphs(t1, t2, graph=store, relationships=store))
    assert diff.is_identical is True
