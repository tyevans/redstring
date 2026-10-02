---
id: REFACTOR-tests-unit-composition-test_themes
title: Refactor and Decompose Legacy File test_themes.py
status: Proposed
created: 2026-09-29
governing_adrs:
  - ADR-0002
governing_prds:
  - PRD-0001
governing_stories:
  - US-0004
target_bc: core
---

# TASK-REFACTOR-tests-unit-composition-test_themes: Refactor Legacy File test_themes.py

## Summary
The grandfathered debt file `tests/unit/composition/test_themes.py` contains 575 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (test_themes_entity.py, test_themes_eid.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `tests/unit/composition/test_themes/` with submodules:
- `test_themes_entity.py`: entity, OneChunkPerEntity, edge, RecordingProvider, barbell, TestPartitioning, TestTenantIsolation, TestPagination, TestMinimumSize, TestWhatTheModelIsShown, TestFailures, TestGuards
- `test_themes_eid.py`: eid

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/tests/unit/composition/test_themes.py (575 lines):
  Submodule 'test_themes_entity.py' (~502 lines):
    - [function] entity (lines 52-66)
    - [class] OneChunkPerEntity (lines 116-145)
    - [function] edge (lines 69-77)
    - [class] RecordingProvider (lines 80-113)
    - [function] barbell (lines 148-172)
    - [class] TestPartitioning (lines 175-233)
    - [class] TestTenantIsolation (lines 236-255)
    - [class] TestPagination (lines 258-321)
    - [class] TestMinimumSize (lines 324-365)
    - [class] TestWhatTheModelIsShown (lines 368-455)
    - [class] TestFailures (lines 458-511)
    - [class] TestGuards (lines 514-575)
  Submodule 'test_themes_eid.py' (~3 lines):
    - [function] eid (lines 47-49)
  Suggested barrel exports:
    from .test_themes_entity import entity, OneChunkPerEntity, edge, RecordingProvider, barbell, TestPartitioning, TestTenantIsolation, TestPagination, TestMinimumSize, TestWhatTheModelIsShown, TestFailures, TestGuards
    from .test_themes_eid import eid

    __all__ = ["entity", "OneChunkPerEntity", "edge", "RecordingProvider", "barbell", "TestPartitioning", "TestTenantIsolation", "TestPagination", "TestMinimumSize", "TestWhatTheModelIsShown", "TestFailures", "TestGuards", "eid"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
