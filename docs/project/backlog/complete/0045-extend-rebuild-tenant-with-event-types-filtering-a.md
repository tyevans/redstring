---
id: '0045'
title: Extend rebuild_tenant with event_types filtering and iterable subscribers
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
target_bc: projections
persona: Jordan (The AI-Native Engineering Lead) & Alex (The Agentic Systems Architect)
---

# TASK-0045: Extend rebuild_tenant with event_types filtering and iterable subscribers

## Summary
Extend `rebuild_tenant` in `src/redstring/projections/rebuild.py` to forward `event_types` filtering to `eventsource`'s 0.15.0 `replay()` and accept any iterable of projection subscribers.

## Definition of Done (Blackbox Frontdoor TDD)
1. `rebuild_tenant` accepts `event_types` filter parameter and forwards to `replay()`.
2. Verified via unit tests in `tests/unit/projections/test_tenant_rebuild.py`.
3. All new source files strictly under 500 lines.
