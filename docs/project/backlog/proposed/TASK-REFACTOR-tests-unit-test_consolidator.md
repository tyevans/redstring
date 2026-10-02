---
id: REFACTOR-tests-unit-test_consolidator
title: Refactor and Decompose Legacy File test_consolidator.py
status: Proposed
created: 2026-09-29
governing_adrs:
  - ADR-0002
governing_prds:
  - PRD-0001
governing_stories:
  - US-0002
target_bc: core
---

# TASK-REFACTOR-tests-unit-test_consolidator: Refactor Legacy File test_consolidator.py

## Summary
The grandfathered debt file `tests/unit/test_consolidator.py` contains 575 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (test_consolidator_the.py, test_consolidator_store.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `tests/unit/test_consolidator/` with submodules:
- `test_consolidator_the.py`: TestMergeReachesTheStore, TestUndoReachesTheStore, TestTheLogIsWhereUndoLooks, TestTheStoresTheCallerSupplies, entity, edge, tenant_id, TestResolve, TestResolveMany, TestFeatureWeights
- `test_consolidator_store.py`: store

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/tests/unit/test_consolidator.py (575 lines):
  Submodule 'test_consolidator_the.py' (~508 lines):
    - [class] TestMergeReachesTheStore (lines 82-142)
    - [class] TestUndoReachesTheStore (lines 145-168)
    - [class] TestTheLogIsWhereUndoLooks (lines 171-219)
    - [class] TestTheStoresTheCallerSupplies (lines 304-476)
    - [function] entity (lines 44-58)
    - [function] edge (lines 61-69)
    - [function] tenant_id (lines 73-74)
    - [class] TestResolve (lines 222-262)
    - [class] TestResolveMany (lines 265-301)
    - [class] TestFeatureWeights (lines 479-575)
  Submodule 'test_consolidator_store.py' (~2 lines):
    - [function] store (lines 78-79)
  Suggested barrel exports:
    from .test_consolidator_the import TestMergeReachesTheStore, TestUndoReachesTheStore, TestTheLogIsWhereUndoLooks, TestTheStoresTheCallerSupplies, entity, edge, tenant_id, TestResolve, TestResolveMany, TestFeatureWeights
    from .test_consolidator_store import store

    __all__ = ["TestMergeReachesTheStore", "TestUndoReachesTheStore", "TestTheLogIsWhereUndoLooks", "TestTheStoresTheCallerSupplies", "entity", "edge", "tenant_id", "TestResolve", "TestResolveMany", "TestFeatureWeights", "store"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
