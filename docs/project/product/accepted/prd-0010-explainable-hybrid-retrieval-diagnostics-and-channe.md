---
id: '0010'
title: Explainable Hybrid Retrieval Diagnostics and Channel Attribution
status: Accepted
created: 2026-10-16
target_persona: Tessa
component: composition
release: v0.14.0
---

# PRD-0010 — Explainable Hybrid Retrieval Diagnostics and Channel Attribution

## Who this is for

- **Tessa (The Search Relevance & Retrieval Diagnostics Engineer)**: Needs per-channel 0-based ranks, exact Reciprocal Rank Fusion (RRF) contributions, matched lexical blocking keys, dangling vector diagnostics, and per-term BM25 score breakdowns to debug and evaluate hybrid entity and passage retrieval.

## What the person cannot do today

- **Opaque RRF Rank Fusion**: `Retriever.retrieve` and `ChunkRetriever.retrieve_chunks` return fused scores alongside raw channel floats, but hide each match's 0-based semantic rank, 0-based lexical rank, exact additive RRF contribution (`1.0 / (60 + rank + 1)`), and dominant channel (`"semantic"`, `"lexical"`, or `"tie"`).
- **Hidden Recall Gates & BM25 Term Weights**: Search engineers cannot inspect which `query_blocking_keys(query)` recalled an entity, which dangling vector IDs were dropped because the entity was absent from `EntityReader`, or the per-term BM25 contributions (`term_bm25_contributions`) and `CorpusStats` for ranked passages.

## What good looks like

1. **Explainable Entity Retrieval (`explain_entity_retrieval`, `Redstring.explain_entity_retrieval`)**:
   - Returns `EntityRetrievalExplanation` containing `query`, `mode`, `overfetch`, `query_blocking_keys`, `semantic_candidates_considered`, `lexical_candidates_considered`, `dangling_vector_ids`, `elapsed_ms`, and ordered `ExplainedEntityMatch` records.
   - Each `ExplainedEntityMatch` exposes `entity`, `fused_rank`, `fused_score`, `semantic_rank`, `semantic_score`, `semantic_rrf_contribution`, `lexical_rank`, `lexical_score`, `lexical_rrf_contribution`, `matched_blocking_keys`, and `dominant_channel`.

2. **Explainable Passage Retrieval (`explain_chunk_retrieval`, `Redstring.explain_passage_retrieval`)**:
   - Returns `ChunkRetrievalExplanation` containing `query`, `mode`, `overfetch`, `query_terms`, `corpus_stats`, `semantic_candidates_considered`, `lexical_candidates_considered`, `elapsed_ms`, and ordered `ExplainedChunkMatch` records.
   - Each `ExplainedChunkMatch` exposes `chunk`, `fused_rank`, `fused_score`, `semantic_rank`, `semantic_score`, `semantic_rrf_contribution`, `lexical_rank`, `lexical_score`, `lexical_rrf_contribution`, `doc_length`, `matched_terms`, `term_bm25_contributions`, and `dominant_channel`.

## What this does not do

- **Modify Hot-Path Retrieval Contracts**: `Retriever.retrieve` and `ChunkRetriever.retrieve_chunks` remain unchanged.

## Checkable Outcomes

1. For any query, tenant, `k`, and `mode`, `explain_entity_retrieval` returns the exact same entity ordering and fused scores as `Retriever.retrieve`, and every `ExplainedEntityMatch` satisfies `semantic_rrf_contribution + lexical_rrf_contribution == pytest.approx(fused_score)`.
2. When `VectorReader.search` returns an `EntityId` absent from `EntityReader`, `explain_entity_retrieval` records it in `dangling_vector_ids` without raising an error.
3. For any passage query, `explain_chunk_retrieval` returns the exact same chunk ordering and fused scores as `ChunkRetriever.retrieve_chunks`, and every lexically ranked `ExplainedChunkMatch` satisfies `sum(term_bm25_contributions.values()) == pytest.approx(lexical_score)`.

## Linked User Stories

- [`US-0024`](../../user_stories/accepted/us-0024-explainable-entity-retrieval-diagnostics-and-rrf-ch.md): Explainable Entity Retrieval Diagnostics and RRF Channel Attribution
- [`US-0025`](../../user_stories/accepted/us-0025-explainable-passage-retrieval-diagnostics-and-per-t.md): Explainable Passage Retrieval Diagnostics and Per-Term BM25 Attribution
