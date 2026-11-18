---
id: '0019'
title: "Explainable Entity Connection Paths and Streaming Batch Ingestion"
status: Accepted
created: 2026-10-16
persona: "Elena"
target_bc: "composition"
feature: "FEAT-COMP-19"
governing_prd: "PRD-0007"
scenarios:
  - "Discover shortest connection path between two entities with source chunk citations"
  - "Return None when no path exists within max_depth hops"
  - "Stream incremental GraphBuildReports as batch documents complete"
---

# US-0019 — Explainable Entity Connection Paths and Streaming Batch Ingestion

## Governing PRD
- [`PRD-0007: Portable Graph Visualization Exports, Explainable Citation Paths, and Streaming Ingestion`](../../product/accepted/prd-0007-portable-graph-visualization-exports-explainable-c.md)

## Governing ADR
- [`ADR-0149: Unified Redstring Client Facade and In-Memory Indexing Architecture`](../../adrs/accepted/adr-0149-unified-redstring-client-facade-and-indexing.md)

## User Story

**As an** Elena (The Temporal & Investigative Intelligence Analyst) and Alex (The Knowledge Graph Architect),
**I want** `explain_path` to find the shortest multi-hop relationship chain between two entities along with supporting `StoredChunk` passages, and `build_graph_stream` to yield `GraphBuildReport` items incrementally as batch documents complete,
**So that** I can audit the exact textual evidence connecting any two entities and stream real-time ingestion progress to interactive interfaces.

## Acceptance Criteria

```gherkin
Scenario: Discover shortest connection path between two entities with source chunk citations
  Given a tenant graph where "Ada Lovelace" connects to "Charles Babbage" who connects to "Analytical Engine"
  When I call client.explain_path from "Ada Lovelace" to "Analytical Engine" with max_depth 3
  Then a PathExplanation is returned containing the ordered entities and connecting relationships
  And supporting StoredChunk passages from the chunk store are attached to the explanation.
```

```gherkin
Scenario: Return None when no path exists within max_depth hops
  Given a tenant graph with two disconnected entities or entities farther apart than max_depth
  When I call client.explain_path between them with max_depth 1
  Then None is returned without scanning other tenants.
```

```gherkin
Scenario: Stream incremental GraphBuildReports as batch documents complete
  Given a Redstring client and a sequence of 4 documents
  When I iterate over client.build_graph_stream with max_concurrency 2
  Then 4 GraphBuildReport instances are yielded incrementally as each document finishes
  And all 4 documents are persisted in the tenant's graph store.
```
