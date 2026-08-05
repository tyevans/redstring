# Rebuild a projection from the event log

Rebuild a `GraphStore` or `VectorStore` from scratch by replaying the global
event feed through `project`. Use this when a read model is wrong, stale, or
built by an older version of the fold — the log is the write model, and the
stores are derived and disposable.

## Before you start

You need an event store that exposes a `GlobalEventFeed` (`read_all`), the
stores you want to rebuild, and — strongly recommended — a `DLQRepository` so
events that fail to apply are recoverable rather than merely counted.

Everything you need is exported from the package root:

```python
from redstring import GraphProjection, ReplayReport, VectorProjection, project
```

Everything else is `eventsource`'s, not this package's. `project`'s signature
names those types directly — `GlobalEventFeed` (the `feed` argument),
`EventSubscriber` (what a projection is, as far as `project` is concerned) and
`Position` — as do the projection constructors, with `ProjectionCheckpoints`
and `DLQRepository`. Import them from `eventsource`: `redstring.__all__` is
the whole promise this package makes, and it deliberately does not re-export
another library's ports.

For live catch-up rather than a rebuild, see
[Drive projections from an event store](drive-projections-from-an-event-store.md).
For what each event means, see [Events](../reference/events.md).

## When a rebuild is the right move (and when catch-up is)

A rebuild is a foreground operation someone is waiting on: it wipes the read
model and folds the whole log back in from position zero. Reach for one when

- **the fold changed** — a new handler, a changed id derivation, a corrected
  bug in `GraphProjection` or `VectorProjection`. Events already applied were
  applied by the old code, and nothing will revisit them;
- **the store is wrong** — corrupted, partially written, or restored from a
  backup you do not trust more than the log;
- **the store is new** — a second adapter (a `pgvector` store beside the
  in-memory one, a Neo4j graph beside a test double) standing up over a log
  that already exists.

**If the projection is merely behind, do not rebuild.** Catch-up is the same
`project` call with `from_position` set to where you stopped:

```python
report = await project(event_store, projections, from_position=previous.last_position)
```

A rebuild over a large log costs the whole log; a catch-up costs the tail. The
result is identical either way, because both folds are the same upserts and
idempotent deletes — so the choice is about time, not about correctness.

`project` never consults a checkpoint. It starts wherever `from_position`
says, which for a rebuild is the default `None`. That is the property that
makes a rebuild simple — there is no checkpoint to reset first — and it is
also the thing to be careful about, because **the checkpoint and the position
are two different mechanisms and one is not usable as the other**:

| | Checkpoint | Position |
|---|---|---|
| Belongs to | one projection | the feed |
| Lives in | a `ProjectionCheckpoints` repository | `ReplayReport.last_position` |
| Holds | the last applied `event_id` and its type | an opaque feed cursor |
| Read by | `projection.get_checkpoint()`, live subscription runners | `project(..., from_position=...)` |

So a catch-up driven by `project` resumes from a `last_position` **you** kept,
not from `get_checkpoint()` — the checkpoint is an event id and `from_position`
wants a `Position`. If you want catch-up you do not have to carry state for,
that is a subscription runner's job rather than this recipe's; see
[Drive projections from an event store](drive-projections-from-an-event-store.md).

The projections still write checkpoints during a rebuild if you gave them a
`checkpoint_repo`, so a rebuild leaves them pointing at the last event of the
log — which is what a live runner should see next.

## Step 1: Wipe the read model per tenant

Wipe the stores yourself, one tenant at a time:

```python
for tenant_id in tenant_ids:
    entities_removed = await graph_store.delete_by_tenant(tenant_id)
    vectors_removed = await vector_store.delete_by_tenant(tenant_id)
```

Both return a count of what they removed, and neither touches another tenant.
`GraphStore.delete_by_tenant` counts **entities only** — the tenant's
relationships and alias rows go with them but are not in the number, so a
return of `0` means "this tenant had no entities", not "nothing happened".

You have to enumerate the tenants because nothing else can. Neither port has
an operation that spans tenants, so there is no "list every tenant in the
store" to call and no cross-tenant truncate to fall back on. The tenant list
comes from wherever your application already keeps it.

### Do not call `reset()` — and know what it does before it refuses

`GraphProjection` and `VectorProjection` inherit `reset()` from the library's
projection base, and both **deliberately refuse** it:

```python
await graph_projection.reset()
# NotImplementedError: GraphStore has no cross-tenant delete by design;
# wipe with delete_by_tenant(tenant_id) for each tenant being rebuilt
```

The refusal is the point. The base class's default `_truncate_read_models` is
a no-op, and inheriting that would give you a `reset()` that appeared to wipe
the read model and wiped nothing — after which a rebuild reports success while
the store still carries every stale entity from before, and nothing will ever
remove them. An error you cannot miss is better than a rebuild you cannot
trust.

**`reset()` is not atomic, and the part that runs first is the part that
succeeds.** The base class clears the projection's checkpoint *before* calling
`_truncate_read_models`, so a `reset()` that raises has still discarded the
checkpoint if you passed a `checkpoint_repo`. That is harmless for a rebuild —
you were about to fold the whole log in anyway, and `project` never consults a
checkpoint (see the previous section) — but do not read the `NotImplementedError`
as "nothing happened".

### Wipe only the store you are rebuilding

The two stores are separate ports on purpose (see
[ADR 0002](../adr/0002-two-store-ports.md)), and the two folds consume disjoint
events. If you are rebuilding only the vector projection, wipe only the vector
store; deleting the graph tenant as well would oblige you to replay the graph
fold too. "Rebuilding only one of the two projections" below covers that case.

For a Neo4j graph store, `delete_by_tenant` also reaps the tenant's alias nodes
and blocking-key nodes. The alias sweep is load-bearing rather than tidiness:
alias ids are derived rather than generated, so aliases surviving a wipe would
be replayed over by the tenant's merges and `delete_by_tenant` would stop being
a reset in exactly the case a rebuild needs it to be one.

## Step 2: Construct the projections with a DLQ repository

Each projection takes its store first and everything else by keyword:

```python
graph_projection = GraphProjection(
    graph_store,
    checkpoint_repo=checkpoints,
    dlq_repo=dlq,
)
vector_projection = VectorProjection(
    vector_store,
    checkpoint_repo=checkpoints,
    dlq_repo=dlq,
)
```

The store is the only argument this package adds. Everything after it is the
parent projection constructor, spelled out rather than forwarded through
`**kwargs`, so `checkpoint_repo`, `dlq_repo`, `enable_tracing`, `retry_policy`,
`tracer` and `tenant_filter` are all reachable and all typed. The last three
are **keyword-only**; `checkpoint_repo`, `dlq_repo` and `enable_tracing` are
positional-or-keyword, and passing them positionally is a way to get
`enable_tracing` where you meant `retry_policy` — name them.

### Pass `dlq_repo`, or a poison event is a number and nothing else

`dlq_repo` is optional and defaults to `None`, which is the wrong default for a
rebuild. Without it a permanent failure is logged at critical and re-raised;
`project` catches it and increments `failed`, and the event itself is gone as
far as your process is concerned. You would know *how many* events did not land
and nothing about *which*. With a repository, each failure is a `DLQEntry`
carrying `event_id`, `event_type`, the serialized `event_data`,
`error_message`, `error_stacktrace` and `retry_count` — enough to fix the cause
and re-run (Step 5).

One repository serves both projections. Entries are tagged with
`projection_name`, which is the projection's class name — `"GraphProjection"`
or `"VectorProjection"` — so a shared DLQ stays attributable:
`get_failed_events(projection_name="GraphProjection")` narrows to one fold, and
`get_projection_failure_counts()` says which fold is unhappy without reading
every entry.

### `checkpoint_repo` is optional, and a rebuild does not read it

`project` starts wherever `from_position` says and never consults a checkpoint,
so a rebuild works fine with `checkpoint_repo=None`. Pass one anyway if a live
subscription runner will take over afterwards: the projections write
checkpoints as they go, and the rebuild leaves them pointing at the last event
of the log, which is where the runner should resume. With `None`, checkpoint
tracking is off entirely and `get_checkpoint()` and `get_lag_metrics()` both
return `None`.

### Consider `retry_policy` for the rebuild specifically

The default policy is an `ExponentialBackoffRetryPolicy` with `max_retries=2`
(three attempts) and a 2-second base delay, and it retries **every** exception.
That is the right shape for a live projection, where most failures are a
transient store hiccup. In a rebuild it is often not: a `MissingEntityError` or
a `DimensionMismatchError` is deterministic, and the retries cost roughly six
seconds per poison event before it reaches the DLQ. Over a log with a few
thousand of them, that is the whole run.

Two ways to spend less of the rebuild asleep:

```python
from eventsource.application.projections.retry import (
    ExponentialBackoffRetryPolicy,
    FilteredRetryPolicy,
    NoRetryPolicy,
)
from eventsource.application.subscriptions.retry import TRANSIENT_EXCEPTIONS

# Fail fast on everything: fastest, but a blipping store becomes a DLQ entry.
GraphProjection(graph_store, dlq_repo=dlq, retry_policy=NoRetryPolicy())

# Retry only what could plausibly succeed on a second attempt.
GraphProjection(
    graph_store,
    dlq_repo=dlq,
    retry_policy=FilteredRetryPolicy(
        base_policy=ExponentialBackoffRetryPolicy(),
        retryable_exceptions=TRANSIENT_EXCEPTIONS,
    ),
)
```

`FilteredRetryPolicy` is an **allowlist**: it wraps a base policy and retries
only exceptions matching `retryable_exceptions`, so anything you leave out —
`MissingEntityError`, `DimensionMismatchError` — goes straight to the DLQ.
Either way the event still lands there; exhausting retries and declining to
retry take the same path. Reach for `FilteredRetryPolicy` when the store is
remote (Neo4j, pgvector) and `NoRetryPolicy` when it is in-process and cannot
fail transiently.

### Leave `tenant_filter` alone unless you mean it

`tenant_filter` makes a projection ignore events for other tenants — a `UUID`
for a static filter, or a `Callable[[], UUID | None]` evaluated per event. It
pairs with the per-tenant wipe in Step 1 for a single-tenant rebuild. Set it
and you must wipe exactly the tenants it admits: a filter narrower than the
wipe leaves that tenant's store empty, and a wipe narrower than the filter
leaves stale rows for the tenants you did not delete. The default of `None`
processes everything, which is what a full rebuild wants.

`enable_tracing` defaults to `False` and is ignored when you pass a `tracer`
explicitly.

## Step 3: Run `project` over the global feed

```python
report = await project(event_store, [graph_projection, vector_projection])
```

That is the whole rebuild. `project` reads the feed with
`read_all(from_position)` and delivers every envelope's event to every
projection you passed, in log order, one event at a time — projection order
within an event follows the sequence you passed, so nothing here parallelises
and nothing reorders.

The default `from_position=None` means from the very beginning, which is what a
rebuild wants. There is nothing else to reset first: `project` never reads a
checkpoint (see "When a rebuild is the right move" above), so the projections'
existing checkpoints do not have to be cleared and will simply be overwritten
as the fold advances.

### Pass every projection you wiped, in one call

Deliver the log once to both projections rather than twice to one each. The
two folds consume disjoint events, so a second pass costs you a second full
read of the log and buys nothing. If you wiped only one store, pass only that
projection — see "Rebuilding only one of the two projections" below.

### An event a projection has no handler for is ignored, not an error

`GraphProjection` handles `DocumentExtracted`, `EntitiesMerged` and
`MergeUndone`; `VectorProjection` handles only `EntitiesEmbedded`. Each
projection ignores everything else silently — the library's default
`unregistered_event_handling` is `"ignore"`, and neither projection changes
it — so delivering the whole feed to both is correct rather than merely
tolerated.

Two consequences worth having straight before you read the report:

- **An ignored event still counts as `applied`.** `project` counts an event as
  applied when no projection rejected it, so an event nothing folded is
  applied, not skipped. `applied` measures delivery, not writes.
- **Both projections checkpoint on the last event of the log**, not on the last
  one they had a handler for. A checkpoint is a resume point; stopping it
  before an event a projection deliberately ignored would re-deliver that event
  on every resume, forever. This is pinned by
  `tests/unit/projections/test_checkpoints.py::test_the_checkpoint_tracks_delivery_and_not_application`,
  because the opposite assumption is the natural one.

### A poison event does not stop the run

By the time `project` sees an exception, the projection base class has already
retried it under the `retry_policy` and written a `DLQEntry` if you passed a
`dlq_repo` (Step 2) — then re-raised. That re-raise exists for *live*
subscriptions, where stopping is right because checkpointing past a failure
loses the event. A rebuild wants the opposite: the failure is already recorded,
and stopping would let one bad event deny the projection every event after it.
So `project` catches, increments `failed`, and continues to the next event.

The catch is deliberately unnarrowed — a projection may raise anything, and
"this event did not apply" is the only distinction a rebuild can act on. Two
things follow:

- **A failure is counted once per event**, however many projections rejected
  it, so `applied + failed` is the number of events read.
- **A rejecting projection does not stop its siblings** from seeing that same
  event: `project` finishes the projection loop before recording the failure.

A failed event can still have written part of its state — the graph fold writes
entities and then relationships, so a `MissingEntityError` on an edge leaves
that document's entities in the store. That is safe only because re-applying
the fixed event is idempotent, which is exactly what makes the DLQ replayable
(Step 5).

### Watch the run, don't just wait for it

`project` returns nothing until the feed ends, so a rebuild over a large log is
silent for its duration. If you want progress, get it from the projections
rather than from `project`: with a `checkpoint_repo`, `get_checkpoint()` and
`get_lag_metrics()` are live during the fold. Failures are visible the same
way — `dlq.get_projection_failure_counts()` climbs while the run is still
going, so you can tell a slow rebuild from one quietly DLQ-ing everything
without waiting for the report.

For what each event means and which fold consumes it, see
[Events](../reference/events.md). For running the same fold continuously
instead of once, see
[Drive projections from an event store](drive-projections-from-an-event-store.md).

## Step 4: Read the `ReplayReport`

`project` returns one frozen dataclass, and it is the only thing it returns:

```python
@dataclass(frozen=True, slots=True)
class ReplayReport:
    applied: int
    last_position: Position | None
    failures: tuple[ReplayFailure, ...] = ()

    @property
    def failed(self) -> int: ...
```

- **`applied`** — events delivered that no projection rejected. An event every
  projection *ignored* still counts as applied: it was delivered and nothing
  rejected it. `applied` measures delivery, not writes, so on a log of one
  `DocumentExtracted` and one `EntitiesEmbedded` a run over both projections
  reports `applied=2` even though each fold wrote for only one of them.
- **`failed`** — events *any* projection rejected, counted **once per event**
  however many projections rejected it, because the count answers "how much of
  the log did not make it into the read models". It is a count rather than a
  bool so that "some events failed" cannot be mistaken for "none did" by a
  truthiness check — `if report.failed:` and `if not report.failed:` both read
  correctly, and there is no `ok` attribute to be tempted by. It is a
  **property derived from `failures`**, not a field: the two cannot drift, and
  `ReplayReport(...)` takes no `failed` argument.
- **`failures`** — one `ReplayFailure` per *rejection*, so an event both folds
  rejected appears twice while `failed` counts it once. Each carries
  `position`, `event_type`, `projection` (the class name) and `error` (the
  exception object, not its message). This is the field to read when `failed`
  is non-zero and you want to know *which* event; see Step 5 for the DLQ,
  which holds the same information durably.
- **`last_position`** — the position of the last event *read*, or `None` if the
  feed yielded nothing. Failure does not affect it: a rejected event still
  advances the position, because the position records where reading got to, not
  where applying got to.

The first check after a rebuild is `failed`:

```python
report = await project(event_store, [graph_projection, vector_projection])
if report.failed:
    ...  # Step 5: the events are in the DLQ
```

### `applied + failed` is the number of events read

Every event read lands in exactly one of the two counters, so the sum is the
size of the log you just folded — a fact worth asserting if you know the log
size independently, since it is what would catch a feed that ended early.

What the sum does *not* tell you is how many writes happened. A run over a
single projection reports the whole log as applied, most of it ignored; a run
over both reports the same number. Neither counter is per projection. If you
want to know what each fold did, ask the projections: `get_checkpoint()` for
where each got to, and `dlq.get_projection_failure_counts()` for which fold is
unhappy (Step 2).

### `last_position` is a resume point, not a record of success

Pass it straight back to continue where the run stopped:

```python
report = await project(event_store, projections, from_position=previous.last_position)
```

Two consequences, both of which surprise people:

- **`from_position` is exclusive**, so resuming from the `last_position` of a
  *completed* run applies nothing and reports `applied=0, failed=0,
  last_position=None`. That is a correct no-op, not a lost log — see
  "Rebuilding a subset" below.
- **`last_position=None` is ambiguous on its own.** It means the feed yielded
  no events, which is either an empty log or a resume from the end. Read it
  together with `applied + failed == 0`, and take the meaning from what you
  asked for rather than from the report.

Because failure still advances the position, a run that reports
`failed > 0` and a `last_position` at the end of the log has read the whole log
and left the failures in the DLQ. There is no partial-run position to recover
from: `project` only returns after the feed ends, and if it raised instead
(see "Bounding the run with `max_events`") there is no report at all — the last
position reached is in the `RuntimeError` message.

### What a healthy rebuild looks like

`failed == 0`, `applied` equal to the number of events in the log, and
`last_position` not `None` unless the log really is empty. Anything else is
Step 5.

## Step 5: Inspect the DLQ for events that did not land

`report.failures` names the events in memory; the DLQ is where they are
durably, with the serialized payload. Read whichever suits — in a script,
start with the report:

```python
for failure in report.failures:
    print(failure.projection, failure.event_type, failure.position, failure.error)
```

For the payload, and for failures from *earlier* runs, read the DLQ straight
after the run:

```python
if report.failed:
    for entry in await dlq.get_failed_events(limit=1000):
        print(entry.projection_name, entry.event_type, entry.error_message)
```

Each entry is a `DLQEntry` (`eventsource.ports.dlq`) carrying `id`, `event_id`,
`projection_name`, `event_type`, `event_data`, `error_message`,
`error_stacktrace`, `retry_count`, `first_failed_at`, `last_failed_at`,
`status`, `resolved_at` and `resolved_by`. `event_data` is the event serialized
with `model_dump(mode="json")`, so it is the whole event — enough to reproduce
the failure without going back to the log.

### `get_failed_events` defaults will hide entries from you

Two defaults bite on a rebuild, and both fail quietly:

- **`limit=100`.** A rebuild that DLQ'd four hundred events shows you a hundred
  of them, and nothing says so. Compare what you read against `report.failed`,
  or pass a `limit` above it.
- **`status="failed"`.** Entries you marked resolved, or that a retry left in
  `"retrying"`, are not in the default result. Pass `status` explicitly when you
  are auditing rather than triaging.

Narrow by fold with `projection_name` — the projection's class name,
`"GraphProjection"` or `"VectorProjection"` (Step 2). For a count per fold
without reading entries, `get_projection_failure_counts()`; for the whole DLQ's
health, `get_failure_stats()` returns `total_failed`, `total_retrying`,
`affected_projections` and `oldest_failure`.

### Entries are keyed by `(event_id, projection_name)`, not appended

`add_failed_event` upserts. The same event failing the same projection again —
on a re-run, or after a retry — updates the existing row's `retry_count` and
error rather than adding a second one, and `first_failed_at` keeps the original
time while `last_failed_at` moves. So the DLQ size is the number of distinct
poisoned events per fold, and it does not inflate when you re-run a rebuild.

One consequence worth knowing before you count anything: an event that failed
in *both* folds is two entries with one `event_id`, while `ReplayReport.failed`
counted it once (Step 4). `sum(c.failure_count for c in counts)` and
`report.failed` are allowed to disagree, and the difference is events both
projections rejected. `len(report.failures)` is the one that matches the DLQ,
because it is per rejection for the same reason the DLQ is.

### Read the error, then decide whether the fold or the input is wrong

The two failures a rebuild actually produces:

- **`MissingEntityError: entity <id> does not exist in tenant <id>`** — the
  graph fold's `upsert_relationship` refused an edge whose endpoint this tenant
  does not have. Usually a document referencing an entity that another,
  unfolded document introduces. The entity ids are attributes on the exception,
  not just text in the message, and the failing event's *entities* did land —
  the handler writes entities and then relationships.
- **`DimensionMismatchError: expected a vector of dimension N, got M`** — the
  vector store and the emitter disagree about which embedding model is in play.
  This one is not a replay-order problem and re-running will not fix it; see
  Troubleshooting.

### Re-run the whole log rather than replaying single entries

The DLQ is not a graveyard, and there is no per-entry replay to reach for here.
Once the cause is fixed, run `project` over the whole log again: every write in
both folds is an upsert or an idempotent delete, so the events that already
landed land identically and the previously poisoned one now applies. That is
the same property that makes the rebuild restartable at all (Step 4), and it is
why a partially-applied poison event — entities written, relationship refused —
is safe to leave in place.

Prefer a full re-run over hand-applying an event: it exercises the fold you
fixed against the log you have, which is the thing you actually want to be
confident in.

Mark the entries off once they have landed:

```python
for entry in await dlq.get_failed_events(limit=1000):
    await dlq.mark_resolved(entry.id, resolved_by="rebuild-2026-08-04")
```

Nothing marks them for you — a successful re-run does not touch the DLQ, so an
unresolved entry means "nobody has looked at this", not "still broken". Clean up
later with `delete_resolved_events(older_than_days=...)`, which deletes only
`"resolved"` entries and never a failed or retrying one. Note the cutoff is a
rolling instant rather than a calendar boundary, so `older_than_days=0` deletes
everything already resolved, including a moment ago.

### If you rebuilt without a `dlq_repo`

There is nothing to inspect. The failures were logged at critical by the
projection before `project` counted them, so the log is the only record: grep
for `sent to DLQ` and `failed permanently` alongside the event ids. Add a
`dlq_repo` (Step 2) and re-run — a rebuild is cheap compared with reconstructing
which events were poisoned from a log file.

A DLQ write can itself fail, and it is deliberately swallowed so it cannot mask
the processing error being re-raised. That case logs
`Failed to write event <id> to DLQ`, and the event is counted in `failed` with
no entry to match it. If your entry count is short of `report.failed` and the
`limit` is not the reason, look for that line.

## Rebuilding a subset: `from_position` is exclusive

Sometimes you do not want the whole log: a rebuild that died halfway, or a
catch-up over the tail. Pass `from_position`:

```python
report = await project(event_store, projections, from_position=previous.last_position)
```

`from_position` is **exclusive**, and it is exclusive because `read_all` is —
`project` hands the value straight to `feed.read_all(from_position)` and adds
no arithmetic of its own. The event *at* that position is not re-delivered.

Two consequences, in the order they bite:

- **`None` — the default — means before the first event, not at it.** A
  rebuild passes nothing and gets the whole log. An off-by-one here would drop
  the first event of every rebuild, which on this schema is a whole document,
  so it is pinned by
  `tests/unit/projections/test_replay_equivalence.py::test_a_replay_from_position_zero_reads_the_first_event`.
- **Resuming from a completed run's `last_position` applies nothing.** You get
  `applied=0, failed=0, last_position=None` — a correct no-op, not a lost log
  (Step 4 on why that `None` is ambiguous read on its own).

### Resume from a position you kept, not from a checkpoint

`from_position` wants a `Position`; `get_checkpoint()` returns an event id
string. They are not interchangeable, and `project` never reads a checkpoint
(see "When a rebuild is the right move"). The only `Position` on offer is
`ReplayReport.last_position` from a previous call, so a resumable rebuild means
keeping that value somewhere outside the process. If you would rather not carry
it, you want a subscription runner rather than this recipe — see
[Drive projections from an event store](drive-projections-from-an-event-store.md).

### Do not wipe when you resume

Step 1's per-tenant wipe belongs to a *rebuild*. Resuming mid-log over a wiped
store folds in only the tail and leaves the read models missing everything
before `from_position` — with a `ReplayReport` that looks entirely healthy,
because every event it read applied. Wipe and start from `None`, or resume and
do not wipe.

### Halves equal the whole

Projecting the first half and then the rest produces the same store state as
projecting the whole log in one go — the property that makes a restart safe at
all, and the one that would otherwise fail invisibly, since no single run
reveals a read model that is slightly wrong.
`tests/unit/projections/test_checkpoints.py::test_two_halves_project_to_the_same_state_as_one_whole`
holds it by dumping two independently built rigs through the ports and
comparing.

It holds because both folds are upserts and idempotent deletes, so an event
delivered twice is harmless. That is what makes overlapping resumes safe too:
if you are unsure where a dead run stopped, resume from an *earlier* position
rather than a later one. Re-delivering events costs time; skipping them costs
correctness.

## Bounding the run with `max_events`

`project` reads at most `max_events` events and raises when the feed keeps
going past it:

```python
report = await project(event_store, projections, max_events=50_000)
# RuntimeError: replay read more than 50000 events without the feed ending;
# the adapter's cursor is probably not advancing (last position: <Position>)
```

The default is `MAX_EVENTS_PER_REPLAY`, which is `10_000_000` — far above any
real rebuild, so the default never fires on a healthy log. It is not a
pagination knob and there is no "read the next `max_events`" idiom; `from_position`
is how you fold a subset (see the previous section).

### Why the bound exists at all

The loop's exit condition is adapter-supplied. `project` iterates
`feed.read_all(from_position)` until it stops yielding, so a cursor that fails
to advance yields forever and the rebuild **hangs**. A hang is worse than a
failure: in CI it reads as infrastructure trouble and gets retried rather than
investigated, and a rebuild is exactly the long-running foreground operation
nobody is surprised to see take a while. The bound converts that into a
`RuntimeError` naming the likely cause and carrying the last position reached,
which is what tells a stuck cursor apart from a bound set too low.

`tests/unit/projections/test_replay_equivalence.py::TestTheReplayIsBounded`
holds both halves — a bound nothing exercises is a bound nobody knows works.

### The bound is the number of events *allowed*

`max_events=2` over a two-event log succeeds and reports `applied=2`. The check
fires only on the event *after* the bound, so equality is fine and the natural
off-by-one — refusing a log exactly the size of its bound — is pinned by
`test_a_log_exactly_at_the_bound_is_not_rejected`.

### When it raises there is no report

The `RuntimeError` propagates out of `project`, so you get no `ReplayReport`
and therefore no `last_position` to resume from — the last position reached is
in the exception message and nowhere else. Everything folded before the raise
**stayed folded**: the projections were called event by event and nothing is
rolled back, so a store left by a bounded-out run is partially built. Fix the
cause and re-run from the beginning; the folds are idempotent, so re-reading
what already landed is only a cost in time (Step 4).

### Setting it deliberately

Lower it when you would rather a rebuild fail fast than run away — a smoke run
against a new adapter is the usual case, where a cursor bug is exactly what you
are looking for. Do not set it below the size of the log you intend to fold, or
a healthy run raises after doing most of the work. If you know the log size,
`max_events` a little above it turns "the feed never ended" into an error in
seconds rather than at ten million.

## Rebuilding only one of the two projections

`project` takes any sequence of subscribers, so pass just the one you are
rebuilding:

```python
for tenant_id in tenant_ids:
    await vector_store.delete_by_tenant(tenant_id)

report = await project(event_store, [vector_projection])
```

Wipe only the store belonging to that projection. Deleting the other tenant's
rows as well would oblige you to replay its fold too, and a rebuild that folds
one projection over a store you wiped for both leaves the other empty with a
perfectly healthy `ReplayReport` (Step 4 — the counters are not per
projection).

### Why this is safe

The two folds consume **disjoint** events. `GraphProjection` handles
`DocumentExtracted`, `EntitiesMerged` and `MergeUndone`; `VectorProjection`
handles only `EntitiesEmbedded`. Neither reads the other's store, and the
stores are separate ports on purpose — see
[ADR 0002](../adr/0002-two-store-ports.md). So rebuilding one cannot disturb
the other, and the one you left alone keeps serving reads throughout.

Delivering the whole feed to a single projection is correct rather than merely
tolerated: everything it has no handler for is ignored (Step 3), so a
single-projection rebuild reports nearly the whole log as `applied` while
writing only its own events. Do not try to pre-filter the feed to "just the
events this fold wants" — there is no argument to `project` for it, and the
projection already does exactly that.

### What still holds even though the folds are disjoint

- **`applied` counts delivery, not writes.** A vector-only rebuild over a log
  that is mostly `DocumentExtracted` reports them all applied. Check the DLQ
  and `get_projection_failure_counts()` to see what the fold actually did
  (Steps 2 and 5).
- **The checkpoint still advances to the end of the log**, for the projection
  you ran and not for the other one. If a live runner is driving the projection
  you did *not* rebuild, its checkpoint is untouched and it carries on from
  where it was.
- **A single-projection run still reads the whole log**, so it costs the same
  read as a run over both. If you are rebuilding both, pass both in one call
  rather than making two passes.

### Which one you usually want

The vector store is the cheap one to rebuild: one handler, one idempotent
`upsert_many` per event, and no ordering constraint between events — two
`EntitiesEmbedded` for disjoint entity sets commute, and two for the same
entity are a last-write-wins the log's order settles.

The graph fold is the one with structure: relationships need their endpoints,
and every endpoint is resolved through the alias table before the upsert, so a
graph rebuild is where `MissingEntityError` and merge ordering show up
(Step 5, and [ADR 0009](../adr/0009-the-extraction-fold-resolves-through-aliases.md)).
A `DimensionMismatchError`, by contrast, is only ever the vector fold.

Rebuilding one projection is also the shape of standing up a *new* adapter — a
`pgvector` store beside the in-memory one — over a log that already exists.
There is nothing extra to do: construct the projection over the new store and
project the log, leaving the graph side untouched.

## Troubleshooting

### `NotImplementedError: ... has no cross-tenant delete by design`

```
NotImplementedError: GraphStore has no cross-tenant delete by design; wipe with
delete_by_tenant(tenant_id) for each tenant being rebuilt
```

You called `reset()` on a projection. Both projections raise this from
`_truncate_read_models` — the graph one naming `GraphStore`, the vector one
naming `VectorStore` — and the fix is Step 1: loop over your tenants calling
`delete_by_tenant(tenant_id)` on the store you are rebuilding.

The error is deliberate. The base class's default `_truncate_read_models` is a
no-op, so inheriting it would give you a `reset()` that appeared to wipe the
read model, wiped nothing, and left the rebuild reporting success over a store
still full of stale entities.

**Check the checkpoint before you retry.** `reset()` clears the projection's
checkpoint *before* calling `_truncate_read_models`, so the raise leaves the
checkpoint already gone if you passed a `checkpoint_repo`. Harmless for a
rebuild — `project` never reads one — but do not read the exception as
"nothing happened" (Step 1).

### `RuntimeError: replay read more than N events`

```
RuntimeError: replay read more than 50000 events without the feed ending; the
adapter's cursor is probably not advancing (last position: <Position>)
```

The feed kept yielding past `max_events`. Two causes, and the message
distinguishes them:

- **The bound is lower than your log.** The position in the message is a real,
  advancing position somewhere in the middle of the log. Raise `max_events`
  above the log size, or leave it at the default `MAX_EVENTS_PER_REPLAY`
  (10,000,000) and it will never fire on a healthy feed.
- **The adapter's cursor is not advancing.** The position is at or near the
  start, or is the same one you saw on the previous attempt. That is an adapter
  bug in `read_all`, not something to tune around — re-running with a larger
  bound only makes the failure take longer.

The position printed is the last one *recorded*, which is the event before the
one that tripped the bound; `project` checks the count before assigning the new
position. Treat it as "roughly where reading got to", not as a resume point.

**There is no `ReplayReport` when this raises**, so there is no
`last_position` to resume from — the message is the only record. Everything
folded before the raise stayed folded; nothing is rolled back. Fix the cause
and re-run from the beginning (the folds are idempotent, so re-reading what
already landed costs only time).

### The rebuild reports `failed > 0`

The events are in the DLQ if you passed a `dlq_repo`; read it (Step 5) rather
than guessing. Remember `failed` counts an event once however many projections
rejected it, so it can be smaller than the number of DLQ entries.

The common cause in the graph fold is `MissingEntityError`:

```
MissingEntityError: entity <entity_id> does not exist in tenant <tenant_id>
```

`GraphStore` refuses a dangling edge, so writing a relationship whose endpoint
this tenant does not hold fails. During a rebuild that usually means a
document referencing an entity another, not-yet-folded document introduces.
The exception carries `entity_id` and `tenant_id` as attributes, so you can
check the missing endpoint against the log rather than parsing the message.

Two things about the partial state it leaves:

- **The document's entities did land.** `_apply_extraction` calls
  `upsert_entities` and then `upsert_relationships`, and only the second one
  raised. This is safe purely because re-applying the fixed event is
  idempotent.
- **Self-loops are already gone.** Endpoints are resolved through the alias
  table first, and an edge whose two endpoints collapse onto one entity is
  deleted rather than written (see
  [ADR 0009](../adr/0009-the-extraction-fold-resolves-through-aliases.md)). An
  edge missing after a rebuild is not necessarily a failure — check the DLQ
  before assuming it is.

The fix is not to hand-apply the event. Correct whatever produced the ordering
or the missing entity, then run `project` over the whole log again: every write
in both folds is an upsert or an idempotent delete, so the events that already
landed land identically and the poisoned one now applies.

If `failed > 0` and the DLQ is empty, you either rebuilt without a `dlq_repo`
or a DLQ write itself failed — both covered at the end of Step 5.

### `DimensionMismatchError` on the vector projection

```
DimensionMismatchError: expected a vector of dimension 768, got 384
```

Only `VectorProjection` can raise this, and only from its one handler:
`_apply_embeddings` calls `upsert_many`, and the store rejects any vector
whose length is not its configured `dimension`. The store and whatever emitted
the `EntitiesEmbedded` disagree about which embedding model is in play.
`expected` (the store's dimension) and `actual` (the vector's length) are
attributes on the exception, so you do not have to parse the message.

**Re-running will not fix this one.** Unlike `MissingEntityError`, it is not
an ordering problem — nothing later in the log changes the store's dimension,
so the same event fails identically on every replay and a second rebuild just
refills the DLQ. Stop and fix the disagreement first.

**The whole event is rejected, not the bad record.** `upsert_many` validates
the batch before writing anything, so an `EntitiesEmbedded` carrying one
wrong-length vector among hundreds leaves *none* of them in the store. That is
unlike the graph fold's partial write (entities landed, relationship refused),
and it is why re-running after the fix is enough: there is no half-applied
batch to reconcile.

Fix it by rebuilding against a store whose `dimension` matches the model that
produced the embeddings. For a genuine model change that means a **new** store
— a new table or collection — not an in-place write of differently-shaped
vectors. The port is explicit that accepting the wrong length would not
surface as an exception but as mediocre search results, which read as a
mediocre embedding model rather than as a bug. Note that two models of the
*same* dimension are equally incomparable and nothing in the port can detect
that, so "the numbers match" is not the check you want.

With the pgvector adapter the same exception can come from `ensure_schema()`
instead of from a projected event, when the existing table's declared
`vector(n)` typmod differs from the dimension you constructed the store with —
there, `expected` is the table's declared dimension and `actual` is your
store's. That is the same disagreement caught one step earlier, and it is the
one to hope for: it fails before any event is folded, and it names the store
rather than surfacing as a Postgres error on the first insert. Call
`ensure_schema()` before you start the rebuild.

If a rebuild is DLQ-ing a run's worth of these, stop it rather than waiting —
`dlq.get_projection_failure_counts()` is live during the fold (Step 3), and the
default retry policy spends about six seconds per poison event before giving
up on a failure that could never have succeeded. `NoRetryPolicy` or a
`FilteredRetryPolicy` allowlist (Step 2) is worth setting for a rebuild you
expect to surface these.

## Verifying the rebuild

A `ReplayReport` of `failed=0` says every event was delivered without an
exception. It does not say the read models hold what the log describes. To
check that, compare the store state before and after.

### Dump through the ports, not through the adapter

Read the state back with `find_entities`, `get_relationships_for`,
`find_aliases` and `VectorStore.get`, and with nothing else. A dump that
reached into an adapter's internal dicts or ran raw Cypher would agree with
itself on a store that has diverged from what its own port reports — and that
divergence is exactly what a rebuild check is for.

`tests/unit/projections/conftest.py::dump_stores` is the working version to
copy. Three details in it are load-bearing rather than incidental:

- **Sort everything before comparing.** `find_entities` promises a total
  order; `get_relationships_for` promises none. A comparison that depends on
  the order an adapter happened to return fails intermittently and for the
  wrong reason.
- **Page `find_entities` with a bound.** `_all_entities` walks the `after`
  cursor with `limit=50` and gives up after 1000 pages with a message naming
  the cause, because a cursor that fails to advance would otherwise hang
  rather than fail.
- **Include alias rows.** They are part of the state a rebuild must reproduce
  and the only part carrying an id the fold generates, so a dump that omitted
  them would let a `uuid4` alias id pass every replay comparison. Aliases are
  reached per entity via `find_aliases(entity.id, tenant_id)`, so an entity
  missing from the dump takes its aliases with it.

### Two things a rebuild must reproduce exactly

- **Alias rows.** `GraphProjection._alias_id` hashes the tenant and the
  absorbed entity into a `uuid5`. Derived, not generated — a `uuid4` would
  make each replay produce different alias rows for the same log. The merge
  event id is deliberately *not* in the hash: the row is keyed by
  `(tenant_id, alias_entity_id)` in every adapter, so hashing anything else in
  would let one logical row carry two ids depending on which merge last wrote
  it.
- **Edge endpoints.** The extraction fold resolves every endpoint through the
  alias table before upserting, so a re-extracted document cannot undo a merge
  in the read model, and an edge whose endpoints resolve to the same entity is
  *deleted* rather than written. Expect that edge to be absent after a
  rebuild; it is not a dropped write. See
  [ADR 0009](../adr/0009-the-extraction-fold-resolves-through-aliases.md).

### What the suite already holds

`tests/unit/projections/test_replay_equivalence.py` makes four claims over
hypothesis-generated logs, and the ordering of them is the useful part:

1. **The fold is correct** — the projected state matches an oracle the log
   builder maintains (`BuiltLog.expected_shape()`), compared through
   `dump_shape`, which keeps only what an oracle can predict: entity ids, each
   edge's endpoints, and each vector.
2. **A wiped store replays to the same state** — project, `delete_by_tenant`
   both stores, project again, compare full dumps.
3. **At-least-once delivery changes nothing** — every event handed to every
   projection twice.
4. **Re-projecting over a live store changes nothing** — the rebuild an
   operator starts without wiping first.

Claims 2 to 4 are self-consistency properties: both sides run the same
handlers, so a fold that does too little — never applies an undo, never
deletes a dropped edge, never writes relationships at all — leaves both sides
agreeing on the same wrong state and all three pass. That is why claim 1
exists and why it is not redundant; finding out cost three surviving mutants.
**If you write your own rebuild check, an oracle is the part to write first.**

The named boundaries (an empty log, a single document, and the merge and undo
cases) run as pinned parametrised cases as well as through the sampler, since
a property test is a sampler rather than a proof. And every test builds its
own rig via `fresh_rig()` rather than taking a fixture, because a
function-scoped fixture is created once for a whole `@given` and example 2
onwards would run against what example 1 left behind.

## Related

- [Drive projections from an event store](drive-projections-from-an-event-store.md)
  — the same folds run continuously instead of once. Go there when the
  projection is merely behind, when you want catch-up you do not have to carry
  a `Position` for, or when you are wiring the runner that takes over after a
  rebuild.
- [Events reference](../reference/events.md) — what each event carries and
  which fold consumes it. The disjointness this recipe leans on
  (`DocumentExtracted`, `EntitiesMerged`, `MergeUndone` to the graph;
  `EntitiesEmbedded` to the vector store) is stated there per event.
- [ADR 0002: two store ports](../adr/0002-two-store-ports.md) — why
  `GraphStore` and `VectorStore` are separate, why neither has a cross-tenant
  delete, and why `delete_by_tenant` is the only wipe there is. Read it if
  Step 1's refusal looks like an oversight.
- [ADR 0009: the extraction fold resolves through aliases](../adr/0009-the-extraction-fold-resolves-through-aliases.md)
  — why a replay reproduces merges exactly, why alias ids are derived rather
  than generated, and why an edge whose endpoints collapse onto one entity is
  deleted rather than written. The reasoning behind most of "Verifying the
  rebuild".
