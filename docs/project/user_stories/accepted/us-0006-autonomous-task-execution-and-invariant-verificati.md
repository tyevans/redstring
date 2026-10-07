---
id: '0006'
title: "Autonomous Task Execution and Invariant Verification"
status: Accepted
created: 2026-10-02
persona: "Morgan"
target_bc: "core"
feature: "FEAT-ORCH-01"
governing_prd: "PRD-0001"
scenarios:
  - "Executing backlog tasks in isolated git worktrees"
  - "Enforcing file length and boundary invariants during preflight"
  - "Completing task worktree integration from secondary worktree"
---

# US-0006 — Autonomous Task Execution and Invariant Verification

## Governing PRD
- [`PRD-0001: Knowledge Graph Extraction and Event-Sourced Storage Engine`](../../product/accepted/prd-0001-knowledge-graph-extraction-and-event-sourced-.md)

## User Story

**As an** autonomous coding agent (Morgan),
**I want** explicit task contracts that execute inside isolated git worktrees with automated preflight validation,
**So that** changes are developed with zero merge conflicts, 100% blackbox frontdoor verification, and zero file limit violations.

## Acceptance Criteria

```gherkin
Scenario: Executing backlog tasks in isolated git worktrees
  Given a refined task selected from "docs/project/backlog/PRIORITY.md"
  When the agent creates and enters a worktree via "spec-ops worktree create TASK-XXXX"
  Then work executes on an isolated git branch without touching shared backlog files (ADR-0005)
  And all implementation code adheres strictly to public contracts.
```

```gherkin
Scenario: Enforcing file length and boundary invariants during preflight
  Given completed feature code inside the active worktree
  When the agent runs preflight verification via "spec-ops health"
  Then zero source files exceed 500 lines (ADR-0002)
  And zero prohibited cross-layer or cross-context dependencies are introduced.
```

```gherkin
Scenario: Completing task worktree integration from secondary worktree
  Given an isolated secondary task worktree on a feature branch
  When the agent executes worktree finalization via "spec-ops worktree finish"
  Then the primary repository root is resolved without checkout collision on main
  And integration commits are finalized under merge lock
```
