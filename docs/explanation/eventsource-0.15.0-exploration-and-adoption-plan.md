# Eventsource-py 0.15.0 Exploration & Adoption Plan

This document records the architectural exploration and adoption plan for **`eventsource-py` 0.15.0** (**TASK-0042**), assessing upstream modifications, confirming zero regression across `redstring`'s test surface, and specifying vertical implementation tasks for full feature adoption.

---

## 1. Executive Summary

`eventsource-py` 0.15.0 is an ergonomics, resilience, and modularity release for the event-sourcing runtime that powers `redstring`'s write and projection models.

Key upstream enhancements include:
- Uniform Pythonic container protocols (`len()`, `__contains__`, `__iter__`, `__bool__`) across event stores, buses, and registries.
- High-level aggregate and repository convenience methods (`exists()`, `get_or_none()`, `get_or_raise()`, `load_many()`, `save_all()`, `get_version()`, and `execute()` with variadic commands).
- Flexible projection replay (`replay()` accepting single or iterable projections, plus selective `event_types` filtering and rich `ReplayReport` metrics).
- Transactional outbox support for SQLite, delta-compressed stored events, and non-blocking retry backoffs.
- Modular architectural decomposition of `eventsource-py` into source modules strictly under 160 lines.

### Empirical Verification
- **Baseline Verification**: Tested `redstring` unit test suite directly against installed `eventsource-py 0.15.0`: **3,789 tests passed** (0 failures, 2 skipped, 373 integration/live tests deselected by default).
- **Public API & Contract Gate**: All static typing (`mypy --strict`), linting (`ruff`), and import linter boundaries passed with 0 violations.
- **Backwards Compatibility**: Zero breaking changes impacted `redstring`'s imported symbols or runtime behavior.

---

## 2. Upstream Changes Impact Analysis

| Component in `eventsource-py` | Upstream 0.15.0 Change | `redstring` Surface Affected | Impact & Action Required |
|---|---|---|---|
| `pyproject.toml` | Release of 0.15.0 | Dependency declarations | Update floor and ceiling: `eventsource-py>=0.15.0,<0.16`. Update pyproject narrative block. |
| `AggregateRepository` & `TenantAwareRepository` | `exists()`, `get_or_none()`, `get_or_raise()`, `load_many()`, `save_all()`, `get_version()` | `redstring.aggregates.repositories` | Expose and test these methods in unit tests and consolidation services. |
| `replay()` & `StoreProjection` | `event_types` filtering, `Iterable[EventSubscriber]` support | `redstring.projections.rebuild.rebuild_tenant` | Expose `event_types` parameter in `rebuild_tenant()` for selective rebuilds. |
| `InMemoryEventStore` | `len()`, `in`, `bool`, `clear()`, `stream_exists()`, single-event `append()` | Unit test fixtures in `tests/unit/` | Streamline test fixtures and assertions using container protocols. |
| Tooling & Dependabot | GitHub Actions updates (PR #171) & Pydantic 2.14 compatibility | `src/redstring/extraction/constrained.py` & CI | Fix Pydantic 2.14 `create_model` typing annotations and merge Dependabot PR. |

---

## 3. Backlog Task Breakdown

To execute adoption with maximum concurrency and clean vertical slices (<500 lines per file, INVEST criteria):

1. **TASK-0043 (Core)**: Bump `eventsource-py` to `>=0.15.0,<0.16` in `pyproject.toml`, update dependency rationale, and verify lockfile.
2. **TASK-0044 (Aggregates)**: Adopt `eventsource-py 0.15.0` repository convenience methods (`exists`, `get_or_none`, `get_or_raise`, `load_many`, `save_all`, `get_version`) in aggregate repositories and unit tests.
3. **TASK-0045 (Projections)**: Extend `rebuild_tenant` in `src/redstring/projections/rebuild.py` to support `event_types` filtering and iterable subscribers using 0.15.0 `replay()`.
4. **TASK-0046 (Testing)**: Leverage `InMemoryEventStore` container protocols (`len()`, `__contains__`, `stream_exists()`, single-event `append()`) across unit test suites.
5. **TASK-0047 (Tooling & Dependabot)**: Harmonize Pydantic 2.14 `create_model` typing in `src/redstring/extraction/constrained.py` and integrate Dependabot PR #171 (Actions group bump).
6. **TASK-0048 (Release)**: Final release task cutting `redstring` 0.13.0, updating CHANGELOG.md, version metadata, and tagging release.
