---
id: '0025'
title: "Explainable Passage Retrieval Diagnostics and Per-Term BM25 Attribution"
status: Accepted
created: 2026-10-16
persona: "Tessa"
target_bc: "composition"
feature: "FEAT-COMP-25"
governing_prd: "PRD-0010"
release: "v0.14.0"
scenarios:
  - "Hybrid chunk retrieval explanation exposes per-term BM25 contributions and RRF math"
  - "Unembedded corpus in hybrid mode explains lexical matches while semantic channel reports zero candidates"
---

# US-0025 — Explainable Passage Retrieval Diagnostics and Per-Term BM25 Attribution

## Governing PRD
- [`PRD-0010: Explainable Hybrid Retrieval Diagnostics and Channel Attribution`](../../product/accepted/prd-0010-explainable-hybrid-retrieval-diagnostics-and-channe.md)

## Governing ADRs
- [`ADR-0142: Hybrid Retrieval Fuses By Rank`](../../adrs/accepted/adr-0142-hybrid-retrieval-fuses-by-rank.md)
- [`ADR-0149: Unified Redstring Client Facade and In-Memory Indexing Architecture`](../../adrs/accepted/adr-0149-unified-redstring-client-facade-and-in-memory-inde.md)
- [`ADR-0006: Executable BDD User Stories`](../../adrs/accepted/adr-0006-executable-bdd-user-stories.md)

## User Story

**As a** Tessa (The Search Relevance & Retrieval Diagnostics Engineer),
**I want** `explain_chunk_retrieval` and `Redstring.explain_passage_retrieval` to return a `ChunkRetrievalExplanation` exposing tokenized query terms, `CorpusStats`, per-term BM25 score contributions, and RRF rank breakdowns,
**So that** I can diagnose passage ranking behavior and lexical vs. semantic channel dominance.

## Acceptance Criteria

```gherkin
Scenario: Hybrid chunk retrieval explanation exposes per-term BM25 contributions and RRF math
  Given a tenant chunk store containing embedded passages about "Ada Lovelace" and "analytical engine"
  When I call client.explain_passage_retrieval for query "Ada analytical" with k 5 and mode HYBRID
  Then a ChunkRetrievalExplanation is returned with query_terms ("ada", "analytical")
  And explanation.corpus_stats reports n_docs, avg_doc_length, and doc_frequencies for ("ada", "analytical")
  And each lexically matched ExplainedChunkMatch has term_bm25_contributions whose values sum to lexical_score
  And each match's semantic_rrf_contribution plus lexical_rrf_contribution equals its fused_score.
```

```gherkin
Scenario: Unembedded corpus in hybrid mode explains lexical matches while semantic channel reports zero candidates
  Given a tenant chunk store populated via index_documents without embeddings
  When I call explain_chunk_retrieval for query "Ada Lovelace" with mode HYBRID
  Then explanation.semantic_candidates_considered is 0
  And explanation.matches contains the lexically matched passages with semantic_rank None and dominant_channel "lexical".
```
