# Historical Migration and Planning Archive

This directory preserves historical planning blueprints, execution records, and design notes authored during the development and ring migration of `redstring` prior to adopting **SpecOps** (Project Management as Code).

Living specifications, product requirements, user stories, and architecture decision records now live version-locked under `docs/project/`:
- **PRDs**: [`docs/project/product/`](../project/product/)
- **User Stories**: [`docs/project/user_stories/`](../project/user_stories/)
- **ADRs**: [`docs/project/adrs/`](../project/adrs/REGISTRY.md)
- **Backlog**: [`docs/project/backlog/`](../project/backlog/PRIORITY.md)

---

## Historical Ring Migration Records

- [`2026-08-ring-migration-plan.md`](2026-08-ring-migration-plan.md): The overarching ring migration plan restructuring the repository into layered event-sourced architecture.
- [`2026-08-mutation-and-measurement.md`](2026-08-mutation-and-measurement.md): Mutation testing baseline analysis and measurement protocols.

---

## Historical Design Specifications (`specs/`)

Preserved design blueprints from the ring migration:
- `specs/2026-08-06-entity-retrieval-design.md`: Hybrid entity retrieval and graph traversal design.
- `specs/2026-08-07-chunk-lexical-channel-design.md`: BM25 and lexical scoring channel over passages.
- `specs/2026-08-07-chunk-store-design.md`: ChunkStore port and adapter storage architecture.
- `specs/2026-08-12-property-provenance-design.md`: Property provenance value objects and temporal tracking.
- `specs/2026-08-13-chunk-semantic-channel-design.md`: Semantic vector search channel over stored chunks.
- `specs/2026-08-13-ingestion-benchmark-design.md`: Ingestion benchmark harness and progress reporting.
- `specs/2026-08-13-merged-properties-design.md`: Field resolution and merge policies during entity consolidation.

---

## Historical Execution Plans (`plans/`)

Task-level execution blueprints executed during historical development sprints:
- `plans/ring-migration.md`: Ring migration execution breakdown.
- `plans/2026-08-06-entity-retrieval.md`: Entity retrieval implementation plan.
- `plans/2026-08-07-chunk-lexical-channel.md`: Lexical channel implementation plan.
- `plans/2026-08-07-chunk-store.md`: ChunkStore implementation plan.
- `plans/2026-08-12-property-provenance.md`: Property provenance implementation plan.
- `plans/2026-08-13-benchmark-harness.md`: Benchmark harness implementation plan.
- `plans/2026-08-13-bounded-concurrency.md`: Bounded chunk concurrency implementation plan.
- `plans/2026-08-13-chunk-semantic-channel.md`: Semantic channel implementation plan.
- `plans/2026-08-13-merged-properties.md`: Merged properties implementation plan.
- `plans/2026-08-14-consolidation-recall-and-throughput.md`: Consolidation recall tuning plan.
- `plans/2026-08-20-stored-chunk-id-is-derived.md`: Derived chunk ID implementation plan.
