---
paths:
  - "src/**/*.py"
  - "tests/**/*.py"
  - "docs/**/*.md"
---

# Definition of Done

## Deferred work (applies to ALL work)

Nothing is complete while something you noticed sits only in your head, a TODO
comment, or a chat message. It goes in `BACKLOG.md`, in the same commit that
passes it by, written so someone picking it up cold does not have to
rediscover what you already know. See `CLAUDE.md` — this is the project's
hardest rule and it has no exceptions.

## Architectural decisions (applies to ALL work)

No work is complete if the decisions it made or changed are not documented:

1. **No design spec or plan is complete** until it has been run against the
   existing ADRs in `docs/adr/`. A spec should say, for each related ADR,
   whether it **stands**, is **amended**, or is **superseded**.
2. If work amends or supersedes an ADR, that is part of the work, not a
   follow-up: write the new ADR (or amend the old one's Consequences), and
   update the old ADR's **Status** with an "Amended by" / "Superseded by"
   pointer. ADR bodies are immutable records — never rewrite a Decision
   retroactively; supersede it.
3. New architecturally significant decisions get their own ADR in
   `docs/adr/`, numbered after the current highest. For this project that
   means: changes to the layered import contract in `pyproject.toml`; the
   entity/graph data model; consolidation and merge semantics; anything that
   changes a public contract or a persistence format; and **the shape of the
   `LlmProvider` port in `src/redstring/ports/llm_provider.py` or of the
   plug-in protocols in `src/redstring/extraction/protocols.py`**. That last
   one is what "extraction strategy selection" now means: there is no
   selectable backend to choose between. Extraction calls one narrow port —
   `LlmProvider.extract(text, schema, *, system_prompt)` — and everything a
   chat API makes you think about stops at the adapter in
   `redstring/llm/adapters/`. The only shape extraction itself plugs in is
   `Chunker`; `Preprocessor` and `EntityMerger` were both removed, with the
   reasoning recorded in that module. So widening the port, adding a second
   protocol beside `Chunker`, or bringing a removed one back is an
   architectural decision and needs an ADR; writing a new adapter behind the
   unchanged port is not — see "New port adapter or extraction strategy"
   below.
4. **ADR bodies carry no counts and no file tables.** Those go in the commit
   message, which is immutable and scoped to a moment. See
   `.claude/rules/recurring-defects.md` §5.
5. **ADR numbers are allocated at merge, not at drafting.** Draft under a
   provisional name; re-check `docs/adr/` on current `main` before merging.
   **Do not trust a range written here** -- run the command in
   `recurring-defects.md` §6 instead. This sentence has already gone stale
   once, naming `0014` as the highest while `0015` through `0017` were on
   `main`, which is §5 happening to the rule that warns about §5. The number
   is allocated against the highest already on `main` at the moment the work
   merges — not against
   the highest you saw when you started drafting. Parallel branches routinely
   draft the same next number; the one that merges second renumbers.

   **Renumbering means the title and every inbound citation, not the
   filename.** The eight-way `0007` collision was resolved by renaming files
   to `0008`–`0014` and stopping there: seven H1s still read `# ADR 0007:`,
   and every inbound `](../adr/0007-<slug>.md)` link pointed at a path that no
   longer existed. Nothing failed, because nothing checks. `mkdocs build
   --strict` checks now, which is the mechanism this rule had been missing.

**The ADRs a spec has to be run against.** Item 1 is only actionable if you
know what is already decided, so here is the set, by what each one settles:

| ADR | Settles |
|---|---|
| [`0001` event log schema and granularity](../../docs/adr/0001-event-log-schema-and-granularity.md) | What the persisted events are, at what granularity, and which aggregates own them. The one irreversible decision in the migration — a log already written cannot be refactored. |
| [`0002` two store ports](../../docs/adr/0002-two-store-ports.md) | `GraphStore` and `VectorStore` are the only store ports; why there is no `delete_entity`; the alias surface (`upsert_alias`, `remove_alias`, `find_aliases`, `resolve_entity_ids`) that makes the absence safe. |
| [`0003` blocking keys as nodes](../../docs/adr/0003-blocking-keys-as-nodes.md) | Blocking keys are Neo4j nodes rather than a list property — settled by measurement, and the cheaper-looking alternative is recorded as already tried. |
| [`0004` consolidation emits events](../../docs/adr/0004-consolidation-emits-events.md) | Consolidation decides and emits; a projection writes. Records what collapsing the two back together would cost in auditability. |
| [`0005` temporal inference on read](../../docs/adr/0005-temporal-inference-on-read.md) | Inferred temporal edges are computed on read, never emitted into `DocumentExtracted`. |
| [`0006` the public surface is gated](../../docs/adr/0006-the-public-surface-is-gated.md) | `__all__` is the whole promise, held by three tests each blind to what the other two catch. |
| [`0007` composition is the only top layer](../../docs/adr/0007-composition-is-the-only-top-layer.md) | Why one module occupies the top layer, and why `build_graph` writes without a log. |
| [`0008` the two non-store ports](../../docs/adr/0008-the-two-non-store-ports.md) | `Cache` and `LlmProvider`: what each promises, and what an adapter must absorb. |
| [`0009` the extraction fold resolves through aliases](../../docs/adr/0009-the-extraction-fold-resolves-through-aliases.md) | The fold's half of `0002`'s contract — resolve-before-write, and why a collapsed edge is deleted rather than upserted. |
| [`0010` one total order for preference](../../docs/adr/0010-one-total-order-for-preference.md) | Which mapping of a thing survives, decided by one total order in `domain.preference`. |
| [`0011` domain schemas prompt but do not constrain](../../docs/adr/0011-domain-schemas-prompt-but-do-not-constrain.md) | A schema shapes the prompt; it is not a validator, and an off-schema entity is not an error. |
| [`0012` no ANN index in a multi-tenant vector store](../../docs/adr/0012-no-ann-index-in-a-multi-tenant-vector-store.md) | Why pgvector carries no `hnsw`/`ivfflat` index, and what an index does to `tenant_id`. |
| [`0013` resilience behind the cache port](../../docs/adr/0013-resilience-behind-the-cache-port.md) | Retry, rate limiting and circuit breaking live in `llm/` over `Cache`, not in the pipeline. |
| [`0014` exemption lists are empty and must stay falsifiable](../../docs/adr/0014-exemption-lists-are-empty-and-must-stay-falsifiable.md) | An exemption list needs a test that its entries still match something; an emptied *exclusion* is deleted rather than kept. |
| [`0015` consolidation gets a composed entry point](../../docs/adr/0015-consolidation-gets-a-composed-entry-point.md) | Consolidation's entry point, and why an absent graph signal stopped meaning zero. |
| [`0016` `GraphStore` is five capabilities](../../docs/adr/0016-graph-store-is-five-capabilities.md) | The port is five capability protocols, composed, rather than one flat interface. |
| [`0017` the embedding provider port](../../docs/adr/0017-the-embedding-provider-port.md) | `EmbeddingProvider` is a port and declares its own dimension. |
| [`0018` a replay report carries its failures](../../docs/adr/0018-a-replay-report-carries-its-failures.md) | What a `ReplayReport` names about the events it dropped, and that a replay's read can be scoped to one tenant. **Superseded by 0020** — the driver is `eventsource-py`'s now. |
| [`0019` batch relationship writes are atomic](../../docs/adr/0019-batch-relationship-writes-are-atomic.md) | `upsert_relationships` is all-or-nothing; what the two adapters disagreed about before, and what a new backend now owes. |
| [`0020` the replay driver goes upstream](../../docs/adr/0020-the-replay-driver-goes-upstream.md) | `replay` and `StoreProjection` are `eventsource-py`'s, adopted and **not** re-exported. **Supersedes 0018.** |
| [`0021` `composition` holds a second module](../../docs/adr/0021-composition-holds-a-second-module.md) | `retrieval` joins `build_graph` on the top layer, because `vector`, `graph` and `llm` are siblings and no lower layer may hold all three. Amends 0007. |
| [`0022` the lexical channel is not BM25](../../docs/adr/0022-the-lexical-channel-is-not-bm25.md) | Why corpus statistics are undefined over entity names, why the channels fuse by rank rather than by a weighted score, and what reusing blocking keys costs in recall. Amended by 0023 and 0024. |
| [`0023` the chunk corpus](../../docs/adr/0023-the-chunk-corpus.md) | Passages are retained, content-addressed rather than positional, and replaced a whole source at a time. Amends 0022's premise, not its decision; amended by 0038. |
| [`0024` BM25 over the chunk corpus](../../docs/adr/0024-bm25-over-the-chunk-corpus.md) | Scoring is a pure function in `domain/`; adapters supply only recall and corpus statistics, so the two adapters rank identically. 0038 states an exception for the vector channel. |
| [`0025` consolidation's substitution points are protocols](../../docs/adr/0025-consolidation-substitution-is-two-protocols.md) | `resolve`'s `finder` and `adjudicator` are one-method protocols, so substituting them does not mean subclassing a class whose constructor demands collaborators you do not have. Amends 0015's typing. |
| [`0026` `ChunkStore` and `Cache` are capabilities too](../../docs/adr/0026-chunk-store-and-cache-are-capabilities-too.md) | 0016's argument applied to the two ports that still had the problem. Amends 0008 and 0023 in typing only. **Amended by 0027**, which corrects its claim that these were the two *remaining* ports — `VectorStore` had it too — and by 0038. |
| [`0027` `VectorStore` is three capabilities](../../docs/adr/0027-vector-store-is-three-capabilities-and-so-is-every-collaborator.md) | The port 0026 left out, plus the two collaborators `ports/graph_store.py` told everyone to narrow and nobody had. Amends 0002 in typing only. |
| [`0028` a capability declares its own release](../../docs/adr/0028-a-capability-declares-its-own-release.md) | Every capability protocol inherits `AsyncClosable`, so `async with` is reachable through a port rather than only through the adapter behind it. Deliberately stops at the store-shaped ports — see BACKLOG B107b. |
| [`0029` a chunk is not extracted alone](../../docs/adr/0029-a-chunk-is-not-extracted-alone.md) | Each chunk's prompt names what earlier chunks found, and a chunk may be shown its own answer and asked what it missed. Both are prompt content, so 0011 stands and 0008 needed no widening. |
| [`0030` a domain schema may constrain, when asked](../../docs/adr/0030-a-domain-schema-may-constrain-when-asked.md) | `constrain_to_domain=True` turns a domain's type ids into an enum in the decoded schema. Amends 0011, which stays the default. |
| [`0031` extraction does not think](../../docs/adr/0031-extraction-does-not-think.md) | `openai_compatible` sends `enable_thinking: false` by default — measured 5.7x faster with two thirds fewer false positives. Amends 0008 in consequences only. |
| [`0032` the id names are `NewType`s](../../docs/adr/0032-the-id-names-are-newtypes.md) | `EntityId`, `RelationshipId`, `TenantId` and `SourceId` are distinct to a type checker and identical at runtime, so transposing `(entity_id, tenant_id)` is a gate failure. Amends 0002 and 0006 in typing only; 0001 needed nothing, because `NewType` has no wire representation. |
| [`0033` the compliance suites ship](../../docs/adr/0033-the-compliance-suites-ship.md) | The port compliance suites move into the package as `redstring.testing`, behind a `test` extra, so an adapter written elsewhere runs the same bodies this repo does. Amends 0006 with a second gated surface, and displaces `composition` from the top of the import contract. |
| [`0034` neighbours are compared by name](../../docs/adr/0034-neighbours-are-compared-by-name.md) | Entity ids are namespaced by `source_id`, so a Jaccard over neighbour *ids* scored every cross-document duplicate `0.0`. Amends 0015's disjoint-neighbourhood clause; 0009's namespacing stands and is the reason. |
| [`0035` provenance is a value object](../../docs/adr/0035-provenance-is-a-value-object.md) | Five fields move off `Entity` onto a `Provenance` carrying a required `observed_at`, and `LATEST` is renamed for the question it can answer. The blocker was never a missing timestamp — it was that `resolve` received bare values. **No previously written `DocumentExtracted` payload validates: `event_version = 2`.** Amends 0001's payload shape; extends 0010 by composition. |
| [`0036` a merge resolves the canonical entity's fields](../../docs/adr/0036-a-merge-resolves-the-canonical-entitys-fields.md) | A merge decides `description`, `external_ids` and `properties` on the canonical entity only, recorded as a before/after pair on `EntitiesMerged` rather than recomputed on read. Strategy selection is a `PropertyMergePolicy` keyed by dotted path. |
| [`0037` one exception type for a dimension mismatch](../../docs/adr/0037-one-exception-for-a-dimension-mismatch.md) | Every composition entry point refusing a mismatched provider and store raises `DimensionMismatchError`, never a bare `ValueError`. The gate is introspective over `composition`'s public surface, so a new entry point is covered by construction. |
| [`0038` the chunk's vector lives on the chunk](../../docs/adr/0038-the-chunks-vector-lives-on-the-chunk.md) | A chunk's embedding is a nullable column on `StoredChunk`, not a second store, searched by a new `SemanticCandidateSource` capability. The adapter scores — a stated exception to 0024. Amends 0023 and 0026. |
| [`0039` bounded concurrency over chunks](../../docs/adr/0039-bounded-concurrency-over-chunks.md) | `ExtractionPipeline` and `build_graph` take a `concurrency` bound; chunks extract in wavefront batches with carryover folded in between, and one `CallLimiter` bounds every call against the endpoint. `concurrency=1` is byte-identical to before. Amends 0010's consequences; amended by 0041. |
| [`0040` overlap-aware name similarity](../../docs/adr/0040-overlap-aware-name-similarity.md) | `string_similarity` is the maximum of Jaro-Winkler and a token overlap coefficient capped at `CONTAINMENT_CEILING`, so a title-qualified name reaches the adjudication band. The ceiling sits strictly below `HIGH_SIMILARITY`, so containment alone **can never trigger an automatic merge**. Widening `LOW_SIMILARITY` and adding a fourth feature are both recorded as rejected. |
| [`0041` the consolidation pass is decide-then-emit](../../docs/adr/0041-the-consolidation-pass-is-decide-then-emit.md) | `resolve_many` consolidates a corpus as three phases — score and band concurrently, adjudicate behind a barrier in batches spanning subjects, emit serially. The fan-out cannot be over `resolve` itself, because each merge changes the graph the next subject reads. Widens `MergeAdjudicator`, and moves `CallLimiter` down to `domain/limiter.py` so extraction and consolidation can share one endpoint ceiling. Amends 0039. |
| [`0042` themes are recomputed, never stored](../../docs/adr/0042-themes-are-recomputed-never-stored.md) | A thematic layer above the entity: communities are a function of the graph at an instant, so they are computed per call and written nowhere — no event, no store, no projection. Clustering pages the existing capabilities rather than widening `GraphStore`. |
| [`0043` a query is embedded differently from a document](../../docs/adr/0043-a-query-is-embedded-differently-from-a-document.md) | `EmbeddingProvider` gains `embed_query` beside `embed`, same batch and positional contract; both adapters take `document_prefix` and `query_prefix`, defaulting to empty. Modern models are asymmetric and a one-method port cannot say so, which fails silently — unprefixed vectors are well-formed and merely worse. The document prefix is part of the model's identity, so changing it on a populated store means a new store. Amends 0017. |

Anything touching an event payload, a store port, consolidation, temporal
relations, or `__all__` has a related ADR by construction — say for each one
whether it **stands**, is **amended**, or is **superseded**, rather than
leaving the reader to infer it from silence.

The eight-way `0007` collision this section used to describe is **resolved**:
those drafts are `0007` through `0014`, each with a unique number, a matching
H1, and inbound citations that resolve. See `recurring-defects.md` §6 for how
it happened and what the half-finished renumber cost.

## Recurring defect check (applies to ALL work)

`.claude/rules/recurring-defects.md` lists six defect shapes. No work is
complete without a pass against its quick checklist. The two that gate most
often:

- **A method with sibling implementations changed or added** — the semantics
  belong in one shared test body exercised by every implementation, not in a
  test module named after one of them. A test that names a single adapter
  cannot catch the next one, which is the whole defect shape.

  The multi-implementation contracts in this project are the port compliance
  suites under `src/redstring/testing/`, and they are where such a test goes:

  | Suite | Port | Implementations that run it |
  |---|---|---|
  | `src/redstring/testing/graph_store.py` (`GraphStoreCompliance`) | `GraphStore` | `tests/unit/graph/test_memory_store.py`, `tests/integration/graph/test_neo4j_store.py` |
  | `src/redstring/testing/vector_store.py` (`VectorStoreCompliance`) | `VectorStore` | `tests/unit/vector/test_memory_store.py`, `tests/integration/vector/test_pgvector_store.py` |
  | `src/redstring/testing/chunk_store.py` (`ChunkStoreCompliance`) | `ChunkStore` | `tests/unit/chunks/test_memory_store.py`, `tests/integration/chunks/test_postgres_store.py` |
  | `src/redstring/testing/cache.py` (`CacheCompliance`) | `Cache` | `tests/unit/llm/test_memory_cache.py`, `tests/integration/llm/test_redis_cache.py` |
  | `src/redstring/testing/embedding_provider.py` (`EmbeddingProviderCompliance`) | `EmbeddingProvider` | `tests/unit/llm/test_fake_embedding_provider.py`, `tests/unit/llm/test_langchain_embedding_provider.py`, `tests/integration/llm/test_live_embeddings.py` |

  An adapter opts in by subclassing and supplying one hook (`new_store` for
  the store suites, a `cache` or `provider` fixture for the other two); the
  suite is then run **unchanged**. Editing the shared body to make one adapter pass is
  the defect, not the fix — if the port genuinely permits both behaviours,
  say so in the port and state the weaker contract for everyone.

- **A read method added to `GraphStore` or `VectorStore`** — this one is
  enforced rather than advised, and the enforcement is what makes it worth
  knowing before you write the method. `tests/unit/graph/test_compliance_coverage.py`
  and `tests/unit/vector/test_compliance_coverage.py` derive the read-method
  list **from the Protocol by introspection** and fail until each method has
  both a mutation-isolation test and a tenant-isolation test on the
  compliance class. Follow the naming convention —
  `test_<method>_returns_copies` and `test_<method>_never_crosses_tenants` —
  and neither gate module needs editing at all. (`GraphStore`'s gate carries
  a closed legacy registry for eight pre-convention names; it is checked both
  ways, so an entry naming a method the port no longer has fails too. Do not
  add to it.)

  Behavioural tests do not imply the isolation test: handing back the live
  internal object is correct on every read and wrong only afterwards, so no
  assertion about the returned value can see it. Four read methods shipped
  that way in slice 3 and a mutation run, not review, found each one.

- **New counter, stat, or metric field** — a test asserts it non-zero under
  the condition it counts.

## Quality gates

Do not run ruff, bandit, or `lint-imports` as separate steps. They are wired
into `pre-commit` and run on `git commit`; running them by hand duplicates the
work. Write the change, then commit; the hook reports what is wrong and often
fixes it in place (re-`git add` and commit again when it does).

**pytest is the exception.** No hook runs it any more — `git commit` succeeds
whether or not the suite passes — so run `uv run pytest` yourself before
committing; CI is otherwise the first thing to disagree, on a branch already
pushed. See `CLAUDE.md`.

Work is not done until the commit passes the gate — not until it passes
"except for the hook", and not with a check disabled. A rule you ignored is a
deferral: it goes in `BACKLOG.md` with why ignoring it was correct.

## New feature

1. Implementation under `src/redstring/`, in the correct layer — the
   `lint-imports` contract in `pyproject.toml` is the authority, and a
   cross-layer import means either the code is in the wrong layer or the
   contract needs an explicit, argued change (which is an ADR).
2. Unit tests in `tests/unit/`, mirroring the package path, covering happy
   path and edge cases.
3. `hypothesis` properties wherever a property is easier to state than a table
   of examples.
4. New dependencies added with `uv add` / `uv add --optional <extra>` — never
   by hand-editing `pyproject.toml`.
5. Commit passes the gate, and `uv run pytest` is green with coverage not
   below `.coverage-baseline` — no hook runs the suite any more, so CI is the
   first place a regression would otherwise be caught. Run
   `uv run python scripts/coverage_ratchet.py` if the change raises coverage,
   so the new baseline lands in the same commit; see `BACKLOG.md` B-RATCHET-1.

## Bug fix

1. A failing test that reproduces the bug, **proved red against the pre-fix
   source** via `git checkout HEAD~1 -- <paths>` (not `git stash`).
2. The fix.
3. All existing tests pass.
4. **If the bug was a divergence between two implementations of one
   contract**, the regression test lives in the shared/parametrised suite and
   the per-implementation duplicates it subsumes are deleted.
5. **The assertion is written from the documented contract, not from observed
   output.** A test written from what the code currently prints encodes the
   bug as the spec.
6. If a `BACKLOG.md` entry described this bug, it is deleted in the same
   commit.

## Refactor

1. No behaviour change — existing tests pass **without modification**. A
   refactor that requires editing assertions is not a refactor; say so and
   treat it as a behaviour change.
2. No public API changes unless explicitly intended.
3. Commit passes the gate.

## New port adapter or extraction strategy

There are no extraction "backends" to subclass any more. A new implementation
is an **adapter behind a port**: `GraphStore`, `VectorStore`, `Cache` or
`LlmProvider`, each a Protocol in `src/redstring/ports/`. See
`docs/how-to/implement-a-store-adapter.md` for the walkthrough; this list is
what makes one *done*.

1. **Implements the relevant Protocol in `src/redstring/ports/`**, and lives
   under that port's sibling package (`graph/`, `vector/`, `llm/`) — not under
   `extraction/`. The Protocol is the contract; nothing else is. Widening the
   port to fit an adapter is an architectural decision and needs an ADR (see
   above) — the adapter is supposed to absorb the awkwardness of its backend,
   which is the whole reason `LlmProvider.extract` says nothing about chat
   turns.
2. **Optional dependency guarded with `try`/`except ImportError`** if it pulls
   a heavy or optional package, re-raised with a message naming the extra to
   install. `RedisCache.from_url` and `LangChainProvider` both do this; copy
   the shape rather than letting a bare `ImportError` reach the caller.
3. **Subclasses the parametrised compliance suite in `src/redstring/testing/` and
   runs it unchanged** — `GraphStoreCompliance`, `VectorStoreCompliance` or
   `CacheCompliance`. Opting in is one hook: `new_store` for the store suites,
   a `cache` fixture for `CacheCompliance`. An adapter with only bespoke tests
   diverges silently from its siblings; that is defect shape §1 and the single
   most expensive shape in this list. Editing the shared body to make your
   adapter pass is the defect, not the fix.

   For `GraphStore` and `VectorStore` this is *enforced*: if the change also
   adds a read method to the Protocol, the coverage gates
   (`tests/unit/graph/test_compliance_coverage.py`,
   `tests/unit/vector/test_compliance_coverage.py`) derive the read-method
   list by introspection and fail until that method has both a
   mutation-isolation test (`test_<method>_returns_copies`) and a
   tenant-isolation test (`test_<method>_never_crosses_tenants`) on the
   compliance class. Follow the naming convention and neither gate module
   needs touching. `Cache` has no such introspective gate — every `Cache`
   adapter runs `CacheCompliance`, but nothing fails when a *new* method is
   added to the port without isolation cases, so that one is still a habit
   rather than a mechanism.

   Note that the two store compliance suites must be run in **separate pytest
   invocations** (`BACKLOG.md` B10m).

4. **If the change could move extraction quality, run the accuracy suite and
   say what it did.** `tests/accuracy/` measures precision, recall and F1 over
   a graded corpus:

   ```bash
   KG_LLM_BASE_URL=http://host:8080/v1 uv run pytest -m accuracy tests/accuracy/
   ```

   Every other gate here checks that the library is *correct*; this is the only
   one that checks it finds the **right** things, and a change can satisfy
   every invariant in `tests/unit/` while extracting worse.

   Two limits to state honestly when you quote it. The corpus is five
   hand-graded documents — enough to catch a regression, not enough to be a
   benchmark — and the floors are set where a regression trips them rather
   than where a good model sits. A run that clears the floors is not evidence
   that quality improved; it is evidence that it did not visibly fall. If you
   are claiming an improvement, quote the counts, not the F1.
