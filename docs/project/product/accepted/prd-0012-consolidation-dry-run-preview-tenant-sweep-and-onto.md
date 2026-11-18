---
id: '0012'
title: Consolidation Dry-Run Preview, Tenant Sweep, and Ontology Schema Conformance Auditing
status: Accepted
created: 2026-10-16
target_persona: Kieran
component: composition
release: v0.14.0
---

# PRD-0012 — Consolidation Dry-Run Preview, Tenant Sweep, and Ontology Schema Conformance Auditing

## Who this is for

- **Kieran (The Knowledge Graph Steward & Ontology QA Curator)**: Needs non-destructive consolidation dry-run previews (`preview_consolidation` -> `ConsolidationPreview`), one-call tenant-wide consolidation sweeps (`consolidate_tenant` -> `TenantConsolidationReport`), post-extraction schema conformance auditing (`audit_schema_conformance` -> `SchemaConformanceReport`), and ontology Mermaid visualization (`format_schema_mermaid`).
- **Priya (The Domain Ontologist & Subject-Matter Expert)**: Needs visual Mermaid ontology diagrams and post-extraction schema conformance verification across custom and bundled `DomainSchema` definitions.

## What the person cannot do today

- **No Dry-Run Consolidation Preview**: Calling `Consolidator.resolve` immediately mutates the graph store and emits `EntitiesMerged` events. Data stewards cannot preview candidate duplicates across `MERGE`, `ADJUDICATE`, and `REJECT` bands with their `SimilarityFeatures` before committing merges.
- **Manual Tenant-Wide Consolidation Loops**: Consolidating an entire tenant requires manual pagination over `find_entities` and manual alias filtering via `resolve_entity_ids`.
- **Unverified Post-Extraction Schema Conformance**: `DomainSchema` shapes extraction prompts, but no composed audit verifies stored entities and relationships against declared entity types, `required=True` properties, or relationship source/target endpoint constraints (`valid_source_types` / `valid_target_types`), nor renders a `DomainSchema` ontology itself as a Mermaid diagram.

## What good looks like

1. **Non-Destructive Consolidation Dry-Run (`preview_consolidation`, `Redstring.preview_consolidation`)**:
   - Evaluates blocked candidates for a subject entity (`Entity`, `EntityId`, or name) with `minimum_score=0.0` and classifies each candidate into `MergeDecision.MERGE`, `MergeDecision.ADJUDICATE`, or `MergeDecision.REJECT` without mutating the graph or emitting events.
   - Returns `ConsolidationPreview` with `candidates: tuple[PreviewCandidate, ...]` and properties `merge_candidates`, `adjudicate_candidates`, and `reject_candidates`.

2. **Tenant-Wide Consolidation Sweep (`consolidate_tenant`, `Redstring.consolidate_tenant`)**:
   - Scans all entities in `tenant_id`, skips absorbed aliases via `AliasStore.resolve_entity_ids`, executes `Consolidator.resolve_many`, and returns `TenantConsolidationReport(tenant_id, entities_scanned, aliases_skipped, merges_executed, entities_absorbed, relationships_changed, reports, elapsed_ms)`.

3. **Post-Extraction Schema Conformance Auditing & Mermaid Visualization (`audit_schema_conformance`, `format_schema_mermaid`)**:
   - `audit_schema_conformance` inspects a tenant's entities and relationships against a `DomainSchema` (or bundled domain ID) and returns `SchemaConformanceReport` detailing `conforming_entities`, `off_schema_entity_types`, `missing_required_properties_count`, `conforming_relationships`, `off_schema_relationship_types`, `endpoint_violations_count`, `violations: tuple[SchemaViolation, ...]`, and `conformance_rate`.
   - `format_schema_mermaid(schema)` renders any `DomainSchema` or bundled domain ID into a deterministic `flowchart LR` Mermaid diagram depicting entity types, required properties (`*`), and directed (`-->`) or bidirectional (`<-->`) relationship types.

## What this does not do

- **Mutate Graph During Preview or Schema Audit**: `preview_consolidation` and `audit_schema_conformance` are strictly read-only.

## Checkable Outcomes

1. Calling `preview_consolidation` partitions candidate duplicates into `merge_candidates`, `adjudicate_candidates`, and `reject_candidates` with `SimilarityFeatures` while leaving `GraphStore` entities and aliases completely unchanged.
2. Calling `consolidate_tenant` sweeps a tenant's canonical entities, skips pre-existing aliases, merges duplicate clusters, and returns accurate `entities_scanned`, `aliases_skipped`, `merges_executed`, and `entities_absorbed` counts.
3. Calling `audit_schema_conformance` detects unknown entity types, missing required properties, unknown relationship types, and invalid relationship source/target endpoint types, computing an exact `conformance_rate` in `[0.0, 1.0]`.
4. Calling `format_schema_mermaid` produces a valid, deterministic `flowchart LR` diagram for any custom `DomainSchema` or bundled domain ID.

## Linked User Stories

- [`US-0028`](../../user_stories/accepted/us-0028-non-destructive-consolidation-dry-run-preview-and-t.md): Non-Destructive Consolidation Dry-Run Preview and Tenant-Wide Sweep
- [`US-0029`](../../user_stories/accepted/us-0029-post-extraction-ontology-schema-conformance-auditin.md): Post-Extraction Ontology Schema Conformance Auditing and Schema Mermaid Visualization
