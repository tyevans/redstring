---
id: '0021'
title: Audit and port legacy ADRs (0001-0047) to SpecOps Registry
status: Proposed
governing_adrs:
- ADR-0001
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0005
target_bc: core
---

# TASK-0021: Audit and port legacy ADRs (0001-0047) to SpecOps Registry

## Summary
Audit the 47 legacy Architecture Decision Records in `docs/adr/`, triage which remain active architectural decisions vs superseded or obsolete, format active records with SpecOps YAML frontmatter, integrate them into `docs/project/adrs/` and `REGISTRY.md`, and update `mkdocs.yml` navigation.

## Context & Objectives
1. `docs/adr/` holds 47 pre-SpecOps architectural decision records that document the core technical contracts of `redstring` (event log schema, store ports, temporal inference, preference order, etc.).
2. SpecOps manages architectural decisions under `docs/project/adrs/` with YAML frontmatter linked to PRDs, Personas, and Stories.
3. Triage the 47 legacy ADRs:
   - Identify active decisions that govern the engine today.
   - Reconcile numbering or establish a clean namespace in `docs/project/adrs/` (avoiding collision with SpecOps framework meta-ADRs ADR-0001 through ADR-0010).
   - Format with SpecOps frontmatter (`id`, `title`, `status`, `target_bc`, etc.).
   - Update `docs/project/adrs/REGISTRY.md`.
   - Update `mkdocs.yml` so that MkDocs builds cleanly with `mkdocs --strict` and zero broken links.
