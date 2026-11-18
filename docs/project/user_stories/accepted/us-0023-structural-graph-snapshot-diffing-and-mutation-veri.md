---
id: '0023'
title: "Structural Graph Snapshot Diffing and Mutation Verification"
status: Accepted
created: 2026-10-16
persona: "Soren"
target_bc: "composition"
feature: "FEAT-COMP-23"
governing_prd: "PRD-0009"
release: "v0.14.0"
scenarios:
  - "Detect added, removed, and attribute-modified entities and relationships across tenants"
  - "Verify round-trip identity via diff_graphs after snapshot restoration"
---

# US-0023 — Structural Graph Snapshot Diffing and Mutation Verification

## Governing PRD
- [`PRD-0009: Graph Topology Diagnostics, Hub Centrality Reporting, and Snapshot Diffing`](../../product/accepted/prd-0009-graph-topology-diagnostics-hub-centrality-reporting.md)

## Governing ADRs
- [`ADR-0149: Unified Redstring Client Facade and In-Memory Indexing Architecture`](../../adrs/accepted/adr-0149-unified-redstring-client-facade-and-in-memory-inde.md)
- [`ADR-0006: Executable BDD User Stories`](../../adrs/accepted/adr-0006-executable-bdd-user-stories.md)

## User Story

**As a** Soren (The Network Scientist & Graph Topology Auditor),
**I want** `diff_graphs` and `Redstring.diff_graphs` to compare two tenant graphs or snapshots and return a `GraphDiffReport` enumerating added, removed, and modified entities and relationships,
**So that** I can quantify exact structural changes introduced by document ingestion, entity consolidation, or snapshot restoration.

## Acceptance Criteria

```gherkin
Scenario: Detect added, removed, and attribute-modified entities and relationships across tenants
  Given a baseline tenant snapshot and a target tenant cloned via export_graph and import_graph
  When a new entity and relationship are added to the target tenant and an existing entity's description is updated
  And I call client.diff_graphs comparing the baseline tenant and target tenant
  Then the GraphDiffReport lists 1 added entity, 1 added relationship, 1 modified entity with changed_fields ("description",), and 0 removed records
  And report.is_identical is False.
```

```gherkin
Scenario: Verify round-trip identity via diff_graphs after snapshot restoration
  Given a baseline tenant graph and a fresh target tenant hydrated from the baseline's JSON-LD export
  When I call client.diff_graphs comparing the baseline tenant and target tenant
  Then report.is_identical is True with 0 added, 0 removed, and 0 modified entities or relationships.
```
