---
id: '0008'
title: "Store Content-Addressed Document Chunks"
status: Accepted
created: 2026-10-04
persona: "Jordan"
target_bc: "chunks"
feature: "FEAT-CHUNKS-01"
governing_prd: "PRD-0002"
scenarios:
  - "Persist stored chunks with deterministic hash IDs"
  - "Atomic replacement of chunks on document re-extraction"
---

# US-0008 — Store Content-Addressed Document Chunks

## Governing PRD
- [`PRD-0002: Dual-Channel Chunk Corpus and Passage Retrieval Engine`](../../product/accepted/prd-0002-dual-channel-chunk-corpus-and-passage-retriev.md)

## Governing ADRs
- [`ADR-0144: A Chunk ID Is Derived, Not Supplied`](../../adrs/accepted/adr-0144-a-chunk-id-is-derived-not-supplied.md)
- [`ADR-0146: A Chunk Write Reports What It Added`](../../adrs/accepted/adr-0146-a-chunk-write-reports-what-it-added.md)

## User Story

**As a** Jordan (The Data Platform Lead),
**I want** extracted document chunks to be persisted with deterministic content-addressed `ChunkId` hashes and replaced atomically per source upon re-extraction,
**So that** downstream passage citations never suffer positional offset drift or retain stale orphan passages when documents are updated.

## Acceptance Criteria

```gherkin
Scenario: Persist stored chunks with deterministic hash IDs
  Given a SourceDocument and an InMemoryChunkStore
  When build_graph or index_documents writes the document chunks
  Then StoredChunk records are persisted with deterministic content-addressed ChunkIds
  And GraphBuildReport.chunks_written reports the exact count of stored chunks.
```

```gherkin
Scenario: Atomic replacement of chunks on document re-extraction
  Given a ChunkStore containing stored chunks for source "doc-1"
  When source "doc-1" is re-indexed with updated passage text
  Then the previous chunks for "doc-1" are replaced atomically by the new chunks
  And chunks belonging to other sources or tenants remain untouched.
```
