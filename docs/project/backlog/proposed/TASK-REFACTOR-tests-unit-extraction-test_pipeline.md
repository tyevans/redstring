---
id: REFACTOR-tests-unit-extraction-test_pipeline
title: Refactor and Decompose Legacy File test_pipeline.py
status: Proposed
created: 2026-09-29
governing_adrs:
  - ADR-0002
target_bc: core
---

# TASK-REFACTOR-tests-unit-extraction-test_pipeline: Refactor Legacy File test_pipeline.py

## Summary
The grandfathered debt file `tests/unit/extraction/test_pipeline.py` contains 1099 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (test_pipeline_chunk.py, test_pipeline_one.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `tests/unit/extraction/test_pipeline/` with submodules:
- `test_pipeline_chunk.py`: TestOneChunk, TestEachChunkIsToldWhatTheEarlierOnesFound, TestAskingTheSameChunkTwice, _FailsOneMarkedChunk, tenant_id, aggregate, document, payload, small_chunker, TestManyChunks, TestFailingChunks, TestRecording, TestNoStoreReachesExtraction, TestPromptIsExtractionsBusiness, TestRecordRefusesAResultFromAnotherDocument, TestTheChunkingIsCarriedOut, FailOnSubstring, RecordingProvider, GleaningFails, mention_pipeline, TestHowManyChunksReportedEachEntity
- `test_pipeline_one.py`: TestRecordGetsItsInstantFromExactlyOnePlace

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/tests/unit/extraction/test_pipeline.py (1099 lines):
  Submodule 'test_pipeline_chunk.py' (~919 lines):
    - [class] TestOneChunk (lines 76-147)
    - [class] TestEachChunkIsToldWhatTheEarlierOnesFound (lines 772-841)
    - [class] TestAskingTheSameChunkTwice (lines 844-936)
    - [class] _FailsOneMarkedChunk (lines 980-999)
    - [function] tenant_id (lines 48-49)
    - [function] aggregate (lines 53-54)
    - [function] document (lines 57-58)
    - [function] payload (lines 61-68)
    - [function] small_chunker (lines 71-73)
    - [class] TestManyChunks (lines 150-209)
    - [class] TestFailingChunks (lines 212-255)
    - [class] TestRecording (lines 258-372)
    - [class] TestNoStoreReachesExtraction (lines 375-387)
    - [class] TestPromptIsExtractionsBusiness (lines 390-408)
    - [class] TestRecordRefusesAResultFromAnotherDocument (lines 468-539)
    - [class] TestTheChunkingIsCarriedOut (lines 542-703)
    - [class] FailOnSubstring (lines 706-725)
    - [class] RecordingProvider (lines 728-752)
    - [class] GleaningFails (lines 939-959)
    - [function] mention_pipeline (lines 1002-1009)
    - [class] TestHowManyChunksReportedEachEntity (lines 1012-1099)
  Submodule 'test_pipeline_one.py' (~52 lines):
    - [class] TestRecordGetsItsInstantFromExactlyOnePlace (lines 414-465)
  Suggested barrel exports:
    from .test_pipeline_chunk import TestOneChunk, TestEachChunkIsToldWhatTheEarlierOnesFound, TestAskingTheSameChunkTwice, _FailsOneMarkedChunk, tenant_id, aggregate, document, payload, small_chunker, TestManyChunks, TestFailingChunks, TestRecording, TestNoStoreReachesExtraction, TestPromptIsExtractionsBusiness, TestRecordRefusesAResultFromAnotherDocument, TestTheChunkingIsCarriedOut, FailOnSubstring, RecordingProvider, GleaningFails, mention_pipeline, TestHowManyChunksReportedEachEntity
    from .test_pipeline_one import TestRecordGetsItsInstantFromExactlyOnePlace

    __all__ = ["TestOneChunk", "TestEachChunkIsToldWhatTheEarlierOnesFound", "TestAskingTheSameChunkTwice", "_FailsOneMarkedChunk", "tenant_id", "aggregate", "document", "payload", "small_chunker", "TestManyChunks", "TestFailingChunks", "TestRecording", "TestNoStoreReachesExtraction", "TestPromptIsExtractionsBusiness", "TestRecordRefusesAResultFromAnotherDocument", "TestTheChunkingIsCarriedOut", "FailOnSubstring", "RecordingProvider", "GleaningFails", "mention_pipeline", "TestHowManyChunksReportedEachEntity", "TestRecordGetsItsInstantFromExactlyOnePlace"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
