---
id: '0001'
title: Knowledge Graph Extraction and Event-Sourced Storage Engine
status: Accepted
created: 2026-10-02
target_persona: Alex
component: core
---

# PRD-0001 — Knowledge Graph Extraction and Event-Sourced Storage Engine

## Who this is for

- **Alex (The Knowledge Graph Architect)**: Engineers and data practitioners who need to construct connected knowledge graphs from document collections and query them for cross-document synthesis.
- **Morgan (The Autonomous Agent)**: Coding agents requiring structured, verifiable domain contracts and deterministic graph operations to build GraphRAG applications.
- **Riley (The Library Maintainer)**: Library maintainers ensuring deterministic event replay, high test coverage, and modular bounded-context layering.

## What the person cannot do today

- **Cross-Document Connection Blindness**: Full-text and naive chunk-based RAG cannot answer questions spanning multiple documents (e.g. tracking people, contracts, or incidents across disjoint files).
- **Mention Splintering**: Direct LLM extraction generates separate nodes for every lexical alias ("Ada Lovelace", "Lovelace, A.", "Ada King"), fracturing the graph and producing incorrect relationship counts.
- **Destructive Re-extraction**: Storing extractions directly into graph stores makes prompt and model upgrades destructive; changing an extraction prompt requires wiping and migrating databases rather than deterministically replaying a log of events.

## What good looks like

1. **Extraction via Structured LLM Prompts**:
   - `extract_document` extracts typed entities, directional relationships, and temporal intervals from an unformatted `SourceDocument`.
   - Domain schemas (e.g. Schema.org vocabulary) guide and prompt extraction without rigidly constraining open discovery.

2. **Consolidation and Alias Resolution**:
   - `ConsolidationService` identifies candidate duplicate mentions, executes deterministic tie-breaking and merge planning, and emits `EntitiesMerged` events.
   - All relationship endpoints resolve through the canonical alias table so graph queries reach the single canonical entity.

3. **Event-Sourced Decoupling (Extraction Writes to No Store)**:
   - Extraction writes to no store directly; it emits immutable domain events (`DocumentExtracted`).
   - Read projections fold events into pluggable storage ports: `GraphStore` (Neo4j / in-memory), `VectorStore` (pgvector / in-memory), and `ChunkStore` (Postgres / in-memory).
   - Re-extracting or upgrading prompts is an event replay rather than an irreversible database migration.

4. **Hybrid Multi-Channel Retrieval**:
   - `Retriever` orchestrates vector semantic similarity, lexical matching, and graph neighborhood traversals, returning enriched chunks with entity citations.

## What this does not do

- **Document Ingestion & Crawling**: Redstring never fetches content; web crawling, HTML cleaning, OCR, and PDF parsing are external concerns.
- **Direct Store Writes from Extraction**: Extraction pipelines never write directly to Neo4j, pgvector, or PostgreSQL; all mutations flow through the event log.
- **Cross-Tenant Operations**: Strict tenant isolation across all ports; cross-tenant reads or cross-tenant deletes are strictly forbidden.

## Checkable Outcomes

1. Calling `extract_document` on a `SourceDocument` emits a valid `DocumentExtracted` event containing typed entities, temporal intervals, and relationships.
2. Running `ConsolidationService.resolve` maps alias mentions to canonical entity IDs with deterministic preference ordering.
3. Replaying an event stream via `GraphProjection` and `VectorProjection` faithfully reconstructs the full graph and embedding index.
4. Querying `Retriever.retrieve` with a query string returns ranked chunks combining vector scores and graph neighborhood context.

## Linked User Stories

- [`US-0001`](../../user_stories/accepted/us-0001-extract-entities-and-relationships-from-documents.md): Extract Entities and Relationships from Documents
- [`US-0002`](../../user_stories/accepted/us-0002-consolidate-mentions-and-resolve-entity-aliases.md): Consolidate Mentions and Resolve Entity Aliases
- [`US-0003`](../../user_stories/accepted/us-0003-project-extraction-events-to-graph-vector-and-chun.md): Project Extraction Events to Graph, Vector, and Chunk Stores
- [`US-0004`](../../user_stories/accepted/us-0004-hybrid-multi-channel-retrieval-over-knowledge-grap.md): Hybrid Multi-Channel Retrieval Over Knowledge Graph
- [`US-0005`](../../user_stories/accepted/us-0005-verify-public-surface-boundaries-and-release-integ.md): Verify Public Surface Boundaries and Release Integrity
- [`US-0006`](../../user_stories/accepted/us-0006-autonomous-task-execution-and-invariant-verificati.md): Autonomous Task Execution and Invariant Verification
- [`US-0007`](../../user_stories/accepted/us-0007-measure-ingestion-throughput-and-track-chunk-progress.md): Measure Ingestion Throughput and Track Chunk Progress

## Implementing Backlog Tasks

- [`TASK-0001`](../../backlog/refined/0001-initial-architecture-spike-and-setup.md): Initial Architecture Spike and System Foundation
- [`TASK-0002`](../../backlog/refined/0002-re-read-and-re-plan-merge-on-expectedversion-confl.md): Re-read and re-plan merge on ExpectedVersion conflict (B43)
- [`TASK-0003`](../../backlog/refined/0003-retract-stale-entities-on-document-re-extraction.md): Retract stale entities on document re-extraction (B32)
- [`TASK-0004`](../../backlog/refined/0004-support-tenant-scoped-projection-rebuild.md): Support tenant-scoped projection rebuild (B35)
