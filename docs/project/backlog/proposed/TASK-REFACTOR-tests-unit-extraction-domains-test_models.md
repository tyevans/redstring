---
id: REFACTOR-tests-unit-extraction-domains-test_models
title: Refactor and Decompose Legacy File test_models.py
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

# TASK-REFACTOR-tests-unit-extraction-domains-test_models: Refactor Legacy File test_models.py

## Summary
The grandfathered debt file `tests/unit/extraction/domains/test_models.py` contains 996 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (test_models_schema.py, test_models_domain.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `tests/unit/extraction/domains/test_models/` with submodules:
- `test_models_schema.py`: TestPropertySchema, TestEntityTypeSchema, TestRelationshipTypeSchema, TestDomainSchema, TestConfidenceThresholds, TestClassificationResult, TestModelIntegration
- `test_models_domain.py`: TestDomainSummary

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/tests/unit/extraction/domains/test_models.py (996 lines):
  Submodule 'test_models_schema.py' (~876 lines):
    - [class] TestPropertySchema (lines 30-122)
    - [class] TestEntityTypeSchema (lines 130-242)
    - [class] TestRelationshipTypeSchema (lines 250-394)
    - [class] TestDomainSchema (lines 454-767)
    - [class] TestConfidenceThresholds (lines 402-446)
    - [class] TestClassificationResult (lines 824-895)
    - [class] TestModelIntegration (lines 903-996)
  Submodule 'test_models_domain.py' (~42 lines):
    - [class] TestDomainSummary (lines 775-816)
  Suggested barrel exports:
    from .test_models_schema import TestPropertySchema, TestEntityTypeSchema, TestRelationshipTypeSchema, TestDomainSchema, TestConfidenceThresholds, TestClassificationResult, TestModelIntegration
    from .test_models_domain import TestDomainSummary

    __all__ = ["TestPropertySchema", "TestEntityTypeSchema", "TestRelationshipTypeSchema", "TestDomainSchema", "TestConfidenceThresholds", "TestClassificationResult", "TestModelIntegration", "TestDomainSummary"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
