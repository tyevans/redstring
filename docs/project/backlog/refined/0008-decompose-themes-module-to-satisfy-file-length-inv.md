---
id: '0008'
title: Decompose themes module to satisfy file length invariant (<500 lines)
status: Refined
governing_adrs:
- ADR-0002
governing_prds:
- PRD-0001
governing_stories:
- US-0004
target_bc: composition
mutation_scope: '[''src/redstring/composition/themes.py'']'
---

# TASK-0008: Decompose themes module to satisfy file length invariant (<500 lines)

## Summary
`src/redstring/composition/themes.py` has grown to 485 lines and triggers proactive refactoring warnings under `spec-ops health`, approaching the hard 500-line invariant ceiling (ADR-0002). This task decomposes the module into cohesive submodules while maintaining backwards-compatible exports.

## Problem Statement & Context
1. `themes.py` combines passage ranking, theme graph construction, clustering, and retrieval scoring into a single monolithic file.
2. At 485 lines, any minor feature or bugfix risks breaching the 500-line invariant.
3. Extracting clustering algorithms and passage ranking into dedicated submodules restores clean single-responsibility boundaries.

## Definition of Done (Blackbox Frontdoor TDD)
1. `src/redstring/composition/themes/` package created with focused submodules (`ranking.py`, `graph.py`, `models.py`) each under 300 lines.
2. Root `themes.py` or `themes/__init__.py` maintains 100% public API compatibility.
3. All existing unit and BDD tests pass without regressions.
4. Mutation testing on decomposition boundaries satisfies >=80% kill score.
