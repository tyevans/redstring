---
id: '0002'
title: Consolidate Mentions and Resolve Entity Aliases
status: Accepted
created: 2026-10-02
persona: Alex (The Knowledge Graph Architect)
target_bc: consolidation
feature: FEAT-CONSOL-01
governing_prd: PRD-0001
scenarios:
  - Resolve multiple alias mentions to canonical entity
  - Merge duplicate properties with deterministic preference ordering
---

# US-0002 — Consolidate Mentions and Resolve Entity Aliases

## Governing PRD
- [`PRD-0001: Knowledge Graph Extraction and Event-Sourced Storage Engine`](../../product/accepted/prd-0001-knowledge-graph-extraction-and-event-sourced-.md)

## User Story

**As a** knowledge graph architect (Alex),
**I want** `ConsolidationService` to merge alias mentions and resolve relationship endpoints to canonical nodes,
**So that** queries across multiple documents connect through the same canonical entity rather than fractured alias nodes.

## Acceptance Criteria

```gherkin
Scenario: Resolve multiple alias mentions to canonical entity
  Given extracted mentions for "Ada Lovelace" and "Lovelace, A." representing the same person
  When "ConsolidationService.merge" executes a consolidation plan
  Then an "EntitiesMerged" event is appended to the event log
  And graph projections redirect all edges pointing to "Lovelace, A." to the canonical "Ada Lovelace" id.
```

```gherkin
Scenario: Merge duplicate properties with deterministic preference ordering
  Given two entity records with conflicting values for property "birth_year" ("1815" vs "1816")
  When consolidation plans and executes the merge
  Then property conflict resolution applies the total preference order rule (ADR-0010)
  And the winning value is deterministically selected and preserved.
```
