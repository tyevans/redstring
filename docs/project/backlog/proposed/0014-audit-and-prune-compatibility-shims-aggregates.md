---
id: '0014'
title: Audit and prune backwards compatibility shims in aggregates BC
status: Proposed
governing_adrs:
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0002
target_bc: aggregates
---

# TASK-0014: Audit and prune backwards compatibility shims in aggregates BC

## Summary
Audit `src/redstring/aggregates/` roots (`Document`, `ConsolidationLog`) and repository wrappers for obsolete state method aliases, legacy compatibility methods, and deprecated re-exports.

## Context & Objectives
- Prune obsolete aliases on write aggregates.
- Ensure event emission methods enforce current domain contracts.
- Verify repository factories provide clean typed access without transitional shims.
