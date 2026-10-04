---
id: '0012'
title: Audit and prune backwards compatibility shims in consolidation BC
status: Complete
governing_adrs:
- ADR-0003
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0002
target_bc: consolidation
signed_off_by: Ty Evans <ty@tyevans.net>
signed_off_at: '2026-10-04T17:02:23.997936+00:00'
mutation_scope: '[''src/redstring/consolidation/'']'
---

# TASK-0012: Audit and prune backwards compatibility shims in consolidation BC

## Summary
Audit `src/redstring/consolidation/` modules (`service.py`, `banded.py`, `policy.py`, `candidates.py`, `resolve_many.py`) for legacy compatibility aliases (e.g., `_Banded`), private helper exports, and superseded merge policy wrappers.

## Problem Statement & Context
1. Historical refactoring relocated consolidation models and merge routines across module boundaries.
2. Lingering compatibility re-exports and alias shims obscure true architectural boundaries.
3. Callers and test suites should import from authoritative locations rather than relying on legacy shims.

## Definition of Done (Blackbox Frontdoor TDD)
1. Complete audit of all files in `src/redstring/consolidation/` for deprecated shims and legacy re-exports.
2. Remove identified dead shims without breaking public exports in `redstring/__init__.py`.
3. Update any internal callsites across tests and sibling modules to import from authoritative definitions.
4. Verify all consolidation test suites (`tests/unit/consolidation/`) pass with 100% compliance.
5. All source files strictly <500 lines.
