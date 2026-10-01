---
id: REFACTOR-tests-unit-test_composition
title: Refactor and Decompose Legacy File test_composition.py
status: Proposed
created: 2026-09-29
governing_adrs:
  - ADR-0002
target_bc: core
---

# TASK-REFACTOR-tests-unit-test_composition: Refactor Legacy File test_composition.py

## Summary
The grandfathered debt file `tests/unit/test_composition.py` contains 691 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (test_composition_the.py, test_composition_a.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `tests/unit/test_composition/` with submodules:
- `test_composition_the.py`: TestWhereTheObservationInstantComesFrom, TestTheModelIsAskedOnce, TestTheClassifierCallSharesTheCeiling, TestTheSignatureAndTheReportAreThemselvesContracts, TestWhatLandsInTheStore, TestTheCorpusIsOptionalAndSeparate, CountingProvider, document, TestWhichPromptIsSent
- `test_composition_a.py`: TestAClassifierThatGaveUpIsDistinguishableFromOneThatChose, TestAPartialExtractionIsRefusedBeforeAnythingIsWritten, TestConstrainingToADomainsVocabulary

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/tests/unit/test_composition.py (691 lines):
  Submodule 'test_composition_the.py' (~403 lines):
    - [class] TestWhereTheObservationInstantComesFrom (lines 81-146)
    - [class] TestTheModelIsAskedOnce (lines 149-195)
    - [class] TestTheClassifierCallSharesTheCeiling (lines 198-278)
    - [class] TestTheSignatureAndTheReportAreThemselvesContracts (lines 281-309)
    - [class] TestWhatLandsInTheStore (lines 433-468)
    - [class] TestTheCorpusIsOptionalAndSeparate (lines 514-587)
    - [class] CountingProvider (lines 52-74)
    - [function] document (lines 77-78)
    - [class] TestWhichPromptIsSent (lines 312-356)
  Submodule 'test_composition_a.py' (~215 lines):
    - [class] TestAClassifierThatGaveUpIsDistinguishableFromOneThatChose (lines 359-430)
    - [class] TestAPartialExtractionIsRefusedBeforeAnythingIsWritten (lines 471-511)
    - [class] TestConstrainingToADomainsVocabulary (lines 590-691)
  Suggested barrel exports:
    from .test_composition_the import TestWhereTheObservationInstantComesFrom, TestTheModelIsAskedOnce, TestTheClassifierCallSharesTheCeiling, TestTheSignatureAndTheReportAreThemselvesContracts, TestWhatLandsInTheStore, TestTheCorpusIsOptionalAndSeparate, CountingProvider, document, TestWhichPromptIsSent
    from .test_composition_a import TestAClassifierThatGaveUpIsDistinguishableFromOneThatChose, TestAPartialExtractionIsRefusedBeforeAnythingIsWritten, TestConstrainingToADomainsVocabulary

    __all__ = ["TestWhereTheObservationInstantComesFrom", "TestTheModelIsAskedOnce", "TestTheClassifierCallSharesTheCeiling", "TestTheSignatureAndTheReportAreThemselvesContracts", "TestWhatLandsInTheStore", "TestTheCorpusIsOptionalAndSeparate", "CountingProvider", "document", "TestWhichPromptIsSent", "TestAClassifierThatGaveUpIsDistinguishableFromOneThatChose", "TestAPartialExtractionIsRefusedBeforeAnythingIsWritten", "TestConstrainingToADomainsVocabulary"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
