# Release Notes: Knowledge Graph Extraction and Event-Sourced Storage Engine (PRD-0001)

**Status:** Accepted
**Date:** 2026-10-04
**Target Persona:** Alex

## Overview

- **Cross-Document Connection Blindness**: Full-text and naive chunk-based RAG cannot answer questions spanning multiple documents (e.g. tracking people, contracts, or incidents across disjoint files). - **Mention Splintering**: Direct LLM extraction generates separate nodes for every lexical alias ("Ada Lovelace", "Lovelace, A.", "Ada King"), fracturing the graph and producing incorrect relationship counts. - **Destructive Re-extraction**: Storing extractions directly into graph stores makes prompt and model upgrades destructive; changing an extraction prompt requires wiping and migrating databases rather than deterministically replaying a log of events.

## Verifiable Customer UAT Checkmarks

- [x] Calling `extract_document` on a `SourceDocument` emits a valid `DocumentExtracted` event containing typed entities, temporal intervals, and relationships.
- [x] Running `ConsolidationService.resolve` maps alias mentions to canonical entity IDs with deterministic preference ordering.
- [x] Replaying an event stream via `GraphProjection` and `VectorProjection` faithfully reconstructs the full graph and embedding index.
- [x] Querying `Retriever.retrieve` with a query string returns ranked chunks combining vector scores and graph neighborhood context.

## Target Persona Benefits

### Alex — The Knowledge Graph Architect — Software engineer and GraphRAG application architect.

- Engineers and data practitioners who need to construct connected knowledge graphs from document collections and query them for cross-document synthesis.
- Clean LLM-driven entity and relationship extraction emitting immutable domain events.
- Automatic alias consolidation and deterministic property merge resolution.
- Multi-channel hybrid retrieval combining vector similarity, lexical search, and graph traversal.

### Morgan — The Autonomous Coding Agent — LLM-powered coding worker contributing features, bug fixes, and refactors to the redstring repository.

- Coding agents requiring structured, verifiable domain contracts and deterministic graph operations to build GraphRAG applications.
- Explicit task specifications citing governing ADRs, PRDs, and executable BDD user stories.
- Strict worktree isolation preventing merge conflicts across concurrent workers.
- Blackbox frontdoor test suites verifying contracts without private internal mocks.

### Riley — The Open-Source Library Maintainer — Core maintainer responsible for releases, code quality, and architectural integrity.

- Library maintainers ensuring deterministic event replay, high test coverage, and modular bounded-context layering.
- Strict preflight verification gates asserting 100% test passes and zero file length violations.
- Automated documentation drift audits ensuring all CLI and code snippets remain verified.
- Bidirectional traceability connecting user personas, stories, tasks, and commits.

### Jordan — The Data Platform Lead — Data platform engineer managing document pipelines and persistent store infrastructure.

- Event-sourced write model where read projections can be safely cleared and replayed from sequence zero.
- Pluggable storage adapters conforming to strict port contracts with zero leaky abstractions.
- Multi-tenant isolation enforced at the port boundary with zero cross-tenant reads or writes.

## Shipped Capabilities

- **Extraction via Structured LLM Prompts**: `extract_document` extracts typed entities, directional relationships, and temporal intervals from an unformatted `SourceDocument`. Domain schemas (e.g. Schema.org vocabulary) guide and prompt extraction without rigidly constraining open discovery.
- **Consolidation and Alias Resolution**: `ConsolidationService` identifies candidate duplicate mentions, executes deterministic tie-breaking and merge planning, and emits `EntitiesMerged` events. All relationship endpoints resolve through the canonical alias table so graph queries reach the single canonical entity.
- **Event-Sourced Decoupling (Extraction Writes to No Store)**: Extraction writes to no store directly; it emits immutable domain events (`DocumentExtracted`). Read projections fold events into pluggable storage ports: `GraphStore` (Neo4j / in-memory), `VectorStore` (pgvector / in-memory), and `ChunkStore` (Postgres / in-memory). Re-extracting or upgrading prompts is an event replay rather than an irreversible database migration.
- **Hybrid Multi-Channel Retrieval**: `Retriever` orchestrates vector semantic similarity, lexical matching, and graph neighborhood traversals, returning enriched chunks with entity citations.

## Completed User Journeys

- **US-0001**: Extract Entities and Relationships from Documents (Alex (The Knowledge Graph Architect))
- **US-0002**: Consolidate Mentions and Resolve Entity Aliases (Alex (The Knowledge Graph Architect))
- **US-0003**: Project Extraction Events to Graph Vector and Chunk Stores (Jordan (The Data Platform Lead))
- **US-0004**: Hybrid Multi-Channel Retrieval Over Knowledge Graph (Alex (The Knowledge Graph Architect))
- **US-0005**: Verify Public Surface Boundaries and Release Integrity (Riley)
- **US-0006**: Autonomous Task Execution and Invariant Verification (Morgan)
- **US-0007**: Measure Ingestion Throughput and Track Chunk Progress (Alex (The Knowledge Graph Architect))
