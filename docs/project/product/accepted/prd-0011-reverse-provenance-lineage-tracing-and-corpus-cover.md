---
id: '0011'
title: Reverse Provenance Lineage Tracing and Corpus Coverage Reporting
status: Accepted
created: 2026-10-16
target_persona: Liam
component: composition
release: v0.14.0
---

# PRD-0011 — Reverse Provenance Lineage Tracing and Corpus Coverage Reporting

## Who this is for

- **Liam (The Provenance Lineage & Corpus Quality Auditor)**: Needs forward source-to-artifact lineage (`trace_source_lineage` -> `SourceLineageReport`), backward entity-to-evidence dossiers across canonical and merged alias IDs (`trace_entity_lineage` -> `EntityLineageReport`), and tenant corpus coverage diagnostics (`analyze_corpus` -> `CorpusHealthReport`).
- **Nadia (The Compliance & Multi-Tenant Governance Officer)**: Needs verifiable provenance tracing for source retraction audits and compliance inquiries.

## What the person cannot do today

- **No Source-to-Artifact Lineage Query**: Callers have no single function to ask what entities, relationships, and linked/unlinked chunks were produced by a given `SourceId`.
- **Fragmented Post-Merge Entity Evidence**: After `Consolidator` merges duplicate entities into a canonical entity, supporting chunks in `ChunkStore` may still reference absorbed alias IDs. Assembling the full evidence dossier for an entity requires manual multi-port calls across `EntityReader`, `AliasStore`, `RelationshipStore`, and `ChunkReader`.
- **Unquantified Corpus Coverage**: Operators cannot easily compute how many chunks are linked vs. unlinked (`index_documents` vs. `build_graph`), embedded vs. unembedded, or what fraction of entities have supporting passage citations (`entity_chunk_coverage_ratio`).

## What good looks like

1. **Source-to-Artifact Lineage (`trace_source_lineage`, `Redstring.trace_source_lineage`)**:
   - Returns `SourceLineageReport` listing all `entities`, `relationships`, and `chunks` originating from `source_id` in `tenant_id`, along with `linked_chunk_count`, `unlinked_chunk_count`, and `elapsed_ms`.

2. **Entity-to-Evidence Reverse Lineage (`trace_entity_lineage`, `Redstring.trace_entity_lineage`)**:
   - Resolves an `EntityId` or entity name (including resolving an absorbed alias `EntityId` transitively via `AliasStore.resolve_entity_ids` to its canonical `Entity`).
   - Returns `EntityLineageReport` containing the canonical `entity`, `provenance`, direct absorbed `aliases`, incident `relationships`, deduplicated supporting `chunks` across both canonical and absorbed alias IDs, and sorted unique `source_ids`.

3. **Tenant Corpus Health & Coverage Reporting (`analyze_corpus`, `Redstring.analyze_corpus`)**:
   - Returns `CorpusHealthReport` with `total_entities`, `total_relationships`, `unique_source_count`, `total_chunks`, `linked_chunks`, `unlinked_chunks`, `embedded_chunks`, `unembedded_chunks`, `min_chunk_chars`, `max_chunk_chars`, `mean_chunk_chars`, `entities_with_chunks`, `entity_chunk_coverage_ratio`, and `elapsed_ms`.

## What this does not do

- **Cross-Tenant Scanning**: All lineage and corpus health operations are strictly scoped to a single `TenantId`.

## Checkable Outcomes

1. Calling `trace_source_lineage(source_id, tenant_id, ...)` returns all entities, relationships, and chunks from `source_id` with `linked_chunk_count + unlinked_chunk_count == len(chunks)`, and zero artifacts from other sources or tenants.
2. Calling `trace_entity_lineage(entity_or_alias_id, tenant_id, ...)` resolves absorbed aliases to the canonical entity and aggregates supporting chunks from both canonical and absorbed alias IDs.
3. Calling `analyze_corpus(tenant_id, ...)` returns a `CorpusHealthReport` satisfying `linked_chunks + unlinked_chunks == total_chunks`, `embedded_chunks + unembedded_chunks == total_chunks`, and `0.0 <= entity_chunk_coverage_ratio <= 1.0`.

## Linked User Stories

- [`US-0026`](../../user_stories/accepted/us-0026-reverse-source-and-entity-provenance-lineage-tracin.md): Reverse Source and Entity Provenance Lineage Tracing
- [`US-0027`](../../user_stories/accepted/us-0027-tenant-corpus-health-and-entity-to-chunk-coverage-r.md): Tenant Corpus Health and Entity-to-Chunk Coverage Reporting
