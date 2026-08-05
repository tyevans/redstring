# ADR 0007: `composition` is the only top layer, and `build_graph` writes without a log

**Status:** accepted, slice 10 of the ring migration.

**Why this is an ADR:** three decisions in `src/redstring/composition.py` look
like conveniences and are not. A layer holding one module reads like
over-engineering until you notice that the layer below it is deliberately split
in half; `build_graph` folding an event straight into a `GraphStore` reads like
the simple path until you need to rebuild that store; and `AUTO` being an object
rather than the string `"auto"` reads like taste until a domain is actually
called `auto`. Each of the three costs something, and this records what was
bought with it.

The decisions are:

1. **`composition` is the top layer of the import contract and holds exactly one
   module.** It exists to bridge a separation the layer below it enforces —
   `extraction` may not import `projections` — because otherwise the library
   ships two halves and a diagram.
2. **`build_graph` applies the event it produced directly to a
   `GraphProjection`,** with no event store in between. That costs per-document
   idempotency and any ability to rebuild, and it returns `report.event` so a
   caller who wants both can take the other path — see
   [Drive projections from an event store](../how-to/drive-projections-from-an-event-store.md)
   and [ADR 0001](0001-event-log-schema-and-granularity.md) for why the log is
   shaped the way it is.
3. **`domain=AUTO` is a sentinel object whose extra model call is named in the
   signature,** and `AutoDomain` is public solely because that signature
   mentions it — the gate from
   [ADR 0006](0006-the-public-surface-is-gated.md) forbids a public signature
   naming a type a caller cannot reach.

What the domain prompt does once chosen is a separate decision, recorded in
[ADR: domain schemas prompt but do not constrain](0007-domain-schemas-prompt-but-do-not-constrain.md).

The same reasoning appears in three other places, because each is read by
someone who will not read this file: the module docstring of
`src/redstring/composition.py`, the inline comments on the `layers` list in
`pyproject.toml`, and the "How it fits together" section of
[README.md](../../README.md). This ADR is the canonical copy; the last section
below says what each of the others is allowed to say.

## Context

Nine slices of the ring migration built the pieces of a knowledge-graph build
and never joined them. Each arrow in the chain existed and was tested on its
own:

```
SourceDocument -> ExtractionPipeline -> Document.record_extraction
               -> DocumentExtracted -> GraphProjection -> GraphStore
```

Nothing called them in sequence. `redstring.__init__` exported a version
string and nothing else, so the library had a write model, a read model, two
store ports, and no answer to "how do I turn a document into a graph?".

The obvious fix — let `ExtractionPipeline` take a store — is the one the
architecture spends most of its effort preventing. `extraction` and
`projections` are separated in the import contract precisely so that a store
reference cannot grow back into the pipeline, and the separation is enforced
rather than documented:
`tests/unit/extraction/test_pipeline.py::TestNoStoreReachesExtraction` fails if
it erodes. `ExtractionPipeline.record` reflects the same rule in miniature — it
asks the aggregate for an event and writes nothing itself, which is the property
the whole re-architecture rests on.

That leaves a gap that is structural, not accidental. If no module may know both
halves, then no module can join them, and the library ships two halves and a
diagram. Something has to hold both. The only question is whether that something
is admitted deliberately, with a named place in the layer contract, or arrives as
a store parameter quietly added to a pipeline constructor because a caller needed
one. The import contract's `exhaustive = true` setting means the choice cannot be
made by default: a new top-level package fails the gate until it is placed on a
layer on purpose.

Three further questions had to be answered at the same time, because the joining
module is where each of them first becomes visible:

- **Does the composed path go through an event store?** The write model already
  emits `DocumentExtracted`, and `redstring.projections.project` already folds a
  feed of events into a store — the mechanism described in
  [Drive projections from an event store](../how-to/drive-projections-from-an-event-store.md),
  over a log whose shape is argued in
  [ADR 0001](0001-event-log-schema-and-granularity.md). But most callers have no
  event store, and requiring one to extract a single document makes the library's
  entry point conditional on infrastructure it does not provide.
- **How does a caller ask for a domain-specialised prompt without naming a
  domain?** `ContentClassifier` can choose one, at the cost of a model call
  before the extraction calls. A build that silently pays for an extra call is a
  cost discovered from a bill rather than from a signature — and the classifier
  never fails loudly, falling back to `encyclopedia_wiki` on three separate
  paths, so a give-up is indistinguishable from a confident answer unless the
  return type says otherwise.
- **What does that request look like in a parameter that also takes domain
  ids?** `domain` accepts a domain id string or a `DomainSchema`. A magic string
  such as `"auto"` in that same field makes a real schema called `auto`
  unreachable — the trap `ScrapingJob.extraction_strategy` had already fallen
  into in this codebase, where a magic string in a free-form field decided
  control flow.

Each of the three has a cheap answer that costs nothing today and something
specific later. The sections below record which cost was accepted in each case,
and what was bought.

## Decision 1: one top layer above the sibling band, holding exactly one module

`composition` is the highest layer in the `lint-imports` contract in
`pyproject.toml`, above the sibling band
(`extraction : consolidation : temporal : graph : vector : llm`), and it holds
exactly one module: `src/redstring/composition.py`, whose only public entry
point is `build_graph`.

A layer with one module in it looks like ceremony. It is not, and the reason is
that the layer immediately below it is deliberately cut in half. `extraction`
and `projections` are on different layers, and siblings on the extraction line
may not import each other, so no module in either half can legally reach the
other. That is the property the re-architecture is built on: `ExtractionPipeline`
takes no store, asks `Document.record_extraction` for an event, and writes
nothing. Given that rule, the sequence

```
SourceDocument -> ExtractionPipeline -> Document.record_extraction
               -> DocumentExtracted -> GraphProjection -> GraphStore
```

cannot be expressed anywhere inside the layers that implement it. A joining
module is not a convenience on top of the architecture; it is the thing the
architecture makes necessary by forbidding the join anywhere else.

So the real choice was never "one module or none". It was **where the join is
admitted**. The alternative to a named top layer is not a smaller library — it
is a `graph_store` parameter appearing on `ExtractionPipeline.__init__` the
first time somebody needs a document to end up in a graph, which reads as
reasonable in review and quietly reverts the separation. Putting the join in a
layer of its own makes it a structural fact that `lint-imports` checks on every
commit: `composition` may import both halves, and nothing may import
`composition`.

Two properties of the contract do the work here, and both are settings rather
than conventions:

- **`exhaustive = true` with `containers = ["redstring"]`.** A new top-level
  package under `redstring` fails the contract until someone places it on a
  layer on purpose. `composition` could not have been added by accident, and a
  second top-level package cannot be either. That option is not decorative —
  slice 9 proved it bites by adding a throwaway package, watching the contract
  break, and removing it, on the principle that a passing check nobody has
  seen fail is not yet evidence.
- **Nothing sits above it.** Because `composition` is the top layer, no module
  in the package may import it, which is what keeps the dependency one-way:
  `build_graph` knows about `ExtractionPipeline` and `GraphProjection`, and
  neither knows about `build_graph`. If a second module ever needs to be
  imported *by* the composed path, it belongs on a lower layer, not this one.

The cost is that the layer is visibly disproportionate — one file above six
siblings — and that this asymmetry invites tidying. Anyone reading the layer
list will be tempted either to fold `composition` into `extraction` (which
undoes the separation) or to grow it into a general-purpose "app" or "services"
layer (which is the shape slice 9 deleted, along with `models`, `db` and
`schemas`; there is no service layer here, and re-adding one needs an
argument rather than a spare seat). The module docstring and the inline comment
on the `layers` list both close with the same instruction — *if a second module
wants in here, ask what it composes* — and the next subsection makes that test
concrete.

### What enforces the separation it exists to bridge

A top layer bridging a gap is only worth having if the gap is real, and the
gap is only real if something fails when it closes. Four checks make that so,
and they are listed here because each is blind to what the others see — a
point worth stating, since the natural assumption is that the import contract
covers all of it.

- **`lint-imports`, on every commit.** The layered contract in
  `pyproject.toml` puts `extraction` and `projections` on different layers,
  and sibling layers on the extraction line may not import each other. An
  `import redstring.projections` appearing anywhere under
  `src/redstring/extraction/` fails the pre-commit hook. This is the check
  that catches the honest version of the mistake: someone who reaches for the
  projection because the pipeline is where they happen to be standing.
- **`tests/unit/extraction/test_pipeline.py::TestNoStoreReachesExtraction`.**
  It introspects `ExtractionPipeline.__init__` and fails if any parameter has
  `store` in its name. The import contract cannot see this one, because the
  dangerous version does not import anything: a caller constructs the store
  and hands it in, so `extraction` acquires a store *reference* without
  acquiring a store *import*. That is the shape that would pass review — a
  `graph_store` parameter looks like dependency injection, not like an
  architectural reversal — and it is the shape a signature test catches and a
  contract does not.
- **`ExtractionPipeline.record` returning an event.** The pipeline asks
  `Document.record_extraction` for a `DocumentExtracted` and writes nothing
  itself. This is not a check but the property the checks defend: as long as
  `record`'s only output is an event, there is no half-measure available where
  extraction writes "just the entities" and leaves the rest to a projection.
- **`tests/unit/test_end_to_end_example.py`.** It executes
  `docs/examples/build_a_graph.py` and parses it, failing on any import that is
  neither `redstring` nor standard library. That is what stops the composed
  path being demonstrated by reaching into `redstring.graph.adapters.memory`
  — an example that does so proves the internals work and says nothing about
  whether the join is reachable from outside.

What the first two have in common is the reason both exist. The contract
checks imports; the signature test checks a parameter list. A store arriving
as a constructor argument is invisible to the first and fatal to the second,
and a projection imported for a type annotation is the reverse. The
architecture's central claim — *extraction emits events, projections write* —
has two independent ways to erode, so it needs two independent checks, and
this is the same reasoning that puts `tests/unit/llm/test_port_does_not_leak.py`
beside the contract for the `llm` sibling: `lint-imports` only sees
first-party imports, so a `langchain` import outside the adapter package needs
a check of its own. Any boundary this codebase deliberately maintains gets a
second kind of check, because the first kind has a known blind spot.

Note what none of these check: that `composition` still holds exactly one
module. That is the one part of this decision resting on review rather than on
a gate, and the next subsection is the test a reviewer applies by hand.

### The admission test for a second module

Nothing in the repository fails when `composition` grows a second module. `lint-imports` checks *direction*, not population: a new file under `redstring/composition/` would inherit the top layer's permissions — import both halves, be imported by nothing — and pass. `exhaustive = true` catches a new top-level *package*, which is the opposite mistake. So the constraint that keeps this layer one module wide is a review habit, and both surviving copies of the reasoning end on the same sentence: *if a second module wants in here, ask what it composes.* This subsection says what a good answer looks like, because "ask what it composes" is only useful if the wrong answers are recognisable.

The test has one question in it, and it is about the *pair*: **which two layers, forbidden from importing each other, does this module join?** `composition.py` has an answer that can be written as an import list — it imports `ExtractionPipeline` from `extraction` and `GraphProjection` from `projections`, and no other module in the package may do both. A candidate that cannot name such a pair is not composing anything; it is a piece of one of the halves that has been placed above them for convenience.

Three concrete rejections, all of them shapes this codebase has already produced:

- **"It orchestrates several extraction steps."** That is `extraction`, and the sibling band is where it goes. Height is not a reward for calling more functions. Both `consolidation` and `temporal` were argued into the *sibling* line rather than above `extraction`, for reasons `pyproject.toml` records inline: being above would let them reach `mapping.py`, which is how a second entity-id scheme gets born. A module that only ever imports downward is already legal where it stands.
- **"Callers need somewhere to put application wiring."** That is a `services` layer, and it was the top layer of this contract until slice 9 deleted it — along with `models`, `db` and `schemas` — because the write model is `aggregates` + `events`, the read model is `projections`, and persistence is the two ports. There is no service layer here. A general-purpose "app" layer is the shape that grows without ever being argued for, since anything can be justified as belonging to it.
- **"The composed path needs to import it."** Then it belongs *below* `composition`, not beside it. Nothing may import the top layer, so a module admitted here can never be reused by the code it supports. That constraint is the useful part: a helper `build_graph` wants to call is a helper on a lower layer, and `_resolve_prompt` staying a private function in the same file rather than becoming a module is the small version of the same call.

The accepting answer looks like the one already here, and the shape is worth stating because it is narrower than "joins two layers": the module must join a pair whose separation is *itself defended*. `extraction` and `projections` are not merely on different layers — the split is held by `lint-imports`, by `TestNoStoreReachesExtraction` introspecting a parameter list, and by `ExtractionPipeline.record` returning an event and writing nothing. A second module earns this layer by naming a second such pair and the checks that keep it apart. If the separation it claims to bridge has no test that fails when it closes, the honest fix is to delete the separation, not to add a bridge over it.

One clarification, since the rule is about modules rather than symbols: `composition.py` exports four public names — `build_graph`, `GraphBuildReport`, `AUTO` and `AutoDomain` — and that is not a violation waiting to be split up. `GraphBuildReport` is `build_graph`'s return type, and `AutoDomain` is public only because the signature names it, which [ADR 0006](0006-the-public-surface-is-gated.md) requires. Splitting them into their own modules would satisfy nothing and lose the docstring that explains why `AUTO` is an object rather than the string `"auto"`. The unit that matters is the composition, not the file count.

The remaining risk is the one no test covers and no rule prevents: this layer is visibly disproportionate, one file above six siblings, and the tidying instinct will keep arriving. Both the module docstring of `src/redstring/composition.py` and the inline comment on the `layers` list exist to meet that instinct at the point of the edit, since neither a reviewer nor an author reliably reads an ADR before adding a file. Keep all three copies saying the same thing — the final section of this document says exactly how much each is allowed to say.

## Decision 2: `build_graph` folds the event straight into the store

`build_graph` extracts, asks the aggregate for an event, and — when there is
one — applies it immediately:

```python
result = await pipeline.extract(document, tenant_id)
event = await pipeline.record(
    aggregate, document, tenant_id, allow_partial=allow_partial, result=result
)

if event is not None:
    await GraphProjection(store).handle(event)
```

There is no event store in that sequence. The event exists, it is a real
`DocumentExtracted` produced by `Document.record_extraction`, and it is folded
into the `GraphStore` by the same `GraphProjection` a replay would use — but
nothing appends it anywhere, and once `build_graph` returns, the only record
that the extraction happened is the graph itself.

The alternative was available and is not hypothetical: `redstring.projections`
already exports `project`, which drives a projection over a `GlobalEventFeed`,
and [Drive projections from an event store](../how-to/drive-projections-from-an-event-store.md)
is the how-to for exactly that path. Making `build_graph` take an `EventStore`,
append to it, and let a projection catch up would have been the architecturally
tidy shape — the write model writes events, the read model derives from them,
and the library's entry point demonstrates its own claim.

It was rejected because of who has to supply the store. `redstring` provides
neither an `EventStore` implementation nor a feed; both are ports, satisfied by
`eventsource` adapters the caller wires up. Requiring one would make "extract
one document into a graph" — the single question the library had no answer to
before slice 10 — conditional on infrastructure the library does not ship. A
first call that cannot be written without provisioning an event log is a first
call most readers never make, and the failure mode of that is not an unused
feature but a `GraphStore` populated by hand from `ExtractionPipeline` output,
which is the store-in-extraction shape the whole layer split exists to prevent.

What makes the trade acceptable is that the rejected path stays open at zero
cost. `GraphBuildReport.event` is the same object the projection just consumed,
not a copy — `tests/unit/test_composition.py` asserts identity for this reason,
because a copy would let the log and the store drift while every test still
passed. A caller who does have an event store appends `report.event` and has
lost nothing by having gone through `build_graph` first.

What is given up is real, and it is two specific things rather than a vague
loss of purity. Both are named in the module docstring so a reader of the code
meets them without reading this file, and the two subsections below say what
each costs in practice:

- **Idempotency is per call, not per document.** The aggregate's refusal to
  extract twice under one `model_version` lives in aggregate *state*, and
  `build_graph` constructs a fresh `Document` on every call.
- **There is no log to rebuild from.** Replay-equivalence is a property this
  codebase tests and relies on; a store filled by `build_graph` has nothing to
  replay.

Neither cost is a defect to be fixed later. They are what a caller buys by not
running an event store, and the honest thing is to price them where the caller
can see them rather than let them be discovered from a duplicate model bill or
an unrecoverable store.

### Cost 1: idempotency is per call, not per document

The aggregate has exactly one rule, and it is an idempotency rule.
`Document.record_extraction` returns `None` and emits nothing when
`model_version` is already in `DocumentState.extraction_model_versions`, so a
retry after a crash is a no-op rather than a second write of the same ten
thousand entities. `aggregates/document.py` argues the key at length: it is the
model version and not the payload, because decoding is not deterministic and
comparing payloads would classify every retry as a new extraction.

`build_graph` gets none of that protection, because the rule lives in aggregate
*state* and the function builds a fresh aggregate on every call:

```python
aggregate = Document(document_stream(tenant_id=tenant_id, source_id=document.id).aggregate_id)
```

The stream id is derived and therefore stable — the same document under the
same tenant addresses the same stream every time — but nothing loads that
stream. A newly constructed `Document` has an empty `extraction_model_versions`
list, so the membership check that would refuse a repeat is asked of a state
that has never seen anything. Two `build_graph` calls for one document under
one model extract twice, and the second call returns an event rather than
`None`.

That is why the docstring's `Returns:` says `report.event is None` "only
happens if you pass the same one twice" — a rehydrated aggregate is the only
way to reach the refusal through this entry point, and `build_graph` does not
accept one.

**What this does not cost is a corrupted store.** Entity ids are derived, not
invented: `entity_id_for` in `extraction/mapping.py` is a pure function of
`(tenant, source, entity type, normalized name)`, and every write in
`GraphProjection` is an `upsert` or an idempotent `delete`. The second run
therefore lands on the first run's entities instead of doubling them. The two
runs may disagree in detail — the same model can word a description
differently — and the later one wins, which is the same "last write wins" a
re-extraction under a new model version already has.

So the cost is money and time, not correctness: **the model was paid for
twice.** That is a bill, and a bill is discovered late and attributed to
something else. Two documents' worth of tokens for one document does not look
like a design consequence at the point it shows up; it looks like a pricing
surprise. Naming it here and in the module docstring is the only thing that
turns it back into a decision.

Three ways to get the property back, in increasing order of what they ask of
the caller:

- **Do not call twice.** For a one-shot script or a first integration this is
  the whole answer, and it is why the shape is acceptable as a default.
- **Keep the aggregate.** The refusal is real whenever the state is real, so a
  caller with an `EventStore` loads the `Document`, calls
  `ExtractionPipeline.record` directly, and gets exactly the per-document
  idempotency `build_graph` gives up. `build_graph` is a convenience over that
  path, not a replacement for it.
- **Drive the projection from the log.** Append `report.event` and let
  `redstring.projections.project` catch up — see
  [Drive projections from an event store](../how-to/drive-projections-from-an-event-store.md)
  and [ADR 0001](0001-event-log-schema-and-granularity.md) for the log's shape.
  This is the same escape hatch the next cost needs, and the section below
  describes it once.

One thing the rule does *not* let a caller do, in any of the three: re-run an
unchanged model and have it recorded. The version is the key, so a genuine
re-run needs a version bump — which is what a re-run worth recording implies
anyway. Passing the same `model_version` twice through a loaded aggregate is
refused; passing it twice through `build_graph` is charged for.

### Cost 2: there is no log to rebuild from

`redstring.projections` opens by stating what a read model is here: "a
`GraphStore` and a `VectorStore` are projections of it — derived, disposable,
and rebuildable by replay." That is not a slogan the package leaves unproven.
`tests/unit/projections/test_replay_equivalence.py` asserts a wiped store
replays to the same state, that at-least-once delivery changes nothing, and
that a replay over a live projection changes nothing — each against an oracle
recorded independently of the fold, because both sides of an equivalence run
the same code and a fold that does too little leaves them agreeing on the same
wrong state.

A store filled by `build_graph` has none of that. The event was real and the
same `GraphProjection` a rebuild would use consumed it, but nothing appended
it anywhere, so the moment `build_graph` returns, the graph is the only record
that the extraction happened. **"Derived and disposable" becomes false in one
direction and stays true in the other**: the store is still disposable —
nothing else depends on it — and it is no longer derivable, so disposing of it
disposes of the data.

The module docstring puts the consequence in one sentence, and it is the
sentence to quote when someone asks how bad this is: *a store rebuilt from
nothing is a store restored from backup.* That is a different operational
posture, not a smaller one. Backups have a retention window, an RPO, and a
restore procedure someone has to have tested; a log has none of those because
the log *is* the record. A caller who takes this path is choosing a backup
strategy whether or not they realise they have made a choice, which is the
whole reason it is written down here and in the code.

Four things stop working that
[Rebuild a projection from the event log](../how-to/rebuild-a-projection.md)
otherwise offers, and they map onto that how-to's own list of when a rebuild
is the right move:

- **The fold changed.** A corrected bug in `GraphProjection`, a new handler, a
  changed id derivation — the how-to's first reason for rebuilding, and the
  one most likely to arrive. Events already applied were applied by the old
  code and nothing will revisit them. Without a log the only repair is to
  extract every document again, which is the model bill of the entire corpus.
- **The store is wrong.** Corrupted, partially written, or restored from a
  backup nobody trusts more than the log. There is no log to trust more.
- **The store is new.** Standing a `pgvector` store up beside an in-memory
  one, or a Neo4j graph beside a test double, is a replay over a log that
  already exists. This one bites immediately rather than eventually, because
  **`build_graph` writes to a `GraphStore` and nothing else.** It takes no
  `VectorStore`, so `VectorProjection` never sees the event, and the
  embeddings half of the read model is not merely stale — it was never built,
  and there is nothing to build it from.
- **Catch-up.** The cheap version of a rebuild, `project(..., from_position=…)`
  over the tail. It needs a feed and a `Position`, and there is neither.

What is *not* lost is worth being precise about, because the phrase "no log"
invites more alarm than the situation deserves. The event is not destroyed —
`GraphBuildReport.event` is returned, and
`tests/unit/test_composition.py::test_the_returned_event_is_what_the_projection_consumed`
asserts it is the object the projection consumed rather than a copy, so a
caller who appends it has a log entry that cannot have drifted from what was
written. Nor does anything become unrecoverable that was never recoverable:
the source documents are the caller's, this library never fetched them, and
re-extraction reproduces the graph modulo model non-determinism. The loss is
that recovery costs model calls over the whole corpus instead of a fold over
a log, and that the two runs may disagree in detail where the log would have
been exact.

So the decision reduces to a question the caller can actually answer: **is
this graph cheaper to rebuild or cheaper to log?** For a script, a first
integration, or a corpus small enough to re-extract, the answer is rebuild,
and `build_graph` is right. For a corpus whose extraction is a real bill, or a
store that will outlive the code that wrote it, the answer is log — and the
next section describes the two lines that get you there, since it is the same
escape hatch [Cost 1](#cost-1-idempotency-is-per-call-not-per-document) needs.

### The escape hatch: `report.event` and `projections.project`

Both costs above have the same remedy, and it is deliberately small: **the
event `build_graph` produced is returned, and `redstring.projections` already
exports the function that folds a feed of such events into a store.** Nothing
else had to be built for the logged path, and nothing about `build_graph`
needs to change to take it.

```python
report = await build_graph(document, provider=provider, store=store, tenant_id=tenant_id)

if report.event is not None:
    await appender.append(document_stream(tenant_id=tenant_id, source_id=document.id), [report.event])
```

From there the read model is derived rather than written:
`await project(feed, [GraphProjection(graph), VectorProjection(vectors, embedder)])`.
[Drive projections from an event store](../how-to/drive-projections-from-an-event-store.md)
is the step-by-step version, including which `eventsource` ports the caller has
to supply; [ADR 0001](0001-event-log-schema-and-granularity.md) is why one
coarse `DocumentExtracted` per document per model version is the thing being
appended.

**`GraphBuildReport.event` is the event the projection consumed, not a
reconstruction of it.** That is the property the hatch rests on, and it is
tested rather than assumed:
`tests/unit/test_composition.py::test_the_returned_event_is_what_the_projection_consumed`
asserts the entity ids in the store are exactly the entity ids in
`report.event`, with a comment saying why — *if it were a copy, or a different
event, the log and the store would diverge from the first append*. A returned
event that merely described the same extraction would be worse than no event
at all: the divergence would begin at the first append and be invisible until
a rebuild produced a different graph from the one the code had been serving.

What the hatch does and does not recover, taken one cost at a time:

- **Cost 2 it recovers completely.** Once the event is appended, the store is
  derived and disposable again in both directions, and all four of the things
  named above come back: a changed fold can be replayed, a corrupt store can
  be rebuilt from something more trustworthy than a backup, a new store —
  including the `VectorStore` `build_graph` never touches — can be stood up by
  replaying the log into `VectorProjection`, and `project(..., from_position=…)`
  can catch up over the tail.
- **Cost 1 it does not.** Appending after the fact does not give the aggregate
  its memory back. `build_graph` still constructed a fresh `Document`, so it
  still extracted, and the model was still paid. The log now records *that*
  extraction, which means a second `build_graph` call for the same document
  under the same model appends a second `DocumentExtracted` — and the log is
  the one place where a duplicate is visible rather than absorbed by an
  upsert. Per-document idempotency comes back only by loading the aggregate
  from the log and calling `ExtractionPipeline.record` directly, which is
  [Use the write model](../how-to/use-the-write-model.md), not this hatch.

Two smaller points that are easy to get wrong, and that the how-to states in
full because a caller meets them immediately:

- **`store` is not optional, so "log instead of write" is a choice about
  *which* store.** `build_graph` folds the event into whatever `GraphStore` it
  was handed before returning. A caller who wants the log to be the only path
  into the real read model passes a throwaway store and lets `project` write
  the real one — which has the merit that a rebuild then exercises the same
  code path that populated the store originally. A caller who wants the graph
  live the moment extraction returns passes the real store and accepts that
  the event is folded twice, which is safe rather than merely tolerated:
  every `GraphProjection` write is an upsert or an idempotent delete, the same
  property at-least-once delivery already relies on.
- **Handle `report.event is None` even though it cannot fire here.** The type
  says `DocumentExtracted | None`, appending `None` is a type error, and the
  guard costs a line. It is unreachable through `build_graph` for exactly the
  reason Cost 1 exists — the aggregate is always fresh — and it becomes
  reachable the moment the caller moves to a loaded aggregate, which is the
  direction this hatch points.

The reason to record all of this as a *decision* rather than a tip is that the
hatch is what makes Decision 2 defensible. Folding straight into the store is
acceptable because the alternative was not removed, only left unwired: the
event is real, it is returned, the projection that would replay it is the one
that consumed it, and `project` is already public and already tested for
replay equivalence. If any of those four stopped being true, `build_graph`'s
shape would stop being a trade and start being a dead end — which is the thing
to check first if someone proposes making `event` a summary, a copy, or
private.

## Decision 3: `AUTO` is a sentinel object, and its extra model call is priced in the signature

`build_graph`'s `domain` parameter is typed `str | DomainSchema | AutoDomain | None`, and the way a caller asks for automatic classification is a module-level object:

```python
AUTO: Final = AutoDomain()
```

Two things were decided at once here, and they are separable: *how* the request is spelled, and *whether* the cost of honouring it is visible from the signature. They are recorded together because both were forced by the same parameter — `domain` is the field that carries all four possibilities, so anything overloaded into it has to coexist with the other three.

**The spelling: an object, not the string `"auto"`.** `domain` already accepts a domain id, so a magic string in that same field takes a name out of the id space. A schema whose `domain_id` is `auto` becomes unreachable through the public entry point — not misbehaved, not warned about, just permanently interpreted as a request for the classifier. That is not hypothetical in this codebase: `ScrapingJob.extraction_strategy` was a free-form field in which a magic string decided control flow, and the comment on `AUTO` names it, because the argument for the sentinel is the memory of that field rather than a general principle about stringly-typed code. Domain ids come from YAML that callers author (see [Author a domain schema](../how-to/author-a-domain-schema.md)), so the id space is not the library's to reserve from.

The sentinel also makes the request checkable by type rather than by value. `_resolve_prompt` dispatches on `isinstance(domain, AutoDomain)` *before* the `isinstance(domain, str)` branch, so the two cases cannot be confused by any input, and a typo — `domain="atuo"` — is an `UnknownDomainError` naming an id no schema has, rather than a silent fall-through to the default prompt.

**The cost: one extra model call, stated in the parameter docs and in the module docstring.** `AUTO` runs `ContentClassifier(provider).classify(document.text)` before any extraction call. The other three values of `domain` cost nothing extra: `None` returns `DEFAULT_SYSTEM_PROMPT`, a `str` or a `DomainSchema` goes straight to `domain_system_prompt`. So the docstring's phrasing — "`AUTO` to have `ContentClassifier` choose, at the cost of one extra model call" — is the price tag on the one branch that has one, in the place a caller reads before choosing.

The bill is bounded, and the bound is the property worth protecting: **the classifier is called once per document, not once per chunk.** It sees the head of the text (truncated to `MAX_CONTENT_FOR_CLASSIFICATION`), not every window, so `AUTO` adds exactly one call regardless of document length. `tests/unit/test_composition.py::test_auto_costs_exactly_one_extra_call_for_the_classifier` asserts `len(provider.calls) == 2` against a one-chunk document, with a comment saying why the number is the point — a per-chunk classifier would multiply the bill by the chunk count while producing the same answer each time. That test is a cost gate wearing the clothes of a behavioural test; a refactor that moved classification inside the chunk loop would keep every other test in the file green.

**`AUTO` never fails, which is why `report.domain_confidence` exists.** `ContentClassifier` falls back to `encyclopedia_wiki` with confidence `0.0` on three separate paths: content under `MIN_CONTENT_LENGTH` (100 characters), which is never sent to the model at all; an answer below `confidence_threshold`; and any `LlmProviderError`. Falling back is the right behaviour *there* — a misclassified document is extracted with a worse schema, whereas raising would hand the domain decision straight back to the caller who used `AUTO` precisely to avoid making it, an argument [ADR: domain schemas prompt but do not constrain](0007-domain-schemas-prompt-but-do-not-constrain.md) develops in full.

But a fallback that returns the same shape as a choice is a plausible answer nobody investigates. So the confidence is carried out of the classifier rather than logged and dropped, and the report distinguishes three states rather than two:

| `domain` argument | `report.domain` | `report.domain_confidence` |
|---|---|---|
| `None` | `None` | `None` |
| a `str` or `DomainSchema` | that domain id | `None` |
| `AUTO`, classifier confident | the chosen id | the classifier's number |
| `AUTO`, classifier gave up | `encyclopedia_wiki` | `0.0` |

The `None`-versus-`0.0` distinction in the last two rows is load-bearing and is tested as such: `test_without_auto_there_is_no_confidence_to_report` asserts `None` for an explicit domain, with the reason in a comment — *a caller filtering on `domain_confidence == 0.0` to find give-ups must not catch every run that named its domain.* Reporting `0.0` for the non-classified cases would have been simpler and would have made the give-up filter useless, which is the recurring failure shape in this repo: a value that makes two different situations agree.

What this costs is surface area. `AutoDomain` has to be public, an unused-looking class with one `__repr__` and an instruction not to construct it, and a fourth member in a union type that already had three. That is the subject of the next section — the cost is real, and it is paid to a gate rather than to taste.

The alternatives that were not taken, and what each gives up:

- **`domain="auto"`.** Cheapest to write, takes `auto` out of the caller's id space forever, and makes the mistake invisible: the caller who names a schema `auto` gets classification, not an error.
- **A separate `classify: bool = False` parameter.** Keeps `domain` clean and creates a contradiction to validate — `classify=True, domain="news_journalism"` has to mean something, and every answer is a rule the caller has to learn. One parameter with four mutually exclusive values has no invalid combination in it.
- **An enum member, `Domain.AUTO`.** Equivalent in safety and heavier at the call site; an enum with one member is a sentinel with extra steps, and a second member would be a domain id, which is exactly the space the sentinel is trying to stay out of.
- **Classifying by default when `domain is None`.** Makes the default path cost a model call, which is the shape this decision exists to avoid. A cost the caller did not ask for is discovered from a bill, and `None` meaning "the general-purpose prompt, no extra call" is the one reading that can never surprise anyone.

## Related: `AutoDomain` is public because the signature names it

`AutoDomain` is exported from `redstring.__init__` and listed in `__all__`. It is a class with one method — `__repr__`, returning `"AUTO"` — a docstring telling the reader not to construct one, and no other content. On any ordinary reading of "public API" it does not belong there: nobody calls it, nobody subclasses it, and the only instance anyone should ever hold is the module-level `AUTO`. It is public for one reason, and the reason is mechanical rather than aesthetic.

`build_graph`'s `domain` parameter is annotated `str | DomainSchema | AutoDomain | None`. [ADR 0006](0006-the-public-surface-is-gated.md) records the rule that follows from that: **every identifier in an exported signature must itself be exported, or be a foreign type recorded with its import path.** `tests/unit/test_public_surface_is_self_contained.py::test_exported_name_mentions_only_reachable_types` parses `composition.py`, pulls the identifiers out of every annotation on `build_graph` and `GraphBuildReport`, and fails on any that a caller cannot reach. A private `_Auto` would leave the union naming a type the caller is told about and cannot import — the annotation advertises it, `from redstring import _Auto` does not work, and the signature has become a description of something unavailable.

That failure mode is not hypothetical here. It is one of the four findings that caused the gate to be written at all, and slice 10's review found it on this very parameter: `domain` accepted and advertised a `DomainSchema` that had no public constructor. `DomainSchema` was exported in response, and so were `load_schema_from_file` and `load_schema_from_string` — because a type in a signature has to be *constructible*, not merely importable. `AutoDomain` is the same finding on the same parameter, resolved the same way. The union has four members and every one of them is reachable: `str`, `DomainSchema`, `AutoDomain`, `None`.

The class's own docstring says this in the place a reader meets it first:

> A private `_Auto` would leave `domain: str | DomainSchema | AutoDomain | None` on the public surface, telling a caller who reads the signature that there is a type they cannot have.

Two alternatives would have kept the class private, and both cost more than the export does.

- **Widen the annotation** to `str | DomainSchema | object | None`, or drop it to `Any`. This passes the gate by telling the caller nothing: the type checker stops rejecting `domain=3`, and the sentinel's second benefit — that `_resolve_prompt` dispatches on `isinstance(domain, AutoDomain)` before the `str` branch, so no input can confuse the cases — is no longer visible to anyone reading the signature. Trading a precise annotation for a smaller `__all__` is trading the thing callers use for the thing maintainers count.
- **Add `AutoDomain` to `DOCUMENTED_FOREIGN_TYPES`.** It would not fit and should not be made to. That list is for types belonging to *other packages* — the `eventsource` types in `project`'s signature — where re-exporting under our own name would be worse than depending on them openly. `AutoDomain` is defined in `redstring/composition.py`. Putting it there would turn a list that records an answer into a list that silences a check, which is the distinction the list's own comment draws.

The export is cheap because the surface is gated rather than curated, and that distinction is the point of the cross-reference. `__all__` is not a hand-picked highlight reel where an odd-looking member is a blemish; it is the closure of what the exported signatures name. `Entity` obliging `TemporalExtent` obliging `DatePrecision` is the same mechanism producing a longer chain. `AutoDomain` is a one-link chain, and it looks strange only if you expect `__all__` to have been curated for elegance.

Two consequences worth stating for the next person who reads the export list and reaches for the delete key.

**Deleting `AutoDomain` from `__all__` fails the suite, and that is the design working.** Not a lint warning, not a review comment: `test_exported_name_mentions_only_reachable_types[build_graph]` fails with the identifier and the annotation it came from, and an error message saying to export the type or record it as foreign. The gate exists because four leaks passed review, and four occurrences is a missing gate rather than four mistakes.

**If `domain` ever stops naming it, the export goes.** The obligation is created by the annotation and lasts exactly as long as it does. There is no staleness check that would catch an `AutoDomain` left in `__all__` after the union changed — `test_no_documented_foreign_type_is_stale` covers the foreign-type list, and F822 covers `__all__` entries that name nothing, but neither sees a name that resolves and is no longer required by any signature. So it is a review habit, and it is the narrow version of the same rule this ADR's other sections keep applying: an entry justified by a specific fact should be deleted when that fact stops being true, and the justification has to be written down or nobody can tell.

For the same reason, `AUTO` itself is exported but is *not* checked by this gate: it is an instance, not a class or a function, so `_gated_names()` skips it — the module's comment names it and `__version__` as the only two exports without a signature. `AUTO` is on the surface because it is the value callers pass; `AutoDomain` is on the surface because the parameter that takes that value has a type.

## Where this reasoning is duplicated, and which copy is canonical

The argument above is written down in five places. That is not accidental duplication to be cleaned up, and it is not a licence to let the copies drift: each one is read by somebody who will not read the others, at the moment they are about to make the mistake it prevents. A reviewer looking at a new file under `redstring/composition/` is reading a diff, not an ADR. Somebody adding a layer name is looking at `pyproject.toml`. Somebody deciding whether to call `build_graph` at all is reading the module docstring or the README.

**This ADR is canonical.** When two copies disagree, this file is right and the other is stale. When the decision itself changes, it changes here first and the other four are updated in the same commit.

What each copy is allowed to say:

| Copy | Scope | Must not |
|---|---|---|
| `docs/adr/0007-composition-is-the-only-top-layer.md` (this file) | Everything: the alternatives, the costs, the admission test, the escape hatch, what each check catches and what it is blind to. | — |
| `src/redstring/composition.py` module docstring | The two costs of Decision 2 in the words a caller needs before calling (`report.event` is the way out), that this is a layer of its own and why, and that `AUTO` costs one call per document rather than per chunk. | Re-argue the alternatives. A reader here has already chosen to use the module. |
| The comment on `layers` in `pyproject.toml` | Why `composition` exists at all — `extraction` may not import `projections`, and something must hold both — and the admission question. | Say anything about `build_graph`'s behaviour. Nothing in the import contract depends on whether an event store is involved. |
| "How it fits together" in [README.md](../../README.md) | That there are two producers, one projection, and that `build_graph` does the whole thing in one call while a caller with an event store appends `report.event` and drives `project` instead. | Enumerate the costs. The README's job is to make the fork visible, not to price it. |
| The architecture-contract block in `CLAUDE.md` | The one-line rule an author needs mid-edit: `composition` is the top layer, holds one module, and a second module has to say what it composes. | Duplicate the ADR's reasoning. `CLAUDE.md` is binding instructions, and a long argument there is an argument nobody finishes reading. |

The common thread is that **each copy carries only what its reader can act on at that spot**, and every copy ends on the same sentence rather than a paraphrase of it: *if a second module wants in here, ask what it composes.* That sentence is the one thing all five share verbatim, which makes a drifted copy findable by grep.

Two hazards specific to this arrangement, both instances of rules recorded elsewhere in the repo.

**A stale copy in binding instructions is worse than no copy.** `CLAUDE.md` says to keep its layer block in step with `pyproject.toml`, "a stale layer diagram in binding instructions sends the next author to a package that does not exist" — and `services`, `models`, `db`, `schemas`, `cache`, `config` and `context` are all names that were in that list and are now deleted packages. The failure is not that the documentation is wrong; it is that an author trusts it and writes an import against nothing.

**Nothing checks any of this.** There is no test that the five copies agree, in the way `tests/unit/test_end_to_end_example.py` checks the example still runs or `test_no_documented_foreign_type_is_stale` checks a list still names real things. That is a deliberate gap rather than an oversight: a test comparing prose to prose either compares string equality — which forbids each copy having its own scope, the thing that makes the duplication worth having — or compares meaning, which it cannot do. So this is a review habit, and by the standard the rest of this document applies, a rule with no failing check is the weakest kind. Treat the table above as the checklist: a change to any of the three decisions is not finished until every row that covers it has been read and either updated or confirmed unaffected.

## Consequences

**The library has an entry point, and it is one call.** `build_graph(document, provider=..., store=..., tenant_id=...)` answers the question `redstring` had no answer to through nine slices. Everything after the first argument is keyword-only — `tests/unit/test_composition.py::test_everything_after_the_document_must_be_passed_by_name` pins that — so a caller cannot get `provider` and `store` the wrong way round, and adding a parameter later cannot silently shift the meaning of an existing positional one.

**`extraction` still cannot see a store, and now there is nowhere for one to be smuggled in.** Before the top layer existed, the pressure to add `graph_store=` to `ExtractionPipeline.__init__` had no legal outlet; the checks would have refused it and the caller would still have needed a join. Now the answer to "where does the store go?" is a module that already exists, which is the point: a rule that forbids something without providing the alternative is a rule that eventually loses.

**A `GraphStore` filled by `build_graph` is not derivable.** This is the consequence that shows up latest and hurts most, and it is the one to check before adopting the entry point at scale: no log means no replay after a fold change, no rebuild of a corrupt store, no standing up a second store, and — immediately rather than eventually — **no vector half at all**, because `build_graph` takes no `VectorStore` and `VectorProjection` never sees the event. The remedy is two lines and is in the shape of the API rather than in a future version: append `report.event`, drive [`project`](../how-to/drive-projections-from-an-event-store.md). What makes that remedy real is a property that must not be traded away — `report.event` is the object the projection consumed, asserted by `test_the_returned_event_is_what_the_projection_consumed`. **If someone proposes making `event` a summary, a copy, or private, Decision 2 stops being a trade and becomes a dead end.**

**Re-running the same document under the same model is charged for, not refused.** `build_graph` constructs a fresh `Document` every call, so the aggregate's one rule never fires through this path. The store stays correct — ids are derived by `entity_id_for` and every projection write is an upsert or an idempotent delete — so the damage is a duplicate model bill, which is the kind of thing discovered from an invoice and attributed to something else. Per-document idempotency is available and costs an event store: load the aggregate, call `ExtractionPipeline.record` yourself ([Use the write model](../how-to/use-the-write-model.md)).

**`__all__` grew by a class nobody calls.** `AutoDomain` is exported because `domain: str | DomainSchema | AutoDomain | None` names it and [ADR 0006](0006-the-public-surface-is-gated.md)'s gate refuses a signature naming a type a caller cannot reach. Deleting it from `__all__` fails `test_exported_name_mentions_only_reachable_types[build_graph]`. The reverse — leaving it exported after the union stops naming it — fails nothing, so it is a review obligation with a stated trigger rather than a check.

**Two cost gates now wear the clothes of behavioural tests**, and both are easy to break with a refactor that keeps the graph correct. `test_a_one_chunk_document_costs_exactly_one_call` exists because `build_graph` extracts once and hands the `result` to `record` rather than letting `record` extract again; `test_auto_costs_exactly_one_extra_call_for_the_classifier` asserts `len(provider.calls) == 2` because a classifier moved inside the chunk loop would give the same answer and multiply the bill by the chunk count. Neither failure is visible in any assertion about the resulting store.

**One layer is now maintained by review rather than by a gate.** `lint-imports` checks direction, `exhaustive = true` checks new top-level packages, and nothing checks that `composition` holds exactly one module. By this document's own standard that is the weakest kind of rule, which is why the admission test is written out concretely and why the same closing sentence — *if a second module wants in here, ask what it composes* — appears verbatim in the module docstring, in `pyproject.toml`, and in `CLAUDE.md`, where an author meets it mid-edit. Its being verbatim is deliberate: a drifted copy is findable by grep, which is as close to a check as prose gets.

**Five copies of this reasoning now have to be kept in step, with nothing to tell you when they are not.** The table above is the checklist. The failure mode worth fearing is the one `CLAUDE.md` already names for the layer diagram — an author trusts a stale copy and writes against a package that does not exist — and `services`, `models`, `db`, `schemas`, `cache`, `config` and `context` are the evidence that it happens here.

**What would reopen this ADR.** Decision 1 changes if a second module can name a pair of layers whose separation is itself defended by a failing check. Decision 2 changes if `redstring` ever ships an `EventStore` adapter of its own, since the argument against the logged default is entirely that the caller must supply infrastructure the library does not provide. Decision 3 changes if `domain` stops being one parameter carrying four mutually exclusive values — and only then, because every alternative considered was rejected for what it does *inside that parameter*.
