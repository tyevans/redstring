---
id: '0007'
title: "Measure Ingestion Throughput and Track Chunk Progress"
status: Accepted
created: 2026-10-04
persona: "Alex (The Knowledge Graph Architect)"
target_bc: "extraction"
feature: "FEAT-BENCH-01"
governing_prd: "PRD-0001"
scenarios:
  - "Observe incremental chunk extraction progress with real denominators"
  - "Execute reproducible throughput sweeps across concurrency levels"
---

# US-0007 — Measure Ingestion Throughput and Track Chunk Progress

## Governing PRD
- [`PRD-0001: Knowledge Graph Extraction and Event-Sourced Storage Engine`](../../product/accepted/prd-0001-knowledge-graph-extraction-and-event-sourced-.md)

## User Story

**As a** knowledge graph engineer (Alex),
**I want** incremental progress observers during long-running extractions and a dedicated benchmark runner,
**So that** ingestion throughput, time-to-first-entity, and extraction latency are observable and empirically measurable.

## Acceptance Criteria

```gherkin
Scenario: Observe incremental chunk extraction progress with real denominators
  Given an extraction pipeline configured with a progress callback
  When processing a document split into N chunks under bounded concurrency
  Then the observer receives progress notifications reporting current chunk index and total chunk count
  And progress updates fire as wavefront batches complete.
```

```gherkin
Scenario: Execute reproducible throughput sweeps across concurrency levels
  Given the committed benchmark corpus in bench/corpus/
  When the benchmark harness executes a sweep matrix
  Then structured latency, token throughput, and chunk rate metrics are written to machine-readable JSON
  Without requiring network calls to external uncommitted data sources.
```
