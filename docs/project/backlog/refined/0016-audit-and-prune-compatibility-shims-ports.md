---
id: '0016'
title: Audit and prune backwards compatibility shims in ports BC
status: Refined
governing_adrs:
- ADR-0003
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0005
target_bc: ports
mutation_scope: '[''src/redstring/ports/'']'
---

# TASK-0016: Audit and prune backwards compatibility shims in ports BC

## Summary
Audit `src/redstring/ports/` interfaces (`graph_store.py`, `chunk_store.py`, `vector_store.py`) to eliminate obsolete method signatures, deprecated capability flags, and transitional shims.

## Problem Statement & Context
1. Historical refactoring relocated port protocols and adapter contracts.
2. Lingering compatibility re-exports and alias shims obscure true architectural boundaries.
3. Callers and adapters should implement and consume authoritative port protocols directly.

## Definition of Done (Blackbox Frontdoor TDD)
1. Complete audit of all files in `src/redstring/ports/` for deprecated shims, parameters, and re-exports.
2. Ensure port protocols define pure interfaces without infrastructure leakage or backward-compatibility parameters.
3. Update any internal callsites across tests and adapters to import from authoritative definitions.
4. Verify port and adapter tests pass with 100% compliance.
5. All source files strictly <500 lines.
