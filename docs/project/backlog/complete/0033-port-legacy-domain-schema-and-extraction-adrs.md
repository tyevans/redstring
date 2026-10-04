---
id: '0033'
title: Port legacy domain schema and extraction ADRs to SpecOps
status: Complete
governing_adrs:
- ADR-0001
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0001
target_bc: architecture
signed_off_by: Ty Evans <ty@tyevans.net>
signed_off_at: '2026-10-04T21:29:34.812023+00:00'
---

# TASK-0033: Port legacy domain schema and extraction ADRs to SpecOps

## Summary
Port legacy architectural decisions from `docs/adr/` (`0009`, `0011`, `0030`, `0031`) governing domain schemas, alias resolution, and non-thinking extraction into `docs/project/adrs/accepted/`.

## Context & Objectives
1. Governed by ADR-0001 (PMaC) and Custom Invariant 11 (Sourcing vs Storage).
2. Port active architecture decisions:
   - `0009-the-extraction-fold-resolves-through-aliases.md` -> `ADR-0109: Extraction fold resolves through aliases`
   - `0011-domain-schemas-prompt-but-do-not-constrain.md` -> `ADR-0111: Domain schemas prompt but do not constrain`
   - `0030-a-domain-schema-may-constrain-when-asked.md` -> `ADR-0130: Domain schema may constrain when asked`
   - `0031-extraction-does-not-think.md` -> `ADR-0131: Extraction does not think`
3. Update `docs/project/adrs/REGISTRY.md`.
4. Verify `spec-ops health` passes.

## Executable Acceptance Criteria (ADR-0006)

```gherkin
Scenario: Auditing ported domain schema and extraction ADRs
  Given the ported ADRs in "docs/project/adrs/accepted/"
  When "spec-ops health" executes
  Then zero duplicate ADR numbers are reported
  And "docs/project/adrs/REGISTRY.md" indexes all accepted ADRs.
```
