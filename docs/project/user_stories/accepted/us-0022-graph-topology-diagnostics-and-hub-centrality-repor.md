---
id: '0022'
title: "Graph Topology Diagnostics and Hub Centrality Reporting"
status: Accepted
created: 2026-10-16
persona: "Soren"
target_bc: "composition"
feature: "FEAT-COMP-22"
governing_prd: "PRD-0009"
release: "v0.14.0"
scenarios:
  - "Compute comprehensive topology diagnostics and top-k hub entities"
  - "Sub-500ms performance bound on multi-hundred-node topology analysis"
---

# US-0022 — Graph Topology Diagnostics and Hub Centrality Reporting

## Governing PRD
- [`PRD-0009: Graph Topology Diagnostics, Hub Centrality Reporting, and Snapshot Diffing`](../../product/accepted/prd-0009-graph-topology-diagnostics-hub-centrality-reporting.md)

## Governing ADRs
- [`ADR-0149: Unified Redstring Client Facade and In-Memory Indexing Architecture`](../../adrs/accepted/adr-0149-unified-redstring-client-facade-and-in-memory-inde.md)
- [`ADR-0006: Executable BDD User Stories`](../../adrs/accepted/adr-0006-executable-bdd-user-stories.md)

## User Story

**As a** Soren (The Network Scientist & Graph Topology Auditor),
**I want** `analyze_topology` and `Redstring.analyze_topology` to return a `GraphTopologyReport` with entity/relationship type distributions, connected component counts, isolated singleton counts, degree metrics, and top-$k$ `EntityHub` rankings,
**So that** I can audit structural connectivity and identify central hub entities without LLM cost.

## Acceptance Criteria

```gherkin
Scenario: Compute comprehensive topology diagnostics and top-k hub entities
  Given a tenant graph with two connected multi-node clusters, one isolated singleton entity, and a central hub entity with the highest degree
  When I call client.analyze_topology with top_k 3
  Then a GraphTopologyReport is returned with accurate entity_count, relationship_count, entity_type_counts, and relationship_type_counts
  And connected_components is 3, isolated_entities is 1, and top_hubs ranks the central hub entity first with exact in_degree, out_degree, and degree.
```

```gherkin
Scenario: Sub-500ms performance bound on multi-hundred-node topology analysis
  Given a tenant graph containing 200 entities and 300 relationships
  When I run analyze_topology for the tenant
  Then the GraphTopologyReport completes deterministically with elapsed_ms < 500.0.
```
