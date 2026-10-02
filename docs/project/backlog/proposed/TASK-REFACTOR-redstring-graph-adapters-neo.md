---
id: REFACTOR-redstring-graph-adapters-neo-graph
title: Refactor and Decompose Legacy File neo4j.py
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

# TASK-REFACTOR-redstring-graph-adapters-neo4j: Refactor Legacy File neo4j.py

## Summary
The grandfathered debt file `src/redstring/graph/adapters/neo4j.py` contains 848 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (neo4j_row.py, neo4j_from.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `src/redstring/graph/adapters/neo4j/` with submodules:
- `neo4j_row.py`: _entity_row, _alias_row, _relationship_row, Neo4jGraphStore, rows_carrying_keys
- `neo4j_from.py`: _entity_from, _alias_from, _relationship_from

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/src/redstring/graph/adapters/neo4j.py (848 lines):
  Submodule 'neo4j_row.py' (~613 lines):
    - [function] _entity_row (lines 705-739)
    - [function] _alias_row (lines 784-801)
    - [function] _relationship_row (lines 820-833)
    - [class] Neo4jGraphStore (lines 161-694)
    - [function] rows_carrying_keys (lines 770-781)
  Submodule 'neo4j_from.py' (~53 lines):
    - [function] _entity_from (lines 742-767)
    - [function] _alias_from (lines 804-817)
    - [function] _relationship_from (lines 836-848)
  Suggested barrel exports:
    from .neo4j_row import _entity_row, _alias_row, _relationship_row, Neo4jGraphStore, rows_carrying_keys
    from .neo4j_from import _entity_from, _alias_from, _relationship_from

    __all__ = ["_entity_row", "_alias_row", "_relationship_row", "Neo4jGraphStore", "rows_carrying_keys", "_entity_from", "_alias_from", "_relationship_from"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
