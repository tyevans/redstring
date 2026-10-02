---
id: '0018'
title: Audit and prune backwards compatibility shims in domain BC
status: Proposed
governing_adrs:
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0002
target_bc: domain
---

# TASK-0018: Audit and prune backwards compatibility shims in domain BC

## Summary
Audit `src/redstring/domain/` (`domain/__init__.py` and core domain models) for circular or redundant barrel re-exports, deprecated aliases, and transitional shims.

## Context & Objectives
- Ensure domain models maintain pure encapsulation without lingering transitional shims.
- Clean up `domain/__init__.py` barrel exports to prevent circular dependency hazards.
- Ensure all domain types are strictly validated and satisfy Hypothesis property tests.
