---
id: '0030'
title: Standardize ExtractionRetryPolicy naming and consolidation with LLM resilience
status: Refined
governing_adrs:
- ADR-0001
- ADR-0007
- ADR-0008
- ADR-0013
governing_prds:
- PRD-0001
governing_stories:
- US-0001
target_bc: extraction
mutation_scope:
- src/redstring/extraction/pipeline.py
---

# TASK-0030: Standardize ExtractionRetryPolicy naming and consolidation with LLM resilience

## Summary
The empirical spike in TASK-0019 (`docs/explanation/cross-bc-abstractions-and-consolidation-findings.md`) identified that `ExtractionRetryPolicy` in `src/redstring/extraction/pipeline.py` specifically configures retry behaviour for LLM provider calls, while `redstring.llm.retry` provides complementary resilience decorators. Standardize the naming to `LlmRetryPolicy` (or provide clean type aliases) and harmonize configuration semantics across the BC boundary.

## Context & Objectives
1. Governed by ADR-0008 (The two non-store ports) and ADR-0013 (Resilience behind the cache port).
2. `ExtractionPipeline` takes `retry_policy: ExtractionRetryPolicy`. Because the retry policy exclusively governs LLM provider invocations, naming it `ExtractionRetryPolicy` obscures its true domain responsibility.
3. Rename or alias to `LlmRetryPolicy` while ensuring backward compatibility if `ExtractionRetryPolicy` was exported in `__all__` or public documentation.
4. Update unit tests and documentation in `docs/how-to/harden-model-calls.md`.

## Executable Acceptance Criteria (ADR-0006)

```gherkin
Scenario: Harmonizing retry policy naming across extraction and LLM boundaries
  Given the extraction pipeline in "src/redstring/extraction/pipeline.py"
  When retry configuration is supplied
  Then the pipeline accepts "LlmRetryPolicy" with clear resilience parameters
  And all extraction pipeline unit tests pass cleanly.
```
