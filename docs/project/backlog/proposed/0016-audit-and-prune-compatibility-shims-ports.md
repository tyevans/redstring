---
id: '0016'
title: Audit and prune backwards compatibility shims in ports BC
status: Proposed
governing_adrs:
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0002
target_bc: ports
---

# TASK-0016: Audit and prune backwards compatibility shims in ports BC

## Summary
Audit `src/redstring/ports/` interfaces (`graph_store.py`, `chunk_store.py`, `vector_store.py`) to eliminate obsolete method signatures, deprecated capability flags, and transitional shims.

## Context & Objectives
- Verify port interfaces strictly define foundational contracts with zero infrastructure leakage.
- Remove deprecated backward-compatibility parameters and shims across port protocols.
- Align adapter implementations to canonical port methods.
