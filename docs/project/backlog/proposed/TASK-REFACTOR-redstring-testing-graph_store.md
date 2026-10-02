---
id: REFACTOR-redstring-testing-graph_store
title: Refactor and Decompose Legacy File graph_store.py
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

# TASK-REFACTOR-redstring-testing-graph_store: Refactor Legacy File graph_store.py

## Summary
The grandfathered debt file `src/redstring/testing/graph_store.py` contains 1792 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (graph_store_example.py, graph_store_mutate.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `src/redstring/testing/graph_store/` with submodules:
- `graph_store_example.py`: _example_entity, _example_alias, _example_relationship, GraphStoreCompliance
- `graph_store_mutate.py`: _mutate

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/src/redstring/testing/graph_store.py (1792 lines):
  Submodule 'graph_store_example.py' (~1654 lines):
    - [function] _example_entity (lines 1746-1761)
    - [function] _example_alias (lines 1764-1774)
    - [function] _example_relationship (lines 1777-1792)
    - [class] GraphStoreCompliance (lines 133-1743)
  Submodule 'graph_store_mutate.py' (~14 lines):
    - [function] _mutate (lines 117-130)
  Suggested barrel exports:
    from .graph_store_example import _example_entity, _example_alias, _example_relationship, GraphStoreCompliance
    from .graph_store_mutate import _mutate

    __all__ = ["_example_entity", "_example_alias", "_example_relationship", "GraphStoreCompliance", "_mutate"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
