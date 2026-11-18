---
id: '0069'
title: Cap the blocking read per key in consolidation and lexical retrieval
status: Complete
governing_adrs:
- ADR-0152
governing_prds:
- PRD-0013
governing_stories:
- US-0030
target_bc: core
---

# TASK-0069: Cap the blocking read per key in consolidation and lexical retrieval

## Summary
Give `find_by_blocking_keys` a per-key `limit_per_key`, applied in the store by both adapters, and pass `MAX_BLOCK_READ` from every library caller.

## Problem Statement & Context
Was BACKLOG B167, recorded in ADR-0150's Consequences.

- TASK-0065 capped the candidates scored per subject, not the read before the cut. The `t:<type>` block holds every entity of that type, so a common type made each subject read a block the size of the type.
- Lexical retrieval and its diagnostic read the blocks of caller text with no limit.
- Removing the type key from the default strategies was rejected: it lowers recall for every tenant. ADR-0152 records the choice.

## Definition of Done (Blackbox Frontdoor TDD)
1. The compliance suite (`testing/graph_store_blocking_limits.py`) tests lowest-id selection with ids whose hash order differs from their sort order, the per-key scope, zero, a cap above the block, another tenant's lower ids, and a negative value; it passes on the in-memory adapter and on Neo4j.
2. The Neo4j statement puts `LIMIT` inside a per-key subquery (offline test).
3. `CandidateFinder`, `Retriever` and `explain_entity_retrieval` each pass `MAX_BLOCK_READ`; each test fails when that caller's cap is removed.
4. `MAX_CANDIDATES <= MAX_BLOCK_READ` is asserted.
5. Delivered in PR #202 (`f947ac4`, after the `56cf144` codec move).
