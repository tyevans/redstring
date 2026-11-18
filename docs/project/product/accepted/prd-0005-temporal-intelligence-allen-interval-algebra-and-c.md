---
id: '0005'
title: Temporal Intelligence, Allen Interval Algebra, and Community Theme Synthesis
status: Accepted
created: 2026-10-16
target_persona: Elena
component: temporal
---

# PRD-0005 — Temporal Intelligence, Allen Interval Algebra, and Community Theme Synthesis

## Who this is for

- **Elena (The Temporal & Investigative Intelligence Analyst)**: Needs chronological timeline reconstruction, interval overlap queries that respect date precision and uncertainty markers, Allen interval algebra inference, and corpus-wide community theme summarization.
- **Alex (The Knowledge Graph Architect)**: Needs derived temporal relations (`InferredRelation`) and community themes (`ThemeReport`) computed purely on read so the event log and graph store never accumulate stale derived state.

## What the person cannot do today

- **Imprecise Date & Uncertainty Blindness**: Standard SQL/Cypher `WHERE start_date >= X AND end_date <= Y` queries miss year-precision dates (`2023-01-01` with `end_date=None` representing all of 2023) and fail on open-ended uncertainty markers (`UncertaintyMarker.BEFORE` and `AFTER`).
- **Stale Stored Temporal Edges**: Persisting `PRECEDES` or `OVERLAPS` edges during extraction only captures within-document pairs and silently goes stale when any document is re-extracted with updated dates.
- **Corpus-Scale Summarization Cost**: Summarizing a large knowledge graph by prompting over raw documents scales linearly with corpus length rather than graph community structure.

## What good looks like

1. **Precision-Aware Timeline & Interval Queries (`TemporalQuery`)**:
   - `TemporalQuery(store).timeline(tenant_id, interval=..., entity_type=...)` returns dated entities in deterministic chronological order (`order_key`), handling open lower/upper bounds and tie-breaking by `EntityId`.
   - `TemporalQuery(store).entities_in_interval(tenant_id, interval, relations=...)` evaluates exact bounds widened by `DatePrecision` and `UncertaintyMarker`.

2. **On-Read Allen Interval Algebra (`infer_relations`)**:
   - `infer_relations` and `TemporalQuery.relations_in_interval` compute canonical `InferredRelation` tuples (`BEFORE`, `CONTAINS`, `OVERLAPS`, `EQUALS`) across entities without persisting derived edges or emitting duplicate inverse edges (`AFTER` -> `BEFORE`, `DURING` -> `CONTAINS`).
   - Enforces a pre-check `max_pairs` ceiling before comparing pairs to prevent accidental quadratic blowups.

3. **Community Detection & Theme Summarization (`summarize_themes`)**:
   - `summarize_themes` partitions a tenant's graph via Louvain/modularity community detection (`detect_communities`), ranks members by node degree, attaches supporting chunks from `ChunkReader`, and synthesizes structured `Theme` titles and summaries into a `ThemeReport` (governed by ADR-0142).

## What this does not do

- **Persist Derived Temporal Edges or Themes**: Neither `InferredRelation` nor `ThemeReport` writes back to `GraphStore` or the event log.
- **Cross-Tenant Timeline or Community Reads**: All scans and community partitions are strictly scoped to a single `TenantId`.

## Checkable Outcomes

1. Querying `TemporalQuery.timeline` orders entities chronologically across mixed precisions (`YEAR`, `MONTH`, `DAY`) and open uncertainty bounds (`BEFORE`, `AFTER`).
2. Calling `infer_relations` on dated entities emits canonical `InferredRelation` records (`BEFORE`, `CONTAINS`, `OVERLAPS`, `EQUALS`) with zero inverse duplicates and raises `ValueError` immediately if pairs exceed `max_pairs`.
3. Calling `summarize_themes` partitions connected entity clusters of at least `min_size` and returns a `ThemeReport` with synthesized `Theme` objects, community counts, and passage citations.

## Linked User Stories

- [`US-0014`](../../user_stories/accepted/us-0014-temporal-timeline-queries-and-allen-interval-infere.md): Temporal Timeline Queries and Allen Interval Inference
- [`US-0015`](../../user_stories/accepted/us-0015-community-detection-and-corpus-theme-summarization.md): Community Detection and Corpus Theme Summarization

## Implementing Backlog Tasks

- [`TASK-0008`](../../backlog/complete/0008-decompose-themes-module-to-satisfy-file-length-inv.md): Decompose themes module to satisfy file length invariant
