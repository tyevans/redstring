---
id: 0018
title: Audit and prune backwards compatibility shims in domain BC
status: Complete
governing_adrs:
- ADR-0003
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0001
target_bc: domain
signed_off_by: Ty Evans <ty@tyevans.net>
signed_off_at: '2026-10-04T18:32:03.232784+00:00'
mutation_scope: '[''src/redstring/domain/'']'
---

# TASK-0018: Audit and prune backwards compatibility shims in domain BC

## Summary
Audit `src/redstring/domain/` (`domain/__init__.py` and core domain models) for circular or redundant barrel re-exports, deprecated aliases, and transitional shims.

## Problem Statement & Context
1. Historical refactoring accumulated backwards compatibility shims and re-exports in domain modules.
2. Lingering compatibility aliases obscure pure DDD domain contracts.
3. Callers and test suites should import from authoritative domain modules.

## Definition of Done (Blackbox Frontdoor TDD)
1. Complete audit of all files in `src/redstring/domain/` for deprecated shims and legacy re-exports.
2. Ensure domain models maintain pure encapsulation without lingering transitional shims.
3. Update any internal callsites across tests and sibling modules to import from authoritative definitions.
4. Verify all domain test suites (`tests/unit/domain/`) pass with 100% compliance.
5. All source files strictly <500 lines.
