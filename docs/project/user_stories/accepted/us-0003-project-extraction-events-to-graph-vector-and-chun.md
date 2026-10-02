---
id: '0003'
title: Project Extraction Events to Graph Vector and Chunk Stores
status: Accepted
created: 2026-10-02
persona: Jordan (The Data Platform Lead)
target_bc: projections
feature: FEAT-PROJ-01
governing_prd: PRD-0001
scenarios:
  - Project events to graph store and vector store
  - Rebuilding projections deterministically from the event log
---

# US-0003 — Project Extraction Events to Graph, Vector, and Chunk Stores

## Governing PRD
- [`PRD-0001: Knowledge Graph Extraction and Event-Sourced Storage Engine`](../../product/accepted/prd-0001-knowledge-graph-extraction-and-event-sourced-.md)

## User Story

**As a** pipeline operator and systems architect (Alex),
**I want** read projections to fold immutable domain events into graph, vector, and chunk stores,
**So that** stores remain derived read models that can be rebuilt or regenerated via event replay at any time.

## Acceptance Criteria

```gherkin
Scenario: Project events to graph store and vector store
  Given a newly appended "DocumentExtracted" event in the event log
  When "GraphProjection" and "VectorProjection" process the event
  Then entities and relationships appear in the configured "GraphStore"
  And chunk text embeddings appear in the configured "VectorStore"
  And the projection checkpoint advances to the event's sequence number.
```

```gherkin
Scenario: Rebuilding projections deterministically from the event log
  Given an existing tenant with historical events and a newly provisioned store
  When the caller triggers projection replay from sequence zero
  Then all entities, relationships, and embeddings are re-projected identically
  And the rebuilt store matches the state of a live-folded store.
```
