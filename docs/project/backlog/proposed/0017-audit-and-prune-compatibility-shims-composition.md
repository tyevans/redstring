---
id: '0017'
title: Audit and prune backwards compatibility shims in composition BC
status: Proposed
governing_adrs:
- ADR-0007
governing_prds:
- PRD-0001
governing_stories:
- US-0002
target_bc: composition
---

# TASK-0017: Audit and prune backwards compatibility shims in composition BC

## Summary
Audit `src/redstring/composition/` (`composition/__init__.py`, `build_graph.py`, `retrieval.py`, and `themes/`) to dismantle the blanket module re-export facade and replace with intentional, documented entrypoints.

## Context & Objectives
- Replace the legacy "re-export everything" pattern in `composition/__init__.py` with explicit exports.
- Clean up legacy parameter shims and compatibility aliases across high-level composition services (`Retriever`, `ChunkRetriever`, `Consolidator`, `build_graph`).
- Verify public facade contracts and end-to-end tests remain green.
