---
id: '0013'
title: Audit and prune backwards compatibility shims in projections BC
status: Refined
governing_adrs:
- ADR-0003
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0003
target_bc: projections
mutation_scope: '[''src/redstring/projections/'']'
---

# TASK-0013: Audit and prune backwards compatibility shims in projections BC

## Summary
Audit `src/redstring/projections/` modules for deprecated import shims, legacy event handling shims, and transitional re-exports.

## Problem Statement & Context
1. Historical refactoring relocated projection logic and event handlers across package boundaries.
2. Lingering compatibility re-exports and alias shims obscure true architectural boundaries.
3. Callers and test suites should import from authoritative locations rather than relying on legacy shims.

## Definition of Done (Blackbox Frontdoor TDD)
1. Complete audit of all files in `src/redstring/projections/` for deprecated shims and legacy re-exports.
2. Ensure read model projections cleanly consume domain events without transitional translation shims.
3. Update any internal callsites across tests and sibling modules to import from authoritative definitions.
4. Verify all projection test suites (`tests/unit/projections/`) pass with 100% compliance.
5. All source files strictly <500 lines.
