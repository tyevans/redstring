# Drive projections from an event store

This guide shows you how to run extraction and projection as two separate
steps with a durable event log in between: extract with `build_graph`, append
the `DocumentExtracted` event it returns to an `EventStore`, and drive
`redstring.project` over the store's global feed to fold that log into a
`GraphStore` and a `VectorStore`.

`build_graph` on its own already writes to a graph store — it applies the
event to a `GraphProjection` for you and returns the same event as
`report.event`. Taking this path replaces that single in-process fold with a
log you own, which is what buys you a store you can wipe and rebuild, a
checkpoint to resume from, and a dead-letter queue for events that could not
be applied.

By the end you will have:

- a script that extracts a document and appends its event to an event store,
- `GraphProjection` and `VectorProjection` constructed with a checkpoint
  repository, a DLQ repository and a retry policy,
- a `replay` call over the feed, and a `ReplayReport` you know how to read,
- a rebuild-from-zero you can run twice without corrupting the result.

This is a task guide, not a tour of the event model. For what the events mean
and which handler consumes each one, see
[the events reference](../reference/events.md); for why the log is shaped
around a coarse per-document event, see
[ADR 0001](../adr/0001-event-log-schema-and-granularity.md). For why one
module is allowed to hold both the extraction pipeline and the projections,
see [ADR 0007](../adr/0007-composition-is-the-only-top-layer.md).

If all you want is to restore one store from an existing log, the shorter
recipe is [Rebuild a projection](rebuild-a-projection.md). If you want to
drive the aggregate yourself rather than let `build_graph` build one per call,
see [Use the write model](use-the-write-model.md).

## When you need this (and when `build_graph` alone is enough)

Take this path when you need at least one of three things:

- **A log you can rebuild from.** `build_graph` folds the event into the graph
  store and then lets it go. Nothing else keeps it, so a store rebuilt from
  nothing is a store restored from backup, not from the record.
- **Idempotency per document rather than per call.** `Document.record_extraction`
  refuses a second extraction under the same `model_version`, but that refusal
  lives in the aggregate's state and `build_graph` builds a fresh aggregate
  every call. Two calls for one document under one model extract twice — the
  store still ends up right, because every projection write is an upsert, but
  you paid the model twice.
- **A vector store.** `build_graph` takes a `GraphStore` and constructs a
  `GraphProjection`; there is no parameter for a `VectorStore`. Driving
  projections yourself is how you fold the same event into both.

A checkpoint to resume from and a dead-letter queue for events that would not
apply come with the same move: both are constructor arguments on the
projections, and `build_graph` passes neither.

**If none of those apply, `build_graph` is the right call and this guide is
overhead.** A caller with no event store — most callers, most of the time —
gets a populated graph in one `await`, and every arrow in this guide is
already wired inside it. Adding an event store to gain a rebuild you will
never run is a durable component to operate in exchange for nothing.

You do not need this guide to *write* to the log by hand. If what you want is
to drive the aggregates yourself — record an extraction, merge entities, undo a
merge — that is [Use the write model](use-the-write-model.md), and it stops at
`repo.save(...)`. This guide picks up on the other side: the events are already
in the store, and something has to fold them into the read models.

And if the log already exists and one store is wrong, stale, or newly added,
you want the shorter recipe: wipe that store per tenant and replay from zero,
in [Rebuild a projection](rebuild-a-projection.md). The difference is not the
machinery — both end in a `replay` call — but the scope: that guide restores
one read model, this one stands the whole path up, extraction included.

## What `build_graph` gives up: per-call idempotency and no log to rebuild from

Before you wire any of this up, be clear about what the one-call path does and
does not promise, because both limits are structural rather than defects, and
both are the reason the rest of this guide exists.

**The aggregate is built per call, so its idempotency is per call.**
`build_graph` constructs the `Document` itself:

```python
aggregate = Document(document_stream(tenant_id=tenant_id, source_id=document.id).aggregate_id)
```

It is never loaded from a repository and never saved to one. A fresh aggregate
has an empty `extraction_model_versions`, so the check in
`Document.record_extraction` — "have I already recorded this
`model_version`?" — is being asked of state that was created moments ago and
is discarded when the call returns. Two `build_graph` calls for the same
document under the same model therefore both extract, both emit a
`DocumentExtracted`, and both write. `report.event is None` shows up only if
you pass the *same aggregate* twice, which `build_graph` gives you no way to
do.

What that costs you is the model call, not the graph. Every write in
`GraphProjection` is an upsert or an idempotent delete, so the second fold
lands on the same ids and the store ends up in the same state. The damage is
the bill and the latency, and neither is visible in the result.

**The event is applied and then dropped.** `build_graph` does exactly this
with it:

```python
if event is not None:
    await GraphProjection(store).handle(event)
```

The projection is constructed inline, used once, and collected. Nothing
appends the event anywhere. `report.event` is the *only* reference that
survives the call, and if you let it go the record of that extraction exists
solely as the entities and relationships now sitting in the graph store. A
store you wipe is a store you can restore from a backup of itself, not from
the log — because there is no log.

The two limits are the same missing component seen twice. An `EventStore`
gives `Document` somewhere to be loaded from, which turns "already extracted
under this model" into a fact about the document rather than about this
process; and it gives the event somewhere to live after the fold, which is
what makes a rebuild possible. That is why the very next step of this guide is
appending `report.event`, and why everything after it reads from the feed
rather than from a return value.

Three smaller things `build_graph` does not carry, all of which follow from
folding in-process:

- **No checkpoint.** There is no position to resume from, because there is no
  feed being consumed.
- **No dead-letter queue.** A handler that raises propagates out of
  `build_graph` to you, mid-write. There is nowhere for the bad event to go
  and nothing that records it was skipped.
- **No second read model.** The signature takes a `GraphStore`; a
  `VectorProjection` needs an event to hand it, which means a log.

Two properties do carry over unchanged, and it is worth naming them so you do
not go looking for replacements:

- **Partial extractions are still refused before anything is written.** With
  `allow_partial=False`, `PartialExtractionError` is raised while the event is
  being recorded, ahead of the projection — so on this path it means nothing
  reaches the event store either.
- **The event's granularity is the same event.** `DocumentExtracted` is one
  coarse event per document per model version whether you fold it in-process
  or append it; see
  [ADR 0001](../adr/0001-event-log-schema-and-granularity.md) for why it is
  shaped that way, and [the events reference](../reference/events.md) for what
  each handler does with it.

If you want the per-document idempotency but not the projection machinery,
note that it is available on its own: load the `Document` from a repository,
call `record_extraction`, save. That is
[Use the write model](use-the-write-model.md). This guide assumes you want
both halves.

## What you need before you start

Five things, three of which redstring does not supply and never will.

**An `LlmProvider`, and a document to extract.** Step 1 is still a
`build_graph` call, so you need whatever you would need for one: a
`SourceDocument` you built yourself (this library fetches nothing) and a
provider to ask. `FakeLlmProvider` is a complete implementation and validates
like the real one, which is what makes this guide runnable end to end without
a server.

**An event store that both appends and reads a global feed.** This is
`eventsource`'s, not ours — redstring never creates one. Two capabilities are
needed and they are two different ports: appending `report.event` needs an
`EventAppender` (`append(stream, events, expected)`), and `replay` needs a
`GlobalEventFeed` (`read_all(from_position)`). `eventsource`'s
`InMemoryEventStore` is both, and is a complete implementation rather than a
test double:

```python
from eventsource.adapters.memory import InMemoryEventStore

event_store = InMemoryEventStore()
```

Swap in `PostgreSQLEventStore` or `SQLiteEventStore` when the log has to
outlive the process. Nothing in this guide changes: `replay` takes the feed,
not an adapter.

**The stores you are projecting into.** A `GraphStore` for `GraphProjection`;
a `VectorStore` too if you want the second read model. `InMemoryGraphStore`
and `InMemoryVectorStore` are exported from the package root and are, again,
real implementations.

**A checkpoint repository and a DLQ repository.** Both are optional
constructor arguments on the projections, and both are the reason you are here
rather than on the one-call path — without a `dlq_repo` a failing event is
counted in `ReplayReport.failed` and otherwise lost. `eventsource` ships
in-memory ones:

```python
from eventsource.adapters.memory import (
    InMemoryCheckpointRepository,
    InMemoryDLQRepository,
)
```

Use durable ones (`SQLCheckpointRepository`, `SQLDLQRepository`) the moment
the log is durable: an in-memory DLQ over a Postgres log means the poison
events are gone at the next restart and the log still says they were never
applied.

**A retry policy, if the default is not what you want.** `ProjectionRetryPolicy`
is `eventsource`'s and is passed as `retry_policy=`. Leaving it `None` is a
decision, not an omission — see step 3, which also covers the near-miss import
that will otherwise cost you an afternoon.

### What you import, and from where

Everything of ours comes from the package root, because
`redstring.__all__` is the whole promise and a dotted path is internal:

```python
from redstring import (
    FakeLlmProvider,
    GraphProjection,
    InMemoryGraphStore,
    InMemoryVectorStore,
    SourceDocument,
    VectorProjection,
    build_graph,
    document_stream,
)
```

`replay` and `ReplayReport` are **not** in that list. They were exported here
until `eventsource-py` 0.12.0 upstreamed the rebuild driver this package had
written for itself (its ADR 0054), and they are now imported from there:

```python
from eventsource import ReplayFailedError, ReplayFailure, ReplayReport, replay
```

Which is the same rule as the foreign types below, not an exception to it:
re-exporting another package's function under our name would be worse than
depending on it openly.

`document_stream` is in that list because you need it in step 2: the event has
to be appended to the same stream the aggregate was addressed by, and
`document_stream(tenant_id=..., source_id=...)` is how that id is derived.
Deriving rather than storing it is deliberate — re-extracting a document
appends to the stream it already has.

The foreign types are `eventsource`'s and stay under their own names —
`GlobalEventFeed`, `EventSubscriber` and `Position` in `replay`'s signature,
`ProjectionCheckpoints`, `DLQRepository` and `ProjectionRetryPolicy` in the projections'
constructors (where they arrive as `ProjectionOptions`, eventsource's
`TypedDict` naming that option set once). The next section says where each one lives. Re-exporting another
package's ports under our names would be worse than depending on them openly,
and `eventsource` is a core dependency, so they are always importable.

### Two prerequisites that are easy to miss

**Importing `redstring` is what registers the events.** `DocumentExtracted`
and `EntitiesEmbedded` are registered with `eventsource`'s event registry at
import of `redstring.events.document`, which `import redstring` pulls in. A
store that has to rebuild an event from its serialised form — every durable
adapter — needs that registration to have happened in the reading process too,
not just the writing one. A rebuild worker that imports only `eventsource`
will fail on the first envelope.

**`VectorProjection` will not see anything `build_graph` produced.** It
handles `EntitiesEmbedded`, and extraction emits `DocumentExtracted`. Nothing
in this library computes embeddings, so the vector half of the log is yours to
append: `Document.record_embeddings(...)` with the `VectorRecord`s from
whatever embedding model you run, saved through a repository — that is
[Use the write model](use-the-write-model.md). Wire `VectorProjection` in
anyway if that is coming; a projection that ignores every event in the feed is
harmless, and `replay` still counts those events as applied.

### What you do not need

**A checkpoint to resume from, on the first run.** `replay` never consults
one. It starts wherever `from_position` says, and the default `None` means the
beginning of the log — see the resume section below, and
[Rebuild a projection](rebuild-a-projection.md) for the same property used
deliberately.

**A subscription runner.** `replay` is a loop over a feed, driven by you.
Live catch-up on a timer is a different job with a different component.

## The foreign types this path needs, and where they come from (`EventStore`, `GlobalEventFeed`, `EventSubscriber`, `Position` from `eventsource`)

Four `eventsource` names show up in the signatures you are about to call, and
none of them is re-exported under a redstring name. That is deliberate — see
the note at the end of this section — so it is worth knowing what each one is
before you go looking for ours.

### `GlobalEventFeed` — the first argument to `replay`

`replay`'s first parameter is a feed, not a store:

```python
async def project(
    feed: GlobalEventFeed,
    projections: Sequence[EventSubscriber],
    *,
    from_position: Position | None = None,
    tenant_id: UUID | None = None,
    strict: bool = False,
    max_events: int = MAX_EVENTS_PER_REPLAY,
) -> ReplayReport: ...
```

`GlobalEventFeed` lives in `eventsource.ports.store` and is a two-method
Protocol:

```python
class GlobalEventFeed(Protocol):
    def read_all(
        self,
        from_position: Position | None = None,
        options: FeedReadOptions | None = None,
    ) -> AsyncIterator[EventEnvelope]: ...
    async def current_position(self) -> Position | None: ...
```

`replay` uses exactly one of them — `read_all(from_position)`, iterated with
`async for`. That is the whole coupling. Anything with those methods works,
which is why this guide's script and a Postgres-backed rebuild are the same
code with a different constructor.

### There is no single `EventStore` type — there are five capability ports

`eventsource.ports.store` does not define one fat `EventStore`. It defines
five narrow Protocols, and the concrete adapters implement all of them:

| Port | Method you care about | Who needs it here |
|---|---|---|
| `EventAppender` | `append(stream, events, expected)` | step 2, appending `report.event` |
| `StreamReader` | `read_stream`, `get_stream_version` | loading a `Document` aggregate |
| `GlobalEventFeed` | `read_all`, `current_position` | `replay` |
| `EventLookup` | `event_exists(event_id)` | nothing in this guide |
| `CategoryQuery` | `read_category(category)` | nothing in this guide |

Two unions sit on top: `AggregateStore` (appender + stream reader — what a
repository needs, and deliberately *not* the feed) and `FullEventStore` (all
five). `InMemoryEventStore` satisfies `FullEventStore`, which is why one
object serves as both the appender in step 2 and the feed in step 4.

Type your own plumbing against the narrowest port that does the job. A rebuild
worker that takes a `GlobalEventFeed` cannot append, and that is a property
worth having in the signature rather than in a comment.

### `EventSubscriber` — what `GraphProjection` and `VectorProjection` are

`replay`'s second argument is a `Sequence[EventSubscriber]`.
`EventSubscriber` is an **ABC**, not a Protocol — `eventsource.ports.handlers`
makes it one so subclasses can add methods beyond the two abstract ones:

```python
def subscribed_to(self) -> list[type[DomainEvent]]: ...
async def handle(self, event: DomainEvent) -> None: ...
```

Our two projections are subscribers by inheritance: both extend
`StoreProjection`, which extends `eventsource`'s `DeclarativeProjection`,
which supplies `subscribed_to` from the handler declarations and wraps
`handle` with the retry, checkpoint and DLQ behaviour. You never implement
either method; you construct the projection and hand it to `replay`.

Because the parameter is a plain sequence of subscribers, a projection of your
own goes in the same list. It has to be a real `EventSubscriber` — an object
with a bare `handle` will type-fail, and the retry and DLQ behaviour lives in
the base class you would be skipping.

### `Position` — an opaque token you compare and persist, never build

`Position` (`eventsource.ports.positions`) is a frozen dataclass of a
`store_id` and an opaque `key` tuple. Three properties matter for this guide:

- **It is opaque.** The key's shape is the adapter's business. Compare
  positions, order them, store them — never do arithmetic on one, and never
  construct one to mean "position 5".
- **It is store-scoped — as far as `store_id` is distinct.** Ordering two
  positions from different stores raises `PositionForeignError`, and equality
  across stores is `False`. A checkpoint saved against one store is meaningless
  against another.

    The qualifier is load-bearing, and `eventsource-py` 0.11.0 documented it
    after the fact: the guard *is* `store_id`, and the adapter defaults are
    derived rather than unique — `memory` for the in-memory store, `pg:{database}`
    for Postgres, `sqlite::memory:` for SQLite. Two in-memory stores in one
    process, or two Postgres stores against one database, therefore share an id,
    and `PositionForeignError` silently does not fire for a pair that really is
    foreign. If you run more than one store of a kind, set `store_id` explicitly
    and set it once: **it is embedded in every persisted position and
    checkpoint**, so changing it later invalidates the checkpoints you already
    have, and a projection resumes from nowhere.
- **It is serialisable.** `to_str()` and `Position.from_str()` round-trip it,
  which is how a resume position survives a process restart if you are
  tracking it yourself rather than through a checkpoint repository.

`Position` reaches you in two places: `ReplayReport.last_position` (typed
`Position | None`, and `None` when the feed was empty, because it is only ever
set from an envelope you actually read) and `replay`'s `from_position`, which
is **exclusive** and defaults to `None` for "from the very beginning". Feeding
one back into the other is the whole resume protocol.

One adapter detail worth knowing before you build on positions:
`EventEnvelope.position` is typed `Position | None` because feedless stores
exist. A store with no global feed is not one you can drive `replay` over at
all, so on this path you will always see a real position — but that is a fact
about your adapter choice, not a guarantee from the type.

### Two more foreign types, on the projection constructors

`ProjectionCheckpoints` (`eventsource.ports.checkpoints`), `DLQRepository`
(`eventsource.ports.dlq`) and `ProjectionRetryPolicy`
(`eventsource.application.projections.retry`) reach you through
`StoreProjection`'s constructor, which both of our projections inherit
unchanged. The store is the only positional parameter; everything else is a
keyword option:

```python
def __init__(self, store: TStore, **options: Unpack[ProjectionOptions]) -> None: ...
```

The option names are not `**kwargs` in the untyped sense — they are the keys of
`ProjectionOptions`, a `total=False` `TypedDict` in
`eventsource.application.projections.store`, and that is the one place to read
them:

```python
class ProjectionOptions(TypedDict, total=False):
    checkpoint_repo: ProjectionCheckpoints | None
    dlq_repo: DLQRepository | None
    enable_tracing: bool
    retry_policy: ProjectionRetryPolicy | None
    tracer: Tracer | None
    tenant_filter: TenantFilter
```

Naming the set once, rather than restating it on every subclass, is what stops
a subclass quietly narrowing its parent's constructor — the hazard `eventsource`
widened its own projection constructors for in the first place. The practical
consequence for you is unchanged: `retry_policy`, `tenant_filter` and the
tracing switches are reachable and typed through our classes, and `Unpack`
means a type checker still rejects a misspelled option rather than swallowing
it. You do not have to reach past `GraphProjection` or `VectorProjection` to
configure the base. Step 3 covers what to pass.

### Why these keep their own names

Everything of ours comes from `redstring`'s root — `__all__` is the whole
promise. These do not, and the alternative is worse: re-exporting another
package's ports under our names would hide that a `GlobalEventFeed` you got
from `eventsource` is the same type our signature wants, and it would make
every `eventsource` upgrade a change to our public surface. `eventsource` is a
core dependency, so all of these are importable wherever `redstring` is.

The one thing that is ours and shares the neighbourhood is `ReplayReport` —
a redstring dataclass, exported from the root, described under
[Reading the `ReplayReport`](#reading-the-replayreport-applied-failed-failures-last_position)
below.

## Step 1: extract without writing — call `build_graph` and keep `report.event`

Extraction is the same call you would make on the one-call path. What changes
is what you do with the result: you keep `report.event`, because on this path
the log is the record and everything after this step reads from it.

```python
tenant_id = uuid4()
scratch_graph = InMemoryGraphStore()

report = await build_graph(
    SourceDocument(
        id="lovelace-notes",
        text="Ada Lovelace worked with Charles Babbage on the Analytical Engine.",
    ),
    provider=FakeLlmProvider(by_substring={"Ada": ANSWER}),
    store=scratch_graph,
    tenant_id=tenant_id,
)

event = report.event
```

### `store` is required, so "without writing" is a choice you make about *which* store

`build_graph`'s signature takes a `GraphStore` and there is no way to opt out:
if an event was produced, the function constructs a `GraphProjection` around
whatever you passed and folds the event into it before returning. So the
literal reading of this step is not available — the question is only where
that write lands.

Two answers, both correct:

- **Pass a throwaway store**, as above. `scratch_graph` is populated and then
  discarded, and the store you actually care about is only ever written by
  `replay` in step 4. Take this when you want the log to be the single path
  into the read model — a rebuild then exercises the same code that built the
  store in the first place, which is the property that makes a rebuild
  trustworthy.
- **Pass the real graph store.** The event is folded once now and once again
  when `replay` reaches it. That is safe rather than merely tolerable: every
  write in `GraphProjection` is an upsert or an idempotent delete, which is
  the same property the redelivery section below rests on. Take this when you
  want the graph populated the moment extraction returns and do not want to
  wait for a projection pass.

What you must not do is treat the scratch store as the record. Nothing
appends the event for you — `report.event` is the only surviving reference,
and step 2 is where it becomes durable.

### `report.event` is `None` more rarely than you would like

The field is typed `DocumentExtracted | None`, and `None` means the aggregate
declined to record: `Document.record_extraction` refuses a second extraction
under a `model_version` it has already seen. Because `build_graph` constructs
the aggregate itself —

```python
aggregate = Document(document_stream(tenant_id=tenant_id, source_id=document.id).aggregate_id)
```

— and never loads it from a repository, that state is always empty on entry.
**`report.event is None` cannot happen through `build_graph`.** Two calls for
the same document under the same model both extract and both emit.

Handle the `None` anyway. It is what the type says, appending `None` is a type
error, and the branch costs one line:

```python
if report.event is None:
    return  # already extracted under this model — nothing to append
```

If you want that guard to actually fire, the aggregate has to be loaded from
the store rather than built per call, which is
[Use the write model](use-the-write-model.md).

### The event carries the whole extraction, and validates itself

`DocumentExtracted` holds `source_id`, `model_version`, and the full
`entities` and `relationships` lists — one coarse event per document per
model version, for the reasons in
[ADR 0001](../adr/0001-event-log-schema-and-granularity.md). Its own model
validator rejects entities or relationships belonging to another tenant, and
entities attributed to a different `source_id` than the event's. So an event
that reaches your hand is internally consistent before it reaches the store;
what it does *not* promise is that its edge endpoints exist in the graph yet,
which is the poison-event case step 4 deals with.

`model_version` comes from the provider — it is `provider.model`, not
something you pass — and it is what the per-document idempotency check
compares. Swapping providers is what makes a document eligible for
re-extraction.

### What the rest of the report is for

The other fields are counters, and they are the only place some outcomes are
visible at all:

- `entities` / `relationships` — what the run found.
- `unresolved_relationships` — edges the model asserted between entities it
  never listed, so they could not be resolved to ids. Normal in small
  numbers; a large count means the prompt is not landing. These never reach
  the event.
- `failed_chunks` / `total_chunks` — non-zero only with
  `skip_failed_chunks=True`.
- `domain` and `domain_confidence` — which prompt was used and, under `AUTO`,
  how sure the classifier was. `domain_confidence` is `None` when no
  classifier ran and `0.0` when it ran and gave up.

None of these are recoverable from the log later: the event carries the
entities, not the counters. Log them here if you want them.

### Failures happen before anything exists to append

Two of `build_graph`'s errors are worth knowing on this path specifically,
because both fire ahead of the fold and therefore ahead of your append:

- **`PartialExtractionError`** — chunks failed and `allow_partial` is
  `False` (the default). It is raised inside `record`, before the aggregate
  is touched, so no event exists and nothing reaches the event store. The
  refusal cannot cause the gap it prevents. Passing `allow_partial=True`
  records the incomplete run as this model version's extraction, which makes
  the retry that would repair it a silent no-op — on this path that
  incomplete event is also what every future rebuild will replay.
- **`LlmProviderError`** — a model call failed and `skip_failed_chunks` is
  off. Same position: nothing recorded, nothing to append.

`UnknownDomainError` fires earlier still, while the prompt is being resolved.

In every case the correct response is to retry the whole step. There is no
partially-appended state to clean up, because the append has not happened
yet — that is step 2.

## Step 2: append `report.event` to the event store on the document stream

One call, and everything interesting about it is in the arguments:

```python
from eventsource.ports.positions import ExpectedVersion

from redstring import document_stream

stream = document_stream(tenant_id=tenant_id, source_id=document.id)

result = await event_store.append(stream, [event], ExpectedVersion.any_())
```

`append` is `EventAppender`'s only method, and its signature is
`append(stream, events, expected) -> AppendResult`. The three arguments are
the three decisions.

### The stream must be the one the aggregate was addressed by

`document_stream(tenant_id=..., source_id=...)` is not a helper you could
replace with a stream name of your own. `build_graph` builds its aggregate as

```python
aggregate = Document(document_stream(tenant_id=tenant_id, source_id=document.id).aggregate_id)
```

so the event's `aggregate_id` is already that stream's id. Appending it
somewhere else produces a log whose events disagree with the streams holding
them, and the `Document` aggregate then rehydrates from a stream that has none
of its history.

The id is `uuid5(tenant_id, source_id)` — derived, not stored, so re-extracting
a document appends to the stream it already has, and the same `source_id`
under two tenants is two streams. Pass the *same* `tenant_id` and
`document.id` you passed to `build_graph`; there is no field on the report that
carries the stream back to you. `document_stream` raises `ValueError` for a
blank `source_id`, which is the last point where a document with no id can be
caught — `SourceDocument.id` itself does not validate.

### `events` is a sequence, and an empty one is an error

`append` raises `ValueError("cannot append an empty batch of events")` rather
than treating a no-op batch as success. That is why step 1's `None` guard
matters in practice: `[report.event]` with `report.event is None` is not an
empty batch, it is a batch containing `None`, which fails deeper and less
legibly. Guard first, then append.

Batching several documents' events into one call is not available here even
if you want it: a batch goes to *one* stream, and every document has its own.
Adapters may cap batch size — `EventAppender.max_append_batch` is `int | None`,
and `InMemoryEventStore` sets `None` for "no limit" — which only bites when
one document yields more events than the cap, not on this path.

### Choosing the `ExpectedVersion`

`ExpectedVersion` is the optimistic-concurrency expectation, and versions are
1-based event counts with an absent stream at version 0. Four kinds:

| Constructor | Appends only if | Use when |
|---|---|---|
| `ExpectedVersion.any_()` | always | the default here — see below |
| `ExpectedVersion.no_stream()` | the document has never been extracted | first extraction must be first |
| `ExpectedVersion.stream_exists()` | it has been extracted before | a follow-up event |
| `ExpectedVersion.exact(n)` | the stream holds exactly `n` events | you read the version and are guarding it |

A mismatch raises `OptimisticLockError`, carrying `aggregate_id`,
`expected_version` and `actual_version`. Non-numeric kinds are rendered by
name in the message (`expected no_stream, but current version is 2`) rather
than as an integer, so the error says what you actually asked for.

**`any_()` is the right default on this path**, and it is a real argument
rather than laziness: `build_graph` has already extracted, the model has
already been paid for, and there is nothing to be gained by discarding the
result because another process appended to the same document meanwhile. The
guard you would want — "do not extract this document twice under this model" —
belongs before the extraction, and getting it means loading the aggregate,
which is step 1's other branch and
[Use the write model](use-the-write-model.md).

Reach for `exact(n)` only when you read the version yourself
(`get_stream_version(stream)`, on the `StreamReader` port) and are enforcing
something about it. Reach for `no_stream()` when a second extraction of a
document is a bug in your pipeline and you want it to fail loudly.

### The append is idempotent on `event_id`, and it raises to say so

Appending the same event object twice raises `DuplicateEventError` —
`event_id` is unique across the store, checked both against what is stored and
within the batch. It is not a silent no-op, so a retry loop that re-appends
after an ambiguous failure has to treat that exception as success:

```python
from eventsource.domain.exceptions import DuplicateEventError

try:
    result = await event_store.append(stream, [event], ExpectedVersion.any_())
except DuplicateEventError:
    pass  # a previous attempt got through; the log already has it
```

This is the one place on this path where at-least-once delivery is your
problem rather than the projections'. Everything downstream of the log
tolerates redelivery by construction — see
[Redelivery and re-projection](#redelivery-and-re-projection-why-applying-the-log-twice-is-safe)
below — but the log itself de-duplicates by refusing.

### No `tenant_scope` is needed for a direct append

`redstring.aggregates.document_repository` wraps its repository in
`TenantAwareRepository`, so loading and saving through it must happen inside
`async with tenant_scope(tenant_id)` and an event whose `tenant_id` disagrees
with the ambient scope is refused. A direct `event_store.append` goes through
none of that: the store takes what you give it.

What keeps you honest instead is the event's own validator.
`DocumentExtracted` rejects entities or relationships carrying a tenant other
than the event's, and entities attributed to another `source_id` — and it
does so at construction, inside `build_graph`, so an event that reached your
hand has already passed. Appending is where that guarantee stops being
re-checked, which is the argument for using the repository path if you are
constructing events yourself rather than relaying one from `build_graph`.

### What `AppendResult` gives you, and the one thing not to do with it

```python
AppendResult(stream=..., new_version=..., position=...)
```

`new_version` is the stream's version after the append — `1` for a document's
first extraction. `position` is the global-feed position of the **first**
event in the batch, and is `None` for a feedless store.

**Do not pass that position to `replay` as `from_position`.**
`from_position` is exclusive, so a replay starting there skips the event you
just appended — which is exactly backwards. It is a handle for "where in the
feed did my write land", useful for logging or for waiting on a consumer to
catch up. Resuming is the checkpoint repository's job, and rebuilding wants
`None`.

### The version stamp on a relayed event, and when it bites

`build_graph` builds a fresh aggregate every call, so **every event it returns
carries `aggregate_version=1`**, including the second and third extraction of
the same document under new models. The store does not mind: the memory
adapter computes `stream_version` from the stream's length, so the envelopes
come back correctly numbered 1, 2, 3, and both `replay` and the projections
read the event's payload rather than its version.

Rehydration does mind. `AggregateRoot.apply_event` sets
`self._version = event.aggregate_version` on replay, so a stream of two
build_graph events rehydrates to a `Document` with the right state and version
`1`:

```python
async with tenant_scope(tenant_id):
    document_aggregate = await document_repository(event_store).load(stream.aggregate_id)
# document_aggregate.version == 1, state.extraction_model_versions == ["m1", "m2"]
```

The state is correct — the idempotency check consults
`extraction_model_versions`, and both are there — but the next `save` through a
repository computes its expected version from `aggregate.version` and is
rejected:

```
OptimisticLockError: expected version 1, but current version is 2
```

So: relaying `report.event` straight into the log is sound as long as the log
is *only* written that way. The moment you mix in writes through
`document_repository`, drive the whole document through the write model
instead — load, `record_extraction`, `save` — and let the repository stamp the
versions. That is [Use the write model](use-the-write-model.md), and the
per-document idempotency you get with it is the reason to want it anyway.
`EntitiesEmbedded` is in the same position: if you are appending embeddings
for `VectorProjection` to consume, you are already on the repository path.

### If the append fails

Nothing has been written to the event store, and the extraction is gone with
the process unless you kept `report.event`. Retry the append if the failure
was transient; retry the whole of step 1 if it was not, accepting the second
model call. There is no half-appended state to repair — a batch is atomic, and
this batch is one event.

What may exist is a graph write, if step 1 passed the real graph store rather
than a scratch one: the fold happened inside `build_graph`, before you ever
got the event. That store now holds entities the log does not mention. It is
not corruption — the next successful extraction re-folds the same upserts over
them — but it is the concrete reason to prefer the scratch store when you want
"the log is the record" to be literally true.

The event carries the whole extraction, so a durable log is the only thing
between a crash here and paying for the extraction twice. Append before you do
anything else with the report.

## Step 3: construct the projections (`GraphProjection`, `VectorProjection`) with a checkpoint repository, a DLQ repository and a retry policy

Two constructors, four arguments each, and every argument past the first is
the reason you are not on the one-call path:

```python
from eventsource.adapters.memory import (
    InMemoryCheckpointRepository,
    InMemoryDLQRepository,
)
from eventsource.application.projections.retry import ExponentialBackoffRetryPolicy
from eventsource.application.subscriptions.retry import RetryConfig

from redstring import GraphProjection, InMemoryGraphStore, InMemoryVectorStore, VectorProjection

checkpoints = InMemoryCheckpointRepository()
dlq = InMemoryDLQRepository()
no_backoff = ExponentialBackoffRetryPolicy(config=RetryConfig(max_retries=0))

graph_store = InMemoryGraphStore()
vector_store = InMemoryVectorStore(dimension=768)

projections = [
    GraphProjection(
        graph_store,
        checkpoint_repo=checkpoints,
        dlq_repo=dlq,
        retry_policy=no_backoff,
    ),
    VectorProjection(
        vector_store,
        checkpoint_repo=checkpoints,
        dlq_repo=dlq,
        retry_policy=no_backoff,
    ),
]
```

Both classes inherit `StoreProjection.__init__` unchanged, so both take the
same shape: `store` positionally, then `checkpoint_repo`, `dlq_repo` and
`enable_tracing`, then `retry_policy`, `tracer` and `tenant_filter`
keyword-only. Pass the repositories by keyword anyway — `GraphProjection(store,
checkpoints, dlq)` is legal and reads as three anonymous objects.

The list order does not matter. `replay` hands every event to every
projection, each projection ignores what it has no handler for, and neither
fold reads the other's store.

### The store argument is the only required one, and the others are not defaults you can ignore

`GraphProjection(graph_store)` works. It also gives you a projection that
retries a poison event twice with real sleeps, drops it on the floor, and
keeps no record of where it got to. Each omission has a specific consequence:

| Omitted | What you lose |
|---|---|
| `checkpoint_repo` | `get_checkpoint()` returns `None` forever; nothing records what has been delivered |
| `dlq_repo` | a permanently failed event is counted in `ReplayReport.failed` and otherwise gone |
| `retry_policy` | the base class's default: 3 attempts with ~2s and ~4s sleeps per failing event |

None of them changes what a *successful* fold does. They are the difference
between a rebuild you can operate and one you can only rerun.

### The checkpoint is keyed by class name, and it is an event id — not a position

`CheckpointTrackingProjection` sets `self._projection_name =
self.__class__.__name__`, and every checkpoint read and write goes through
that string. Two consequences:

- **One repository can serve both projections.** They are `"GraphProjection"`
  and `"VectorProjection"`, so their rows do not collide — the shared
  `checkpoints` above is fine. Give each its own repository if you prefer;
  nothing in either fold reads a checkpoint.
- **Two instances of the same class share a row.** A second `GraphProjection`
  over a second store, on the same checkpoint repository, is one checkpoint
  describing two different amounts of progress. Give those separate
  repositories.

What lands in the row is `event_id` and `event_type` — `update_checkpoint`
takes no position, and `get_checkpoint()` hands you back a string. **You
cannot feed it to `replay`'s `from_position`**, which wants a `Position`.
These are two mechanisms that sound like one, and
`tests/unit/projections/test_checkpoints.py` exists to keep them apart: the
checkpoint answers "what has this projection seen", the position answers
"where in the feed am I". Resuming is the position's job; see
[Resuming](#resuming-from_position-is-exclusive-and-none-means-rebuild-from-the-beginning).

Two more properties, both pinned by that suite and both the opposite of the
natural guess:

- **A projection that has seen nothing has `None`, not "the position of
  nothing".** Treating those as the same is how a resume silently skips the
  log's first event.
- **The checkpoint tracks delivery, not application.** `GraphProjection` ends
  on the log's last event even when that event is an `EntitiesEmbedded` it has
  no handler for. A checkpoint that stopped at the last *handled* event would
  make every resume redeliver everything after it, forever.

The checkpoint is written after the handler succeeds, outside the retry loop —
deliberately, in `eventsource`: a checkpoint-store failure re-raises rather
than retrying, because retrying would re-run a fold that already applied and
exhausting the retries would DLQ an event the projection has done.

### The DLQ is what makes a failed event recoverable rather than merely counted

Without `dlq_repo`, a permanently failed event raises out of `handle`,
`replay` catches it, `failed` goes up by one, and the event is not named
anywhere. `ReplayReport.failed` tells you *how much* of the log did not land
and nothing else — you cannot fix what you cannot identify.

With it, the base class calls `add_failed_event` with the event id, the
projection name, the event type, `event.model_dump(mode="json")`, the error
and the retry count, and you read them back with `get_failed_events()`:

```python
for entry in await dlq.get_failed_events():
    print(entry.projection_name, entry.event_type, entry.error_message)
```

The realistic failure here is not exotic. A `DocumentExtracted` whose edge
points at an entity no document has produced yet raises `MissingEntityError`
from `upsert_relationship` — `GraphStore` refuses dangling edges — and that is
one document referencing another that has not been folded. On the vector side
it is `DimensionMismatchError`: the store is built for one embedding model and
the event carries vectors of another length. Both are poison in the same
sense, and both are worth having the event for.

**A DLQ write that itself fails is logged at `critical` and swallowed**, so
the original processing error is not masked. The log line distinguishes the
two cases explicitly ("sent to DLQ after N attempts" versus "failed
permanently after N attempts; NO DLQ entry was recorded"), which is the only
place the second case is visible — `failed` counts both identically.

Use a durable DLQ the moment the log is durable. An in-memory DLQ over a
Postgres log loses the poison events at the next restart while the log still
says they were never applied.

### Choosing a retry policy, and why `None` is the wrong one for a rebuild

`retry_policy=None` does not mean "no retries". The base class substitutes
`ExponentialBackoffRetryPolicy(RetryConfig(max_retries=2, initial_delay=2.0,
exponential_base=2.0, jitter=0.1))` — three attempts per failing event, with
`await asyncio.sleep(...)` of about two and then four seconds in between.
`replay` is a sequential loop, so that is six seconds of wall clock *per
poison event*, spent in the middle of your rebuild.

Note the trap in the neighbouring default: `ExponentialBackoffRetryPolicy()`
constructed with no config is **not** the same policy. It is
`max_retries=3, initial_delay=2.0, jitter=0.0`. If you want the base class's
behaviour explicitly, write the `RetryConfig` out.

And note the worse trap one import line above it: **`from eventsource import
RetryPolicy` still works, and it is not the type this parameter wants.** The
protocol `retry_policy=` is typed against is `ProjectionRetryPolicy`, in
`eventsource.application.projections.retry`. The top-level `RetryPolicy`
resolves somewhere else entirely — `eventsource.adapters._bus.retry` — where it
is an unrelated dataclass describing backoff for the *event bus*. It is
structurally incompatible: it does not answer `get_backoff` or `should_retry`,
so the projection loop fails on the first failing event rather than at
construction, which is the worst place to find out. `eventsource`'s own ADR 0062
records this as the reason the projections protocol was renamed, and the
rename does not close the hole, because the top-level export is still there to
be followed. Import the protocol and every concrete policy —
`ExponentialBackoffRetryPolicy`, `NoRetryPolicy`, `FilteredRetryPolicy`,
`DEFAULT_RETRY_POLICY` — from `eventsource.application.projections.retry`, and
treat a bare `from eventsource import ...` of anything retry-shaped as a bug.

Ask what a retry could fix. The failures the two folds produce are
deterministic functions of the event and the store — a missing endpoint is
still missing two seconds later, and a 768-vector does not become a
4-vector — so retrying them is pure latency. Retries earn their place when the
store is remote and the failure might be a dropped connection.

That gives two sensible settings:

- **`ExponentialBackoffRetryPolicy(config=RetryConfig(max_retries=0))`** for a
  rebuild over in-process or local stores. One attempt, straight to the DLQ.
  This is what the projection suite uses, for exactly this reason — the
  default policy would put the tests to sleep. `NoRetryPolicy()` from the same
  module is the same behaviour with no config to write.
- **The default, or a longer one**, when `GraphStore` is a Neo4j over a
  network and a transient failure is a real category.

Retrying is safe when you want it: `should_retry` sees every exception, and a
retry re-runs the whole handler. Re-running is harmless because every write in
both folds is an upsert or an idempotent delete — the same property the
[redelivery section](#redelivery-and-re-projection-why-applying-the-log-twice-is-safe)
rests on.

Be clear-eyed about what "failed" leaves behind, though. `_apply_extraction`
upserts entities and *then* relationships, so an event that fails on a
dangling edge has already written its entities. That is pinned by
`test_the_poisoned_event_left_no_partial_state`, and it is safe only because
replaying the fixed event is idempotent: once the missing endpoint exists,
re-running `replay` over the whole log applies the previously poisoned event
with no special handling and `failed` drops to zero. Partial application is a
state to replay out of, not one to repair by hand.

### The three arguments you probably do not need

- **`enable_tracing=False` / `tracer=None`.** OpenTelemetry spans per event,
  off by default because projections are high-frequency. Pass a `Tracer` if
  you have one; passing it makes `enable_tracing` irrelevant.
- **`tenant_filter=None`.** A `UUID`, or a callable returning one, that makes
  the projection ignore events belonging to any other tenant. Useful for
  rebuilding one tenant's read model from a shared log — but note it filters
  *delivery*, so a filtered-out event still advances the checkpoint, and the
  wipe you pair it with is `delete_by_tenant(tenant_id)`. See
  [Rebuild a projection](rebuild-a-projection.md).

### Neither projection can `reset()` itself

Both override `_truncate_read_models` to raise `NotImplementedError` naming
`delete_by_tenant`, so `await projection.reset()` fails loudly instead of
resetting the checkpoint and wiping nothing. That is deliberate: `GraphStore`
and `VectorStore` have no cross-tenant delete by design, and the library's
default no-op truncate would give you a rebuild that looked successful over a
store still holding every stale row. Wipe per tenant yourself — the
[rebuild section](#rebuilding-a-store-from-scratch-wipe-replay-from-zero-verify)
below does.

With the projections built, everything is in place: a feed to read, subscribers
to fold with, and somewhere for progress and poison to go. Step 4 is one call.

## Step 4: drive `replay` over the feed

```python
from eventsource import replay

report = await replay(event_store, projections)
```

That is the whole step. `event_store` is being passed as a `GlobalEventFeed` —
`replay` calls `read_all(from_position, options)` on it and nothing else — and
`projections` is the list you built in step 3.

The signature has seven more parameters, all keyword-only and all with
defaults that are right for a first run:

```python
report = await replay(
    event_store,
    projections,
    from_position=None,  # from the very beginning of the log
    tenant_id=None,  # every tenant in the store
    aggregate_type=None,  # every aggregate type in the store
    strict=False,  # record failures and carry on
    max_events=MAX_EVENTS_PER_REPLAY,
    max_failures=MAX_FAILURES_PER_REPLAY,  # retain this many, count the rest
    on_failure=None,  # called for every failure, retained or not
)
```

`from_position` is covered in
[Resuming](#resuming-from_position-is-exclusive-and-none-means-rebuild-from-the-beginning);
`tenant_id` and `aggregate_type` in [Scoping the read](#scoping-the-read-tenant_id-narrows-the-query-tenant_filter-narrows-the-delivery);
`strict` in [Failing loudly](#failing-loudly-stricttrue-and-replayfailure);
`max_events` in
[Bounding the loop](#bounding-the-loop-max_events_per_replay-and-the-cursor-that-fails-to-advance);
`max_failures` and `on_failure` in
[Reading the `ReplayReport`](#reading-the-replayreport-applied-failed-failures-last_position).

### Scoping the read: `tenant_id` narrows the query, `tenant_filter` narrows the delivery

In a store shared with other event types — sessions, jobs, whatever else the
application writes — a rebuild reads all of it. `tenant_id` is the cheap fix:

```python
report = await replay(event_store, projections, tenant_id=this_tenant)
```

It becomes `FeedReadOptions(tenant_id=...)` on the `read_all` call, and the
SQLite and PostgreSQL adapters push that into the `WHERE` clause. So the
rebuild costs what this tenant wrote rather than what the store holds.

`tenant_filter` on a projection (step 3) does something that *looks* the same
and is not: it drops foreign events **after** delivery, so the read still
costs the whole log. It is the right tool when one projection in a list should
see less than the others; it is the wrong tool for scoping a rebuild.

Setting both is harmless and pointless — the filter has nothing left to drop.

`aggregate_type` narrows the same way and composes with it. This library
writes `Document` and `ConsolidationLog` aggregates, so a rebuild of the read
models wants the first and none of the second:

```python
report = await replay(event_store, projections, tenant_id=this_tenant, aggregate_type="Document")
```

It became possible in `eventsource-py` 0.12.0 (its ADR 0052). Before that the
filter had nowhere to go — `FeedReadOptions` carried `tenant_id` and not this
— so a consumer interested in one aggregate type read the whole feed and
discarded the rest in Python. On PostgreSQL it is served by a composite
`(aggregate_type, global_position)` index; on SQLite `global_position` is the
rowid and none is needed. Note it is not `read_category`, which selects the
same events and orders them by storage time.

One thing neither does. Scoping by *stream* is still not a thing `replay`
offers — `GlobalEventFeed` has no `read_category`. Taking it would mean `replay`
accepting a narrower port than the one it documents, or accepting both and
branching, and nobody has asked for it — a caller wanting one stream can pass
`from_position`, a tenant and an aggregate type. It is upstream's call to make
rather than this project's, which is why `BACKLOG.md` no longer carries it as
an entry. And `last_position` is then the last
position the *filtered* read reached, which is the one to checkpoint for a subsequent
scoped run and **not** interchangeable with a whole-feed cursor.

### Failing loudly: `strict=True` and `ReplayFailure`

The default is a rebuild's default: a poison event is recorded and the pass
continues, because stopping denies the projection every event after it. In a
test, or on a first deployment, that is exactly backwards — a silent partial
rebuild is most costly when it is least visible.

```python
from redstring import ReplayFailedError

try:
    report = await replay(event_store, projections, strict=True)
except ReplayFailedError as exc:
    print(exc.failure.position, exc.failure.event_type, exc.failure.error)
```

`strict=True` stops at the first rejection and raises. The exception carries
the `ReplayFailure`, and the original exception is its `__cause__`.

You do not need strict mode to get that detail — the lenient run collects the
same objects in `report.failures`. Strict is a convenience for "stop now";
`failures` is the part a caller cannot reconstruct for itself.

### What one call does, in order

`replay` is a single pass over the feed. For each envelope it reads, in
order:

1. counts the event against `max_events` and raises if the bound is exceeded,
2. records `envelope.position` as the run's `last_position`,
3. awaits `projection.handle(envelope.event)` for **every** projection in the
   sequence, in list order,
4. counts the event as applied, or as failed if any projection raised.

Then it returns `ReplayReport(applied=..., last_position=..., failures=...)`,
whose `failed` is derived from `failures` rather than counted alongside it.

Two things follow from step 3 being a plain sequential loop. Projections are
folded one after another, not concurrently — `GraphProjection` finishes before
`VectorProjection` starts on the same event — so a slow store is wall clock
you pay per event. And an event is delivered to a projection that has no
handler for it just the same; `DeclarativeProjection` matching nothing is what
makes that a no-op, not `replay` filtering by `subscribed_to`.

### `replay` catches and continues; the projections raise

This is the one behaviour worth understanding before you run a rebuild over a
log you did not curate.

`CheckpointTrackingProjection.handle` retries, writes the DLQ entry, and then
**re-raises** — the re-raise is how a live subscription is told to stop rather
than checkpoint past a failure. A rebuild wants the opposite, so `replay`
catches, increments `failed`, and reads the next envelope. One bad event
therefore costs you that event, not every event after it. This is pinned by
`test_the_events_after_it_are_still_applied`: a three-event log whose middle
event has a dangling edge comes back `applied == 2, failed == 1`, and the
third document's entities are in the graph.

The `except` is deliberately unnarrowed. A projection may raise anything, and
"this event did not apply" is the only distinction a rebuild can act on.

### How the counts are assigned

Both counters are per *event*, not per projection, and each envelope
increments exactly one of them:

- **An event every projection ignores still counts as `applied`.** It was
  delivered and nothing rejected it. So an `EntitiesEmbedded` in a log you are
  replaying with only a `GraphProjection` is applied, not skipped.
- **An event any projection rejects counts as `failed` once**, however many
  projections rejected it — the count answers "how much of the log did not
  make it into the read models", and double-counting one event would make that
  answer wrong.
- **A partially-applied event still counts as failed**, and it counts once
  even though the projections that succeeded did write. `applied + failed` is
  the number of events read, always.

`failed` is a count rather than a bool on purpose: "some events failed" cannot
then be mistaken for "none did" by a truthiness check.

### Calling it twice

Nothing about `replay` is single-use, and re-running it over the same feed
from the same position is the normal way to make progress after fixing the
cause of a failure. Re-running an already-applied event is safe for the
reasons in
[Redelivery and re-projection](#redelivery-and-re-projection-why-applying-the-log-twice-is-safe);
what changes on the second run is `failed`, once the data the poison event
needed exists. `test_a_rerun_after_the_missing_entity_arrives_applies_it`
replays a log whose `failed` was 1, adds the missing endpoint, replays again,
and gets `failed == 0` with the previously poisoned edge in the graph — no
special handling, no DLQ replay API, just the same call.

So the operating loop is: run `replay`, read `failed`, and if it is non-zero
go to the DLQ for the events themselves. `report.failed` tells you how much
did not land; only `dlq.get_failed_events()` tells you what.

### What `replay` does not do

- **It does not wipe anything.** A replay from zero over a populated store
  folds on top of what is there. Wiping is a separate, per-tenant step; see
  [Rebuilding a store from scratch](#rebuilding-a-store-from-scratch-wipe-replay-from-zero-verify).
- **It does not consult a checkpoint.** It starts at `from_position` and
  nothing else. The projections' checkpoints are written as a side effect of
  delivery and are never read back by this function.
- **It does not follow the feed.** When `read_all` is exhausted, `replay`
  returns. This is a foreground operation someone is waiting on, not a
  subscription. Following a live feed is a different job, and it belongs to
  `eventsource.application.subscriptions` — *not* to `ProjectionCoordinator`,
  which polls nothing and owns no timer or background task. That class is a
  dispatch coordinator over a registry (`dispatch_events`, `rebuild_all`,
  `rebuild_projection`, `catchup`, `health_check`), driven by a caller that
  already has the events in hand; `eventsource` 0.14.0 deleted the batch-size
  and poll-interval settings that made it look otherwise, and states that the
  polling behaviour they implied was never true of any released version. If you
  want catch-up on a timer here, the loop is yours to write —
  [Resuming](#resuming-from_position-is-exclusive-and-none-means-rebuild-from-the-beginning)
  shows it, cursor and all.

## Reading the `ReplayReport`: `applied`, `failed`, `failures`, `last_position`

`replay` returns one small value, and it is the only thing the call tells
you:

```python
@dataclass(frozen=True, slots=True)
class ReplayReport:
    applied: int
    last_position: Position | None
    failures: tuple[ReplayFailure, ...] = ()
    failures_truncated: int = 0

    @property
    def failed(self) -> int:
        """Events at least one *retained* failure names."""
```

It is imported from `eventsource` (`from eventsource import ReplayReport`),
frozen, and compares by value — two runs that read the same amount of the same
feed produce equal reports, which is convenient in tests and meaningless as a
health check.

`failed` is a **property over `failures`**, not a stored count. Two projections
rejecting one event is one failed event and two failures, and deriving the
first from the second is what stops the two answers disagreeing. There is no
`failed` argument to the constructor.

It dedupes on **`event_id`**, not on `position`. `position` is
`Position | None` by contract, and a store with no feed sets it on nothing, so
keying on it would fold an entire failed rebuild into a count of one. This
package's copy of the driver keyed on `position` and had that latent defect;
`eventsource-py` 0.12.0 fixed it on the way up.

Each `ReplayFailure` carries `position`, `event_id`, `event_type`, `projection`
(the rejecting projection's class name) and `error` — **the exception object**,
so `MissingEntityError.entity_id` is reachable without parsing a message. That
is the part a caller cannot reconstruct: a count can always be derived from
detail, never the other way round. `event_type` is the event's own registered
`event_type` string rather than `type(event).__name__` — the same string here,
and the stored one wherever a wire name is deliberately pinned.

### `max_failures` and `on_failure`: the list is capped, and says so

A retained `ReplayFailure` pins a live exception and every frame's locals with
it, so `failures` is capped at `MAX_FAILURES_PER_REPLAY` (1000). What the cap
drops is **counted, not hidden**: `failures_truncated` is non-zero exactly
when the report is a sample, and `failed` is then a lower bound.

```python
if report.failures_truncated:
    print(f"{report.failed}+ events failed; {report.failures_truncated} not named here")
```

Truncating silently would have recreated the defect `failures` exists to fix —
an operator told "N failed" with no route to the Nth. `on_failure` is the way
to keep all of them, and it fires for every failure whether retained or not,
before a `strict=True` raise:

```python
report = await replay(event_store, projections, on_failure=log.write_failure)
```

This is `BACKLOG.md` B73 closed, and closed upstream rather than here: the
entry argued a cap that *says* it truncated, or a streaming callback, were the
two honest shapes, and that neither was worth deciding before someone had a
replay failing at that scale. `eventsource-py` 0.12.0 took both.

### `applied + failed` is the number of events read

Every envelope increments exactly one counter, so the sum is the size of the
slice of the log this call consumed. That is the first thing to check, and it
answers a question neither counter does on its own: **did the feed give me
anything at all?**

```python
report = await replay(event_store, projections)
read = report.applied + report.failed
```

`read == 0` with a log you know is populated means the feed started past the
end — almost always a `from_position` that was not what you thought, since
`None` is the only value that means "from the beginning".

### `applied` counts delivery, not writes

An event counts as applied when it was handed to every projection and none of
them raised. It does **not** mean any store was written:

- An event no projection subscribes to is applied. Replaying a log containing
  `EntitiesEmbedded` with only a `GraphProjection` in the list gives you a
  full `applied` count and an untouched graph for those events.
- An event filtered out by a projection's `tenant_filter` is applied. The
  filter suppresses delivery to that projection; nothing rejected the event.

So `applied` is "how much of the log went past the projections without
complaint", not "how many rows changed". `test_a_replay_from_position_zero_reads_the_first_event`
pins the distinction from the other side: it asserts `applied == 2` for a
one-document log *and then* asserts one entity and one vector actually landed,
because the count alone would not have caught a fold that wrote nothing.

### `failed` is a count, and the events themselves are in the DLQ

`failed` goes up by one per *event*, however many projections rejected it —
the question it answers is "how much of the log did not make it into the read
models", and counting one event twice would make that answer wrong. A
partially-applied event (graph fold succeeded, vector fold raised) counts as
failed, once.

It is deliberately an `int` rather than a flag, so `if report.failed:` reads
as what it is and "some events failed" cannot be mistaken for "none did" by a
truthiness check on a bool that was never set. Check `failures_truncated`
alongside it: past the cap, `failed` counts the events the *retained*
failures name and nothing more.

What it does not carry is *which* events. That is the DLQ's job, and it is why
step 3 tells you to pass a `dlq_repo`:

```python
if report.failed:
    for entry in await dlq.get_failed_events():
        print(entry.event_id, entry.projection_name, entry.error_message)
```

Without a DLQ, a non-zero `failed` is a number you cannot act on. With one,
the operating loop is: read `failed`, fetch the entries, fix the cause
(usually a missing entity that a later document supplies), and re-run
`replay` from the beginning — `test_a_rerun_after_the_missing_entity_arrives_applies_it`
does exactly that and comes back `failed == 0`.

**A non-zero `failed` does not mean the run stopped.** `replay` catches and
continues, so the events after a poison event are applied normally:
`test_the_events_after_it_are_still_applied` replays a three-event log with a
bad middle event and gets `applied == 2, failed == 1`, with the third
document's entities in the graph.

### `last_position` is the last event *read*, not the last applied

This is the field most likely to be misread. `replay` records
`envelope.position` before handing the event to any projection, so a failed
event still advances `last_position`. It is a high-water mark for the feed,
not a watermark for successful application — resuming from it will not
re-deliver the events that failed.

That is the right behaviour for the two things the field is for (knowing where
the feed got to, and resuming a bounded pass), and the wrong thing to reach
for if you want to retry failures. Retrying failures means replaying from
`None` after fixing the cause; the previously-applied events are re-applied
harmlessly, for the reasons in
[Redelivery and re-projection](#redelivery-and-re-projection-why-applying-the-log-twice-is-safe).

`None` means the loop never read an envelope. It is only ever assigned from an
envelope, so:

- an empty log gives `ReplayReport(applied=0, last_position=None, failures=())` —
  pinned by `test_an_empty_log_projects_to_empty_stores`, which exists to
  catch a `replay` whose loop never ran and whose report was fabricated;
- **a resume that finds nothing new also gives `None`**, not the position you
  passed in. `test_resuming_from_the_last_position_applies_nothing` asserts
  both `applied == 0` and `last_position is None`. So do not blindly write
  `last_position` back to your own cursor after every run:

```python
# Wrong: a caught-up run resets the cursor to the beginning of the log.
cursor = report.last_position

# Right: only advance when the run actually read something.
if report.last_position is not None:
    cursor = report.last_position
```

`Position` is opaque and store-scoped — compare and persist it (`to_str()` /
`Position.from_str()`), never build one or do arithmetic on it. And it is not
interchangeable with a projection's checkpoint, which is an `event_id` string
keyed by class name: see
[the checkpoint note in step 3](#step-3-construct-the-projections-graphprojection-vectorprojection-with-a-checkpoint-repository-a-dlq-repository-and-a-retry-policy).

### What the report cannot tell you

- **Whether the read models are correct.** Verify by reading the stores, as
  the [rebuild section](#rebuilding-a-store-from-scratch-wipe-replay-from-zero-verify)
  does. A replay over a store nobody wiped reports a clean `applied` count
  while leaving stale rows in place.
- **Whether the DLQ write succeeded.** A DLQ failure is logged at `critical`
  and swallowed so it does not mask the original error; `failed` counts that
  event identically to one safely captured. If `failed` is non-zero and
  `get_failed_events()` returns fewer entries, the log is where the
  difference is explained.
- **Whether the whole log was read.** It was, unless `max_events` was
  exceeded — and that raises rather than returning a short report. See
  [Bounding the loop](#bounding-the-loop-max_events_per_replay-and-the-cursor-that-fails-to-advance).

## Resuming: `from_position` is exclusive, and `None` means rebuild from the beginning

`from_position` is the only thing that decides where a `replay` call starts.
It has exactly two modes, and the second one is the default:

```python
# Resume: read everything strictly *after* this position.
report = await replay(event_store, projections, from_position=cursor)

# Rebuild: read the whole log, from before the first event.
report = await replay(event_store, projections, from_position=None)
```

### Exclusive means "after", so hand back the last position you read

`replay` passes `from_position` straight to `feed.read_all(from_position)`,
and the feed's contract is strictly-greater-than — the memory adapter filters
with `e.position > from_position`. So the position of an event you have
already applied is the correct thing to resume from: that event is not
redelivered, and the next one is.

That makes the resume protocol a two-line loop, with `last_position` from one
run becoming `from_position` for the next:

```python
cursor: Position | None = None

while True:
    report = await replay(event_store, projections, from_position=cursor)
    if report.last_position is None:
        break  # caught up: nothing new in the feed
    cursor = report.last_position
```

`test_resuming_from_the_last_position_applies_nothing` pins both halves of
this: a replay of a one-document log, then a second `replay` from
`first.last_position`, comes back `applied == 0` — the last event is not
re-applied — and `last_position is None`.

**The `is None` guard is not optional.** `last_position` is only ever assigned
from an envelope the loop actually read, so a caught-up run returns `None`,
not the position you passed in. Writing it back unconditionally resets your
cursor to "the beginning of the log" and the next call replays everything:

```python
# Wrong: one caught-up run silently turns the next resume into a full rebuild.
cursor = report.last_position
```

A full rebuild is safe (that is the next section's point), so this failure is
not corruption — it is a rebuild you did not ask for, on every poll, forever.

### `None` is the beginning of the log, not the position of the first event

The default means *before* the first event. An off-by-one here would drop the
first event of every rebuild, which on this schema is a whole document, so it
is pinned directly:
`test_a_replay_from_position_zero_reads_the_first_event` replays a
single-document log with `from_position` left at its default and asserts
`applied == 2` (the `DocumentExtracted` and the `EntitiesEmbedded`) *and* that
one entity and one vector actually landed. The count alone would not have
caught a fold that wrote nothing.

`test_an_empty_log_projects_to_empty_stores` covers the other end: no events,
no prior run, no state left by an earlier phase, and
`ReplayReport(applied=0, last_position=None, failures=())`. It exists because a
loop that never runs and a report that was fabricated look identical from the
outside.

So `None` is the value you pass to rebuild, and it is the only value that
means that. If a run of a log you know is populated comes back with
`applied + failed == 0`, the first thing to check is that `from_position` was
not a stale cursor.

### Resuming does not retry what failed

`replay` records `envelope.position` *before* handing the event to any
projection, so a failed event advances `last_position` exactly like an applied
one. Resuming from it therefore skips the failure permanently.

That is deliberate — `last_position` is a high-water mark for the feed, not a
watermark for successful application — but it means the two operations are
different calls:

| You want | Pass |
|---|---|
| the events that arrived since last time | `from_position=report.last_position` |
| another attempt at events that failed | `from_position=None` |

Retrying failures means replaying the whole log after fixing the cause,
usually a missing entity that a later document supplies.
`test_a_rerun_after_the_missing_entity_arrives_applies_it` does exactly that
and comes back `failed == 0`; the previously-applied events are re-applied
harmlessly, for the reasons in
[Redelivery and re-projection](#redelivery-and-re-projection-why-applying-the-log-twice-is-safe).

### A position is not a checkpoint, and neither is a substitute for the other

These are the two mechanisms most easily confused, and they answer different
questions:

| | `Position` | Projection checkpoint |
|---|---|---|
| What it is | opaque `(store_id, key)` token | an `event_id` string plus `event_type` |
| Scope | the whole feed | one projection, keyed by class name |
| Who writes it | you, from `report.last_position` | the projection base class, after each delivery |
| Accepted by `replay` | yes, as `from_position` | **no** — wrong type, wrong meaning |

`get_checkpoint()` hands back a string, and there is no way to turn it into a
`Position`. `tests/unit/projections/test_checkpoints.py` exists to keep the
two apart; see
[the checkpoint note in step 3](#step-3-construct-the-projections-graphprojection-vectorprojection-with-a-checkpoint-repository-a-dlq-repository-and-a-retry-policy)
for what the checkpoint *is* good for.

### Persisting a cursor across restarts

If you are driving the loop yourself and the process may die, the position is
serialisable and that is how you carry it:

```python
raw = report.last_position.to_str()  # store this
cursor = Position.from_str(raw)  # ...and read it back later
```

Two properties constrain where that value is valid. It is **opaque** — the
key's shape is the adapter's business, so compare and order positions, never
do arithmetic on one and never construct one to mean "position 5". And it is
**store-scoped**: ordering positions from two stores raises
`PositionForeignError`, and equality across them is `False`. A cursor saved
against one store and replayed against another is not merely wrong, it is
meaningless — after a migration to a different event store, resume from
`None`.

### When to just pass `None`

Resuming is for a process that is following a growing log. Everything else
wants the beginning:

- restoring one read model — wipe per tenant, then replay from zero:
  [Rebuild a projection](rebuild-a-projection.md);
- adding a projection to a log that already exists — it has seen nothing, so
  there is no position that would be honest;
- retrying failures, as above;
- any run you are not certain about. A replay from `None` over an already-
  correct store is wasted work and nothing worse: every write in both folds is
  an upsert or an idempotent delete.

## Why a rebuild does not stop on a bad event, and where that event went

A rebuild reads a log you did not curate, so sooner or later an event will not
apply. `replay` is built for that case rather than against it: the bad event
is counted, recorded, and left behind, and the rest of the log is folded
normally.

### The two halves disagree on purpose

The projection base class and `replay` do opposite things with a failure, and
both are right for the job they are doing.

`CheckpointTrackingProjection.handle` retries under your policy, writes a DLQ
entry, and then **re-raises**. That re-raise is what a *live subscription*
needs: stopping is how a subscription avoids checkpointing past an event it
never applied.

`replay` is not a subscription. It catches, increments `failed`, and reads
the next envelope:

```python
for projection in projections:
    try:
        await projection.handle(envelope.event)
    except Exception:
        rejected = True
```

Stopping here would let one bad event deny the projection every event after
it — and there is no reason to, because the failure has already been recorded
by the time the exception reaches this frame. The `except` is deliberately
unnarrowed: a projection may raise anything, and "this event did not apply" is
the only distinction a rebuild can act on.

`test_the_events_after_it_are_still_applied` pins it with the poison in the
*middle* of a three-document log, so a projection that stopped would visibly
drop the third: the run comes back `applied == 2, failed == 1`, and the third
document's entity is in the graph.

### The failure is a real one, not an injected exception

The case worth planning for is not exotic. A `DocumentExtracted` whose
relationship points at an entity no document has produced raises
`MissingEntityError` from `upsert_relationship` — `GraphStore` refuses
dangling edges — and that is one document referencing another the projection
has not folded yet. On the vector side the equivalent is
`DimensionMismatchError`: the store was built for one embedding model and the
event carries vectors of another length.

Both are deterministic functions of the event and the store, which is the
argument for `max_retries=0` in step 3: a missing endpoint is still missing
two seconds later.

### Where the event went: the DLQ, if you gave it one

`report.failed` is a count and nothing more. The events themselves are in the
DLQ repository you passed to each projection:

```python
if report.failed:
    for entry in await dlq.get_failed_events():
        print(entry.event_id, entry.projection_name, entry.event_type, entry.error_message)
```

`add_failed_event` is called with the event id, the projection's name, the
event type, `event.model_dump(mode="json")`, the exception and the retry
count — so the entry holds the whole event, not a reference to it, and is
enough to reconstruct what was attempted. `test_the_failure_is_recorded_rather_than_swallowed`
asserts on exactly that: one entry, `event_type == "DocumentExtracted"`, and
an `error_message` naming the missing endpoint.

**Without a `dlq_repo`, the event is gone.** `failed` still counts it, and
nothing anywhere names it. That is the single strongest reason step 3 tells
you to pass one.

Two edges to know about:

- **A DLQ write that itself fails is logged at `critical` and swallowed**, so
  it cannot mask the original processing error. `failed` counts that event
  identically to one safely captured, so if `failed` exceeds
  `len(await dlq.get_failed_events())`, the application log is where the
  difference is explained.
- **The DLQ is keyed per projection.** One event rejected by both projections
  produces two entries and one `failed`. The counts answer different
  questions and are not meant to match.

### A failing event may have written part of itself

`_apply_extraction` upserts entities and *then* relationships, so an event
that dies on a dangling edge has already written its entities.
`test_the_poisoned_event_left_no_partial_state` pins that shape directly: the
poisoned document's entity is in the graph and no edge is.

This is a consequence of the port's writes not being one transaction, and it
is safe only because replaying the event is idempotent. Do not try to repair
it by hand — partial application is a state you replay out of.

### Fixing and re-running

The DLQ is not a graveyard. Once whatever the event needed exists, replaying
the whole log applies it with no special handling and no DLQ replay API:

```python
report = await replay(event_store, projections)  # failed == 1
...  # the missing document arrives
report = await replay(event_store, projections)  # failed == 0
```

`test_a_rerun_after_the_missing_entity_arrives_applies_it` does this end to
end — including asserting that a rerun *before* the fix still reports
`failed == 1`, so the pass is not being credited to redelivery — and finishes
with the previously poisoned edge in the graph.

Two things to get right when you do this:

- **Replay from `None`, not from `report.last_position`.** `replay` records
  the position before handing the event to any projection, so a failed event
  advances the high-water mark exactly like an applied one. Resuming from it
  skips the failure permanently; see
  [Resuming](#resuming-from_position-is-exclusive-and-none-means-rebuild-from-the-beginning).
- **Do not wipe first.** Re-applying the events that already landed is
  harmless — every write in both folds is an upsert or an idempotent delete,
  which is the property
  [Redelivery and re-projection](#redelivery-and-re-projection-why-applying-the-log-twice-is-safe)
  rests on — and wiping would discard the entities the fix depends on.

So the operating loop is: run `replay`, read `failed`, and if it is non-zero
go to the DLQ for the events, fix the cause, and run `replay` from the
beginning again. If you are restoring one read model rather than repairing a
run, the shorter recipe is
[Rebuild a projection](rebuild-a-projection.md).

## Bounding the loop: `MAX_EVENTS_PER_REPLAY` and the cursor that fails to advance

`replay`'s last parameter is a safety bound, not a page size:

```python
report = await replay(event_store, projections, max_events=MAX_EVENTS_PER_REPLAY)
```

`MAX_EVENTS_PER_REPLAY` is `10_000_000` — far above any real rebuild and far
below forever. Leave it alone unless you are testing the bound itself.

### Why a rebuild needs a bound at all

The loop is `async for envelope in feed.read_all(from_position)`, and the feed
is adapter-supplied. Whether the iteration ever ends is therefore a property
of somebody else's cursor, not of this function. An adapter whose cursor fails
to advance — re-reading the same page forever — turns the loop into a hang.

A hang is a worse failure than an exception, and specifically worse in the
place you will meet it: in CI a hung job reads as infrastructure trouble and
gets retried rather than investigated, and a rebuild is a foreground operation
someone is already waiting on. So the loop counts what it has read and refuses
past the bound:

```python
seen += 1
if seen > max_events:
    raise RuntimeError(
        f"replay read more than {max_events} events without the feed "
        f"ending; the adapter's cursor is probably not advancing "
        f"(last position: {last_position})"
    )
```

The message names the likely cause and carries the last position read, which
is the one datum that distinguishes the two explanations: a position that
never changes across successive runs is a stuck cursor, and a position that
advances normally means the log really is bigger than the bound.

### It raises — it does not return a short report

This is the one exit from `replay` that does not produce a `ReplayReport`.
There is no partial report, no `truncated` flag, and no position handed back
to resume from:

```python
try:
    report = await replay(event_store, projections)
except RuntimeError:
    ...  # no report exists; nothing tells you how far it got except the message
```

That is deliberate. A short report would be indistinguishable from a feed that
ended, and "the rebuild finished" is exactly the conclusion a stuck cursor must
not be allowed to support. Compare it with a failing event, which *is*
survivable and *is* reported: one poison event costs you that event and the run
continues, because the failure is understood. An unbounded feed is not
understood, so the run stops.

What the raise does not undo is what already landed. Every event read before
the bound was tripped has been folded into the projections and checkpointed
normally; the stores are as far along as the count says. Because both folds are
idempotent — the property
[Redelivery and re-projection](#redelivery-and-re-projection-why-applying-the-log-twice-is-safe)
rests on — the recovery is the usual one: fix the adapter, then replay from
`None`. There is nothing to clean up first.

### `max_events` is a count of events read, not a limit on events applied

The counter increments once per envelope, before delivery, so failed events
count against the bound exactly like applied ones. `applied + failed` for a run
that completes is always the number of events read, and it is that number the
bound constrains.

The comparison is `seen > max_events`, so **the bound is the number of events
allowed, not the number after which reading stops.** A log of exactly
`max_events` events replays cleanly. Both halves are pinned:
`test_a_log_exactly_at_the_bound_is_not_rejected` replays a two-event log with
`max_events=2` and asserts `applied == 2`, and
`test_a_feed_that_will_not_end_fails_instead_of_hanging` runs the same log with
`max_events=1` and asserts the `RuntimeError`. The second test is how the bound
is known to work at all: a guard nobody has watched fire is a guard nobody
knows is wired up, and this one can only fire by construction in a test.

Note what that pair implies about the small-`max_events` case — it is not a
pagination knob. Setting `max_events=1` to "read one event" does not read one
event and stop; it reads two and raises, having already applied the first.
There is no supported way to consume a log in bounded chunks through
`replay`. If that is what you want, the mechanism is `from_position` over a
feed that ends, and the chunking belongs to your adapter.

### When to change it

Almost never. Three cases:

- **Testing the bound.** A deliberately small value is the only way to
  exercise the raise, which is what the suite above does.
- **A log genuinely larger than ten million events.** Raise it. On this
  schema an event is a whole document, so this is a very large corpus rather
  than a busy day.
- **A new or suspect feed adapter.** Lowering it to somewhat above the log
  size you expect turns "the cursor is stuck" from a hang into a fast, named
  failure. Do that while you are proving the adapter out, and put it back
  afterwards — a bound tuned to today's log size becomes a spurious failure
  the week the corpus grows.

The bound is per call, not per process. A resume loop that calls `replay`
repeatedly gets a fresh count each time, so it is never the thing that stops a
long-running follower; see
[Resuming](#resuming-from_position-is-exclusive-and-none-means-rebuild-from-the-beginning).

## Consolidating into the same log

Everything above appends `DocumentExtracted`. The other two events in the
schema — `EntitiesMerged` and `MergeUndone` — come from consolidation, and on
this path they belong in the same log for the same reason: a store you can
rebuild has to be rebuildable from *all* of the facts, not most of them.

`Consolidator` takes the same two eventsource ports the rest of this guide
uses. Hand it yours and its merges are recorded where your replays can find
them:

```python
from redstring import Consolidator

consolidator = Consolidator(
    graph_store,
    event_store=event_store,
    snapshot_store=snapshot_store,
)

report = await consolidator.resolve(subject)
```

**Omitting those two arguments is the thing to avoid here.** `Consolidator`
falls back to an in-memory log, which is the right default for the
`build_graph` shape — no event store, nothing to rebuild from — and exactly
wrong once you have one. Two symptoms follow, and neither announces itself:

- **A rebuild loses every merge.** The graph is correct while the process
  lives, because the consolidator projects each event as it emits it. Wipe
  and replay from the log, and the merges are simply absent: the entities
  come back unmerged, and nothing failed.
- **`undo` stops working across restarts.** It reads what to restore from the
  log, so a new `Consolidator` cannot reverse an earlier one's merge and
  raises `UnknownMergeError` — the same error it raises for a merge that
  never happened.

`consolidator.remembers_merges_across_restarts` reports which arrangement is
in use, and is worth asserting once at startup on this path rather than
discovering the answer after a rebuild.

The events themselves need no special handling. `GraphProjection` already
handles all three, in the order the log delivers them, and the redelivery
argument below covers merges as well as extractions.

## Rebuilding a store from scratch: wipe, replay from zero, verify

Three steps, in this order, and the third is not optional:

```python
for tenant_id in tenant_ids:  # 1. wipe, per tenant
    await graph_store.delete_by_tenant(tenant_id)
    await vector_store.delete_by_tenant(tenant_id)

report = await replay(event_store, projections)  # 2. replay from zero

assert report.failed == 0  # 3. verify — see below
```

That is the whole procedure. Everything below is why each step is shaped the
way it is, and what to check before you believe the result. If you are
restoring *one* read model rather than standing the whole path up, the shorter
recipe is [Rebuild a projection](rebuild-a-projection.md).

### Step 1: wipe per tenant — there is no cross-tenant delete, on purpose

`delete_by_tenant(tenant_id)` is the only bulk delete either port has.
`GraphStore`'s removes that tenant's entities, relationships and aliases and
returns the number of *entities* removed; `VectorStore`'s removes that
tenant's records and returns how many. Neither touches another tenant.

So a rebuild has to iterate the tenants you are rebuilding, and **you have to
know what they are** — no port will tell you. They come from your own
records, or from a pass over the log reading `event.tenant_id`.

The obvious alternative does not exist:

```python
await projection.reset()  # NotImplementedError
```

Both projections override `_truncate_read_models` to raise, naming
`delete_by_tenant` in the message. That is deliberate rather than unfinished:
the library's default truncate is a no-op, so inheriting it would give you a
`reset()` that reset the checkpoint, wiped nothing, and returned successfully
— a rebuild that looks clean while the store still holds every stale row.
Failing loudly is the point.

**The wipe must include the aliases, and that is an adapter obligation.** A
tenant wiped of entities but left with its alias rows replays its merges over
surviving aliases, and `delete_by_tenant` stops being a reset in exactly the
case a rebuild needs it to be one. The in-memory adapter pops the alias map
alongside the entity and relationship maps; the Neo4j adapter deletes alias
nodes — and orphaned blocking-key nodes — in separate statements after the
`DETACH DELETE` of the entities. If you have written your own adapter, this is
the thing to check before trusting a rebuild through it.

**Wipe before you replay, not after some of it.** Wiping mid-run discards
entities that later events' edges depend on and manufactures poison events out
of a log that was fine.

### Step 2: replay from zero means `from_position=None`

The default. It is the only value that means "from before the first event" —
see
[Resuming](#resuming-from_position-is-exclusive-and-none-means-rebuild-from-the-beginning)
for why a cursor you happen to be carrying is the wrong thing to pass here.

Nothing needs resetting first. `replay` never consults a checkpoint, so a
projection whose checkpoint says it has seen the whole log still replays the
whole log. The checkpoints are rewritten as the rebuild delivers, and end
where they started — pointing at the last event of the feed, which is what a
live runner should see next.

Use the same `projections` list you built in step 3. Rebuilding one store and
not the other is legitimate — pass a list of one — but then wipe only that
store's tenants, or you will have emptied a read model nothing in this run
will refill.

### Step 3: verify — the report alone cannot tell you the rebuild worked

Three checks, each catching something the others cannot.

**`report.failed == 0`.** Non-zero means some events did not reach the read
models, and the count is all `replay` gives you; the events themselves are in
the DLQ:

```python
if report.failed:
    for entry in await dlq.get_failed_events():
        print(entry.event_id, entry.projection_name, entry.error_message)
```

On a rebuild the usual cause is not corruption — it is a `MissingEntityError`
from an edge whose endpoint belongs to a document further down the log, or a
`DimensionMismatchError` from a vector store built for a different embedding
model. The first fixes itself on a rerun once the endpoint exists; see
[Why a rebuild does not stop on a bad event](#why-a-rebuild-does-not-stop-on-a-bad-event-and-where-that-event-went).

**`report.applied + report.failed` is the number of events you expected.**
This is the check that catches a wipe-and-replay where the *replay* did
nothing. Zero events read over a log you know is populated almost always means
`from_position` was a stale cursor rather than `None`.

**Read the stores.** Neither counter says anything about rows. An event no
projection subscribes to counts as applied, and so does an event a
`tenant_filter` suppressed — so a clean `applied` count is consistent with an
empty graph. Query for something you know should be there:

```python
entities = await graph_store.find_by_blocking_key(known_key, tenant_id)
assert entities
```

The suite makes the same distinction on the way in:
`test_a_replay_from_position_zero_reads_the_first_event` asserts `applied == 2`
for a one-document log *and then* asserts that one entity and one vector
actually landed, because the count alone would not have caught a fold that
wrote nothing.

### Why the wipe is the step people skip, and why the tests refuse to

Replaying without wiping converges rather than corrupting — every write in
both folds is an upsert or an idempotent delete, which is the property
[Redelivery and re-projection](#redelivery-and-re-projection-why-applying-the-log-twice-is-safe)
rests on, and `test_replaying_over_a_live_projection_changes_nothing` pins it
for a rebuild an operator started without wiping first. That is exactly what
makes skipping the wipe tempting and wrong: it converges on the log's state
*plus whatever the store already held that the log does not describe*. Rows
written by a fold you have since fixed, or by something other than this log,
survive a replay untouched. If they did not, you would not need the rebuild.

The equivalence suite's `_wipe` helper asserts both stores are empty before
replaying, and the docstring says why: a wipe that silently did nothing would
make every replay test pass trivially, because the "replayed" state would be
the state the first projection left behind and the replay could be deleted
outright without a test noticing.

### What a rebuild is guaranteed to reproduce

`tests/unit/projections/test_replay_equivalence.py` is where this procedure is
proven, over hypothesis-generated logs plus pinned cases covering a merge that
moves an edge, a merge that drops one, an undo of each, two tenants in one
log, and an empty log. It asserts:

- **project, wipe, replay from zero gives the same state** — the claim this
  section makes;
- **delivering every event twice gives the same state** — at-least-once
  delivery, which catches a fold that accumulates rather than replaces;
- **replaying over a live projection changes nothing** — the un-wiped rebuild
  above;
- and, separately and non-redundantly, **the folded state matches an
  independent oracle**. The first three are self-consistency properties: both
  sides run the same handlers, so a handler that does too little makes both
  sides agree on the same wrong state. Three surviving mutants is what it cost
  to find that out.

Reproducibility depends on the fold deriving its ids rather than generating
them. Alias ids are a `uuid5` of the tenant and absorbed entity, precisely so
a replay produces the same rows as the original run — a `uuid4` there would
make every rebuild differ from the store it replaced, and no equivalence test
would pass.

### Rebuilding one tenant out of a shared log

`tenant_filter` on the projection constructor makes it ignore events for other
tenants, which pairs with a `delete_by_tenant` of just that tenant:

```python
GraphProjection(graph_store, dlq_repo=dlq, tenant_filter=tenant_id)
```

Note what it filters: *delivery*. A suppressed event still counts as applied
and still advances the checkpoint, so a projection built this way ends its
rebuild with a checkpoint at the end of the whole feed. That is fine for a
one-shot rebuild and worth knowing before you hand the same instance to a live
runner. [Rebuild a projection](rebuild-a-projection.md) covers the per-tenant
recipe in more detail.

### When the log itself is the thing you distrust

A rebuild reproduces the log. It cannot repair it. If an event was appended
with `allow_partial=True` over failed chunks, every rebuild from now on
replays that incomplete extraction — the fix is a re-extraction under a new
`model_version`, appended through
[the write model](use-the-write-model.md), not a replay. Same for a document
whose entities were extracted by a provider you no longer trust: the log says
what happened, and what happened is what you get back.

## Redelivery and re-projection: why applying the log twice is safe

Every other section leans on one property, so here it is stated once and
sourced: **applying an event twice leaves the same state as applying it once.**
That is what makes a rebuild something you can run without ceremony, a retry
something you can do without bookkeeping, and an at-least-once feed something
you can drive `replay` over without a dedupe layer.

You do not have to arrange for it. There is no dedupe table, no "have I seen
this event" check in either projection, and nothing to configure.

### Where the property actually comes from

It comes from the two store ports, not from the projections. Every write
either fold performs is an upsert or an idempotent delete:

| Handler | Writes | Why re-running is a no-op |
|---|---|---|
| `_apply_extraction` (`DocumentExtracted`) | `upsert_entities`, `upsert_relationships`, and `delete_relationship` for an edge whose endpoints collapsed | upserts are last-write-wins by id; deleting an absent id returns `False` rather than raising |
| `_apply_merge` (`EntitiesMerged`) | `upsert_alias` per absorbed entity, then `upsert_relationship` or `delete_relationship` per redirection | alias rows are keyed `(tenant_id, alias_entity_id)`; the redirection writes the same edge id either way |
| `_apply_undo` (`MergeUndone`) | `remove_alias`, then `upsert_relationships` of the restored edges | removing an absent alias is not an error; the restored edges carry whole `Relationship`s, so re-writing them is the same write |
| `_apply_embeddings` (`EntitiesEmbedded`) | `upsert_many` | last-write-wins per `(tenant_id, entity_id)`, within a call and across calls |

`GraphStore` and `VectorStore` both say so in their port docstrings — "every
write is idempotent because projection handlers replay" — and the compliance
suite holds every adapter to it. So the property survives swapping the
in-memory stores for Neo4j and pgvector; it is not an artefact of the
reference adapters.

### The ids have to be derived, or none of this holds

Idempotent writes are only half of it. A fold that *generated* an id on each
run would write a new row every time even through an upsert, and a replay
would accumulate rather than converge.

The one place either fold invents an id is the alias row a merge writes, and
it is a `uuid5` of the tenant and the absorbed entity precisely so a replay
produces the same row:

```python
def _alias_id(tenant_id, alias_entity_id):
    return uuid5(NAMESPACE_OID, f"redstring:alias:{tenant_id}:{alias_entity_id}")
```

The merge event's id is deliberately *not* in that hash even though it is to
hand: the row's identity is `(tenant_id, alias_entity_id)` in every adapter, so
hashing anything else in would let one logical row carry two ids depending on
which merge last wrote it. A `uuid4` there would make every rebuild differ
from the store it replaced, and no equivalence test could pass.

Everything else — entity ids, relationship ids, vector record ids — arrives in
the event. The fold never mints one.

### What redelivery is, and the one thing it is not

The delivery shape the projections are built for is **order-preserving
at-least-once**: the same events, possibly repeated, never reordered. That is
what a checkpointed feed produces — a redelivered suffix is contiguous and in
order, so the last occurrence of each event is still in log order and the final
state is the log's.

`_deliver_twice` in the equivalence suite models exactly that, handing every
envelope to every projection twice in feed order, and
`test_at_least_once_delivery_changes_nothing` asserts the result equals the
single-delivery state. Its docstring names what it is for: it catches a fold
that **accumulates rather than replaces**, which a single clean replay cannot
distinguish.

What is *not* covered is reordering. A bus that could deliver `e1, e2, e1`
would break the graph fold, and no amount of upserting fixes that — an
`EntitiesMerged` followed by a stale redelivery of the `DocumentExtracted` it
superseded is a different final state. `replay` over a `GlobalEventFeed`
cannot produce that shape: `read_all` yields in position order.

There was a real ordering hazard here once and it was not a delivery problem
at all. The extraction fold used to write a document's edges with the endpoints
extraction found, so re-extracting a document under a new `model_version` after
a merge had moved its entities wrote the pre-merge endpoints back and silently
undid the merge — in strict log order, every event delivered exactly once. The
fix was the alias table: `_apply_extraction` now resolves both endpoints
through `resolve_entity_ids` before upserting, transitively, because `B → A`
then `A → C` is legal. That is worth knowing because it is the shape of the bug
idempotency does *not* protect you from: an ordering assumption about what the
write side emits, which no delivery mechanism can supply.

### Three things you therefore do not need to do

- **Do not check whether an event has already been applied.** The projection
  checkpoint looks like it is for this and is not — it records the last
  `event_id` *delivered*, is keyed by class name, and is never read by
  `replay`. See
  [the checkpoint note in step 3](#step-3-construct-the-projections-graphprojection-vectorprojection-with-a-checkpoint-repository-a-dlq-repository-and-a-retry-policy).
- **Do not wipe before re-running a failed replay.** Re-applying the events
  that already landed is free, and wiping would discard the entities a poison
  event's fix depends on.
- **Do not narrow a retry policy for correctness reasons.** `should_retry` sees
  every exception and a retry re-runs the whole handler, including the writes
  that already succeeded. `max_retries=0` is a latency decision (see step 3),
  not a safety one.

### Two limits, both worth knowing before you rely on this

**A partially-applied event stays partially applied until you replay it.**
`_apply_extraction` upserts entities and *then* relationships, and the port's
writes are not one transaction, so an event that dies on a dangling edge has
already written its entities — pinned by
`test_the_poisoned_event_left_no_partial_state`. Idempotency is what makes that
state safe to replay out of rather than repair by hand; it does not make the
partial write not happen.

**Re-projecting converges on the log, not on correctness.** A replay over an
un-wiped store is safe — `test_replaying_over_a_live_projection_changes_nothing`
covers the rebuild an operator starts without wiping first, over
hypothesis-generated logs and every pinned scenario — but "changes nothing" is
the point: rows the log does not describe survive it untouched. If you are
rebuilding to *remove* something, the wipe is not optional; see
[Rebuilding a store from scratch](#rebuilding-a-store-from-scratch-wipe-replay-from-zero-verify).

### Where redelivery is *not* tolerated: the append

One place on this path refuses a duplicate rather than absorbing it. Appending
the same event object twice raises `DuplicateEventError` — `event_id` is unique
across the store — so a retry loop around step 2 has to treat that exception as
success. Everything downstream of the log tolerates redelivery by construction;
the log itself de-duplicates by refusing. See
[step 2](#step-2-append-reportevent-to-the-event-store-on-the-document-stream).

### How far the guarantee has actually been proven

`tests/unit/projections/test_replay_equivalence.py` runs three self-consistency
properties — wipe and replay, deliver everything twice, replay over a live
projection — over hypothesis-generated scenarios plus seven pinned ones,
including a merge that moves an edge, a merge that drops one, an undo of each,
and two tenants in a single log.

And it runs a fourth claim that is not redundant with those three, which is the
part to take seriously if you are writing your own projection: the folded state
is compared against an **independent oracle** the log builder maintains. All
three equivalence properties passed against a handler that never applied an
undo, one that never deleted a dropped edge, and one that never wrote
relationships at all — because both sides of an equivalence run the same fold,
and a fold that does too little leaves both sides agreeing on the same wrong
state. Three surviving mutants is what finding that out cost. Self-consistency
tells you a replay is *reproducible*; only an oracle tells you it is right.

## The complete script

Every step above, in one file. It runs as written — `FakeLlmProvider`,
`InMemoryEventStore` and the two in-memory stores are complete
implementations, so there is no server to start and nothing is stubbed.

```python
"""Extract one document, append its event to a log, and fold that log into
two read models. The one-call path is `docs/examples/build_a_graph.py`; this
is the same work with a durable event store in the middle.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from eventsource.adapters.memory import (
    InMemoryCheckpointRepository,
    InMemoryDLQRepository,
    InMemoryEventStore,
)
from eventsource.application.projections.retry import ExponentialBackoffRetryPolicy
from eventsource.application.subscriptions.retry import RetryConfig
from eventsource.ports.positions import ExpectedVersion

from redstring import (
    FakeLlmProvider,
    GraphProjection,
    InMemoryGraphStore,
    InMemoryVectorStore,
    SourceDocument,
    VectorProjection,
    build_graph,
    document_stream,
    project,
)

#: What the model "finds" in the text below. A real provider reads the text.
ANSWER = {
    "entities": [
        {"name": "Ada Lovelace", "entity_type": "Person"},
        {"name": "Charles Babbage", "entity_type": "Person"},
    ],
    "relationships": [
        {
            "source_name": "Ada Lovelace",
            "target_name": "Charles Babbage",
            "relationship_type": "WORKED_WITH",
        },
    ],
}


async def main() -> tuple[int, int, list[str]]:
    tenant_id = uuid4()
    document = SourceDocument(
        id="lovelace-notes",
        text="Ada Lovelace worked with Charles Babbage on the Analytical Engine.",
    )

    event_store = InMemoryEventStore()

    # Step 1: extract. `scratch` is written and thrown away, so the log is the
    # only path into the read models below.
    report = await build_graph(
        document,
        provider=FakeLlmProvider(by_substring={"Ada": ANSWER}),
        store=InMemoryGraphStore(),
        tenant_id=tenant_id,
    )
    if report.event is None:  # cannot happen through `build_graph`; the type says it can
        return (0, 0, [])

    # Step 2: append, on the stream the aggregate was addressed by.
    stream = document_stream(tenant_id=tenant_id, source_id=document.id)
    await event_store.append(stream, [report.event], ExpectedVersion.any_())

    # Step 3: construct the projections. Every argument past the store is the
    # reason this path exists rather than the one-call one.
    graph_store = InMemoryGraphStore()
    vector_store = InMemoryVectorStore(dimension=768)
    checkpoints = InMemoryCheckpointRepository()
    dlq = InMemoryDLQRepository()
    straight_to_dlq = ExponentialBackoffRetryPolicy(config=RetryConfig(max_retries=0))

    projections = [
        GraphProjection(
            graph_store,
            checkpoint_repo=checkpoints,
            dlq_repo=dlq,
            retry_policy=straight_to_dlq,
        ),
        VectorProjection(
            vector_store,
            checkpoint_repo=checkpoints,
            dlq_repo=dlq,
            retry_policy=straight_to_dlq,
        ),
    ]

    # Step 4: fold the log. `from_position` defaults to None — the beginning.
    replay = await replay(event_store, projections)

    if replay.failed:
        for entry in await dlq.get_failed_events():
            print(entry.event_id, entry.projection_name, entry.error_message)

    # Verify by reading the store, not by trusting the counts.
    people = await graph_store.find_entities(tenant_id, entity_type="Person")
    return (replay.applied, replay.failed, sorted(entity.name for entity in people))


if __name__ == "__main__":
    print(asyncio.run(main()))
```

### What the script does and does not prove

`replay.applied` comes back `1` — one `DocumentExtracted` — and
`replay.failed` `0`. The names come back from the graph store, and that last
read is the part that matters: `applied` counts *delivery*, so it would read
`1` just as happily over a fold that wrote nothing, and `VectorProjection` is
in the list contributing exactly that. It subscribes to `EntitiesEmbedded`,
which extraction never emits, so it sees this event and ignores it. Wire it in
anyway if embeddings are coming; appending them is
[the write model's](use-the-write-model.md) job.

Two lines are load-bearing in a way that is easy to edit away:

- **`document_stream(tenant_id=..., source_id=...)`** must use the same two
  values passed to `build_graph`. Nothing on the report carries the stream
  back to you, and an event appended elsewhere leaves the `Document` aggregate
  rehydrating from a stream with none of its history.
- **`if report.event is None`.** It cannot fire through `build_graph` — the
  aggregate is built fresh per call — but the type says it can, `[None]` is
  not an empty batch, and `append` rejects an empty one outright.

### Turning it into something you operate

Four substitutions, none of which changes the composition:

| Swap | For | Why |
|---|---|---|
| `InMemoryEventStore` | `PostgreSQLEventStore` / `SQLiteEventStore` | the log has to outlive the process to be a log |
| `InMemoryCheckpointRepository` / `InMemoryDLQRepository` | `SQLCheckpointRepository` / `SQLDLQRepository` | an in-memory DLQ over a durable log loses the poison events while the log still says they never applied |
| `InMemoryGraphStore` / `InMemoryVectorStore` | `Neo4jGraphStore` / `PgVectorStore` | their extras; the ports are unchanged |
| `max_retries=0` | the default policy | a remote store makes a transient failure a real category — see [step 3](#step-3-construct-the-projections-graphprojection-vectorprojection-with-a-checkpoint-repository-a-dlq-repository-and-a-retry-policy) |

The one thing that does change with a durable log: **every process that reads
it must `import redstring`**, because that import is what registers
`DocumentExtracted` and `EntitiesEmbedded` with `eventsource`'s registry. A
rebuild worker importing only `eventsource` fails on the first envelope it
tries to deserialise.

To rebuild rather than build, put the wipe in front of the `replay` call and
keep `from_position` at `None`:

```python
await graph_store.delete_by_tenant(tenant_id)
await vector_store.delete_by_tenant(tenant_id)
replay = await replay(event_store, projections)
```

That is the whole difference, and it is covered in
[Rebuilding a store from scratch](#rebuilding-a-store-from-scratch-wipe-replay-from-zero-verify)
and, for one read model at a time, in
[Rebuild a projection](rebuild-a-projection.md).

### Where this script lives

`docs/examples/drive_projections.py`, executed by a test modelled on
`tests/unit/test_end_to_end_example.py` — an example nothing runs is an
example that rots. That test asserts every import is from `redstring`
itself, so its `ALLOWED_NON_KG_ROOTS` has to admit `eventsource` for this
one: the foreign types are the point of the guide, and re-exporting another
package's ports under our names would be worse than depending on them openly.
Nothing else is admitted, so the redstring half of the script stays evidence
about the public surface.
