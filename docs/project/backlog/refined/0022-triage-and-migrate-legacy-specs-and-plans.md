---
id: '0022'
title: Triage and migrate legacy specs and plans from superpowers/ and plans/
status: Refined
governing_adrs:
- ADR-0001
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0005
target_bc: core
mutation_scope: []
---

# TASK-0022: Triage and migrate legacy specs and plans from superpowers/ and plans/

## Summary
Audit legacy planning and design specifications in `docs/superpowers/plans/`, `docs/superpowers/specs/`, and `docs/plans/`. Extract enduring architectural and product requirements into SpecOps PRDs and User Stories, and archive historical execution records into `docs/history/`.

## Context & Objectives
1. Before adopting SpecOps, requirements and implementation blueprints were authored under `docs/superpowers/plans/`, `docs/superpowers/specs/`, and `docs/plans/`.
2. These documents contain domain rationale (entity retrieval design, lexical channel design, chunk store design, property provenance, ingestion benchmarks, etc.) that should be preserved.
3. Triage each document:
   - Extract living requirements, acceptance criteria, and domain models into `docs/project/product/` (PRDs) and `docs/project/user_stories/`.
   - Move completed historical migration logs to `docs/history/`.
   - Remove obsolete pre-SpecOps ad-hoc directories once enduring content is captured.
