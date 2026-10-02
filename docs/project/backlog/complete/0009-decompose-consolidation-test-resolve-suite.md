---
id: 0009
title: Decompose consolidation test resolve suite (<500 lines)
status: Complete
governing_adrs:
- ADR-0002
- ADR-0003
governing_prds:
- PRD-0001
governing_stories:
- US-0002
target_bc: consolidation
mutation_scope: '[''src/redstring/consolidation/service.py'']'
---

# TASK-0009: Decompose consolidation test resolve suite (<500 lines)

## Summary
`tests/unit/consolidation/test_resolve.py` is at 498 lines—only 2 lines away from triggering a hard failure against the <500 lines invariant (ADR-0002). This task decomposes this large test suite into focused scenario modules.

## Problem Statement & Context
1. `tests/unit/consolidation/test_resolve.py` tests alias resolution, candidate pruning, transitive closures, and circular alias graphs all in a single file.
2. At 498 lines, modifying any test case or adding a scenario immediately trips CI failure.
3. Splitting tests into `test_resolve_cycles.py`, `test_resolve_pruning.py`, and `test_resolve_aliases.py` improves parallel execution and keeps every file well below 300 lines.

## Definition of Done (Blackbox Frontdoor TDD)
1. Split into focused test modules under `tests/unit/consolidation/resolve/` or separate siblings.
2. 100% of test assertions preserved with zero backdoor mocks.
3. All split test files strictly under 300 lines.
4. Total test count preserved.
