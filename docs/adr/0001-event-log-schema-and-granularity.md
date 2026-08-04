# ADR 0001: The event log's schema, granularity, and aggregates

**Status:** accepted, slice 5b of the ring migration.

**Why this one is an ADR when the rest of the migration is not:** everything
else in the re-architecture is reversible. A persisted event schema is not.
Code can be refactored; a log that already holds a million `DocumentExtracted`
events cannot be, except through an upcaster written against whatever mistake
was made. This records the choices while the reasoning is still available.

## Context

kg-builder is becoming event-sourced: the event log is the write model, and
`GraphStore` and `VectorStore` are projections of it -- derived, disposable,
and rebuildable by replay.

`kg_builder/events/` held 67 event classes at the start of this slice. They
were shaped against the SQLAlchemy models, and **none had ever been emitted**,
so there was no compatibility to preserve and no reason to carry their
mistakes into something permanent.

## Decision 1: two aggregates, `Document` and `ConsolidationLog`

Reverses an earlier decision to skip the aggregate pattern. The objection
recorded then -- "a document yielding ten thousand entities is not ten
thousand transactional aggregates" -- is an argument against **`Entity`** as
the aggregate, not against the pattern. With `Document` as the aggregate, ten
thousand entities are one aggregate and the objection evaporates.

The earlier decision was also self-contradictory: it had already chosen
`document` and `consolidation` as stream categories, which *are* aggregate
boundaries. Taking aggregate stream semantics while refusing the object that
gives them meaning means hand-rolling the version management and invariants
the pattern provides -- and `append()` requires `expected: ExpectedVersion`
either way, so declining aggregates avoids nothing.

| Aggregate | Stream id | Length | Owns |
|---|---|---|---|
| `Document` | `uuid5(tenant_id, source_id)` | short, one per model version | extraction is idempotent per model version |
| `ConsolidationLog` | the `tenant_id` | unbounded, snapshotted | the three merge invariants |

**`Entity` is deliberately not an aggregate.** That part of the original
objection stands and is the reason the granularity below is coarse.

### Stream ids

`StreamId.aggregate_id` is a `UUID`; `SourceId` is a caller-supplied `str`.
`uuid5` bridges them: deterministic, so re-extracting a document appends to
the stream it already has rather than forking a new one, and namespaced by
**tenant**, so the same URL ingested by two tenants is two streams. The tenant
is the namespace rather than part of the hashed name, which keeps the two
halves of the key structurally separate -- a scheme that concatenated them
before hashing would map `("t", "ab")` and `("ta", "b")` onto one stream, and
one half is free-form text.

The consolidation stream id **is** the tenant id, not a derivation of it:
there is exactly one consolidation log per tenant, so a further mapping would
be a fiction with no second value to distinguish.

### Why consolidation is serialised per tenant

Two concurrent merges touching the same entities must not interleave. Nothing
narrower than the tenant sees the conflict, because consolidation is
cross-document by nature. The cost is that a tenant's merges are serialised
and its stream grows without bound; snapshots keep rehydration bounded.

What was built is not a policy object but a threshold on the repository:
`consolidation_repository` in `aggregates/repositories.py` constructs
`AggregateRepository(event_store, ConsolidationLog, snapshot_store=...,
snapshot_threshold=CONSOLIDATION_SNAPSHOT_EVERY)`, wrapped in
`TenantAwareRepository`. `CONSOLIDATION_SNAPSHOT_EVERY` is 100, and
`snapshot_store` is a required parameter rather than an optional one. Decision
8 records why both of those are the way they are, and why `Document` gets no
snapshot store at all.

If a tenant ever makes serialisation painful, the escape hatch is
partitioning consolidation by entity cluster -- more complex, so not built
until measurement demands it.

## Decision 2: coarse events. One `DocumentExtracted` per extraction run

`DocumentExtracted` carries every entity and every relationship the run found.
Rejected: one event per entity.

The volume argument -- a document yielding 10k entities should not yield 10k
events -- is true but secondary, and would not on its own have been decisive.
**The argument that settled it came from the projection.**

`GraphStore.upsert_relationship` raises `MissingEntityError` when an endpoint
is absent; dangling edges are not permitted. A coarse event lets one handler
write the entities and then the edges between them, so an extraction is never
*partly* applied. Split into per-entity events, the ordering between an edge
and its endpoints becomes load-bearing **across** events, and any reordering
or partial delivery turns perfectly good data into a poison event. Coarse
events make the fold order-independent between events, which is what the
projection contract asks for; within one event, "entities before edges" is one
handler's business rather than the bus's.

**The cost, stated plainly.** A consumer wanting per-entity granularity
iterates the payload. And a re-extraction that finds *fewer* entities than the
previous run cannot express the removal -- the projection upserts, so the
graph converges on the union of every run rather than on the latest one. That
is BACKLOG B32, originally to be taken up in slice 6 when extraction actually
emits; the alternatives all require either reading the projection from the
write path or a `delete_entity` that slice 3 deliberately declined. **Still
open as of slice 11**: slice 6 began emitting and did not take it up, so B32
is now open on its own merits rather than scheduled against a slice. The
blocker recorded there is not the mechanism but the meaning -- the `Document`
aggregate already replays every `DocumentExtracted` and could compute the
retraction itself, but ids are derived per run by `uuid5` over the extracted
name, so "the same entity across two runs" has no definition yet.

## Decision 3: `event_version = 1`, declared explicitly, on every event

`DomainEvent` defaults it to 1 already. An event that never mentions it looks
versioned and is not: nobody chose the number, and nobody will think to bump
it. `tests/unit/events/test_schema.py` checks `__annotations__` rather than
the resolved default, which is the difference between "the value is 1" and
"somebody wrote 1".

`event_type` is **never** declared: `DomainEvent` derives it from the class
name, and declaring it is either noise or a silent decoupling of the wire name
from the class name.

Both of those are properties of an event class, and a per-class property is
only as good as the list of classes it is applied to. So the schema is now
**closed**: `KG_EVENT_TYPES` in `events/__init__.py` is a tuple naming every
event kg-builder writes -- currently `DocumentExtracted`, `EntitiesEmbedded`,
`EntitiesMerged` and `MergeUndone` -- and it exists as a tuple rather than as
prose precisely so the properties above can be asserted by introspection over
it. A new event class inherits every check in the suite by joining it.

"Adding an event means adding it here" is therefore **enforced rather than
advisory**. That distinction is the one CLAUDE.md keeps relearning: a rule
that lives only in a docstring is the rule that failed four times in slice 3.
An event class that is written, registered, and simply left out of the tuple
would get no schema check, no replay case and no handler check, and nothing
would go red -- so the omission is caught by a test that does not read the
tuple at all, described next.

The tuple is the schema surface a reader should start from; see
`docs/reference/events.md` for the per-event payloads.

### What makes the tuple enforceable: the filesystem-vs-registry cross-check

A hand-maintained tuple that everything keys off is a single point of
forgetting, so one test refuses to read it.
`test_the_tuple_lists_exactly_the_registered_events` in
`tests/unit/events/test_schema.py` walks every module of `kg_builder.events`
with `pkgutil.iter_modules`, importing each, then collects from
`eventsource`'s `default_registry` the classes whose `__module__` starts with
`kg_builder.events`. It asserts set equality against `KG_EVENT_TYPES` in
**both** directions, and the two directions fail for different reasons:

- `missing` -- registered but absent from the tuple. Such an event gets no
  schema check, no replay case and no handler check, and nothing else goes
  red.
- `extra` -- listed in the tuple but not registered. A stored one cannot be
  rehydrated, because `@register_event` is what turns a wire name back into a
  class.

**Why the package walk rather than the tuple.** An event module nothing
imports registers nothing, so a registry read on its own would find it absent
and agree with a tuple that also omits it -- the same hole, one layer down.
Walking the package makes the *filesystem* the source of truth, and the
filesystem is the one thing here that cannot be forgotten: the class has to be
in a file to exist at all.

This is the single gate in the schema suite that cannot key off
`KG_EVENT_TYPES`, which is precisely what qualifies it to check the tuple.
Its counterpart is `test_the_schema_is_not_empty`: every other test in the
module is parametrised over the tuple, and a parametrised suite over an empty
tuple passes with no cases at all. That test is what stops a registry-driven
suite -- or a tuple emptied by a bad merge -- reporting green while asserting
nothing.

### The two consumers of the tuple

Two test modules parametrise over `KG_EVENT_TYPES`, which is what makes
joining the tuple the act that earns an event its checks. Between them they
cover the two things a persisted event has to be: well-formed on the way in,
and foldable on the way back out.

**`tests/unit/events/test_schema.py` -- the shape of the class.** Six
properties, each asserted once per event type:

| Property | Why it is not left to review |
|---|---|
| `event_version` appears in `__annotations__`, and its default is 1 | the inherited default makes an unversioned event look versioned |
| `event_type` is *not* in `__annotations__`, and `event_type_name()` equals the class name | a hand-declared wire name can drift from the class and only the log would know |
| `aggregate_type`'s default is `DOCUMENT_CATEGORY` or `CONSOLIDATION_CATEGORY` | a third category is a stream no aggregate owns and no repository versions |
| `tenant_id` is a required field | an optional tenant is one `None` away from a projection writing rows nobody owns |
| `model_validate` with an undeclared field raises | a typo'd field silently dropped is indistinguishable, afterwards, from the emitter never setting it |
| `get_event_class(wire_name)` returns the class | `@register_event` is what rehydrates a stored event |

The last row is the one that had to be learned. Unregistered, an event
round-trips through JSON as a dict and nothing fails until a *persistent*
store tries to rehydrate one -- long after the events were written. The
in-memory store keeps object identity, so no other test in the suite can see
the difference, and a cosmic-ray mutant deleting the decorator survived until
this check existed. It also guards the reverse direction: slice 5b had to
un-register the legacy consolidation events because they were holding wire
names this schema needs, and the registry refuses duplicates.

**`tests/unit/projections/test_replay_coverage.py` -- the event's place in the
read model.** Two properties, again per event type:

- *A pinned case emits it.* The fixture replays every scenario in
  `test_replay_equivalence.PINNED` into a fresh rig, reads the resulting log
  back, and collects the types actually written. An event no scenario produces
  has nothing proving it replays.
- *A projection handles it.* The union of `GraphProjection.subscribed_to()`
  and `VectorProjection.subscribed_to()` must contain it.

An event no projection folds is a fact the read models never learn. That is
occasionally the right answer -- an event kept purely for audit -- but never
the right *accident*, so it has to be argued for by amending this test rather
than discovered when a query comes back empty.

Together with the cross-check above, the chain is closed in both directions: a
class in a file is in the registry, a class in the registry is in the tuple,
and a class in the tuple is schema-checked, emitted by a replay scenario, and
folded by a projection. See `docs/reference/events.md` for the payloads and
`docs/how-to/use-the-write-model.md` for emitting one.

## Decision 4: `dict[str, Any]` payload fields stay as they are

`Entity.properties`, `Entity.external_ids` and `Relationship.properties` reach
the log as free-form mappings. As persisted payloads these are permanent,
unversioned escape hatches, and the question was whether any needs a versioned
sub-schema **before** events start flowing.

Decision: leave them. Their contents come from LLM extraction and are
open-world by nature; a versioned sub-schema would have to be invented per
entity type, ahead of any caller asking for one, and would be wrong in a way
that is then permanent. `event_version` plus an upcaster is the escape route
if a well-known key later deserves promotion to a typed field -- and promoting
a key out of a free-form dict is a strictly easier migration than removing a
typed field nobody wanted.

`SourceDocument.metadata` is moot: no event carries a `SourceDocument`. Only
`source_id` crosses into the log.

One hazard is *not* covered and is filed rather than fixed: these dicts can
hold a NUL character, which Postgres `jsonb` refuses outright --
`domain/vector.py` already learned this for `VectorRecord.metadata`. Adding
the same rejection later is a tightening that only refuses data which could
never have been persisted anyway, so deferring it is safe in a way that
deferring a schema decision is not. See BACKLOG B36.

## Decision 5: `Alias.displaced` deleted

It was added when undo was a storage problem. Undo is now a compensating
`MergeUndone` event, so the log already holds the pre-merge state, and a
`displaced` dict would be a second, unversioned copy of it.

What a merge actually displaces is **edge endpoints**, and that is now carried
in typed form as `RelationshipRedirection` -- the edge before, and the edge
after, with `after is None` meaning the merge dropped it because both
endpoints were absorbed and the edge would have become a self-loop that
`Relationship` forbids. `before` is a whole `Relationship` rather than a pair
of ids precisely so undo can *recreate* a dropped edge, not merely move one
back.

`Alias` also gained `extra="forbid"`, so a call site still passing
`displaced=` fails instead of silently losing the data to pydantic's default.

## Decision 6: `MergeUndone` carries its restoration

The question was whether pre-merge state comes back by **replay** or by
**payload**. The answer is both, in the only arrangement that works:

- On the **write** side, recovery is by replay. `ConsolidationLog` rehydrates
  its merge history from the log, so when asked to undo a merge it *derives*
  what that merge displaced from replayed state. Nothing is stored anywhere
  else, and nothing is passed in by the caller.
- On the **read** side, recovery is by payload. A projection handler sees one
  event at a time; resolving `merge_event_id` would mean reading the log from
  inside a fold, which is what a projection exists to avoid.

So `MergeUndone` is that recovery materialised once, at the boundary, by the
aggregate that could compute it. Replay is the source of truth; the payload is
its cached answer.

### The asymmetry this leaves, recorded rather than resolved

`EntitiesMerged.redirections` has to be **computed by reading the projection
from the write path**: something must know which edges currently touch the
absorbed entity, and edges live in `Document` streams, so the
`ConsolidationLog` aggregate cannot derive them from its own history.

That is the same coupling Decision 2 rejects, where "diffing means reading the
projection from the write path" was the argument against retraction events.
The asymmetry is real and is recorded rather than argued away: a retraction
event is avoidable, a redirection is not, and putting it in the payload is
what keeps the *read* side free of the coupling.

The consequence, which the payload shape makes permanent: computed against a
lagging projection, an `EntitiesMerged` records an edge set that never existed
at that position in the log, and a `MergeUndone` derived from it later
restores edges to a state the log never held. Nothing bounds that staleness
today. The emitter was slice 7, which owned the choice between reading through
the same process that writes (fresh by construction), requiring the projection
to be caught up to the log's head before merging, and accepting the staleness
and saying so.

**Slice 7 chose the third, deliberately.** `ConsolidationService.merge` in
`consolidation/service.py` calls `get_relationships_for` *before* entering
`tenant_scope` and loading the aggregate, so the edge set a merge plans against
can already be stale when the append happens. The argument, recorded in full in
[ADR 0004](0004-consolidation-emits-events.md), is that no ordering of the two
steps makes the graph authoritative -- the read model lags the log by
construction -- so doing the read inside the aggregate's window would widen the
window without making it correct.

What that buys is a bounded rather than an unbounded consequence. Of the three
ways staleness shows up, two are benign: a redirection for an edge that has
since gone is idempotent on both writes, and an edge that appeared after the
read is repaired by the extraction fold's alias resolution
([ADR 0007](0007-the-extraction-fold-resolves-through-aliases.md)). The third
is not, and is filed rather than fixed: if the canonical entity already carries
the same claim, that resolution creates a permanent parallel edge instead of
fixing one. That is BACKLOG B43, open, pinned in
`tests/unit/consolidation/test_known_gaps.py` as a test asserting the wrong
answer on purpose. Only re-planning on version conflict addresses it, because
`plan_redirections` is the only code that deduplicates and by definition it
never saw the late edge.

The paragraph above was written so the decision would be made rather than
inherited; it was, and this is the record of it.

## Decision 7: both aggregates load and save through `TenantAwareRepository`

Neither `Document` nor `ConsolidationLog` is reachable through a bare
`AggregateRepository`. `document_repository` and `consolidation_repository` in
`aggregates/repositories.py` are the only constructors, and both return a
`TenantAwareRepository` wrapping the plain one, so there is no supported way to
obtain a repository for either aggregate that does not check the tenant on the
way in.

The alternative was to check at each call site: the write path already knows
which tenant it is acting for, so an assertion before every `save` would have
the same effect. It was rejected because tenant isolation is the one property
this project treats as inviolable, and a per-call-site check is a rule rather
than a mechanism -- the failure mode is the *next* call site, written by
someone who did not know the rule existed. Wrapping at the constructor makes
the check impossible to omit without deleting the wrapper, which is a visible
edit to a module whose docstring says why it is there.

The tenant is not passed as an argument. `eventsource` carries it in a
`ContextVar` entered by `tenant_scope`, so the ambient scope is what the
repository validates against, and a `save` is checked against the scope the
caller is actually in rather than against a value it repeats. That is
[recurring defect §2](../../.claude/rules/recurring-defects.md) avoided by
construction: the tenant has one declaration site per operation, and there is
no second one to drift from it.

Two knobs on the wrapper decide what "checked" means, and the two are set
differently on purpose; the subsections below record why. See
[`docs/reference/aggregates.md`](../reference/aggregates.md) for the
constructors' signatures and
[`docs/how-to/use-the-write-model.md`](../how-to/use-the-write-model.md) for
what a caller does with them.

### `validate_on_save=True`, `enforce_on_load` deliberately off

Both constructors call `TenantAwareRepository(...)` with no keyword arguments,
so both knobs sit at the library's defaults: `validate_on_save=True`,
`enforce_on_load=False`. Only one of those is a decision -- the save side is
the default because it is the right answer, and the load side is the default
because turning it on would buy nothing here.

**Save validation is what enforcement means.** With `validate_on_save=True`,
`TenantAwareRepository.save` walks every uncommitted event and compares its
`tenant_id` against the ambient scope, raising before any of them reach the
store. That is enforcement at *write* time, in *tested library code*: the log
is permanent, so an event with the wrong tenant is not a bug that can be fixed
by redeploying -- it is a row somebody has to go and delete, in a store whose
whole premise is that it is append-only. Checking on the way in is the only
point at which the mistake is still cheap.

**`enforce_on_load` is off because of what it actually does.** Its name reads
like a filter, and it is not one. The library's own docstring says so: it
"validates context exists but does not filter events (filtering requires
EventStore changes)". Turned on, `load` asserts that *some* tenant scope is
entered and then loads exactly the stream it was asked for, whichever tenant's
events that stream holds. It cannot catch a cross-tenant read, because it never
compares the scope to the data -- it only rejects the case where a caller has
no scope at all.

So the choice was between a check that stops bad writes and a check that adds
a precondition without a corresponding guarantee. Enabling the second would
buy a *narrower* form of the same thing already enforced elsewhere -- a load
with no scope is followed by a save with no scope, which already raises
`TenantContextNotSetError` -- while reading, to anyone scanning the
constructor, as though loads were tenant-filtered. **A check that looks
stronger than it is costs more than no check**, because it is the reason
nobody writes the one that would have worked. That is the same failure shape
CLAUDE.md records for property tests that stay green under a deliberate
defect.

The next subsection records the structural reason a load needs no filter here,
which is what makes leaving it off safe rather than merely cheap.

### Why loading is safe without it

The general worry `enforce_on_load` gestures at is a stream holding more than
one tenant's events, so that loading it hands the caller somebody else's data.
That cannot arise here, and the reason is Decision 1 rather than anything the
repository does.

Both stream ids are **derived from the tenant**, so a stream is a tenant's by
construction:

- a `Document` stream id is `uuid5(tenant_id, source_id)`, with the tenant as
  the `uuid5` **namespace**. Two tenants ingesting the same URL get two
  different aggregate ids, and no `source_id` can be chosen that lands one
  tenant's document in another's stream -- that is the property the namespace
  split in Decision 1 was chosen for.
- a `ConsolidationLog` stream id **is** the tenant id. There is no derivation
  to get wrong; the aggregate id and the tenant are the same UUID.

So a filter over a loaded stream would have nothing to remove. Every event in
a `Document` stream was written by a save that `validate_on_save` checked
against the tenant whose id is half the stream's own name, and every event in
a consolidation stream was checked against the tenant the stream *is*. The
write-side check is what makes the read side uneventful: no event with a
foreign `tenant_id` can be in the stream to begin with, so filtering on the
way out would be a second enforcement of an invariant already held.

The other half is that a caller cannot reach a stream without already holding
the tenant. `document_stream` and `consolidation_stream` in `events/streams.py`
both take `tenant_id` as a keyword argument and are the only supported way to
produce a `StreamId` -- `ConsolidationService.merge` calls
`consolidation_stream(tenant_id=tenant_id).aggregate_id` inside the same
`tenant_scope` it later saves in. A caller who passes the wrong tenant there
does not read another tenant's log; it computes an id that identifies a
*different* stream and reads that one, and any event it then tries to append
is rejected by `validate_on_save` against the ambient scope. The failure mode
is an empty aggregate and a raise, not a leak.

None of this holds because a check runs. It holds because the id scheme makes
a cross-tenant stream unnameable -- which is the difference between an
invariant that is enforced and one that is inferred, and the reason the
enforcement that *is* needed sits on the write path.

### Consequence: a save outside a scope raises, and so does a mismatched event

The two knobs above are configuration; this is what they mean to a caller.
Every save through either repository goes through
`_validate_tenant_consistency`, which asks for the ambient tenant and then
compares it against each uncommitted event, so two things that would otherwise
be silent writes are now raises:

- **No `tenant_scope` at all.** `get_required_tenant` raises
  `TenantContextNotSetError`. There is no "default tenant" and no fallback to
  a value on the aggregate; a write path that forgot the scope stops rather
  than guessing.
- **An event whose `tenant_id` disagrees with the scope.** The validator
  collects every mismatched event's id and raises `TenantMismatchError`
  carrying the expected tenant, the first mismatched one, and the ids -- so
  the failure names the events rather than only the aggregate.

Both raise **before** `AggregateRepository.save` is called, so nothing reaches
the store: no partial append, no compensating delete, and the aggregate still
holds its uncommitted events. That is the point of putting the check here.
An event log is append-only by construction, so the alternative to raising is
not a bug that gets fixed by redeploying -- it is a row in a permanent store
that somebody has to go and remove, in the one store whose premise is that
nothing is removed. **A mistake this class is cheap only at the moment of the
write.**

The validation is per *event*, not per aggregate, which matters for the case
the coarse-event decision makes likely: a single save can carry several
events, and one of them carrying a foreign tenant is enough to refuse all of
them. It is also why `tenant_id` being a required field on every event
(Decision 3, asserted per type over `KG_EVENT_TYPES`) is load-bearing rather
than tidy -- the library skips events that have no `tenant_id` attribute at
all, so an event that made the field optional would be *silently exempt* from
the check described here. The schema gate and the repository gate only
compose because the first one holds.

`tests/unit/aggregates/test_repositories.py` pins both raises against a real
`InMemoryEventStore` and the real `tenant_scope` -- no mocks, because a mock
repository would agree with any implementation, including one that validated
nothing. The mismatch test is the one worth reading: it enters a scope for one
tenant and records an extraction for a *different* one, which is the shape a
caller actually produces when a tenant id is threaded through a call chain and
one hop uses the wrong variable. The neighbouring test that one tenant's
consolidation log is not another's covers the same property from the stream
side.

What this does **not** buy, and the boundary is the same one the previous
subsection drew: it says nothing about *reads*. A load outside a scope
succeeds, because `enforce_on_load` is off and because the stream id already
encodes the tenant. Isolation on the way out is a property of Decision 1's id
scheme; isolation on the way in is a property of this check. Neither
substitutes for the other, and a reader looking for "where is tenant isolation
enforced?" needs both halves.

See [`docs/how-to/use-the-write-model.md`](../how-to/use-the-write-model.md)
for the two exceptions as a caller handles them, and
[`docs/reference/aggregates.md`](../reference/aggregates.md) for the
repository constructors that impose them.

### Consequence: turning `enforce_on_load` on later is a live option

Leaving it off is not a door closed. Both constructors take the wrapper's
defaults, so enabling it is a keyword argument in two functions in
`aggregates/repositories.py` and nothing else -- no event schema changes, no
migration, no rewrite of a call site. It is worth writing down *why* it is off
rather than merely that it is, because the reasons are conditions on the
current design rather than a judgement about the feature, and conditions can
stop holding without anyone noticing they were load-bearing.

What would have to change to make it worth turning on:

- **A stream id that is not derived from the tenant.** The whole of "why
  loading is safe without it" rests on Decision 1: the `Document` namespace
  and the `ConsolidationLog` identity. A third aggregate keyed on something a
  caller supplies -- or a shared stream any tenant may append to -- would put
  a foreign event within reach of a load, and the argument here would be gone
  the moment that aggregate landed rather than when someone noticed. A new
  aggregate is therefore the trigger to re-read this subsection, and adding
  one without re-reading it is the failure mode to expect.
- **A load path that runs outside a tenant scope.** Today the only caller is
  `ConsolidationService.merge`, which loads inside the same `tenant_scope` it
  saves in, so `enforce_on_load` would never fire. Batch tooling that
  rehydrates aggregates without a scope -- a replay harness, an admin script,
  a metrics job -- is the case where "assert a scope was entered" starts
  catching something, because such a caller has no save to be checked by
  later.
- **The library gaining real filtering.** Its docstring says filtering
  "requires EventStore changes" and calls the parameter a future enhancement.
  If a version arrives where `enforce_on_load` filters events by tenant rather
  than only asserting a context, it becomes a different feature and this
  decision is about a name that no longer means what it meant here. Re-read it
  on an `eventsource-py` upgrade, not just on a design change.

Two things not to mistake for such a trigger. Enabling it is not a way to make
loads tenant-*filtered* -- it never compares the scope to the data, and
believing otherwise is precisely the mis-reading the previous subsection
refuses to invite. And it is not free of consequence: `exists` takes the same
branch as `load`, so turning it on adds a precondition to a method whose
callers may reasonably be probing before entering a scope at all.

Recording the argument this way is what makes the option live. A decision
whose reasoning was never written down cannot be revisited, only reversed by
someone guessing at it -- which is the same thing this ADR exists to prevent
for the event schema, applied to a keyword argument.

## Decision 8: `ConsolidationLog` is snapshotted every 100 events; `Document` has no snapshot store

The two aggregates get opposite answers to the same question, and the split is
Decision 1's table made operational: a `Document` stream is short and bounded,
a `ConsolidationLog` stream is unbounded by design. Snapshotting is therefore
not a global policy in this codebase but a per-aggregate one, expressed in the
two constructors in `aggregates/repositories.py` -- `document_repository`
takes no snapshot store, and `consolidation_repository` requires one.

Snapshots are an *optimisation*, and the thing that makes them safe to reason
about is that they are invisible: a load through a snapshot must produce the
state a full replay would. That is asserted directly rather than assumed --
`test_a_snapshot_restores_the_same_state_a_full_replay_would` in
`tests/unit/aggregates/test_repositories.py` drives six merges at
`snapshot_every=2`, then compares the snapshotted load against a load through
a repository with a *fresh* snapshot store, on both `state` and `version`. The
independent side is what makes it a test rather than a determinism check: an
oracle produced by the same snapshot would agree with any implementation,
including one that never wrote a snapshot at all, which is why the test also
asserts `snapshot_exists` before comparing.

### Why `Document` has none

A document accumulates **one event per model version**, in two separate key
spaces. `record_extraction` returns `None` and emits nothing when the
`model_version` is already in `DocumentState.extraction_model_versions`, and
`record_embeddings` does the same against `embedding_models`, so a retry after
a crash appends nothing and only a genuine model upgrade adds an event. A
document's whole life is a handful of them -- three, typically, after a couple
of upgrades. Rehydrating that is reading three rows.

A snapshot store would make every save a candidate for a second write, to save
replaying those three events. That is a cost paid on the write path to
optimise a read that was never slow, and it adds a store to the constructor's
signature -- and to every caller's wiring -- for no measurable return.

The case that would change this is worth naming, because it is the one to
watch for: a `Document` stream grows only if the event granularity changes. If
`DocumentExtracted` were ever split per entity (which Decision 2 rejects, and
whose rejection is on projection grounds rather than volume ones), a document
yielding ten thousand entities would have a ten-thousand-event stream and the
arithmetic here would invert. The right response to *that* would be to undo
the split rather than to add snapshots -- snapshots would be treating the
symptom of a granularity decision this ADR already made.

### Why `ConsolidationLog` requires one, non-optionally

The unbounded stream is the *known, accepted* cost of serialising
consolidation per tenant (Decision 1). A tenant's merge history only grows,
and nothing in the design ever truncates it, so rehydration without snapshots
grows without bound alongside it. Every merge loads the aggregate first, so
the cost lands on the operation that is already the serialisation point.

`snapshot_store` is a **required positional parameter, not an optional one
defaulting to `None`.** That is the decision, and the reasoning is the one
CLAUDE.md keeps relearning about rules versus mechanisms: an optional
parameter is one nobody passes. Constructing the repository without it would
succeed, every test would pass, every merge would be correct, and the omission
would surface as slow merges long after the code that omitted them was
written -- at which point the symptom (consolidation is slow) does not point
at the cause (a keyword argument absent from a wiring module). Making it
required moves the failure from a production latency curve to a `TypeError` at
the call site, which is the earliest and cheapest place it can be noticed.

The same reasoning is why the parameter is not defaulted to an in-memory
snapshot store. A default that silently works in tests and silently loses
snapshots in production is worse than no default, for the same reason
`enforce_on_load` is left off in Decision 7: **a mechanism that looks
load-bearing and is not costs more than its absence**, because it is what
stops anyone wiring the real one.

### `CONSOLIDATION_SNAPSHOT_EVERY = 100` is a starting point, not a measured optimum

The constant is declared in `aggregates/repositories.py` and passed as
`AggregateRepository`'s `snapshot_threshold`. **Nothing depends on the number
being 100**, and it is recorded here as an unmeasured choice so that nobody
later mistakes it for a tuned one. The reasoning behind it is only this: small
enough that a rehydration reads a bounded tail, large enough that a snapshot
is not written on most saves. Both halves are qualitative.

It is overridable per call --
`consolidation_repository(event_store, snapshot_store, snapshot_every=N)` --
and the override is keyword-only, so a caller changing it says so at the call
site. The suite already exercises the mechanism at `snapshot_every=2`, which
is a second reason the constant is not load-bearing: the equivalence test does
not run at 100 and does not need to.

What would settle the number, stated so the measurement is not re-derived from
scratch:

- **The distribution of merges per tenant**, which nobody has yet, because
  consolidation only began emitting in slice 7. A tenant whose log never
  reaches 100 events pays for a snapshot mechanism it never triggers; a tenant
  at 10^5 merges is the one the threshold exists for. These want different
  numbers, and the interesting question is whether the spread is wide enough
  to justify making it per-tenant rather than per-deployment.
- **The cost ratio between replaying one event and writing one snapshot**, in
  the snapshot store actually deployed. Rehydration reads at most
  `snapshot_every` events beyond the snapshot, and a snapshot of a
  `ConsolidationLog` is its whole `merges` list plus the `alias_of` map
  derived from it (`ConsolidationLogState`, dumped through
  `model_dump(mode="json")`) -- both grow with the tenant's merge history, so
  snapshot *size* is not constant either. A threshold tuned
  against a small alias map is not the same threshold at scale, and that
  interaction is the part a synthetic benchmark would most easily miss.

Until one of those is measured, changing the number is a guess replacing a
guess. Recording that here is the point: the alternative is a future reader
treating 100 as evidence.

See [`docs/reference/aggregates.md`](../reference/aggregates.md) for the
constructors' signatures and
[`docs/how-to/use-the-write-model.md`](../how-to/use-the-write-model.md) for
wiring a snapshot store into a caller.

## Consequences

- **The 67 classes are gone, and the package holds nothing but live schema.**
  Five modules with no consumers went in slice 5b, when this ADR was written.
  The remaining two were held up by their consumers rather than by any doubt
  about the schema, and each died with the last of them: `consolidation.py`
  was un-registered in slice 5b -- so it stopped holding wire names the live
  events needed -- and deleted in slice 7 alongside `services/consolidation/`,
  and `scraping.py` in slice 9 alongside `services/neo4j_errors.py` and the
  rest of `services/`. `events/base.py` went in the same commit as
  `scraping.py`: it was a one-line re-export of `TenantDomainEvent` that
  existed only because the legacy modules inherited from it, and
  `document.py` and `merge.py` had always imported that class directly.
  What is left is `document.py`, `merge.py` and `streams.py`, so **every
  module in `kg_builder/events/` is now schema this library actually
  writes** -- which is the precondition for the package walk in Decision 3
  being a complete check rather than a check with an exception list.
- `eventsource-py` moved from 0.5.0 to `>=0.9.1,<0.11`. 0.5.0 predates the
  library's own ring migration: `eventsource.domain`, `eventsource.ports` and
  `eventsource.application` do not exist there, and neither does `StreamId`,
  so every import in this slice is unresolvable against it. The concepts are
  not all absent -- 0.5.0 has optimistic concurrency (as `int` sentinels on
  `append_events(aggregate_id, aggregate_type, events, expected_version)`) and
  a `TenantAwareRepository` under `eventsource.multitenancy` -- but the API
  carrying them is a different one, and the stream-based design here is built
  on the shape 0.9 introduced. Verified against the 0.5.0 sdist rather than
  from memory.
- **A later `DocumentExtracted` silently reverts a merge**, and the fold
  cannot detect it, because `GraphStore` has nowhere to record that a merge
  happened. This needs no redelivery and no reordering: re-extracting a
  document under a new model version after consolidation is enough, and
  `Document.record_extraction` exists to permit exactly that. This was BACKLOG
  B34, pinned as deliberately-wrong tests. **Closed in slice 7**, the slice
  that began emitting `EntitiesMerged`: `GraphStore` gained
  `upsert_alias`/`remove_alias`/`resolve_entity_ids`, the extraction fold now
  resolves each endpoint before writing, and the pinned tests were inverted
  into `tests/unit/projections/test_aliases_survive_re_extraction.py`. Note
  that neither event schema changed to fix it -- the gap was in the read
  model's shape, which is what this ADR predicted by making the events
  permanent and the projections disposable.
