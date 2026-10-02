---
id: '0015'
title: Audit and prune backwards compatibility shims in events BC
status: Refined
governing_adrs:
- ADR-0003
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0001
target_bc: events
mutation_scope: '[''src/redstring/events/'']'
---

# TASK-0015: Audit and prune backwards compatibility shims in events BC

## Summary
Audit `src/redstring/events/` schemas and stream factories for legacy event type aliases, deprecated field translations, and transitional re-exports.

## Problem Statement & Context
1. Historical refactoring relocated event classes and stream payloads.
2. Lingering compatibility re-exports and alias shims obscure true architectural boundaries.
3. Callers and test suites should import from authoritative event schemas rather than relying on legacy shims.

## Definition of Done (Blackbox Frontdoor TDD)
1. Complete audit of all files in `src/redstring/events/` for deprecated shims and legacy re-exports.
2. Ensure immutable event definitions represent canonical domain occurrences without deprecated field aliases.
3. Update any internal callsites across tests and sibling modules to import from authoritative definitions.
4. Verify all event test suites (`tests/unit/events/`) pass with 100% compliance.
5. All source files strictly <500 lines.
