---
id: '0024'
title: "Explainable Entity Retrieval Diagnostics and RRF Channel Attribution"
status: Accepted
created: 2026-10-16
persona: "Tessa"
target_bc: "composition"
feature: "FEAT-COMP-24"
governing_prd: "PRD-0010"
release: "v0.14.0"
scenarios:
  - "Hybrid entity retrieval explanation decomposes semantic and lexical RRF contributions"
  - "Dangling vector matches are recorded in diagnostics without corrupting results"
  - "Lexical-only entity retrieval explanation operates without an embedding provider"
---

# US-0024 — Explainable Entity Retrieval Diagnostics and RRF Channel Attribution

## Governing PRD
- [`PRD-0010: Explainable Hybrid Retrieval Diagnostics and Channel Attribution`](../../product/accepted/prd-0010-explainable-hybrid-retrieval-diagnostics-and-channe.md)

## Governing ADRs
- [`ADR-0142: Hybrid Retrieval Fuses By Rank`](../../adrs/accepted/adr-0142-hybrid-retrieval-fuses-by-rank.md)
- [`ADR-0149: Unified Redstring Client Facade and In-Memory Indexing Architecture`](../../adrs/accepted/adr-0149-unified-redstring-client-facade-and-in-memory-inde.md)
- [`ADR-0006: Executable BDD User Stories`](../../adrs/accepted/adr-0006-executable-bdd-user-stories.md)

## User Story

**As a** Tessa (The Search Relevance & Retrieval Diagnostics Engineer),
**I want** `explain_entity_retrieval` and `Redstring.explain_entity_retrieval` to return an `EntityRetrievalExplanation` detailing per-channel 0-based ranks, raw scores, exact RRF contributions, matched blocking keys, and skipped dangling vector IDs,
**So that** I can inspect why each entity ranked where it did across semantic and lexical retrieval channels.

## Acceptance Criteria

```gherkin
Scenario: Hybrid entity retrieval explanation decomposes semantic and lexical RRF contributions
  Given a tenant with entities "Ada Lovelace" and "Analytical Engine" indexed in both the graph and vector stores
  When I call client.explain_entity_retrieval for query "Ada Lovelace" with k 5 and mode HYBRID
  Then an EntityRetrievalExplanation is returned with mode HYBRID and non-empty query_blocking_keys
  And the top ExplainedEntityMatch for "Ada Lovelace" has fused_rank 0
  And its semantic_rrf_contribution plus lexical_rrf_contribution equals its fused_score
  And its matched_blocking_keys contains the prefix and soundex keys shared with the query.
```

```gherkin
Scenario: Dangling vector matches are recorded in diagnostics without corrupting results
  Given a tenant where the vector store holds an embedding for an EntityId absent from the graph store
  And the vector store and graph store both hold "Charles Babbage"
  When I call explain_entity_retrieval for query "Charles Babbage" with mode HYBRID
  Then the missing EntityId is listed in explanation.dangling_vector_ids
  And explanation.matches contains "Charles Babbage" without raising KeyError.
```

```gherkin
Scenario: Lexical-only entity retrieval explanation operates without an embedding provider
  Given a tenant graph containing "Ada Lovelace" and no embedding provider or vector store
  When I call explain_entity_retrieval for query "Ada Lovelace" with mode LEXICAL
  Then explanation.matches contains "Ada Lovelace" with semantic_rank None and semantic_rrf_contribution 0.0
  And its dominant_channel is "lexical" and lexical_rrf_contribution equals its fused_score.
```
