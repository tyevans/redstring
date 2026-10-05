---
id: REFACTOR-redstring-extraction-domains-models
title: Refactor and Decompose Legacy File models.py
status: Refined
governing_adrs:
- ADR-0002
governing_prds:
- PRD-0001
governing_stories:
- US-0001
target_bc: core
---

# TASK-REFACTOR-redstring-extraction-domains-models: Refactor Legacy File models.py

## Summary
The grandfathered debt file `src/redstring/extraction/domains/models.py` contains 699 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (models_schema.py, models_type.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `src/redstring/extraction/domains/models/` with submodules:
- `models_schema.py`: PropertySchema, EntityTypeSchema, RelationshipTypeSchema, DomainSchema, normalize_identifier, ConfidenceThresholds, DomainSummary, ClassificationResult
- `models_type.py`: normalize_type_id

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/src/redstring/extraction/domains/models.py (699 lines):
  Submodule 'models_schema.py' (~613 lines):
    - [class] PropertySchema (lines 88-144)
    - [class] EntityTypeSchema (lines 147-241)
    - [class] RelationshipTypeSchema (lines 244-348)
    - [class] DomainSchema (lines 382-597)
    - [function] normalize_identifier (lines 73-85)
    - [class] ConfidenceThresholds (lines 351-379)
    - [class] DomainSummary (lines 600-648)
    - [class] ClassificationResult (lines 651-699)
  Submodule 'models_type.py' (~23 lines):
    - [function] normalize_type_id (lines 48-70)
  Suggested barrel exports:
    from .models_schema import PropertySchema, EntityTypeSchema, RelationshipTypeSchema, DomainSchema, normalize_identifier, ConfidenceThresholds, DomainSummary, ClassificationResult
    from .models_type import normalize_type_id

    __all__ = ["PropertySchema", "EntityTypeSchema", "RelationshipTypeSchema", "DomainSchema", "normalize_identifier", "ConfidenceThresholds", "DomainSummary", "ClassificationResult", "normalize_type_id"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
