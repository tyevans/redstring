---
id: '0007'
title: Detect corpus embedded under mismatched task prefixes in vector store
status: Refined
governing_adrs:
- ADR-0003
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0003
target_bc: domain
mutation_scope: '[''src/redstring/llm/adapters/'']'
---

# TASK-0007: Detect corpus embedded under mismatched task prefixes in vector store

## Summary
When `document_prefix` is changed on `LangChainEmbeddingProvider` or `FakeEmbeddingProvider`, vectors from prior runs become mathematically incomparable to vectors from newer runs within the same vector store (BACKLOG B156). This task introduces provenance verification on vector stores to guard against mixed prefix spaces.

## Problem Statement & Context
1. In `src/redstring/llm/adapters/`, embedding providers support asymmetric retrieval prefixes (such as BGE query prefixes).
2. Changing `document_prefix` alters the vector embedding space without modifying the stored `model` column.
3. This creates a silent semantic degradation where similarity search yields corrupted rankings across heterogeneous runs.

## Definition of Done (Blackbox Frontdoor TDD)
1. Provenance metadata (model, document prefix, dimension) recorded and verified at initialization.
2. Incompatible prefix changes raise clear validation errors rather than silently appending incomparable embeddings.
3. Frontdoor unit tests assert rejection of mismatched prefix spaces.
4. All source files strictly <500 lines.
