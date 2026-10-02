---
id: '0011'
title: Audit and prune backwards compatibility shims in extraction BC
status: Refined
dependencies: []
governing_adrs:
- ADR-0003
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0005
target_bc: extraction
mutation_scope:
- src/redstring/extraction/
---

# TASK-0011: Audit and prune backwards compatibility shims in extraction BC

## Summary
Audit `src/redstring/extraction/` modules (`mapping.py`, `pipeline.py`, `protocols.py`, `chunkers/`, and `domains/`) for legacy re-exports of domain models moved during historical refactors, redundant alias definitions, and backwards compatibility shims.

## Problem Statement & Context
1. Historical refactoring relocated extraction models and schema definitions across package boundaries.
2. Lingering compatibility re-exports and alias shims obscure true architectural boundaries.
3. Callers and test suites should import from authoritative locations rather than relying on legacy shims.

## Definition of Done (Blackbox Frontdoor TDD)
1. Complete audit of all files in `src/redstring/extraction/` for deprecated shims and legacy re-exports.
2. Remove identified dead shims without breaking public exports in `redstring/__init__.py`.
3. Update any internal callsites across tests and sibling modules to import from authoritative definitions.
4. Verify all extraction test suites (`tests/unit/extraction/`) pass with 100% compliance.
5. All source files strictly <500 lines.
