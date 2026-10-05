---
id: '0036'
title: "Domain Model & State Handlers for Dual-Channel Chunk Corpus and Passage Retrieval Engine"
status: Proposed
created: 2026-10-04
dependencies: ['TASK-0035']
governing_prds:
  - PRD-0002
governing_stories:
  - US-0010
target_bc: chunks
---

# TASK-0036: Domain Model & State Handlers for Dual-Channel Chunk Corpus and Passage Retrieval Engine

## Summary
Implement domain model & state handlers in component `chunks` fulfilling PRD-0002.

## Problem Statement
Deliver focused slice satisfying INVEST criteria and Hard Invariant 6 (<500 lines).

## Definition of Done (Blackbox Frontdoor TDD)
1. Public interfaces or standard domain contracts implemented.
2. Verified via automated blackbox tests with zero private backdoor manipulation.
3. All new source files strictly under 500 lines.
