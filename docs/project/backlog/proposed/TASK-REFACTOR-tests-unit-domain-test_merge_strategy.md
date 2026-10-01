---
id: REFACTOR-tests-unit-domain-test_merge_strategy
title: Refactor and Decompose Legacy File test_merge_strategy.py
status: Proposed
created: 2026-09-29
governing_adrs:
  - ADR-0002
target_bc: core
---

# TASK-REFACTOR-tests-unit-domain-test_merge_strategy: Refactor Legacy File test_merge_strategy.py

## Summary
The grandfathered debt file `tests/unit/domain/test_merge_strategy.py` contains 511 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (test_merge_strategy_claim.py, test_merge_strategy_prefer.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `tests/unit/domain/test_merge_strategy/` with submodules:
- `test_merge_strategy_claim.py`: claim, claim_strategy, test_the_claim_order_is_total, test_resolve_refuses_an_empty_claim_list, entity_with, TestUnion, TestMostRecentlyObserved, TestTheDeferredStrategies, TestClaimsFor, TestPolicyLookup, TestPolicyRefusals, TestClaimsForPaths
- `test_merge_strategy_prefer.py`: TestPreferCanonical, TestPreferMerged

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/tests/unit/domain/test_merge_strategy.py (511 lines):
  Submodule 'test_merge_strategy_claim.py' (~413 lines):
    - [function] claim (lines 41-56)
    - [function] claim_strategy (lines 228-247)
    - [function] test_the_claim_order_is_total (lines 251-260)
    - [function] test_resolve_refuses_an_empty_claim_list (lines 276-278)
    - [function] entity_with (lines 59-72)
    - [class] TestUnion (lines 97-159)
    - [class] TestMostRecentlyObserved (lines 162-225)
    - [class] TestTheDeferredStrategies (lines 281-312)
    - [class] TestClaimsFor (lines 315-352)
    - [class] TestPolicyLookup (lines 355-404)
    - [class] TestPolicyRefusals (lines 407-452)
    - [class] TestClaimsForPaths (lines 455-511)
  Submodule 'test_merge_strategy_prefer.py' (~31 lines):
    - [class] TestPreferCanonical (lines 75-94)
    - [class] TestPreferMerged (lines 263-273)
  Suggested barrel exports:
    from .test_merge_strategy_claim import claim, claim_strategy, test_the_claim_order_is_total, test_resolve_refuses_an_empty_claim_list, entity_with, TestUnion, TestMostRecentlyObserved, TestTheDeferredStrategies, TestClaimsFor, TestPolicyLookup, TestPolicyRefusals, TestClaimsForPaths
    from .test_merge_strategy_prefer import TestPreferCanonical, TestPreferMerged

    __all__ = ["claim", "claim_strategy", "test_the_claim_order_is_total", "test_resolve_refuses_an_empty_claim_list", "entity_with", "TestUnion", "TestMostRecentlyObserved", "TestTheDeferredStrategies", "TestClaimsFor", "TestPolicyLookup", "TestPolicyRefusals", "TestClaimsForPaths", "TestPreferCanonical", "TestPreferMerged"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
