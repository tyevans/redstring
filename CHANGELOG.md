# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**What "breaking" means here is narrower than it looks.** The public API is
`redstring.__all__` and nothing else — anything reached through a dotted path
(`redstring.consolidation.service`, `redstring.llm.retry`) is internal, and a
rename or signature change there is not a breaking change and will not appear
under **Removed** or **Changed**. See
[ADR 0006](https://github.com/tyevans/redstring/blob/main/docs/adr/0006-the-public-surface-is-gated.md).

## [0.8.0] - 2026-08-14

Extraction can now run several chunks against the model at once, bounded by a
ceiling the caller sets. The default is unchanged and byte-identical to the
serial pipeline — `concurrency=1` issues the same calls, with the same
prompts, in the same order — so this release is additive for every existing
caller.

Measured on one 33k-character document against a local 30B model with an
8-slot server: **332.7s to 166.4s**, while extracting *more* (329 entities and
384 relationships against 209 and 276). The gain is not mostly the concurrency
— it is that concurrency makes smaller chunks affordable, and smaller chunks
extract more. If you have tuned `chunk_size` upward for speed, that trade may
now run the other way; `docs/how-to/tune-ingestion-throughput.md` covers how
to check against your own model and hardware, and why the numbers above should
not be copied.

### Added

- **`concurrency` on `build_graph` and `ExtractionPipeline`.** How many calls
  against the provider may be in flight at once. Chunks go out in batches of
  that size, and carryover folds back in **chunk order** rather than
  completion order, so a concurrent run is reproducible rather than dependent
  on which call returned first. Defaults to `1`.

  Note that effective concurrency is `min(concurrency, chunks in the batch)`:
  raising it past a document's chunk count does nothing, and the chunk count
  is *not* `len(text) / chunk_size` — ask
  `chunker.chunk(text).total_chunks` instead.

- **`CallLimiter`**, exported. One shared ceiling over every model call a
  `build_graph` makes — classification, extraction, gleaning and embedding
  alike — so a stated ceiling of four stays four rather than becoming six when
  gleaning overlaps the next batch. Construct one and pass it to several
  `build_graph` calls to bound a whole batch of documents against one endpoint.

### Documentation

- **`docs/how-to/tune-ingestion-throughput.md`** — how `chunk_size` and
  `concurrency` interact, why raising one alone often changes nothing, and the
  chunk size below which extraction starts manufacturing duplicate identities
  rather than finding more.
- [ADR 0039](https://github.com/tyevans/redstring/blob/main/docs/adr/0039-bounded-concurrency-over-chunks.md)
  records the decision and what makes `concurrency=1` byte-identical.

## [0.7.0] - 2026-08-12

Nothing on the public surface moved: no name was added, removed or renamed,
and no exported signature changed. The bump is minor rather than a patch
because the supported `eventsource-py` range moved to `>=0.14.0,<0.15`, and
narrowing a dependency is breaking for a consumer even when the API is not.
If you pin `eventsource-py` yourself, 0.13.x and this release cannot be
installed together — that is the one thing to check on upgrade, and it is the
same reason the 0.12.0 and 0.13.0 floor moves went out as minors.

Everything else here is documentation, including one correction that was
wrong before this release rather than because of it.

### Changed

- **`eventsource-py` is now `>=0.14.0,<0.15`.** Floor and cap move together,
  as they have since 0.13.0 — one supported version rather than a range.

  0.14.0 carries many breaking changes and almost none of them touch this
  library: subscriptions, the broker buses, read models, migration
  configuration and `ProjectionCoordinator`'s removed knobs are all surface
  redstring does not use. Exactly one reaches this package, and it reached
  the documentation rather than the code — the projections retry Protocol in
  `eventsource.application.projections.retry` is now `ProjectionRetryPolicy`
  rather than `RetryPolicy`, with no shim. Nothing under `src/` imports it,
  so no exported signature or behaviour changes; the how-to that named it in
  a signature does.

  Worth knowing if you write your own retry policy: `from eventsource import
  RetryPolicy` still resolves after the rename, but to an unrelated
  bus-backoff dataclass that a projection cannot use. Import the projections
  one by its new name from its own module rather than reaching for the
  top-level export.

### Fixed

- **The documented claim that `ProjectionCoordinator` polls is retracted.** It
  described behaviour the class does not have. No code in this library
  depended on the description being true, so the correction is to the prose
  only — but a reader designing around a polling loop was designing around
  something that never ran.
- **`replay(batch_size=...)` is documented, and with it the allocation it
  bounds.** The parameter is new in `eventsource-py` 0.14.0, which is also
  where `replay` stopped reading an entire feed into memory in one call — every
  store adapter materialises its whole result set before yielding an envelope,
  so a rebuild over a large log allocated the log. `max_events` never bounded
  that and could not: it counts envelopes already in hand, so it fires after
  the allocation it would have prevented. The pages describing a rebuild now
  say which knob bounds memory and which bounds the run.

## [0.6.0] - 2026-08-11

One new exported exception and two behaviour changes, neither of which
removes or renames anything on the public surface. The bump is minor rather
than a patch because `ConsolidationService.resolve` now succeeds where it
used to raise, so a caller that was catching `MergeIntoAliasError` around it
will find that path stops being reached.

### Added

- **`UnstructuredCompletionError`**, exported alongside its siblings, raised
  when a completion is not JSON at all (or is JSON that is not an object)
  rather than JSON of the wrong shape.

  It is a **subclass** of `MalformedCompletionError`, deliberately: an
  existing `except MalformedCompletionError` keeps catching this case instead
  of quietly starting to miss it. The narrower type exists because the two
  failures want different investigations — "the server accepted
  `response_format` and never applied it" is a server or chat-template
  problem, and "the model got a field wrong" is a prompt or schema one. They
  used to report identically, off the same `pydantic.ValidationError`, and
  that cost one investigation a detour through a schema that was fine. The
  message now names the likely cause and what to check: whether a grammar was
  compiled in the server's logs, and whether the chat-template handler wires
  `response_format` at all.

### Changed

- **`ConsolidationService.resolve` resolves an aliased subject to its
  terminal canonical instead of raising `MergeIntoAliasError`.**

  If A was merged into B, consolidating around A means consolidating around
  B — the merge already asserted they are the same entity. The docstring
  previously documented the workaround it demanded of every caller ("a caller
  sweeping a whole tenant should resolve its ids first"), which was the tell:
  the library was asking callers to do something it could do itself, with a
  capability (`GraphStore.resolve_entity_ids`) it already used for the
  symmetric case. Candidate selection has excluded aliased candidates through
  exactly that call since `CandidateFinder` existed; only the *subject* side
  was left unresolved.

  Resolution happens before candidates are fetched, rather than by catching
  the exception afterwards. An aliased subject's own edges were redirected
  away by the merge that made it an alias, so scoring against it directly
  starves the graph feature of neighbours the real canonical does have.
  Chains resolve to their terminal canonical, not one hop.

  `MergeIntoAliasError` can still surface from `resolve`, but now only from a
  genuine race — the resolved canonical becoming an alias between that
  resolution and the append inside `merge`. That is retried once against the
  newer canonical; a second failure is let through.

  **What to check on upgrade.** Nothing about the absorbed entity changes:
  its relationships and properties were folded into the canonical by the
  earlier merge, and this call neither re-derives nor re-applies that. The
  explicit path, `Consolidator.merge` / `ConsolidationService.merge`, is
  untouched and still raises on an aliased `canonical_entity_id` — that call
  is for a caller who already knows which id should be canonical, and
  resolving it silently would override a decision that may be deliberately
  correcting an earlier merge.

- **`Extraction`'s emitted JSON Schema requires `entities` and
  `relationships`.**

  pydantic omits any field with a default from `required`, and both use
  `default_factory=list` — so `{}` was a grammar-legal completion wherever
  the schema is compiled into one. A model could answer "found nothing"
  without having looked, indistinguishable from a document that genuinely
  held nothing.

  The defaults are unchanged at the pydantic level; a `json_schema_extra`
  hook sets `required` on the emitted dict only. `Extraction()` still
  constructs, an empty list for either key is still legal, and only the
  *absent* key is not. Nested schemas are untouched: their optional fields
  exist so one sloppy field does not cost a whole extraction, which does not
  apply to the two keys naming what was found at all.

## [0.5.0] - 2026-08-10

One behaviour change, and it changes which entities get merged. Nothing was
added to or removed from the public surface; the bump is minor rather than a
patch because a corpus consolidated on 0.4.0 and on 0.5.0 can differ, in the
direction of merging more.

### Changed

- **Consolidation's graph signal compares neighbours by name, not by id, and
  cross-document duplicates stop scoring zero.**

  `extraction.mapping.entity_id_for` namespaces every entity id by
  `source_id`, deliberately — deciding that `doc-1`'s "Ada" and `doc-2`'s
  "Ada" are one person is consolidation's judgement rather than something
  extraction settles by choosing an id. Neighbour ids are namespaced with
  everything else, so two extractions of one neighbour had different ids **by
  construction** and the Jaccard overlap of two neighbour *id* sets was
  structurally empty for every cross-document pair.

  The graph feature therefore reported maximum disagreement on exactly the
  pairs consolidation exists to find, and its disagreement was an artefact of
  an id scheme rather than a finding about the world. Two documents each
  naming "Ada Lovelace" alongside "Charles Babbage" scored `graph=0.0` and a
  combined `0.7143` — below `LOW_SIMILARITY` (0.75), so the pair was rejected
  outright rather than adjudicated.

  Worse than the cutoff: with the signal on, a cross-document pair could not
  reach `HIGH_SIMILARITY` (0.92) **at all**, because a perfect name and a
  perfect embedding ceiling out at 0.8 against a structural zero. Auto-merge
  across documents was unreachable regardless of the evidence.

  Neighbours are now compared by normalized name, which is the property two
  extractions of one neighbour actually share. Within a single document
  nothing changes — two different neighbours have two different names — so
  the discrimination [ADR 0015](https://github.com/tyevans/redstring/blob/main/docs/adr/0015-consolidation-gets-a-composed-entry-point.md)
  protected is kept, by the same mechanism as before.

  **What to check on upgrade.** If you run `CandidateFinder` with
  `use_graph_signal=True` over a multi-document corpus, expect more merges and
  more traffic in the adjudication band, and re-check `high` and `low` against
  your own corpus — the scale is unchanged but the distribution is not. If you
  had worked around this with a score floor, that floor is not made redundant:
  it is a statement about which pairs deserve a model's attention.

  Two costs are recorded rather than fixed, in `BACKLOG.md`: two genuinely
  different neighbours sharing a name now read as agreement, bounded by the
  graph weight (B123), and neighbours are not resolved through aliases before
  being compared (B124).

  See [ADR 0034](https://github.com/tyevans/redstring/blob/main/docs/adr/0034-neighbours-are-compared-by-name.md),
  which amends 0015's disjoint-neighbourhood clause.

- The graph signal now costs **two store reads per side** rather than one:
  `get_relationships` for the edges and a batched `get_entities` for the
  neighbours they name. `use_graph_signal=False` remains the lever for a large
  sweep, and it is still the only way to skip the round trips —
  `FeatureWeights(graph=0.0)` produces identical scores and pays for them.

### Fixed

- `docs/how-to/consolidate-duplicate-entities.md` claimed that two entities
  with no neighbours score `0.0`. That has been `None` since 0.4.0 shipped ADR
  0015; the page was stale for the whole release.

## [0.4.0] - 2026-08-09

Forty names added to the public surface and none removed. The headline is
that redstring now **stores passages and retrieves over them** — a chunk
corpus, a BM25 lexical channel, and a hybrid entity retriever — and that
extraction quality is measured rather than asserted.

One change is invisible at runtime and visible to a type checker: the four id
names are `NewType`s. See **Changed**.

### Added

- **Hybrid entity retrieval.** `Retriever.retrieve` fuses a semantic channel
  (the vector store) and a lexical one (blocking keys plus Jaro-Winkler) into
  a `RetrievalResult` of `ScoredEntity`. `RetrievalMode` selects `semantic`,
  `lexical` or `hybrid`.

  `ScoredEntity.score` is a **reciprocal-rank-fusion** score: ordinal,
  unbounded, and *not* on `VectorMatch`'s cosine 0..1 scale. The two channels
  share no unit — a weighted sum of them invents an exchange rate that is
  wrong for some corpus and unfalsifiable for the rest — so ranks are fused
  rather than scores. `semantic` and `lexical` are `None` when that channel
  did not rank the entity and a float when it did; those are two different
  facts a caller acts on differently.

  Exported: `Retriever`, `RetrievalMode`, `RetrievalResult`, `ScoredEntity`.
  See [ADR 0022](https://github.com/tyevans/redstring/blob/main/docs/adr/0022-the-lexical-channel-is-not-bm25.md).

- **A chunk corpus.** `StoredChunk` is one passage of one document under one
  tenant, behind the `ChunkStore` port with `InMemoryChunkStore` and a
  Postgres adapter. `index_documents` chunks a corpus with **no model call
  anywhere**, returning an `IndexReport`, so passages can be stored for every
  document a caller holds and extraction run over whichever subset is worth
  paying for.

  Identity is **content-addressed**: `ChunkId` is a digest of the source id
  and the text. Positional identity would make re-chunking an in-place
  overwrite, so chunk 3 of a re-chunked document would be a different passage
  wearing the same id — with the old entity links, and later the old
  embedding, describing text that no longer says what they claim.

  The port has **no search method**. Retrieval over the corpus was a separate,
  later design, and guessing the signature would have given the port a method
  no adapter could implement consistently.

  Exported: `StoredChunk`, `ChunkId`, `ChunkStore`, `InMemoryChunkStore`,
  `ChunkProjection`, `DocumentChunked`, `index_documents`, `IndexReport`.
  See [ADR 0023](https://github.com/tyevans/redstring/blob/main/docs/adr/0023-the-chunk-corpus.md).

- **BM25 over the chunk corpus, with the scorer in the domain.**
  `rank_chunks` scores adapter-supplied `CorpusStats` into `RankedChunk`s, and
  `tokenize` decides what a term is. Both live in `domain/` rather than in an
  adapter, so every backend ranks identically and the compliance suite keeps
  the gate that has caught every divergence so far.

  Exported: `rank_chunks`, `RankedChunk`, `tokenize`, `CorpusStats`,
  `LexicalCandidate`, `LexicalCandidates`, `LexicalCandidateSource`.
  See [ADR 0024](https://github.com/tyevans/redstring/blob/main/docs/adr/0024-bm25-over-the-chunk-corpus.md).

- **Two chunkers, both exported.** `SlidingWindowChunker` (unchanged default)
  and `BoundaryPreferenceChunker`, contributed by a downstream consumer.
  Both cascade paragraph → sentence → word → hard cut; the new one searches
  the **whole window** for a boundary rather than its last 500 characters, and
  recognises a sentence that ends the text or is followed by a closing quote.
  Pass it when the passages will be quoted back to a reader: a chunk ending
  mid-sentence produces a quotation nobody can use.

  It is not the default because chunk ids are content-addressed, so moving a
  boundary re-keys every chunk of every re-ingested document.

- **Every port that can be substituted is now typed as a capability**, and the
  capabilities are exported so a caller can implement the narrow one:
  `ChunkReader`/`ChunkWriter`/`ChunkPurge`,
  `VectorReader`/`VectorWriter`/`VectorPurge`, `KeyValueCache`/`HitWindow`
  composing `Cache`, and `CandidateSource`/`MergeAdjudicator`/
  `ConsolidationGraph` for consolidation.

  Consolidation's two substitution points are the reason this matters rather
  than being tidiness: `Consolidator.resolve` invited a caller to supply their
  own blocking or their own adjudication, and annotated both against
  *classes* whose constructors demand a `GraphStore` and an `LlmProvider`. The
  two substitutions the docstring invited were the two the annotation
  obstructed. See
  [ADR 0025](https://github.com/tyevans/redstring/blob/main/docs/adr/0025-consolidation-substitution-is-two-protocols.md),
  [0026](https://github.com/tyevans/redstring/blob/main/docs/adr/0026-chunk-store-and-cache-are-capabilities-too.md),
  [0027](https://github.com/tyevans/redstring/blob/main/docs/adr/0027-vector-store-is-three-capabilities-and-so-is-every-collaborator.md).

- **The resource-owning adapters are async context managers.**
  `Neo4jGraphStore`, `PgVectorStore`, `PostgresChunkStore` and `RedisCache`
  each held a driver, pool or client and offered only `close()`, so the
  shipped usage was `connect()` plus a `try`/`finally` the caller had to
  remember. `AsyncClosable` is exported as the shape.

  Ownership still decides what closes: entering a block with an adapter built
  around an *injected* driver leaves that driver open on the way out. And
  `__aexit__` returns `None` deliberately — anything truthy would swallow what
  the body raised, including `CancelledError`. See
  [ADR 0028](https://github.com/tyevans/redstring/blob/main/docs/adr/0028-a-capability-declares-its-own-release.md).

- **The port compliance suites ship, as `redstring.testing`.** They were under
  `tests/` and therefore not in the wheel, so "a correct adapter runs the
  shared suite unchanged" was a rule only this repository could obey. An
  outside author had the Protocol — which pins signatures and says nothing
  about semantics — and a how-to telling them to subclass a class they could
  not import.

  Install `redstring[test]`. Same bodies, not a reduced variant: a weaker
  suite for outside adapters would make the port mean two different things.
  `redstring.testing` sits **above** every other layer in the import contract,
  so nothing under `src/` can reach it and `import redstring` can never pull
  in `pytest`. See
  [ADR 0033](https://github.com/tyevans/redstring/blob/main/docs/adr/0033-the-compliance-suites-ship.md).

- **Carryover: a chunk is told what earlier chunks found.** On by default at
  `DEFAULT_CARRYOVER_ENTITIES` (32). Chunk two says "Lovelace" where chunk one
  said "Ada Lovelace", and ids derive from the normalized name — so that is
  not a wobble, it manufactures a second entity that merging cannot combine
  and that consolidation pays a model call to resolve.

  The names go in the **system prompt**. Inside the chunk they are
  indistinguishable from the document, and the model then reports carried
  names as entities of a passage that never mentioned them. See
  [ADR 0029](https://github.com/tyevans/redstring/blob/main/docs/adr/0029-a-chunk-is-not-extracted-alone.md).

- **Gleaning — show a chunk its own answer and ask what it missed — off by
  default.** One model call per chunk per pass, and a library that silently
  doubles what a caller pays for a model has made the caller's decision.

- **A domain schema can constrain the decode, when asked.** Opt-in; the
  `LlmProvider` port needed no widening, because constraining the vocabulary
  is a different *argument* to `extract`, not a different port. `Extraction`,
  `ExtractedEntity` and `ExtractedRelationship` are exported. See
  [ADR 0030](https://github.com/tyevans/redstring/blob/main/docs/adr/0030-a-domain-schema-may-constrain-when-asked.md).

- **`list_available_domains`** returns a `DomainSummary` per bundled domain.
  Previously the supported way to discover a valid id was to pass a wrong one
  and read `UnknownDomainError` — the last guess-and-catch step in the public
  surface.

### Changed

- **The four id names are `NewType`s.** `EntityId`, `RelationshipId` and
  `TenantId` over `UUID`; `SourceId` over `str`. Three of them used to be
  *the same object* — `EntityId is TenantId` was true — so transposing the
  adjacent `(entity_id, tenant_id)` arguments every store port takes was a
  tenant-isolation defect that type-checked cleanly.

  **Nothing changes at runtime.** `NewType` has no representation of its own,
  so no persisted event, no Neo4j property and no Postgres column moves, and
  no existing log becomes unreadable. What changes is that a type checker now
  rejects the swap.

  *If you run mypy or pyright against redstring*, you may need to wrap
  constructions: `EntityId(uuid4())` rather than a bare `uuid4()`. Untyped
  callers are unaffected. See
  [ADR 0032](https://github.com/tyevans/redstring/blob/main/docs/adr/0032-the-id-names-are-newtypes.md).

- **Extraction no longer asks the model to think.** `openai_compatible` sends
  `chat_template_kwargs: {enable_thinking: false}`; pass `thinking=True` to
  restore the server's own behaviour.

  Measured over the whole graded corpus, both arms in one run:

  | | wall clock | entity tp/fp/fn | relationship tp/fp/fn |
  |---|---|---|---|
  | thinking | 155.1s | 12 / 9 / 0 | 5 / 11 / 1 |
  | no thinking | 27.3s | 12 / **3** / 0 | 5 / **6** / 1 |

  Recall identical and perfect, precision better on both, 5.7× faster. The
  mechanism is not luck: extraction asks for what the text *states*, and a
  model given room to deliberate uses it to infer — every inference is a
  false positive under the corpus's first grading rule. See
  [ADR 0031](https://github.com/tyevans/redstring/blob/main/docs/adr/0031-extraction-does-not-think.md).

### Fixed

- **`SlidingWindowChunker` silently discarded a document's final
  characters.** It stopped once the unconsumed remainder was shorter than
  `min_chunk_size`, under a comment claiming the last chunk already included
  it. At `overlap=0, chunk_size=1000` a 5025-character document produced
  5000 characters, with no error and no counter. A dropped span loses
  entities in a way indistinguishable from a document that said less.

- **The accuracy suite had been skipping rather than running.** Its provider
  was constructed with the wrong arguments, raising `TypeError`
  unconditionally, and a broad `except` turned that into a skip blaming the
  endpoint. Every floor — entity recall and precision, relationship recall,
  and the `empty-negative` document that is the only test of hallucination
  here — was inert.

- Four stale documentation claims, including a `LangChainLlmProvider`
  construction shown three ways in `README.md`, `docs/getting-started.md` and
  `docs/installation.md` that had never been valid.

### Notes

- **The accuracy suite exists now**, which retires the last line of the 0.1.0
  notes below. It is five hand-graded documents — enough to catch a
  regression, not a benchmark — and its floors are set where a regression
  trips them rather than where a good model sits. Its scorer and corpus run in
  the commit gate against a scripted provider, which is what makes any live
  number believable: measuring nothing reports F1 = 0.0 and reads as a bad
  model, and comparing the corpus against itself reports 1.0 and reads as a
  good one.

- **Two measured changes returned null results and are recorded as such.**
  Constrained decoding changed nothing once thinking was off — identical
  counts in both arms, and the mechanism first proposed for it turned out to
  be describing the reasoning trace. That verdict was published and then
  **retracted**; ADR 0030 keeps the original reasoning verbatim and says why
  it was wrong. Gleaning likewise measured null, on a corpus that cannot show
  its benefit, which is why the default is "off" rather than "no".

- The `test` extra is new, and carries `pytest`, `hypothesis` and
  `pytest-asyncio` for `redstring.testing`. It is not needed to use the
  library.

## [0.3.0] - 2026-08-06

### Added

- **`Relationship.source_id` says which document stated an edge.**
  `SourceId | None`, defaulting to `None`, matching `Entity.source_id`.
  `map_extraction` fills it from the document being extracted, so extraction
  output carries it without a caller doing anything. Previously the endpoints
  had provenance and the edge between them had none, which is backwards for a
  corpus whose claims are mostly relational.

  `DocumentExtracted` gained the matching rule: an edge naming a *different*
  document is rejected, an edge naming none is accepted. The asymmetry against
  the entity rule is deliberate — the field postdates the event, and the
  validator runs on replay, so rejecting the absent case would make existing
  logs unreadable.

  There is no `source_text` counterpart. `ExtractedRelationship` has no span
  field, so a value there could only be paraphrased, and a paraphrase in a
  field named for a quotation reads as evidence.

- **`project` can scope its read to one tenant.** `project(..., tenant_id=...)`
  forwards `FeedReadOptions(tenant_id=...)` to the feed, which the eventsource
  adapters push into the query. Rebuilding one tenant out of a shared store is
  now an indexed read rather than a full scan filtered in Python. Scoping with
  `tenant_filter` on the projection still works and still costs the whole read
  — it drops foreign events after delivery.

- **`ReplayReport.failures` names the events a replay dropped.** One
  `ReplayFailure` per rejection, carrying `position`, `event_type`, the
  rejecting projection's class name, and `error` — the exception object itself,
  not a message. Previously the exception was discarded and `failed` was a bare
  count, which is safe and gives an operator no path from "3 events failed" to
  the poison event.

- **`project(..., strict=True)`** raises `ReplayFailedError` on the first
  rejection instead of recording it and carrying on. The error carries the same
  `ReplayFailure` and sets the original exception as its `__cause__`.

- **`replay`, an alias export for `project`.** Callers whose own vocabulary has
  a *project* noun can import the alias; it is the same function object.

Exported: `ReplayFailure`, `ReplayFailedError`, `replay`. See
[ADR 0018](https://github.com/tyevans/redstring/blob/main/docs/adr/0018-a-replay-report-carries-its-failures.md).

### Changed

- **`ReplayReport.failed` is now a property derived from `failures`**, and the
  constructor takes no `failed=` argument. Reading `report.failed` is
  unchanged and means the same thing (events at least one projection rejected,
  counted once per event); constructing a report with `failed=` now raises
  `TypeError`. `failures` has one entry per *rejection*, so an event both folds
  rejected counts once in `failed` and twice in `failures`.

- **The eight string enums are now `enum.StrEnum`.** `DatePrecision`,
  `MergeStrategy`, `ExtractionMethod` and the rest. `.value` and
  member-as-plain-string are unchanged — those reach Neo4j properties and event
  payloads, and are pinned member by member in
  `tests/unit/test_enum_values_are_a_wire_format.py`. What does change is
  `str(DatePrecision.YEAR)`, which was `"DatePrecision.YEAR"` and is now
  `"year"`. Anything formatting a member into a message or a log line reads
  differently; nothing that persists one does.

- **Batch relationship writes are atomic.** A batch that fails writes no edges
  rather than a prefix. See
  [ADR 0019](https://github.com/tyevans/redstring/blob/main/docs/adr/0019-batch-relationship-writes-are-atomic.md).

### Fixed

- **`DomainSchema` normalizes a type id one way, not three.** The same rule was
  spelled six times in three strengths, and the disagreement was reachable from
  ordinary input: `is_valid_source("Main Character")` answered `False` against a
  list built from that exact string, and `get_entity_type("Access Road")`
  returned `None` for a type declared as `Access Road`. Callers passing an
  `EntityTypeSchema.id` never saw it — ids are normalized on load — but a caller
  passing an `Entity.entity_type`, which is free-form text from the model, did.
  Eleven call sites now share `normalize_type_id` / `normalize_identifier`.

  Two behaviour changes fall out, both intended: a reference written `__site__`
  matches an entity type written `__site__`, and lookups that returned `None`
  for an unnormalized argument now find the type.

- **A vector is rejected for a zero *norm*, not for zero components.** Those are
  different questions: components around 1e-30 are eight good float64 values
  with a non-zero float64 norm that stores as zero, and `cosine_score` needs the
  norm.

- **A NUL byte is refused in every field that reaches the event log**, not only
  in metadata.

- **`"Sept"` parses.** The spelling table claimed it and the pattern refused it.

## [0.2.0] - 2026-08-05

### Added

- **`EmbeddingProvider`, and a `VectorStore` you can actually fill.**
  `VectorStore` shipped in `0.1.0` with two adapters and no way for the library
  to put a vector in it — every write path took vectors you had computed
  elsewhere. `build_graph` now takes an `embedding_provider` and a
  `vector_store` together and populates both stores:

  ```python
  await build_graph(
      document,
      provider=llm,
      store=graph,
      tenant_id=tenant_id,
      embedding_provider=embedder,
      vector_store=vectors,
  )
  ```

  Exported: `EmbeddingProvider` (the port), `FakeEmbeddingProvider`
  (deterministic, no model needed — for your tests as much as ours), and
  `EmbeddingProviderError`.
  `redstring.llm.adapters.langchain_embedding.LangChainEmbeddingProvider`
  speaks to any OpenAI-compatible embeddings endpoint and is reached by path,
  so `import redstring` still does not pull LangChain in.

  Vectors reach the store through an `EntitiesEmbedded` event and
  `VectorProjection`, so a vector store stays rebuildable by replay. See
  [ADR 0017](https://github.com/tyevans/redstring/blob/main/docs/adr/0017-the-embedding-provider-port.md).

- **Dimensions are checked where the mistake is.** A provider declares the
  width it produces and a store declares what it holds; `build_graph` refuses a
  mismatched pair before anything is embedded, and refuses one of the two
  without the other. Previously the first sign of either was a database
  complaining about a column type after you had paid for the embedding calls.

- **An accuracy suite.** `-m accuracy` measures precision, recall and F1 over a
  graded corpus. Five hand-graded documents — enough to catch a regression, not
  a benchmark, and it says so.

- **Every third-party client is confined to one directory, and that is now
  checked.** `langchain`/`openai`, `neo4j`, `asyncpg` and `redis` may each be
  imported from exactly one place. Only the first was enforced; the other three
  were correct by convention with nothing holding them there. This is internal,
  but it is what keeps `import redstring` from pulling a driver in, so it
  protects a promise the public surface makes.

### Fixed

- **`eventsource-py` is now `>=0.10.0,<0.12`**, tested against 0.11.0. The
  0.11.0 release renames `ports.readmodels.OptimisticLockError` to
  `ReadModelVersionConflictError` — redstring uses no read models, so nothing
  here changes and **the floor stays at 0.10.0**: it states what this library
  needs, not what is newest. Both versions work.

- **`eventsource-py` floor raised to `>=0.10.0`.** `0.1.0` declared `>=0.9.1`
  while `redstring.projections` forwards `retry_policy` and `tracer`, which
  `DeclarativeProjection.__init__` gained in 0.10.0. A resolver picking the low
  end raised `TypeError: unexpected keyword argument 'retry_policy'` when
  constructing a projection — **not at import**, so `import redstring`
  succeeded and the error surfaced in your code with no obvious link to a
  dependency bound. If you pinned `eventsource-py==0.9.1` alongside
  `redstring==0.1.0`, upgrading resolves it.

### Notes

- **Embedding vectors are reproducible in direction, not bit-for-bit.** The
  same text embeds to the same vector in the sense that matters — cosine above
  0.99 — and not an identical one, because floating-point accumulation depends
  on how a batch was packed. Do not compare vectors with `==`, and do not hash
  one as an identity. Measured at up to `4e-3` per component against llama.cpp;
  the compliance suite states the contract this way because an earlier version
  asserted equality and no real backend could satisfy it.
- Entity **names** are embedded, whole. There is no chunk-level or
  document-level embedding yet, and a merged entity keeps its pre-merge vector
  until something re-embeds it.
- No breaking changes. Everything above is additive.

## [0.1.0a1] - 2026-08-04

**A rehearsal of the release pipeline, published to TestPyPI as
`redstring-test`. Not a release, and not on PyPI.**

The library is identical to `0.1.0` below; this version exists so that the
tagging, building, publishing and post-publish verification steps run once
against a real index on a version nobody minds burning. PyPI never permits
reusing a filename, so the first execution of that path is also irreversible —
which is a poor combination with never having executed it.

Install it, if you want to look at it, with both indexes — TestPyPI does not
mirror PyPI, so resolving `pydantic` needs the real one:

```bash
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ \
            redstring-test==0.1.0a1
```

The import name is `redstring` on both indexes; only the distribution is
renamed, because `redstring` on TestPyPI belongs to an unrelated project.

## [0.1.0] - 2026-08-04

First release.

### Added

- **Extraction.** `ExtractionPipeline` and `build_graph`: chunk a
  `SourceDocument`, extract entities and relationships through the
  `LlmProvider` port, merge across chunks, and emit a `DocumentExtracted`
  event. Six bundled domain schemas, selectable by name or by classifier
  (`AUTO`).
- **Two store ports with two implementations each.** `GraphStore`
  (`InMemoryGraphStore`, `Neo4jGraphStore`) and `VectorStore`
  (`InMemoryVectorStore`, `PgVectorStore`), both held to a shared compliance
  suite that every adapter runs unchanged.
- **An event-sourced write model.** `DocumentExtracted`, `EntitiesMerged` and
  `MergeUndone`, with `GraphProjection` and `VectorProjection` folding them
  into the stores. Extraction and consolidation both emit and write to no
  store, so a store can be rebuilt by replay.
- **Consolidation.** `Consolidator` — blocking, scoring, banding and model
  adjudication behind `resolve()`, an explicit `merge()`, and an `undo()` that
  takes only the merge's event id and reads what to restore from the log.
  Every change reaches the graph through a projection, never a direct write.
  With no `event_store` argument the merge history is in-memory, so undo is
  session-only; `remembers_merges_across_restarts` reports which arrangement
  is in use. See ADR 0015.
- **Temporal inference.** Interval relations computed on read from
  `TemporalExtent`, never persisted into the event log. Not exported yet.
- **Resilience over the `Cache` port.** Retry with jitter, rate limiting and
  circuit breaking, with in-memory and Redis cache adapters. Not exported yet.
- **Multi-tenancy throughout.** Every store call takes a `tenant_id`, and
  every compliance suite asserts reads never cross tenants.
- **A gated public surface.** `__all__` is the whole promise, held by three
  tests: exported signatures name only exported types, every `RedstringError`
  is exported or recorded, and the end-to-end example imports nothing but
  `redstring`.
- `py.typed`, so downstream type checkers see the annotations.
- Documentation at <https://tyevans.github.io/redstring>, including the
  architecture decision records.

### Notes

- Requires Python 3.13+.
- **Every backend is an extra.** The base install is `pydantic`,
  `eventsource-py` and four small pure-Python libraries — no database driver,
  no Redis client, no compiled numerical package. `neo4j`, `pgvector`,
  `redis` and `llm` each pull exactly what their adapter needs, and reaching
  an adapter without its extra raises an `ImportError` naming the extra.
- `eventsource-py` is the one **core** dependency that is not pure
  configuration, and that is deliberate: `redstring.__init__` exports types
  that need it, and a public API that fails to import without an extra is not
  a public API.
- The library **never fetches content**, and extraction **writes to no store**.
  Both are architectural commitments rather than gaps.
- There is no accuracy suite. `tests/accuracy/` is empty, so no claim about
  extraction *quality* is backed by anything in this repository — correct and
  accurate are different properties (`BACKLOG.md` B12).

[Unreleased]: https://github.com/tyevans/redstring/compare/v0.8.0...HEAD
[0.8.0]: https://github.com/tyevans/redstring/releases/tag/v0.8.0
[0.7.0]: https://github.com/tyevans/redstring/releases/tag/v0.7.0
[0.6.0]: https://github.com/tyevans/redstring/releases/tag/v0.6.0
[0.5.0]: https://github.com/tyevans/redstring/releases/tag/v0.5.0
[0.4.0]: https://github.com/tyevans/redstring/releases/tag/v0.4.0
[0.3.0]: https://github.com/tyevans/redstring/releases/tag/v0.3.0
[0.2.0]: https://github.com/tyevans/redstring/releases/tag/v0.2.0
[0.1.0]: https://github.com/tyevans/redstring/releases/tag/v0.1.0
[0.1.0a1]: https://github.com/tyevans/redstring/releases/tag/v0.1.0a1
