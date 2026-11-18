---
id: '0064'
title: Bound graph traversal depth and pin tenant on every traversal endpoint
status: Complete
governing_adrs:
- ADR-0116
governing_prds:
- PRD-0013
governing_stories:
- US-0030
target_bc: graph
---

# TASK-0064: Bound graph traversal depth and pin tenant on every traversal endpoint

## Summary
Give graph traversal a documented maximum depth on every adapter and on `explain_path`, and add the tenant predicate to the far end of every Neo4j traversal.

## Problem Statement & Context
Found by the 2026-11-17 security sweep (resource-exhaustion and tenant-isolation reviews), verified locally.

- `src/redstring/graph/adapters/neo4j.py:636-652` checks `depth` only for sign and then runs `[rels:RELATES_TO*1..{depth}]`. Neo4j enumerates every relationship-unique trail before `DISTINCT`, so cost is exponential in depth on a dense graph. The memory adapter uses BFS, so the two adapters differ in cost on the same input.
- `src/redstring/composition/paths.py:63-64` checks only `max_depth >= 1`, and fetches each neighbour entity before it compares with the target (`:100-121`).
- `neo4j.py:462-469` alias resolution and `neo4j.py:597-599` `get_relationships_for` do not constrain the far-end node to the tenant. No path crosses tenants today, because writes keep both ends in one tenant, so this is defence in depth.

## Definition of Done (Blackbox Frontdoor TDD)
1. A `MAX_TRAVERSAL_DEPTH` constant is part of the `GraphStore` contract; `neighbors(depth=MAX_TRAVERSAL_DEPTH + 1)` raises `ValueError` on the memory adapter and on Neo4j, and `depth=MAX_TRAVERSAL_DEPTH` is accepted (compliance suite case, so third-party adapters inherit it).
2. `explain_path(max_depth=...)` has the same upper limit, and compares the target id before it fetches the entity.
3. Neo4j alias resolution and `get_relationships_for` include `tenant_id = $tenant_id` on every node in the pattern.
