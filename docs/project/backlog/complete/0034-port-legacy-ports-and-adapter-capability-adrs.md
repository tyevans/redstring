---
id: '0034'
title: Port legacy ports and adapter capability ADRs to SpecOps
status: Complete
governing_adrs:
- ADR-0001
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0001
- US-0003
target_bc: architecture
signed_off_by: Ty Evans <ty@tyevans.net>
signed_off_at: '2026-10-04T21:29:40.922546+00:00'
---

# TASK-0034: Port legacy ports and adapter capability ADRs to SpecOps

## Summary
Port legacy architectural decisions from `docs/adr/` (`0008`, `0016`, `0026`, `0027`, `0028`) governing ports, adapter capabilities, and collaborator protocol boundaries into `docs/project/adrs/accepted/`.

## Context & Objectives
1. Governed by ADR-0001 (PMaC) and Custom Invariant 12 (Layered Port & Adapter Isolation).
2. Port active architecture decisions:
   - `0008-the-two-non-store-ports.md` -> `ADR-0108: The two non-store ports (LLM provider and cache)`
   - `0016-graph-store-is-five-capabilities.md` -> `ADR-0116: Graph store decomposed into capability protocols`
   - `0026-chunk-store-and-cache-are-capabilities-too.md` -> `ADR-0126: Chunk store and cache capability decomposition`
   - `0027-vector-store-is-three-capabilities-and-so-is-every-collaborator.md` -> `ADR-0127: Vector store capability protocols`
   - `0028-a-capability-declares-its-own-release.md` -> `ADR-0128: Adapter capabilities declare lifecycle release`
3. Update `docs/project/adrs/REGISTRY.md`.
4. Verify `spec-ops health` passes.

## Executable Acceptance Criteria (ADR-0006)

```gherkin
Scenario: Auditing ported capability ADRs
  Given the ported ADRs in "docs/project/adrs/accepted/"
  When "spec-ops health" executes
  Then zero duplicate ADR numbers are reported
  And "docs/project/adrs/REGISTRY.md" indexes all accepted ADRs.
```
