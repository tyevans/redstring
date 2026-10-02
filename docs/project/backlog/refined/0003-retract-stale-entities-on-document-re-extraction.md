---
id: '0003'
title: Retract stale entities on document re-extraction
status: Blocked
dependencies:
- SPIKE-0020
governing_adrs:
- ADR-0001
governing_prds:
- PRD-0001
governing_stories:
- US-0001
target_bc: extraction
mutation_scope: '[''src/redstring/aggregates/document.py'']'
blocker:
  type: spike_needed
  question: How to define entity identity across extraction runs and retract stale
    entities without violating GraphStore port invariants?
  raised_at: '2026-10-02T13:45:02.874371-07:00'
  spike_id: SPIKE-0020
---

# TASK-0003: Retract stale entities on document re-extraction

## Summary
When re-extracting a document under a newer model or prompt that discovers fewer entities than the previous extraction run, previously extracted entities remain in the graph forever (BACKLOG B32). This task computes entity retractions in the `Document` aggregate to reflect the current extraction state.

## Problem Statement & Context
`DocumentExtracted` events project into stores via `upsert_entities`, causing the graph to converge on the union of all historical extractions rather than the latest state. The `Document` aggregate replays `DocumentExtracted` events and can compute diff retractions without coupling the extraction write path to store read models.

## Mutation Testing Scope (ADR-0009)
Target module: `src/redstring/aggregates/document.py`. Ensure >=80% mutant kill score under `mutmut`.

## Definition of Done (Blackbox Frontdoor TDD)
1. Re-extraction with fewer entities retracts dropped entities in the projected graph.
2. Verified via blackbox tests without private mock backdoors.
3. Target module achieves >=80% mutation kill score under `mutmut`.
4. All source files strictly under 500 lines limit.
