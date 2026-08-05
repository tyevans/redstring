# Aggregates

The write model: two aggregates, their commands, the invariants they enforce,
and the repositories that load and save them. `Entity` is deliberately not an
aggregate — see
[`docs/adr/0001-event-log-schema-and-granularity.md`](../adr/0001-event-log-schema-and-granularity.md).

For the events these commands emit, see
[`docs/reference/events.md`](events.md). For a worked sequence, see
[`docs/how-to/use-the-write-model.md`](../how-to/use-the-write-model.md).

## Scope and import paths

### What is exported (`Document`) and what is dotted-path internal (`ConsolidationLog`, the repository factories)

`redstring.__all__` is the whole public promise. From this area it carries
exactly two names:

| Name | Public? | Import |
|---|---|---|
| `Document` | yes | `from redstring import Document` |
| `document_stream` | yes | `from redstring import document_stream` |
| `ConsolidationLog` | no | `redstring.aggregates.ConsolidationLog` |
| `document_repository`, `consolidation_repository` | no | `redstring.aggregates` |
| `consolidation_stream`, `DOCUMENT_CATEGORY`, `CONSOLIDATION_CATEGORY` | no | `redstring.events.streams` |
| `MergeRecord`, `DocumentState`, `ConsolidationLogState` | no | the defining module |
| `MergeIntoAliasError`, `DoubleMergeError`, `UnknownMergeError` | no | `redstring.domain.exceptions` |

Anything reached by a dotted path is internal and may change without notice.

## Stream keys

Defined in `redstring.events.streams`.

### `DOCUMENT_CATEGORY` and `CONSOLIDATION_CATEGORY`

```python
DOCUMENT_CATEGORY = "Document"
CONSOLIDATION_CATEGORY = "Consolidation"
```

Each is both the `StreamId.category` and the aggregate class's
`aggregate_type`.

### `document_stream(*, tenant_id, source_id) -> StreamId`

```python
document_stream(*, tenant_id: TenantId, source_id: SourceId) -> StreamId
```

Returns `StreamId(aggregate_id=uuid5(tenant_id, source_id),
category=DOCUMENT_CATEGORY)`.

The tenant is the `uuid5` **namespace**, not part of the hashed name. That
keeps the two halves of the key structurally separate: a scheme that
concatenated them before hashing would map `("t", "ab")` and `("ta", "b")` onto
one stream, and `SourceId` is free-form text, so nothing else would stop it.
The same URL ingested by two tenants is therefore two streams.

Deriving the id rather than storing a mapping means there is no table to keep
consistent and no lookup on the write path. It is deterministic, so
re-extracting a document appends to the stream it already has instead of
starting a new one.

Raises `ValueError` when `source_id` is empty or whitespace-only — the check is
`not source_id.strip()`, so `""` and `"   "` are both rejected, and the message
mentions `source_id`. `SourceDocument.id` carries no validation of its own, so
this is the last point at which a blank one can be caught; hashed instead, it
would yield a valid-looking stream shared by every document that had one.

### `consolidation_stream(*, tenant_id) -> StreamId`

```python
consolidation_stream(*, tenant_id: TenantId) -> StreamId
```

Returns `StreamId(aggregate_id=tenant_id, category=CONSOLIDATION_CATEGORY)`.

The aggregate id **is** the `tenant_id`, passed through unmodified — no
`uuid5`, no derivation. Both are already `UUID`s, so no bridge is needed, and
there is exactly one consolidation log per tenant: any further mapping would
be a fiction with no second value to distinguish. `ConsolidationLog` declares
the same thing from the other side, `aggregate_type = CONSOLIDATION_CATEGORY`.

One stream per tenant is a deliberate serialisation point, not an accident of
the key. Merges span documents, and two concurrent merges touching the same
entities must not interleave — sharing one stream puts them behind the same
optimistic-concurrency check, which is what makes `MergeIntoAliasError` and
`DoubleMergeError` enforceable rather than advisory. The cost is an unbounded
stream, which is why `consolidation_repository` requires a snapshot store.

Unlike `document_stream` this raises nothing: there is no free-form input to
reject.

Callers that need the aggregate id for a repository `load` or `create_new` use
`consolidation_stream(tenant_id=tenant_id).aggregate_id` rather than passing
the tenant id directly — the two are equal today, and going through the
function is what keeps them equal if that ever stops being true.

## `Document`

`redstring.aggregates.document.Document`, an
`AggregateRoot[DocumentState]` with `aggregate_type = DOCUMENT_CATEGORY`.
Construct it with the aggregate id from `document_stream`:

```python
from redstring import Document, document_stream

stream = document_stream(tenant_id=tenant_id, source_id="doc-1")
document = Document(stream.aggregate_id)
```

One stream per document — short, and parallel across documents, so extraction
keeps the concurrency it already has and ordering applies only where one
document's own runs need it. There is no snapshot policy (see
`document_repository` below).

Two commands, `record_extraction` and `record_embeddings`. Both are
keyword-only, both return their event or `None`, and both enforce the single
rule this aggregate owns: **a run is recorded at most once per model.**

### `DocumentState` fields

| Field | Type | Default |
|---|---|---|
| `extraction_model_versions` | `list[str]` | `[]` |
| `embedding_models` | `list[str]` | `[]` |

A pydantic `BaseModel` with two fields, both `Field(default_factory=list)`, so
a `Document` with no history starts from two empty lists rather than a shared
one.

`list` rather than `set` because state is snapshotted through
`model_dump(mode="json")` and JSON has no representation for a set. Membership
is what both fields are for; order is incidental — nothing reads a position,
and both commands test with `in`.

The only writer is `_apply`, which appends `event.model_version` on a
`DocumentExtracted` and `event.embedding_model` on an `EntitiesEmbedded`. So
the two lists are exactly the models this document has been through, in the
order the events were applied, and they are rebuilt the same way whether the
aggregate emitted the events in this process or replayed them from the log.

The two fields are the whole state. The idempotency check reads
`extraction_model_versions` for extraction and `embedding_models` for
embeddings, which is why the two key spaces are separate (see *Idempotency
keys* below); nothing about the extracted entities, relationships or vectors
is kept here — those live in the events, and projections read them.

### `record_extraction(...) -> DocumentExtracted | None`

```python
record_extraction(
    *,
    tenant_id: TenantId,
    source_id: SourceId,
    model_version: str,
    entities: Sequence[Entity] = (),
    relationships: Sequence[Relationship] = (),
) -> DocumentExtracted | None
```

Records what one extraction run found. Keyword-only; `entities` and
`relationships` both default to the empty tuple, so a run that found nothing
is still recordable and still consumes the model version.

| Parameter | Type | Notes |
|---|---|---|
| `tenant_id` | `TenantId` | carried onto the event; every payload must agree with it |
| `source_id` | `SourceId` | the document; every entity must agree with it |
| `model_version` | `str` | the idempotency key |
| `entities` | `Sequence[Entity]` | copied with `list(...)` |
| `relationships` | `Sequence[Relationship]` | copied with `list(...)` |

**Returns `None` and emits nothing** when `model_version` is already in
`state.extraction_model_versions`. The check is a plain `in` against replayed
state, so it holds after `load_from_history` just as it does within one
instance — which is the only case that matters, since every real retry loads
from the log first. A document with no history accepts any model version.

Otherwise it calls `create_event(DocumentExtracted, ...)`, which appends to
the aggregate's uncommitted events and returns the event. Nothing is
persisted until the repository saves; `record_extraction` writes to no store.

`entities` and `relationships` are copied into lists on the way in. The caller
may pass any `Sequence` — a generator materialised elsewhere, a list it goes
on mutating — and the event holds a snapshot taken at the call.

Applying the event appends `model_version` to
`state.extraction_model_versions`, which is what makes the second call a
repeat.

**A repeat is dropped even when the run found something different.** The
idempotency key is the model version, never the payload; see *Idempotency
keys* below for why, and what it costs.

`DocumentExtracted` validates its own payload, so the following raise
`ValueError` from the event constructor rather than returning `None`:

| Rejected | Why |
|---|---|
| an entity or relationship whose `tenant_id` differs from the event's | the projection writes each payload under *its own* `tenant_id`, so a foreign one would land silently in a tenant that never emitted it — this is the last place the two can be compared |
| an entity whose `source_id` differs from the document's | provenance: an entity attributed to a document it was not extracted from |

Both are refusals to record something untrue, and are the opposite case from
the `None` return — see *`None` return vs. raising*.

### `record_embeddings(...) -> EntitiesEmbedded | None`

```python
record_embeddings(
    *,
    tenant_id: TenantId,
    source_id: SourceId,
    embedding_model: str,
    embeddings: Sequence[VectorRecord] = (),
) -> EntitiesEmbedded | None
```

Records the vectors one embedding run produced for this document's entities.
Keyword-only; `embeddings` defaults to the empty tuple.

| Parameter | Type | Notes |
|---|---|---|
| `tenant_id` | `TenantId` | carried onto the event; every `VectorRecord` must agree with it |
| `source_id` | `SourceId` | the document |
| `embedding_model` | `str` | the idempotency key |
| `embeddings` | `Sequence[VectorRecord]` | copied with `list(...)` |

**Returns `None` and emits nothing** when `embedding_model` is already in
`state.embedding_models` — a plain `in` against replayed state, so it holds
after `load_from_history` exactly as it does within one instance. Otherwise it
calls `create_event(EntitiesEmbedded, ...)`, which appends to the aggregate's
uncommitted events and returns the event; nothing is persisted until the
repository saves. Applying the event appends `embedding_model` to
`state.embedding_models`, which is what makes the second call a repeat.

`embeddings` is copied into a list on the way in, so the caller may pass any
`Sequence` and go on mutating its own; the event holds a snapshot taken at the
call. A run under a *different* model is a new run and emits normally —
re-embedding is how a document moves onto a better model.

`EntitiesEmbedded` validates its own payload: a `VectorRecord` whose
`tenant_id` differs from the event's raises `ValueError` from the constructor
rather than returning `None`, for the same reason as extraction — the
projection writes each record under *its own* `tenant_id`, so a foreign one
would land silently in a tenant that never emitted it.

There is no `source_id` check on the payload here, unlike `record_extraction`:
a `VectorRecord` carries `entity_id`, `tenant_id` and `vector` and no source
attribution, so there is nothing to compare. `source_id` on this command
identifies the document whose stream the event joins, not a property of the
vectors.

Embedding is a separate event rather than a field on `DocumentExtracted`
because it runs against a separate model, and re-embedding under a new model
must not re-emit the entities. `embedding_model` is on the event rather than
implied because a `VectorStore` holds vectors from exactly one model, and two
models' vectors are not comparable even at equal dimension.

Its key space is separate from extraction's — see *Idempotency keys* below.

### Idempotency keys

| Command | Key | Checked against |
|---|---|---|
| `record_extraction` | `model_version` | `state.extraction_model_versions` |
| `record_embeddings` | `embedding_model` | `state.embedding_models` |

This is the one rule `Document` owns: **a run is recorded at most once per
model.** A crash between the store's `append` and the caller's acknowledgement
is the normal case, and the retry must not write the same ten thousand
entities a second time. It is also why `model_version` is on the event at all:
without it two runs are indistinguishable and the aggregate has nothing to be
idempotent *on*.

**The key is the model, never the payload.** A re-run of the same model can
legitimately produce different output — decoding is not deterministic — so
comparing payloads would classify the retry as a new extraction and write it,
which is exactly the double write the rule prevents. The consequence is worth
stating plainly: a repeat is dropped **even when it found something
different**. Extract `"Ada"` under `m`, then extract `"Grace"` under `m`, and
the second call returns `None`; the document's recorded state is `"Ada"`.

The cost is that a genuine re-run under an unchanged version cannot be
recorded. Bump the version — a re-run worth recording implies something about
the model changed, and that is what a version is for.

#### Separate key spaces

The two keys do not share a namespace. An extraction under `"shared-name"`
does not suppress an embedding run under `"shared-name"`:

```python
document.record_extraction(..., model_version="shared-name")  # emits
document.record_embeddings(..., embedding_model="shared-name")  # also emits
```

The overlap is real rather than hypothetical — `ollama/qwen3.6-27b` is a
plausible name for either kind of run — so one shared list would silently
swallow the second. Two fields on `DocumentState` is what keeps the spaces
apart; there is no composite key and no prefixing.

Conversely, within a key space a *different* model is always a new run:
extracting under `ollama/qwen4-30b` after `ollama/qwen3.6-27b` emits, and
re-embedding under `openai/text-embedding-3-small` emits. That is how a
document moves onto a better model.

#### The check runs against replayed state

Both checks read `self._current`, the state rebuilt by `_apply` from applied
events — not a flag set by the emitting call. So the rule survives
rehydration, which is the only case that matters: every real retry runs
against an aggregate the repository loaded from the log, not against the
instance that emitted the first event.

A document with no history at all — `version == 0`, no snapshot, no events —
has two empty lists and accepts any model.


### `None` return vs. raising — what a repeat means for the caller

A repeat is the **expected** outcome of a retry, not an error, so both commands
return `None` rather than raising. Raising would push every caller into a
`try`/`except` around the *normal* path — a block that then swallows real
failures alongside the benign repeat, which is the opposite of what an
exception is for.

So the two outcomes are different in kind:

| Outcome | Means | Signalled by |
|---|---|---|
| an event | this run is new; it is now uncommitted on the aggregate | the return value |
| `None` | this model's run is already recorded; nothing was emitted | the return value |
| `ValueError` | the caller asked to record something untrue (foreign `tenant_id`, stray `source_id`) | raised from the event constructor |

#### What a caller does with `None`

Nothing, in the simple case. `None` means no event was created, so the
aggregate has no uncommitted events and `repository.save(document)` is a
documented no-op — the retry ends where it started, which is the point. A
caller that ignores the return value is *correct*; it simply has nothing new
to persist.

A caller that does something downstream with the event has to branch, because
there is no event to hand on:

```python
event = document.record_extraction(
    tenant_id=tenant_id, source_id="doc-1", model_version=MODEL, entities=entities
)
if event is not None:
    await repository.save(document)
    await projection.handle(event)
```

The type is `DocumentExtracted | None`, so under `mypy --strict` an unchecked
attribute access on the result does not type-check. The `None` is not a
convention to remember; it is in the signature.

This is why the return value is threaded through the library rather than
discarded. `ExtractionPipeline.record` returns the aggregate's result
unchanged, and `GraphBuildReport.event` is `DocumentExtracted | None` for the
same reason — `None` there means *nothing was recorded*, and the field
documents it rather than leaving the caller to infer it from an empty store.

#### What `None` does not mean

- **Not "the run failed."** Extraction succeeded; the aggregate declined to
  record it a second time. A failure raises, from the pipeline or the
  provider, and never reaches the command.
- **Not "the payload was identical."** The key is the model, never the
  payload, so a repeat is dropped even when it found something different (see
  *Idempotency keys*). If that matters, the version must change.
- **Not "nothing was spent."** The model ran; only the write was suppressed.
  A caller wanting to avoid the *cost* has to check before extracting, not
  after — `build_graph` does not, and its docstring says so.

#### Why the invalid cases raise instead

`DocumentExtracted` and `EntitiesEmbedded` raise `ValueError` for a payload
carrying a foreign `tenant_id`, and `DocumentExtracted` for an entity whose
`source_id` is not this document's. Returning `None` there would be
indistinguishable from a benign repeat, and the caller would drop a
cross-tenant write on the floor with no signal. A repeat is a request already
satisfied; these are requests that must not be satisfied at all. Same
distinction on `ConsolidationLog`, which returns no `None` at all: every
refusal it makes is an invariant violation, so all three of its failures are
exceptions.

## `ConsolidationLog`

`redstring.aggregates.consolidation_log.ConsolidationLog`, an
`AggregateRoot[ConsolidationLogState]` with
`aggregate_type = CONSOLIDATION_CATEGORY`. Not exported; construct it with the
aggregate id from `consolidation_stream`:

```python
from redstring.aggregates import ConsolidationLog
from redstring.events.streams import consolidation_stream

log = ConsolidationLog(consolidation_stream(tenant_id=tenant_id).aggregate_id)
```

One tenant's whole merge history, in one stream. The aggregate id **is** the
tenant, so a tenant's merges are serialised behind a single optimistic
concurrency check — two concurrent merges touching the same entities cannot
interleave. That is the point of the boundary rather than a side effect of it:
consolidation genuinely spans documents, so there is no narrower aggregate
that still sees the conflicts.

Two commands, `merge` and `undo_merge`. Both are keyword-only and both return
an event — unlike `Document`, neither ever returns `None`. Every refusal this
aggregate makes is an invariant violation, so all of them are exceptions.

Three rules, and each corrupts a graph *silently* rather than loudly:

| Rule | Enforced by | What breaks without it |
|---|---|---|
| No merging into an alias | `MergeIntoAliasError` | B into A, then C into B, leaves C pointing at something that is not canonical, and nothing in `GraphStore` resolves a chain |
| No merging an entity twice | `DoubleMergeError` | B absorbed by A and then by C has two canonical parents, and which wins depends on the order the projection folded them in |
| An undo must reference a merge in effect | `UnknownMergeError` | edges are restored that were never displaced — a pre-merge graph written over a graph that was never merged |

A service could check all three. What it could not do is check them against a
consistent view of the tenant's history *while holding the write lock that
makes the check meaningful*, which is what an aggregate plus `ExpectedVersion`
gives. Hence the rules live here.

The cost is a stream that grows with a tenant's merge history rather than with
anything bounded, which is why `consolidation_repository` requires a snapshot
store (see *Repositories* below).

### `MergeRecord` fields

One merge as the log remembers it. A pydantic `BaseModel`, internal
(`redstring.aggregates.consolidation_log.MergeRecord`), and never constructed
by a caller: the only writer is `_apply`, from an applied `EntitiesMerged`.

| Field | Type | Default | Source on `EntitiesMerged` |
|---|---|---|---|
| `merge_event_id` | `UUID` | — | `event.event_id` |
| `canonical_entity_id` | `EntityId` | — | `event.canonical_entity_id` |
| `merged_entity_ids` | `list[EntityId]` | — | `list(event.merged_entity_ids)` |
| `redirections` | `list[RelationshipRedirection]` | `[]` | `list(event.redirections)` |
| `undone` | `bool` | `False` | not carried — set by `MergeUndone` |

#### `merge_event_id`

The `event_id` of the `EntitiesMerged` that produced the record, not an id the
record mints. That is what makes it the handle a caller passes back to
`undo_merge`: the caller has the event, and the event identifies its own
merge.

`_merge_in_effect` looks it up with `==`, not `is`. A real caller's id was
parsed from a request body or a database row, so it is a *different `UUID`
object* carrying the same value — identity comparison would find nothing and
every undo would raise `UnknownMergeError`.

#### `canonical_entity_id` and `merged_entity_ids`

The merge as decided: one survivor, and the entities absorbed into it.
`merged_entity_ids` has at least one element (`EntitiesMerged` declares
`min_length=1`), never contains the canonical id, and never repeats one — all
three checked on the event, so no `MergeRecord` can exist violating them.

The list is copied out of the event on apply, so later mutation of the event's
list cannot reach recorded state. Its order is **not meaningful**; it is
preserved only because a list preserves it (see *Which fields have meaningful
order*).

#### `redirections`

Every edge the merge moved onto the canonical entity, and every edge it
dropped because both endpoints were absorbed — each a
`RelationshipRedirection` with `before` and an `after` that is `None` when the
edge was dropped.

Kept, rather than recomputed at undo time, because **nothing else can
reconstruct it**: deriving it would need the pre-merge graph, which the
projection overwrote the moment it applied the merge. This is the field that
makes an undo a restoration rather than a note; `undo_merge` reads
`[r.before for r in record.redirections]` straight onto `MergeUndone`.

#### `undone`

`False` on every record `_apply_merged` writes. `_apply_undone` sets it `True`
on every record whose `merge_event_id` matches, and `_merge_in_effect` skips
any record where it is `True`.

It is a **flag, not a deletion**: the record stays in `merges`, so the history
remains append-only and a merge that was made and reversed is still visible.
The pair of it and `merge_event_id` is what makes a second undo of the same
merge raise `UnknownMergeError` rather than emit a second `MergeUndone`.

Being ordinary state, `undone` survives a snapshot round trip like everything
else — an undo replayed from JSON marks its merge undone exactly as an
in-process one does.

### `ConsolidationLogState` fields

The whole state the invariants are checked against — a pydantic `BaseModel`
with two fields, both `Field(default_factory=list/dict)`, so a log with no
history starts from its own empty collections rather than shared ones.

| Field | Type | Default | Written by |
|---|---|---|---|
| `merges` | `list[MergeRecord]` | `[]` | `_apply_merged` appends; `_apply_undone` flips `undone` |
| `alias_of` | `dict[EntityId, EntityId]` | `{}` | `_apply_merged` sets; `_apply_undone` pops |

#### `merges`

Every merge the tenant has ever recorded, in the order the events were
applied, undone ones included — see *`MergeRecord` fields* above. It is
append-only: an undo marks, it does not remove. `_merge_in_effect` scans this
list, so its **order is meaningful** in the sense that the scan returns the
first matching record; nothing else reads a position.

#### `alias_of`

Maps each absorbed entity to the canonical entity that absorbed it —
`alias_of[b] = a` after merging `b` into `a`. Membership is the whole
question both of `merge`'s guards ask: `canonical_entity_id` in the map means
`MergeIntoAliasError`, any element of `merged_entity_ids` in the map means
`DoubleMergeError`. `.get(...)` is used rather than `in`, so the raised error
can name the canonical parent the entity already has.

It is **derived from `merges`** and could be recomputed on every command. It
is materialised because the "is this an alias" check runs once per entity per
merge, and a tenant's merge list is unbounded — recomputing would make each
merge cost the whole history. That is a performance decision, not a semantic
one: the two are the same information, and a snapshot carries both rather
than rebuilding one from the other.

An undo `pop`s each unmerged entity from the map (with a default, so a
redelivered `MergeUndone` finding the entry already gone is idempotent rather
than an error). That is what makes a bad merge *correctable*: the entity stops
being an alias and can be merged somewhere else. The `MergeRecord` stays,
`undone = True`; the map entry does not. So the two fields deliberately
disagree about a reversed merge — `merges` remembers it happened, `alias_of`
describes only what is in effect now.

Undoing one merge touches only that merge's entries. Another tenant merge left
alone keeps its alias, and re-merging its absorbed entity is still refused.

#### Why both are `list`/`dict` and never `set`

State is snapshotted through `model_dump(mode="json")` and a `set` has no JSON
form. See *State serialisation contract* below; `EntityId` is a `UUID`, and
pydantic serialises the dict keys to strings and parses them back on load, so
a replayed `alias_of` compares equal to one built in process.

---

### `merge(...) -> EntitiesMerged`

```python
merge(
    *,
    tenant_id: TenantId,
    canonical_entity_id: EntityId,
    merged_entity_ids: Sequence[EntityId],
    merge_reason: str | None = None,
    redirections: Sequence[RelationshipRedirection] = (),
) -> EntitiesMerged
```

Records that `merged_entity_ids` were absorbed into `canonical_entity_id`.
Keyword-only. Always returns an `EntitiesMerged` — there is no `None` path, so
every refusal is an exception.

| Parameter | Type | Notes |
|---|---|---|
| `tenant_id` | `TenantId` | carried onto the event; every redirection must agree with it |
| `canonical_entity_id` | `EntityId` | the survivor; must not itself be an alias |
| `merged_entity_ids` | `Sequence[EntityId]` | at least one, none already an alias; copied with `list(...)` |
| `merge_reason` | `str \| None` | free text, default `None`; unvalidated, unread by the aggregate |
| `redirections` | `Sequence[RelationshipRedirection]` | the edge effect, default `()`; copied with `list(...)` |

Two guards run before anything is created, in this order:

1. `state.alias_of.get(canonical_entity_id)` — not `None` raises
   `MergeIntoAliasError`.
2. the same lookup for **every** element of `merged_entity_ids` — not `None`
   raises `DoubleMergeError`.

`.get` rather than `in` so the error can name the canonical parent the entity
already has. The batch loop checks every element rather than stopping at the
first legal one: a merge absorbs a list, and an offender in any position is
found.

Otherwise it calls `create_event(EntitiesMerged, ...)`, which appends to the
aggregate's uncommitted events and returns the event. Nothing is persisted
until the repository saves; `merge` writes to no store and reads no graph.

Applying the event appends a `MergeRecord` — built from `event.event_id`, so
the merge's handle for `undo_merge` is the event's own id — and sets
`alias_of[e] = canonical_entity_id` for every `e` in `merged_entity_ids`. The
sequences are copied on the way in and again on apply, so a caller that keeps
mutating its list cannot reach recorded state.

Being canonical is not being merged: a second merge into the same
`canonical_entity_id` is the normal case and emits normally. Only the *merged*
side is one-shot.

`EntitiesMerged` validates its own payload, so these raise `ValueError` from
the constructor rather than from a guard: an empty `merged_entity_ids`
(`min_length=1`), the canonical id appearing among the merged ids, a duplicate
merged id (reported by *which* id repeated), and a redirection whose
`before.tenant_id` differs from the event's.

The guards check the tenant's replayed history, so they hold after a load from
the log exactly as they do within one instance — which is the case that
matters, since a real second merge runs against an aggregate the repository
rehydrated. That the check is meaningful at all is the per-tenant stream:
`ExpectedVersion` on save is what stops two concurrent merges each passing a
guard against a state the other invalidated.

### Exceptions raised by `merge` — `MergeIntoAliasError`, `DoubleMergeError`

Both are raised by `ConsolidationLog.merge`, both subclass
`ConsolidationInvariantError` → `RedstringError`, both take keyword-only
constructor arguments, and both are internal
(`redstring.domain.exceptions`). `merge` has no `None` path, so these are the
only two ways it declines.

| Exception | Raised when | Attributes |
|---|---|---|
| `MergeIntoAliasError` | `canonical_entity_id` is itself a key in `state.alias_of` | `alias_entity_id`, `canonical_entity_id` |
| `DoubleMergeError` | **any** element of `merged_entity_ids` is a key in `state.alias_of` | `entity_id`, `canonical_entity_id` |

The guards run in that order, before `create_event`, so a refused merge creates
no event, appends nothing to `uncommitted_events`, and leaves state untouched
— "raises" and "did not happen" are the same thing here, end to end: the
round-trip suite asserts a refused merge leaves both the graph snapshot and the
event count exactly as they were.

#### `MergeIntoAliasError`

Merging into an entity that has itself been absorbed. Merge `b` into `a`, then
attempt `c` into `b`:

```python
log.merge(tenant_id=t, canonical_entity_id=a, merged_entity_ids=[b])
log.merge(tenant_id=t, canonical_entity_id=b, merged_entity_ids=[c])
# MergeIntoAliasError: cannot merge into <b>: it is already an alias of <a>
```

`excinfo.value.alias_entity_id` is `b` — the id the caller offered as
canonical — and `canonical_entity_id` is `a`, the parent it already has. That
second attribute is why the guard is `alias_of.get(...) is not None` rather
than `in`: the lookup's *value* is what tells the caller where the entity
actually went, so a retry can name `a` without re-reading the log.

Without it, `c` ends up pointing at something that is not canonical, and
nothing in `GraphStore` resolves a chain — the edges sit on a non-canonical
entity and every read of `a` misses them.

**Being canonical is not being merged.** A second merge into the same
`canonical_entity_id` is the normal case and emits normally; only an id in
`alias_of` — the absorbed side — is refused.

#### `DoubleMergeError`

An entity already absorbed being absorbed again. `b` into `a`, then `b` into
`c`:

```python
log.merge(tenant_id=t, canonical_entity_id=a, merged_entity_ids=[b])
log.merge(tenant_id=t, canonical_entity_id=c, merged_entity_ids=[b])
# DoubleMergeError: entity <b> has already been merged into <a>
```

`entity_id` is the offending element, `canonical_entity_id` the parent it
already has. Without the guard `b` has two canonical parents, and which one
wins depends on the order the projection folded the two events in.

**Every element of the batch is checked, not just the first.** A merge absorbs
a list, and an offender behind a legal id is found:

```python
log.merge(tenant_id=t, canonical_entity_id=c, merged_entity_ids=[d, b])
# DoubleMergeError naming b, not d
```

The loop raises on the first offender it reaches, so with several already-merged
ids in one batch the error names the earliest by position.

#### Both hold after replay

The guards read `self._current` — state rebuilt by `_apply` from applied
events — not a flag the emitting call set. So a `ConsolidationLog` the
repository rehydrated from the log (or from a snapshot plus a tail, through
JSON) refuses exactly what the emitting instance would have. That is the case
that matters: a real second merge never runs against the instance that made
the first.

An `undo_merge` `pop`s the freed entity from `alias_of`, so `DoubleMergeError`
stops applying to it and it can be merged elsewhere — that is what makes a bad
merge correctable. Undoing one merge frees only that merge's entities; another
merge's absorbed entity keeps its alias and re-merging it still raises.

#### Validation that lives on the event instead

`EntitiesMerged` checks its own payload and raises `ValueError`, not a
`ConsolidationInvariantError`, for: an empty `merged_entity_ids`
(`min_length=1`), the canonical id appearing among the merged ids, a duplicate
merged id (the message names which id repeated), and a redirection whose
`before.tenant_id` differs from the event's. These are malformed events rather
than violated history, so they belong to the event type and fire for any
producer of one — see
[`docs/reference/events.md`](events.md).

### `undo_merge(*, tenant_id, merge_event_id) -> MergeUndone`

```python
undo_merge(*, tenant_id: TenantId, merge_event_id: UUID) -> MergeUndone
```

Reverses the merge that `merge_event_id` recorded. Keyword-only, and the
caller supplies **nothing but those two arguments** — everything the undo
restores is read out of replayed state.

| Parameter | Type | Notes |
|---|---|---|
| `tenant_id` | `TenantId` | carried onto the event; every restored relationship must agree with it |
| `merge_event_id` | `UUID` | the `event_id` of the `EntitiesMerged` being reversed |

`_merge_in_effect` scans `state.merges` and returns the first record whose
`merge_event_id` **equals** the argument *and* whose `undone` is `False`;
`UnknownMergeError` if there is none. The comparison is `==`, not `is`: a real
caller's id was parsed from a request body or a database row, so it is a
different `UUID` object with the same value.

Otherwise it calls `create_event(MergeUndone, ...)`, filling the payload from
the record — see *Fields carried on `MergeUndone`* below. Nothing is persisted
until the repository saves; `undo_merge` writes to no store and reads no
graph.

Applying the event does two things:

- marks `undone = True` on **every** record whose `merge_event_id` matches
  (`==`, again — a `<=` there would mark every merge sorting at or below the
  named one);
- `pop`s each id in `unmerged_entity_ids` from `alias_of`, with a default, so
  a redelivered `MergeUndone` finding the entry already gone is idempotent
  rather than an error.

The two halves deliberately disagree: `merges` still remembers the merge
happened, `alias_of` no longer says it is in effect. The history stays
append-only and a reversed merge remains visible.

Clearing the alias is the point of the command. Both sides are freed — the
absorbed entity can be merged elsewhere, and the canonical entity can itself
be absorbed:

```python
log.merge(tenant_id=t, canonical_entity_id=a, merged_entity_ids=[b])
log.undo_merge(tenant_id=t, merge_event_id=log.uncommitted_events[0].event_id)
log.merge(tenant_id=t, canonical_entity_id=c, merged_entity_ids=[b])  # emits
```

An undo frees only the entities of the merge it names. Another merge's
absorbed entity keeps its alias and re-merging it still raises
`DoubleMergeError`.

There is no `None` path and no second-undo path: once the record is `undone`,
`_merge_in_effect` skips it, so a repeated call raises `UnknownMergeError`
rather than emitting a second `MergeUndone` whose entities are no longer
aliases.

### Exceptions raised by `undo_merge` — `UnknownMergeError`

`undo_merge` has no `None` path and exactly one failure. It is internal
(`redstring.domain.exceptions.UnknownMergeError`), subclasses
`ConsolidationInvariantError` → `RedstringError`, and takes one keyword-only
argument.

| Exception | Raised when | Attributes | Message |
|---|---|---|---|
| `UnknownMergeError` | `_merge_in_effect` finds no `MergeRecord` whose `merge_event_id` equals the argument *and* whose `undone` is `False` | `merge_event_id` | `no merge in effect with event id <uuid>` |

It is raised from the scan, before `create_event`, so a refused undo emits
nothing and leaves state untouched — "raises" and "did not happen" are the
same thing here.

#### "Never happened" and "already undone" are one case

The type deliberately does not distinguish them. From the caller's side there
is nothing to reverse either way, and separating them would invite handling
only one:

```python
log.undo_merge(tenant_id=t, merge_event_id=uuid4())  # never happened
log.merge(tenant_id=t, canonical_entity_id=a, merged_entity_ids=[b])
mid = log.uncommitted_events[0].event_id
log.undo_merge(tenant_id=t, merge_event_id=mid)  # emits
log.undo_merge(tenant_id=t, merge_event_id=mid)  # already undone
```

The first and last calls both raise `UnknownMergeError`. A log replayed from
genuinely nothing — `version == 0`, no events, no snapshot — is the same case:
it knows of no merge to undo.

#### Why an undo of a merge that never happened must not proceed

`MergeUndone` restores relationships. Applied against a graph that was never
merged, the projection upserts `before` edges that were never displaced —
writing a pre-merge graph over a graph that has no pre-merge state, silently
rather than loudly. The second undo is the same hazard with a live victim:
the entities are no longer aliases, so a second `MergeUndone` would restore
edges over the corrected graph.

#### What it cannot catch

An undo naming the **wrong** merge. Both ids are merges that happened, so the
guard passes and the wrong merge is reversed. That is why
`ConsolidationService.merge` returns the emitted `EntitiesMerged` — the
`event_id` it carries is the only handle a caller has for the merge it just
made, and there is no lookup that would recover it afterwards.

#### On a retry, it is usually the right answer

An undo that appears to have failed may have succeeded before the failure
reached the caller. `UnknownMergeError` on the retry means the first attempt
landed, not that anything is wrong — see
[`docs/how-to/use-the-write-model.md`](../how-to/use-the-write-model.md) for
how to handle it.

### Fields carried on `MergeUndone` and where they come from (replayed state, not the caller)

`undo_merge` takes two arguments and emits an event with four payload fields.
Everything but the two arguments is read off the `MergeRecord` that
`_merge_in_effect` returned — that is, off **replayed aggregate state**.

| Field on `MergeUndone` | Type | Source |
|---|---|---|
| `tenant_id` | `TenantId` | the caller's argument |
| `merge_event_id` | `UUID` | the caller's argument |
| `canonical_entity_id` | `EntityId` | `record.canonical_entity_id` |
| `unmerged_entity_ids` | `list[EntityId]` | `list(record.merged_entity_ids)` |
| `restored_relationships` | `list[Relationship]` | `[r.before for r in record.redirections]` |

There is no parameter by which a caller could supply, override or extend any
of the last three. A caller holding a stale idea of what the merge absorbed
cannot write it into the log; the aggregate rebuilt the history and answers
from that.

#### `unmerged_entity_ids`

Exactly `record.merged_entity_ids`, copied into a fresh list — the entities
the merge absorbed, which the undo frees. `MergeUndone` declares
`min_length=1`, so an undo that frees nothing is not constructible; the record
it is built from cannot be empty either, since `EntitiesMerged` declares the
same bound.

`_apply_undone` reads this field, not the record, when popping `alias_of`, so
the event is self-sufficient on replay.

#### `restored_relationships`

The `before` side of every `RelationshipRedirection` the merge recorded —
`list[Relationship]`, not `list[RelationshipRedirection]`. The `after` side is
deliberately dropped: it describes the merged graph, and an undo is putting
the pre-merge edges back.

Both kinds of redirection restore the same way, which is why `before` is a
whole `Relationship` rather than a pair of endpoint ids:

| Redirection | What the merge did | What the undo restores |
|---|---|---|
| `after` is a `Relationship` | moved the edge onto the canonical entity | `before` — the edge on its original endpoint |
| `after` is `None` | dropped the edge, both endpoints absorbed | `before` — the edge has to be *recreated*, not moved |

`restored_relationships` defaults to `[]` and is empty whenever the merge
recorded no redirections. That is a legitimate undo, not a degenerate one: it
frees the aliases and restores nothing, because nothing was displaced.

The event validates the payload it was handed — a `Relationship` whose
`tenant_id` differs from the event's raises `ValueError` — but by construction
the aggregate cannot produce one, since `EntitiesMerged` already rejected a
foreign redirection on the way in.

#### Why the payload exists at all

A projection handler sees one event at a time. Resolving `merge_event_id`
against the log would be a read from inside a fold, which is the thing a
projection exists to avoid — so the restoration travels on the event.
`GraphProjection._apply_undo` needs nothing else: it removes the aliases named
by `unmerged_entity_ids` and upserts `restored_relationships`.

That does not make the payload the source of truth. The log is, and the
aggregate derives the payload from it by replay; the event is that derivation
materialised once, at the boundary, so every downstream consumer gets it for
free. The distinction matters when the two could disagree — they cannot,
because nothing but `undo_merge` constructs one in this library.

`merge_event_id` earns its place separately. An undo carrying only
restorations would be indistinguishable from an unrelated correction, and the
aggregate could not tell whether the merge it names ever happened — which is
the check `UnknownMergeError` is.

For the event definitions themselves see
[`docs/reference/events.md`](events.md); for `RelationshipRedirection` see
[`docs/reference/domain-value-types.md`](domain-value-types.md).

## State serialisation contract

Both state models are snapshotted, so both are constrained by what a snapshot
can carry. The round trip is `AggregateRoot._serialize_state`, which is
`self._state.model_dump(mode="json")`, and `_restore_from_snapshot`, which is
`state_type.model_validate(state_dict)` followed by replay of the events after
the snapshot's version. Nothing in this package overrides either.

So the contract on `DocumentState` and `ConsolidationLogState` is: **every
field must survive `model_dump(mode="json")` → `model_validate` unchanged in
meaning.** A field that does not is not a slow field, it is a field that
loses information the invariants are checked against.

### Why every collection is `list`/`dict` and never `set`

JSON has no representation for a set, so `mode="json"` has nowhere to put one.
That is the whole reason for the collection types here, and both state models
say so in their own docstrings:

| Field | Type | Semantics |
|---|---|---|
| `extraction_model_versions` | `list[str]` | set-like — membership is all that is read |
| `embedding_models` | `list[str]` | set-like — membership is all that is read |
| `merges` | `list[MergeRecord]` | genuinely a sequence |
| `merged_entity_ids` | `list[EntityId]` | set-like (no duplicates, checked on the event) |
| `redirections` | `list[RelationshipRedirection]` | sequence, paired positionally |
| `alias_of` | `dict[EntityId, EntityId]` | a mapping, which JSON does have |

The set-like fields are the ones to be careful about, because a `set` would
read better at every use site: four of the six lookups above are `in` or
`.get`, and none of them cares about position. The type is chosen by the
snapshot, not by the call site.

`alias_of` is the case where JSON changes the representation without changing
the meaning. `EntityId` is a `UUID`, and JSON object keys are strings, so
pydantic serialises each key with `str(...)` and parses it back to a `UUID` on
`model_validate`. A replayed `alias_of` therefore compares equal to one built
in process while holding **equal-but-distinct** `UUID` objects — which is why
every id comparison in this package is `==` and never `is`. That is not a
style preference: `_merge_in_effect` matching by `is` would find nothing after
any real load, and every `undo_merge` would raise `UnknownMergeError`.

`MergeRecord.undone` is an ordinary `bool` and survives the same way. There is
no separate "undone" list to keep in step, which is part of why an undo is a
flag rather than a deletion.

Nothing else in state needs special handling: `str`, `bool`, `UUID`,
`Relationship` and `RelationshipRedirection` are all pydantic-serialisable,
and `Relationship` reaches state only nested inside a `RelationshipRedirection`
on a `MergeRecord`.

### Which fields have meaningful order

| Field | Order meaningful? | Why |
|---|---|---|
| `merges` | **yes** | replay order; `_merge_in_effect` scans it front to back and returns the first record in effect |
| `redirections` | **yes**, positionally | `undo_merge` builds `restored_relationships` as `[r.before for r in record.redirections]`, so index *i* of one is index *i* of the other |
| `merged_entity_ids` | no | `ConsolidationLogState`'s docstring says so explicitly — "preserved only because a list preserves it" |
| `extraction_model_versions` | no | membership only |
| `embedding_models` | no | membership only |
| `alias_of` | n/a | a mapping; nothing iterates it |

"Order is not meaningful" is a claim about *readers*, not about stability. A
`list` preserves order whether or not anyone depends on it, and the two
model-version lists happen to come out in the order the events were applied.
Nothing may start depending on that — the fields are documented as membership,
and a reader that sorts or reverses them would still be correct.

The two rows that *are* meaningful are meaningful in different ways.
`merges` is a scan order: the first record matching an id and not `undone`
wins, so a merge and its later undo are found in the order they happened.
`redirections` is a positional pairing, which is the stronger claim of the
two — a reordering there would silently mismatch nothing, because
`restored_relationships` is derived from it in the same pass and no other
field indexes into either.

### What a snapshot does not carry

The snapshot holds state, not history. `MergeRecord.merge_event_id` is the
only link back to the log, and it is *in* state precisely so an undo works
against an aggregate rehydrated from a snapshot plus a short tail, with the
original `EntitiesMerged` never re-read.

`Document` is not snapshotted at all (`document_repository` takes no snapshot
store), so its state contract is exercised only by event replay. That does not
make it slack: `DocumentState` is declared under the same rule, and the
repository could be given a snapshot store tomorrow without touching the
model.

## Repositories

`redstring.aggregates.repositories`. Both return
`TenantAwareRepository`, so a `save` outside a `tenant_scope` raises rather than
writing, and an event whose `tenant_id` disagrees with the ambient scope raises
rather than landing in the log.

### `document_repository(event_store) -> TenantAwareRepository[Document]`

```python
document_repository(event_store: AggregateStore) -> TenantAwareRepository[Document]
```

No snapshot store, by design: a document accumulates one event per model
version — a handful over its whole life — so a snapshot would cost a write to
save replaying three events.

### `consolidation_repository(event_store, snapshot_store, *, snapshot_every=...)`

```python
consolidation_repository(
    event_store: AggregateStore,
    snapshot_store: SnapshotStore,
    *,
    snapshot_every: int = CONSOLIDATION_SNAPSHOT_EVERY,
) -> TenantAwareRepository[ConsolidationLog]
```

`snapshot_store` is **required**, not optional. The unbounded stream is the
known cost of serialising consolidation per tenant, and an optional parameter is
one nobody passes — the omission would surface as slow merges long after the
code that omitted it was written. It is passed through as the underlying
`AggregateRepository`'s `snapshot_store`, with `snapshot_every` as its
`snapshot_threshold`.

### `CONSOLIDATION_SNAPSHOT_EVERY = 100`

Events between `ConsolidationLog` snapshots. A starting point rather than a
measured optimum: small enough that rehydration reads a bounded tail, large
enough that a snapshot is not written on most saves. Override per call with
`snapshot_every=`; nothing depends on the number being 100.

### Tenant enforcement settings

`validate_on_save` is left at its library default `True`. `enforce_on_load` is
**not** turned on: it validates that a context exists without filtering events
by it. Loading is safe regardless — a `Document` stream holds one document's
events and its id is already derived from the tenant, and a `ConsolidationLog`
stream *is* a tenant.

## Exception reference

| Exception | Raised by | Constructor keywords |
|---|---|---|
| `MergeIntoAliasError` | `ConsolidationLog.merge` | `alias_entity_id`, `canonical_entity_id` |
| `DoubleMergeError` | `ConsolidationLog.merge` | `entity_id`, `canonical_entity_id` |
| `UnknownMergeError` | `ConsolidationLog.undo_merge` | `merge_event_id` |
| `ValueError` | `document_stream` | positional message (blank `source_id`) |

All three consolidation errors subclass `ConsolidationInvariantError` →
`RedstringError`, and all take keyword-only arguments.

## Related

- [`docs/adr/0001-event-log-schema-and-granularity.md`](../adr/0001-event-log-schema-and-granularity.md)
  — why `Entity` is not an aggregate, and Decisions 7 and 8 on repositories and
  snapshots.
- [`docs/reference/events.md`](events.md) — the event payloads and their
  validators.
- [`docs/reference/domain-value-types.md`](domain-value-types.md) —
  `EntityId`, `TenantId`, `SourceId`, `RelationshipRedirection`.
- [`docs/how-to/use-the-write-model.md`](../how-to/use-the-write-model.md) —
  loading, commanding and saving an aggregate end to end.
