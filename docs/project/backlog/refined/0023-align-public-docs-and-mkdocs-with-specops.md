---
id: '0023'
title: Align public documentation and MkDocs navigation with SpecOps PMaC
status: Refined
governing_adrs:
- ADR-0001
- ADR-0004
governing_prds:
- PRD-0001
governing_stories:
- US-0005
target_bc: documentation
mutation_scope: []
---

# TASK-0023: Align public documentation and MkDocs navigation with SpecOps PMaC

## Summary
Audit root-level documentation (`contributing.md`, `getting-started.md`, `installation.md`, `index.md`, `operating-manual.md`) and `mkdocs.yml` navigation to fully align with SpecOps standards, Diataxis principles, and modern development workflows.

## Context & Objectives
1. Modern development on `redstring` is governed by SpecOps PMaC (`uv run spec-ops ...`, worktree isolation, Definition of Ready/Done, executable BDD tests).
2. Existing public docs still reference pre-SpecOps conventions, manual commit workflows, and legacy directory layouts.
3. Review and update:
   - `docs/contributing.md`: Update contributor guidance to reflect SpecOps PMaC, worktree conventions, and preflight health checks.
   - `docs/operating-manual.md`: Keep synchronized with `AGENTS.md` and SpecOps invariants.
   - `mkdocs.yml`: Ensure navigation structure cleanly integrates Diataxis docs and project specifications without broken anchors or missing pages under `mkdocs --strict`.
