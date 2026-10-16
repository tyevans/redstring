---
id: '0003'
title: Ergonomics, Unified Knowledge Graph Client, and High-Performance Indexing
status: Accepted
created: 2026-10-15
target_persona: Alex
component: composition
---

# PRD-0003 — Ergonomics, Unified Knowledge Graph Client, and High-Performance Indexing

## Who this is for

- **Alex (The Knowledge Graph Architect)**: Needs a unified, batteries-included client interface that coordinates graph, vector, and chunk storage, entity extraction, hybrid retrieval, and consolidation without manually managing dozens of keyword arguments and disparate class instantiations.
- **Jordan (The Data Platform Lead)**: Needs high-throughput concurrent batch ingestion (`build_graph_batch`) with bounded concurrency, progress callbacks, and graceful error handling over large document collections.
- **Morgan (The Autonomous Coding Agent)**: Needs fast in-memory graph traversal and vector search during local evaluation, testing, and graph exploration without table-scanning performance bottlenecks.

## What the person cannot do today

- **High Friction Client Setup**: To extract facts or retrieve answers today, callers must separately instantiate multiple store adapters, remember 18 keyword arguments for `build_graph`, configure and wire `Retriever` and `ChunkRetriever` manually, and pass repetitive tenant IDs on every call.
- **String and Document Ceremony**: Callers cannot pass plain text strings or document dictionaries to ingestion functions; they must manually wrap everything in `SourceDocument(id=..., text=...)`.
- **Lack of First-Class Batch Ingestion**: Callers ingesting multiple documents must write manual concurrency loops, rate-limit logic, and error handlers, with no built-in aggregated progress or batch metrics.
- **In-Memory Traversal Bottlenecks**: `InMemoryGraphStore.neighbors()` scans the entire edge table of the tenant on every single query, and `find_by_blocking_keys()` scans every stored entity, causing $O(|E|)$ and $O(|V|)$ degradation as graph size increases.

## What good looks like

1. **Unified Batteries-Included Client (`Redstring`)**:
   - Provides `Redstring.in_memory()` and `Redstring.connect()` factory constructors.
   - Fluent, cohesive API surface: `build_graph()`, `build_graph_batch()`, `retrieve_entities()`, `retrieve_passages()`, `neighbors()`, and `consolidate()`.
   - Supports transparent tenant scoping (`redstring.for_tenant(tenant_id)` or default tenant configuration).
   - Flexible input coercion: Accepts plain `str`, dict with `text`/`id`, or `SourceDocument`.

2. **High-Throughput Concurrent Batch Ingestion**:
   - `build_graph_batch` accepts collections of documents or texts.
   - Bounded concurrency via `max_concurrency` semaphore.
   - Fault tolerance option (`continue_on_error=True`) collecting individual errors while completing remaining documents.
   - Returns a structured `BatchGraphReport` aggregating documents processed, entities created, relationships established, chunks written, and errors encountered.

3. **High-Performance In-Memory Indexing & Traversal**:
   - `InMemoryGraphStore` maintains incremental adjacency and endpoint indexes, executing `neighbors()` in $O(\text{degree})$ time without full table scans.
   - `InMemoryGraphStore` maintains inverted blocking key indices for sub-millisecond candidate lookup.
   - `InMemoryVectorStore.search` calculates query vector norm once per search pass rather than recomputing norm for every candidate.

## What this does not do

- **External Network Fetching**: Redstring remains strictly a processing and knowledge graph library; document scraping, web crawling, or PDF rendering remain external caller responsibilities.
- **Breaking Existing Low-Level Functions**: Low-level functions (`build_graph`, `index_documents`, `Retriever`, `ChunkRetriever`) remain available and fully backward-compatible for advanced users requiring granular pipeline customization.

## Checkable Outcomes

1. `Redstring.in_memory(provider=..., embeddings=...)` instantiates a functional, fully wired client without manual store initialization.
2. Ingesting a plain string via `client.build_graph("Ada Lovelace worked with Charles Babbage.")` succeeds, automatically assigning a deterministic content-derived ID.
3. Ingesting multiple documents via `client.build_graph_batch(documents, max_concurrency=4)` processes documents concurrently and returns an aggregated `BatchGraphReport`.
4. Calling `client.retrieve_entities(query)` and `client.retrieve_passages(query)` executes hybrid multi-channel search through a single unified client method.
5. In-memory `neighbors()` on a graph with 1,000 edges returns identical results to the reference implementation without re-scanning unrelated edges or rebuilding full adjacency sets per call.
6. In-memory `find_by_blocking_keys()` retrieves matching candidate entities via inverted index in $O(|keys|)$ time without scanning non-matching entities.
7. All new and modified modules strictly adhere to the <500 lines limit (ADR-0002) and pass 100% of existing compliance and integration test suites.

## Linked User Stories

- `US-0011`
- `US-0012`

## Implementing Backlog Tasks

- `TASK-0049`
- `TASK-0050`
- `TASK-0051`
- `TASK-0052`
