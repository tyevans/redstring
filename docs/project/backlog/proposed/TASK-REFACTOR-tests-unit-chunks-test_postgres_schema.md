---
id: REFACTOR-tests-unit-chunks-test_postgres_schema
title: Refactor and Decompose Legacy File test_postgres_schema.py
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

# TASK-REFACTOR-tests-unit-chunks-test_postgres_schema: Refactor Legacy File test_postgres_schema.py

## Summary
The grandfathered debt file `tests/unit/chunks/test_postgres_schema.py` contains 532 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (test_postgres_schema_store.py, test_postgres_schema_chunk.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `tests/unit/chunks/test_postgres_schema/` with submodules:
- `test_postgres_schema_store.py`: _store, TestChunkStoreSyntaxDoesNotLeak, _ExplodingPool, TestConstruction, TestGuardsRunBeforeAnyIO, TestSqlConstruction, TestEncoding, TestDeduplicate, TestStructure
- `test_postgres_schema_chunk.py`: _chunk

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/tests/unit/chunks/test_postgres_schema.py (532 lines):
  Submodule 'test_postgres_schema_store.py' (~436 lines):
    - [function] _store (lines 57-58)
    - [class] TestChunkStoreSyntaxDoesNotLeak (lines 510-532)
    - [class] _ExplodingPool (lines 44-54)
    - [class] TestConstruction (lines 84-131)
    - [class] TestGuardsRunBeforeAnyIO (lines 134-204)
    - [class] TestSqlConstruction (lines 207-381)
    - [class] TestEncoding (lines 384-426)
    - [class] TestDeduplicate (lines 429-449)
    - [class] TestStructure (lines 452-493)
  Submodule 'test_postgres_schema_chunk.py' (~21 lines):
    - [function] _chunk (lines 61-81)
  Suggested barrel exports:
    from .test_postgres_schema_store import _store, TestChunkStoreSyntaxDoesNotLeak, _ExplodingPool, TestConstruction, TestGuardsRunBeforeAnyIO, TestSqlConstruction, TestEncoding, TestDeduplicate, TestStructure
    from .test_postgres_schema_chunk import _chunk

    __all__ = ["_store", "TestChunkStoreSyntaxDoesNotLeak", "_ExplodingPool", "TestConstruction", "TestGuardsRunBeforeAnyIO", "TestSqlConstruction", "TestEncoding", "TestDeduplicate", "TestStructure", "_chunk"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
