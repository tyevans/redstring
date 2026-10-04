---
id: '0032'
title: Port legacy storage and event log ADRs to SpecOps
status: Complete
governing_adrs:
- ADR-0001
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0001
- US-0002
target_bc: architecture
signed_off_by: Ty Evans <ty@tyevans.net>
signed_off_at: '2026-10-04T21:29:31.735407+00:00'
---

# TASK-0032: Port legacy storage and event log ADRs to SpecOps

## Summary
Port legacy architectural decision records from `docs/adr/` (`0001-event-log-schema-and-granularity.md`, `0002-two-store-ports.md`, `0004-consolidation-emits-events.md`) into `docs/project/adrs/accepted/` with standard YAML frontmatter and update `docs/project/adrs/REGISTRY.md`.

## Context & Objectives
1. Governed by ADR-0001 (Specification as Code - PMaC) and user mandate to port legacy ADRs into SpecOps structure.
2. Port active architecture decisions governing event sourcing and store boundaries:
   - `0001-event-log-schema-and-granularity.md` -> `ADR-0101: Event log schema and granularity`
   - `0002-two-store-ports.md` -> `ADR-0102: Two store ports (graph and vector separation)`
   - `0004-consolidation-emits-events.md` -> `ADR-0104: Consolidation emits domain events`
3. Update `docs/project/adrs/REGISTRY.md` and link from governing PRDs and User Stories.
4. Verify `spec-ops health` reports 0 numbering collisions and valid metadata.

## Executable Acceptance Criteria (ADR-0006)

```gherkin
Scenario: Auditing ported storage and event log ADRs
  Given the ported ADRs in "docs/project/adrs/accepted/"
  When "spec-ops health" executes
  Then zero duplicate ADR numbers are reported
  And "docs/project/adrs/REGISTRY.md" indexes all accepted ADRs.
```
