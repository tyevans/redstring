---
id: '0030'
title: "Ingest untrusted documents without resource exhaustion or injection"
status: Accepted
created: 2026-11-17
persona: "Jordan"
target_bc: "core"
feature: "FEAT-CORE-30"
governing_prd: "PRD-0013"
scenarios:
  - "Adversarial text does not stall classification"
  - "LLM output cannot inject into later prompts"
  - "Traversal and batch parameters are bounded"
  - "Imported graph files are parsed as safe XML and JSON"
---

# US-0030 — Ingest untrusted documents without resource exhaustion or injection

## Governing PRD
- [`PRD-0013: Security Hardening of Untrusted Input Boundaries and Supply Chain`](../../product/accepted/prd-0013-security-hardening-of-untrusted-input-boundar.md)

## User Story

**As** Jordan, a data platform lead who ingests documents from sources I do not control,
**I want** redstring to bound the work and the text that a document can cause,
**So that** one adversarial document cannot stall the pipeline or inject text into prompts, logs or query text.

## Acceptance Criteria

```gherkin
Scenario: Adversarial text does not stall classification
  Given a document of 80 000 characters that repeats "a." and has no "@"
  When the content classifier sanitises it
  Then sanitisation completes in less than 0.5 seconds
  And a real email address in normal text is still redacted
```

```gherkin
Scenario: LLM output cannot inject into later prompts
  Given the LLM returns an entity whose name contains a newline and an instruction
  When the next chunk of the same document is extracted
  Then the carried-over mention in that prompt is on one line
  And it is no longer than the entity name limit
```

```gherkin
Scenario: Traversal and batch parameters are bounded
  Given a graph store adapter
  When the caller asks for neighbours at a depth above the documented maximum
  Then the call raises ValueError before any query runs
```

```gherkin
Scenario: Imported graph files are parsed as safe XML and JSON
  Given a GraphML payload that declares an XML entity
  When the caller imports it
  Then the import raises ValueError
  And a JSON-LD payload nested 100 000 levels deep also raises ValueError
```
