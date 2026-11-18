---
id: '0029'
title: "Post-Extraction Ontology Schema Conformance Auditing and Schema Mermaid Visualization"
status: Accepted
created: 2026-10-16
persona: "Kieran"
target_bc: "composition"
feature: "FEAT-COMP-29"
governing_prd: "PRD-0012"
release: "v0.14.0"
scenarios:
  - "Audit a fully conforming tenant graph against a DomainSchema"
  - "Detect off-schema types, missing required properties, and invalid relationship endpoints"
  - "Render a DomainSchema ontology as a deterministic Mermaid flowchart"
---

# US-0029 — Post-Extraction Ontology Schema Conformance Auditing and Schema Mermaid Visualization

## Governing PRD
- [`PRD-0012: Consolidation Dry-Run Preview, Tenant Sweep, and Ontology Schema Conformance Auditing`](../../product/accepted/prd-0012-consolidation-dry-run-preview-tenant-sweep-and-onto.md)

## Governing ADRs
- [`ADR-0111: Domain Schemas Shape Prompts and Validate Nothing`](../../adrs/accepted/adr-0111-domain-schemas-shape-prompts-and-validate-nothing.md)
- [`ADR-0149: Unified Redstring Client Facade and In-Memory Indexing Architecture`](../../adrs/accepted/adr-0149-unified-redstring-client-facade-and-in-memory-inde.md)
- [`ADR-0006: Executable BDD User Stories`](../../adrs/accepted/adr-0006-executable-bdd-user-stories.md)

## User Story

**As a** Kieran (The Knowledge Graph Steward & Ontology QA Curator) and Priya (The Domain Ontologist),
**I want** `audit_schema_conformance` to check a tenant's stored entities and relationships against a `DomainSchema` (or bundled domain ID) and `format_schema_mermaid` to render any `DomainSchema` as a Mermaid `flowchart LR` diagram,
**So that** I can measure post-extraction schema compliance, catch off-schema types or missing required properties, and publish visual ontology blueprints.

## Acceptance Criteria

```gherkin
Scenario: Audit a fully conforming tenant graph against a DomainSchema
  Given a DomainSchema declaring entity types "fault" and "site" (with required property "region") and relationship "observed_at" from "fault" to "site"
  And a tenant graph containing a "fault" entity, a "site" entity with property "region", and an "observed_at" relationship from the fault to the site
  When I call client.audit_schema_conformance with the DomainSchema
  Then a SchemaConformanceReport is returned with conformance_rate 1.0 and zero violations
  And conforming_entities is 2 and conforming_relationships is 1.
```

```gherkin
Scenario: Detect off-schema types, missing required properties, and invalid relationship endpoints
  Given a DomainSchema declaring entity types "fault" and "site" (with required property "region") and relationship "observed_at" from "fault" to "site"
  And a tenant graph containing a "site" entity missing required property "region", an off-schema "person" entity, a reversed "observed_at" edge, and an off-schema "invented_by" edge
  When I call client.audit_schema_conformance with the DomainSchema
  Then the SchemaConformanceReport lists violations for "missing_required_property", "unknown_entity_type", "invalid_source_type", and "unknown_relationship_type"
  And off_schema_entity_types contains "person" and off_schema_relationship_types contains "invented_by"
  And conformance_rate is strictly less than 1.0.
```

```gherkin
Scenario: Render a DomainSchema ontology as a deterministic Mermaid flowchart
  Given a DomainSchema or bundled domain ID with constrained and bidirectional relationship types
  When I call format_schema_mermaid with the schema
  Then a deterministic Mermaid "flowchart LR" diagram is returned
  And every declared entity type node, required property marker, directed arrow "-->", and bidirectional arrow "<-->" is present.
```
