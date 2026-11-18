---
id: '0014'
title: "Temporal Timeline Queries and Allen Interval Inference"
status: Accepted
created: 2026-10-16
persona: "Elena"
target_bc: "temporal"
feature: "FEAT-TEMP-14"
governing_prd: "PRD-0005"
scenarios:
  - "Reconstruct chronological timeline across mixed date precisions and uncertainty bounds"
  - "Filter entities intersecting a query interval"
  - "Infer canonical Allen interval relations on read without inverse duplicates"
  - "Refuse quadratic temporal inference when pairs exceed max_pairs ceiling"
---

# US-0014 — Temporal Timeline Queries and Allen Interval Inference

## Governing PRD
- [`PRD-0005: Temporal Intelligence, Allen Interval Algebra, and Community Theme Synthesis`](../../product/accepted/prd-0005-temporal-intelligence-allen-interval-algebra-and-c.md)

## User Story

**As an** Elena (The Temporal & Investigative Intelligence Analyst),
**I want** precision-aware timeline reconstruction (`TemporalQuery`) and on-read Allen interval algebra (`infer_relations`),
**So that** I can query what a tenant's knowledge graph held over any historical window and derive temporal precedence without persisting stale edges.

## Acceptance Criteria

```gherkin
Scenario: Reconstruct chronological timeline across mixed date precisions and uncertainty bounds
  Given a tenant graph containing entities with year precision, exact day intervals, and open BEFORE bounds
  When I call TemporalQuery.timeline for the tenant
  Then dated entities are returned in deterministic chronological order with open-below bounds first
  And undated entities are excluded from the timeline.
```

```gherkin
Scenario: Filter entities intersecting a query interval
  Given a tenant graph with entities spanning 1840-1845, 1850, and 1900
  When I call TemporalQuery.entities_in_interval for the window 1842 to 1855
  Then only the entities intersecting 1842 to 1855 are returned
  And entities from another tenant are never included.
```

```gherkin
Scenario: Infer canonical Allen interval relations on read without inverse duplicates
  Given dated entities where event A precedes event B and era C contains event B
  When I call infer_relations or TemporalQuery.relations_in_interval
  Then canonical InferredRelation records BEFORE and CONTAINS are returned with source and target extents
  And inverse relations AFTER and DURING are never emitted.
```

```gherkin
Scenario: Refuse quadratic temporal inference when pairs exceed max_pairs ceiling
  Given a collection of 10 dated entities producing 45 pairs
  When I call infer_relations with max_pairs set to 10
  Then a ValueError is raised immediately before comparing pairs.
```
