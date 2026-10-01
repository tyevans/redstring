---
id: REFACTOR-tests-unit-graph-test_neo4j_adapter_is_wired
title: Refactor and Decompose Legacy File test_neo4j_adapter_is_wired.py
status: Proposed
created: 2026-09-29
governing_adrs:
  - ADR-0002
target_bc: core
---

# TASK-REFACTOR-tests-unit-graph-test_neo4j_adapter_is_wired: Refactor Legacy File test_neo4j_adapter_is_wired.py

## Summary
The grandfathered debt file `tests/unit/graph/test_neo4j_adapter_is_wired.py` contains 668 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (test_neo4j_adapter_is_wired_the.py, test_neo4j_adapter_is_wired_port.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `tests/unit/graph/test_neo4j_adapter_is_wired/` with submodules:
- `test_neo4j_adapter_is_wired_the.py`: TestTheAdapterImplementsThePort, TestCypherNeverLeavesTheAdapter, TestTheWriteReportsWhatItFailedToWrite, TestSchemaStatementsAreWellFormed, TestNoUnparameterisedInterpolation, _ExplodingDriver, _offline_store, TestArgumentValidationNeedsNoDatabase, _ScriptedStore, _edge, TestEncodingIsPureAndReversible, _entity, _as_node, _modules_containing_cypher
- `test_neo4j_adapter_is_wired_port.py`: _port_methods

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/tests/unit/graph/test_neo4j_adapter_is_wired.py (668 lines):
  Submodule 'test_neo4j_adapter_is_wired_the.py' (~541 lines):
    - [class] TestTheAdapterImplementsThePort (lines 93-140)
    - [class] TestCypherNeverLeavesTheAdapter (lines 143-187)
    - [class] TestTheWriteReportsWhatItFailedToWrite (lines 373-436)
    - [class] TestSchemaStatementsAreWellFormed (lines 190-211)
    - [class] TestNoUnparameterisedInterpolation (lines 214-248)
    - [class] _ExplodingDriver (lines 251-259)
    - [function] _offline_store (lines 262-263)
    - [class] TestArgumentValidationNeedsNoDatabase (lines 266-331)
    - [class] _ScriptedStore (lines 334-359)
    - [function] _edge (lines 362-370)
    - [class] TestEncodingIsPureAndReversible (lines 439-616)
    - [function] _entity (lines 619-637)
    - [function] _as_node (lines 640-649)
    - [function] _modules_containing_cypher (lines 652-659)
  Submodule 'test_neo4j_adapter_is_wired_port.py' (~7 lines):
    - [function] _port_methods (lines 662-668)
  Suggested barrel exports:
    from .test_neo4j_adapter_is_wired_the import TestTheAdapterImplementsThePort, TestCypherNeverLeavesTheAdapter, TestTheWriteReportsWhatItFailedToWrite, TestSchemaStatementsAreWellFormed, TestNoUnparameterisedInterpolation, _ExplodingDriver, _offline_store, TestArgumentValidationNeedsNoDatabase, _ScriptedStore, _edge, TestEncodingIsPureAndReversible, _entity, _as_node, _modules_containing_cypher
    from .test_neo4j_adapter_is_wired_port import _port_methods

    __all__ = ["TestTheAdapterImplementsThePort", "TestCypherNeverLeavesTheAdapter", "TestTheWriteReportsWhatItFailedToWrite", "TestSchemaStatementsAreWellFormed", "TestNoUnparameterisedInterpolation", "_ExplodingDriver", "_offline_store", "TestArgumentValidationNeedsNoDatabase", "_ScriptedStore", "_edge", "TestEncodingIsPureAndReversible", "_entity", "_as_node", "_modules_containing_cypher", "_port_methods"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
