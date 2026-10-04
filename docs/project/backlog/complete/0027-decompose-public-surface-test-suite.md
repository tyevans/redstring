---
id: '0027'
title: Decompose public surface test suite to satisfy file length limit
status: Complete
governing_adrs:
- ADR-0001
- ADR-0002
- ADR-0006
governing_prds:
- PRD-0001
governing_stories:
- US-0005
target_bc: core
signed_off_by: Ty Evans <ty@tyevans.net>
signed_off_at: '2026-10-04T20:16:28.757620+00:00'
mutation_scope: '[]'
---

# TASK-0027: Decompose public surface test suite to satisfy file length limit

## Summary
`tests/unit/test_public_surface_is_self_contained.py` is currently 468 lines, approaching the 500-line hard invariant limit and triggering a proactive refactoring warning at >=400 lines (ADR-0002). Decompose the suite into focused test modules separating `__all__` verification, error hierarchy tests, and parameter introspection.

## Context & Objectives
1. Governed by ADR-0002 (<500 lines per file) and ADR-0006 (Gated public API surface).
2. The current test file covers:
   - Module `__all__` completeness
   - Public type export self-containment
   - Exception type derivation and isolation
   -Dotted-path confinement
3. Decompose into focused test files (e.g. `tests/unit/test_public_surface_exports.py` and `tests/unit/test_public_surface_types.py`).
4. Ensure all public surface assertions continue to execute and pass cleanly.

## Executable Acceptance Criteria (ADR-0006)

```gherkin
Scenario: Auditing public surface test file length
  Given the decomposed public surface test modules in "tests/unit/"
  When "spec-ops health" executes
  Then each decomposed test file contains fewer than 400 lines
  And zero proactive refactoring warnings are emitted
  And all public surface verification assertions pass cleanly.
```
