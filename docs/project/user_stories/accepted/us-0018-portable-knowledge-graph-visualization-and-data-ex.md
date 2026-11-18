---
id: '0018'
title: "Portable Knowledge Graph Visualization and Data Export"
status: Accepted
created: 2026-10-16
persona: "Elena"
target_bc: "composition"
feature: "FEAT-COMP-18"
governing_prd: "PRD-0007"
scenarios:
  - "Export tenant knowledge graph to Mermaid flowchart diagram"
  - "Export tenant knowledge graph to JSON-LD linked data document"
  - "Export tenant knowledge graph to GraphML XML format"
---

# US-0018 — Portable Knowledge Graph Visualization and Data Export

## Governing PRD
- [`PRD-0007: Portable Graph Visualization Exports, Explainable Citation Paths, and Streaming Ingestion`](../../product/accepted/prd-0007-portable-graph-visualization-exports-explainable-c.md)

## Governing ADR
- [`ADR-0149: Unified Redstring Client Facade and In-Memory Indexing Architecture`](../../adrs/accepted/adr-0149-unified-redstring-client-facade-and-indexing.md)

## User Story

**As an** Elena (The Temporal & Investigative Intelligence Analyst),
**I want** to export a tenant's knowledge graph into Mermaid, JSON-LD, and GraphML formats directly from `Redstring.export_graph` or `export_graph`,
**So that** I can embed deterministic graph diagrams in Markdown reports, publish standards-compliant JSON-LD linked data, and load subgraphs into external network analysis tools with zero custom boilerplate.

## Acceptance Criteria

```gherkin
Scenario: Export tenant knowledge graph to Mermaid flowchart diagram
  Given a Redstring client with extracted entities and relationships for a tenant
  When I call client.export_graph with format "mermaid"
  Then a deterministic Mermaid "flowchart LR" string is returned
  And every entity node and labeled relationship edge for the tenant is represented with safe quoting.
```

```gherkin
Scenario: Export tenant knowledge graph to JSON-LD linked data document
  Given a Redstring client with extracted entities and relationships for a tenant
  When I call client.export_graph with format "json-ld"
  Then a valid JSON-LD string with "@context" and "@graph" is returned
  And entities include their types, descriptions, temporal bounds, and outbound relationships.
```

```gherkin
Scenario: Export tenant knowledge graph to GraphML XML format
  Given a Redstring client with extracted entities and relationships for a tenant
  When I call client.export_graph with format "graphml"
  Then a well-formed XML GraphML string is returned containing "<node>" and "<edge>" elements for the tenant
  And data from other tenants is strictly excluded.
```
