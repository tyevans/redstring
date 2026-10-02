---
id: '0006'
title: Refactor caller-owned resource tests to verify connection and pool identity
status: Complete
governing_adrs:
- ADR-0003
governing_prds:
- PRD-0001
governing_stories:
- US-0003
target_bc: ports
mutation_scope: '[''src/redstring/testing/lifetime.py'']'
---

# TASK-0006: Refactor caller-owned resource tests to verify connection and pool identity

## Summary
Integration tests for caller-owned resources (`test_pgvector_store.py`, `test_neo4j_store.py`, `test_postgres_store.py`) test that an adapter leaves an external pool or driver open by executing a subsequent query (BACKLOG B119). However, executing a query can succeed even on disposed resources (as proved in eventsource-py 0.13.0). This task replaces query execution checks with explicit pool/connection identity and closedness assertions.

## Problem Statement & Context
1. An adapter handed an external connection pool must not close it on `adapter.close()`.
2. Proving non-closure by running `SELECT 1` or `RETURN 1` is an unverified assertion because certain pool implementations auto-reconnect or mint ephemeral connections upon query.
3. Checking `pool.is_closed()` or checking resource identity directly verifies the contract truthfully.

## Definition of Done (Blackbox Frontdoor TDD)
1. Tests in `test_pgvector_store.py`, `test_neo4j_store.py`, and `test_postgres_store.py` assert resource identity and state directly.
2. Negative mutant verified: intentionally closing the caller resource in the adapter causes the test suite to fail immediately.
3. Lifetime contracts in `src/redstring/testing/lifetime.py` unified and verified.
4. All source files strictly <500 lines.
