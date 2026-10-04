---
id: 0028
title: Decompose LangChain adapter test suite to satisfy file length limit
status: Complete
governing_adrs:
- ADR-0001
- ADR-0002
- ADR-0008
- ADR-0147
governing_prds:
- PRD-0001
governing_stories:
- US-0001
target_bc: llm
signed_off_by: Ty Evans <ty@tyevans.net>
signed_off_at: '2026-10-04T20:28:01.741198+00:00'
mutation_scope: '[]'
---

# TASK-0028: Decompose LangChain adapter test suite to satisfy file length limit

## Summary
`tests/unit/llm/test_langchain_adapter.py` is currently 467 lines, triggering a proactive refactoring warning at >=400 lines and approaching the 500-line hard invariant limit (ADR-0002). Decompose the test suite into focused modules (e.g. prompt invocation tests vs streaming and error handling tests).

## Context & Objectives
1. Governed by ADR-0002 (<500 lines per file) and ADR-0008 (The two non-store ports).
2. The current test file covers:
   - Chat model invocation and prompt rendering
   - Schema parameter extraction and structured outputs
   - Model retry, error mapping, and exception translation
3. Decompose into focused modules under `tests/unit/llm/` (e.g. `test_langchain_adapter_invocation.py` and `test_langchain_adapter_structured.py`).
4. Ensure all unit tests pass with zero warnings.

## Executable Acceptance Criteria (ADR-0006)

```gherkin
Scenario: Auditing LangChain adapter test file length
  Given the decomposed LangChain adapter test modules in "tests/unit/llm/"
  When "spec-ops health" executes
  Then each decomposed test file contains fewer than 400 lines
  And zero proactive refactoring warnings are emitted
  And all LangChain adapter unit tests pass cleanly.
```
