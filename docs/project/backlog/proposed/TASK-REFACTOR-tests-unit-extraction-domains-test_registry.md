---
id: REFACTOR-tests-unit-extraction-domains-test_registry
title: Refactor and Decompose Legacy File test_registry.py
status: Proposed
created: 2026-09-29
governing_adrs:
  - ADR-0002
governing_prds:
  - PRD-0001
governing_stories:
  - US-0001
target_bc: core
---

# TASK-REFACTOR-tests-unit-extraction-domains-test_registry: Refactor Legacy File test_registry.py

## Summary
The grandfathered debt file `tests/unit/extraction/domains/test_registry.py` contains 618 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (test_registry_domain.py, test_registry_convenience.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `tests/unit/extraction/domains/test_registry/` with submodules:
- `test_registry_domain.py`: TestDomainSchemaRegistrySingleton, TestDomainSchemaRegistryLoading, TestDomainSchemaRegistryAccessors, TestDomainSchemaRegistryLazyLoading, TestDomainSchemaRegistryHotReload, TestDomainSchemaRegistryDefaultSchema, TestDomainSchemaRegistryEntityTypeSearch, TestRegistryWithRealSchemas
- `test_registry_convenience.py`: TestConvenienceFunctions

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/tests/unit/extraction/domains/test_registry.py (618 lines):
  Submodule 'test_registry_domain.py' (~494 lines):
    - [class] TestDomainSchemaRegistrySingleton (lines 63-117)
    - [class] TestDomainSchemaRegistryLoading (lines 120-211)
    - [class] TestDomainSchemaRegistryAccessors (lines 214-322)
    - [class] TestDomainSchemaRegistryLazyLoading (lines 325-370)
    - [class] TestDomainSchemaRegistryHotReload (lines 373-423)
    - [class] TestDomainSchemaRegistryDefaultSchema (lines 426-487)
    - [class] TestDomainSchemaRegistryEntityTypeSearch (lines 490-522)
    - [class] TestRegistryWithRealSchemas (lines 573-618)
  Submodule 'test_registry_convenience.py' (~46 lines):
    - [class] TestConvenienceFunctions (lines 525-570)
  Suggested barrel exports:
    from .test_registry_domain import TestDomainSchemaRegistrySingleton, TestDomainSchemaRegistryLoading, TestDomainSchemaRegistryAccessors, TestDomainSchemaRegistryLazyLoading, TestDomainSchemaRegistryHotReload, TestDomainSchemaRegistryDefaultSchema, TestDomainSchemaRegistryEntityTypeSearch, TestRegistryWithRealSchemas
    from .test_registry_convenience import TestConvenienceFunctions

    __all__ = ["TestDomainSchemaRegistrySingleton", "TestDomainSchemaRegistryLoading", "TestDomainSchemaRegistryAccessors", "TestDomainSchemaRegistryLazyLoading", "TestDomainSchemaRegistryHotReload", "TestDomainSchemaRegistryDefaultSchema", "TestDomainSchemaRegistryEntityTypeSearch", "TestRegistryWithRealSchemas", "TestConvenienceFunctions"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
