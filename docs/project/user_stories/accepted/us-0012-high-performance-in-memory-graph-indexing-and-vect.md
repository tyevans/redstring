---
id: '0012'
title: "High-Performance In-Memory Graph Indexing and Vector Traversal"
status: Accepted
created: 2026-10-15
persona: "Morgan"
target_bc: "graph"
feature: "FEAT-GRAP-12"
governing_prd: "PRD-0003"
scenarios:
  - "Traverse entity neighbors via incremental adjacency index"
  - "Lookup candidate entities via inverted blocking index"
  - "Vector search with precomputed query norm"
---

# US-0012 — High-Performance In-Memory Graph Indexing and Vector Traversal

## Governing PRD
- [`PRD-0003: Ergonomics, Unified Knowledge Graph Client, and High-Performance Indexing`](../../product/accepted/prd-0003-ergonomics--unified-knowledge-graph-client--a.md)

## Governing ADR
- [`ADR-0149: Unified Redstring Client Facade and In-Memory Indexing Architecture`](../../adrs/accepted/adr-0149-unified-redstring-client-facade-and-indexing.md)

## User Story

**As a** Morgan (The Autonomous Coding Agent & Performance Engineer),
**I want** incremental adjacency indexing in `InMemoryGraphStore` and single-pass norm calculation in `InMemoryVectorStore`,
**So that** graph traversal, neighbor queries, blocking lookups, and vector search execute with maximum speed during local testing, benchmarking, and real-time graph exploration.

## Acceptance Criteria

```gherkin
Scenario: Traverse entity neighbors via incremental adjacency index
  Given an InMemoryGraphStore with 1000 stored relationships
  When I query neighbors for an entity with depth 1
  Then neighbor entities are returned in order
  And the lookup traverses the incremental adjacency index without rebuilding graph adjacency from scratch.
```

```gherkin
Scenario: Lookup candidate entities via inverted blocking index
  Given an InMemoryGraphStore containing entities with blocking keys
  When I call find_by_blocking_keys with a list of search keys
  Then candidate entities are looked up via the inverted blocking index
  And keys with zero matches return empty lists.
```

```gherkin
Scenario: Vector search with precomputed query norm
  Given an InMemoryVectorStore with 100 stored embedding vectors
  When I call search with a query vector and k 5
  Then top k VectorMatch records are returned ranked by cosine score
  And the query norm is computed once per search call.
```
