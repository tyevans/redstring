---
id: '0021'
title: "Filtered Subgraph and Ego-Network Visualization Export"
status: Accepted
created: 2026-10-16
persona: "Elena"
target_bc: "composition"
feature: "FEAT-COMP-21"
governing_prd: "PRD-0008"
release: "v0.14.0"
scenarios:
  - "Export focused k-hop ego-network subgraph around a center entity"
  - "Filter exported subgraph by entity_types and relationship_types"
---

# US-0021 — Filtered Subgraph and Ego-Network Visualization Export

## Governing PRD
- [`PRD-0008: Portable Graph Import, Lossless Round-Tripping, and Filtered Subgraph Visualization`](../../product/accepted/prd-0008-portable-graph-import-lossless-round-tripping-and-f.md)

## Governing ADRs
- [`ADR-0149: Unified Redstring Client Facade and In-Memory Indexing Architecture`](../../adrs/accepted/adr-0149-unified-redstring-client-facade-and-in-memory-inde.md)
- [`ADR-0006: Executable BDD User Stories`](../../adrs/accepted/adr-0006-executable-bdd-user-stories.md)

## User Story

**As a** Elena (The Temporal & Investigative Intelligence Analyst),
**I want** `export_graph` and `Redstring.export_graph` to accept `center_entity_id`, `max_depth`, `entity_types`, and `relationship_types` filters,
**So that** I can export focused k-hop ego networks and type-filtered subgraphs into Mermaid, JSON-LD, or GraphML without cluttering visualizations with unrelated nodes.

## Acceptance Criteria

```gherkin
Scenario: Export focused k-hop ego-network subgraph around a center entity
  Given a multi-hop tenant graph connecting "Ada Lovelace" to "Charles Babbage" (1 hop) and "London" (2 hops)
  When I call client.export_graph with center_entity_id "Ada Lovelace" and max_depth 1
  Then the exported diagram contains "Ada Lovelace" and "Charles Babbage" and their connecting relationship
  And "London" (at 2 hops) is excluded from the exported subgraph.
```

```gherkin
Scenario: Filter exported subgraph by entity_types and relationship_types
  Given a tenant graph containing "Person" and "Machine" entities connected by "COLLABORATED_WITH" and "DESIGNED" relationships
  When I call client.export_graph with entity_types ["Person"] and relationship_types ["COLLABORATED_WITH"]
  Then only "Person" entities and "COLLABORATED_WITH" edges between them appear in the exported output.
```
