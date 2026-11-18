# User Personas

Archetypes representing the core users, operators, and developers interacting with redstring.

---

## 1. Alex — The Knowledge Graph Architect
- **Role**: Software engineer and GraphRAG application architect.
- **Pain Points**:
  - Context blindness when trying to answer cross-document questions using standard vector search.
  - Fragile graph stores that require destructive database migrations when prompts change.
  - Fractured entity nodes split across lexical aliases ("Ada Lovelace" vs "Lovelace, A.").
- **Goals with redstring**:
  - Clean LLM-driven entity and relationship extraction emitting immutable domain events.
  - Automatic alias consolidation and deterministic property merge resolution.
  - Multi-channel hybrid retrieval combining vector similarity, lexical search, and graph traversal.

---

## 2. Jordan — The Data Platform Lead
- **Role**: Data platform engineer managing document pipelines and persistent store infrastructure.
- **Pain Points**:
  - High operational burden syncing multiple databases (Neo4j, pgvector, PostgreSQL) during pipeline failures.
  - Inability to safely wipe or rebuild tenant data without risking cross-tenant data leaks.
- **Goals with redstring**:
  - Event-sourced write model where read projections can be safely cleared and replayed from sequence zero.
  - Pluggable storage adapters conforming to strict port contracts with zero leaky abstractions.
  - Multi-tenant isolation enforced at the port boundary with zero cross-tenant reads or writes.

---

## 3. Morgan — The Autonomous Coding Agent
- **Role**: LLM-powered coding worker contributing features, bug fixes, and refactors to the redstring repository.
- **Pain Points**:
  - Ambiguous task requirements lacking explicit domain invariants and Definition of Done.
  - Breaking public API contracts or violating file length limits (<500 lines).
- **Goals with redstring**:
  - Explicit task specifications citing governing ADRs, PRDs, and executable BDD user stories.
  - Strict worktree isolation preventing merge conflicts across concurrent workers.
  - Blackbox frontdoor test suites verifying contracts without private internal mocks.

---

## 4. Riley — The Open-Source Library Maintainer
- **Role**: Core maintainer responsible for releases, code quality, and architectural integrity.
- **Pain Points**:
  - Silent version declaration drift between `pyproject.toml` and `__init__.py`.
  - Stale Diataxis documentation and out-of-date code examples in published guides.
  - Untracked architectural decisions and creeping file length sprawl.
- **Goals with redstring**:
  - Strict preflight verification gates asserting 100% test passes and zero file length violations.
  - Automated documentation drift audits ensuring all CLI and code snippets remain verified.
  - Bidirectional traceability connecting user personas, stories, tasks, and commits.

---

## 5. Priya — The Domain Ontologist & Subject-Matter Expert
- **Role**: Knowledge engineer and domain specialist designing ontology schemas, vocabulary constraints, and prompt guidance across specialized verticals (biomedical, legal, finance, fiction).
- **Pain Points**:
  - Generic, unconstrained LLM extraction invents inconsistent entity types and predicate verbs across documents.
  - Rigid code-level schemas require developer intervention whenever domain vocabularies or confidence thresholds evolve.
  - Long multi-chunk documents lose track of core entities introduced in earlier passages or miss dense factual relationships on a single pass.
- **Goals with redstring**:
  - Declarative YAML `DomainSchema` specifications (`load_schema_from_string`, `load_schema_from_file`) mapped to Schema.org vocabularies with configurable confidence thresholds.
  - Automatic zero-config document domain routing (`domain=AUTO`) with transparent classification confidence (`domain_confidence`) and optional strict vocabulary enforcement (`constrain_to_domain=True`).
  - Multi-pass iterative gleaning (`gleanings`) and cross-chunk entity carryover (`carryover_entities`) to maximize recall on dense texts.

---

## 6. Elena — The Temporal & Investigative Intelligence Analyst
- **Role**: Investigative analyst and research lead reconstructing chronological timelines, causal chains, and high-level thematic narratives across large document corpora.
- **Pain Points**:
  - Naive date range queries fail on imprecise dates ("2023", "19th century") or open-ended uncertainty markers (`BEFORE`, `AFTER`, `CIRCA`).
  - Persisting derived temporal edges in the database leads to silent staleness whenever documents are re-extracted with improved models.
  - Inability to see the forest for the trees in large graphs without paying for LLM calls proportional to document count rather than community structure.
  - Lack of explainable connection paths and portable graph exports (Mermaid, GraphML, JSON-LD, Cypher) to share findings with stakeholders.
- **Goals with redstring**:
  - Precision-aware timeline reconstruction (`TemporalQuery.timeline`, `entities_in_interval`) and Allen interval algebra (`infer_relations`) computed on read without polluting the event log.
  - Structure-scaled community detection and theme synthesis (`summarize_themes`) enriched with highest-degree entities and supporting source passages.
  - Explainable shortest-path subgraph citations connecting entities with source chunk provenance, plus zero-dependency graph serialization and visualization exports.

---

## 7. Marcus — The Production AI & LLM Reliability Engineer
- **Role**: Site reliability and AI platform engineer operating high-throughput LLM extraction and embedding pipelines under strict latency, rate-limit, and cost budgets.
- **Pain Points**:
  - Unbounded concurrent LLM requests trigger upstream HTTP 429 rate limits or knock over single-GPU local model servers.
  - Cascading model outages stall entire worker pools when failing endpoints are retried blindly without circuit breaking or jittered backoff.
  - Fixed-window rate counters allow double-burst traffic spikes across minute boundaries, and closing one shared cache client inadvertently tears down sibling resilience components.
- **Goals with redstring**:
  - Sliding-window per-tenant rate limiting (`RateLimiter`) and three-state circuit breaking (`CircuitBreaker` with `CLOSED`, `OPEN`, `HALF_OPEN` probing) backed by pluggable `Cache` ports (`MemoryCache`, `RedisCache`).
  - Exponential backoff with jitter (`LlmRetryPolicy`, `with_retry`) and shared endpoint concurrency ceilings (`CallLimiter`) across extraction, classification, consolidation, and theme summarization.
  - Caller-owned cache lifecycle guarantees where shared `Cache` instances are never prematurely closed by borrowers, paired with fault-tolerant batch ingestion (`continue_on_error=True`).

---

## 8. Nadia — The Compliance & Multi-Tenant Governance Officer
- **Role**: Data governance, privacy, and security architect responsible for tenant isolation, right-to-be-forgotten erasure, audit provenance, and non-destructive merge reversal.
- **Pain Points**:
  - Cross-tenant data leakage in shared graph, vector, or chunk stores during multi-tenant retrieval or projection replay.
  - Irreversible entity merges that permanently corrupt graph topology when an automated adjudicator or human operator makes a bad merge decision.
  - Unverified or mismatched vector embeddings persisting after model upgrades or incomplete right-to-be-forgotten tenant purges.
- **Goals with redstring**:
  - Cryptographically and structurally enforced `TenantId` isolation across every port (`GraphStore`, `VectorStore`, `ChunkStore`) with complete, verifiable tenant purge (`delete_by_tenant`) and scoped replay (`rebuild_tenant`).
  - Event-sourced merge audit logs (`ConsolidationLog`) enabling deterministic, single-ID merge reversal (`Consolidator.undo` emitting `MergeUndone`) that restores absorbed entities and original relationship endpoints.
  - Strict immutable `Provenance` records on every entity and vector (`VectorProvenance`), automatically detecting model or dimension drift (`VectorProvenanceMismatchError`, `TaskPrefixMismatchError`).

---

## 9. Mateo — The Knowledge Federation & Interoperability Engineer
- **Role**: Data federation and semantic interoperability engineer integrating external knowledge bases, partner subgraphs, and archival graph snapshots across environments.
- **Pain Points**:
  - One-way serialization traps: `export_graph` emits standards-compliant JSON-LD (`@context` / `@graph`) and GraphML XML, but without a symmetric `import_graph` or parser, hydrating exported snapshots back into a tenant's `EntityWriter` and `RelationshipStore` requires re-running expensive LLM extraction.
  - All-or-nothing tenant exports: Exporting an entire multi-thousand-entity tenant graph into Mermaid or GraphML overwhelms diagram renderers when an engineer only wants a focused subgraph around a focal entity (`center_entity_id` + `max_depth`) or filtered by `entity_types` and `relationship_types`.
- **Goals with redstring**:
  - Lossless, idempotent round-trip graph hydration (`import_graph`, `parse_json_ld`, `parse_graphml`, and `Redstring.import_graph`) that parses JSON-LD and GraphML payloads into domain `Entity` and `Relationship` objects, upserts them into any `EntityWriter` + `RelationshipStore`, and reports exact counts and latency (`GraphImportReport`).
  - Filtered subgraph export on `export_graph` and `Redstring.export_graph` supporting `entity_types`, `relationship_types`, and k-hop ego-network extraction (`center_entity_id` / name + `max_depth`).

---

## 10. Soren — The Network Scientist & Graph Topology Auditor
- **Role**: Computational network scientist and graph analytics lead auditing structural health, hub centrality, fragmentation, and longitudinal graph evolution across extraction runs.
- **Pain Points**:
  - Structural blindness prior to LLM summarization: Running `summarize_themes` invokes LLM calls per community, but analysts cannot cheaply inspect zero-LLM structural health metrics first (entity/relationship counts, type histograms, connected component fragmentation, isolated singletons, degree distribution, and top-$k$ hub entities).
  - Unquantified graph mutations: After ingesting new document batches, running entity consolidation, or undoing merges, there is no structural diff tool to compare two tenants or before/after snapshots and see added, removed, and modified entities and relationships.
- **Goals with redstring**:
  - Deterministic, zero-LLM topology diagnostics (`analyze_topology`, `Redstring.analyze_topology`) returning an immutable `GraphTopologyReport` with type histograms, connected component counts, singleton counts, degree distribution stats, top-$k$ `EntityHub` rankings, and execution latency (`elapsed_ms`).
  - Structural graph comparison (`diff_graphs`, `Redstring.diff_graphs`) computing an exact `GraphDiffReport` between two tenants or stores (added/removed/modified entities and relationships with field-level entity diffing and timing metrics).

---

## 11. Tessa — The Search Relevance & Retrieval Diagnostics Engineer
- **Role**: Information retrieval (IR) engineer and search quality specialist tuning hybrid GraphRAG pipelines across semantic vector and lexical ranking channels.
- **Pain Points**:
  - Opaque Reciprocal Rank Fusion (RRF): `Retriever.retrieve` and `ChunkRetriever.retrieve_chunks` return fused scores alongside raw `semantic` and `lexical` floats, but conceal each item's 0-based channel rank, its exact RRF contribution (`1 / (60 + rank + 1)`), and which channel dominated the final ordering.
  - Invisible recall gates and BM25 term contributions: When an entity or chunk ranks unexpectedly, engineers cannot easily inspect which `query_blocking_keys(query)` matched, which dangling vector IDs were dropped, or the per-term BM25 score breakdown (`term_bm25_contributions`) and `CorpusStats`.
- **Goals with redstring**:
  - Deterministic entity retrieval diagnostics (`explain_entity_retrieval` -> `EntityRetrievalExplanation`) exposing per-channel 0-based ranks, raw scores, exact RRF score contributions, matched blocking keys, candidate pool counts, and skipped dangling vector IDs.
  - Deterministic passage retrieval diagnostics (`explain_chunk_retrieval` -> `ChunkRetrievalExplanation`) exposing tokenized query terms, `CorpusStats`, per-term BM25 contribution breakdowns (`term_bm25_contributions`), semantic vs. lexical channel ranks, and RRF contributions.

---

## 12. Liam — The Provenance Lineage & Corpus Quality Auditor
- **Role**: Knowledge graph quality lead, legal discovery auditor, and RAG observability engineer responsible for end-to-end provenance traceability and corpus coverage health.
- **Pain Points**:
  - One-way extraction blindness: While `build_graph` and `index_documents` map `SourceDocument -> Entity + Relationship + StoredChunk`, callers have no built-in inverse query to ask what exact entities, relationships, and linked/unlinked chunks a given `SourceId` produced across the tenant.
  - Fragmented entity evidence after consolidation: Reconstructing the complete evidence dossier for a canonical entity requires manually stitching together `Entity.provenance`, `AliasStore.find_aliases`, `RelationshipStore.get_relationships`, and `ChunkReader.get_by_entity` across both canonical and absorbed alias IDs.
  - Blind corpus coverage: Operators cannot easily quantify linked vs. unlinked chunks, embedded vs. unembedded chunks, character length distributions, and entity-to-chunk coverage ratios.
- **Goals with redstring**:
  - Source-to-artifact lineage (`trace_source_lineage` -> `SourceLineageReport`) returning every `Entity`, `Relationship`, and `StoredChunk` originating from or citing a given `SourceId`.
  - Entity-to-evidence reverse lineage (`trace_entity_lineage` -> `EntityLineageReport`) resolving canonical or alias IDs and assembling `Provenance`, absorbed `Alias` records, incident `Relationship`s, supporting `StoredChunk` passages, and deduplicated originating `SourceId`s.
  - Tenant-scoped corpus health and coverage diagnostics (`analyze_corpus` -> `CorpusHealthReport`).

---

## 13. Kieran — The Knowledge Graph Steward & Ontology QA Curator
- **Role**: Data quality steward, entity resolution curator, and ontology governance lead responsible for deduplication threshold calibration, tenant-wide consolidation sweeps, and post-extraction `DomainSchema` conformance verification.
- **Pain Points**:
  - Calling `Consolidator.resolve(subject)` immediately mutates the graph and emits `EntitiesMerged` events, making it impossible to preview candidate pairs across `MERGE`, `ADJUDICATE`, and `REJECT` bands without mutating state.
  - Consolidating an entire tenant requires manual pagination loops and alias resolution boilerplate.
  - `DomainSchema` shapes extraction prompts, but no composed audit checks stored tenant graphs for off-schema entity/relationship types, violated endpoint constraints (`valid_source_types` / `valid_target_types`), or missing `required: true` properties, nor renders a `DomainSchema` ontology itself as a Mermaid diagram.
- **Goals with redstring**:
  - Non-destructive consolidation dry-run preview (`preview_consolidation` -> `ConsolidationPreview`) and one-call tenant-wide consolidation sweep (`consolidate_tenant` -> `TenantConsolidationReport`).
  - Post-extraction schema conformance auditing (`audit_schema_conformance` -> `SchemaConformanceReport`) and deterministic ontology schema Mermaid visualization (`format_schema_mermaid`).
