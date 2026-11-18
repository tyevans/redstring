---
id: '0010'
title: "Dual-Channel Chunk Corpus and Passage Retrieval Engine"
status: Accepted
created: 2026-10-04
persona: "Jordan"
target_bc: "chunks"
feature: "FEAT-CHUNKS-03"
governing_prd: "PRD-0002"
scenarios:
  - "Index document batch into chunk corpus with entity linkages"
  - "Enforce strict tenant isolation during chunk retrieval"
---

# US-0010 — Dual-Channel Chunk Corpus and Passage Retrieval Engine

## Governing PRD
- [`PRD-0002: Dual-Channel Chunk Corpus and Passage Retrieval Engine`](../../product/accepted/prd-0002-dual-channel-chunk-corpus-and-passage-retriev.md)

## Governing ADRs
- [`ADR-0123: The Chunk Corpus`](../../adrs/accepted/adr-0123-the-chunk-corpus.md)
- [`ADR-0138: The Chunk's Vector Lives on the Chunk`](../../adrs/accepted/adr-0138-the-chunks-vector-lives-on-the-chunk.md)

## User Story

**As a** Jordan (The Data Platform Lead),
**I want** `index_documents` and `ChunkProjection` to populate a tenant-isolated dual-channel chunk corpus where each chunk carries its embedding vector and extracted entity references,
**So that** multi-document corpora can be indexed and searched with zero cross-tenant leakage.

## Acceptance Criteria

```gherkin
Scenario: Index document batch into chunk corpus with entity linkages
  Given a collection of SourceDocuments and a configured GraphStore, VectorStore, and ChunkStore
  When I call index_documents across the document collection
  Then an IndexReport is returned summarizing total documents, entities, and chunks written
  And each stored chunk links back to the EntityIds extracted from its span.
```

```gherkin
Scenario: Enforce strict tenant isolation during chunk retrieval
  Given chunks indexed for tenant A and tenant B in the same ChunkStore
  When tenant A queries ChunkRetriever.retrieve_chunks
  Then only chunks belonging to tenant A are returned and tenant B chunks are never exposed.
```
