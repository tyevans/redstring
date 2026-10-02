---
id: '0005'
title: "Verify Public Surface Boundaries and Release Integrity"
status: Accepted
created: 2026-10-02
persona: "Riley"
target_bc: "core"
feature: "FEAT-GATE-01"
governing_prd: "PRD-0001"
scenarios:
  - "Verifying public API surface is self-contained and isolated"
  - "Verifying dual-declaration version synchronization"
---

# US-0005 — Verify Public Surface Boundaries and Release Integrity

## Governing PRD
- [`PRD-0001: Knowledge Graph Extraction and Event-Sourced Storage Engine`](../../product/accepted/prd-0001-knowledge-graph-extraction-and-event-sourced-.md)

## User Story

**As an** open-source library maintainer (Riley),
**I want** automated preflight gates that assert the public API surface is strictly self-contained and version declarations agree,
**So that** published releases never leak private internal modules or ship with desynchronized version identifiers.

## Acceptance Criteria

```gherkin
Scenario: Verifying public API surface is self-contained and isolated
  Given the public export definitions in "src/redstring/__init__.py"
  When the automated public surface test suite executes
  Then every symbol exported in "__all__" is self-contained and resolves to an approved public type (ADR-0006)
  And zero internal implementation modules leak across the public facade.
```

```gherkin
Scenario: Verifying dual-declaration version synchronization
  Given version strings in "pyproject.toml" and "src/redstring/__init__.py"
  When preflight release verification runs
  Then both files declare identical version strings
  And any discrepancy fails the preflight gate with an actionable error.
```
