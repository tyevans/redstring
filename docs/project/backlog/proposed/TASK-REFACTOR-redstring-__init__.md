---
id: REFACTOR-redstring-__init__
title: Refactor and Decompose Legacy File __init__.py
status: Proposed
created: 2026-09-29
governing_adrs:
  - ADR-0002
target_bc: core
---

# TASK-REFACTOR-redstring-__init__: Refactor Legacy File __init__.py

## Summary
The grandfathered debt file `src/redstring/__init__.py` contains 509 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (__init___part1.py, __init___part2.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `src/redstring/__init__/` with submodules:
- `__init___part1.py`:
- `__init___part2.py`:

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/src/redstring/__init__.py (509 lines):
  Submodule '__init___part1.py' (~0 lines):
  Submodule '__init___part2.py' (~0 lines):

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
