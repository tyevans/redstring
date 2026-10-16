---
id: '0043'
title: Bump eventsource-py to 0.15.0 and document floor rationale
status: Complete
governing_adrs:
- ADR-0001
- ADR-0002
- ADR-0003
- ADR-0004
governing_prds:
- PRD-0001
governing_stories:
- US-0001
target_bc: core
allows_dependencies: true
persona: Jordan (The AI-Native Engineering Lead) & Alex (The Agentic Systems Architect)
---

# TASK-0043: Bump eventsource-py to 0.15.0 and document floor rationale

## Summary
Bump `eventsource-py` to `>=0.15.0,<0.16` in `pyproject.toml`, document the 0.15.0 adoption rationale and invariants, and verify integration tests.

## Problem Statement & Context
Task TASK-0043 implements the dependency bump for `eventsource-py 0.15.0` in bounded context core.

## Definition of Done (Blackbox Frontdoor TDD)
1. Public interfaces or standard domain contracts implemented.
2. Verified via automated blackbox tests without private backdoor manipulation.
3. All new source files strictly under 500 lines.
