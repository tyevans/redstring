---
id: REFACTOR-redstring-domain-temporal_parsing
title: Refactor and Decompose Legacy File temporal_parsing.py
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

# TASK-REFACTOR-redstring-domain-temporal_parsing: Refactor Legacy File temporal_parsing.py

## Summary
The grandfathered debt file `src/redstring/domain/temporal_parsing.py` contains 609 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (temporal_parsing_parse.py, temporal_parsing_date.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `src/redstring/domain/temporal_parsing/` with submodules:
- `temporal_parsing_parse.py`: _parse_range, _parse_partial, _parse_period, _parse_absolute, _parse_natural, parse_temporal, _Parsed, detect_uncertainty, _strip_markers, _month_number, _time_precision, _attempt, render_temporal, widen
- `temporal_parsing_date.py`: AmbiguousReferenceDateError

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/src/redstring/domain/temporal_parsing.py (609 lines):
  Submodule 'temporal_parsing_parse.py' (~347 lines):
    - [function] _parse_range (lines 224-259)
    - [function] _parse_partial (lines 290-328)
    - [function] _parse_period (lines 335-370)
    - [function] _parse_absolute (lines 399-410)
    - [function] _parse_natural (lines 413-452)
    - [function] parse_temporal (lines 472-528)
    - [class] _Parsed (lines 122-127)
    - [function] detect_uncertainty (lines 201-206)
    - [function] _strip_markers (lines 209-212)
    - [function] _month_number (lines 271-281)
    - [function] _time_precision (lines 377-396)
    - [function] _attempt (lines 455-466)
    - [function] render_temporal (lines 540-582)
    - [function] widen (lines 585-609)
  Submodule 'temporal_parsing_date.py' (~15 lines):
    - [class] AmbiguousReferenceDateError (lines 105-119)
  Suggested barrel exports:
    from .temporal_parsing_parse import _parse_range, _parse_partial, _parse_period, _parse_absolute, _parse_natural, parse_temporal, _Parsed, detect_uncertainty, _strip_markers, _month_number, _time_precision, _attempt, render_temporal, widen
    from .temporal_parsing_date import AmbiguousReferenceDateError

    __all__ = ["_parse_range", "_parse_partial", "_parse_period", "_parse_absolute", "_parse_natural", "parse_temporal", "_Parsed", "detect_uncertainty", "_strip_markers", "_month_number", "_time_precision", "_attempt", "render_temporal", "widen", "AmbiguousReferenceDateError"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
