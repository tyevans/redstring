---
id: '0026'
title: Decompose domains registry module to satisfy file length limit
status: Refined
governing_adrs:
- ADR-0001
- ADR-0002
- ADR-0111
governing_prds:
- PRD-0001
governing_stories:
- US-0001
target_bc: extraction
mutation_scope:
- src/redstring/extraction/domains/registry.py
---

# TASK-0026: Decompose domains registry module to satisfy file length limit

## Summary
`src/redstring/extraction/domains/registry.py` is currently 461 lines, significantly exceeding the 400-line warning threshold and approaching the 500-line hard invariant limit (ADR-0002). Decompose the registry into modular components (e.g. separating catalog registration from schema loading and cache management).

## Context & Objectives
1. Governed by ADR-0002: Hard invariant file length limit (<500 lines), proactive refactoring at >=400 lines.
2. `src/redstring/extraction/domains/registry.py` handles builtin domain discovery, YAML parsing delegation, and domain validation.
3. Extract loader and discovery logic into submodules while preserving the public API in `registry.py`.
4. Maintain 100% test pass rate across domain loading tests.

## Executable Acceptance Criteria (ADR-0006)

```gherkin
Scenario: Auditing domains registry file length limit
  Given the refactored domains registry in "src/redstring/extraction/domains/"
  When "spec-ops health" executes
  Then "src/redstring/extraction/domains/registry.py" has fewer than 400 lines
  And zero proactive refactoring warnings are emitted for the module
  And all domain loader unit tests pass cleanly.
```
