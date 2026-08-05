# Use the write model

This guide shows you how to append to the event log yourself: record an
extraction run on a `Document`, merge and undo merges through a
`ConsolidationLog`, and handle the errors both aggregates raise instead of
writing.

Use it when you have an event store and want the log to be the record. If you
only want a populated graph and have no event store, call `build_graph`
instead — it extracts and projects in one call, and none of this applies.

Everything here follows the same shape:

1. Build a repository over your `AggregateStore` (`document_repository`, or
   `consolidation_repository` with a `SnapshotStore`).
2. Derive the aggregate id from the stream helper — `document_stream(...)` or
   `consolidation_stream(...)`.
3. Inside `async with tenant_scope(tenant_id)`, load or create the aggregate,
   call a command on it, and `await repo.save(...)`.

The commands emit events and nothing else. Saving appends them to the log; it
writes nothing to a `GraphStore` or `VectorStore`. Projections do that, and
they run separately — see
[Drive projections from an event store](drive-projections-from-an-event-store.md).

The write model is two aggregates, and each owns rules that a caller cannot be
trusted to re-check:

- **`Document`** makes extraction idempotent per model version, so a retry
  after a crash writes nothing a second time.
- **`ConsolidationLog`** enforces the three merge invariants — no merging into
  an alias, no merging an entity twice, no undoing a merge that is not in
  effect — against a consistent view of the tenant's history, under the write
  lock that makes the check mean something.

For what each aggregate holds and what each command returns, see
[the aggregates reference](../reference/aggregates.md) and
[the events reference](../reference/events.md); for why entities are not
aggregates and why the streams are shaped this way, see
[ADR 0001](../adr/0001-event-log-schema-and-granularity.md). For the
consolidation service that wraps the merge half of this in a graph-aware API,
see [Consolidate duplicate entities](consolidate-duplicate-entities.md).

## Before you start

### What you need: an `AggregateStore`, and a `SnapshotStore` for consolidation

Both repositories are built over an `eventsource` `AggregateStore` — that is
your event log, and redstring never creates one for you. Consolidation
additionally needs a `SnapshotStore`; it is a required positional argument, not
an optional one, because a tenant's merge stream grows without bound and an
omitted snapshot store surfaces as slow merges long after the code that omitted
it was written.

For a single-process job or a test, `eventsource`'s own in-memory adapters are
complete implementations and are what this project's own suites use:

```python
from eventsource.adapters.memory import InMemoryEventStore, InMemorySnapshotStore

event_store = InMemoryEventStore()
snapshot_store = InMemorySnapshotStore()
```

You also need `tenant_scope`, the async context manager every load and save
runs inside:

```python
from eventsource.domain.tenant_context import tenant_scope
```

### What is exported and what you import by path

`Document` and `document_stream` are in `redstring.__all__`, so import them
from the package root:

```python
from redstring import Document, document_stream
```

The rest of the write model is reached by path. `ConsolidationLog` and the two
repository factories live in `redstring.aggregates`; `consolidation_stream`
lives in `redstring.events.streams`:

```python
from redstring.aggregates import (
    ConsolidationLog,
    consolidation_repository,
    document_repository,
)
from redstring.events.streams import consolidation_stream
```

That split is a statement about stability, not an accident of packaging.
Anything reached by a dotted path is internal and may change in a patch
release — consolidation has no composed entry point yet, so exporting its
classes would publish a shape still being decided. Import them and expect
movement.

`CONSOLIDATION_SNAPSHOT_EVERY`, the default snapshot interval, comes from
`redstring.aggregates.repositories` if you want to reference it rather than
retype its value:

```python
from redstring.aggregates.repositories import CONSOLIDATION_SNAPSHOT_EVERY
```

The errors you will catch come from `eventsource` and from redstring both.
`TenantContextNotSetError`, `TenantMismatchError` and `OptimisticLockError`
are `eventsource.domain.exceptions`; the merge invariant errors —
`MergeIntoAliasError`, `DoubleMergeError`, `UnknownMergeError`, and their
shared base `ConsolidationInvariantError` — are `redstring.domain.exceptions`:

```python
from eventsource.domain.exceptions import (
    OptimisticLockError,
    TenantContextNotSetError,
    TenantMismatchError,
)
from redstring.domain.exceptions import ConsolidationInvariantError
```

`ConsolidationInvariantError` derives from `RedstringError`, so a caller that
already handles that base handles all three merge failures without naming
them.

For what each aggregate and command does with these types, see
[the aggregates reference](../reference/aggregates.md); for the event payloads
the commands emit, [the events reference](../reference/events.md).

## Build a repository

Both factories live in `redstring.aggregates` and both return an
`eventsource` `TenantAwareRepository` — the same type, with the same
`create_new` / `load` / `load_or_create` / `save` surface. What differs is
what you have to hand them.

### `document_repository(event_store)` — no snapshot store, and why

```python
from redstring.aggregates import document_repository

documents = document_repository(event_store)
```

That is the whole call: one positional argument, the `AggregateStore`. There
is no snapshot parameter to pass and no interval to tune. You get back a
`TenantAwareRepository[Document]`.

The reason there is no snapshot store is the shape of a document's stream. A
`Document` records one event per *model version* — a `DocumentExtracted` per
extraction model, an `EntitiesEmbedded` per embedding model — and its state is
nothing but the two lists of versions already seen. That is a handful of
events over the aggregate's whole life, so a snapshot would cost a write to
save replaying three. Each document also gets its own stream, so the streams
stay short no matter how many documents a tenant has.

If a document's stream ever does grow past a handful, read that as something
emitting per-chunk or per-entity events into the aggregate — the fix is to
stop doing that, not to add snapshots. Per-entity granularity is the thing
[ADR 0001](../adr/0001-event-log-schema-and-granularity.md) rejects, and the
extraction payloads ride inside the one event by design.

For what `Document` holds and what each command returns, see
[the aggregates reference](../reference/aggregates.md).

### `consolidation_repository(event_store, snapshot_store, snapshot_every=CONSOLIDATION_SNAPSHOT_EVERY)` — the snapshot store is required

```python
from redstring.aggregates import consolidation_repository

consolidations = consolidation_repository(event_store, snapshot_store)
```

`snapshot_store` is the second **positional** argument, and it is required.
That is deliberate. A tenant's `ConsolidationLog` holds every merge and undo
that tenant has ever performed, and the stream has no natural bound — so
rehydrating it without snapshots grows without bound too. An optional
parameter is one nobody passes, and the omission would not fail: it would
surface as slow merges months later, far from the code that omitted it. Made
required, the decision has to be made once, at the call site, in the open.

`snapshot_every` is keyword-only and defaults to `CONSOLIDATION_SNAPSHOT_EVERY`
(currently `100`). It is the number of events between snapshots:

```python
from redstring.aggregates.repositories import CONSOLIDATION_SNAPSHOT_EVERY

consolidations = consolidation_repository(
    event_store, snapshot_store, snapshot_every=CONSOLIDATION_SNAPSHOT_EVERY
)
```

The default is a starting point, not a measured optimum — small enough that a
rehydration reads a bounded tail, large enough that most saves write no
snapshot. Nothing in the library depends on the number being 100; tune it once
you know a tenant's merge volume. See
[Merges are slow: tune `snapshot_every`](#consolidation_repositoryevent_store-snapshot_store-snapshot_everyconsolidation_snapshot_every--the-snapshot-store-is-required).

Snapshots are an optimisation and nothing more. A log loaded through a
snapshot and the same log replayed from event 0 with an empty snapshot store
produce equal state and equal versions — the project asserts exactly that — so
changing `snapshot_every`, or pointing a process at a fresh snapshot store,
changes performance and never answers.

Both repositories wrap their `AggregateRepository` in `TenantAwareRepository`,
which is what makes the next section's rules enforced rather than advisory:
saving outside a `tenant_scope` raises, and so does saving an event whose
`tenant_id` disagrees with the ambient scope. Tenant isolation is checked at
write time by tested library code instead of being re-derived at every call
site.

## Do every load and save inside `tenant_scope`

Wrap the whole unit of work — derive the id, load or create, call the command,
save — in one scope:

```python
from eventsource.domain.tenant_context import tenant_scope

async with tenant_scope(tenant_id):
    aggregate_id = document_stream(tenant_id=tenant_id, source_id=source_id).aggregate_id
    document = repo.create_new(aggregate_id)
    document.record_extraction(
        tenant_id=tenant_id, source_id=source_id, model_version="ollama/qwen3.6-27b"
    )
    await repo.save(document)
```

`tenant_scope` is an async context manager that sets the ambient tenant on a
`ContextVar` and restores the previous value on exit, including on an
exception. It is what `TenantAwareRepository` reads on every save.

### Derive the aggregate id: `document_stream(...).aggregate_id`, `consolidation_stream(...).aggregate_id`

Both helpers take keyword arguments only, and both return an `eventsource`
`StreamId` whose `aggregate_id` is the `UUID` the repository wants:

```python
from redstring import document_stream
from redstring.events.streams import consolidation_stream

document_id = document_stream(tenant_id=tenant_id, source_id="doc-1").aggregate_id
consolidation_id = consolidation_stream(tenant_id=tenant_id).aggregate_id
```

Derive the id; never invent one. `document_stream` computes
`uuid5(tenant_id, source_id)` — the tenant as the `uuid5` *namespace*, the
source id as the name — so re-extracting the same document appends to the
stream it already has instead of starting a second one, and the same URL
ingested by two tenants is two streams. There is no mapping table to keep
consistent and no lookup on the write path, which also means a hand-rolled id
silently forks the history rather than failing.

Keeping the tenant in the namespace rather than concatenating it into the
hashed name is what makes the key unambiguous: a scheme that joined the two
halves before hashing would map `("t", "ab")` and `("ta", "b")` onto one
stream, and `source_id` is free-form text, so nothing else would stop it.

`consolidation_stream` returns the `tenant_id` itself as the aggregate id.
There is exactly one consolidation log per tenant, so any further derivation
would be a fiction with no second value to distinguish. One consequence is
worth knowing before you design around it: a tenant's merges are serialised,
because they all contend for that one stream. That is the intent — two
concurrent merges touching the same entities must not interleave.

A blank or whitespace-only `source_id` raises `ValueError` rather than
hashing:

```python
document_stream(tenant_id=tenant_id, source_id="   ")  # ValueError
```

`SourceDocument.id` carries no validation of its own, so this is the last
point at which a blank one can be caught; hashed, it would produce a
valid-looking stream shared by every document that had one.

The `StreamId` also carries a `category` — `DOCUMENT_CATEGORY`
(`"Document"`) or `CONSOLIDATION_CATEGORY` (`"Consolidation"`), both exported
from `redstring.events.streams`. You do not pass it to the repository, which
takes the `aggregate_id` alone, but it is what you match on when subscribing
to the log downstream; see
[Drive projections from an event store](drive-projections-from-an-event-store.md).
For why the two streams are shaped this way — one short stream per document,
one unbounded stream per tenant — see
[ADR 0001](../adr/0001-event-log-schema-and-granularity.md).

### `repo.create_new(aggregate_id)` for a first write, `await repo.load(aggregate_id)` afterwards

`create_new` is synchronous and touches no store — it instantiates the
aggregate at version 0 and nothing else. `load` is a coroutine: it replays the
stream (through a snapshot where one is configured), and raises
`AggregateNotFoundError` if the stream holds no events.

```python
async with tenant_scope(tenant_id):
    document = repo.create_new(document_id)  # first write to this stream
    ...
    reloaded = await repo.load(document_id)  # every write after that
```

`create_new` is not a "create" in the persistence sense — no stream is
reserved and no event is written, so two callers can both hold a `create_new`
aggregate for the same id quite happily. The conflict surfaces at `save`,
which appends at the exact version the aggregate believes it is at: a fresh
aggregate expects version 0, so the second save over a stream that already has
events raises `OptimisticLockError`. That is the guarantee, and it is worth
knowing which call enforces it — `create_new` never fails, `load` fails when
there is nothing, and `save` fails when your view is stale.

### Use `load_or_create` when you do not know which case you are in

A retry after a crash, a re-run, a worker that may or may not be first — none
of these know whether the stream exists. `load_or_create` returns the replayed
aggregate when it does and a fresh one when it does not:

```python
log = await repo.load_or_create(consolidation_id)
```

It is `load` with `AggregateNotFoundError` caught, so it costs the same read
and there is no reason to hand-roll the `try`/`except`. A `ConsolidationLog`
is the usual case: there is one per tenant and it is created by whichever
merge happens first, so a call site that has to decide is a call site written
against a fact it cannot know.

To tell which branch you got, check `version`, not `state`:

```python
log = await repo.load_or_create(consolidation_id)
if log.version == 0:
    ...  # nothing has ever been merged for this tenant
```

`version` is 0 for a fresh aggregate and the event count for a replayed one.
`state` is unreliable for this — a never-saved aggregate may report `None`
rather than an empty state object, so `if not log.state` and
`log.state.merges == []` are different questions and neither of them is the
one you meant to ask.

### Call commands against a freshly loaded aggregate

This is the rule the rest of the guide depends on. `Document`'s per-model
idempotency and `ConsolidationLog`'s three merge invariants are checked
against *replayed* state — the aggregate's own memory of what has already
happened. Call a command on a `create_new` aggregate over a stream that
already has events and the check runs against an empty history: it can only
say yes.

```python
# Wrong: the retry re-emits an extraction the log already has.
document = repo.create_new(document_id)
document.record_extraction(tenant_id=tenant_id, source_id="doc-1", model_version=MODEL)
await repo.save(document)  # OptimisticLockError

# Right: the reload is what makes the second call return None.
document = await repo.load(document_id)
assert (
    document.record_extraction(tenant_id=tenant_id, source_id="doc-1", model_version=MODEL) is None
)
```

The wrong version does not corrupt anything — `save` refuses it — but the
failure it produces is an `OptimisticLockError`, which reads as a concurrency
problem and sends you looking for a second writer that does not exist. The
same mistake against a `ConsolidationLog` is worse to diagnose, because a
merge validated against empty state passes checks it should have failed, and
only the version check stops it landing.

Loading does not require a `tenant_scope`; only saving does. See the next
section for why the asymmetry is deliberate rather than an oversight.

`AggregateNotFoundError` and `OptimisticLockError` are both
`eventsource.domain.exceptions`:

```python
from eventsource.domain.exceptions import AggregateNotFoundError, OptimisticLockError
```

For what `Document` and `ConsolidationLog` hold after a replay and what each
command returns, see [the aggregates reference](../reference/aggregates.md);
for the events a replay is built from, [the events
reference](../reference/events.md).

### `await repo.save(document)` outside a scope raises `TenantContextNotSetError`

```python
document = repo.create_new(document_id)
document.record_extraction(tenant_id=tenant_id, source_id="doc-1", model_version=MODEL)
await repo.save(document)  # TenantContextNotSetError — nothing is written
```

This is a refusal, not a warning: nothing reaches the log. `save` reads the
required tenant from the ambient context *before* delegating to the underlying
repository, so a missing scope fails at the boundary rather than writing an
untenanted event that a later read has no way to attribute. The aggregate is
untouched — its uncommitted events are still there, so entering a scope and
saving again works:

```python
async with tenant_scope(tenant_id):
    await repo.save(document)  # the same aggregate, now accepted
```

The check runs on every save, not only on saves that have something to write.
The tenant is read first and the uncommitted events are examined second, so a
`save` of an aggregate with no uncommitted events raises just the same outside
a scope. There is no quiet no-op path.

Fix it by widening the scope, not by catching the error. The rule in
[Do every load and save inside `tenant_scope`](#do-every-load-and-save-inside-tenant_scope)
is one scope around the whole unit of work — derive the id, load, command,
save — and a `TenantContextNotSetError` almost always means a save escaped
that block rather than that a scope is genuinely unavailable. The two shapes
that produce it are a save moved after the `async with` ended, and a save
handed to a background task that does not inherit the context; for the second,
see
[`TenantContextNotSetError` on save from a background task that lost the scope](#await-reposavedocument-outside-a-scope-raises-tenantcontextnotseterror).

Catch it from `eventsource` when you do need to handle it — at a job boundary,
say, where the alternative is an unlabelled traceback:

```python
from eventsource.domain.exceptions import TenantContextNotSetError
```

### `load` does not require a scope, and that is deliberate

Only `save` enforces the context. `load`, `load_or_create`, `exists` and
`create_new` all work outside a scope:

```python
document = await repo.load(document_id)  # no scope needed
```

The asymmetry is a decision, not an oversight. `TenantAwareRepository` takes
an `enforce_on_load` flag and this project leaves it off, because what the
flag does is validate that *some* context exists without filtering the events
it reads by it. That would buy nothing here: a `Document`'s aggregate id is
`uuid5(tenant_id, source_id)` and a `ConsolidationLog`'s aggregate id *is* the
tenant, so a caller in the wrong tenant cannot name a stream belonging to
another one in the first place. Enforcement on load would add a failure mode
without removing one.

What it does mean is that the write path is where isolation is enforced, so
keep the scope around the whole unit of work anyway. A load outside the scope
and a save inside it is legal and reads as an accident.

For the aggregates these rules protect, see
[the aggregates reference](../reference/aggregates.md); for the events a
refused save would have appended, [the events
reference](../reference/events.md).

### An event whose `tenant_id` disagrees with the ambient scope raises `TenantMismatchError`

Every command takes `tenant_id` explicitly and stamps it on the event it
emits. If that value is not the one in scope, `save` refuses:

```python
document.record_extraction(tenant_id=other_tenant, source_id="doc-1", model_version=MODEL)
async with tenant_scope(tenant_id):
    await repo.save(document)  # TenantMismatchError — nothing is written
```

Like the missing-scope case, this is a refusal before any delegation: the
validation runs first and the underlying repository is never reached, so no
event lands. The aggregate keeps its uncommitted events, which means a
mismatch is recoverable — enter the right scope and save the same aggregate.

Validation walks *every* uncommitted event on the aggregate, so a batch in
which one event carries the wrong tenant is rejected whole rather than
partially written. The error names all of them:

```python
from eventsource.domain.exceptions import TenantMismatchError

try:
    await repo.save(document)
except TenantMismatchError as exc:
    exc.expected  # the tenant in scope
    exc.actual  # the first foreign tenant found
    exc.event_ids  # every uncommitted event that disagreed
```

`event_ids` is the whole mismatched set; `actual` is only the first foreign
tenant encountered, so a mixed batch reports one of several. Log `event_ids`
rather than `actual` when you want to know how much went wrong.

The check is what makes passing `tenant_id` to a command safe rather than a
second place for the tenant to be wrong. The argument and the scope must
agree, and the save is the point where they are compared — the command itself
does not consult the ambient context, so a wrong `tenant_id` builds a
perfectly valid event and fails only at the boundary.

The straightforward way to keep the two in agreement is to take the tenant
from the scope rather than from a separate variable. `tenant_scope` yields it:

```python
async with tenant_scope(tenant_id) as scoped_tenant:
    document.record_extraction(tenant_id=scoped_tenant, source_id="doc-1", model_version=MODEL)
    await repo.save(document)
```

That habit matters most where scopes nest. `tenant_scope` is re-entrant and
restores the outer tenant on exit, so an inner scope shifts the ambient tenant
under any code that closed over a `tenant_id` variable from further up — and
the failure surfaces as a mismatch at save, some distance from the nesting
that caused it.

### A foreign tenant inside a payload fails earlier, and differently

An event's `tenant_id` is not the only tenant in play. `DocumentExtracted`
carries `Entity` and `Relationship` payloads, each with its own `tenant_id`,
and the projection writes each payload under *its own* — so an event whose
tenant is right but whose entities are foreign would pass the repository check
and still write into a tenant that never emitted it.

The events reject that themselves, in a pydantic validator, so it raises
`ValueError` at event construction — inside the command call, before `save` is
reached and regardless of any scope:

```python
document.record_extraction(...)  # ValueError: entities carries tenants the event
# does not belong to: [...] != ...
```

Two different tenant checks, then, at two different moments: a `ValueError`
from `redstring.events` when a payload disagrees with its event, and a
`TenantMismatchError` from `eventsource` when an event disagrees with the
scope. Neither subsumes the other, and only the second is affected by where
your `async with` starts.

`TenantMismatchError` and `TenantContextNotSetError` both come from
`eventsource.domain.exceptions`; the merge invariant errors below come from
`redstring.domain.exceptions` and are a different family entirely. For which
events each command emits and what each carries, see
[the events reference](../reference/events.md); for why the streams are shaped
this way, [ADR 0001](../adr/0001-event-log-schema-and-granularity.md).

## Record an extraction run

### Call `record_extraction(...)`

`record_extraction` is keyword-only. `tenant_id`, `source_id` and
`model_version` are required; `entities` and `relationships` default to empty
sequences, so a run that found nothing is still recordable:

```python
async with tenant_scope(tenant_id) as scoped_tenant:
    document_id = document_stream(tenant_id=scoped_tenant, source_id="doc-1").aggregate_id
    document = await documents.load_or_create(document_id)
    event = document.record_extraction(
        tenant_id=scoped_tenant,
        source_id="doc-1",
        model_version="ollama/qwen3.6-27b",
        entities=entities,
        relationships=relationships,
    )
    if event is not None:
        await documents.save(document)
```

One call carries the *whole* run — every entity and every relationship the
extraction found, in one `DocumentExtracted`. There is no per-entity command,
and appending entities in batches by calling `record_extraction` repeatedly
under the same `model_version` does not work: the second call returns `None`.
Assemble the full result first, then record it once. For why the event is
coarse — the projection writes entities before the edges that connect them, so
an extraction is never partly applied — see
[ADR 0001](../adr/0001-event-log-schema-and-granularity.md).

`source_id` is a plain `str` (`SourceId`) and `tenant_id` a `UUID`
(`TenantId`); pass the same `source_id` you passed to `document_stream`, or
the event lands in a stream that disagrees with its own payload.

Two things `DocumentExtracted` validates at construction — inside the command
call, before any scope or save is involved:

- every entity **and** every relationship must carry the event's `tenant_id`
- every entity's `source_id` must equal the event's `source_id` (relationships
  carry no `source_id` and are not checked)

Both raise `ValueError` naming the offending values. The second is the one
that catches a genuine mix-up: entities pooled across documents and recorded
against one of them would otherwise be attributed to a document they did not
come from, and the projection writes each payload under its *own* tenant, so
nothing downstream could tell.

`record_extraction` mutates the aggregate and returns the event, but writes
nothing to any store. The event is applied to the aggregate immediately, so
the aggregate's `DocumentState.extraction_model_versions` already contains
`model_version` on return and a second call in the same block returns `None` — the idempotency
does not wait for `save`. Until `save`, the event sits in
`document.uncommitted_events` and the log has never heard of it.

### Handle the `None` return: this model version is already recorded, so save nothing and treat the retry as done

`record_extraction` returns `None` when this document has already recorded an
extraction under that `model_version`. Nothing is emitted, the aggregate is
untouched, and `document.uncommitted_events` is exactly what it was before the
call.

```python
event = document.record_extraction(
    tenant_id=scoped_tenant, source_id="doc-1", model_version=MODEL, entities=entities
)
if event is None:
    return  # already recorded; the retry is complete
await documents.save(document)
```

`None` is the *success* path of a retry, not a failure. A crash between the
append and your acknowledgement of it is the normal case in any pipeline that
retries, and the second attempt must not write the same ten thousand entities
again. That is why it is a return value rather than an exception: making the
caller catch an error to handle the expected outcome would push every call
site into a `try`/`except`, and a `try`/`except` written for the normal path
swallows the real failures alongside it.

Treat it as done and move on. Do not bump the model version to force the write
through, do not fall back to writing to a `GraphStore` or `VectorStore`
directly, and do not treat it as an error to retry again — the log already
holds a `DocumentExtracted` for that version, and the projection has either
applied it or will. See
[Drive projections from an event store](drive-projections-from-an-event-store.md)
if what you are actually missing is graph writes rather than log entries; the
two failure modes look identical from the caller's side and only one of them
is about this command.

The check is against *replayed* state, which is why the aggregate has to be
loaded rather than freshly constructed. On a `create_new` aggregate over a
stream that already has events, `record_extraction` sees an empty history,
happily returns an event, and the failure surfaces at `save` as an
`OptimisticLockError` — a concurrency error for what is really a retry. See
[Call commands against a freshly loaded aggregate](#call-commands-against-a-freshly-loaded-aggregate).

Idempotency is keyed on the model version and **not** on the payload. A re-run
of the same model can legitimately produce different output — decoding is not
deterministic — so comparing payloads would classify the retry as a *new*
extraction and write it, which is the double write being prevented. The
practical consequence is worth stating plainly: a second call under the same
`model_version` returns `None` **even when it found different entities**, and
those entities are discarded. If that is not what you want, the run needed a
different version string; see
[Bump `model_version` when a re-run is genuinely worth recording](#bump-model_version-when-a-re-run-is-genuinely-worth-recording).

Guarding the `save` is about intent rather than safety. Inside a
`tenant_scope`, saving an aggregate with no uncommitted events appends
nothing; *outside* one it still raises `TenantContextNotSetError`, because the
ambient tenant is read before the events are examined. So the `if` costs
nothing and says which branch you meant — and it is the line a reader checks
when asking whether a retry path can double-write.

For what `Document` holds after a replay, see
[the aggregates reference](../reference/aggregates.md); for the
`DocumentExtracted` payload that is not being written a second time, [the
events reference](../reference/events.md). For why one event carries a whole
run — the reason a suppressed repeat is all-or-nothing rather than a partial
append — see [ADR
0001](../adr/0001-event-log-schema-and-granularity.md).

### Record embeddings with `record_embeddings(...)` — a separate key space, same `None` contract

Embeddings are recorded on the same `Document` aggregate, by a second
keyword-only command:

```python
from redstring.domain.vector import VectorRecord

vectors = [
    VectorRecord(entity_id=entity.id, tenant_id=scoped_tenant, vector=embedding)
    for entity, embedding in zip(entities, embeddings, strict=True)
]

event = document.record_embeddings(
    tenant_id=scoped_tenant,
    source_id="doc-1",
    embedding_model="ollama/nomic-embed-text",
    embeddings=vectors,
)
if event is not None:
    await documents.save(document)
```

The shape is the one you already know: `tenant_id`, `source_id` and
`embedding_model` are required, `embeddings` defaults to empty, and the return
is an `EntitiesEmbedded` or `None` when this document has already been
embedded under that `embedding_model`. Everything the previous section says
about `None` applies unchanged — it is the success path of a retry, the check
runs against replayed state so the aggregate must be loaded, and the key is
the model name rather than the payload.

The payload is `VectorRecord`, which pairs an `entity_id` with a `vector` and
its own `tenant_id`. Note what that is *not*: it is not an `Entity`, and it
carries no `source_id`. So the two validators differ —
`EntitiesEmbedded` rejects a record whose `tenant_id` disagrees with the
event's, raising `ValueError` at construction exactly as `DocumentExtracted`
does, but there is no document-attribution check to run, because a vector
record does not claim a document. The `source_id` on the event places the
event in its stream; nothing inside the payload is compared against it.

### The two key spaces are separate, and the names really do collide

Extraction versions and embedding models are tracked in two different lists on
the aggregate — `DocumentState.extraction_model_versions` and
`DocumentState.embedding_models` — so recording an extraction under a name
does not suppress an embedding run under that same name:

```python
document.record_extraction(tenant_id=scoped_tenant, source_id="doc-1", model_version="shared-name")
document.record_embeddings(
    tenant_id=scoped_tenant, source_id="doc-1", embedding_model="shared-name"
)  # an EntitiesEmbedded, not None
```

This is worth more than a footnote because the namespaces genuinely overlap in
practice: `ollama/qwen3.6-27b` is a plausible value for both fields, and under
a shared key the embedding run would return `None` and be quietly dropped —
with the caller's `if event is not None` guard reading it as an already-done
retry. There would be no error to see.

The separation is also why bumping one version does nothing to the other. A
new extraction model does not make the existing embeddings re-recordable, and
re-embedding does not re-emit the entities.

### Why embedding is its own command

Embedding is a separate step against a separate model, and folding it into
`record_extraction` would tie the two together in the wrong direction:
re-embedding under a new model would have to re-emit every entity of the
document. `embedding_model` is on the event rather than implied by
configuration because a `VectorStore` holds vectors from exactly one model —
two models' vectors are not comparable even at equal dimension, so a consumer
has to be able to tell which model produced what it is reading.

Both events belong to the same document stream, so when you do record them in
one go, one `save` carries both:

```python
async with tenant_scope(tenant_id) as scoped_tenant:
    document = await documents.load_or_create(document_id)
    document.record_extraction(
        tenant_id=scoped_tenant,
        source_id="doc-1",
        model_version=MODEL,
        entities=entities,
        relationships=relationships,
    )
    document.record_embeddings(
        tenant_id=scoped_tenant,
        source_id="doc-1",
        embedding_model=EMBEDDING_MODEL,
        embeddings=vectors,
    )
    await documents.save(document)
```

That save is one append of two events, so a mismatched tenant on either one
rejects both — validation walks every uncommitted event. If you would rather
the extraction land even when the embedding step fails, save twice.

For the `EntitiesEmbedded` fields, see
[the events reference](../reference/events.md); for the state the two lists
live in, [the aggregates reference](../reference/aggregates.md). For what
turns an `EntitiesEmbedded` into `VectorStore` writes, see
[Drive projections from an event store](drive-projections-from-an-event-store.md).

### Bump `model_version` when a re-run is genuinely worth recording

The cost of keying on the version is that a genuine re-run under an unchanged
model cannot be recorded. That is intentional, and the remedy is to change the
version:

```python
document.record_extraction(
    tenant_id=scoped_tenant, source_id="doc-1", model_version="ollama/qwen3.6-27b@2"
)
```

A re-run worth recording is one where something about the extraction changed —
a new model, new weights, a changed prompt or schema, a fixed parser. All of
those are things the version string should name, because the version is what a
later reader has to reconstruct "what produced these entities" from. If
nothing changed, the run is not worth recording and `None` is the right
answer.

`model_version` is free-form text and redstring does not parse it, compare
it, or order it — `Document` only ever tests it for membership in
`DocumentState.extraction_model_versions`, the list of versions already seen.
So `"@2"`, `"2026-08-04-prompt-fix"` and `"qwen3.6-27b+schema-v3"` are all
equally valid; the scheme is yours. Two requirements it does have to meet:

- **Unique across the changes you care about.** Two genuinely different runs
  under one string means the second returns `None`.
- **Stable across retries of the *same* run.** A value that varies per
  attempt — a timestamp, a job id, a uuid — defeats the idempotency
  completely: every retry is a new version, and the crash-then-retry case the
  aggregate exists for writes the entities twice.

Bumping the version is safe in the way that matters, because entity ids do not
depend on it. `entity_id_for` derives an id from
`(tenant, source, entity type, normalized name)`, so the second run's version
of a person lands on the first run's entity and the projection upserts over
it. A re-run refines the graph rather than doubling it.

The same fact bounds what a re-run can express: since every write is an
upsert, a re-run that finds *fewer* entities than the last one leaves the
earlier ones in place. Bumping the version records the new run; nothing
retracts the old one. If a previous run's output has to go, that is a
consolidation or a store-level operation, not a re-extraction.

Embedding has the identical rule under a different field name — a re-embed
needs a new `embedding_model`, and because the two key spaces are separate, a
new `model_version` does not make the old `embedding_model` recordable again.

For the fields on `DocumentExtracted` and `EntitiesEmbedded`, see
[the events reference](../reference/events.md); for what `Document` holds
after a replay, [the aggregates reference](../reference/aggregates.md). For
what turns these events into graph and vector writes, see
[Drive projections from an event store](drive-projections-from-an-event-store.md).

## Merge and undo through the consolidation log

Merges go through `ConsolidationLog`, one aggregate per tenant. The shape is
the same as the document half — derive the id, load, command, save — but the
commands raise instead of returning `None`, because a refused merge is a
caller error rather than a retry that has already happened.

### Call `merge(...)`

`merge` is keyword-only. `tenant_id`, `canonical_entity_id` and
`merged_entity_ids` are required; `merge_reason` and `redirections` default to
`None` and empty:

```python
from redstring.aggregates import ConsolidationLog, consolidation_repository
from redstring.events.streams import consolidation_stream

consolidations = consolidation_repository(event_store, snapshot_store)

async with tenant_scope(tenant_id) as scoped_tenant:
    log_id = consolidation_stream(tenant_id=scoped_tenant).aggregate_id
    log = await consolidations.load_or_create(log_id)
    merged = log.merge(
        tenant_id=scoped_tenant,
        canonical_entity_id=canonical.id,
        merged_entity_ids=[duplicate.id],
        merge_reason="same person, two spellings",
        redirections=redirections,
    )
    await consolidations.save(log)

merge_event_id = merged.event_id  # keep this if you may want to undo
```

`merge` returns the `EntitiesMerged` it emitted and writes nothing to any
store. Keep `merged.event_id`: that is the handle `undo_merge` takes, and
there is no lookup by canonical id or by merged id that would find it for you.
If undo has to be possible from another process, persist that id somewhere you
control — the log holds it, but reading it back means scanning the tenant's
merge stream.

One call absorbs a whole batch. `merged_entity_ids` is a `Sequence[EntityId]`
and everything in it is absorbed by the one canonical entity, atomically:
either the event is emitted for all of them or one of the invariant errors
below is raised and nothing is emitted at all. Prefer one call over a loop —
merging duplicates one at a time gives you a partly-merged cluster if the
third call is the one that raises.

The event is applied to the aggregate as soon as it is created, so the
aggregate's own view updates before `save`. A second `merge` in the same block
that names an entity the first one absorbed raises `DoubleMergeError`
immediately; the invariants do not wait for the append.

### `redirections` is the undo, recorded in advance

`redirections` is the whole effect of the merge on the edge set — one
`RelationshipRedirection` per edge the merge moved or dropped:

```python
from redstring.domain.consolidation import RelationshipRedirection

RelationshipRedirection(
    before=edge, after=edge.model_copy(update={"source_entity_id": canonical.id})
)
RelationshipRedirection(before=edge)  # after defaults to None: the merge dropped it
```

`after=None` means **the edge was deleted**, not that nothing happened. It is
what happens when both endpoints were absorbed by the same merge: the moved
edge would be a self-loop, which `Relationship` rejects, so the merge drops it
rather than storing something the domain model forbids.

`RelationshipRedirection` validates that `after` is the *same edge moved*
rather than a different one — `after.id` must equal `before.id` and the two
must share a `tenant_id`, or construction raises `ValueError`. That is not
bookkeeping: the projection applies a redirection by upserting `after` over
the id it shares with `before`, so a differing id would create a second edge,
leave the original in place, and make the undo a no-op on half the change.

Record the redirections at merge time or you cannot undo faithfully. Nothing
else remembers them — reconstructing them later needs the pre-merge graph, and
the projection has already overwritten it. A merge saved with an empty
`redirections` is legal and appends cleanly; its undo simply restores no
edges, which is silent and wrong rather than an error you will be told about.

Computing them means reading the graph, which the aggregate cannot do — it
holds no store reference. If you want them derived for you rather than
assembled by hand, use the consolidation service rather than calling the
aggregate directly; see
[Consolidate duplicate entities](consolidate-duplicate-entities.md).

`merge_reason` is free-form text for a human reader. It rides on the event and
is not used by anything — not by the invariants, not by the projection, and it
is not kept in the aggregate's replayed `MergeRecord`.

### What the event rejects, before any scope or save

`EntitiesMerged` validates itself at construction, inside the `merge` call.
Each of these raises `ValueError` naming the offending values:

- `merged_entity_ids` must be non-empty — there is no merge of nothing
- the canonical id must not appear among the merged ids
- `merged_entity_ids` must contain no duplicates (the error lists them)
- every `redirection.before` must carry the event's `tenant_id`

Note the last one's scope: `before` is checked against the event's tenant, and
`after` is checked against `before` by `RelationshipRedirection` itself. The
two checks compose to cover both sides, and neither one alone does.

These are `ValueError`s from pydantic, and they are a different family from
the three below. The invariant errors are raised by the *aggregate*, from
replayed state, and they are the reason the merge lives in an aggregate at
all: no amount of validating one event in isolation can tell you whether the
entity it names was already merged last Tuesday.

### Catch `MergeIntoAliasError` — the canonical target has itself been merged away

```python
from redstring.domain.exceptions import MergeIntoAliasError

try:
    log.merge(tenant_id=scoped_tenant, canonical_entity_id=b, merged_entity_ids=[c])
except MergeIntoAliasError as exc:
    exc.alias_entity_id  # b — the target you asked to merge into
    exc.canonical_entity_id  # a — what b was itself merged into
```

Merge B into A, then try to merge C into B, and this fires. Allowing it would
leave C pointing at something that is not canonical, and nothing in
`GraphStore` resolves a chain — C's edges would simply land on the wrong
entity, quietly, with every write succeeding.

The check is one lookup of `canonical_entity_id` in the aggregate's `alias_of`
map, made before anything else in `merge`, and one hop is enough precisely
*because* this rule holds: an alias can never itself be canonical, so
`alias_of` is one level deep by construction and `exc.canonical_entity_id` is
the real canonical rather than the next link in a chain.

The remedy is therefore to retry against the id the error names:

```python
except MergeIntoAliasError as exc:
    log.merge(
        tenant_id=scoped_tenant,
        canonical_entity_id=exc.canonical_entity_id,
        merged_entity_ids=[c],
    )
```

Do not undo the first merge to make room for the second. A absorbing C is what
you meant — B and C being duplicates of each other, with B already absorbed by
A, means all three are the same thing.

Nothing is emitted when this raises. The refusal happens before the event is
constructed, so the aggregate is unchanged, `uncommitted_events` is untouched,
and the corrected call can be made on the same aggregate immediately — no
reload, no `save` of a half-done merge. The consolidation service is tested to
the same standard end to end: after a refused merge, the graph and the event
log are both byte-for-byte what they were.

Two things that are *not* this error, and are easy to mistake for it:

- **A canonical entity absorbing more entities later is legal.** A takes B,
  then A takes C, is the normal case. It is the *merged* side that may not
  repeat — that one is `DoubleMergeError`, below.
- **After an undo, B stops being an alias.** `undo_merge` clears the
  `alias_of` entries it recorded, so a merge into B then succeeds. That is
  what makes a bad merge correctable rather than merely recorded; see
  [Call `undo_merge(...)`](#call-undo_mergetenant_id-merge_event_id-the-restoration-is-derived-from-replayed-state).

The check runs against *replayed* state, so it only means anything on a loaded
aggregate. `merge` on a `create_new` `ConsolidationLog` over a tenant that has
merged before sees an empty `alias_of`, cannot raise, and emits an event that
`save` then rejects with `OptimisticLockError` — a concurrency error standing
in for a merge the log would have refused. See
[Call commands against a freshly loaded aggregate](#call-commands-against-a-freshly-loaded-aggregate).

For `alias_of` and `MergeRecord` — the state this check reads — see
[the aggregates reference](../reference/aggregates.md); for the alias surface
a projection maintains from these events, [ADR
0002](../adr/0002-two-store-ports.md) and
[Drive projections from an event store](drive-projections-from-an-event-store.md).

### Catch `DoubleMergeError` — an entity in the batch already has a canonical parent

```python
from redstring.domain.exceptions import DoubleMergeError

try:
    log.merge(tenant_id=scoped_tenant, canonical_entity_id=c, merged_entity_ids=[x, b, y])
except DoubleMergeError as exc:
    exc.entity_id  # b — the one already absorbed
    exc.canonical_entity_id  # a — what absorbed it
```

B absorbed by A and then by C would give B two canonical parents, and which
one won would depend on the order the projection happened to fold them in.
Nothing would fail: both merges write cleanly, and the graph ends up in
whichever state the fold reached last.

The check is a lookup of each merged id in the aggregate's `alias_of` map, and
it covers **every** element of `merged_entity_ids`, not just the first. A
batch is therefore rejected whole, and an already-merged id hidden behind
legal ones is still found — position makes no difference:

```python
log.merge(tenant_id=scoped_tenant, canonical_entity_id=c, merged_entity_ids=[b, d])
log.merge(tenant_id=scoped_tenant, canonical_entity_id=c, merged_entity_ids=[d, b])
# both raise, and both name b
```

`exc.entity_id` is the first already-merged id encountered in the batch, and
`exc.canonical_entity_id` is what absorbed it — the value `alias_of` held, so
it is the real canonical rather than a link in a chain (`MergeIntoAliasError`
is what keeps `alias_of` one level deep).

Nothing is emitted when this raises. The refusal happens before the event is
constructed, so the aggregate is unchanged, `uncommitted_events` is untouched,
and there is no partial merge to clean up — drop the offending id from the
batch and call the same aggregate again, or undo the earlier merge first if
that one was the mistake. The consolidation service is held to the same
standard end to end: after a refused merge, the graph and the event log are
both exactly what they were.

Which order the two invariants fire in is worth knowing when a call could
violate both: `merge` checks the canonical id against `alias_of` first, so a
call naming an alias as canonical *and* an already-merged entity in the batch
raises `MergeIntoAliasError`. Fixing that one can expose this one.

Two things that are *not* this error:

- **A canonical entity absorbing more entities later is legal**, and is the
  normal case: A takes B, then A takes C. It is the *merged* side that may
  not repeat, not the canonical one.
- **After an undo, the absorbed entity stops being an alias.** `undo_merge`
  clears the `alias_of` entries its merge recorded, so the same entity can be
  merged again, under a different canonical. An undo of an *unrelated* merge
  changes nothing here — the other merge's entities stay aliases and stay
  refused.

Like every invariant in this section the check runs against *replayed* state,
so it only means anything on a loaded aggregate. It also holds within a single
block: the event is applied to the aggregate as it is created, so a second
`merge` naming an entity the first one absorbed raises immediately, before any
`save`. On a `create_new` log over a tenant that has merged before, though,
`alias_of` is empty, the double merge is emitted, and `save` rejects it with
`OptimisticLockError` — a concurrency error standing in for a merge the log
would have refused. See
[Call commands against a freshly loaded aggregate](#call-commands-against-a-freshly-loaded-aggregate).

For `alias_of` and `MergeRecord` — the state this check reads — see
[the aggregates reference](../reference/aggregates.md); for the
`EntitiesMerged` payload that is not being written,
[the events reference](../reference/events.md). For the graph-aware wrapper
that computes redirections and raises the same error, see
[Consolidate duplicate entities](consolidate-duplicate-entities.md).

### Call `undo_merge(tenant_id=, merge_event_id=)`; the restoration is derived from replayed state

```python
async with tenant_scope(tenant_id) as scoped_tenant:
    log = await consolidations.load(log_id)
    undone = log.undo_merge(tenant_id=scoped_tenant, merge_event_id=merge_event_id)
    await consolidations.save(log)
```

Two arguments, both keyword-only, and neither of them describes what to
restore. You do not pass the relationships back: the aggregate replayed its
own history, so it knows what the merge displaced, and it writes that into the
`MergeUndone` — `canonical_entity_id`, `unmerged_entity_ids`, and
`restored_relationships` built from each redirection's `before`. A projection
handler applying the undo therefore needs no read of the log from inside its
fold.

That is also why the reload matters here more than anywhere else in this
guide. `undo_merge` on a `create_new` aggregate does not restore an empty set
— it raises `UnknownMergeError`, because the merge it is asked about is not in
the state it replayed.

`merge_event_id` is compared by value, so an id parsed from a request or a
database row works exactly as well as the `UUID` object the merge returned.

After the undo, the entities involved are no longer aliases. That is the
point: a bad merge is correctable, so the same entities can be merged again,
under a different canonical, and the canonical of the undone merge can itself
be absorbed by someone else.

### Catch `UnknownMergeError` — no merge in effect with that event id

```python
from redstring.domain.exceptions import UnknownMergeError

try:
    log.undo_merge(tenant_id=scoped_tenant, merge_event_id=merge_event_id)
except UnknownMergeError as exc:
    exc.merge_event_id
```

"In effect" is the operative phrase, and it covers two situations the type
deliberately does not distinguish: the merge never happened, and the merge
happened and has already been undone. From the caller's side they are one
case — there is no merge to reverse — and splitting them into two exceptions
would invite handling only one.

The already-undone half is the one that catches people. An undo applied twice
would restore edges that are already restored, and would emit a `MergeUndone`
for entities that are no longer aliases; the second call raising is what stops
that. So `UnknownMergeError` on a retry is usually the *safe* outcome, not a
bug — unlike `record_extraction`'s `None`, though, it is an exception, because
here the aggregate cannot tell "already applied" from "never existed".

The other frequent cause is an aggregate that was not loaded, or was loaded
before the merge was saved. Reload and try again before concluding the merge
is gone.

### All three derive from `ConsolidationInvariantError` if you want one handler

```python
from redstring.domain.exceptions import ConsolidationInvariantError

try:
    log.merge(...)
except ConsolidationInvariantError as exc:
    logger.warning("refused: %s", exc)
```

`MergeIntoAliasError`, `DoubleMergeError` and `UnknownMergeError` all derive
from `ConsolidationInvariantError`, which derives from `RedstringError` — so a
caller that already handles the library's base exception handles all three
without naming them, and a batch job that wants to skip a refused merge and
continue can catch the one type.

All three are **refusals to record a fact, not failures to write one**. They
are raised by the command, before any event exists, so the aggregate is
unchanged and there is nothing half-applied: no store was touched, no event
was appended, and the same aggregate can take the corrected call immediately.

They come from `redstring.domain.exceptions`, not from `eventsource`. Keep
them apart from `TenantMismatchError` and `OptimisticLockError` in your
handling — those two mean the write was rejected at the boundary and the
correct response is a scope fix or a reload, while these three mean the merge
you asked for is not one the graph can hold.

The reason these live in an aggregate rather than a service is worth knowing
before you consider re-checking them yourself. A service can evaluate all
three; what it cannot do is evaluate them against a consistent view of the
tenant's history *while holding the write lock that makes the check
meaningful*. The aggregate plus `ExpectedVersion` is what supplies that — see
[handle the `None` return](#handle-the-none-return-this-model-version-is-already-recorded-so-save-nothing-and-treat-the-retry-as-done)
— and it is why a
tenant's merges contend for one stream.

For `MergeRecord`, `alias_of` and what a replayed log holds, see
[the aggregates reference](../reference/aggregates.md); for the
`EntitiesMerged` and `MergeUndone` payloads,
[the events reference](../reference/events.md); for why the consolidation
stream is per tenant, [ADR 0001](../adr/0001-event-log-schema-and-granularity.md).
For what turns these events into graph writes, see
[Drive projections from an event store](drive-projections-from-an-event-store.md).
