---
id: REFACTOR-tests-integration-graph-test_neo4j_store
title: Refactor and Decompose Legacy File test_neo4j_store.py
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

# TASK-REFACTOR-tests-integration-graph-test_neo4j_store: Refactor Legacy File test_neo4j_store.py

## Summary
The grandfathered debt file `tests/integration/graph/test_neo4j_store.py` contains 631 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (test_neo4j_store_probe.py, test_neo4j_store_wipe.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `tests/integration/graph/test_neo4j_store/` with submodules:
- `test_neo4j_store_probe.py`: _probe, neo4j_driver, TestNeo4jStore, TestNeo4jSpecifics, _operators, _QueryLog, _counting, _entity, _relationship
- `test_neo4j_store_wipe.py`: _wipe

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/tests/integration/graph/test_neo4j_store.py (631 lines):
  Submodule 'test_neo4j_store_probe.py' (~532 lines):
    - [function] _probe (lines 66-87)
    - [function] neo4j_driver (lines 108-132)
    - [class] TestNeo4jStore (lines 135-150)
    - [class] TestNeo4jSpecifics (lines 153-550)
    - [function] _operators (lines 553-559)
    - [class] _QueryLog (lines 562-569)
    - [function] _counting (lines 572-591)
    - [function] _entity (lines 594-618)
    - [function] _relationship (lines 621-631)
  Submodule 'test_neo4j_store_wipe.py' (~9 lines):
    - [function] _wipe (lines 90-98)
  Suggested barrel exports:
    from .test_neo4j_store_probe import _probe, neo4j_driver, TestNeo4jStore, TestNeo4jSpecifics, _operators, _QueryLog, _counting, _entity, _relationship
    from .test_neo4j_store_wipe import _wipe

    __all__ = ["_probe", "neo4j_driver", "TestNeo4jStore", "TestNeo4jSpecifics", "_operators", "_QueryLog", "_counting", "_entity", "_relationship", "_wipe"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
