"""Executable BDD and Hypothesis property tests for US-0028 and US-0029.

Covers:
- US-0028: Consolidation Dry-Run Preview & Tenant-Wide Consolidation Sweep
- US-0029: Ontology Schema Conformance Auditing & Schema Mermaid Visualization
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from redstring import (
    Alias,
    DomainSchema,
    Entity,
    EntityId,
    EntityTypeSchema,
    ExtractionMethod,
    FakeEmbeddingProvider,
    FakeLlmProvider,
    InMemoryGraphStore,
    InMemoryVectorStore,
    MergeDecision,
    PropertySchema,
    Provenance,
    Redstring,
    Relationship,
    RelationshipId,
    RelationshipTypeSchema,
    SourceId,
    TenantId,
    audit_schema_conformance,
    consolidate_tenant,
    format_schema_mermaid,
    preview_consolidation,
)
from redstring.domain.blocking import blocking_keys_for


def _make_entity(
    name: str,
    entity_type: str,
    tenant_id: TenantId,
    *,
    properties: dict[str, Any] | None = None,
) -> Entity:
    base = Entity(
        id=EntityId(uuid4()),
        tenant_id=tenant_id,
        name=name,
        normalized_name=name.lower().strip(),
        entity_type=entity_type,
        properties=properties or {},
        provenance=Provenance(
            observed_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
            extraction_method=ExtractionMethod.LLM,
            confidence=0.95,
            source_id=SourceId("doc-1"),
        ),
    )
    return base.model_copy(update={"blocking_keys": list(blocking_keys_for(base))})


def _make_rel(
    tenant_id: TenantId,
    src: EntityId,
    tgt: EntityId,
    rel_type: str,
) -> Relationship:
    return Relationship(
        id=RelationshipId(uuid4()),
        tenant_id=tenant_id,
        source_entity_id=src,
        target_entity_id=tgt,
        relationship_type=rel_type,
        source_id=SourceId("doc-1"),
        properties={},
        confidence=0.9,
    )


# ===========================================================================
# US-0028: Consolidation Dry-Run Preview & Tenant-Wide Consolidation Sweep
# ===========================================================================


@pytest.mark.asyncio
async def test_scenario_us0028_1_non_destructive_dry_run_consolidation_preview() -> None:
    """US-0028 Scenario 1: Non-destructive dry-run consolidation preview classifies bands."""
    tenant = TenantId(uuid4())
    graph = InMemoryGraphStore()

    e1 = _make_entity("Acme Corporation", "organization", tenant)
    e2 = _make_entity("Acme Corporation", "organization", tenant)
    e3 = _make_entity("Acme Holdings Group", "organization", tenant)
    e4 = _make_entity("Acme Sovereign Infrastructure Trust Syndicate", "organization", tenant)
    await graph.upsert_entities([e1, e2, e3, e4])

    preview = await preview_consolidation(
        tenant,
        graph_store=graph,
        high=0.80,
        low=0.35,
    )

    assert preview.entities_scanned == 4
    assert len(preview.merge_candidates) >= 1
    assert all(c.decision is MergeDecision.MERGE for c in preview.merge_candidates)
    assert all(c.decision is MergeDecision.ADJUDICATE for c in preview.adjudicate_candidates)
    assert all(c.decision is MergeDecision.REJECT for c in preview.reject_candidates)
    assert preview.rejected_count == len(preview.reject_candidates)

    stored_after = await graph.find_entities(tenant, limit=20)
    assert len(stored_after) == 4
    for ent in stored_after:
        assert await graph.find_aliases(ent.id, tenant) == []

    client = Redstring(
        tenant_id=tenant,
        provider=FakeLlmProvider(by_substring={}),
        graph_store=graph,
    )
    scoped = await client.preview_consolidation(subject="Acme Corporation", high=0.80, low=0.35)
    assert scoped.entities_scanned == 1
    assert len(scoped.merge_candidates) >= 1


@pytest.mark.asyncio
async def test_scenario_us0028_2_and_3_tenant_sweep_skips_aliases_and_isolates_tenants() -> None:
    """US-0028 Scenarios 2 & 3: Tenant sweep skips pre-existing aliases and isolates tenants."""
    tenant_a = TenantId(uuid4())
    tenant_b = TenantId(uuid4())
    graph = InMemoryGraphStore()
    vectors = InMemoryVectorStore(dimension=8)
    embeddings = FakeEmbeddingProvider(dimension=8)

    canon = _make_entity("Nova Therapeutics", "organization", tenant_a)
    dup = _make_entity("Nova Therapeutics", "organization", tenant_a)
    partner = _make_entity("BioCore Partner", "organization", tenant_a)
    old_alias_entity = _make_entity("Nova Old Alias", "organization", tenant_a)
    await graph.upsert_entities([canon, dup, partner, old_alias_entity])

    await graph.upsert_alias(
        Alias(
            id=uuid4(),
            alias_entity_id=old_alias_entity.id,
            canonical_entity_id=canon.id,
            alias_name=old_alias_entity.name,
            alias_normalized_name=old_alias_entity.normalized_name,
            tenant_id=tenant_a,
            merged_at=datetime(2026, 5, 2, 12, 0, tzinfo=UTC),
        )
    )
    rel_1 = _make_rel(tenant_a, dup.id, partner.id, "partners_with")
    rel_2 = _make_rel(tenant_a, canon.id, partner.id, "collaborates_with")
    await graph.upsert_relationships([rel_1, rel_2])

    vec = (await embeddings.embed_query([canon.name]))[0]
    await vectors.upsert(canon.id, vec, tenant_a)
    await vectors.upsert(dup.id, vec, tenant_a)

    b1 = _make_entity("Nova Therapeutics", "organization", tenant_b)
    b2 = _make_entity("Nova Therapeutics", "organization", tenant_b)
    await graph.upsert_entities([b1, b2])

    report = await consolidate_tenant(
        tenant_a,
        store=graph,
        vector_store=vectors,
        high=0.80,
        low=0.35,
    )

    assert report.aliases_skipped == 1
    assert report.merges_executed >= 1
    assert report.entities_absorbed >= 1
    assert report.relationships_redirected >= 1

    b_entities = await graph.find_entities(tenant_b, limit=10)
    assert len(b_entities) == 2
    assert await graph.find_aliases(b1.id, tenant_b) == []
    assert await graph.find_aliases(b2.id, tenant_b) == []


# ===========================================================================
# US-0029: Ontology Schema Conformance Auditing & Schema Mermaid Visualization
# ===========================================================================


def _sample_domain_schema() -> DomainSchema:
    return DomainSchema(
        domain_id="clinical_trial",
        display_name="Clinical Trial Ontology",
        description="Schema for clinical trials and sponsors.",
        extraction_prompt_template="Extract clinical trial entities from {text}.",
        entity_types=[
            EntityTypeSchema(
                id="organization",
                description="Sponsor organization",
                properties=[
                    PropertySchema(name="country", type="string", required=True),
                ],
            ),
            EntityTypeSchema(
                id="compound",
                description="Therapeutic compound",
                properties=[
                    PropertySchema(name="modality", type="string", required=False),
                ],
            ),
        ],
        relationship_types=[
            RelationshipTypeSchema(
                id="develops",
                description="Organization develops compound",
                valid_source_types=["organization"],
                valid_target_types=["compound"],
            ),
            RelationshipTypeSchema(
                id="co_develops_with",
                description="Symmetric co-development",
                valid_source_types=["organization"],
                valid_target_types=["organization"],
                bidirectional=True,
            ),
        ],
    )


@pytest.mark.asyncio
async def test_scenario_us0029_1_audit_conforming_tenant_graph() -> None:
    """US-0029 Scenario 1: Audit a fully conforming tenant graph."""
    tenant = TenantId(uuid4())
    schema = _sample_domain_schema()
    graph = InMemoryGraphStore()

    org = _make_entity(
        "Helix Pharma",
        "organization",
        tenant,
        properties={"country": "CH"},
    )
    comp = _make_entity("HX-101", "compound", tenant, properties={"modality": "mRNA"})
    await graph.upsert_entities([org, comp])
    await graph.upsert_relationship(_make_rel(tenant, org.id, comp.id, "develops"))

    report = await audit_schema_conformance(tenant, schema, graph=graph, relationships=graph)
    assert report.conformance_rate == pytest.approx(1.0)
    assert report.violations == ()
    assert report.off_schema_entity_types == ()
    assert report.off_schema_relationship_types == ()


@pytest.mark.asyncio
async def test_scenario_us0029_2_detect_off_schema_types_endpoints_and_missing_props() -> None:
    """US-0029 Scenario 2: Detect off-schema types, invalid endpoints, and missing properties."""
    tenant = TenantId(uuid4())
    schema = _sample_domain_schema()
    graph = InMemoryGraphStore()

    org_missing_prop = _make_entity("Incomplete Org", "organization", tenant, properties={})
    unknown_ent = _make_entity("Starship", "spaceship", tenant)
    comp = _make_entity("HX-202", "compound", tenant)
    await graph.upsert_entities([org_missing_prop, unknown_ent, comp])

    rel_unknown = _make_rel(tenant, org_missing_prop.id, comp.id, "teleports_to")
    rel_bad_endpoints = _make_rel(tenant, comp.id, org_missing_prop.id, "develops")
    await graph.upsert_relationships([rel_unknown, rel_bad_endpoints])

    client = Redstring(
        tenant_id=tenant,
        provider=FakeLlmProvider(by_substring={}),
        graph_store=graph,
    )
    report = await client.audit_schema_conformance(schema)

    assert report.off_schema_entity_types == ("spaceship",)
    assert report.off_schema_relationship_types == ("teleports_to",)
    violation_types = {v.violation_type for v in report.violations}
    assert "unknown_entity_type" in violation_types
    assert "missing_required_property" in violation_types
    assert "unknown_relationship_type" in violation_types
    assert "invalid_source_type" in violation_types
    assert "invalid_target_type" in violation_types
    assert report.conformance_rate == pytest.approx(1 / 5)


def test_scenario_us0029_3_render_domain_schema_ontology_as_mermaid() -> None:
    """US-0029 Scenario 3: Render a DomainSchema ontology as a Mermaid flowchart."""
    schema = _sample_domain_schema()
    mermaid_1 = format_schema_mermaid(schema)
    mermaid_2 = format_schema_mermaid(schema)

    assert mermaid_1 == mermaid_2
    assert mermaid_1.startswith("flowchart LR\n")
    assert 'type_organization["organization (country)"]' in mermaid_1
    assert 'type_compound["compound"]' in mermaid_1
    assert 'type_organization -->|"develops"| type_compound' in mermaid_1
    assert 'type_organization <-->|"co_develops_with"| type_organization' in mermaid_1


@given(
    type_ids=st.lists(
        st.from_regex(r"[a-z][a-z0-9]{1,10}", fullmatch=True),
        min_size=1,
        max_size=5,
        unique=True,
    )
)
@settings(max_examples=30)
def test_property_format_schema_mermaid_deterministic_and_complete(
    type_ids: list[str],
) -> None:
    """Property: format_schema_mermaid is deterministic and emits every entity type node."""
    schema = DomainSchema(
        domain_id="prop_domain",
        display_name="Property Domain",
        description="Generated schema",
        extraction_prompt_template="Extract {text}.",
        entity_types=[EntityTypeSchema(id=tid, description=f"Type {tid}") for tid in type_ids],
        relationship_types=[
            RelationshipTypeSchema(
                id="relates_to",
                description="Generic relation",
                valid_source_types=[type_ids[0]],
                valid_target_types=[type_ids[-1]],
            )
        ],
    )
    rendered = format_schema_mermaid(schema)
    assert rendered == format_schema_mermaid(schema)
    assert rendered.startswith("flowchart LR\n")
    for tid in type_ids:
        assert f'type_{tid}["{tid}"]' in rendered
