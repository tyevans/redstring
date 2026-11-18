---
id: '0015'
title: "Community Detection and Corpus Theme Summarization"
status: Accepted
created: 2026-10-16
persona: "Elena"
target_bc: "composition"
feature: "FEAT-COMP-15"
governing_prd: "PRD-0005"
scenarios:
  - "Cluster tenant graph into communities and synthesize themes with passage citations"
  - "Exclude singleton communities below min_size from LLM summarization calls"
  - "Skip failed community model calls gracefully when skip_failed is enabled"
---

# US-0015 — Community Detection and Corpus Theme Summarization

## Governing PRD
- [`PRD-0005: Temporal Intelligence, Allen Interval Algebra, and Community Theme Synthesis`](../../product/accepted/prd-0005-temporal-intelligence-allen-interval-algebra-and-c.md)

## Governing ADR
- [`ADR-0142: Themes Are Recomputed, Never Stored`](../../adrs/accepted/adr-0142-themes-are-recomputed-never-stored.md)

## User Story

**As an** Elena (The Temporal & Investigative Intelligence Analyst),
**I want** `summarize_themes` to partition a tenant's knowledge graph into communities and generate LLM-synthesized thematic reports backed by highest-degree members and source chunks,
**So that** I can understand macro-level narratives across a corpus with model calls scaling by graph structure rather than document count.

## Acceptance Criteria

```gherkin
Scenario: Cluster tenant graph into communities and synthesize themes with passage citations
  Given a tenant graph with connected entity clusters and stored source chunks
  When I call summarize_themes with a GraphStore, ChunkStore, and LlmProvider
  Then a ThemeReport is returned containing synthesized Theme titles, summaries, and member EntityIds
  And nothing is written back to the GraphStore or ChunkStore.
```

```gherkin
Scenario: Exclude singleton communities below min_size from LLM summarization calls
  Given a tenant graph containing a connected cluster of 3 entities and 2 isolated singleton entities
  When I call summarize_themes with min_size 2
  Then only the cluster of 3 entities triggers an LLM summarization call
  And ThemeReport.too_small records the 2 skipped singleton communities.
```

```gherkin
Scenario: Skip failed community model calls gracefully when skip_failed is enabled
  Given a tenant graph with multiple communities and an LlmProvider that fails on one community
  When I call summarize_themes with skip_failed set to True
  Then surviving communities are returned in ThemeReport.themes
  And ThemeReport.failed increments to 1 without aborting the entire report.
```
