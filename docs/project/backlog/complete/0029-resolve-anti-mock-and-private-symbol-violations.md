---
id: 0029
title: Resolve anti-mock and private symbol backdoor violations in tests
status: Complete
governing_adrs:
- ADR-0001
- ADR-0003
- ADR-0006
governing_prds:
- PRD-0001
governing_stories:
- US-0006
target_bc: testing
signed_off_by: Ty Evans <ty@tyevans.net>
signed_off_at: '2026-10-04T20:44:33.956039+00:00'
mutation_scope: '[]'
---

# TASK-0029: Resolve anti-mock and private symbol backdoor violations in tests

## Summary
`uv run spec-ops test verify-frontdoors` identifies 14 anti-mock and private symbol backdoor violations in the test suites (governed by ADR-0003). Refactor these tests to exercise features exclusively through public domain models, adapter protocols, and public frontdoors without importing private internals or using `unittest.mock.patch`.

## Context & Objectives
1. Governed by ADR-0003 (Blackbox frontdoor verification): tests must exercise public interfaces rather than reaching into private internals or using backdoor monkeypatches.
2. The 14 reported violations are:
   - `tests/integration/llm/test_live_embeddings.py:43`: private import of `_cosine`.
   - `tests/unit/chunks/test_postgres_schema.py:32, 300`: private import of `_SCORE`.
   - `tests/unit/domain/test_merge_strategy.py:17`: private import of `_order_key`.
   - `tests/unit/domain/test_temporal_parsing.py:182`: private import of `_MONTH`, `_MONTH_NUMBERS`.
   - `tests/unit/extraction/test_schema_org.py:34`: private import of `_map_og_type`.
   - `tests/unit/temporal/test_inference.py:16`: private import of `_CANONICAL`.
   - `tests/unit/llm/test_retry.py`: 4 invocations of `patch.object(...)` and import from `unittest.mock`.
   - `tests/unit/test_optional_dependency_guards.py:27`: import of `unittest.mock.patch`.
3. Replace private symbol assertions with public contract assertions, and replace `unittest.mock.patch` with clean test doubles or dependency injection via ports.
4. Verify `uv run spec-ops test verify-frontdoors` passes with 0 violations.

## Executable Acceptance Criteria (ADR-0006)

```gherkin
Scenario: Auditing frontdoors and anti-mock compliance across test suites
  Given the refactored test suites in "tests/"
  When "spec-ops test verify-frontdoors" executes
  Then zero private symbol imports are detected
  And zero "unittest.mock" backdoor invocations are found
  And all unit and integration tests pass cleanly through public interfaces.
```
