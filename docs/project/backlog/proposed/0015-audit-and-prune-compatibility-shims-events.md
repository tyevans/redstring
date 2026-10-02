---
id: '0015'
title: Audit and prune backwards compatibility shims in events BC
status: Proposed
governing_adrs:
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0002
target_bc: events
---

# TASK-0015: Audit and prune backwards compatibility shims in events BC

## Summary
Audit `src/redstring/events/` schemas and stream factories for legacy event type aliases, deprecated field translations, and transitional re-exports.

## Context & Objectives
- Ensure immutable event definitions represent canonical domain occurrences.
- Remove deprecated alias names or transitional conversion methods from event classes.
- Verify stream definition factories remain concise and idiomatic.
