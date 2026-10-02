---
id: REFACTOR-redstring-consolidation-service
title: Refactor and Decompose Legacy File service.py
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

# TASK-REFACTOR-redstring-consolidation-service: Refactor Legacy File service.py

## Summary
The grandfathered debt file `src/redstring/consolidation/service.py` contains 635 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (service_batches.py, service_banded.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `src/redstring/consolidation/service/` with submodules:
- `service_batches.py`: _batches, ConsolidationService
- `service_banded.py`: _Banded

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/src/redstring/consolidation/service.py (635 lines):
  Submodule 'service_batches.py' (~523 lines):
    - [function] _batches (lines 91-94)
    - [class] ConsolidationService (lines 117-635)
  Submodule 'service_banded.py' (~17 lines):
    - [class] _Banded (lines 98-114)
  Suggested barrel exports:
    from .service_batches import _batches, ConsolidationService
    from .service_banded import _Banded

    __all__ = ["_batches", "ConsolidationService", "_Banded"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
