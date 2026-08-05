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
