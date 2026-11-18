---
id: '0062'
title: Bound and sanitise LLM extraction output before reuse in prompts and logs
status: Complete
governing_adrs:
- ADR-0111
governing_prds:
- PRD-0013
governing_stories:
- US-0030
target_bc: extraction
---

# TASK-0062: Bound and sanitise LLM extraction output before reuse in prompts and logs

## Summary
Treat LLM extraction output as untrusted data: bound its size, remove control characters before it goes into a later prompt, and keep raw model text out of logs.

## Problem Statement & Context
Found by the 2026-11-17 security sweep (LLM review).

- `src/redstring/extraction/schema.py:39-42,68-110`: `entities` and `relationships` have no item limit, and `name`, `entity_type`, `description` and `properties` have no length limit. Only `temporal_expression` has `max_length=500`.
- Second-order prompt injection: `ExtractedEntity.name` and `entity_type` go to `carryover.py:139-161`, which writes `"- {name} ({entity_type})"` into the system prompt of later chunks. `gleaning.py:102` does the same. A name with newlines can thus add instructions to the prompt of the same document's later chunks.
- `src/redstring/llm/retry.py:209`: `retryable_exceptions` defaults to `(Exception,)`, which retries permanent refusals and malformed completions.
- `extraction/schema_org.py:101,287` log with f-strings that contain document-derived exception text. `langchain.py:307-308` and `classifier.py:248-249` put `str(error)` (which can echo model output) into log extras.
- `src/redstring/llm/circuit_breaker.py:124`: breaker keys have no tenant. This is correct for an endpoint-wide breaker, but it is not documented.

## Definition of Done (Blackbox Frontdoor TDD)
1. A completion with an entity name above the limit, or with more entities than the limit, is rejected or clipped by documented rule (test both).
2. A carried-over or gleaning mention whose name contains `\n`, `\r` or other control characters reaches the next prompt on one line (test reads the prompt the provider receives).
3. `with_retry` with default arguments does not retry `LlmProviderError` subclasses (test with a refusal: one call, not four).
4. No log call in `src/` formats exception text into the message with an f-string; logs that record a model failure record the type and a bounded, `repr`-escaped excerpt.
5. The circuit breaker docstring states that its state is endpoint-wide and not per tenant.
