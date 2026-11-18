---
id: '0020'
title: "Portable Graph Import and Lossless JSON-LD / GraphML Round-Tripping"
status: Accepted
created: 2026-10-16
persona: "Mateo"
target_bc: "composition"
feature: "FEAT-COMP-20"
governing_prd: "PRD-0008"
release: "v0.14.0"
scenarios:
  - "Round-trip JSON-LD export and import into a fresh tenant preserves entities and relationships"
  - "Round-trip GraphML export and idempotent re-import into a tenant store"
  - "Reject malformed or dangling-edge import payloads without partial store corruption"
---

# US-0020 — Portable Graph Import and Lossless JSON-LD / GraphML Round-Tripping

## Governing PRD
- [`PRD-0008: Portable Graph Import, Lossless Round-Tripping, and Filtered Subgraph Visualization`](../../product/accepted/prd-0008-portable-graph-import-lossless-round-tripping-and-f.md)

## Governing ADRs
- [`ADR-0149: Unified Redstring Client Facade and In-Memory Indexing Architecture`](../../adrs/accepted/adr-0149-unified-redstring-client-facade-and-in-memory-inde.md)
- [`ADR-0006: Executable BDD User Stories`](../../adrs/accepted/adr-0006-executable-bdd-user-stories.md)

## User Story

**As a** Mateo (The Knowledge Federation & Interoperability Engineer),
**I want** `import_graph`, `parse_json_ld`, `parse_graphml`, and `Redstring.import_graph` to hydrate exported JSON-LD and GraphML payloads back into a tenant's graph store idempotently with a `GraphImportReport`,
**So that** `export_graph` has a lossless logical opposite for migrating, backing up, and federating knowledge graphs without re-running LLM extraction.

## Acceptance Criteria

```gherkin
Scenario: Round-trip JSON-LD export and import into a fresh tenant preserves entities and relationships
  Given a populated tenant graph containing entities with descriptions, properties, temporal extents, and typed relationships
  When I export the tenant graph with format "json-ld" and import the payload into a target tenant via client.import_graph
  Then a GraphImportReport is returned with entities_imported and relationships_imported matching the source graph and elapsed_ms >= 0.0
  And re-exporting the target tenant to "json-ld" produces an identical JSON-LD document.
```

```gherkin
Scenario: Round-trip GraphML export and idempotent re-import into a tenant store
  Given a populated tenant graph exported with format "graphml"
  When I call client.import_graph with format "graphml" twice on the same target tenant
  Then both calls succeed and the target tenant contains the exact entity and relationship counts without duplicates.
```

```gherkin
Scenario: Reject malformed or dangling-edge import payloads without partial store corruption
  Given an empty target tenant store and a JSON-LD payload containing a relationship referencing an unknown target entity URN
  When I call client.import_graph with the invalid payload
  Then a ValueError is raised describing the missing endpoint
  And the target tenant store remains empty.
```
