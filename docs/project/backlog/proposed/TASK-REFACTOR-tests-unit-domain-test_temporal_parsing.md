---
id: REFACTOR-tests-unit-domain-test_temporal_parsing
title: Refactor and Decompose Legacy File test_temporal_parsing.py
status: Proposed
created: 2026-09-29
governing_adrs:
  - ADR-0002
target_bc: core
---

# TASK-REFACTOR-tests-unit-domain-test_temporal_parsing: Refactor Legacy File test_temporal_parsing.py

## Summary
The grandfathered debt file `tests/unit/domain/test_temporal_parsing.py` contains 682 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (test_temporal_parsing_the.py, test_temporal_parsing_ranges.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `tests/unit/domain/test_temporal_parsing/` with submodules:
- `test_temporal_parsing_the.py`: TestTheReferenceDateIsAParameter, TestPartialDatesResolveToTheRightMoment, TestEdgesOfTheStrategyChain, utc, TestPrecision, TestUncertainty, TestUnparseable, TestProperties, TestWiden, TestCenturyPortions, TestRenderDeclines
- `test_temporal_parsing_ranges.py`: TestRanges, TestRangesThatAreNotRanges

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/tests/unit/domain/test_temporal_parsing.py (682 lines):
  Submodule 'test_temporal_parsing_the.py' (~511 lines):
    - [class] TestTheReferenceDateIsAParameter (lines 42-77)
    - [class] TestPartialDatesResolveToTheRightMoment (lines 119-223)
    - [class] TestEdgesOfTheStrategyChain (lines 660-682)
    - [function] utc (lines 38-39)
    - [class] TestPrecision (lines 80-116)
    - [class] TestUncertainty (lines 226-252)
    - [class] TestUnparseable (lines 316-319)
    - [class] TestProperties (lines 350-453)
    - [class] TestWiden (lines 456-509)
    - [class] TestCenturyPortions (lines 537-594)
    - [class] TestRenderDeclines (lines 597-657)
  Submodule 'test_temporal_parsing_ranges.py' (~82 lines):
    - [class] TestRanges (lines 255-313)
    - [class] TestRangesThatAreNotRanges (lines 512-534)
  Suggested barrel exports:
    from .test_temporal_parsing_the import TestTheReferenceDateIsAParameter, TestPartialDatesResolveToTheRightMoment, TestEdgesOfTheStrategyChain, utc, TestPrecision, TestUncertainty, TestUnparseable, TestProperties, TestWiden, TestCenturyPortions, TestRenderDeclines
    from .test_temporal_parsing_ranges import TestRanges, TestRangesThatAreNotRanges

    __all__ = ["TestTheReferenceDateIsAParameter", "TestPartialDatesResolveToTheRightMoment", "TestEdgesOfTheStrategyChain", "utc", "TestPrecision", "TestUncertainty", "TestUnparseable", "TestProperties", "TestWiden", "TestCenturyPortions", "TestRenderDeclines", "TestRanges", "TestRangesThatAreNotRanges"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
