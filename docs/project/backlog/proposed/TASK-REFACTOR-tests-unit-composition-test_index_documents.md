---
id: REFACTOR-tests-unit-composition-test_index_documents
title: Refactor and Decompose Legacy File test_index_documents.py
status: Proposed
created: 2026-09-29
governing_adrs:
  - ADR-0002
governing_prds:
  - PRD-0001
governing_stories:
  - US-0004
target_bc: core
---

# TASK-REFACTOR-tests-unit-composition-test_index_documents: Refactor Legacy File test_index_documents.py

## Summary
The grandfathered debt file `tests/unit/composition/test_index_documents.py` contains 623 lines and violates the hard file length invariant governed by ADR-0002 (<500 lines).
This task plans the incremental extraction of cohesive submodules (test_index_documents_the.py, test_index_documents_embedding.py) and establishes a public barrel export facade.

## Target Submodule Decomposition Path
Target decomposition destination: `tests/unit/composition/test_index_documents/` with submodules:
- `test_index_documents_the.py`: TestTheTwoOrderings, TestTheReport, TestChunkMetadataReachesTheCorpus, document, long_document, numbered, small_chunker, TestThereIsNoModel, TestRepeats, TestTenants, _AnnotatingChunker
- `test_index_documents_embedding.py`: _RaisingEmbeddingProvider, _RecordingEmbeddingProvider, TestEmbedding

## AST Decomposition Blueprint
Decomposition Blueprint for /home/ty/workspace/redstring/tests/unit/composition/test_index_documents.py (623 lines):
  Submodule 'test_index_documents_the.py' (~391 lines):
    - [class] TestTheTwoOrderings (lines 189-291)
    - [class] TestTheReport (lines 294-350)
    - [class] TestChunkMetadataReachesTheCorpus (lines 600-623)
    - [function] document (lines 35-36)
    - [function] long_document (lines 39-41)
    - [function] numbered (lines 44-46)
    - [function] small_chunker (lines 49-50)
    - [class] TestThereIsNoModel (lines 53-88)
    - [class] TestRepeats (lines 91-186)
    - [class] TestTenants (lines 531-553)
    - [class] _AnnotatingChunker (lines 556-597)
  Submodule 'test_index_documents_embedding.py' (~172 lines):
    - [class] _RaisingEmbeddingProvider (lines 353-365)
    - [class] _RecordingEmbeddingProvider (lines 368-385)
    - [class] TestEmbedding (lines 388-528)
  Suggested barrel exports:
    from .test_index_documents_the import TestTheTwoOrderings, TestTheReport, TestChunkMetadataReachesTheCorpus, document, long_document, numbered, small_chunker, TestThereIsNoModel, TestRepeats, TestTenants, _AnnotatingChunker
    from .test_index_documents_embedding import _RaisingEmbeddingProvider, _RecordingEmbeddingProvider, TestEmbedding

    __all__ = ["TestTheTwoOrderings", "TestTheReport", "TestChunkMetadataReachesTheCorpus", "document", "long_document", "numbered", "small_chunker", "TestThereIsNoModel", "TestRepeats", "TestTenants", "_AnnotatingChunker", "_RaisingEmbeddingProvider", "_RecordingEmbeddingProvider", "TestEmbedding"]

## INVEST Criteria
- **Independent**: Executed in isolated task branch without modifying shared backlog on branch.
- **Negotiable**: Concrete boundaries derived from AST seams.
- **Valuable**: Retires grandfathered technical debt from `.specops/grandfathered_debt.json`.
- **Estimable**: Symbol-level decomposition blueprint provided.
- **Small (<500 lines)**: Target submodules each strictly under 400 lines.
- **Testable**: Validated via blackbox frontdoor tests (ADR-0003).
