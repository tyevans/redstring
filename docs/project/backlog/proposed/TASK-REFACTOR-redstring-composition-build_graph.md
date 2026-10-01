---
id: REFACTOR-redstring-composition-build_graph
title: Refactor and Decompose Legacy File build_graph.py
status: Proposed
created: 2026-09-29
governing_adrs:
  - ADR-0002
target_bc: core
---

# TASK-REFACTOR-redstring-composition-build_graph: Refactor Legacy File build_graph.py

## Summary
The grandfathered debt file `src/redstring/composition/build_graph.py` contains 992 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (build_graph_domain.py, build_graph_report.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `src/redstring/composition/build_graph/` with submodules:
- `build_graph_domain.py`: AutoDomain, _ResolvedDomain, build_graph, _persist, _check_vocabulary_wiring, _check_embedding_wiring, _embed_entities, _resolve_prompt, Consolidator
- `build_graph_report.py`: GraphBuildReport, ConsolidationReport

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/src/redstring/composition/build_graph.py (992 lines):
  Submodule 'build_graph_domain.py' (~761 lines):
    - [class] AutoDomain (lines 115-126)
    - [class] _ResolvedDomain (lines 614-628)
    - [function] build_graph (lines 202-461)
    - [function] _persist (lines 464-479)
    - [function] _check_vocabulary_wiring (lines 482-500)
    - [function] _check_embedding_wiring (lines 503-545)
    - [function] _embed_entities (lines 548-611)
    - [function] _resolve_prompt (lines 631-658)
    - [class] Consolidator (lines 689-992)
  Submodule 'build_graph_report.py' (~81 lines):
    - [class] GraphBuildReport (lines 139-199)
    - [class] ConsolidationReport (lines 667-686)
  Suggested barrel exports:
    from .build_graph_domain import AutoDomain, _ResolvedDomain, build_graph, _persist, _check_vocabulary_wiring, _check_embedding_wiring, _embed_entities, _resolve_prompt, Consolidator
    from .build_graph_report import GraphBuildReport, ConsolidationReport

    __all__ = ["AutoDomain", "_ResolvedDomain", "build_graph", "_persist", "_check_vocabulary_wiring", "_check_embedding_wiring", "_embed_entities", "_resolve_prompt", "Consolidator", "GraphBuildReport", "ConsolidationReport"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
