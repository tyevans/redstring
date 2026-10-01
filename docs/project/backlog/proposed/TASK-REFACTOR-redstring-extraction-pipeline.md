---
id: REFACTOR-redstring-extraction-pipeline
title: Refactor and Decompose Legacy File pipeline.py
status: Proposed
created: 2026-09-29
governing_adrs:
  - ADR-0002
target_bc: core
---

# TASK-REFACTOR-redstring-extraction-pipeline: Refactor Legacy File pipeline.py

## Summary
The grandfathered debt file `src/redstring/extraction/pipeline.py` contains 687 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (pipeline_extraction.py, pipeline_result.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `src/redstring/extraction/pipeline/` with submodules:
- `pipeline_extraction.py`: PartialExtractionError, ExtractionPipeline, _batches
- `pipeline_result.py`: PipelineResult

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/src/redstring/extraction/pipeline.py (687 lines):
  Submodule 'pipeline_extraction.py' (~414 lines):
    - [class] PartialExtractionError (lines 193-209)
    - [class] ExtractionPipeline (lines 295-687)
    - [function] _batches (lines 289-292)
  Submodule 'pipeline_result.py' (~75 lines):
    - [class] PipelineResult (lines 212-286)
  Suggested barrel exports:
    from .pipeline_extraction import PartialExtractionError, ExtractionPipeline, _batches
    from .pipeline_result import PipelineResult

    __all__ = ["PartialExtractionError", "ExtractionPipeline", "_batches", "PipelineResult"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
