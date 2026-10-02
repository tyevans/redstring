---
id: REFACTOR-redstring-vector-adapters-pgvector
title: Refactor and Decompose Legacy File pgvector.py
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

# TASK-REFACTOR-redstring-vector-adapters-pgvector: Refactor Legacy File pgvector.py

## Summary
The grandfathered debt file `src/redstring/vector/adapters/pgvector.py` contains 514 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (pgvector_encodable.py, pgvector_store.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `src/redstring/vector/adapters/pgvector/` with submodules:
- `pgvector_encodable.py`: _encodable, encode_vector, deduplicate
- `pgvector_store.py`: PgVectorStore

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/src/redstring/vector/adapters/pgvector.py (514 lines):
  Submodule 'pgvector_encodable.py' (~46 lines):
    - [function] _encodable (lines 105-125)
    - [function] encode_vector (lines 488-502)
    - [function] deduplicate (lines 505-514)
  Submodule 'pgvector_store.py' (~348 lines):
    - [class] PgVectorStore (lines 128-475)
  Suggested barrel exports:
    from .pgvector_encodable import _encodable, encode_vector, deduplicate
    from .pgvector_store import PgVectorStore

    __all__ = ["_encodable", "encode_vector", "deduplicate", "PgVectorStore"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
