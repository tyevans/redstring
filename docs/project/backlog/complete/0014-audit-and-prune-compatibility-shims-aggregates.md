---
id: '0014'
title: Audit and prune backwards compatibility shims in aggregates BC
status: Complete
governing_adrs:
- ADR-0003
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0001
target_bc: aggregates
signed_off_by: Ty Evans <ty@tyevans.net>
signed_off_at: '2026-10-04T17:33:44.443389+00:00'
mutation_scope: '[''src/redstring/aggregates/'']'
---

# TASK-0014: Audit and prune backwards compatibility shims in aggregates BC

## Summary
Audit `src/redstring/aggregates/` roots (`Document`, `ConsolidationLog`) and repository wrappers for obsolete state method aliases, legacy compatibility methods, and deprecated re-exports.

## Problem Statement & Context
1. Historical refactoring relocated aggregate state definitions and event factory methods.
2. Lingering compatibility aliases on aggregate roots obscure event sourcing write boundaries.
3. Callers and test suites should invoke current aggregate methods rather than relying on legacy shims.

## Definition of Done (Blackbox Frontdoor TDD)
1. Complete audit of all files in `src/redstring/aggregates/` for deprecated shims and legacy aliases.
2. Prune obsolete aliases on write aggregates while ensuring event emission contracts remain intact.
3. Update any internal callsites across tests and sibling modules to import from authoritative definitions.
4. Verify all aggregate test suites (`tests/unit/aggregates/` or related unit tests) pass with 100% compliance.
5. All source files strictly <500 lines.
