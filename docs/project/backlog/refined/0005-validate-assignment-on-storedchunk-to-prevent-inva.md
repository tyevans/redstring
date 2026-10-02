---
id: '0005'
title: Validate assignment on StoredChunk to prevent invalid text and identity mutation
status: Refined
governing_adrs:
- ADR-0003
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0003
target_bc: domain
mutation_scope: '[''src/redstring/domain/chunk.py'']'
---

# TASK-0005: Validate assignment on StoredChunk to prevent invalid text and identity mutation

## Summary
`StoredChunk.text`'s validator `_text_is_storable` runs only on construction, not on attribute assignment (BACKLOG B160). When `chunk.text = "bad\x00text"` is executed, Pydantic accepts the invalid string in memory without validation, and since `id` is derived from `text`, `chunk.id` is silently mutated out from under store indices and caller caches.

## Problem Statement & Context
1. In `src/redstring/domain/chunk.py`, `StoredChunk` lacks `validate_assignment=True` in its model configuration.
2. In-place attribute assignment bypasses `_text_is_storable`, admitting unstorable bytes (null bytes, invalid encodings) that fail only downstream at Postgres persistence.
3. Because `StoredChunk.id` is a derived computed field, mutating `text` changes identity silently.

## Definition of Done (Blackbox Frontdoor TDD)
1. In-place assignment to `chunk.text` triggers `_text_is_storable` validation and raises `ValidationError` on unstorable text.
2. Mutation isolation tests continue passing (in-place container mutation on `entity_ids` and `metadata` is unaffected).
3. Target module `src/redstring/domain/chunk.py` maintains >=80% mutant kill score under mutmut.
4. All modified files remain strictly <500 lines.
