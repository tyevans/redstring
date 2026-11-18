"""Executable BDD Acceptance Criteria for US-0013: Domain Schemas, AUTO, and Gleaning.

Governed by:
- ADR-0129 (A Chunk Is Not Extracted Alone)
- ADR-0130 (A Domain Schema May Constrain When Asked)
- ADR-0002 (File Length Limit)
- ADR-0003 (Blackbox Frontdoor Verification)
- ADR-0006 (Executable BDD User Stories)
- PRD-0004
- US-0013
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from redstring import (
    AUTO,
    DomainSchema,
    FakeLlmProvider,
    InMemoryGraphStore,
    SlidingWindowChunker,
    SourceDocument,
    SourceId,
    TenantId,
    build_graph,
    domain_system_prompt,
    load_schema_from_string,
)

CUSTOM_BIOMEDICAL_YAML = """
domain_id: biomedical_research
display_name: Biomedical Research
description: Clinical trials, therapeutics, and biomedical literature.
entity_types:
  - id: ClinicalTrial
    description: A registered clinical study
    properties:
      - name: phase
        type: string
        description: Trial phase
    examples:
      - KEYNOTE-001
  - id: Drug
    description: A pharmaceutical compound or biologic
    properties:
      - name: mechanism
        type: string
        description: Mechanism of action
    examples:
      - Pembrolizumab
relationship_types:
  - id: EVALUATES
    description: Trial evaluates a drug
    valid_source_types: [ClinicalTrial]
    valid_target_types: [Drug]
extraction_prompt_template: |
  You are analyzing biomedical clinical trial literature.
  Extract the following entity types:
  {entity_descriptions}
  Extract the following relationship types:
  {relationship_descriptions}
confidence_thresholds:
  entity_extraction: 0.7
  relationship_extraction: 0.6
version: "1.0.0"
"""


def test_scenario_load_custom_yaml_domain_schema_and_generate_system_prompt() -> None:
    """Scenario: Load custom YAML domain schema and generate system prompt (US-0013)."""
    schema = load_schema_from_string(CUSTOM_BIOMEDICAL_YAML)
    assert isinstance(schema, DomainSchema)
    assert schema.domain_id == "biomedical_research"

    prompt = domain_system_prompt(schema)
    assert "clinicaltrial" in prompt
    assert "drug" in prompt
    assert "evaluates" in prompt


@pytest.mark.asyncio
async def test_scenario_auto_classify_document_domain_with_fallback_on_short_text() -> None:
    """Scenario: Auto-classify document domain with fallback on short text (US-0013)."""
    tenant_id = TenantId(uuid4())
    store = InMemoryGraphStore()

    extraction_payload = {
        "entities": [{"name": "Ada Lovelace", "entity_type": "person"}],
        "relationships": [],
    }
    classification_payload = {
        "domain": "academic_research",
        "confidence": 0.92,
        "reasoning": "Discusses analytical engines and computer science.",
    }
    provider = FakeLlmProvider(
        by_substring={
            "Classify the following content": classification_payload,
        },
        default=extraction_payload,
    )

    # 1. Short document (<100 chars) falls back without calling classifier
    short_doc = SourceDocument(id=SourceId("short-doc"), text="Ada Lovelace wrote notes.")
    short_report = await build_graph(
        short_doc,
        provider=provider,
        store=store,
        tenant_id=tenant_id,
        domain=AUTO,
    )
    assert short_report.domain == "encyclopedia_wiki"
    assert short_report.domain_confidence == 0.0

    # 2. Full-length document (>=100 chars) classifies and records positive confidence
    long_text = (
        "Ada Lovelace collaborated extensively with Charles Babbage on the Analytical Engine, "
        "authoring algorithmic notes in 1843 that are recognized as the first computer program."
    )
    long_doc = SourceDocument(id=SourceId("long-doc"), text=long_text)
    long_report = await build_graph(
        long_doc,
        provider=provider,
        store=store,
        tenant_id=tenant_id,
        domain=AUTO,
    )
    assert long_report.domain == "academic_research"
    assert long_report.domain_confidence == pytest.approx(0.92)


@pytest.mark.asyncio
async def test_scenario_constrain_extracted_entities_to_domain_schema_vocabulary() -> None:
    """Scenario: Constrain extracted entities and relationships to domain schema (US-0013)."""
    tenant_id = TenantId(uuid4())
    store = InMemoryGraphStore()
    schema = load_schema_from_string(CUSTOM_BIOMEDICAL_YAML)

    answer = {
        "entities": [
            {"name": "KEYNOTE-001", "entity_type": "clinicaltrial"},
            {"name": "Pembrolizumab", "entity_type": "drug"},
        ],
        "relationships": [
            {
                "source_name": "KEYNOTE-001",
                "target_name": "Pembrolizumab",
                "relationship_type": "evaluates",
            }
        ],
    }
    provider = FakeLlmProvider(by_substring={}, default=answer)
    doc = SourceDocument(
        id=SourceId("trial-doc"),
        text="Clinical trial KEYNOTE-001 evaluates the efficacy of Pembrolizumab.",
    )

    report = await build_graph(
        doc,
        provider=provider,
        store=store,
        tenant_id=tenant_id,
        domain=schema,
        constrain_to_domain=True,
    )

    assert report.domain == "biomedical_research"
    assert report.entities == 2
    assert report.relationships == 1
    entities = await store.find_entities(tenant_id)
    assert {e.entity_type for e in entities} == {"clinicaltrial", "drug"}


@pytest.mark.asyncio
async def test_scenario_execute_multi_pass_gleaning_and_cross_chunk_carryover() -> None:
    """Scenario: Execute multi-pass gleaning and cross-chunk entity carryover (US-0013)."""
    tenant_id = TenantId(uuid4())
    store = InMemoryGraphStore()

    initial_answer = {
        "entities": [{"name": "Ada Lovelace", "entity_type": "Person"}],
        "relationships": [],
    }
    gleaned_answer = {
        "entities": [
            {"name": "Ada Lovelace", "entity_type": "Person"},
            {"name": "Charles Babbage", "entity_type": "Person"},
        ],
        "relationships": [
            {
                "source_name": "Ada Lovelace",
                "target_name": "Charles Babbage",
                "relationship_type": "COLLABORATED_WITH",
            }
        ],
    }
    # Scripted responses: pass 1 (initial) -> pass 2 (gleaning)
    provider = FakeLlmProvider(script=[initial_answer, gleaned_answer])
    chunker = SlidingWindowChunker(default_chunk_size=500, default_overlap=50)
    doc = SourceDocument(
        id=SourceId("gleaning-doc"),
        text=(
            "Ada Lovelace studied mathematics in London and corresponded with "
            "Charles Babbage regarding the design of the Analytical Engine."
        ),
    )

    report = await build_graph(
        doc,
        provider=provider,
        store=store,
        tenant_id=tenant_id,
        chunker=chunker,
        gleanings=1,
        carryover_entities=10,
    )

    assert report.entities == 2
    assert report.relationships == 1
    entities = await store.find_entities(tenant_id)
    names = {e.name for e in entities}
    assert names == {"Ada Lovelace", "Charles Babbage"}
