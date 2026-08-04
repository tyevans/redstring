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
and its stream grows without bound; `EveryNEvents(100)` snapshots keep
rehydration bounded. If a tenant ever makes that painful, the escape hatch is
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
is BACKLOG B32, to be taken up in slice 6 when extraction actually emits; the
alternatives all require either reading the projection from the write path or
a `delete_entity` that slice 3 deliberately declined.

## Decision 3: `event_version = 1`, declared explicitly, on every event

`DomainEvent` defaults it to 1 already. An event that never mentions it looks
versioned and is not: nobody chose the number, and nobody will think to bump
it. `tests/unit/events/test_schema.py` checks `__annotations__` rather than
the resolved default, which is the difference between "the value is 1" and
"somebody wrote 1".

`event_type` is **never** declared: `DomainEvent` derives it from the class
name, and declaring it is either noise or a silent decoupling of the wire name
from the class name.

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
today, because nothing emits yet. The emitter is slice 7, and slice 7 owns the
choice: read through the same process that writes (fresh by construction),
require the projection to be caught up to the log's head before merging, or
accept the staleness and say so. This paragraph exists so that the decision is
made rather than inherited.

## Consequences

- The 67 classes are gone. Five modules with no consumers were deleted
  outright; `consolidation.py` and `scraping.py` survive only until the legacy
  services that import them die in slices 7 and 9, and are un-registered so
  they no longer hold wire names the live schema needs.
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
