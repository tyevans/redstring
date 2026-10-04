---
id: 0019
title: 'SPIKE: Cross-Bounded Context Abstractions and Consolidation Opportunities'
status: Complete
dependencies:
- TASK-0011
- TASK-0012
- TASK-0013
- TASK-0014
- TASK-0015
- TASK-0016
- TASK-0017
- TASK-0018
governing_adrs:
- ADR-0001
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0002
target_bc: core
signed_off_by: Ty Evans <ty@tyevans.net>
signed_off_at: '2026-10-04T18:46:31.470370+00:00'
mutation_scope: '[''src/redstring/'']'
---

# TASK-0019: SPIKE: Cross-Bounded Context Abstractions and Consolidation Opportunities

## Summary
Audit common cross-cutting patterns across storage adapters (Neo4j, PgVector, In-Memory), event stream handling, retry/replanning loops, and batching primitives (`_batches`), identifying clean shared domain abstractions without violating port/adapter layering or DDD boundaries.

## Problem Statement & Context
As `redstring` evolved, several Bounded Contexts independently developed similar utility patterns:
- Concurrency retry and re-planning logic (e.g. `OptimisticLockError` retry loops in consolidation and extraction).
- Slicing and batching primitives (`_batches` generators across consolidation, themes, chunking).
- Connection and transaction lifecycle boilerplate across adapters (`neo4j.py`, `pgvector.py`, `postgres.py`).
- Caching policies and rate limiters (`CallLimiter` in domain vs LLM rate limiting).

## Spike Objectives & Definition of Done
1. Map out structural redundancies and candidate shared abstractions across bounded contexts.
2. Evaluate abstraction candidates against DDD bounded context boundaries and Layered Port & Adapter Isolation (ADR-0007).
3. Produce an empirical findings brief proposing high-leverage refactorings that preserve zero-loss functionality and maintain <500 line limits.
4. Verify all changes or proposal artifacts comply with ADR-0002 (<500 lines per file).
