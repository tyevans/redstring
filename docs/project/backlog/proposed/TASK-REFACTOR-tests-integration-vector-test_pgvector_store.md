---
id: REFACTOR-tests-integration-vector-test_pgvector_store
title: Refactor and Decompose Legacy File test_pgvector_store.py
status: Proposed
created: 2026-09-29
governing_adrs:
  - ADR-0002
governing_prds:
  - PRD-0001
governing_stories:
  - US-0003
target_bc: core
---

# TASK-REFACTOR-tests-integration-vector-test_pgvector_store: Refactor Legacy File test_pgvector_store.py

## Summary
The grandfathered debt file `tests/integration/vector/test_pgvector_store.py` contains 543 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (test_pgvector_store_probe.py, test_pgvector_store_pool.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `tests/integration/vector/test_pgvector_store/` with submodules:
- `test_pgvector_store_probe.py`: _probe, _truncate, TestPgVectorStore, TestPgVectorSpecifics
- `test_pgvector_store_pool.py`: pool

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/tests/integration/vector/test_pgvector_store.py (543 lines):
  Submodule 'test_pgvector_store_probe.py' (~434 lines):
    - [function] _probe (lines 75-104)
    - [function] _truncate (lines 136-143)
    - [class] TestPgVectorStore (lines 146-161)
    - [class] TestPgVectorSpecifics (lines 164-543)
  Submodule 'test_pgvector_store_pool.py' (~23 lines):
    - [function] pool (lines 111-133)
  Suggested barrel exports:
    from .test_pgvector_store_probe import _probe, _truncate, TestPgVectorStore, TestPgVectorSpecifics
    from .test_pgvector_store_pool import pool

    __all__ = ["_probe", "_truncate", "TestPgVectorStore", "TestPgVectorSpecifics", "pool"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
