---
id: '0025'
title: Decompose extraction schema_org module to satisfy file length limit
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
- src/redstring/extraction/schema_org.py
---

# TASK-0025: Decompose extraction schema_org module to satisfy file length limit

## Summary
`src/redstring/extraction/schema_org.py` is currently 415 lines, exceeding the 400-line proactive refactoring warning threshold (ADR-0002). Decompose the module into focused single-responsibility units (e.g., separating schema type definitions from mapping and prompt generation logic) so all files remain well under 400 lines.

## Context & Objectives
1. Governed by ADR-0002: Hard invariant file length limit (<500 lines), proactive refactoring at >=400 lines.
2. `src/redstring/extraction/schema_org.py` contains vocabulary mappings, type normalization, and prompt formatting for Schema.org entity extraction.
3. Extract helper types or mapping tables into `src/redstring/extraction/schema_org_types.py` or submodules while preserving public imports and backwards compatibility.
4. Verify all unit tests pass and `uv run spec-ops health` reports 0 proactive warnings for this file.

## Executable Acceptance Criteria (ADR-0006)

```gherkin
Scenario: Auditing schema_org file length limit
  Given the refactored schema_org modules in "src/redstring/extraction/"
  When "spec-ops health" executes
  Then "src/redstring/extraction/schema_org.py" has fewer than 400 lines
  And zero proactive refactoring warnings are emitted for the module
  And all unit and extraction regression tests pass cleanly.
```
