# ADR 0035: Provenance is a value object, and a strategy is named for the question it can answer

## Status

Accepted.

**Amends [`0001` event log schema and granularity](0001-event-log-schema-and-granularity.md).**
0001's decisions stand — one coarse `DocumentExtracted` per extraction run,
owned by the `Document` aggregate, with an explicitly declared schema version.
What changes is the *shape of the `Entity` inside that payload*: five fields
move onto a nested value object and a required one joins them, so no payload
written against the old shape validates. 0001's Consequences carry the
version-bump obligation and the clean break.

[`0010` one total order for preference](0010-one-total-order-for-preference.md)
**stands, extended by composition.** `preference` keeps its definition and
reads the same values, now through `entity.provenance`. The claim order
introduced here is a *new and narrower* order over a different thing — one
property's claim rather than a whole entity — composed the way
`duplicate_preference` composes: meaningful components, an id appended for
totality. It is not a second definition of 0010's order and must not become
one.

[`0006` the public surface is gated](0006-the-public-surface-is-gated.md)
**stands, and is exercised**: `Provenance` enters `__all__` only because
`Entity`'s signature now mentions it, which is 0006's closure rule working
exactly as described. The merge-strategy names stay unexported.

[`0002` two store ports](0002-two-store-ports.md) **stands** — no port method
changes; both adapters' row shapes change because the fields moved, and the
contract over them does not.
[`0004` consolidation emits events](0004-consolidation-emits-events.md)
**stands, untouched**, because this work deliberately does not wire property
merging into the merge path.
[`0005` temporal inference on read](0005-temporal-inference-on-read.md)
**stands**, and is worth naming precisely because a new time field arrives:
`TemporalExtent` is when a fact held and `observed_at` is when this library was
told. Nothing infers one from the other.

## Context

`domain/merge_strategy.py` refused the strategy then called `LATEST` with a
reason that read convincingly and was wrong in a way that mattered: that
timestamps were per entity rather than per property, so "the most recently
updated value" had no data behind it.

There were no per-entity timestamps either. `Entity` had no time field at all,
and the only record-time value anywhere in the library was `Alias.merged_at`.
`TemporalExtent` holds *world* time and cannot stand in for record time.

Naming the wrong obstacle pointed at the wrong fix. "Add a per-property
timestamp" is not the smallest honest change, and it is not the change that
makes the strategy answerable. The real blocker was the signature:

```python
def resolve(strategy, *, canonical: Any, others: list[Any]) -> Any
```

`resolve` received bare *values*. Nothing about where a value came from, when,
or how confidently, survived the call. **Every strategy needing more than the
value itself was unanswerable by construction**, and no amount of new data on
`Entity` would have changed that while the call dropped it at the boundary.
The refused strategy was simply the first member of the enum to notice.

## Decision 1: provenance is a value object on `Entity`, carrying a required `observed_at`

`Entity` gains a `Provenance`, and loses the fields that describe the
*claiming* rather than the thing claimed.

The seam is that distinction and nothing else. `name`, `entity_type`,
`description`, `properties` and `temporal` are claims *about the thing*; what
observed it, when, how, from where and how sure are claims about the
*observation*. `normalized_name` and `blocking_keys` stay on `Entity` despite
being derived, because they are derived from the thing rather than from the
observation.

The validators move with the fields, because they were invariants of the
observation all along and sat on `Entity` for want of somewhere better: the
confidence bound, and the rule that a `model` may only be recorded when the
method invoked one. A new validator joins them, requiring `observed_at` to be
timezone-aware — modelled on `Alias.merged_at`, and for the same reason. A
naive datetime compared against an aware one raises at the moment of
comparison, which here means deep inside a merge rather than at the
construction site that could name the offending entity.

**`observed_at` is required, and that is the load-bearing part of this
decision.** An optional one would rebuild the original hole one level down: the
strategy would work for some callers and refuse for others, with no way to tell
which until it ran. Required makes the question answerable by construction — no
`None` branch, no "unprovenanced" error class. The cost is that every
construction site supplies one and no previously written payload validates.
Both were accepted deliberately; there is no persisted log to migrate.

**`Relationship` does not get a `Provenance`.** Symmetry is tempting and would
be wrong: a relationship carries confidence and a source but no extraction
method and no model, so its provenance is a *different shape*. Sharing the type
would mean fields that are always absent, or a base class earning its keep on
two subclasses. The asymmetry is real, and the relationship-provenance gap is
tracked in `BACKLOG.md` on its own terms rather than papered over here.

## Decision 2: a merge strategy is named for the question it can answer

`LATEST` becomes `MOST_RECENTLY_OBSERVED`, wire value included.

The rename is the point rather than incidental tidying. `LATEST` invites a
caller to believe the library knows when a property was last *updated*. It does
not and will not — nothing here tracks a property's edit history, and this
decision does not add one. What is available is weaker and genuinely
answerable: of the entities asserting this property, which was *observed* most
recently, and what did it assert. A name that promises less is the difference
between a strategy and a misrepresentation.

`resolve` takes an ordered sequence of claims, canonical first. Positional
rather than a `canonical=`/`others=` pair because every strategy but the
canonical one treats them as one sequence, and two parameters force each to
re-splice them. `PREFER_MERGED` becomes implementable for free, which is the
signature change paying for itself: it was never hard, only ill-defined about
*which* absorbed entity when there are several, and "the first the merge
listed" is a rule the caller controls and a projection can replay.

**The order behind `MOST_RECENTLY_OBSERVED` must be total**, per 0010: the
moment two claims compare equal the winner is decided by arrival order, in a
durable replayable log, and two entities extracted in the same batch share an
instant exactly. Recency first, confidence second — between two simultaneous
observations, prefer the surer one — and the origin id last, carrying no
meaning at all and present solely so that no two claims from distinct entities
can tie. Totality is asserted as a property test rather than argued in a
comment, because 0010 is explicit that reading a `>` → `>=` survivor as
equivalent is a claim about the order rather than an observation about the
diff.

**`DEEP_MERGE` stays deferred**, for a reason none of this touches: a wrong
deep merge is hard to undo because the pre-merge shape is not recoverable from
its result. It raises rather than falling back to the canonical value, which
would corrupt data while looking like it worked.

## Consequences

**No previously written `DocumentExtracted` payload validates.** This is a
clean break, sanctioned because there is no persisted log. A library that
shipped a log would owe an upcaster here, and 0001's `event_version` plus an
upcaster is the escape route it describes.

**So `DocumentExtracted` carries `event_version = 2`.** "No persisted log" is
a claim about this repository, not about the consumers of a published library,
which is why the number moved even though nothing in the tree can encounter a
`1`. It cost a gate:
[`0001`](0001-event-log-schema-and-granularity.md) Decision 3 asserted every
event's version was *literally 1*, and that assertion had to be split into the
half that was doing work (the version is declared, not inherited) and a
per-event table pinning the number. 0001's Consequences record the split.

**The strategies are implemented and unreached.** Consolidation still merges
edges and discards the absorbed entities' properties, so `resolve` has no
production caller after this decision, exactly as it had none before it.
Wiring it up needs a merged-properties payload on the merge event, a
projection that applies it, and an undo that restores the pre-merge values —
which is a larger change, kept in `BACKLOG.md`. This is
`recurring-defects.md` §3 (code fully tested and never invoked passes every
gate this repository has) held deliberately open with its eyes open, and
stated plainly because the alternative is a reader concluding the feature
shipped.

**Two orders now exist over overlapping data, and they must not converge.**
0010's order ranks whole entities on fields a single property's claim does not
have; the claim order ranks claims on fields an entity-wide order has no
business consulting per property. A future edit that makes one call the other
is the defect 0010 exists to prevent, arriving from the side.

**Reading a value now costs a hop.** Callers write `entity.provenance.confidence`
rather than `entity.confidence`, and there is deliberately no forwarding
property: a second way to spell the same read is a second declaration site, and
the one that loses looks authoritative.

**The merge-strategy names stay unexported.** They have no production caller,
and exporting an uncalled capability is how a promise gets made by accident.
When consolidation reaches them, the export becomes a decision someone takes on
purpose rather than one already taken.
