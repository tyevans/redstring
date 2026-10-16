---
id: '0011'
title: "Unified Knowledge Graph Client and Concurrent Batch Ingestion"
status: Accepted
created: 2026-10-15
persona: "Alex"
target_bc: "composition"
feature: "FEAT-COMP-11"
governing_prd: "PRD-0003"
scenarios:
  - "Instantiate in-memory client facade"
  - "Ingest plain text string into graph"
  - "Ingest document collection concurrently with bounded semaphore"
  - "Retrieve entities and passages through unified facade"
---

# US-0011 — Unified Knowledge Graph Client and Concurrent Batch Ingestion

## Governing PRD
- [`PRD-0003: Ergonomics, Unified Knowledge Graph Client, and High-Performance Indexing`](../../product/accepted/prd-0003-ergonomics--unified-knowledge-graph-client--a.md)

## Governing ADR
- [`ADR-0149: Unified Redstring Client Facade and In-Memory Indexing Architecture`](../../adrs/accepted/adr-0149-unified-redstring-client-facade-and-indexing.md)

## User Story

**As an** Alex (The Knowledge Graph Architect),
**I want** a unified client facade `Redstring` and concurrent batch ingestion `build_graph_batch`,
**So that** I can extract knowledge graphs, ingest document batches, and query hybrid retrieval channels with minimal setup and maximum throughput.

## Acceptance Criteria

```gherkin
Scenario: Instantiate in-memory client facade
  Given an LLM provider and an embedding provider
  When I instantiate a Redstring client using in_memory factory
  Then the client provides coordinated graph, vector, and chunk stores
  And the client exposes unified build_graph, retrieve_entities, and retrieve_passages methods.
```

```gherkin
Scenario: Ingest plain text string into graph
  Given a Redstring client configured with an LLM provider
  When I call build_graph with a plain text string
  Then the text is automatically coerced to a SourceDocument with a content-addressed ID
  And entities and relationships are extracted and stored in the graph.
```

```gherkin
Scenario: Ingest document collection concurrently with bounded semaphore
  Given a Redstring client and a collection of 5 documents
  When I call build_graph_batch with max_concurrency 2
  Then all 5 documents are extracted concurrently without exceeding concurrency limits
  And a BatchGraphReport is returned containing aggregated counts of documents, entities, and chunks.
```

```gherkin
Scenario: Retrieve entities and passages through unified facade
  Given a Redstring client with ingested documents
  When I query retrieve_entities for a known entity name
  And I query retrieve_passages for a known text keyword
  Then ranked entities and ranked chunks are returned with fusion scores.
```
