---
id: '0033'
title: Port legacy domain schema and extraction ADRs to SpecOps
status: Refined
governing_adrs:
- ADR-0001
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0001
target_bc: architecture
mutation_scope: []
---

# TASK-0033: Port legacy domain schema and extraction ADRs to SpecOps

## Summary
Port legacy architectural decisions from `docs/adr/` (`0009`, `0011`, `0030`, `0031`) governing domain schemas, alias resolution, and non-thinking extraction into `docs/project/adrs/accepted/`.

## Context & Objectives
1. Governed by ADR-0001 (PMaC) and Custom Invariant 11 (Sourcing vs Storage).
2. Port active architecture decisions:
   - `0009-the-extraction-fold-resolves-through-aliases.md` -> `ADR-0017: Extraction fold resolves through aliases`
   - `0011-domain-schemas-prompt-but-do-not-constrain.md` -> `ADR-0018: Domain schemas prompt but do not constrain`
   - `0030-a-domain-schema-may-constrain-when-asked.md` -> `ADR-0019: Domain schema may constrain when asked`
   - `0031-extraction-does-not-think.md` -> `ADR-0020: Extraction does not think`
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
