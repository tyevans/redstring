---
id: '0011'
title: Audit and prune backwards compatibility shims in extraction BC
status: Proposed
governing_adrs:
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0002
target_bc: extraction
---

# TASK-0011: Audit and prune backwards compatibility shims in extraction BC

## Summary
Audit `src/redstring/extraction/` modules (including `mapping.py`, `protocols.py`, `chunkers/`, and `domains/`) for legacy re-exports of domain models moved during historical refactors, redundant alias definitions, and backwards compatibility shims.

## Context & Objectives
- Eliminate lingering re-exports (such as types re-exported from `mapping.py` that now live in `domain/`).
- Clarify public module interfaces vs internal helpers.
- Ensure all callsites in tests and consumer modules import directly from authoritative bounded context locations.
- Verify zero regression across extraction test suites.
