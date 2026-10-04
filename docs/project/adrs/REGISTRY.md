# ADR Registry

This registry tracks all Architectural Decision Records (ADRs) governing `redstring`, divided into SpecOps System / SDLC Guardrails (ADR-0001 through ADR-0010) and Core Domain / Architecture Decisions (ADR-0101 through ADR-0147).

---

## SpecOps System & SDLC Guardrails

| ID | Title | Status | Scope |
|---|---|---|---|
| [ADR-0001](accepted/adr-0001-specification-as-code-architecture.md) | Specification as Code and Opinionated SDLC Guardrails | Accepted | `core` |
| [ADR-0002](accepted/adr-0002-modular-file-length-limits-anti-rot.md) | Modular Source File Length Limit (<500 Lines Anti-Rot Rule) | Accepted | `core` |
| [ADR-0003](accepted/adr-0003-blackbox-frontdoor-verification.md) | Blackbox Frontdoor Verification and Zero Backdoor Testing | Accepted | `core` |
| [ADR-0004](accepted/adr-0004-continuous-preflight-and-self-healing-ci.md) | Continuous Pre-flight Verification and Self-Healing CI Loops | Accepted | `core` |
| [ADR-0005](accepted/adr-0005-worktree-concurrency-and-backlog-isolation.md) | Git Worktree Concurrency and Strict Backlog Isolation | Accepted | `core` |
| [ADR-0006](accepted/adr-0006-bdd-gherkin-user-stories-and-playwright-e2e.md) | Behavior-Driven Development (BDD) with Gherkin User Stories and Playwright | Accepted | `core` |
| [ADR-0007](accepted/adr-0007-domain-driven-design-and-bounded-contexts.md) | Domain-Driven Design (DDD) Layering and Explicit Bounded Contexts | Accepted | `core` |
| [ADR-0008](accepted/adr-0008-zero-trust-worker-process-sandboxing.md) | Zero-Trust Autonomous Worker Process Sandboxing | Accepted | `core` |
| [ADR-0009](accepted/adr-0009-immutable-supply-chain-lockfile-enforcement.md) | Immutable Supply-Chain Lockfile Enforcement | Accepted | `core` |
| [ADR-0010](accepted/adr-0010-secret-scanning-and-credential-leak-defense.md) | Real-Time Secret Scanning and Credential Leak Defense | Accepted | `core` |

---

## Redstring Core Domain & Engine Architecture Decisions

The architectural decisions governing Redstring's domain models, store ports, and event-sourced projections (mapped from legacy ADRs 0001 through 0047).

| ID | Legacy | Title | Status | Bounded Context |
|---|---|---|---|---|
| [ADR-0101](accepted/adr-0101-event-log-schema-and-granularity.md) | 0001 | The event log's schema, granularity, and aggregates | Accepted | `events` |
| [ADR-0102](accepted/adr-0102-two-store-ports.md) | 0002 | Two store ports, and the absence of delete_entity | Accepted | `ports` |
| [ADR-0103](accepted/adr-0103-blocking-keys-as-nodes.md) | 0003 | Blocking keys are Neo4j nodes, not a list property | Accepted | `graph` |
| [ADR-0104](accepted/adr-0104-consolidation-emits-events.md) | 0004 | Consolidation emits events rather than writing | Accepted | `consolidation` |
| [ADR-0105](accepted/adr-0105-temporal-inference-on-read.md) | 0005 | Temporal inference is computed on read | Accepted | `temporal` |
| [ADR-0106](accepted/adr-0106-the-public-surface-is-gated.md) | 0006 | The public surface is gated by three tests, not curated | Accepted | `core` |
| [ADR-0107](accepted/adr-0107-composition-is-the-only-top-layer.md) | 0007 | composition is the only top layer, and build_graph writes without a log | Accepted | `composition` |
| [ADR-0108](accepted/adr-0108-the-two-non-store-ports.md) | 0008 | The two non-store ports, Cache and LlmProvider | Accepted | `ports` |
| [ADR-0109](accepted/adr-0109-the-extraction-fold-resolves-through-aliases.md) | 0009 | The extraction fold resolves endpoints through the alias table | Accepted | `extraction` |
| [ADR-0110](accepted/adr-0110-one-total-order-for-preference.md) | 0010 | One total order decides which mapping of a thing survives | Accepted | `temporal` |
| [ADR-0111](accepted/adr-0111-domain-schemas-prompt-but-do-not-constrain.md) | 0011 | Domain schemas prompt the model, they do not constrain it | Accepted | `extraction` |
| [ADR-0112](accepted/adr-0112-no-ann-index-in-a-multi-tenant-vector-store.md) | 0012 | No ANN index in a multi-tenant vector store | Accepted | `vector` |
| [ADR-0113](accepted/adr-0113-resilience-behind-the-cache-port.md) | 0013 | Resilience lives in llm/, over the Cache port | Accepted | `llm` |
| [ADR-0114](accepted/adr-0114-exemption-lists-are-empty-and-must-stay-falsifiable.md) | 0014 | Exemption lists are empty, and an emptied one is deleted rather than kept | Accepted | `core` |
| [ADR-0115](accepted/adr-0115-consolidation-gets-a-composed-entry-point.md) | 0015 | Consolidation gets a composed entry point, and an absent graph signal stops meaning zero | Accepted | `consolidation` |
| [ADR-0116](accepted/adr-0116-graph-store-is-five-capabilities.md) | 0016 | GraphStore is five capability protocols, composed | Accepted | `ports` |
| [ADR-0117](accepted/adr-0117-the-embedding-provider-port.md) | 0017 | The embedding provider is a port, and it declares its dimension | Accepted | `ports` |
| [ADR-0118](superseded/adr-0118-a-replay-report-carries-its-failures.md) | 0018 | A replay report carries its failures, and the read can be scoped | Superseded (by ADR-0120) | `projections` |
| [ADR-0119](accepted/adr-0119-batch-relationship-writes-are-atomic.md) | 0019 | Batch relationship writes are atomic | Accepted | `graph` |
| [ADR-0120](accepted/adr-0120-the-replay-driver-goes-upstream.md) | 0020 | The replay driver goes upstream, and is not re-exported back | Accepted | `projections` |
| [ADR-0121](accepted/adr-0121-composition-holds-a-second-module.md) | 0021 | composition holds a second module, and retrieval is what it composes | Accepted | `composition` |
| [ADR-0122](accepted/adr-0122-the-lexical-channel-is-not-bm25.md) | 0022 | The lexical channel is not BM25, and its recall is bounded by blocking | Accepted | `composition` |
| [ADR-0123](accepted/adr-0123-the-chunk-corpus.md) | 0023 | The chunk corpus, and what a stored passage knows about the graph | Accepted | `chunks` |
| [ADR-0124](accepted/adr-0124-bm25-over-the-chunk-corpus.md) | 0024 | BM25 over the chunk corpus, scored in the domain | Accepted | `domain` |
| [ADR-0125](accepted/adr-0125-consolidation-substitution-is-two-protocols.md) | 0025 | Consolidation's two substitution points are protocols, not classes | Accepted | `consolidation` |
| [ADR-0126](accepted/adr-0126-chunk-store-and-cache-are-capabilities-too.md) | 0026 | ChunkStore and Cache are composed from capabilities, like GraphStore | Accepted | `ports` |
| [ADR-0127](accepted/adr-0127-vector-store-is-three-capabilities-and-so-is-every-collaborator.md) | 0027 | VectorStore is three capabilities, and the collaborators are narrowed to match | Accepted | `ports` |
| [ADR-0128](accepted/adr-0128-a-capability-declares-its-own-release.md) | 0028 | A capability declares its own release | Accepted | `ports` |
| [ADR-0129](accepted/adr-0129-a-chunk-is-not-extracted-alone.md) | 0029 | A chunk is not extracted alone | Accepted | `extraction` |
| [ADR-0130](accepted/adr-0130-a-domain-schema-may-constrain-when-asked.md) | 0030 | A domain schema may constrain, when asked | Accepted | `extraction` |
| [ADR-0131](accepted/adr-0131-extraction-does-not-think.md) | 0031 | Extraction does not think | Accepted | `llm` |
| [ADR-0132](accepted/adr-0132-the-id-names-are-newtypes.md) | 0032 | The id names are NewTypes | Accepted | `domain` |
| [ADR-0133](accepted/adr-0133-the-compliance-suites-ship.md) | 0033 | The compliance suites ship | Accepted | `testing` |
| [ADR-0134](accepted/adr-0134-neighbours-are-compared-by-name.md) | 0034 | Neighbours are compared by name, because ids are namespaced by document | Accepted | `consolidation` |
| [ADR-0135](accepted/adr-0135-provenance-is-a-value-object.md) | 0035 | Provenance is a value object, and a strategy is named for the question it can answer | Accepted | `domain` |
| [ADR-0136](accepted/adr-0136-a-merge-resolves-the-canonical-entitys-fields.md) | 0036 | A merge resolves the canonical entity's fields | Accepted | `consolidation` |
| [ADR-0137](accepted/adr-0137-one-exception-for-a-dimension-mismatch.md) | 0037 | One exception type for a dimension mismatch | Accepted | `composition` |
| [ADR-0138](accepted/adr-0138-the-chunks-vector-lives-on-the-chunk.md) | 0038 | The chunk's vector lives on the chunk | Accepted | `chunks` |
| [ADR-0139](accepted/adr-0139-bounded-concurrency-over-chunks.md) | 0039 | Bounded concurrency over chunks | Accepted | `extraction` |
| [ADR-0140](accepted/adr-0140-overlap-aware-name-similarity.md) | 0040 | Overlap-aware name similarity | Accepted | `consolidation` |
| [ADR-0141](accepted/adr-0141-the-consolidation-pass-is-decide-then-emit.md) | 0041 | The consolidation pass is decide-then-emit | Accepted | `consolidation` |
| [ADR-0142](accepted/adr-0142-themes-are-recomputed-never-stored.md) | 0042 | Themes are recomputed, never stored | Accepted | `composition` |
| [ADR-0143](accepted/adr-0143-a-query-is-embedded-differently-from-a-document.md) | 0043 | A query is embedded differently from a document | Accepted | `ports` |
| [ADR-0144](accepted/adr-0144-a-chunk-id-is-derived-not-supplied.md) | 0044 | A chunk id is derived, not supplied | Accepted | `chunks` |
| [ADR-0145](accepted/adr-0145-a-lexical-only-retriever-is-a-constructor.md) | 0045 | A lexical-only retriever is a constructor, not an omitted argument | Accepted | `composition` |
| [ADR-0146](accepted/adr-0146-a-chunk-write-reports-what-it-added.md) | 0046 | A chunk write reports what it added, and a reader answers 'which of these do I have?' | Accepted | `chunks` |
| [ADR-0147](accepted/adr-0147-four-adapter-paths-are-stable.md) | 0047 | Four adapter import paths are stable, without being exported | Accepted | `ports` |
