---
id: '0013'
title: Security Hardening of Untrusted Input Boundaries and Supply Chain
status: Accepted
created: 2026-11-17
target_persona: Jordan
component: core
---

# PRD-0013 — Security Hardening of Untrusted Input Boundaries and Supply Chain

## Who this is for

- **Jordan** runs redstring on documents that Jordan does not control: web pages, uploads, and third-party exports. Jordan must know that one bad document cannot stop the pipeline, change other data, or put text into a prompt, a log line or a query.

## What the person cannot do today

- A 40 000-character run of text with no `@` stops content classification for more than 3 seconds. The classifier regex for email addresses is quadratic, and it runs on the event loop.
- Entity names that the LLM extracts go into the system prompt of later chunks without a length limit and with their newlines. A document can thus write instructions into the prompt of its own later chunks.
- `neighbors(depth=...)`, `explain_path(max_depth=...)`, batch concurrency, gleaning rounds, page sizes and chunk sizes have no upper limit. A caller value or a dense graph can cause very large work.
- GraphML import uses an HTML parser. It drops CDATA, flattens nested markup, and loses unclosed nodes with no error. Deeply nested JSON-LD gives `RecursionError` instead of `ValueError`.
- One CI workflow runs code from the moving default branch of another repository, with `id-token: write`. ADR-0010 says that secret scanning occurs, but no hook or job does it.

## What good looks like

1. **Bounded work**: every caller parameter that multiplies work has a documented upper limit. Each limit has a test at the limit and at the limit plus one.
2. **Inert LLM output**: LLM output is data. It has length limits, and it has no control characters when it goes into a prompt. Logs record the error type, not the raw model text.
3. **Safe parsers**: graph import uses a parser that rejects DTDs and entity declarations. Malformed input gives `ValueError`.
4. **Pinned supply chain**: every action and remote hook is pinned to a commit SHA. Secret scanning runs in pre-commit and in CI.

## What this does not do

- It does not add authentication or authorisation. redstring is a library, and the caller owns the tenant decision.
- It does not change the lossless round-trip contract of PRD-0008. Import keeps the supplied provenance, and the docs state that the import file is trusted for provenance.

## Checkable Outcomes

1. Classification of 80 000 adversarial characters completes in less than 0.5 s.
2. A carried-over entity name that contains a newline reaches the next prompt with no newline.
3. `neighbors(depth=MAX + 1)` and `explain_path(max_depth=MAX + 1)` raise `ValueError` on every adapter.
4. A GraphML payload that contains `<!DOCTYPE` or `<!ENTITY` raises `ValueError`.
5. `grep -E "uses: [^@]+@v[0-9]" .github/workflows/*.yml` finds no match.

## Linked User Stories

- [`US-0030`](../../user_stories/accepted/us-0030-ingest-untrusted-documents-without-resource-exhaus.md)

## Implementing Backlog Tasks

- TASK-0061 to TASK-0067
