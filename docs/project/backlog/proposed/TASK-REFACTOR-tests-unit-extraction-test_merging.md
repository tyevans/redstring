---
id: REFACTOR-tests-unit-extraction-test_merging
title: Refactor and Decompose Legacy File test_merging.py
status: Proposed
created: 2026-09-29
governing_adrs:
  - ADR-0002
target_bc: core
---

# TASK-REFACTOR-tests-unit-extraction-test_merging: Refactor Legacy File test_merging.py

## Summary
The grandfathered debt file `tests/unit/extraction/test_merging.py` contains 626 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (test_merging_the.py, test_merging_of.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `tests/unit/extraction/test_merging/` with submodules:
- `test_merging_the.py`: TestTheOverlapChunkingCreates, test_the_fold_does_not_depend_on_the_order_of_its_parts, test_the_tie_break_is_reached_at_all_in_the_realistic_case, chunk, entity, link, names, TestChoosingBetweenTwoReports, TestCounters, edges_among, part_from, by_id, TestProperties, _colliding_parts, TestMentionCounts
- `test_merging_of.py`: mentions_of

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/tests/unit/extraction/test_merging.py (626 lines):
  Submodule 'test_merging_the.py' (~493 lines):
    - [class] TestTheOverlapChunkingCreates (lines 77-185)
    - [function] test_the_fold_does_not_depend_on_the_order_of_its_parts (lines 515-536)
    - [function] test_the_tie_break_is_reached_at_all_in_the_realistic_case (lines 539-550)
    - [function] chunk (lines 52-60)
    - [function] entity (lines 63-64)
    - [function] link (lines 67-70)
    - [function] names (lines 73-74)
    - [class] TestChoosingBetweenTwoReports (lines 188-244)
    - [class] TestCounters (lines 247-267)
    - [function] edges_among (lines 307-315)
    - [function] part_from (lines 318-319)
    - [function] by_id (lines 337-338)
    - [class] TestProperties (lines 341-483)
    - [function] _colliding_parts (lines 486-510)
    - [class] TestMentionCounts (lines 553-626)
  Submodule 'test_merging_of.py' (~2 lines):
    - [function] mentions_of (lines 303-304)
  Suggested barrel exports:
    from .test_merging_the import TestTheOverlapChunkingCreates, test_the_fold_does_not_depend_on_the_order_of_its_parts, test_the_tie_break_is_reached_at_all_in_the_realistic_case, chunk, entity, link, names, TestChoosingBetweenTwoReports, TestCounters, edges_among, part_from, by_id, TestProperties, _colliding_parts, TestMentionCounts
    from .test_merging_of import mentions_of

    __all__ = ["TestTheOverlapChunkingCreates", "test_the_fold_does_not_depend_on_the_order_of_its_parts", "test_the_tie_break_is_reached_at_all_in_the_realistic_case", "chunk", "entity", "link", "names", "TestChoosingBetweenTwoReports", "TestCounters", "edges_among", "part_from", "by_id", "TestProperties", "_colliding_parts", "TestMentionCounts", "mentions_of"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
