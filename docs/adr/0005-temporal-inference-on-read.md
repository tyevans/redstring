# ADR 0005: Temporal inference is computed on read

**Status:** accepted, slice 8 of the ring migration.

**Why this is an ADR:** the alternative — emitting inferred edges into
`DocumentExtracted` alongside the ones the model stated — is the obvious design
and would be very hard to reverse once a log contained them. The full argument
is in `temporal/inference.py`'s module docstring; this records it where a
reader looking for decisions will find it, and adds the defect that shaped it.

## Context

Entities carry a `TemporalExtent`. Given two extents, the relation between them
(`BEFORE`, `CONTAINS`, `OVERLAPS`, `EQUALS`) is a pure function — `relate` in
`domain/interval.py`. So "what preceded this" is arithmetic over what the
system already knows.

## Decision

Nothing inferred is persisted. `temporal/inference.py` computes relations at
query time from the extents it is given.

Three reasons, in the order of how much they cost to discover:

1. **It duplicates state that can disagree with its inputs.** Re-extraction
   under a new model is the supported way an entity's dates improve, and
   improving one extent invalidates every inferred edge touching it. There is
   no `TemporalRelationInvalidated` event and there will not be one — ADR 0001's
   granularity is deliberately coarse. Stored edges would go stale silently,
   and a stale `PRECEDES` looks exactly like a fresh one.

2. **It is quadratic in the tenant, not in the document.** The edges worth
   having are mostly *between* documents. Extraction only ever sees one
   document, so emitting from extraction gives the within-document subset —
   the least interesting part — **while looking like the whole answer**.

3. **It puts a derived fact in the durable log.** A replay could then produce
   edges that disagree with the same arithmetic run today, which is the defect
   the whole re-architecture exists to remove.

`InferredRelation` is deliberately **not** a `Relationship` and has no `id`, so
it cannot reach `upsert_relationship` even by accident. That is the enforcement;
the reasoning above is only the argument.

## The defect that shaped the design

The campaign's only Critical finding is in this module, and its lesson is
structural rather than about temporal logic.

`infer_relations` originally canonicalised the pair **before** comparing:
sort by `order_key`, call the earlier one the source. The module's stated
invariant — that `DURING` never appears in the output — was then an *argument
about sort order* rather than a property of the code, and the argument was
wrong. `order_key` sorts by lower bound then upper bound ascending, so two
extents sharing a lower bound put the **shorter** one first; `relate` from
shorter to longer is `DURING`; and the default filter discarded it. **The pair
produced no edge at all.**

The inputs that break it are not exotic: "2023" and "2023-2025", a month and
the year it opens, an event and the era beginning with it. It was invisible
because the direction tests used disjoint years and the one containment test
used March-inside-2023, whose lower bounds differ.

`domain/interval.py` was innocent. The bug was one layer up, in code
re-deriving an invariant the layer below already guaranteed.

**The fix is structural: canonicalise from the computed relation, not from the
sort.** Whatever direction the pair arrives in, the relation decides.
`order_key` still orders pairs, but now only so that `OVERLAPS` and `EQUALS` —
genuinely symmetric — get a *deterministic* direction rather than a correct one.

Grepping afterwards for anything else resting on `order_key` **found the same
reasoning a second time**, in a map entry no test could reach. Hence the habit
now in `CLAUDE.md`: when you fix something that rested on an incidental
property, grep for the second instance. It was there both times this project
looked.

## Related: the query composes rather than adding a port method

`TemporalQuery.entities_in_interval` pages `GraphStore.find_entities` over the
tenant and applies the predicate in Python. It does **not** add a
`temporal_overlaps` filter to the port, and the reason is not laziness.

The predicate is not a range test on two columns: precision widens a bound
(`2023` at YEAR precision denotes all of 2023 even though `end_date` is
`None`), and `UncertaintyMarker.BEFORE`/`AFTER` make a bound infinite from a
field that is neither date column. Reimplementing that in Cypher *and* in the
memory adapter *and* in any future SQL adapter gives three copies of a rule
that lives in `domain/interval.py`, and they will diverge silently — a wrong
answer here looks exactly like a correct one.

The cost is linear in the tenant's entity count regardless of how few entities
are dated. `BACKLOG` B48 is open and states the shape that resolves it: a port
method returning a deliberate **superset** — a cheap indexed range bound,
widened by one year so it cannot exclude a true match — with `relate` kept as
the exact filter over what comes back. Adapters then implement only a range
scan, which they cannot get subtly wrong, and the semantics stay in one place.

## A parsing decision recorded here because it has no other home

The parser resolves relative expressions by **probing the same text against two
reference dates 36 years apart** and raising when the results differ, rather
than enumerating relative-expression keywords. An enumeration of English fails
open; the probe fails closed.

It earned itself immediately: `dateutil.parser.parse` fills omitted components
from the current date, so "March 15" silently acquires the current year. That
is a clock dependency appearing nowhere in this project's source, and in an
event-sourced system it is replay divergence — the worst failure available
here.

## Consequences

- Inference cost is O(n²) in dated entities, bounded by `max_pairs`.
- `Entity.temporal` round-trips through `InMemoryGraphStore` but has **not**
  been verified against Neo4j (`BACKLOG` B53). If that adapter drops the field,
  every temporal query answers `[]` in production while every unit test passes.
  Check this first when temporal work resumes.
- The deleted parser's `confidence`, `parse_method` and named eras were dropped
  deliberately; `BACKLOG` B49 has the argument and the git ref.
