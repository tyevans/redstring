---
id: '0002'
title: Re-read and re-plan merge on ExpectedVersion conflict
status: Complete
governing_adrs:
- ADR-0004
governing_prds:
- PRD-0001
governing_stories:
- US-0002
target_bc: consolidation
mutation_scope: '[''src/redstring/consolidation/service.py'']'
---

# TASK-0002: Re-read and re-plan merge on ExpectedVersion conflict

## Summary
When `ConsolidationService.merge` plans against a graph read outside its aggregate concurrency window, concurrent appends can result in duplicate parallel edges (BACKLOG B43). This task implements retry-and-replan semantics when an `ExpectedVersion` conflict occurs.

## Problem Statement & Context
Deliberate staleness between the aggregate write stream and the projected graph read model allows concurrent merges to create parallel claims for the same `(source, target, relationship_type)`. Re-reading and re-planning on an `ExpectedVersion` conflict ensures the late edge is presented to `plan_redirections`, which performs deduplication.

## Mutation Testing Scope (ADR-0009)
Target module: `src/redstring/consolidation/service.py`. Ensure >=80% mutant kill score under `mutmut`.

## Definition of Done (Blackbox Frontdoor TDD)
1. Concurrency conflict triggers re-read and replan in `ConsolidationService`.
2. Verified via blackbox tests without private internal mocking.
3. Target module satisfies mutation testing kill score under `mutmut`.
4. All source files strictly under 500 lines limit.
