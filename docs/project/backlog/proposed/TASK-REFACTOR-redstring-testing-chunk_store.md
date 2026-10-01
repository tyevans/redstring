---
id: REFACTOR-redstring-testing-chunk_store
title: Refactor and Decompose Legacy File chunk_store.py
status: Proposed
created: 2026-09-29
governing_adrs:
  - ADR-0002
target_bc: core
---

# TASK-REFACTOR-redstring-testing-chunk_store: Refactor Legacy File chunk_store.py

## Summary
The grandfathered debt file `src/redstring/testing/chunk_store.py` contains 1769 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (chunk_store_mutate.py, chunk_store_compliance.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `src/redstring/testing/chunk_store/` with submodules:
- `chunk_store_mutate.py`: _mutate
- `chunk_store_compliance.py`: ChunkStoreCompliance

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/src/redstring/testing/chunk_store.py (1769 lines):
  Submodule 'chunk_store_mutate.py' (~15 lines):
    - [function] _mutate (lines 79-93)
  Submodule 'chunk_store_compliance.py' (~1674 lines):
    - [class] ChunkStoreCompliance (lines 96-1769)
  Suggested barrel exports:
    from .chunk_store_mutate import _mutate
    from .chunk_store_compliance import ChunkStoreCompliance

    __all__ = ["_mutate", "ChunkStoreCompliance"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
