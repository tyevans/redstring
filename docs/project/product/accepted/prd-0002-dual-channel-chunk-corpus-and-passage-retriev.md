---
id: '0002'
title: Dual-Channel Chunk Corpus and Passage Retrieval Engine
status: Accepted
created: 2026-10-04
target_persona: Jordan
component: chunks
---

# PRD-0002 — Dual-Channel Chunk Corpus and Passage Retrieval Engine

## Who this is for

- **Jordan (The Data Platform Lead)**: Needs an explicit chunk corpus persisted alongside graph and vector projections so that downstream passage retrieval and citation generation have verified source text provenance.
- **Alex (The Knowledge Graph Architect)**: Needs hybrid search fusing BM25 lexical ranking and dense vector similarity over stored chunks, returning ranked passages with reciprocal rank fusion (RRF).
- **Morgan (The Autonomous Agent)**: Needs deterministic chunk IDs derived from content hashing (`SourceId:Index:Hash`) and uniform compliance suites across in-memory and PostgreSQL chunk stores.

## What the person cannot do today

- **Passage Citation Blindness**: Without retaining extracted chunks, knowledge graph entities only cite whole document IDs (`source_id`), preventing fine-grained passage citation and attribution.
- **Lexical Recall Gaps**: Pure vector similarity misses exact keyword, acronym, and entity identifier matches that BM25 scoring over caller-supplied chunks easily captures.
- **Positional Brittleness**: Fragile chunk index offsets fail when documents are re-extracted or chunk boundary windows are adjusted.

## What good looks like

1. **Content-Addressed Chunk Corpus**:
   - Chunks are stored with deterministic IDs derived from source and content hash (governed by ADR-0144).
   - Write operations (`write_chunks`) report exact insertion counts and update stored passages atomically per source (governed by ADR-0146).

2. **Dual-Channel Retrieval Fusion**:
   - `retrieve_chunks` queries both lexical (BM25 scored in pure domain logic, ADR-0124) and semantic (dense embedding vector cosine similarity) channels.
   - Channel ranks fuse via Reciprocal Rank Fusion (RRF) with configurable weights and limits.

3. **Pluggable ChunkStore Port**:
   - Clean port interface decomposed into capability protocols (`ChunkStore`, `ChunkReader`, `ChunkWriter`, `ChunkSearcher`, governed by ADR-0126).
   - Shipped with production PostgreSQL adapter (`redstring.chunks.adapters.postgres`) and fast in-memory adapter (`InMemoryChunkStore`) verified against shared compliance suites.

## What this does not do

- **Web Fetching & Preprocessing**: Redstring never fetches or parses documents; text chunking operates on caller-provided strings and `SourceDocument` boundaries.
- **Cross-Tenant Corpus Search**: Strict tenant isolation across all chunk reads and searches; tenant IDs are mandatory query parameters.
- **In-Database BM25 Stored Procedures**: Lexical scoring algorithms reside in pure domain code (`redstring.domain.lexical`) rather than database-specific extensions, guaranteeing identical ranking across storage engines.

## Checkable Outcomes

1. Calling `ChunkStore.write_chunks` writes `StoredChunk` instances and returns a `ChunkWriteResult` reflecting inserted counts.
2. Querying `retrieve_chunks` with hybrid mode returns `ScoredChunk` instances ranked by Reciprocal Rank Fusion combining lexical BM25 and vector similarity.
3. In-memory and PostgreSQL chunk store implementations pass 100% of tests in the shared `ChunkStoreComplianceSuite`.
4. Deriving chunk IDs via `derive_chunk_id` produces deterministic hash IDs that remain identical across independent extraction passes.

## Linked User Stories

- [`US-0008`](../../user_stories/accepted/us-0008-store-content-addressed-document-chunks.md): Store Content-Addressed Document Chunks
- [`US-0009`](../../user_stories/accepted/us-0009-retrieve-scored-passages-via-dual-channel-fusion.md): Retrieve Scored Passages via Dual-Channel Fusion
