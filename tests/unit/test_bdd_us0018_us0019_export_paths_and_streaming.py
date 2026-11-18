"""Executable BDD Acceptance Criteria for US-0018 and US-0019: Export, Paths & Streaming.

Governed by:
- ADR-0149 (Unified Redstring Client Facade and In-Memory Indexing Architecture)
- ADR-0002 (File Length Limit)
- ADR-0003 (Blackbox Frontdoor Verification)
- ADR-0006 (Executable BDD User Stories)
- PRD-0007
- US-0018, US-0019
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from redstring import (
    Entity,
    EntityId,
    ExportFormat,
    ExtractionMethod,
    FakeEmbeddingProvider,
    FakeLlmProvider,
    GraphBuildReport,
    PathExplanation,
    Provenance,
    Redstring,
    Relationship,
    RelationshipId,
    SourceDocument,
    SourceId,
    TenantId,
    format_graphml,
    format_json_ld,
    format_mermaid,
)

PIONEERS_PAYLOAD = {
    "entities": [
        {"name": "Ada Lovelace", "entity_type": "Person", "description": "Mathematician"},
        {"name": "Charles Babbage", "entity_type": "Person", "description": "Inventor"},
        {"name": "Analytical Engine", "entity_type": "Machine", "description": "Computer"},
    ],
    "relationships": [
        {
            "source_name": "Ada Lovelace",
            "target_name": "Charles Babbage",
            "relationship_type": "COLLABORATED_WITH",
        },
        {
            "source_name": "Charles Babbage",
            "target_name": "Analytical Engine",
            "relationship_type": "DESIGNED",
        },
    ],
}


@pytest.mark.asyncio
async def test_scenario_export_tenant_knowledge_graph_to_mermaid() -> None:
    """Scenario: Export tenant knowledge graph to Mermaid flowchart diagram (US-0018)."""
    provider = FakeLlmProvider(by_substring={}, default=PIONEERS_PAYLOAD)
    embeddings = FakeEmbeddingProvider(dimension=16)
    tenant_id = TenantId(uuid4())
    client = Redstring.in_memory(provider=provider, embeddings=embeddings, tenant_id=tenant_id)

    await client.build_graph(
        SourceDocument(
            id=SourceId("doc-mermaid"),
            text="Ada Lovelace collaborated with Charles Babbage who designed Analytical Engine.",
        )
    )

    mermaid_text = await client.export_graph(format=ExportFormat.MERMAID)
    assert mermaid_text.startswith("flowchart LR")
    assert "Ada Lovelace" in mermaid_text
    assert "Charles Babbage" in mermaid_text
    assert "Analytical Engine" in mermaid_text
    assert "COLLABORATED_WITH" in mermaid_text
    assert "DESIGNED" in mermaid_text


@pytest.mark.asyncio
async def test_scenario_export_tenant_knowledge_graph_to_json_ld() -> None:
    """Scenario: Export tenant knowledge graph to JSON-LD linked data document (US-0018)."""
    provider = FakeLlmProvider(by_substring={}, default=PIONEERS_PAYLOAD)
    embeddings = FakeEmbeddingProvider(dimension=16)
    tenant_id = TenantId(uuid4())
    client = Redstring.in_memory(provider=provider, embeddings=embeddings, tenant_id=tenant_id)

    await client.build_graph("Ada Lovelace collaborated with Charles Babbage.")

    json_ld_str = await client.export_graph(format="json-ld")
    parsed = json.loads(json_ld_str)

    assert "@context" in parsed
    assert "@graph" in parsed
    names = {node["name"] for node in parsed["@graph"]}
    assert {"Ada Lovelace", "Charles Babbage", "Analytical Engine"} <= names


@pytest.mark.asyncio
async def test_scenario_export_tenant_knowledge_graph_to_graphml() -> None:
    """Scenario: Export tenant knowledge graph to GraphML XML format (US-0018)."""
    provider = FakeLlmProvider(by_substring={}, default=PIONEERS_PAYLOAD)
    embeddings = FakeEmbeddingProvider(dimension=16)
    tenant_a = TenantId(uuid4())
    tenant_b = TenantId(uuid4())
    client = Redstring.in_memory(provider=provider, embeddings=embeddings, tenant_id=tenant_a)

    await client.build_graph("Ada Lovelace collaborated with Charles Babbage.")

    # Populate another tenant to verify strict tenant isolation in export
    other_provider = FakeLlmProvider(
        by_substring={},
        default={
            "entities": [{"name": "Secret Other Tenant", "entity_type": "Secret"}],
            "relationships": [],
        },
    )
    await build_other_tenant(client, other_provider, tenant_b)

    graphml_str = await client.export_graph(format=ExportFormat.GRAPHML)
    root = ET.fromstring(graphml_str)
    assert root.tag.endswith("graphml")
    assert "Ada Lovelace" in graphml_str
    assert "Secret Other Tenant" not in graphml_str


async def build_other_tenant(
    client: Redstring,
    provider: FakeLlmProvider,
    tenant_b: TenantId,
) -> None:
    from redstring import build_graph

    await build_graph(
        SourceDocument(id=SourceId("other-doc"), text="Secret Other Tenant"),
        provider=provider,
        store=client.graph_store,
        tenant_id=tenant_b,
    )


@pytest.mark.asyncio
async def test_scenario_discover_shortest_connection_path_with_chunk_citations() -> None:
    """Scenario: Discover shortest connection path between two entities (US-0019)."""
    provider = FakeLlmProvider(by_substring={}, default=PIONEERS_PAYLOAD)
    embeddings = FakeEmbeddingProvider(dimension=16)
    tenant_id = TenantId(uuid4())
    client = Redstring.in_memory(provider=provider, embeddings=embeddings, tenant_id=tenant_id)

    await client.build_graph(
        SourceDocument(
            id=SourceId("doc-path"),
            text="Ada Lovelace collaborated with Charles Babbage on the Analytical Engine.",
        )
    )

    explanation = await client.explain_path("Ada Lovelace", "Analytical Engine", max_depth=3)
    assert isinstance(explanation, PathExplanation)
    assert [e.name for e in explanation.entities] == [
        "Ada Lovelace",
        "Charles Babbage",
        "Analytical Engine",
    ]
    assert len(explanation.relationships) == 2
    assert [r.relationship_type for r in explanation.relationships] == [
        "COLLABORATED_WITH",
        "DESIGNED",
    ]
    assert len(explanation.chunks) >= 1
    assert "Ada Lovelace" in explanation.chunks[0].text


@pytest.mark.asyncio
async def test_scenario_return_none_when_no_path_exists_within_max_depth() -> None:
    """Scenario: Return None when no path exists within max_depth hops (US-0019)."""
    provider = FakeLlmProvider(by_substring={}, default=PIONEERS_PAYLOAD)
    embeddings = FakeEmbeddingProvider(dimension=16)
    tenant_id = TenantId(uuid4())
    client = Redstring.in_memory(provider=provider, embeddings=embeddings, tenant_id=tenant_id)

    await client.build_graph("Ada Lovelace collaborated with Charles Babbage.")

    # Analytical Engine is 2 hops from Ada Lovelace, so max_depth=1 returns None
    explanation = await client.explain_path("Ada Lovelace", "Analytical Engine", max_depth=1)
    assert explanation is None

    # Unknown entity returns None
    assert await client.explain_path("Ada Lovelace", "Nonexistent Person", max_depth=3) is None


@pytest.mark.asyncio
async def test_scenario_stream_incremental_graph_build_reports_as_documents_complete() -> None:
    """Scenario: Stream incremental GraphBuildReports as batch documents complete (US-0019)."""
    provider = FakeLlmProvider(by_substring={}, default=PIONEERS_PAYLOAD)
    embeddings = FakeEmbeddingProvider(dimension=16)
    tenant_id = TenantId(uuid4())
    client = Redstring.in_memory(provider=provider, embeddings=embeddings, tenant_id=tenant_id)

    docs = [f"Document {i}: Ada Lovelace and Charles Babbage." for i in range(4)]
    yielded_reports: list[GraphBuildReport] = []

    async for item in client.build_graph_stream(docs, max_concurrency=2):
        assert isinstance(item, GraphBuildReport)
        yielded_reports.append(item)

    assert len(yielded_reports) == 4
    entities = await client.graph.find_entities(tenant_id)
    assert len(entities) >= 3


# ---------------------------------------------------------------------------
# Generative Property-Based Tests (ADR-0009)
# ---------------------------------------------------------------------------
_SAFE_TEXT = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd", "Zs"),
        whitelist_characters='"-_().',
    ),
    min_size=1,
    max_size=30,
).filter(lambda s: bool(s.strip()))


@given(
    names=st.lists(_SAFE_TEXT, min_size=2, max_size=5, unique=True),
)
def test_property_export_serializers_are_deterministic_and_well_formed(
    names: list[str],
) -> None:
    """Property: Mermaid, JSON-LD, and GraphML serializers are deterministic and parseable."""
    tenant_id = TenantId(uuid4())
    prov = Provenance(
        source_id=SourceId("prop-src"),
        observed_at=datetime(2025, 1, 1, tzinfo=UTC),
        extraction_method=ExtractionMethod.LLM,
        confidence=0.9,
    )
    entities = [
        Entity(
            id=EntityId(uuid4()),
            tenant_id=tenant_id,
            name=name,
            normalized_name=name.lower(),
            entity_type="Concept",
            provenance=prov,
        )
        for name in names
    ]
    rel = Relationship(
        id=RelationshipId(uuid4()),
        tenant_id=tenant_id,
        source_entity_id=entities[0].id,
        target_entity_id=entities[1].id,
        relationship_type="RELATES_TO",
        confidence=0.9,
        provenance=prov,
    )

    reversed_entities = list(reversed(entities))

    # Permutation invariance (canonical ordering by EntityId)
    mermaid_fwd = format_mermaid(entities, [rel])
    mermaid_rev = format_mermaid(reversed_entities, [rel])
    assert mermaid_fwd == mermaid_rev
    assert mermaid_fwd.startswith("flowchart LR")

    json_ld_fwd = format_json_ld(entities, [rel])
    json_ld_rev = format_json_ld(reversed_entities, [rel])
    assert json_ld_fwd == json_ld_rev
    parsed_json = json.loads(json_ld_fwd)
    assert len(parsed_json["@graph"]) == len(entities)

    graphml_fwd = format_graphml(entities, [rel])
    graphml_rev = format_graphml(reversed_entities, [rel])
    assert graphml_fwd == graphml_rev
    xml_root = ET.fromstring(graphml_fwd)
    assert xml_root.tag.endswith("graphml")
