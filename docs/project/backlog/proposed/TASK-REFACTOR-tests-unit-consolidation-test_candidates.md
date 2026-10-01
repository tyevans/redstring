---
id: REFACTOR-tests-unit-consolidation-test_candidates
title: Refactor and Decompose Legacy File test_candidates.py
status: Proposed
created: 2026-09-29
governing_adrs:
  - ADR-0002
target_bc: core
---

# TASK-REFACTOR-tests-unit-consolidation-test_candidates: Refactor Legacy File test_candidates.py

## Summary
The grandfathered debt file `tests/unit/consolidation/test_candidates.py` contains 578 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (test_candidates_keyed.py, test_candidates_store.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `tests/unit/consolidation/test_candidates/` with submodules:
- `test_candidates_keyed.py`: keyed, TestBlocking, TestResolutionIsByValue, TestScoring, TestOrderingAndFiltering, TestTheFinderNeverWrites, TestWhatMutationTestingFound
- `test_candidates_store.py`: _store_with

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/tests/unit/consolidation/test_candidates.py (578 lines):
  Submodule 'test_candidates_keyed.py' (~533 lines):
    - [function] keyed (lines 28-31)
    - [class] TestBlocking (lines 40-111)
    - [class] TestResolutionIsByValue (lines 114-159)
    - [class] TestScoring (lines 162-424)
    - [class] TestOrderingAndFiltering (lines 427-475)
    - [class] TestTheFinderNeverWrites (lines 478-499)
    - [class] TestWhatMutationTestingFound (lines 502-578)
  Submodule 'test_candidates_store.py' (~4 lines):
    - [function] _store_with (lines 34-37)
  Suggested barrel exports:
    from .test_candidates_keyed import keyed, TestBlocking, TestResolutionIsByValue, TestScoring, TestOrderingAndFiltering, TestTheFinderNeverWrites, TestWhatMutationTestingFound
    from .test_candidates_store import _store_with

    __all__ = ["keyed", "TestBlocking", "TestResolutionIsByValue", "TestScoring", "TestOrderingAndFiltering", "TestTheFinderNeverWrites", "TestWhatMutationTestingFound", "_store_with"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
