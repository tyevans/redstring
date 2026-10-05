---
id: REFACTOR-redstring-chunks-adapters-postgres
title: Refactor and Decompose Legacy File postgres.py
status: Refined
governing_adrs:
- ADR-0002
governing_prds:
- PRD-0001
governing_stories:
- US-0003
target_bc: core
---

# TASK-REFACTOR-redstring-chunks-adapters-postgres: Refactor Legacy File postgres.py

## Summary
The grandfathered debt file `src/redstring/chunks/adapters/postgres.py` contains 1006 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (postgres_encode.py, postgres_chunk.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `src/redstring/chunks/adapters/postgres/` with submodules:
- `postgres_encode.py`: encode, encode_vector, encode_terms, reject_zero_norm, deduplicate
- `postgres_chunk.py`: PostgresChunkStore, _chunk_from

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/src/redstring/chunks/adapters/postgres.py (1006 lines):
  Submodule 'postgres_encode.py' (~97 lines):
    - [function] encode (lines 900-934)
    - [function] encode_vector (lines 937-949)
    - [function] encode_terms (lines 952-973)
    - [function] reject_zero_norm (lines 869-881)
    - [function] deduplicate (lines 884-897)
  Submodule 'postgres_chunk.py' (~740 lines):
    - [class] PostgresChunkStore (lines 148-856)
    - [function] _chunk_from (lines 976-1006)
  Suggested barrel exports:
    from .postgres_encode import encode, encode_vector, encode_terms, reject_zero_norm, deduplicate
    from .postgres_chunk import PostgresChunkStore, _chunk_from

    __all__ = ["encode", "encode_vector", "encode_terms", "reject_zero_norm", "deduplicate", "PostgresChunkStore", "_chunk_from"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
