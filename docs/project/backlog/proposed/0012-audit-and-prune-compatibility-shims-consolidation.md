---
id: '0012'
title: Audit and prune backwards compatibility shims in consolidation BC
status: Proposed
governing_adrs:
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0002
target_bc: consolidation
---

# TASK-0012: Audit and prune backwards compatibility shims in consolidation BC

## Summary
Audit `src/redstring/consolidation/` modules (`service.py`, `banded.py`, `policy.py`, `candidates.py`, `resolve_many.py`) for legacy compatibility aliases (e.g., `_Banded`), private helper exports, and superseded merge policy wrappers.

## Context & Objectives
- Replace private compatibility aliases with canonical types (`BandedCandidates`).
- Verify module isolation and ensure only domain contracts are exported across package boundaries.
- Ensure all callsites in tests and consumer modules use canonical imports.
