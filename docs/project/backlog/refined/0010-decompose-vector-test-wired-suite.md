---
id: '0010'
title: Decompose vector adapter test wired suite (<500 lines)
status: Refined
governing_adrs:
- ADR-0002
- ADR-0003
governing_prds:
- PRD-0001
governing_stories:
- US-0003
target_bc: ports
mutation_scope: '[''src/redstring/vector/adapters/pgvector.py'']'
---

# TASK-0010: Decompose vector adapter test wired suite (<500 lines)

## Summary
`tests/unit/vector/test_pgvector_adapter_is_wired.py` has grown to 491 lines, within 9 lines of the hard <500 line invariant limit (ADR-0002). This task decomposes the wired adapter contract tests into cohesive test suites.

## Problem Statement & Context
1. `tests/unit/vector/test_pgvector_adapter_is_wired.py` tests argument validation, dimension verification, SQL generation, connection lifecycle, and error mapping all in one module.
2. Near the 500-line ceiling, the file triggers proactive refactoring warnings in `spec-ops health`.
3. Decomposing by concern (e.g. `test_pgvector_wiring_validation.py` and `test_pgvector_wiring_lifecycle.py`) maintains clear modularity.

## Definition of Done (Blackbox Frontdoor TDD)
1. Split into focused test suites each under 300 lines.
2. 100% of contract and wiring checks preserved without mock backdoors.
3. Zero health check warnings on target files.
4. Total test count preserved.
