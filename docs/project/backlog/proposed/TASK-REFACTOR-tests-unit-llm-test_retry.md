---
id: REFACTOR-tests-unit-llm-test_retry
title: Refactor and Decompose Legacy File test_retry.py
status: Proposed
created: 2026-09-29
governing_adrs:
  - ADR-0002
governing_prds:
  - PRD-0001
governing_stories:
  - US-0001
target_bc: core
---

# TASK-REFACTOR-tests-unit-llm-test_retry: Refactor Legacy File test_retry.py

## Summary
The grandfathered debt file `tests/unit/llm/test_retry.py` contains 523 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (test_retry_policy.py, test_retry_delay.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `tests/unit/llm/test_retry/` with submodules:
- `test_retry_policy.py`: TestExtractionRetryPolicy, TestRetryExhausted, TestWithRetryDecorator, TestRetryTiming
- `test_retry_delay.py`: TestGetDelay

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/tests/unit/llm/test_retry.py (523 lines):
  Submodule 'test_retry_policy.py' (~373 lines):
    - [class] TestExtractionRetryPolicy (lines 25-95)
    - [class] TestRetryExhausted (lines 218-240)
    - [class] TestWithRetryDecorator (lines 243-450)
    - [class] TestRetryTiming (lines 453-523)
  Submodule 'test_retry_delay.py' (~118 lines):
    - [class] TestGetDelay (lines 98-215)
  Suggested barrel exports:
    from .test_retry_policy import TestExtractionRetryPolicy, TestRetryExhausted, TestWithRetryDecorator, TestRetryTiming
    from .test_retry_delay import TestGetDelay

    __all__ = ["TestExtractionRetryPolicy", "TestRetryExhausted", "TestWithRetryDecorator", "TestRetryTiming", "TestGetDelay"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
