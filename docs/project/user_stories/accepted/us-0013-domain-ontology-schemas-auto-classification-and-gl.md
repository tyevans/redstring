---
id: '0013'
title: "Domain Ontology Schemas, Auto-Classification, and Multi-Pass Gleaning"
status: Accepted
created: 2026-10-16
persona: "Priya"
target_bc: "extraction"
feature: "FEAT-EXTR-13"
governing_prd: "PRD-0004"
scenarios:
  - "Load custom YAML domain schema and generate system prompt"
  - "Auto-classify document domain with fallback on short text"
  - "Constrain extracted entities and relationships to domain schema vocabulary"
  - "Execute multi-pass gleaning and cross-chunk entity carryover"
---

# US-0013 — Domain Ontology Schemas, Auto-Classification, and Multi-Pass Gleaning

## Governing PRD
- [`PRD-0004: Domain Ontology Schemas, Auto-Classification, and Multi-Pass Gleaning`](../../product/accepted/prd-0004-domain-ontology-schemas-auto-classification-and-gl.md)

## Governing ADRs
- [`ADR-0129: A Chunk Is Not Extracted Alone`](../../adrs/accepted/adr-0129-a-chunk-is-not-extracted-alone.md)
- [`ADR-0130: A Domain Schema May Constrain When Asked`](../../adrs/accepted/adr-0130-a-domain-schema-may-constrain-when-asked.md)

## User Story

**As a** Priya (The Domain Ontologist & Subject-Matter Expert),
**I want** declarative YAML `DomainSchema` specifications, automatic domain classification (`domain=AUTO`), vocabulary enforcement (`constrain_to_domain=True`), and multi-pass gleaning (`gleanings`),
**So that** LLM extraction adheres strictly to my domain ontology and achieves high recall across multi-chunk documents without manual code changes.

## Acceptance Criteria

```gherkin
Scenario: Load custom YAML domain schema and generate system prompt
  Given a valid YAML domain schema string defining "ClinicalTrial" and "Drug" entity types
  When I call load_schema_from_string and pass the schema to domain_system_prompt
  Then a validated DomainSchema object is returned with domain_id "biomedical_research"
  And the generated system prompt includes instructions for "ClinicalTrial" and "Drug".
```

```gherkin
Scenario: Auto-classify document domain with fallback on short text
  Given a Redstring client or build_graph pipeline with domain set to AUTO
  When I extract a short document under 100 characters and a full-length encyclopedia document
  Then the short document falls back to "encyclopedia_wiki" with domain_confidence 0.0
  And the full-length document records the classified domain and a positive domain_confidence in GraphBuildReport.
```

```gherkin
Scenario: Constrain extracted entities and relationships to domain schema vocabulary
  Given a DomainSchema that permits only "Person" and "Organization" entity types
  When build_graph runs with constrain_to_domain set to True
  Then extracted entities conform strictly to the allowed DomainSchema vocabulary.
```

```gherkin
Scenario: Execute multi-pass gleaning and cross-chunk entity carryover
  Given a multi-chunk document and an LLM provider returning supplementary entities on follow-up passes
  When build_graph runs with gleanings set to 1 and carryover_entities enabled
  Then entities discovered in the gleaning pass are merged with the initial extraction into the graph store.
```
