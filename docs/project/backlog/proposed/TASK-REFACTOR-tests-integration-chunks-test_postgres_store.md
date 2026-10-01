---
id: REFACTOR-tests-integration-chunks-test_postgres_store
title: Refactor and Decompose Legacy File test_postgres_store.py
status: Proposed
created: 2026-09-29
governing_adrs:
  - ADR-0002
target_bc: core
---

# TASK-REFACTOR-tests-integration-chunks-test_postgres_store: Refactor Legacy File test_postgres_store.py

## Summary
The grandfathered debt file `tests/integration/chunks/test_postgres_store.py` contains 1041 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (test_postgres_store_pool.py, test_postgres_store_chunk.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `tests/integration/chunks/test_postgres_store/` with submodules:
- `test_postgres_store_pool.py`: pool, _OneConnectionPool, _RecordingPool, _probe, _columns, _truncate
- `test_postgres_store_chunk.py`: TestPostgresChunkStore, TestPostgresChunkStoreSpecifics

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/tests/integration/chunks/test_postgres_store.py (1041 lines):
  Submodule 'test_postgres_store_pool.py' (~141 lines):
    - [function] pool (lines 100-122)
    - [class] _OneConnectionPool (lines 156-187)
    - [class] _RecordingPool (lines 190-223)
    - [function] _probe (lines 69-93)
    - [function] _columns (lines 125-137)
    - [function] _truncate (lines 140-153)
  Submodule 'test_postgres_store_chunk.py' (~814 lines):
    - [class] TestPostgresChunkStore (lines 226-241)
    - [class] TestPostgresChunkStoreSpecifics (lines 244-1041)
  Suggested barrel exports:
    from .test_postgres_store_pool import pool, _OneConnectionPool, _RecordingPool, _probe, _columns, _truncate
    from .test_postgres_store_chunk import TestPostgresChunkStore, TestPostgresChunkStoreSpecifics

    __all__ = ["pool", "_OneConnectionPool", "_RecordingPool", "_probe", "_columns", "_truncate", "TestPostgresChunkStore", "TestPostgresChunkStoreSpecifics"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
