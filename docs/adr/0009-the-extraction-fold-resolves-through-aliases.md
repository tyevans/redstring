# ADR 0009: The extraction fold resolves endpoints through the alias table

**Status:** accepted (slice 7 — the slice that began emitting `EntitiesMerged`).

**Why this is an ADR.** [ADR 0002](0002-two-store-ports.md) gestures at this
decision from the outside: it lists "deleting would break replay" as reason 2
for the absence of `GraphStore.delete_entity`, and moves on. The decision it
gestures at is load-bearing on its own, and it has to answer two questions
0002 does not:

- does `GraphStore` need a `delete_entity` after all, or does an alias row do
  the job better?
- would a `uuid4` alias id do, given nothing reads the id back?

The answers are *no* and *no*, and both matter to anyone touching
`projections/graph.py`.

## Context: what the fold used to do

`GraphProjection._apply_extraction` wrote a document's entities and then its
relationships, by id, with the endpoints extraction had found. That is the
obvious fold for [`DocumentExtracted`](../reference/events.md), and it is
correct as long as the ids extraction emits still name the entities the graph
holds.

### The failure: a re-extraction after a merge silently reverted the merge

They stop naming the same entities the moment a merge lands:

```
DocumentExtracted(doc-1, model=A)   entities e0,e1,e2 and edge e1->e2
EntitiesMerged(e1 into e0)          edge redirected to e0->e2
DocumentExtracted(doc-1, model=B)   the same edge, endpoints e1->e2 again
```

The third event upserts the edge over the id it shares with the redirected
one, restoring the pre-merge endpoints. The merge is gone from the read model
and nothing raises: every write succeeded, and `e1` still exists, so
`upsert_relationship` has no missing endpoint to complain about.

### It was not a redelivery hazard, and no race was required

This is the part that took a slice to see. The log above is in strict order,
each event delivered exactly once, one projection, one process. Re-extraction
is not a pathology either — `Document.record_extraction` is keyed on the model
version precisely so a newer model can re-run a document already extracted.

Redelivery was the milder half of the same backlog item, and there the fold
was already safe: every write is an `upsert` or an idempotent `delete`, so a
checkpointed feed redelivering a contiguous suffix still ends at the log's
state.

### The assumption the fold was actually making, and why no bus can supply it

Stated precisely, the old fold assumed *"no `DocumentExtracted` ever follows a
merge that touched its entities."* That is a claim about what the write side
emits, not about delivery. No ordering guarantee, no exactly-once bus, and no
amount of checkpoint discipline can supply it, which is why the fix could not
live in the delivery layer.

## Decision: the store records merges, and the extraction fold reads that record

### Root cause was `GraphStore`'s shape, not the handler's logic

The handler could not distinguish "this edge belongs to an entity that has
since been absorbed" from "this edge is new", because **the store had nowhere
to record that a merge had happened.** Any fix written inside the handler would
have had to reconstruct that fact, and the only place to reconstruct it from is
the event log — which is to say, from a projection the projection does not
have.

### `upsert_alias` / `remove_alias` / `resolve_entity_ids` are that somewhere

`GraphStore` gained three alias operations
(`ports/graph_store.py`). `upsert_alias` and `remove_alias` are how the merge
and undo folds maintain the record; `resolve_entity_ids` is the read a fold
makes before writing an edge. An id that is not an alias maps to itself, so
every requested id appears in the result and the caller looks up
unconditionally.

`GraphProjection._resolved` is the reader. It is not consolidation logic
leaking into the store: the store is being given somewhere to put a fact that
already happened.

### One batch resolution per document, not one call per edge

`_resolved` collects both endpoints of every relationship in the event, makes a
single `resolve_entity_ids` call, and rewrites the edges from the returned map.
The port's batch shape exists for this caller — per edge it would be two round
trips each, against a store that may be Neo4j across a network.

## Consequence: resolution is transitive

### Chains are legal, so resolution must follow them

`ConsolidationLog` refuses to merge *into* an alias — that is what stops cycles
— but it does not refuse to merge a canonical entity away. So `B -> A` followed
by `A -> C` is a legal pair of merges, and an edge on `B` belongs on `C`.
`resolve_entity_ids` is therefore transitive.

### Why that terminates, and why `find_aliases` stays direct-only

A cycle would need some merge to name an alias as its canonical, which the
write model refuses. **Adapters are required to bound the walk anyway**, and
the port says so rather than leaving it to taste: this is a loop whose exit
depends on adapter-supplied data, and an unbounded one turns a corrupt row into
a hang — which in CI reads as infrastructure trouble and gets retried instead
of investigated. Both adapters detect the cycle and raise `AliasCycleError`
naming the id and tenant, but they get there by different means, and the
difference is worth knowing before you write a third adapter:

- `InMemoryGraphStore` walks alias to canonical at most `len(aliases) + 1`
  times and treats falling off the end as a cycle. The bound is the alias
  count rather than a visited set because a walk longer than that has
  necessarily revisited a node; the `+ 1` keeps a chain that uses *every*
  alias resolving normally.
- The Neo4j adapter cannot walk in Python at all — the chain is one
  `[:ALIAS_OF*1..]` match. Cypher's relationship-uniqueness rule ends the
  traversal on a cycle by returning *nothing*, which is the dangerous answer,
  because "no chain end" and "not an alias" arrive as the same empty result.
  The query therefore also returns `EXISTS { (a)-[:ALIAS_OF]->() }`, and an id
  that has an outgoing alias edge but no chain end is the cycle case. Without
  that second flag the adapter would silently resolve a corrupted id to
  itself, which is a wrong answer rather than a loud one.

The shared rule for any future adapter is the one the port states: never let a
malformed row decide how long the walk runs, and never let "cycle" and "not an
alias" produce the same value.

`find_aliases` deliberately does **not** follow chains: `find_aliases(A)` after
`B -> A` and `C -> B` returns `B` alone, not `[B, C]`. The port says so, both
adapters implement it that way — the Cypher is one hop, not `*1..` — and
`tests/compliance/graph_store.py::test_find_aliases_is_direct_and_ordered`
pins it for every adapter.

The asymmetry is not an inconsistency, because the two methods answer different
questions. `resolve_entity_ids` asks *"which entity now stands for this id"*,
and only the end of the chain does. `find_aliases` asks *"which entities did
**this** merge absorb"* — the question an undo asks, and the one
`tests/unit/consolidation/oracle.py` reconstructs a merge from. A transitive
`find_aliases` would make that unanswerable: undoing `C -> B` would find `A`'s
absorbed set contaminated with an entity `A`'s merge never touched, and the
undo would restore endpoints belonging to a merge still in force.

## Consequence: an edge that collapses to a self-loop is deleted, not written

### There is no value `_resolved` could return meaning "and this one must go"

An edge can have *both* endpoints absorbed by the same merge — `first -> second`
where both were merged into `canonical`. Resolution maps both to the same id,
and the edge collapses.

`_resolved` deletes it through `delete_relationship` and `continue`s, rather
than returning a marker the caller must interpret. That is not a style choice:
`Relationship._reject_self_loops` (`domain/relationship.py`) refuses at
construction to build one whose endpoints are equal, so **there is no
`Relationship` value carrying the outcome**. The return type is
`list[Relationship]`, and the collapsed edge has no representative in it.

The three alternatives were all worse. Returning the edge unresolved would
write pre-merge endpoints back, which is the defect this ADR exists to fix.
Widening the return type to `list[Relationship | Something]` pushes the same
decision onto every future caller of a private helper with one caller.
Constructing the self-loop and letting pydantic raise sends a perfectly
ordinary `DocumentExtracted` to the DLQ — a re-extraction of an
already-consolidated document would fail, which is exactly the workflow
`Document.record_extraction` is keyed by model version to support.

### The originating merge already recorded `after=None` for that edge

Deleting is not a new decision, it is agreement with one already taken on the
write side. `plan_redirections` (`consolidation/planning.py`) drops an edge
whose endpoints are both absorbed, because it "would be a self-loop on `A`",
and records that as a `RelationshipRedirection` with `after=None` rather than
by omitting it — an omitted edge is indistinguishable from one the merge never
saw, and undo would have nothing to recreate it from. `_apply_merge` deletes
it from the store when it applies the event.

So by the time the re-extraction arrives, the edge is already gone from the
graph and already recoverable from the log. The fold deleting it again is
idempotent agreement, not a second policy. (The same `after=None`
representation covers a second case, parallel-edge deduplication, which
resolution never reaches: those edges do not collapse, they duplicate.)

`tests/unit/projections/test_aliases_survive_re_extraction.py::TestAnEdgeThatCollapsesOntoOneEntity`
pins it, in two tests that are both load-bearing. The first is a control which
projects the log *without* the re-extraction: without it, the second could pass
because the edge was never written rather than because it was dropped and
stayed gone. The second asserts `report.failed == 0` alongside the empty edge
set, because "deleted" and "poisoned" are otherwise indistinguishable — a fold
that tried to construct the self-loop would fail the event, and an absent edge
looks the same either way.

Replay is pinned separately, by the `dropping-merge` and
`undo-of-dropping-merge` scenarios in the `PINNED` set of
`tests/unit/projections/test_replay_equivalence.py`. It needs its own coverage
because this is the one path through `_resolved` that does not end in an
upsert, and a delete is the write that a replay can most easily disagree about.

## Consequence: alias ids are `uuid5`-derived

### Why not `uuid4` — replay must produce the same alias rows for the same log

`_alias_id` (`projections/graph.py`) is a pure function of the tenant and the
absorbed entity id:

```python
def _alias_id(tenant_id: TenantId, alias_entity_id: EntityId) -> UUID:
    return uuid5(NAMESPACE_OID, f"redstring:alias:{tenant_id}:{alias_entity_id}")
```

The obvious alternative is `uuid4()` at the point `_apply_merge` builds the
`Alias`, and nothing in the fold would notice: the id is written, never read
back, and no query joins on it. What notices is a *second* run of the fold.
Projecting the same log twice — a rebuild, a resume from a checkpoint, a wipe
and replay — would mint a new id each time, so the graph after a replay would
differ from the graph before it in a value that is supposed to be a function of
the log. That is precisely the property [rebuilding a
projection](../how-to/rebuild-a-projection.md) tells operators they have, and
it is what the replay-equivalence suite exists to forbid.

The reason a `uuid4` is tempting here and correct in most places is worth
naming: a `uuid4` is fine for a value the *write side* mints once and the log
then carries — an event id, an entity id — because the log preserves it. It is
wrong for a value the *read side* mints, because the read side is rebuilt on
demand and its output must be a function of its input. `Alias` is a read-model
row, so its id is derived rather than drawn.

### Why the merge event id is deliberately excluded from the hash — the row is keyed `(tenant_id, alias_entity_id)` in every adapter

`_apply_merge` has the `EntitiesMerged` event in hand and could hash its id in.
It does not, and the reason is that the pair `(tenant_id, alias_entity_id)`
already *is* the row's identity in both adapters:

- `InMemoryGraphStore` stores aliases as `dict[TenantId, dict[EntityId, Alias]]`
  keyed on the **alias** id, not the canonical one, because an entity has at
  most one canonical parent while a canonical may have many aliases.
- The Neo4j adapter carries a uniqueness constraint over the `:AliasRef` node's
  `(tenant_id, entity_id)`, and `upsert_alias` MERGEs on exactly that pair.

That "at most one canonical parent" is the same fact `ConsolidationLog`
enforces on the write side by refusing to merge an entity that has already been
merged away. Since the pair is the identity, a second merge naming the same
absorbed entity must overwrite the same logical row — and hashing the event id
in would give that row a different `id` depending on which merge last wrote it,
while both adapters continued to treat it as one row. The result is not a
duplicate row but something worse: one row whose surrogate id disagrees with
itself across replays, and across adapters if their upsert paths ever differ in
whether they rewrite the property.

Derive the id from the key, in other words, or do not have one.

### What `test_two_halves_project_to_the_same_state_as_one_whole` would catch

The failure a merge-id-derived alias id produces is not the one you would
guess. It survives a single-log replay: the same log carries the same event
ids, so hashing them in still yields the same alias ids on every pass, and the
replay-equivalence scenarios stay green. What breaks is comparing **two
independently built logs of the same scenario** — the ids differ because the
event ids differ.

`tests/unit/projections/test_checkpoints.py::test_two_halves_project_to_the_same_state_as_one_whole`
does exactly that comparison, deliberately: it builds the merge scenario twice
so the two rigs have independent checkpoint state, projects one whole and the
other in two passes, and asserts the dumps match. The scenario fixes every
entity id, so the only value that legitimately differs between the two logs is
wall-clock time — which is why the helper it compares through drops the alias
`merged_at` and nothing else. An alias id that varied with the merge event id
would be a second such value, and the test would fail without that being what
anyone had changed.

Two things follow for anyone editing that test. Widening the drop-list to make
a failure go away would disable this check, so a new dropped field needs the
same kind of justification the `merged_at` comment carries. And the single-log
replay-equivalence scenarios cannot stand in for this test: they hold the event
ids fixed, which is the assumption under examination.

## What this decision does *not* change

Three things about the fold look as though this decision should have touched
them. None of them changed, and each is worth stating, because the natural
assumption in every case is the wrong one.

### `MergeUndone` does not resolve, and the order of its two steps is not load-bearing (checked, not assumed)

`_apply_undo` removes the aliases and then upserts the restored relationships.
**Nothing resolves on that path**, and it must not: `restored_relationships`
carry the *pre-merge* endpoints deliberately, since restoring them is the whole
point of an undo. Resolving them would map each one straight back onto the
canonical the undo is retracting, and the undo would be a no-op. Only
`_apply_extraction` resolves, because only it handles data that predates a
merge without knowing about it — extraction emits the ids it found, and it has
no way to know a merge has happened since.

The order of the two steps therefore does not matter, which is worth saying
because it looks as though it should. An alias still in place while the
pre-merge endpoints are written seems to contradict them; it does not, because
nothing on this path consults the alias table.

That claim was **checked rather than assumed**: swapping the two statements by
hand left the whole of `tests/unit/consolidation` passing. The comment in
`_apply_undo` records the same experiment, with the count it was run at. An earlier version of that comment asserted the
order *was* load-bearing, which is how a later reader comes to believe the fold
resolves here too — a comment asserting a constraint that does not exist is
worse than no comment, because nobody re-derives it.

### Idempotency was already a port property; no second dedupe layer in the projection

Every write in the fold is an `upsert` or an idempotent `delete`. That is a
property of `GraphStore` itself, stated at the top of `ports/graph_store.py`
("Every write is idempotent. Projection handlers replay."), and it was made so
precisely **so that projections would not need a dedupe layer of their own**.

`GraphProjection` has none — no seen-event set, no sequence-number check, no
write-time comparison — and this ADR does not add one. Resolution changes
*which* endpoints an edge is written with; it does not change how many times
writing it is safe.

The property is spread across the port's methods rather than asserted once, and
each spelling matters to a different write in the fold. `upsert_entity` and
`upsert_alias` are "idempotent, last-write-wins", so a second application
leaves exactly one row holding the later value. `delete_relationship` and
`remove_alias` return `False` for something already absent rather than raising,
which is what makes the self-loop delete on the resolution path replayable at
all. And `upsert_relationships` is **explicitly not atomic** — a
`MissingEntityError` part-way through leaves earlier elements written — which
the port can afford only because each element is individually idempotent, so a
retry converges on the same final state instead of needing a rollback the
`GraphStore` interface does not offer.

Any "have I seen this event already" bookkeeping added to the projection would
duplicate a guarantee the port already owes, in the one place it cannot be
tested against every adapter: the compliance suite exercises stores, so a
projection-level dedupe would be checked against `InMemoryGraphStore` and
nothing else, while the guarantee it duplicates is checked against all of them.

What idempotency does *not* buy is worth stating in the same breath, because
conflating the two is how the original defect survived review. Idempotency
makes redelivery safe; it does not make **reordering** safe. A checkpointed
feed redelivers a contiguous suffix in order, so the last occurrence of each
event is still in log order and the final state is the log's — see [rebuilding
a projection](../how-to/rebuild-a-projection.md). A bus that could deliver
`e1, e2, e1` would break this fold no matter how idempotent each write is, and
so would the extraction-after-merge sequence at the top of this ADR, which is
in perfect order and needed the alias table rather than a stronger delivery
guarantee.

### A missing endpoint remains a poison event routed to the DLQ

`upsert_relationship` still raises `MissingEntityError` for an edge pointing at
an entity the tenant does not hold — dangling edges are not permitted, and that
is unchanged. It happens when a document references an entity from a document
not yet projected. The event goes to the DLQ and the projection carries on: not
retried into a wedge, and not silently dropped.

Resolution does not paper over it, and the distinction is exactly the one to
keep: **resolution answers "has this id been merged away", not "does this id
exist".** `resolve_entity_ids` says so in its own docstring, and an id the
tenant has never seen maps to itself for the same reason a non-alias does —
every requested id appears in the result so the caller can look up
unconditionally. The edge is then written with the ids extraction found, and
fails the upsert exactly as it did before this decision.

The alternative — having `_resolved` drop edges whose endpoints do not resolve
to anything the store holds — is the one to refuse. It would need resolution to
distinguish "unknown" from "not an alias", which is a second question the port
deliberately does not answer, and it would trade a loud, diagnosable poison
event for a graph silently missing relationships. Missing edges are the harder
failure to notice and the harder one to attribute later.

Nothing in this ADR touches how that poison event is handled, and the handling
is not local to `GraphProjection`. `StoreProjection` passes `dlq_repo` and
`retry_policy` straight through to eventsource's `DeclarativeProjection`, which
retries and writes the DLQ record; `replay_all` then counts the event in
`ReplayReport.failed` and keeps reading rather than re-raising, because
re-raising is what would wedge a rebuild on one bad event. So the operator
signal for this case is a non-zero `failed` count plus a DLQ entry — see
[rebuilding a projection](../how-to/rebuild-a-projection.md).

## Relationship to ADR 0002

### 0002 lists this as reason 2 for the absence of `delete_entity`; the dependency runs the other way too

[ADR 0002](0002-two-store-ports.md) reaches this decision from the port's side.
Its reason 2 for having no `delete_entity` is the failure at the top of this
document: a `DocumentExtracted` folded after an `EntitiesMerged` writes the
pre-merge endpoints back, and the fix requires the alias to still be there.
From 0002's angle, this ADR is the justification for a method it declines to
add.

The dependency runs the other way too, and that is the half worth stating here:
**resolution requires the absorbed entity's alias row to survive, so the fold
depends on the port's restraint exactly as much as the restraint depends on the
fold.** A `delete_entity` would not break the fold by being called on an
arbitrary entity; it would break it by making "remove the entity a merge
absorbed" an expressible operation, and the alias row is what that operation
would take with it. Neither decision is safe alone. If the port grew a delete
and the fold kept resolving, the fold would resolve against rows something else
was entitled to remove; if the port stayed as it is and the fold stopped
resolving, the alias rows would still be there and nothing would read them.

That mutual hold is why 0002's **Status** records the alias surface as an
*extension of the same decision* rather than a later amendment. The no-delete
argument was always conditional on something keeping the merge fact durable,
and it was aspirational until something depended on it: a projection that
quietly dropped the absorbed row would pass every test about entities and
relationships, because the damage only shows up in the *next* extraction fold.
Resolution is what makes the dependency structural instead — `_resolved` cannot
write an edge without calling `resolve_entity_ids`, `resolve_entity_ids` cannot
answer without the alias rows, and an adapter that discards them fails the
alias tier of `tests/compliance/graph_store.py` rather than a code review.

So the two documents are one decision written from two sides, and the division
of labour between them is deliberate. **0002 owns the port**: which methods
exist, why `delete_entity` is not among them, what an adapter owes — see
[implementing a store adapter](../how-to/implement-a-store-adapter.md) for the
implementer's side of `resolve_entity_ids`. **This ADR owns the fold**: which
handler resolves and which deliberately does not, that resolution is batched
per document, and that a collapsed edge is deleted rather than upserted. A
change to either side has to be checked against the other; in particular,
anything that would let an absorbed entity's row disappear is a change to 0002,
not a local edit to `projections/graph.py`.

## Consequences and open items

- **`BACKLOG` B34 is closed.** It is pinned by
  `tests/unit/projections/test_aliases_survive_re_extraction.py` — formerly
  `test_known_gaps.py`, whose assertions asserted the code was wrong and were
  inverted rather than deleted — and by the `merge`, `dropping-merge`, `undo`
  and `undo-of-dropping-merge` scenarios in the `PINNED` set of
  `tests/unit/projections/test_replay_equivalence.py`.
- **`BACKLOG` B32 is still open.** Nothing here lets a re-extraction *remove*
  an entity an earlier run found, so the graph still converges on the union of
  every extraction run rather than on the latest one. Resolution fixes
  endpoints, not membership. See
  [consolidating duplicate entities](../how-to/consolidate-duplicate-entities.md)
  for what merging does and does not undo.
- **What a future author must preserve.** If you touch `_resolved`: keep the
  resolution batched, keep the self-loop case a delete, and keep the resolution
  on the extraction path only. If you touch `_alias_id`: keep it a pure
  function of `(tenant_id, alias_entity_id)`. Both constraints are tested, and
  both are the kind that pass review while being quietly weakened —
  [ADR 0001](0001-event-log-schema-and-granularity.md) records that this whole
  defect was in the read model's shape, which is why the events did not change
  to fix it and must not change to fix its successor.
