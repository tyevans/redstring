---
id: '0009'
title: "Retrieve Scored Passages via Dual-Channel Fusion"
status: Accepted
created: 2026-10-04
persona: "Alex"
target_bc: "composition"
feature: "FEAT-CHUNKS-02"
governing_prd: "PRD-0002"
scenarios:
  - "Fuse BM25 lexical ranking and dense vector similarity"
  - "Rank passage citations with Reciprocal Rank Fusion"
---

# US-0009 — Retrieve Scored Passages via Dual-Channel Fusion

## Governing PRD
- [`PRD-0002: Dual-Channel Chunk Corpus and Passage Retrieval Engine`](../../product/accepted/prd-0002-dual-channel-chunk-corpus-and-passage-retriev.md)

## Governing ADRs
- [`ADR-0124: BM25 Over the Chunk Corpus`](../../adrs/accepted/adr-0124-bm25-over-the-chunk-corpus.md)
- [`ADR-0145: A Lexical-Only Retriever Is a Constructor`](../../adrs/accepted/adr-0145-a-lexical-only-retriever-is-a-constructor.md)

## User Story

**As an** Alex (The Knowledge Graph Architect),
**I want** `ChunkRetriever` to combine pure-domain BM25 lexical scoring with dense vector cosine similarity via Reciprocal Rank Fusion (RRF),
**So that** passage retrieval captures both exact keyword/acronym matches and semantic similarity with verified entity citations.

## Acceptance Criteria

```gherkin
Scenario: Fuse BM25 lexical ranking and dense vector similarity
  Given an InMemoryChunkStore populated with embedded document passages
  When I query ChunkRetriever.retrieve_chunks in hybrid mode
  Then both lexical BM25 candidates and semantic vector candidates are scored
  And a ChunkRetrievalResult is returned with fused ScoredChunk matches.
```

```gherkin
Scenario: Rank passage citations with Reciprocal Rank Fusion
  Given a ChunkRetriever configured with lexical_only or hybrid mode
  When I query for a specific technical term present in a stored chunk
  Then the matching passage is ranked at the top with its cited EntityIds and source_id preserved.
```
