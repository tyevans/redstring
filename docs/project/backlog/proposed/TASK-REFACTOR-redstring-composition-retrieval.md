---
id: REFACTOR-redstring-composition-retrieval
title: Refactor and Decompose Legacy File retrieval.py
status: Proposed
created: 2026-09-29
governing_adrs:
  - ADR-0002
target_bc: core
---

# TASK-REFACTOR-redstring-composition-retrieval: Refactor Legacy File retrieval.py

## Summary
The grandfathered debt file `src/redstring/composition/retrieval.py` contains 505 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (retrieval_chunk.py, retrieval_core.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `src/redstring/composition/retrieval/` with submodules:
- `retrieval_chunk.py`: ChunkRetriever
- `retrieval_core.py`: Retriever

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/src/redstring/composition/retrieval.py (505 lines):
  Submodule 'retrieval_chunk.py' (~174 lines):
    - [class] ChunkRetriever (lines 332-505)
  Submodule 'retrieval_core.py' (~249 lines):
    - [class] Retriever (lines 81-329)
  Suggested barrel exports:
    from .retrieval_chunk import ChunkRetriever
    from .retrieval_core import Retriever

    __all__ = ["ChunkRetriever", "Retriever"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
