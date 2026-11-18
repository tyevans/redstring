---
id: '0008'
title: Portable Graph Import, Lossless Round-Tripping, and Filtered Subgraph Visualization
status: Accepted
created: 2026-10-16
target_persona: Mateo
component: composition
release: v0.14.0
---

# PRD-0008 — Portable Graph Import, Lossless Round-Tripping, and Filtered Subgraph Visualization

## Who this is for

- **Mateo (The Knowledge Federation & Interoperability Engineer)**: Needs symmetric `import_graph`, `parse_json_ld`, and `parse_graphml` capabilities so exported knowledge graph snapshots can be hydrated back into any tenant's `EntityWriter` and `RelationshipStore` idempotently without re-running LLM extraction.
- **Elena (The Temporal & Investigative Intelligence Analyst)**: Needs filtered and ego-centered subgraph export on `export_graph` (`entity_types`, `relationship_types`, `center_entity_id`, `max_depth`) to visualize focused neighborhoods without whole-tenant clutter.

## What the person cannot do today

- **One-Way Export Trap**: `export_graph` serializes a tenant's graph to JSON-LD and GraphML XML, but callers have no logical opposite operation (`import_graph`) to parse and load those portable formats back into a `GraphStore` or `Redstring` client.
- **Unfiltered Whole-Tenant Diagrams**: Exporting a large tenant graph into Mermaid or GraphML emits every node and edge in the tenant, overwhelming diagram renderers when an analyst only needs a k-hop neighborhood around a focal entity or specific entity/relationship types.

## What good looks like

1. **Symmetric Zero-Dependency Graph Parsers (`parse_json_ld`, `parse_graphml`)**:
   - `parse_json_ld(payload, *, tenant_id)` reconstructs `Entity` (preserving `id`, `name`, `normalized_name`, `entity_type`, `description`, `properties`, `temporal`, and `provenance`) and `Relationship` (preserving `id`, `source_entity_id`, `target_entity_id`, `relationship_type`, `confidence`, `properties`).
   - `parse_graphml(payload, *, tenant_id)` parses GraphML `<node>` and `<edge>` elements into domain `Entity` and `Relationship` instances.
   - Both parsers reject malformed payloads or dangling edges referencing missing endpoints with `ValueError`.

2. **Idempotent Graph Store Hydration (`import_graph`, `Redstring.import_graph`)**:
   - `import_graph(payload, tenant_id, *, graph, relationships, format=...)` upserts parsed entities and relationships into narrow `EntityWriter` and `RelationshipStore` capabilities and returns `GraphImportReport(entities_imported, relationships_imported, format, elapsed_ms)`.

3. **Filtered & Ego-Network Subgraph Export (`export_graph`, `Redstring.export_graph`)**:
   - `export_graph` accepts `entity_types`, `relationship_types`, `center_entity_id` (`EntityId | str | None`), and `max_depth` (`int = 1`), filtering nodes and induced edges before rendering Mermaid, JSON-LD, or GraphML.

## What this does not do

- **Parse Display-Only Mermaid Diagrams**: `import_graph` rejects `ExportFormat.MERMAID` with `ValueError` because Mermaid flowcharts omit full entity and relationship UUID metadata required for lossless graph hydration.
- **Bypass Store Validation**: All imported entities and relationships pass through standard domain constructors and `EntityWriter` / `RelationshipStore` idempotency rules.

## Checkable Outcomes

1. Exporting a tenant graph via `export_graph(..., format=ExportFormat.JSON_LD)` and importing the payload into a fresh tenant via `import_graph` produces an identical set of entities and relationships, and `GraphImportReport` reports exact `entities_imported`, `relationships_imported`, and `elapsed_ms >= 0.0`.
2. Calling `import_graph` with `ExportFormat.GRAPHML` twice on the same target tenant idempotently upserts nodes and edges without creating duplicates.
3. Calling `export_graph` with `center_entity_id="Ada Lovelace", max_depth=1` or `entity_types=["Person"]` serializes only the matching ego-network or type-filtered induced subgraph.
4. Passing a malformed payload or a relationship referencing an unknown endpoint to `import_graph` raises `ValueError` and writes zero partial records to the store.

## Linked User Stories

- [`US-0020`](../../user_stories/accepted/us-0020-portable-graph-import-and-lossless-json-ld-graphml-.md): Portable Graph Import and Lossless JSON-LD / GraphML Round-Tripping
- [`US-0021`](../../user_stories/accepted/us-0021-filtered-subgraph-and-ego-network-visualization-exp.md): Filtered Subgraph and Ego-Network Visualization Export
