---
id: REFACTOR-tests-unit-consolidation-test_planning
title: Refactor and Decompose Legacy File test_planning.py
status: Proposed
created: 2026-09-29
governing_adrs:
  - ADR-0002
target_bc: core
---

# TASK-REFACTOR-tests-unit-consolidation-test_planning: Refactor Legacy File test_planning.py

## Summary
The grandfathered debt file `tests/unit/consolidation/test_planning.py` contains 663 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (test_planning_the.py, test_planning_moving.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `tests/unit/consolidation/test_planning/` with submodules:
- `test_planning_the.py`: TestTheShapeOfThePlan, TestTheMergeEventAcceptsThePlan, _plan, TestDropping, TestDeduplication, TestDuplicatePreference, TestNoOpRedirectionsAreNotEmitted, TestArgumentHandling, test_planning_is_pure, _group, TestPlanProperties
- `test_planning_moving.py`: TestMoving

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/tests/unit/consolidation/test_planning.py (663 lines):
  Submodule 'test_planning_the.py' (~548 lines):
    - [class] TestTheShapeOfThePlan (lines 227-286)
    - [class] TestTheMergeEventAcceptsThePlan (lines 385-416)
    - [function] _plan (lines 28-33)
    - [class] TestDropping (lines 92-130)
    - [class] TestDeduplication (lines 133-224)
    - [class] TestDuplicatePreference (lines 289-362)
    - [class] TestNoOpRedirectionsAreNotEmitted (lines 365-382)
    - [class] TestArgumentHandling (lines 419-435)
    - [function] test_planning_is_pure (lines 438-450)
    - [function] _group (lines 461-483)
    - [class] TestPlanProperties (lines 486-659)
  Submodule 'test_planning_moving.py' (~54 lines):
    - [class] TestMoving (lines 36-89)
  Suggested barrel exports:
    from .test_planning_the import TestTheShapeOfThePlan, TestTheMergeEventAcceptsThePlan, _plan, TestDropping, TestDeduplication, TestDuplicatePreference, TestNoOpRedirectionsAreNotEmitted, TestArgumentHandling, test_planning_is_pure, _group, TestPlanProperties
    from .test_planning_moving import TestMoving

    __all__ = ["TestTheShapeOfThePlan", "TestTheMergeEventAcceptsThePlan", "_plan", "TestDropping", "TestDeduplication", "TestDuplicatePreference", "TestNoOpRedirectionsAreNotEmitted", "TestArgumentHandling", "test_planning_is_pure", "_group", "TestPlanProperties", "TestMoving"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
