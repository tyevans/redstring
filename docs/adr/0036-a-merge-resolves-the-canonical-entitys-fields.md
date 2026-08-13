# ADR 0036: A merge resolves the canonical entity's fields

## Status

Accepted.

**Amends [`0001` event log schema and granularity](0001-event-log-schema-and-granularity.md).**
`EntitiesMerged` gains a field resolution and goes to `event_version = 2`;
`MergeUndone` gains its restoration. 0001's decisions stand — the granularity,
the aggregates, and the rule that every event declares its own version rather
than inheriting one.

[`0004` consolidation emits events](0004-consolidation-emits-events.md)
**stands, and is exercised further**: the resolution is decided once, in
`ConsolidationService.merge`, and recorded on `EntitiesMerged` rather than
recomputed by `GraphProjection`. This is the same rule 0004 already applies to
`redirections`, applied now to entity data.

[`0035` provenance is a value object](0035-provenance-is-a-value-object.md)
**stands, and its deferred consequence is what this ADR closes.** 0035 left
`resolve` and `claims_for` implemented and unreached, and its Consequences
said as much: "the merge-strategy names stay unexported... when consolidation
reaches them, the export becomes a decision someone takes on purpose." This is
that decision.

[`0002` two store ports](0002-two-store-ports.md) **stands** — `GraphStore`
still has no `delete_entity`, which is why the effect of a merge on entity
data is a single before/after pair on the canonical entity rather than
anything touching the absorbed rows.

[`0006` the public surface is gated](0006-the-public-surface-is-gated.md)
**stands, and is exercised**: `PropertyMergePolicy`, `PropertyMergeStrategy`,
`MergeableFields` and `PropertyResolution` enter `__all__` because
`Consolidator.__init__`'s `merge_policy=` parameter and `EntitiesMerged`'s
signatures now mention them. `ConsolidationService` itself is not exported —
`Consolidator`, in `composition/build_graph.py`, is what the signature gate
actually sees.

## Context

Consolidation absorbs duplicate entities but, until now, only ever discarded
what they knew: `GraphProjection` writes an alias for each absorbed entity and
nothing else, and the canonical entity's `description`, `external_ids` and
`properties` pass through a merge unchanged. `domain/merge_strategy.py`
already had four working strategies and a fifth that raises on purpose
(`DEEP_MERGE`, BACKLOG B28) — `resolve` and `claims_for` were fully typed and
fully tested, with no caller outside `tests/` (BACKLOG B127).

Wiring them up needed three things `resolve`'s signature alone could not
supply: a way to say *which* strategy applies to *which* field, a place to
record the decision so a projection applies it instead of recomputing it, and
an undo path that can put the pre-merge values back.

## Decision 1: a merge decides fields only on the canonical entity

A merge does not touch the entities it absorbs. `GraphStore` has no
`delete_entity`, the projection writes an `Alias` per absorbed entity and
nothing else, and those rows survive unchanged. The whole effect of a merge
on entity data is therefore one before/after pair on the canonical entity —
`description`, `external_ids` and `properties`, the fields
`domain.merge_strategy.MERGEABLE_FIELDS` names. `name`, `entity_type` and
`temporal` are deliberately absent: preference between whole entities is
already ADR 0010's `domain.preference`, and re-deciding `name` here would
give one question two answers.

An earlier version of this design asked for every absorbed entity's original
values as well, reasoning that a `UNION` result cannot say who claimed what.
True, and not needed: nothing downstream has a row to put those values back
into, since the absorbed entities are never rewritten.

## Decision 2: the decision is recorded, not recomputed

`EntitiesMerged.resolution` carries the complete post-merge value of all
three fields — a literal snapshot, not a diff, so a key omitted from `after`
is a key deleted and the projection replaces the fields wholesale. Recomputing
the resolution on read would need the pre-merge graph, which the projection
has already overwritten by the time it applies the event, and would let the
read side make a decision the write side is supposed to own. This is 0004's
rule applied to entity data exactly as `redirections` already applies it to
edges: the plan is computed once, before anything is emitted, and the fold
only replays it.

`MergeUndone.restored_fields` is the mirror image, for the same reason
`redirections` needs `before`: an omitted restoration is indistinguishable
from "nothing to restore," and undo has no other source for the pre-merge
values once the log is the only place they still exist.

## Decision 3: strategy selection is a policy keyed by dotted path

`PropertyMergePolicy` holds a `default` strategy and per-path `overrides` —
`description`, `properties`, `properties.<key>`, `external_ids.<key>`.
`strategy_for` resolves exact path, then the field's own default, then the
policy default, in that order, and that order is the whole content of the
type. A single flat default would not let a caller keep the canonical
description while unioning one property key; a policy with no exact-path tier
would not let a caller override just that key without also overriding every
other key of the same field.

## Decision 4: `UNION` outside `properties` is refused at construction

`external_ids` is `dict[str, str]` and `description` is `str | None`; a list
type-checks against neither. `PropertyMergePolicy` refuses `UNION` on any
field but `properties` when it is built, because the alternative failure is
worse: a pydantic error raised inside a fold, with the event already durable
and no way to make progress. Refusing at the point a caller wires up a
service is the only point at which the mistake is cheap. `DEEP_MERGE` is
**not** refused the same way — it still raises from `resolve` at plan time,
before any event exists, and encoding "which strategies are implemented" a
second time in the policy's validator would give that question two answers,
with the newer one nobody remembers to update.

## Decision 5: a `None` description is silence; an explicit `None` property value is a claim

`claims_for` treats the two field shapes differently on purpose. `properties`
and `external_ids` are dicts: an entity whose dict lacks a key is skipped,
because treating absence as a claim would let an entity with no opinion
outvote one with an opinion under `MOST_RECENTLY_OBSERVED` merely by being
newer, while an entity whose dict holds an explicit `None` for a key *is*
kept, because it said something. `description` is a scalar field that always
exists on `Entity`, so there `None` means the entity is silent, not that it
claimed nothing — and is skipped. The asymmetry is real rather than an
inconsistency: a reader who has internalised the dict rule will expect the
opposite for `description`, which is why it is stated here rather than left
to be rediscovered from the two call sites.

## Consequences

**`EntitiesMerged` goes to `event_version = 2`; `MergeUndone` stays at
`event_version = 1`.** `resolution` is optional on the type for replay
compatibility with events already in a caller's log, but every merge from
this decision forward writes one. Both events gained an optional field that
does not invalidate a pre-existing payload, so either could defensibly have
been bumped or left alone. The rule this branch follows: **bump when the new
field is the primary subject of the change** — a merge's resolution is the
decision this ADR adds, so `EntitiesMerged`'s version number should say a
reader inspecting the log can now expect it — **and leave the version alone
when the field is a derived consequence of an already-versioned sibling** —
`MergeUndone`'s `restored_fields` only exists because `EntitiesMerged` can now
carry a `resolution` to restore, so its presence is implied by reading the
merge it undoes rather than by `MergeUndone`'s own shape changing in a way a
version number needs to advertise. This is a judgment call about which event
a schema change is "about", not a mechanical rule; the next asymmetric field
addition should make the same call explicitly rather than copying whichever
of the two it read first.

**BACKLOG B127 is closed.** `resolve` and `claims_for` now have a production
caller: `ConsolidationService.merge` reads the group's entities, calls
`consolidation/planning.py`'s `plan_properties`, and emits the resolution on
`EntitiesMerged`; `GraphProjection._apply_fields` applies it from both the
merge and the undo handlers.

**BACKLOG B28 is narrowed, not closed.** `DEEP_MERGE` remains deferred for
its own reason, unrelated to the signature change: nested-dict semantics are
easy to get subtly wrong, and a wrong deep merge is effectively unrecoverable
because the pre-merge shape is not derivable from the result.

**A merge policy has no per-call override on `Consolidator`.**
`ConsolidationService.merge` takes a `policy` argument for a single call, but
nothing above it — `Consolidator.merge` and `Consolidator.resolve` — exposes
the same override yet. Tracked as BACKLOG B131 rather than closed here.
