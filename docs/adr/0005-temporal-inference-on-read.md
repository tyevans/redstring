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

Since the ring migration these types all live in the domain ring —
`TemporalExtent`, `DatePrecision` and `UncertaintyMarker` in
`domain/temporal.py`, `Bounds`, `TemporalRelation`, `bounds` and `relate` in
`domain/interval.py`, and the parsers with `widen` in
`domain/temporal_parsing.py` (see
[the domain value types reference](../reference/domain-value-types.md)) — so
the arithmetic depends on nothing above `domain`; what that arithmetic assumes
is set out in [the section below](#what-the-extent-to-interval-conversion-assumes).

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

## What the extent-to-interval conversion assumes

Computing on read means the *rule* is the answer: there is no stored edge to
appeal to, so a query is correct exactly insofar as everything that evaluates
it converts extents to intervals the same way. These are the assumptions that
conversion makes, recorded here because each of them is a place a second
implementation would plausibly differ.

### An extent is not an interval; `bounds()` is the only conversion

`TemporalExtent` records what the text said. "2023" is
`start_date=2023-01-01, end_date=None, precision=YEAR` — the *interval* it
denotes is all of 2023, and `bounds` in `domain/interval.py` is the single
place that conversion happens. Every comparison runs on `Bounds`, never on the
raw fields.

`bounds` returns `None` for an extent carrying no dates — one holding only a
`sequence_position`, say. That is not an error, and it is why `relate` is
`TemporalRelation | None`: sequence position orders events that have no dates
at all, and no interval comparison applies to them. A caller has to decide what
"undated" means for its query rather than being handed a default.

### Bounds are half-open `[lower, upper)`

So that adjacent intervals do not overlap: 2022 ends at the instant 2023
begins, and exactly one of them contains that instant.

The alternative — an inclusive upper bound at the last representable instant of
the unit — was rejected because it makes correctness depend on the resolution
of whatever stored the value. `datetime`'s microseconds, Neo4j's nanoseconds
and Postgres's microseconds do not agree on what "the last instant of 2022" is,
so the same two extents could compare one way in memory and another after a
round trip. That is the replay-divergence failure this ADR exists to remove,
reappearing as a storage detail.

### `None` is two different infinities

In `Bounds.lower`, `None` means unbounded below; in `Bounds.upper`, unbounded
above. It is not a "missing" or "unknown" value, and it arises only from
`UncertaintyMarker.BEFORE` and `AFTER`, which are genuine open bounds — "before
1900" has no earliest moment. Every other extent produces two real datetimes
(see [the domain value types reference](../reference/domain-value-types.md)).

The comparison helpers are therefore position-specific: `_lower_le` and
`_upper_le` differ only in what they do with `None`, and that difference is the
whole of their content. `_lower_le(None, y)` is true for every `y`;
`_upper_le(None, y)` is true only when `y` is also `None`.

A single shared `_le` reading `None` as "missing" passes every closed-interval
test in the suite — which is the failure shape `CLAUDE.md` tabulates, since the
inputs that would distinguish the two implementations are precisely the ones a
closed-interval suite never states. `TestOpenBounds` in
`tests/unit/domain/test_interval.py` exists to state them. The other tempting
reading, `None` as "now", is worse than wrong-and-caught: it would pass today
and start failing on a calendar date, so the suite pins open-ended cases
against fixed years rather than against the present.

The same asymmetry is why `relate_bounds` is public rather than an
implementation detail of `relate`: an interval open at *both* ends is not
reachable from any `TemporalExtent` — an extent with neither date has no bounds
at all — and it is exactly where a `None`-handling mistake would hide from a
test that can only build inputs through `TemporalExtent`.

### Precision carries width, not `end_date`

"2023" parses to `start_date=2023-01-01, end_date=None, precision=YEAR` — the
width of the interval it denotes is carried by `precision`, not by a second
date. `end_date` is set only when the text states a second endpoint
("1914-1918") or names a span whose length is a convention rather than a
precision ("the 19th century"). A parser that filled `end_date` in for a bare
year would be recording a claim the text did not make, and the two readings
are not equivalent: `2023-01-01` with `precision=DAY` and `precision=YEAR`
carry the same dates and denote intervals differing by a factor of 365 (see
[the domain value types reference](../reference/domain-value-types.md)).

Turning that precision into an upper bound — widening — happens in exactly one
function, `widen` in `domain/temporal_parsing.py`, which returns the first
instant *after* the unit containing the moment it is given. It lives beside the
flooring parsers rather than next to `bounds` because it is their exact
inverse: the parsers floor "March 2023" to `2023-03-01`, and `widen` has to
recover the March that flooring discarded. Co-location is the only thing
keeping the pair in step — nothing type-checks the inverse relationship — and
splitting them across modules is how one side acquires a case the other lacks.

The same precision widens whichever endpoint is last: for a stated range,
`bounds` widens `end_date`, so "1914-1918" ends where 1919 begins rather than
on New Year's Day 1918.

When an extent states no precision at all, the width is `INSTANT` — one
microsecond, `datetime`'s resolution, defined in `domain/interval.py` beside
the code that reads it. Not a day: defaulting to a day would invent a claim the
extent never made, and would let one exact timestamp swallow every other event
that day, turning `OVERLAPS` into `CONTAINS` for reasons no reader of the
source text could predict. One microsecond is "an instant" in the only units
available; it is a width rather than a point because `Bounds` is half-open and
a zero-width half-open interval contains nothing at all, including its own
lower bound.

### What does *not* widen

Precision is the only thing that widens. Uncertainty mostly does not:
`EXACT`, `CIRCA`, `APPROXIMATE` and `INFERRED` fall through to the ordinary
closed interval, so all four denote exactly what `EXACT` denotes. That is a
decision rather than an omission. "Circa 1850" is a claim about *how
confidently* 1850 is known, not about which years it might have been; widening
it would mean inventing a margin — a decade? a century? — and every subsequent
comparison would then rest on a number nobody chose deliberately. The marker
stays on the extent for a caller that wants to weight it, and the interval
stays what the text said.

`BEFORE` and `AFTER` are the only two markers that touch the bounds, and they
do it by opening one and discarding the far endpoint. They are not symmetric in
where they cut: "before 1900" stops where 1900 *begins*, so the named unit is
excluded, while "after 1900" starts where 1900 *ends* and so is widened by the
precision rule above. Both name one instant, which is why the endpoint they do
not use is dropped rather than kept.

An extent carrying both an open marker and an `end_date` has therefore said
something contradictory — the marker says the range is open in one direction
while the pair of dates says it is closed at both — and the marker wins.
Reading "before" as `(-inf, end_date)` would honour both, but it converts a
contradiction into a plausible interval, and an extent in that state is almost
certainly a parser bug rather than a caller's intent: `parse_temporal` cannot
build one, because the range strategies run before uncertainty is folded in and
never combine the two (see
[the domain value types reference](../reference/domain-value-types.md)). The
guard exists for hand-built extents, and it fails towards the smaller claim —
the narrower reading is the one a wrong answer is cheapest to notice from.

### Why this constrains replay determinism

Everything above is not preamble to the query — it *is* the query. When an
edge is stored, the rule that produced it is a historical detail; the edge is
the answer and two implementations of the rule can be reconciled by looking at
what is in the graph. When nothing is stored, the answer exists only for as
long as it takes to compute, so "correct" means nothing more or less than
"every evaluator converted extents to intervals the same way". There is no
artefact to compare against. See
[how to query a timeline](../how-to/query-a-timeline.md) for what that looks
like from the caller's side.

That makes the conversion rules above load-bearing in a way that is easy to
miss when reading them one at a time. An adapter, a Cypher `WHERE` clause or a
hand-written SQL predicate that re-derives *any* of them is not an
optimisation of the answer; it is a second copy of the answer, free to disagree
with `domain/interval.py` at exactly the inputs a hand-written predicate is
least likely to be tested on — a bare year whose width lives in `precision`
rather than in `end_date`, a `None` bound meaning minus infinity in one
position and plus infinity in the other, an endpoint that coincides rather than
merely being close. All three are rules a range test over two date columns
cannot express, and a predicate that ignores them still returns rows.

Which is the whole difficulty: a divergent copy fails by *returning a
plausible answer*. There is no exception, no empty result, nothing that looks
like a fault — an entity that should have matched is simply absent from a list
that is otherwise right, and the caller has no way to tell that list from the
correct one. It is the same failure mode as a stale stored edge in reason 1 of
the Decision above, reappearing in the read path rather than the write path,
and it is the reason both are refused.

This is the constraint behind
[the port-composition section below](#related-the-query-composes-rather-than-adding-a-port-method)
and behind B48's superset shape. The superset is not a compromise on
performance grounds; it is the only shape that lets a store help without
holding an opinion. A range scan widened by one year cannot exclude a true
match no matter how the adapter implements it, so an adapter that gets it
wrong gets it wrong by returning *too much* — which `relate` then discards.
The exact predicate never leaves `domain/interval.py`, and adapters are left
with a job they cannot get subtly wrong.

It is also the same reason ADR 0002 keeps the two store ports
[narrow](0002-two-store-ports.md). A port method that can only be asked for a
range scan has nowhere to put a divergent copy of this rule; a
`temporal_overlaps=` filter is an invitation to write one per adapter, and to
grow a full interval-semantics conformance section in the compliance suite to
police copies that need not have existed.

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

The predicate is not a range test on two columns. Which rules make it more
than that is set out above, in
[what the extent-to-interval conversion assumes](#what-the-extent-to-interval-conversion-assumes);
the point here is only that they exist and that `domain/interval.py` is where
they live. Reimplementing them in Cypher *and* in the memory adapter *and* in
any future SQL adapter gives three copies of a rule that has one home, and
they will diverge silently — a wrong answer here looks exactly like a correct
one. `WHERE start_date >= ? AND end_date <= ?` is not a near miss of the right
predicate; it is a different predicate that returns rows.

The cost is linear in the tenant's entity count regardless of how few entities
are dated. `BACKLOG` B48 is open and states the shape that resolves it: a port
method returning a deliberate **superset** — a cheap indexed range bound,
widened by one year so it cannot exclude a true match — with `relate` kept as
the exact filter over what comes back. Adapters then implement only a range
scan, which they cannot get subtly wrong, and the semantics stay in one place.

## A parsing decision recorded here because it has no other home

`parse_temporal` in `domain/temporal_parsing.py` resolves relative expressions
by **probing the same text against two reference dates 36 years apart** —
`_PROBE_A` (1999-06-15) and `_PROBE_B` (2035-02-20), both in that module — and
raising `AmbiguousReferenceDateError` when the two parses differ, rather than
enumerating relative-expression keywords. An enumeration of English fails open:
whichever word it omits parses to a confident wrong date. The probe fails
closed, and it asserts the property that actually matters — that the text did
not consult the vantage point — for whatever reason the underlying library had,
rather than for the reasons the list's author thought of.

It earned itself immediately: `dateutil.parser.parse` fills omitted components
from the current date, so "March 15" silently acquires the current year. That
is a clock dependency appearing nowhere in this project's source, and no
keyword list would have contained it. Because the probe drives `default=` as
well as `RELATIVE_BASE`, the same check catches it. In an event-sourced system
that dependency is replay divergence — the worst failure available here — and
it is why `reference_date` is a required parameter with no fallback to today
(see [the domain value types reference](../reference/domain-value-types.md)).

This lives in an ADR about inference-on-read because it is the same argument
one layer down. Inference refuses to store a derived fact so that replay cannot
disagree with arithmetic; the parser refuses a hidden clock so that
re-extraction cannot disagree with the original parse. Neither has a
decision record of its own, and the parsing one had nowhere else to go.

## Consequences

- Inference cost is O(n²) in dated entities, bounded by `max_pairs`.
- `Entity.temporal` round-trips through `InMemoryGraphStore` but has **not**
  been verified against Neo4j (`BACKLOG` B53). If that adapter drops the field,
  every temporal query answers `[]` in production while every unit test passes.
  Check this first when temporal work resumes.
- The deleted parser's `confidence`, `parse_method` and named eras were dropped
  deliberately; `BACKLOG` B49 has the argument and the git ref.
- `TemporalRelation` has six members — `BEFORE`, `AFTER`, `DURING`,
  `CONTAINS`, `OVERLAPS`, `EQUALS` — and is **deliberately coarser than Allen's
  thirteen**. `meets`, `starts`, `finishes` and their inverses all turn on
  *exact endpoint equality*, and this data cannot support that distinction: by
  [the precision rule above](#precision-carries-width-not-end_date) an endpoint
  is usually manufactured by widening, so "2022 meets 2023" would be reporting
  an artefact of how a bare year is converted rather than anything the text
  asserted. Callers wanting Allen's finer relations would be reading confidence
  into coincidences that `widen` produced. `EQUALS` is retained because two
  extents denoting the same interval is a claim worth making even when both
  endpoints were widened; the rest collapse into `OVERLAPS` and `CONTAINS`.
