# A merge resolves the canonical entity's fields

**Status:** approved, ready to plan
**Closes:** BACKLOG B127, and the "nothing calls the rest" half of B28
**Related:** ADR 0001, 0004, 0006, 0010, 0035; new ADR (draft 0036)

## The problem, stated exactly

`domain/merge_strategy.py` implements four strategies over `PropertyClaim`s,
with a total order and a claim constructor, and **no production code calls
any of it**. `consolidation/service.py` merges edges only: the canonical
entity keeps its own properties and the absorbed entities' are discarded.

That is `.claude/rules/recurring-defects.md` §3 — code with no caller — held
open deliberately and recorded as B127. This spec closes it by giving
`resolve` its one production caller, through the write side, so the decision
lands in the log rather than being recomputed on read.

## Two corrections to B127's own framing

Both change the shape of the work, so they are recorded before the design
rather than discovered inside it.

### The undo payload is one entity, not many

B127 says the absorbed entities' original values have to be in the log
"because a resolved value does not contain them", and names `UNION` as the
case that makes it obvious — you cannot tell from `[a, b]` who said what.

**You never need to.** A merge does not touch the entities it absorbs.
`GraphStore` has no `delete_entity` (ADR 0002), `GraphProjection._apply_merge`
writes an `Alias` per absorbed entity and nothing else, and the absorbed rows
survive unchanged. The only entity whose data a merge changes is the
canonical one. So the record of what a merge did to entity data is exactly
`RelationshipRedirection`'s shape — a `before` and an `after` on one thing —
and undo restores it by upserting `before`.

This is not a simplification taken for convenience; it is what the read model
already does. A payload carrying every absorbed entity's originals would be
carrying data no handler could use.

### The obstacle to `PREFER_MERGED` and friends was never the timestamp

Already recorded in ADR 0035 and repeated here because it sets the scope: the
strategies resolve now because `resolve` takes claims rather than bare values.
Nothing in this spec adds a new time source, a per-property edit history, or
any data to `Provenance`. It wires an existing decision into an existing
pipeline.

## Design

### 1. What a merge decides

Three targets on the canonical entity:

| Target | Type | Shape |
|---|---|---|
| `description` | `str \| None` | scalar |
| `external_ids` | `dict[str, str]` | per key |
| `properties` | `dict[str, Any]` | per key |

`name`, `entity_type` and `temporal` are **out of scope**. Preference between
whole entities is ADR 0010's `domain.preference`, which already orders on
`name`, `description` and `temporal`; re-deciding `name` per merge would put
two answers to one question in the codebase. `description` appears in both and
that is deliberate — `preference` uses it to order *entities*, this resolves
its *value*, and neither reads the other's result.

### 2. Naming a target: a dotted path

One key space so a scalar field and a dict key share a single lookup.

| Path | Means |
|---|---|
| `description` | the scalar field |
| `properties` | the default for every key of `properties` |
| `external_ids` | the default for every key of `external_ids` |
| `properties.role` | that one key |
| `external_ids.wikidata` | that one key |

```python
class PropertyMergePolicy(BaseModel, frozen=True):
    default: PropertyMergeStrategy = PropertyMergeStrategy.PREFER_CANONICAL
    overrides: Mapping[str, PropertyMergeStrategy] = {}

    def strategy_for(self, path: str) -> PropertyMergeStrategy: ...
```

`strategy_for` resolves **exact path → field default → policy default**, and
that order is the whole content of the type. A path with no dot has no field
default to fall back to, so it consults `overrides` once and then `default`.

An unknown top-level field in `overrides` is a construction-time error. A
policy naming `properities.role` (typo) would otherwise be silently inert —
every merge would apply the default and nothing would say so. This is the
exemption-list rule from `CLAUDE.md` in a different costume: a mapping entry
that matches nothing must fail rather than pass.

### 3. `UNION` is refused outside `properties`, at construction time

`external_ids` is `dict[str, str]` and `description` is `str | None`. `UNION`
returns a `list`, which type-checks against neither, and pydantic would raise
at the moment the projection tried to upsert the entity — inside a fold,
after the event is durably in the log, with no way to make progress.

So `PropertyMergePolicy` **refuses at construction**: `UNION` is legal only on
`properties` and `properties.*`. The failure then arrives when a caller wires
up a service, before any data has moved, which is the only point at which it
is cheap.

Refusing rather than coercing, for the same reason `DEEP_MERGE` raises rather
than falling back: writing a joined string into `description` because the
caller asked for `UNION` would corrupt data while looking like it worked.

### 4. Silence, and why `description` differs

`claims_for` skips an entity whose `properties` lack the key — silence is not
an assertion, and treating it as one lets an entity with no opinion outvote
one with an opinion under `MOST_RECENTLY_OBSERVED` merely by being newer. An
explicit `None` value *is* a claim and is kept.

`description` has no such distinction: the field always exists, and `None` is
its absence. **A `None` description is therefore silence and is skipped.**
The asymmetry is real, it is not an inconsistency, and it must be stated where
it is implemented — a reader who knows the dict rule will otherwise expect a
`None` description to win a recency contest.

`external_ids` values are `str`, so its keys follow the `properties` rule
unchanged: present means claimed.

### 5. Payload

In `domain/consolidation.py`, beside `RelationshipRedirection`:

```python
class MergeableFields(BaseModel):
    """Exactly the fields a merge may decide."""

    description: str | None = None
    external_ids: dict[str, str] = {}
    properties: dict[str, Any] = {}


class PropertyResolution(BaseModel):
    """What a merge decided about one entity's fields, before and after."""

    entity_id: EntityId
    before: MergeableFields
    after: MergeableFields
```

`EntitiesMerged` gains `resolution: PropertyResolution | None = None` and goes
to **`event_version = 2`**.

Optional rather than required, and the reason is about who can derive it:
`ConsolidationLog` holds no entity data, so it cannot compute a resolution and
must be handed one. A parameter the aggregate cannot validate should not be
mandatory by type when its absence is a true state — "this merge decided
nothing about fields" is exactly what a direct aggregate caller means. The
*service* always supplies one, which is what makes the path production code
rather than more §3 material.

`resolution.entity_id` must equal `canonical_entity_id`; validated on the
event, because a resolution naming an absorbed entity would have the
projection overwrite the wrong row and undo restore it, and nothing
downstream could tell.

`MergeUndone` gains `restored_fields: MergeableFields | None = None`, derived
by replay from `MergeRecord` exactly as `restored_relationships` already is
(ADR 0001 Decision 6). The aggregate stores the resolution on `MergeRecord`
when it folds `EntitiesMerged`, and hands `before` back when asked to undo.

### 6. Planning, on the write side

`consolidation/planning.py` gains a sibling to `plan_redirections`:

```python
def plan_properties(
    *, policy: PropertyMergePolicy, canonical: Entity, others: Sequence[Entity]
) -> PropertyResolution
```

It walks the union of keys across the group for each dict field, calls
`claims_for` and then `resolve` per path, and assembles `before` from the
canonical entity as read.

**`after` is the complete post-merge value of all three fields, not a diff.**
The projection replaces them wholesale, so a key omitted from `after` would be
a key *deleted* — which is why a resolution must be exhaustive over the union
rather than over what changed.

That makes an empty claim list unreachable for a dict key by construction: the
key is in the union because at least one entity has it, so at least one entity
claims it. The one path where `claims_for` can return `[]` is `description`,
when every entity in the group has `None` — and `after.description` is then
`None`, which is that field's absence and the same value `before` held.
`resolve` is not called with an empty list; the guard is not defensive
programming but the only correct answer for a field nobody described.

`ConsolidationService.merge` reads the group's entities (one
`get_entities` call — `EntityReader.get_entities` is already on
`ConsolidationGraph`, so no protocol changes), plans, and passes the
resolution to `log.merge()`. Read-plan-emit, the same shape every other method
there has, and no store write.

The canonical entity missing from that read raises `MissingEntityError`. It is
the same inconsistency `_resolved_subject` already names: a merge whose
canonical has no row means the log and the graph disagree, not that a read
missed.

### 7. Applying it

`GraphProjection._apply_merge` upserts the canonical entity with `after`;
`_apply_undo` upserts it with `before`. Both are literal snapshots rather
than recomputations, so applying either twice leaves the same state — the
projection's idempotency requirement holds by construction rather than by
argument, and replay produces the identical entity row.

A canonical entity absent from the store on either path raises
`MissingEntityError`, which the module docstring already describes as a poison
event routed to the DLQ. Skipping silently would drop the decision with
nothing to notice.

No store adapter changes: `description`, `external_ids` and `properties`
already round-trip through both `GraphStore` implementations.

### 8. Public surface

`ConsolidationService` is not exported. `Consolidator.__init__` (the
composed entry point, `composition/build_graph.py`) is what mentions
`PropertyMergePolicy` in its signature, and the signature gate walks the MRO,
so the policy enters `__all__` and pulls `PropertyMergeStrategy` with it.
`EntitiesMerged` and `MergeUndone` are already exported, so
`PropertyResolution` and `MergeableFields` follow.

B127 predicted this ("the strategy names get exported at that point rather
than now"). ADR 0006 is exercised, not amended.

## Out of scope

- **`DEEP_MERGE`** — keeps its own reason (B28), untouched by anything here.
  A wrong deep merge is unrecoverable because the pre-merge shape is not
  derivable from its result, and this spec does not change that.
- **B128, `Relationship` provenance** — nothing in property merging reads a
  relationship's time. Redirections carry whole `Relationship`s and undo
  restores them verbatim, so adding `observed_at` here would ship another
  unreached field, which is the defect this work exists to close.
- **Strategy selection from `DomainSchema`** — ADR 0011 says a schema shapes
  the prompt and does not constrain; letting it decide merge semantics is a
  different decision needing its own argument.
- **`name`, `entity_type`, `temporal`** — see §1.

## Against the existing ADRs

| ADR | Verdict |
|---|---|
| **0001** event log schema and granularity | **Amended.** Two payloads gain a field and `EntitiesMerged` goes to `event_version = 2`. Decision 6 (an undo carries its restoration) is extended to entity fields rather than changed. |
| **0002** two store ports | **Stands.** No port change; the absence of `delete_entity` is what makes the one-entity payload correct. |
| **0004** consolidation emits events | **Stands, and is exercised for the first time.** A projection now applies a decision it did not make, which is the arrangement 0004 exists to produce. |
| **0006** the public surface is gated | **Stands, exercised.** The policy's closure enters `__all__` through the signature gate, exactly as 0035's did. |
| **0010** one total order for preference | **Stands.** `_order_key` composes with `duplicate_preference` rather than competing; nothing here reorders entities. |
| **0011** domain schemas prompt but do not constrain | **Stands.** Explicitly out of scope above. |
| **0035** provenance is a value object | **Stands.** This consumes `Provenance`; it adds nothing to it. |
| **new (draft 0036)** | A merge resolves the canonical entity's fields, and the event carries before and after. Renumber against `main` at merge. |

## Verification

- Full unit suite green through the pre-commit gate on every commit.
- **A deliberate break per new decision**, watched to fail: delete a
  `PropertyMergePolicy` fallback tier, invert the `None`-description skip,
  swap `before` for `after` in the undo handler. A green break is a finding
  about the fixture, not a pass.
- **Merge-then-undo round-trips the canonical entity's fields exactly**, with
  the expectation recorded independently of the projection rather than
  produced by it — the replay-equivalence lesson in `CLAUDE.md`: both sides of
  a self-consistency property agree on the same wrong state when the fold does
  too little.
- **Test inputs where candidate implementations disagree.** A group of three
  entities, not two, so `PREFER_MERGED` and `MOST_RECENTLY_OBSERVED` are
  distinguishable. Pinned `EntityId`s where an order's tie-break is under test,
  never `uuid4()` — that is the row `CLAUDE.md` gained from the previous
  slice, and it fired while checking the work rather than while doing it.
- Neo4j is unchanged and needs no run to be believed, which is the one claim
  the previous slice could not make.

## Backlog

Deleted on completion: **B127**. **B28** shrinks again to `DEEP_MERGE` alone
and loses its "nothing calls the rest" half. **B128** is untouched and stays.
