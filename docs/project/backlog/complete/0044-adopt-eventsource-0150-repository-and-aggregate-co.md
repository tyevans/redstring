---
id: '0044'
title: Adopt eventsource 0.15.0 repository and aggregate convenience methods
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
target_bc: aggregates
persona: Jordan (The AI-Native Engineering Lead) & Alex (The Agentic Systems Architect)
---

# TASK-0044: Adopt eventsource 0.15.0 repository and aggregate convenience methods

## Summary
Adopt `eventsource-py 0.15.0` repository convenience methods (`exists()`, `get_or_none()`, `get_or_raise()`, `load_many()`, `save_all()`, `get_version()`) in aggregate repositories and unit tests.

## Definition of Done (Blackbox Frontdoor TDD)
1. Verify `document_repository` and `consolidation_repository` support the new 0.15.0 methods.
2. Verified via automated blackbox tests without private backdoor manipulation.
3. All new source files strictly under 500 lines.
