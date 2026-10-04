---
id: '0017'
title: Audit and prune backwards compatibility shims in composition BC
status: Complete
governing_adrs:
- ADR-0003
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0004
target_bc: composition
signed_off_by: Ty Evans <ty@tyevans.net>
signed_off_at: '2026-10-04T18:12:17.097691+00:00'
mutation_scope: '[''src/redstring/composition/'']'
---

# TASK-0017: Audit and prune backwards compatibility shims in composition BC

## Summary
Audit `src/redstring/composition/` (`composition/__init__.py`, `build_graph.py`, `retrieval.py`, and `themes/`) to dismantle the blanket module re-export facade and replace with intentional, documented entrypoints.

## Problem Statement & Context
1. Historical refactoring accumulated wide wildcard and transitional re-exports in composition.
2. Lingering compatibility aliases on composition classes obscure high-level service contracts.
3. Callers and test suites should import and invoke canonical composition entrypoints.

## Definition of Done (Blackbox Frontdoor TDD)
1. Complete audit of all files in `src/redstring/composition/` for deprecated shims and legacy re-exports.
2. Replace blanket re-export facades with explicit, intentional public entrypoints.
3. Update any internal callsites across tests and sibling modules to import from authoritative definitions.
4. Verify all composition test suites (`tests/unit/composition/`) pass with 100% compliance.
5. All source files strictly <500 lines.
