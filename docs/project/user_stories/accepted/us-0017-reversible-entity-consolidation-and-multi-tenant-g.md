---
id: '0017'
title: "Reversible Entity Consolidation and Multi-Tenant Governance"
status: Accepted
created: 2026-10-16
persona: "Nadia"
target_bc: "consolidation"
feature: "FEAT-CONS-17"
governing_prd: "PRD-0006"
scenarios:
  - "Undo an entity merge and restore absorbed entities and relationships"
  - "Purge a single tenant across graph, vector, and chunk stores without cross-tenant impact"
  - "Rebuild a single tenant across read projections from the event feed"
  - "Reject vector embeddings with mismatched provenance or task prefix"
---

# US-0017 — Reversible Entity Consolidation and Multi-Tenant Governance

## Governing PRD
- [`PRD-0006: Production LLM Resilience, Rate Limiting, Circuit Breaking, and Governance`](../../product/accepted/prd-0006-production-llm-resilience-rate-limiting-circuit-b.md)

## Governing ADRs
- [`ADR-0135: Provenance Is a Value Object`](../../adrs/accepted/adr-0135-provenance-is-a-value-object.md)
- [`ADR-0136: A Merge Resolves the Canonical Entity's Fields`](../../adrs/accepted/adr-0136-a-merge-resolves-the-canonical-entitys-fields.md)
- [`ADR-0143: A Query Is Embedded Differently From a Document`](../../adrs/accepted/adr-0143-a-query-is-embedded-differently-from-a-document.md)

## User Story

**As a** Nadia (The Compliance & Multi-Tenant Governance Officer),
**I want** event-sourced merge reversal (`Consolidator.undo`), complete single-tenant erasure (`delete_by_tenant`), tenant-scoped projection rebuild (`rebuild_tenant`), and vector provenance verification,
**So that** bad entity merges can be reversed cleanly from the audit log, right-to-be-forgotten requests erase only the target tenant, and embedding model drift is caught immediately.

## Acceptance Criteria

```gherkin
Scenario: Undo an entity merge and restore absorbed entities and relationships
  Given a tenant graph where duplicate entity B was merged into canonical entity A via Consolidator.merge
  When I call Consolidator.undo with the merge_event_id from the ConsolidationReport
  Then a MergeUndone event is emitted and folded into the GraphStore
  And entity B is restored as an independent entity with its original relationships intact.
```

```gherkin
Scenario: Purge a single tenant across graph, vector, and chunk stores without cross-tenant impact
  Given two tenants A and B with ingested entities, embeddings, and chunks
  When I execute delete_by_tenant for tenant A across the graph, vector, and chunk stores
  Then tenant A has zero remaining entities, vectors, and chunks
  And tenant B retains 100% of its entities, vectors, and chunks.
```

```gherkin
Scenario: Rebuild a single tenant across read projections from the event feed
  Given an event feed containing extraction events for tenants A and B
  When I call rebuild_tenant for tenant A over GraphProjection, VectorProjection, and ChunkProjection
  Then tenant A's stores are wiped and deterministically reconstructed from the event feed
  And tenant B's projected state is unaffected.
```

```gherkin
Scenario: Reject vector embeddings with mismatched provenance or task prefix
  Given a VectorStore populated with embeddings under model "embed-v1"
  When a caller writes or queries vectors under a conflicting model or mismatched task prefix
  Then VectorProvenanceMismatchError or TaskPrefixMismatchError is raised.
```
