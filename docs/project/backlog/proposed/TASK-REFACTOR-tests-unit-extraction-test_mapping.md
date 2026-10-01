---
id: REFACTOR-tests-unit-extraction-test_mapping
title: Refactor and Decompose Legacy File test_mapping.py
status: Proposed
created: 2026-09-29
governing_adrs:
  - ADR-0002
target_bc: core
---

# TASK-REFACTOR-tests-unit-extraction-test_mapping: Refactor Legacy File test_mapping.py

## Summary
The grandfathered debt file `tests/unit/extraction/test_mapping.py` contains 842 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (test_mapping_not.py, test_mapping_one.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `tests/unit/extraction/test_mapping/` with submodules:
- `test_mapping_not.py`: TestEmptyIsNotAnError, test_a_generated_id_is_stable_across_runs_and_not_merely_within_one, test_the_pinned_id_is_not_an_accident_of_this_tenant, mapped, entity, link, TestEntities, TestIdentity, TestRelationships, TestTenantSafety, TestProvenance, TestTheResultTypeItself, TestProperties, TestBlockingKeys
- `test_mapping_one.py`: TestDuplicatesWithinOneCall

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/tests/unit/extraction/test_mapping.py (842 lines):
  Submodule 'test_mapping_not.py' (~701 lines):
    - [class] TestEmptyIsNotAnError (lines 636-641)
    - [function] test_a_generated_id_is_stable_across_runs_and_not_merely_within_one (lines 718-731)
    - [function] test_the_pinned_id_is_not_an_accident_of_this_tenant (lines 734-738)
    - [function] mapped (lines 41-49)
    - [function] entity (lines 52-53)
    - [function] link (lines 56-59)
    - [class] TestEntities (lines 62-168)
    - [class] TestIdentity (lines 171-244)
    - [class] TestRelationships (lines 247-486)
    - [class] TestTenantSafety (lines 564-580)
    - [class] TestProvenance (lines 583-615)
    - [class] TestTheResultTypeItself (lines 618-633)
    - [class] TestProperties (lines 644-715)
    - [class] TestBlockingKeys (lines 741-842)
  Submodule 'test_mapping_one.py' (~73 lines):
    - [class] TestDuplicatesWithinOneCall (lines 489-561)
  Suggested barrel exports:
    from .test_mapping_not import TestEmptyIsNotAnError, test_a_generated_id_is_stable_across_runs_and_not_merely_within_one, test_the_pinned_id_is_not_an_accident_of_this_tenant, mapped, entity, link, TestEntities, TestIdentity, TestRelationships, TestTenantSafety, TestProvenance, TestTheResultTypeItself, TestProperties, TestBlockingKeys
    from .test_mapping_one import TestDuplicatesWithinOneCall

    __all__ = ["TestEmptyIsNotAnError", "test_a_generated_id_is_stable_across_runs_and_not_merely_within_one", "test_the_pinned_id_is_not_an_accident_of_this_tenant", "mapped", "entity", "link", "TestEntities", "TestIdentity", "TestRelationships", "TestTenantSafety", "TestProvenance", "TestTheResultTypeItself", "TestProperties", "TestBlockingKeys", "TestDuplicatesWithinOneCall"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
