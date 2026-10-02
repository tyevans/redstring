---
id: '0001'
title: Extract Entities and Relationships from Documents
status: Accepted
created: 2026-10-02
persona: Alex (The Knowledge Graph Architect)
target_bc: extraction
feature: FEAT-EXTRACT-01
governing_prd: PRD-0001
scenarios:
  - Extract entities and relations from single document
  - Handle document with temporal intervals
---

# US-0001 — Extract Entities and Relationships from Documents

## Governing PRD
- [`PRD-0001: Knowledge Graph Extraction and Event-Sourced Storage Engine`](../../product/accepted/prd-0001-knowledge-graph-extraction-and-event-sourced-.md)

## User Story

**As a** knowledge graph architect (Alex),
**I want** to pass a `SourceDocument` to `extract_document` with an LLM provider,
**So that** structured entities, relationships, and temporal intervals are extracted and emitted as domain events without writing directly to any database.

## Acceptance Criteria

```gherkin
Scenario: Extract entities and relations from single document
  Given a "SourceDocument" with text "Ada Lovelace collaborated with Charles Babbage on the Analytical Engine."
  When the caller invokes "extract_document" through the public extraction pipeline
  Then a "DocumentExtracted" event is emitted
  And the event payload contains entities for "Ada Lovelace" and "Charles Babbage"
  And the event payload contains a directional relationship "collaborated_with" between them.
```

```gherkin
Scenario: Handle document with temporal intervals
  Given a "SourceDocument" containing temporal statements "Ada worked on the engine from 1842 to 1843."
  When "extract_document" processes the document
  Then the resulting relationship contains a valid-time interval
  And the interval start parses to "1842-01-01" and end parses to "1843-12-31".
```
