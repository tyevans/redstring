---
id: '0013'
title: Audit and prune backwards compatibility shims in projections BC
status: Proposed
governing_adrs:
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0002
target_bc: projections
---

# TASK-0013: Audit and prune backwards compatibility shims in projections BC

## Summary
Audit `src/redstring/projections/` modules for deprecated import shims, legacy event handling shims, and transitional re-exports.

## Context & Objectives
- Ensure read model projections cleanly consume domain events without transitional translation shims.
- Remove redundant barrel exports from `projections/__init__.py`.
- Verify replay and projection test suites pass cleanly.
