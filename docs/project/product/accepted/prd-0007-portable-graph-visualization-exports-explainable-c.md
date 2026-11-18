---
id: '0007'
title: Portable Graph Visualization Exports, Explainable Citation Paths, and Streaming Ingestion
status: Accepted
created: 2026-10-16
target_persona: Elena
component: composition
---

# PRD-0007 — Portable Graph Visualization Exports, Explainable Citation Paths, and Streaming Ingestion

## Who this is for

- **Elena (The Temporal & Investigative Intelligence Analyst)**: Needs to export tenant subgraphs into portable diagram and graph exchange formats (Mermaid flowcharts, JSON-LD, GraphML, Cypher) and trace explainable shortest-path connections between entities backed by source passage citations.
- **Alex (The Knowledge Graph Architect)**: Needs streaming async iteration over batch ingestion (`build_graph_stream`) so interactive applications and UIs can render incremental `GraphBuildReport` progress as each document finishes rather than waiting for the entire batch barrier.
- **Morgan (The Autonomous Coding Agent)**: Needs pure, zero-dependency domain serializers and deterministic BFS shortest-path discovery exposed cleanly on both the top-level package and the `Redstring` unified client facade.

## What the person cannot do today

- **Opaque Multi-Hop Connections**: While `neighbors()` returns reachable entities within $k$ hops, callers cannot ask "how is Entity A connected to Entity B, and which exact document chunks prove each hop along the path?"
- **Manual Boilerplate for Graph Visualization & Export**: Callers wanting to embed a knowledge graph in Markdown documentation (Mermaid), import it into Gephi/Cytoscape (GraphML), publish linked data (JSON-LD), or seed a Neo4j script must manually page `GraphStore` entities and edges and write custom formatters.
- **Batch Barrier Latency**: `build_graph_batch` awaits all documents before returning a single `BatchGraphReport`, preventing callers from streaming live extraction results to downstream consumers as documents complete.

## What good looks like

1. **Portable Subgraph Snapshot & Multi-Format Export (`export_graph`)**:
   - Pure domain formatters serialize any collection of `Entity` and `Relationship` records into deterministic **Mermaid** (`flowchart LR`), **JSON-LD** (`@context` / `@graph`), and **GraphML** XML without external runtime dependencies.
   - `Redstring.export_graph(format=..., tenant_id=...)` and top-level `export_graph` read a tenant's graph topology and emit the requested portable representation.

2. **Explainable Shortest-Path Discovery & Provenance Citations (`explain_path`)**:
   - `explain_path` (and `Redstring.explain_path(source, target, max_depth=...)`) finds the shortest undirected or directed relationship path connecting two entities (by `EntityId` or normalized name) within `max_depth` hops.
   - Returns a structured `PathExplanation` containing the ordered sequence of entities, connecting `Relationship` edges, and—when a `ChunkReader` is configured—the supporting `StoredChunk` passages citing each entity along the path.

3. **Streaming Batch Ingestion (`build_graph_stream` / `Redstring.build_graph_stream`)**:
   - An async generator yielding individual `GraphBuildReport` (or `BatchDocumentError` when `continue_on_error=True`) items as each document completes under bounded `max_concurrency`, enabling real-time progress streaming.

## What this does not do

- **Interactive Browser UI Hosting**: Redstring remains a pure Python library; it emits standard Mermaid, JSON-LD, and GraphML strings rather than hosting a web server.
- **Unbounded Graph Path Traversals**: `explain_path` enforces a strict `max_depth` cap (default 3) and deterministic tie-breaking by `EntityId` / `RelationshipId`.

## Checkable Outcomes

1. Calling `client.export_graph(format="mermaid")` and `client.export_graph(format="json-ld")` on a populated tenant graph returns valid, deterministic Mermaid flowchart syntax and JSON-LD `@graph` payloads containing only that tenant's entities and relationships.
2. Calling `client.explain_path("Ada Lovelace", "Analytical Engine", max_depth=3)` discovers the shortest multi-hop chain of entities and relationships between them and attaches supporting `StoredChunk` citations from the chunk store.
3. Iterating over `async for item in client.build_graph_stream(documents, max_concurrency=2)` yields `GraphBuildReport` results incrementally as each document completes while honoring the concurrency limit.
4. All new modules adhere strictly to the <500-line file limit (ADR-0002), layered port isolation, and 100% blackbox frontdoor verification (ADR-0003, ADR-0006).

## Linked User Stories

- [`US-0018`](../../user_stories/accepted/us-0018-portable-knowledge-graph-visualization-and-data-ex.md): Portable Knowledge Graph Visualization and Data Export
- [`US-0019`](../../user_stories/accepted/us-0019-explainable-entity-connection-paths-and-streaming-.md): Explainable Entity Connection Paths and Streaming Batch Ingestion

## Implementing Backlog Tasks

- `TASK-0053`
- `TASK-0054`
- `TASK-0055`
