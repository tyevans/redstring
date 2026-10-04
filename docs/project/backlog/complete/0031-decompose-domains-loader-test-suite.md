---
id: '0031'
title: Decompose domains loader test suite
status: Complete
governing_adrs:
- ADR-0001
- ADR-0002
- ADR-0003
governing_prds:
- PRD-0001
governing_stories:
- US-0001
target_bc: testing
signed_off_by: Ty Evans <ty@tyevans.net>
signed_off_at: '2026-10-04T21:12:57.898568+00:00'
mutation_scope: '[]'
---

# TASK-0031: Decompose domains loader test suite

## Summary
`tests/unit/extraction/domains/test_loader.py` is at 402 lines, exceeding the 400-line proactive refactoring warning threshold (governed by ADR-0002). Decompose the suite by extracting directory scanning and bulk schema loading tests into `tests/unit/extraction/domains/test_loader_directory.py`.

## Context & Objectives
1. Governed by ADR-0002 (File Length Limit <500 lines, proactive warning at >=400 lines).
2. `tests/unit/extraction/domains/test_loader.py` currently tests string loading, file loading, directory loading, and validation.
3. Extract `TestLoadAllSchemas` and `TestValidateSchemaFile` into `tests/unit/extraction/domains/test_loader_directory.py`.
4. Ensure both modules remain well below 400 lines.
5. Verify 0 proactive refactoring warnings across the entire repository.

## Executable Acceptance Criteria (ADR-0006)

```gherkin
Scenario: Verifying file length health after loader test suite decomposition
  Given the decomposed test suites in "tests/unit/extraction/domains/"
  When "spec-ops health" executes
  Then zero source files exceed 500 lines
  And zero proactive refactoring warnings are reported.
```
