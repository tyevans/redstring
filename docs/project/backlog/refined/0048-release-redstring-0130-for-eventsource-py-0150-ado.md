---
id: 0048
title: Release redstring 0.13.0 for eventsource-py 0.15.0 adoption
status: Refined
governing_adrs:
- ADR-0001
- ADR-0002
governing_prds:
- PRD-0001
governing_stories:
- US-0001
target_bc: core
---

# TASK-0048: Release redstring 0.13.0 for eventsource-py 0.15.0 adoption

## Summary
Release `redstring 0.13.0` capturing the complete adoption of `eventsource-py 0.15.0`, including repository convenience methods, tenant projection rebuild filtering, event store container dunders, Pydantic 2.14 compatibility, and CI workflow upgrades.

## Problem Statement & Context
Tasks TASK-0042 through TASK-0047 implemented and tested the adoption of `eventsource-py 0.15.0`. Preparing and cutting the 0.13.0 release requires version synchronization across `pyproject.toml` and `src/redstring/__init__.py`, documenting user-facing changes and bug fixes in `CHANGELOG.md`, verifying version and release tests, and publishing the release via tag `v0.13.0`.

## Definition of Done (Blackbox Frontdoor TDD)
1. Synchronize version `0.13.0` in `pyproject.toml` and `src/redstring/__init__.py`.
2. Add section `## [0.13.0] - 2026-10-15` in `CHANGELOG.md` detailing new features, changes, and fixes.
3. Pass `tests/unit/test_version_is_declared_once.py` and full test suite (`uv run pytest`).
4. Validate codebase health (`uv run spec-ops health`).
5. PR merged into `main` and release tag `v0.13.0` pushed to trigger `.github/workflows/release.yml`.
