# Property provenance, and making `LATEST` a question with an answer

**Status:** design, approved 2026-08-12. Clean break sanctioned by the caller:
there is no persisted log to migrate.

## The problem, restated correctly

`domain/merge_strategy.py` refuses `LATEST` with this reasoning:

> `LATEST` is not merely unimplemented, it is currently *unanswerable*.
> Timestamps are per entity, not per property, so "the most recently updated
> value" has no data behind it.

The refusal is right and the reason is wrong, in a way that matters. There are
no per-entity timestamps either. `Entity` has no time field at all
(`domain/entity.py:36-71`), and the only record-time value anywhere in the
library is `Alias.merged_at` (`domain/alias.py:42`), materialised from
`EntitiesMerged.occurred_at`. `TemporalExtent` holds *world* time — when a fact
held — which is a different clock and cannot stand in.

So the docstring understates its own case, and by naming the wrong obstacle it
points at the wrong fix. "Add a per-property timestamp" is not the smallest
honest change, and it is not even the change that makes `LATEST` answerable.

## The actual root cause is the signature

```python
def resolve(strategy, *, canonical: Any, others: list[Any]) -> Any
```

`resolve` receives bare *values*. Nothing about where a value came from, when,
or how confidently, survives the call. Every strategy that needs more than the
value itself is unanswerable **by construction**, and `LATEST` is simply the
first member of the enum to notice. `PREFER_MERGED` looks "trivial to
implement" (BACKLOG B28's word) only because it happens to need nothing extra;
`DEEP_MERGE` is hard for an unrelated reason.

This reframes the work. The deliverable is not a timestamp. It is a value
object that carries a value together with the observation that produced it, so
that a strategy can be *stated* rather than approximated.

## What "latest" actually means here

Each `Entity` is the product of one extraction of one document. A merge
combines several such entities, each from its own observation. So the
answerable question is:

> Of the entities that assert this property, which was **observed** most
> recently, and what value did it assert?

That is not "when was this property last updated" — nothing in this library
tracks a property's edit history, and this design does not add one. It is a
weaker and genuinely available claim, and the naming must say so or the next
reader will assume the stronger one. The enum member is therefore renamed:

| Was | Becomes |
|---|---|
| `LATEST` | `MOST_RECENTLY_OBSERVED` |

The wire value changes from `"latest"` to `"most_recently_observed"`.
`tests/unit/test_enum_values_are_a_wire_format.py` pins it.

**A rename is the point, not incidental tidying.** `LATEST` invites a caller to
believe the library knows a property's modification time. It does not and will
not. A name that promises less is the difference between a strategy and a
misrepresentation.

## Design

### 1. `domain/provenance.py` — a new value object

Five fields move off `Entity` into `Provenance`, plus one new one:

```python
class Provenance(BaseModel):
    observed_at: datetime  # NEW. tz-aware, required.
    extraction_method: ExtractionMethod
    confidence: float
    source_id: SourceId | None = None
    source_text: str | None = None
    model: str | None = None
```

`Entity` gains `provenance: Provenance` and loses `extraction_method`,
`confidence`, `source_id`, `source_text`, `model`.

**Why these five and not others.** They answer one question between them —
*what observed this, when, how, from where, and how sure* — and none of them is
a property of the entity as a thing in the world. `name`, `entity_type`,
`description`, `properties` and `temporal` are claims *about the thing*;
these are claims about the *claiming*. That is the seam, and it is why
`normalized_name` and `blocking_keys` stay put despite being derived: they are
derived from the thing, not from the observation.

`ExtractionMethod` stays in `domain/entity.py`. It is imported by `Provenance`;
moving it would churn every importer for no gain.

**Two validators move with the fields**, because they are invariants of the
observation and were only ever on `Entity` for want of somewhere better:

- `confidence` in `[0.0, 1.0]`.
- `model` may only be set when `extraction_method` invoked one
  (`LLM` or `HYBRID`). This is the rule `_reject_model_without_a_model_call`
  enforces, and it is *entirely* about provenance — it never mentions another
  `Entity` field.

**One new validator**, modelled on `Alias._require_timezone`: `observed_at`
must be timezone-aware. A naive datetime compared against an aware one raises
`TypeError` at the moment of comparison, which for `MOST_RECENTLY_OBSERVED`
means deep inside a merge rather than at construction.

`observed_at` is **required**. That is what makes
`MOST_RECENTLY_OBSERVED` answerable by construction rather than
conditionally — no `None` branch, no "unprovenanced" error class, no strategy
that works for some callers and refuses for others. The cost is that every
`Entity` construction site must supply it and no already-persisted
`DocumentExtracted` payload will validate. Both are accepted: `DocumentExtracted`
bumps to `event_version = 2`, and there is no data to migrate.

**Why not a `Provenance` on `Relationship` too.** Symmetry is tempting and
would be wrong here. `Relationship` has `confidence` and `source_id` but no
`extraction_method` and no `model`, so its provenance is a *different shape* —
sharing the type would mean either three optional fields that are always
absent, or a base class earning its keep on two subclasses. The asymmetry is
real, and BACKLOG B76 already tracks the relationship-provenance gap on its own
terms. Recorded rather than papered over.

### 2. `domain/merge_strategy.py` — claims, not values

```python
class PropertyClaim(BaseModel):
    value: Any
    provenance: Provenance
    origin: EntityId

def resolve(strategy: PropertyMergeStrategy, claims: Sequence[PropertyClaim]) -> Any
```

`claims` is non-empty and `claims[0]` is the canonical entity's; the rest are
the absorbed entities' in the order the merge listed them. Positional rather
than a `canonical=`/`others=` pair because every strategy but
`PREFER_CANONICAL` treats them as one ordered sequence, and two parameters
force each one to re-splice them.

Strategy semantics:

| Strategy | Result |
|---|---|
| `PREFER_CANONICAL` | `claims[0].value` |
| `PREFER_MERGED` | `claims[1].value`, or `claims[0].value` when nothing was absorbed |
| `UNION` | every distinct value, canonical first, flattening one level — unchanged |
| `MOST_RECENTLY_OBSERVED` | the value of the claim greatest under the order below |
| `DEEP_MERGE` | still raises, still naming B28 |

`PREFER_MERGED` becomes implementable for free, which is the signature change
paying for itself: it was never hard, it was only ever ill-defined about *which*
merged entity when there are several. "The first the merge listed" is a rule
the caller controls and a projection can replay.

`DEEP_MERGE` stays deferred deliberately. B28's reason still holds and is not
addressed by anything here: a wrong deep merge is hard to undo because the
pre-merge shape is not recoverable from the result. Nothing in this design
makes that safer, so implementing it would be scope creep with a data-loss
tail.

### 3. The order behind `MOST_RECENTLY_OBSERVED` must be total

ADR 0010's rule applies unchanged: *the moment two distinct claims compare
equal, the winner is decided by arrival order, in a durable replayable log.*
Two entities extracted in the same batch can share an `observed_at` exactly.

```python
(provenance.observed_at, provenance.confidence, str(origin))
```

`confidence` is meaningful — between two simultaneous observations, prefer the
surer one. `str(origin)` carries no meaning at all and is there solely for
totality: `EntityId`s are distinct by construction, so no two claims in one
merge can tie. This is the same composition `consolidation/planning.py`'s
`duplicate_preference` uses — a meaningful order with an id appended — and it
is deliberately *not* `domain.preference.preference`, which orders whole
entities on fields a single property's claim does not have.

Totality is asserted as a property test, not argued in a comment. ADR 0010 is
explicit that a `>` → `>=` survivor is equivalent only when the order really is
total, and that reading it as equivalent is a claim about the order rather than
an observation about the diff.

### 4. Building claims from entities

```python
def claims_for(
    property_name: str, canonical: Entity, others: Sequence[Entity]
) -> list[PropertyClaim]
```

Skips entities whose `properties` lack the key: an entity that says nothing
about a property has not claimed anything, and treating an absent key as a
`None` claim would let silence outvote a statement under
`MOST_RECENTLY_OBSERVED`. Returns `[]` when nobody claims it, which the caller
distinguishes from "everybody claimed `None`".

This lives in `merge_strategy.py` alongside `resolve` rather than in a new
module. It is the only way to build a `PropertyClaim` correctly, and splitting
the constructor from the consumer is how the two drift.

### 5. Public API

`Entity` is exported, so the gate's closure rule (`CLAUDE.md`: "exporting one
name pulls its closure") forces `Provenance` into `__all__`. `ExtractionMethod`
is already there.

`PropertyMergeStrategy`, `PropertyClaim`, `resolve` and `claims_for` stay
**unexported**, as `merge_strategy.py` is today. They have no production caller;
exporting an uncalled capability is how a promise gets made by accident.

## What this deliberately does not do

**It does not wire property merging into consolidation.** Today
`consolidation/service.py` merges edges and never touches properties — the
canonical entity keeps its own and the absorbed entities' are discarded
(`domain/preference.py:134`). Doing that properly means a merged-properties
payload on `EntitiesMerged`, a projection that applies it, and an undo that
restores the pre-merge values; that is the rest of B28 and a larger change than
this one. `resolve` therefore remains uncalled by production code after this
work, exactly as it is before it. BACKLOG keeps the remainder, and B28 shrinks
rather than closes.

Stated plainly because the alternative is a reader concluding the feature
shipped.

## Against the existing ADRs

Per `.claude/rules/definition-of-done.md`, each related ADR is named with a
verdict rather than left to inference.

| ADR | Verdict |
|---|---|
| [`0001` event log schema and granularity](../../adr/0001-event-log-schema-and-granularity.md) | **Amended.** `DocumentExtracted`'s payload shape changes and the event goes to `event_version = 2`. 0001's decision — one event per document, at that granularity, owned by that aggregate — is untouched; what changes is the shape of the `Entity` inside it. Its Consequences gain the version bump and the clean break. |
| [`0006` the public surface is gated](../../adr/0006-the-public-surface-is-gated.md) | **Stands, and is exercised.** `Provenance` enters `__all__` only because `Entity`'s signature now mentions it — the closure rule working as 0006 describes. `PropertyMergeStrategy` and friends stay unexported. |
| [`0010` one total order for preference](../../adr/0010-one-total-order-for-preference.md) | **Stands, extended by composition.** `preference` keeps its definition and reads the same values through `entity.provenance`. The claim order is a *new, narrower* order over a different thing (one property's claim, not a whole entity), composed the way `duplicate_preference` composes — meaningful components, id appended. It is not a second definition of 0010's order and must not become one. |
| [`0002` two store ports](../../adr/0002-two-store-ports.md) | **Stands.** No port method changes. Both adapters' row shapes change because `Entity`'s fields moved; the contract over them does not. |
| [`0004` consolidation emits events](../../adr/0004-consolidation-emits-events.md) | **Stands, untouched.** This work deliberately does not wire property merging into the merge path, so nothing about what consolidation emits changes. |
| [`0005` temporal inference on read](../../adr/0005-temporal-inference-on-read.md) | **Stands.** Worth stating because `observed_at` is a new time field and 0005 is about time: they are different clocks. `TemporalExtent` is when a fact held; `observed_at` is when the library was told. Nothing infers one from the other. |

A **new ADR** records the decision itself — that provenance is a value object,
and that a strategy is named for the question it can answer. Drafted
provisionally as `0035-provenance-is-a-value-object.md`; the number is
re-checked against `main` at merge per `recurring-defects.md` §6.

## Testing

Beyond the ordinary behavioural tests, four things this project's own history
says will otherwise be got wrong:

1. **Coinciding bounds.** `CLAUDE.md`'s table opens and closes with the same
   defect two years apart: intervals whose bounds never coincide. The `>` in
   the `MOST_RECENTLY_OBSERVED` order is a comparison whose mutants live or die
   on whether two claims ever share an `observed_at`. At least one example
   where two claims are simultaneous, and one where they are simultaneous *and*
   share a confidence, so each component of the tuple decides something in some
   case.
2. **Totality as a property**, per ADR 0010 — group generated claims by their
   order key and assert that any two sharing one are equal.
3. **Boundary examples pinned with `@example`**, not left to the sampler: a
   single claim, two claims, and the empty-`others` case for `PREFER_MERGED`.
4. **A deliberate defect, watched failing**, before any property is trusted:
   make `MOST_RECENTLY_OBSERVED` return `claims[0].value` and confirm the suite
   goes red. A property that stays green under an injected defect is worse than
   no property.

`ExtractionMethod`/`model` validator coverage moves to `Provenance`'s tests
with the code — a test left behind on `Entity` would assert a rule that file no
longer states.

## Files touched

- `src/redstring/domain/provenance.py` — new
- `src/redstring/domain/entity.py` — five fields out, `provenance` in
- `src/redstring/domain/merge_strategy.py` — `PropertyClaim`, new `resolve`, `claims_for`, rename
- `src/redstring/domain/preference.py` — reads moved fields through `provenance`
- `src/redstring/extraction/mapping.py` — `_build_entity` constructs `Provenance`
- `src/redstring/events/document.py` — `DocumentExtracted.event_version = 2`
- `src/redstring/graph/adapters/memory.py`, `neo4j.py` — row shape
- `src/redstring/vector/`, `composition/`, `consolidation/` — field reads
- `src/redstring/__init__.py` — export `Provenance`
- `docs/adr/0035-provenance-is-a-value-object.md` — new
- `BACKLOG.md` — B28 shrinks; relationship-provenance asymmetry recorded
