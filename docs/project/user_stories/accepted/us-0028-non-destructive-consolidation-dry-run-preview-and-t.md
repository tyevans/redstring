---
id: '0028'
title: "Non-Destructive Consolidation Dry-Run Preview and Tenant-Wide Sweep"
status: Accepted
created: 2026-10-16
persona: "Kieran"
target_bc: "composition"
feature: "FEAT-COMP-28"
governing_prd: "PRD-0012"
release: "v0.14.0"
scenarios:
  - "Preview consolidation candidates across MERGE, ADJUDICATE, and REJECT bands without mutating the graph"
  - "Execute a tenant-wide consolidation sweep skipping pre-existing aliases"
---

# US-0028 — Non-Destructive Consolidation Dry-Run Preview and Tenant-Wide Sweep

## Governing PRD
- [`PRD-0012: Consolidation Dry-Run Preview, Tenant Sweep, and Ontology Schema Conformance Auditing`](../../product/accepted/prd-0012-consolidation-dry-run-preview-tenant-sweep-and-onto.md)

## Governing ADRs
- [`ADR-0115: Entity Consolidation Uses Hybrid Similarity and Three-Tier Confidence`](../../adrs/accepted/adr-0115-entity-consolidation-uses-hybrid-similarity-and-thr.md)
- [`ADR-0149: Unified Redstring Client Facade and In-Memory Indexing Architecture`](../../adrs/accepted/adr-0149-unified-redstring-client-facade-and-in-memory-inde.md)
- [`ADR-0006: Executable BDD User Stories`](../../adrs/accepted/adr-0006-executable-bdd-user-stories.md)

## User Story

**As a** Kieran (The Knowledge Graph Steward & Ontology QA Curator),
**I want** `preview_consolidation` to evaluate candidate merges across `MERGE`, `ADJUDICATE`, and `REJECT` bands without mutating the store, and `consolidate_tenant` to sweep all canonical entities in a tenant and return a `TenantConsolidationReport`,
**So that** I can calibrate similarity thresholds safely before committing merges and run one-call deduplication sweeps across an entire tenant.

## Acceptance Criteria

```gherkin
Scenario: Preview consolidation candidates across MERGE, ADJUDICATE, and REJECT bands without mutating the graph
  Given a tenant graph containing subject entity "Ada Lovelace" and blocked candidates across high, middle, and low similarity bands
  When I call client.preview_consolidation for "Ada Lovelace" with high 0.90 and low 0.70
  Then a ConsolidationPreview is returned containing PreviewCandidate entries with SimilarityFeatures and MergeDecision bands
  And preview.merge_candidates, preview.adjudicate_candidates, and preview.reject_candidates partition the candidates by threshold
  And the tenant graph retains all original unmerged entities with zero aliases recorded.
```

```gherkin
Scenario: Execute a tenant-wide consolidation sweep skipping pre-existing aliases
  Given a tenant graph with duplicate entity pairs, an already-merged alias, and entities belonging to another tenant
  When I call client.consolidate_tenant with high 0.85 and low 0.70
  Then a TenantConsolidationReport is returned with entities_scanned, aliases_skipped >= 1, merges_executed >= 1, and elapsed_ms >= 0.0
  And duplicate entities in the active tenant are merged into their canonical entities
  And entities in the other tenant remain untouched.
```
