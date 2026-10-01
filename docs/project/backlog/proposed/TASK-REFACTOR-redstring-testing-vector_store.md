---
id: REFACTOR-redstring-testing-vector_store
title: Refactor and Decompose Legacy File vector_store.py
status: Proposed
created: 2026-09-29
governing_adrs:
  - ADR-0002
target_bc: core
---

# TASK-REFACTOR-redstring-testing-vector_store: Refactor Legacy File vector_store.py

## Summary
The grandfathered debt file `src/redstring/testing/vector_store.py` contains 1074 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (vector_store_mutate.py, vector_store_compliance.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `src/redstring/testing/vector_store/` with submodules:
- `vector_store_mutate.py`: _mutate_record, _mutate_match
- `vector_store_compliance.py`: VectorStoreCompliance

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/src/redstring/testing/vector_store.py (1074 lines):
  Submodule 'vector_store_mutate.py' (~17 lines):
    - [function] _mutate_record (lines 117-126)
    - [function] _mutate_match (lines 129-135)
  Submodule 'vector_store_compliance.py' (~937 lines):
    - [class] VectorStoreCompliance (lines 138-1074)
  Suggested barrel exports:
    from .vector_store_mutate import _mutate_record, _mutate_match
    from .vector_store_compliance import VectorStoreCompliance

    __all__ = ["_mutate_record", "_mutate_match", "VectorStoreCompliance"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
