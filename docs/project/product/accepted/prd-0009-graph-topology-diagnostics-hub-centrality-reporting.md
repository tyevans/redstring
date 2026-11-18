---
id: '0009'
title: Graph Topology Diagnostics, Hub Centrality Reporting, and Snapshot Diffing
status: Accepted
created: 2026-10-16
target_persona: Soren
component: composition
release: v0.14.0
---

# PRD-0009 — Graph Topology Diagnostics, Hub Centrality Reporting, and Snapshot Diffing

## Who this is for

- **Soren (The Network Scientist & Graph Topology Auditor)**: Needs zero-LLM structural topology diagnostics (`analyze_topology` returning `GraphTopologyReport` and `EntityHub` rankings) and tenant/snapshot graph diffing (`diff_graphs` returning `GraphDiffReport`) to audit graph connectivity, fragmentation, and longitudinal mutations.

## What the person cannot do today

- **No Zero-LLM Structural Telemetry**: Before paying for LLM calls in `summarize_themes`, operators cannot inspect connected component counts, isolated singleton counts, degree distributions, graph density, or top-$k$ hub entities.
- **Unverified Graph Mutations**: After running batch ingestion, entity consolidation (`Consolidator.resolve`), or merge reversal (`Consolidator.undo`), operators have no structural diff function to compare two tenants or snapshots and enumerate added, removed, and modified entities and relationships.

## What good looks like

1. **Structural Topology & Hub Centrality Reporting (`analyze_topology`, `Redstring.analyze_topology`)**:
   - Reads a tenant's graph via `EntityReader` and `RelationshipStore` and returns `GraphTopologyReport` containing `entity_count`, `relationship_count`, `dangling_edge_count`, `entity_type_counts`, `relationship_type_counts`, `connected_components`, `largest_component_size`, `isolated_entities`, `min_degree`, `max_degree`, `mean_degree`, `density`, `top_hubs: tuple[EntityHub, ...]`, and `elapsed_ms`.
   - Each `EntityHub` records `entity`, `degree`, `in_degree`, and `out_degree`, sorted descending by `degree` and tie-broken deterministically by `str(entity.id)`.

2. **Snapshot & Tenant Graph Diffing (`diff_graphs`, `Redstring.diff_graphs`)**:
   - Compares a baseline `(baseline_tenant_id, graph, relationships)` against a target `(target_tenant_id, target_graph, target_relationships)` and returns `GraphDiffReport` with `added_entities`, `removed_entities`, `modified_entities` (`EntityModification`), `added_relationships`, `removed_relationships`, `modified_relationships` (`RelationshipModification`), `unchanged_entity_count`, `unchanged_relationship_count`, `is_identical`, and `elapsed_ms`.

## What this does not do

- **Mutate Stores or Invoke LLMs**: `analyze_topology` and `diff_graphs` are pure, deterministic read-only operations over `EntityReader` and `RelationshipStore`.

## Checkable Outcomes

1. Calling `analyze_topology(tenant_id, graph=store, relationships=store, top_k=5)` returns a `GraphTopologyReport` with exact connected component counts, isolated singleton counts, type histograms, degree statistics, and deterministically ordered `EntityHub` records.
2. Calling `diff_graphs(baseline_tenant, target_tenant, graph=store, relationships=store)` accurately identifies added, removed, and attribute-modified entities (`changed_fields`) and relationships, and reports `is_identical is True` when comparing identical snapshots.
3. On a 250-entity, 500-relationship graph, `analyze_topology` and `diff_graphs` complete deterministically in under 500 milliseconds (`elapsed_ms < 500.0`).

## Linked User Stories

- [`US-0022`](../../user_stories/accepted/us-0022-graph-topology-diagnostics-and-hub-centrality-repor.md): Graph Topology Diagnostics and Hub Centrality Reporting
- [`US-0023`](../../user_stories/accepted/us-0023-structural-graph-snapshot-diffing-and-mutation-veri.md): Structural Graph Snapshot Diffing and Mutation Verification
