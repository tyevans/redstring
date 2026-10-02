---
id: REFACTOR-tests-unit-consolidation-test_merge_undo_round_trip
title: Refactor and Decompose Legacy File test_merge_undo_round_trip.py
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

# TASK-REFACTOR-tests-unit-consolidation-test_merge_undo_round_trip: Refactor Legacy File test_merge_undo_round_trip.py

## Summary
The grandfathered debt file `tests/unit/consolidation/test_merge_undo_round_trip.py` contains 623 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (test_merge_undo_round_trip_the.py, test_merge_undo_round_trip_rig.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `tests/unit/consolidation/test_merge_undo_round_trip/` with submodules:
- `test_merge_undo_round_trip_the.py`: TestTheRoundTrip, TestTheRoundTripCanFail, TestTheInvariantsInAnger, TestTheRoundTripAsAProperty, _diamond, TestChainsAndTenants
- `test_merge_undo_round_trip_rig.py`: Rig

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/tests/unit/consolidation/test_merge_undo_round_trip.py (623 lines):
  Submodule 'test_merge_undo_round_trip_the.py' (~517 lines):
    - [class] TestTheRoundTrip (lines 129-321)
    - [class] TestTheRoundTripCanFail (lines 324-385)
    - [class] TestTheInvariantsInAnger (lines 388-529)
    - [class] TestTheRoundTripAsAProperty (lines 574-623)
    - [function] _diamond (lines 97-126)
    - [class] TestChainsAndTenants (lines 532-571)
  Submodule 'test_merge_undo_round_trip_rig.py' (~39 lines):
    - [class] Rig (lines 56-94)
  Suggested barrel exports:
    from .test_merge_undo_round_trip_the import TestTheRoundTrip, TestTheRoundTripCanFail, TestTheInvariantsInAnger, TestTheRoundTripAsAProperty, _diamond, TestChainsAndTenants
    from .test_merge_undo_round_trip_rig import Rig

    __all__ = ["TestTheRoundTrip", "TestTheRoundTripCanFail", "TestTheInvariantsInAnger", "TestTheRoundTripAsAProperty", "_diamond", "TestChainsAndTenants", "Rig"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
