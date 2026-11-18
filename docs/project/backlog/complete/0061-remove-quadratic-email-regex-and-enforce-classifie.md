---
id: '0061'
title: Remove quadratic email regex and enforce classifier and LLM call timeouts
status: Complete
governing_adrs:
- ADR-0131
governing_prds:
- PRD-0013
governing_stories:
- US-0030
target_bc: extraction
---

# TASK-0061: Remove quadratic email regex and enforce classifier and LLM call timeouts

## Summary
Remove the quadratic email regex from content sanitisation, and make the classifier and LLM adapters enforce a timeout and a bounded retry count.

## Problem Statement & Context
Found by the 2026-11-17 security sweep (resource-exhaustion and LLM reviews), verified locally.

- `src/redstring/extraction/classifier.py:83` `EMAIL_PATTERN` is `\b[A-Za-z0-9._%+-]+@...`. On text with no `@`, each word boundary inside a run of local-part characters starts a new attempt that backtracks over the whole run. Measured: 20 000 characters of `"a."` take 0.8 s, and 40 000 take 3.2 s. `_sanitize_content` runs it on the full content before truncation (`:188-189`), synchronously on the event loop, so no timeout can interrupt it.
- `classifier.py:117,144` stores `timeout_seconds`, but nothing applies it. The `except TimeoutError` at `:253-257` is therefore unreachable.
- `src/redstring/llm/adapters/langchain.py` and `langchain_embedding.py` build the OpenAI client with no `timeout` and no `max_retries`. The SDK defaults are 600 s and 2 retries, so a stalled endpoint holds a `CallLimiter` slot for up to 30 minutes.

## Definition of Done (Blackbox Frontdoor TDD)
1. Sanitisation of 80 000 characters of `"a."`, and of `"a@" + "a." * 20000`, completes in less than 0.5 s (test with a wall-clock bound).
2. Normal email addresses are still redacted (existing sanitisation tests stay green, plus one case with a sub-domain).
3. Content is truncated before it is sanitised.
4. A classifier provider that never returns causes `classify` to finish at `timeout_seconds` with the documented fallback.
5. The OpenAI-compatible chat and embedding adapters pass an explicit request timeout and retry count, and both are constructor parameters.
