---
id: '0026'
title: "Reverse Source and Entity Provenance Lineage Tracing"
status: Accepted
created: 2026-10-16
persona: "Liam"
target_bc: "composition"
feature: "FEAT-COMP-26"
governing_prd: "PRD-0011"
release: "v0.14.0"
scenarios:
  - "Trace all entities, relationships, and chunks originating from a SourceId"
  - "Trace entity lineage across canonical entity, absorbed aliases, and supporting chunks"
  - "Unknown source or unknown entity lineage queries handle absence cleanly"
---

# US-0026 — Reverse Source and Entity Provenance Lineage Tracing

## Governing PRD
- [`PRD-0011: Reverse Provenance Lineage Tracing and Corpus Coverage Reporting`](../../product/accepted/prd-0011-reverse-provenance-lineage-tracing-and-corpus-cover.md)

## Governing ADRs
- [`ADR-0135: Provenance Is a Value Object`](../../adrs/accepted/adr-0135-provenance-is-a-value-object.md)
- [`ADR-0149: Unified Redstring Client Facade and In-Memory Indexing Architecture`](../../adrs/accepted/adr-0149-unified-redstring-client-facade-and-in-memory-inde.md)
- [`ADR-0006: Executable BDD User Stories`](../../adrs/accepted/adr-0006-executable-bdd-user-stories.md)

## User Story

**As a** Liam (The Provenance Lineage & Corpus Quality Auditor),
**I want** `trace_source_lineage` and `trace_entity_lineage` (and their `Redstring` facade methods) to trace all graph and chunk artifacts produced by a `SourceId` and assemble the full evidence dossier (provenance, absorbed aliases, relationships, chunks, and source IDs) for any entity or merged alias,
**So that** extraction has a verifiable reverse lineage inquiry for discovery, retraction, and citation auditing.

## Acceptance Criteria

```gherkin
Scenario: Trace all entities, relationships, and chunks originating from a SourceId
  Given a tenant where document "doc-lovelace" was extracted into entities, relationships, and chunks
  And document "doc-babbage" was extracted separately in the same tenant
  When I call client.trace_source_lineage for SourceId "doc-lovelace"
  Then a SourceLineageReport is returned containing only the entities, relationships, and chunks from "doc-lovelace"
  And report.linked_chunk_count plus report.unlinked_chunk_count equals len(report.chunks)
  And no artifacts exclusive to "doc-babbage" or another tenant appear in the report.
```

```gherkin
Scenario: Trace entity lineage across canonical entity, absorbed aliases, and supporting chunks
  Given a tenant where "Ada Lovelace" absorbed duplicate entity "A. Lovelace" via consolidation
  And chunks in the chunk store reference both the canonical EntityId and the absorbed alias EntityId
  When I call client.trace_entity_lineage for the absorbed alias EntityId
  Then an EntityLineageReport is returned for the canonical "Ada Lovelace" entity
  And report.aliases contains the Alias record for "A. Lovelace"
  And report.chunks contains deduplicated supporting passages for both the canonical ID and the absorbed alias ID
  And report.source_ids lists all unique SourceIds from the entity provenance, relationships, and chunks.
```

```gherkin
Scenario: Unknown source or unknown entity lineage queries handle absence cleanly
  Given an empty or populated tenant
  When I call client.trace_source_lineage for an unknown SourceId "doc-missing"
  Then a SourceLineageReport is returned with empty entities, relationships, and chunks tuples
  And when I call client.trace_entity_lineage for an unknown entity "Nonexistent Person"
  Then None is returned.
```
