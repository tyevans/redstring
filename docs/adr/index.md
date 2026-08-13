# Decisions

Architecture decision records: the choices that are expensive to revisit, each
with the alternative that was rejected and what rejecting it cost.

An ADR here is not a design doc. It exists when a reader would otherwise
reasonably conclude the code is wrong — a layer holding one module, a store
port with no `delete_entity`, a vector table with no index. Every one of those
looks like an oversight and is a decision, and the ADR is what tells them
apart.

## Conventions

**Bodies carry no counts and no file tables.** Numbers decay the next time
anyone touches the tree, so they live in the commit message, which is
immutable and correctly scoped to a moment.

**Bodies are immutable records.** A decision that changes gets a *new* ADR and
a "Superseded by" pointer on the old one's Status; a Decision section is never
rewritten to match what the code does now.

**Numbers are allocated at merge, not at drafting.** Parallel branches
routinely draft the same next number. This directory once carried eight files
numbered `0007-*` for exactly that reason, and renumbering them left seven
titles and 43 inbound links wrong for several slices — which is why the site
builds with `mkdocs --strict`, so a citation to a missing page is now a build
failure rather than a silent one.

## The records

| ADR | Settles |
|---|---|
| [0001 · Event log schema and granularity](0001-event-log-schema-and-granularity.md) | What is persisted, at what granularity, owned by which aggregate. **The one irreversible decision here** — a log already written cannot be refactored. |
| [0002 · Two store ports](0002-two-store-ports.md) | Why `GraphStore` and `VectorStore` are separate, why neither has `delete_entity`, and the alias surface that makes the absence safe. |
| [0003 · Blocking keys as nodes](0003-blocking-keys-as-nodes.md) | Blocking keys are Neo4j nodes rather than a list property — settled by measurement, with the cheaper-looking alternative recorded as already tried. |
| [0004 · Consolidation emits events](0004-consolidation-emits-events.md) | Consolidation decides and emits; a projection writes. What collapsing the two would cost in auditability. |
| [0005 · Temporal inference on read](0005-temporal-inference-on-read.md) | Inferred temporal edges are computed on read and never emitted into `DocumentExtracted`. |
| [0006 · The public surface is gated](0006-the-public-surface-is-gated.md) | `__all__` is the whole promise, held by three tests each blind to what the other two catch. |
| [0007 · `composition` is the only top layer](0007-composition-is-the-only-top-layer.md) | Why a layer holds one module, and why `build_graph` writes without a log. |
| [0008 · The two non-store ports](0008-the-two-non-store-ports.md) | `Cache` and `LlmProvider`: what each promises, and what an adapter is expected to absorb. |
| [0009 · The extraction fold resolves through aliases](0009-the-extraction-fold-resolves-through-aliases.md) | The fold's half of 0002's contract: resolve before write, and why a collapsed edge is deleted rather than upserted. |
| [0010 · One total order for preference](0010-one-total-order-for-preference.md) | Which mapping of a thing survives, decided by one total order rather than three tie-breaks that disagree. |
| [0011 · Domain schemas prompt but do not constrain](0011-domain-schemas-prompt-but-do-not-constrain.md) | A schema shapes the prompt and validates nothing; an off-schema entity is not an error. |
| [0012 · No ANN index in a multi-tenant vector store](0012-no-ann-index-in-a-multi-tenant-vector-store.md) | Why pgvector carries no `hnsw` or `ivfflat` index, and what one does to `tenant_id` filtering. |
| [0013 · Resilience behind the cache port](0013-resilience-behind-the-cache-port.md) | Retry, rate limiting and circuit breaking live in `llm/` over `Cache`, not in the pipeline. |
| [0014 · Exemption lists are empty and must stay falsifiable](0014-exemption-lists-are-empty-and-must-stay-falsifiable.md) | Every exemption list needs a test that its entries still match something — and an emptied *exclusion* is deleted rather than kept. |
| [0015 · Consolidation gets a composed entry point](0015-consolidation-gets-a-composed-entry-point.md) | `Consolidator` composes decide-and-emit with project-and-write; an empty-vs-empty neighbour comparison stops meaning zero. |
| [0016 · `GraphStore` is five capabilities](0016-graph-store-is-five-capabilities.md) | An eighteen-method port becomes five composed protocols, because a fat interface pushed a test double into subclassing a real adapter. |
| [0017 · The embedding provider port](0017-the-embedding-provider-port.md) | `VectorStore` had no way to be filled from inside the library. A third provider port, declaring the dimension it produces, checked against the store's before anything is embedded. |
| [0018 · A replay report carries its failures](0018-a-replay-report-carries-its-failures.md) | A replay names the events it dropped and can scope its read to one tenant; `failed` is derived from `failures` so the two cannot disagree. |
| [0019 · Batch relationship writes are atomic](0019-batch-relationship-writes-are-atomic.md) | `upsert_relationships` is all-or-nothing. The two adapters had already disagreed about what a failure left behind, and nothing asserted it. |
| [0020 · The replay driver goes upstream](0020-the-replay-driver-goes-upstream.md) | `replay` and `StoreProjection` were written here, reported upstream, and shipped in `eventsource-py` 0.12.0. Adopted and **not** re-exported — supersedes 0018. |
| [0021 · `composition` holds a second module](0021-composition-holds-a-second-module.md) | `retrieval` joins `build_graph` on the top layer, because `vector`, `graph` and `llm` are siblings and no lower layer may hold all three. Amends 0007. |
| [0022 · The lexical channel is not BM25](0022-the-lexical-channel-is-not-bm25.md) | Why corpus statistics are undefined over entity names, why the channels fuse by rank rather than by a weighted score, and what reusing blocking keys costs in recall. |
| [0023 · The chunk corpus](0023-the-chunk-corpus.md) | Passages are retained, content-addressed rather than positional, and replaced a whole source at a time. Amends 0022's premise and not its decision. |
| [0024 · BM25 over the chunk corpus, scored in the domain](0024-bm25-over-the-chunk-corpus.md) | Scoring is a pure function in `domain/`; adapters supply only recall and corpus statistics, so the two adapters rank identically. Amends 0022's Status, not its Decision. |
| [0025 · Consolidation's substitution points are protocols](0025-consolidation-substitution-is-two-protocols.md) | `resolve`'s `finder` and `adjudicator` are typed against one-method protocols, so substituting them no longer means subclassing a class whose constructor demands collaborators you do not have. Amends 0015's typing, not its Decision. |
| [0026 · `ChunkStore` and `Cache` are capabilities too](0026-chunk-store-and-cache-are-capabilities-too.md) | 0016's argument applied to the two ports that still had the problem: `ChunkStore` had one first-party consumer using one of nine methods. Amends 0008 and 0023 in typing only. |
| [0027 · `VectorStore` is three capabilities](0027-vector-store-is-three-capabilities-and-so-is-every-collaborator.md) | The port 0026 left out, plus the two collaborators `ports/graph_store.py` told everyone to narrow and nobody had. Amends 0002 in typing only. |
| [0028 · A capability declares its own release](0028-a-capability-declares-its-own-release.md) | Every capability protocol inherits `AsyncClosable`, so `async with` is reachable through a port rather than only through the adapter class behind it. Amends 0002 in typing only. |
| [0029 · A chunk is not extracted alone](0029-a-chunk-is-not-extracted-alone.md) | Each chunk's prompt names what earlier chunks found, and a chunk may be shown its own answer and asked what it missed. Both are prompt content, so 0011 stands and 0008 needed no widening. |
| [0030 · A domain schema may constrain, when asked](0030-a-domain-schema-may-constrain-when-asked.md) | `constrain_to_domain=True` turns a domain's type ids into an enum in the decoded schema. Amends 0011, which stays the default; 0008 needed no widening. |
| [0031 · Extraction does not think](0031-extraction-does-not-think.md) | `openai_compatible` sends `enable_thinking: false` by default. 5.7x faster and two thirds fewer false positives, measured. Amends 0008 in consequences only. |
| [0032 · The id names are `NewType`s](0032-the-id-names-are-newtypes.md) | `EntityId`, `RelationshipId`, `TenantId` and `SourceId` become distinct to a type checker and identical at runtime, so transposing `(entity_id, tenant_id)` is a gate failure. Amends 0002 and 0006 in typing only; 0001 needed nothing, because `NewType` has no wire representation. |
| [0033 · The compliance suites ship](0033-the-compliance-suites-ship.md) | The port compliance suites move into the package as `redstring.testing`, behind a `test` extra, so an adapter written elsewhere runs the same bodies this repo does. Amends 0006 with a second gated surface and displaces `composition` from the top of the import contract. |
| [0034 · Neighbours are compared by name](0034-neighbours-are-compared-by-name.md) | Entity ids are namespaced by `source_id`, so a Jaccard over neighbour *ids* scored every cross-document duplicate `0.0` and put `HIGH_SIMILARITY` out of reach. Amends 0015's disjoint-neighbourhood clause; 0009's namespacing stands and is the reason. |
| [0035 · Provenance is a value object](0035-provenance-is-a-value-object.md) | Five fields move off `Entity` onto a `Provenance` carrying a required `observed_at`, and `LATEST` is renamed for the question it can actually answer. The blocker was never a missing timestamp — it was that `resolve` received bare values. Amends 0001's payload shape; extends 0010 by composition. |
| [0036 · A merge resolves the canonical entity's fields](0036-a-merge-resolves-the-canonical-entitys-fields.md) | A merge decides `description`, `external_ids` and `properties` on the canonical entity only, recorded as a before/after pair on `EntitiesMerged` rather than recomputed on read. Strategy selection is a `PropertyMergePolicy` keyed by dotted path; `UNION` outside `properties` is refused at construction. Amends 0001's payload shape; closes what 0035 left deferred. |
| [0037 · One exception type for a dimension mismatch](0037-one-exception-for-a-dimension-mismatch.md) | Every composition entry point that refuses a mismatched embedding provider and store now raises `DimensionMismatchError`, never a bare `ValueError`; a half-configured pair keeps `ValueError`. The gate is introspective over `composition`'s public surface, so a new entry point is covered by construction. |
| [0038 · The chunk's vector lives on the chunk](0038-the-chunks-vector-lives-on-the-chunk.md) | A chunk's embedding is a nullable column on `StoredChunk`, not a second store, read back by the existing `ChunkReader` methods and searched by a new `SemanticCandidateSource` capability. The adapter scores, a stated exception to 0024; the store declares its width at construction; composition embeds. Amends 0023 and 0026. |
