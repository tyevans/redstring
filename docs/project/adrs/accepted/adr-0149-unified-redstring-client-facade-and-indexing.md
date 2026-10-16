---
id: '0149'
title: Unified Redstring Client Facade and In-Memory Indexing Architecture
status: Accepted
target_bc: composition
governing_prds:
- PRD-0003
governing_stories:
- US-0011
- US-0012
amends:
- ADR-0106
- ADR-0107
---

# ADR-0149: Unified Redstring Client Facade and In-Memory Indexing Architecture

## Status

Accepted.

Amends [ADR 0106](adr-0106-the-public-surface-is-gated.md) and [ADR 0107](adr-0107-composition-is-the-only-top-layer.md).
The low-level compositions (`build_graph`, `index_documents`, `Retriever`, `ChunkRetriever`) remain available and unchanged. This record introduces a high-level, batteries-included client facade `Redstring` in `composition/` and formalizes incremental indexing optimizations within in-memory store reference adapters.

## Context

While `redstring` maintains clean layered architecture and strict port/adapter boundaries, developer ergonomics suffered from high assembly friction:
1. Callers must manually construct multiple storage adapters, LLM/embedding providers, and manually pass up to 18 keyword arguments to `build_graph`.
2. Retrieval and consolidation require instantiating independent `Retriever`, `ChunkRetriever`, and `Consolidator` objects with redundant parameters.
3. Every operation requires explicit `SourceDocument` instantiation, prohibiting plain text or dictionary payloads.
4. Batch ingestion of multiple documents required callers to author bespoke concurrency loops without built-in rate-limiting, error isolation, or aggregated reporting.
5. In-memory graph traversal (`neighbors`) and candidate lookup (`find_by_blocking_keys`) performed $O(|E|)$ and $O(|V|)$ table scans across the entire tenant on every query, hindering interactive graph exploration and performance testing.

## Decision

1. **Unified Batteries-Included Client (`Redstring`)**:
   - Resides in `redstring.composition.client` and exported in `redstring.__all__`.
   - Factory constructors:
     - `Redstring.in_memory(provider=..., embeddings=..., tenant_id=...)` creates pre-wired in-memory stores.
     - `Redstring.connect(graph_store=..., vector_store=..., chunk_store=..., ...)` binds persistent stores.
   - Provides unified methods:
     - `await client.build_graph(doc_or_text, ...)` with automatic string-to-SourceDocument coercion.
     - `await client.build_graph_batch(docs_or_texts, max_concurrency=4, continue_on_error=True, ...)` returning `BatchGraphReport`.
     - `await client.retrieve_entities(query, k=5, ...)` and `await client.retrieve_passages(query, k=5, ...)`.
     - `await client.neighbors(entity_id_or_name, depth=1)`.
     - `await client.consolidate(...)`.
     - `client.for_tenant(tenant_id)` context scoping.

2. **Incremental In-Memory Indexing**:
   - `InMemoryGraphStore` maintains internal adjacency mapping (`_adjacency: dict[TenantId, dict[EntityId, set[tuple[EntityId, str]]]]`) updated incrementally on relationship mutations. `neighbors()` traverses directly via index in $O(\text{degree})$ time without edge-table scans.
   - `InMemoryGraphStore` maintains an inverted blocking key map (`_blocking_index: dict[TenantId, dict[str, set[EntityId]]]`) updated on entity upsert/delete, yielding $O(|keys|)$ lookups for `find_by_blocking_keys()`.
   - `InMemoryVectorStore.search` calculates query vector norm once per search pass rather than redundantly per stored vector candidate.

## Consequences

- **Ergonomics**: Reduces initial onboarding and day-to-day usage to ~3 lines of Python code while retaining full access to low-level pipelines.
- **Performance**: In-memory traversal and candidate blocking operate with orders-of-magnitude lower latency during local graph construction and testing.
- **Architectural Integrity**: `Redstring` lives purely in `composition/` and respects all port boundaries without circular dependencies.
