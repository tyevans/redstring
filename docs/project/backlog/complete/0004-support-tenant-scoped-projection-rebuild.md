---
id: '0004'
title: Support tenant-scoped projection rebuild
status: Complete
governing_adrs:
- ADR-0002
- ADR-0102
governing_prds:
- PRD-0001
governing_stories:
- US-0003
target_bc: projections
mutation_scope: '[''src/redstring/projections/graph.py'']'
---

# TASK-0004: Support tenant-scoped projection rebuild

## Summary
`GraphProjection._truncate_read_models` raises `NotImplementedError` because ports deliberately disallow cross-tenant delete operations (BACKLOG B35). This task implements tenant-scoped `rebuild(tenant_id)` entry points on projections.

## Problem Statement & Context
Neither GraphStore nor VectorStore allows cross-tenant deletion, ensuring strict multi-tenant isolation. Consequently, `CheckpointTrackingProjection.reset()` cannot be honoured globally without corrupting other tenants. Implementing explicit `rebuild(tenant_id)` or `reset(tenant_id)` allows safe tenant-scoped projection clearing and re-hydration.

## Mutation Testing Scope (ADR-0009)
Target module: `src/redstring/projections/graph.py`. Ensure >=80% mutant kill score under `mutmut`.

## Definition of Done (Blackbox Frontdoor TDD)
1. Tenant-scoped rebuild resets read model exclusively for the targeted tenant.
2. Verified via blackbox frontdoor tests exercising projection replay.
3. Target module achieves >=80% mutation kill score under `mutmut`.
4. All source files strictly under 500 lines limit.
