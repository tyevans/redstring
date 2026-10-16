---
id: '0047'
title: Fix Pydantic 2.14 type compatibility and integrate Dependabot PR 171
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
target_bc: extraction
persona: Jordan (The AI-Native Engineering Lead) & Alex (The Agentic Systems Architect)
---

# TASK-0047: Fix Pydantic 2.14 type compatibility and integrate Dependabot PR 171

## Summary
Harmonize Pydantic 2.14 typing compatibility in `src/redstring/extraction/constrained.py` so CI passes under strict typing, and integrate Dependabot PR #171 (Actions group bump).

## Definition of Done (Blackbox Frontdoor TDD)
1. `_literal` and `create_model` in `constrained.py` satisfy `mypy --strict` with Pydantic 2.14+.
2. CI actions updated and passing.
3. All new source files strictly under 500 lines.
