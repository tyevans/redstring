---
id: '0066'
title: Validate SQL identifiers at every interpolation site in Postgres adapters
status: Complete
governing_adrs:
- ADR-0123
governing_prds:
- PRD-0013
governing_stories:
- US-0030
target_bc: chunks
---

# TASK-0066: Validate SQL identifiers at every interpolation site in Postgres adapters

## Summary
Validate the table name inside every Postgres SQL helper that interpolates it, and keep derived identifiers inside the Postgres 63-byte limit.

## Problem Statement & Context
Found by the 2026-11-17 security sweep (query-injection review). No exploitable path exists today.

- `src/redstring/chunks/adapters/postgres/postgres_search.py:59-70,135-137` and `postgres_sql.py:153-374` take `table` and put it into SQL text. Only the store constructor validates it (`postgres_chunk.py:93`), so a direct caller of a helper bypasses the check.
- `src/redstring/vector/adapters/pgvector.py:89` and the chunk identifier regex accept 63-character names, and the adapters then add suffixes such as `_tenant_source_idx`. Postgres truncates identifiers above 63 bytes, so two derived names can collide and `CREATE INDEX IF NOT EXISTS` skips one with no error.
- `backfill_lexical` reads every tenant's rows. It keeps each row's tenant, but its docstring must state that it is an operator-only maintenance call.

## Definition of Done (Blackbox Frontdoor TDD)
1. Each module-level helper that formats `table` into SQL rejects an invalid identifier (test calls the helper directly with `"x; DROP TABLE y"`).
2. The base table name is limited so that the longest derived identifier is at most 63 bytes, on both adapters (test at the limit and at the limit + 1).
3. `backfill_lexical` documents that it spans all tenants and is for operators.
