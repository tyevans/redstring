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

## User Story

**As an** Alex,
**I want** an autonomous capability to execute "Retrieve Scored Passages via Dual-Channel Fusion",
**So that** business outcomes are delivered reliably across the composition bounded context.

## Acceptance Criteria

```gherkin
Scenario: Fuse BM25 lexical ranking and dense vector similarity
  Given the system is initialized and ready
  When the user executes the workflow for "Fuse BM25 lexical ranking and dense vector similarity"
  Then observable outputs satisfy public contracts without backdoor tampering.
```

```gherkin
Scenario: Rank passage citations with Reciprocal Rank Fusion
  Given the system is initialized and ready
  When the user executes the workflow for "Rank passage citations with Reciprocal Rank Fusion"
  Then observable outputs satisfy public contracts without backdoor tampering.
```
