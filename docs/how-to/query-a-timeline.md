# Query a timeline

Once entities are in a `GraphStore`, some of them carry a `TemporalExtent` —
the dates extraction pulled out of the text. This guide shows how to ask what a
tenant's graph held over a window of time, get those entities back in
chronological order, and derive the temporal relations between them.

Everything here is `redstring.temporal.query.TemporalQuery`, which composes
over any `GraphStore` — the in-memory adapter, Neo4j, or one of your own. It
adds no port method and writes nothing back: a timeline read is a paged scan of
the tenant plus interval arithmetic in Python, and an inferred relation is
computed fresh on every call. That is a decision, not an implementation detail;
[ADR 0005](../adr/0005-temporal-inference-on-read.md) records why an inferred
edge is never persisted.

You will need:

- entities already written to a store (extraction and projection produce them),
- some of them dated — an entity with no `temporal`, or one whose extent holds
  only a `sequence_position`, never appears in any result below,
- `Bounds` from `redstring.domain.interval` to describe the window you are
  asking about. See
  [domain value types](../reference/domain-value-types.md) for what a
  `TemporalExtent` records and how precision and uncertainty markers widen or
  open the interval it denotes.

Temporal is **not** part of the public API — you import it by dotted path, and
the next section explains what that costs you.

## Before you start: temporal is not in the public API

`redstring.__all__` is the whole supported surface, and nothing in this guide
is in it. `TemporalQuery`, `CursorStalledError`, `InferredRelation`,
`DEFAULT_MAX_PAIRS`, `Bounds`, `bounds` and `TemporalRelation` are all reached
by dotted path:

```python
from redstring.domain.interval import Bounds, TemporalRelation, bounds
from redstring.temporal.inference import DEFAULT_MAX_PAIRS, InferredRelation
from redstring.temporal.query import CursorStalledError, TemporalQuery
```

Anything imported that way is internal and may change without notice,
including in a patch release. The package is real and tested — it is held back
because it has no composed entry point yet, and exporting the classes would
publish a shape its callers have not finished deciding. Expect movement in the
names and signatures below.

Two consequences are worth planning around rather than discovering:

- **The closure of exported types does not extend to these.** `Entity` and
  `TemporalExtent` *are* exported, so the values you get back are stable; the
  types you use to ask the question are not.
- **`CursorStalledError` is a `RuntimeError`, not a `RedstringError`.** The
  library's exception gate covers what descends from `RedstringError`, and
  this does not — `except RedstringError` will not catch a stalled cursor, so
  catch it by name or let it propagate. `ValueError` from `page_size` and
  `max_pairs` is likewise a plain builtin.

If you want insulation, wrap the two or three calls you make in a module of
your own and import `Entity`, `TemporalExtent`, `DatePrecision` and
`UncertaintyMarker` from `redstring` — those are exported and gated. See
[domain value types](../reference/domain-value-types.md) for that vocabulary.

None of this constrains which store you read through. `TemporalQuery` takes the
`GraphStore` port, which *is* exported, so the in-memory adapter, the
[Neo4j store](../reference/neo4j-graph-store.md), or your own adapter all work
unchanged — the instability is in this package, not in what it reads.

## Build a TemporalQuery over a GraphStore

`TemporalQuery` takes a store and nothing else:

```python
from redstring.graph.adapters.memory import InMemoryGraphStore
from redstring.temporal.query import TemporalQuery

store = InMemoryGraphStore()
query = TemporalQuery(store)
```

Swap the adapter and nothing else changes — the constructor takes the
`GraphStore` port, so the [Neo4j store](../reference/neo4j-graph-store.md) or
your own adapter goes in the same slot. Construction touches no I/O and is not
a connection: build one per store and keep it, or build one per call, whichever
suits you. It holds no state between calls.

The one keyword argument is `page_size`, and it is keyword-only:

```python
query = TemporalQuery(store, page_size=100)  # default is DEFAULT_PAGE_SIZE, 500
```

`page_size` is a tuning knob, never a cap on the answer. Every method below
pages until the tenant is exhausted, so `page_size=100` and `page_size=5000`
return the same entities in the same order — only the number of round trips
differs. `TemporalQuery(store, page_size=0)` raises `ValueError` before any
query runs, because a page size that cannot advance would page forever; see
[Tune page_size](#tune-page_size-and-why-page_size--1-raises).

Every method takes the tenant as its first argument, and there is no
cross-tenant read:

```python
found = await query.entities_in_interval(tenant_id, window)
```

Two things this deliberately does *not* do, both of which are decisions you
inherit by using it:

- **It adds no port method.** A timeline read is `find_entities` paged over the
  tenant plus interval arithmetic in Python, because the predicate is not a
  range test on two date columns — `DatePrecision` widens a bound and an
  `UncertaintyMarker` can make one infinite, from a field that is neither date
  column. Pushing that into Cypher would mean a second copy of the rules, and a
  wrong answer here looks exactly like a right one. The cost is that a query is
  linear in the tenant's entity count regardless of how few are dated.
- **It writes nothing back.** An inferred relation is computed fresh on every
  call and never persisted;
  [ADR 0005](../adr/0005-temporal-inference-on-read.md) records why.

The three methods are `entities_in_interval`, `timeline` and
`relations_in_interval`. All are `async` and all return lists — there is no
streaming API, so a query holds its matches in memory. The rest of this guide
works through them in that order, starting with how to describe the window.

## Describe the window you are asking about (Bounds)

`Bounds` is the window. It is a two-field `NamedTuple` from
`redstring.domain.interval` — `lower` and `upper`, both
`datetime | None` — and it is what every method here takes:

```python
from datetime import UTC, datetime

from redstring.domain.interval import Bounds

window = Bounds(datetime(2023, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, tzinfo=UTC))
window.lower, window.upper   # the two fields
lower, upper = window        # it is a tuple; unpack it if you prefer
```

Three properties of a window, each of which decides an answer you will see
later in this guide:

- **It is half-open, `[lower, upper)`.** The lower bound is included and the
  upper is not, so `Bounds(2023-01-01, 2024-01-01)` is all of 2023 and nothing
  of 2024. That is what makes adjacent windows not overlap: 2022 ends at the
  instant 2023 begins, and exactly one of them contains that instant. The
  alternative — an inclusive upper bound at "the last instant of 2022" — makes
  the answer depend on whether the store counts in microseconds or nanoseconds.
- **`None` means infinity, outwards.** `None` in `lower` is "unbounded below",
  `None` in `upper` is "unbounded above". It never means "missing", and it
  never means "now". The [next section](#ask-for-open-ended-windows-boundsnone-x-and-boundsx-none)
  is about those windows specifically.
- **Both datetimes must be timezone-aware.** `TemporalExtent` rejects a naive
  `datetime` at construction, so every extent you are comparing against is
  aware. `Bounds` is a bare `NamedTuple` and validates nothing, so a naive
  bound is accepted here and fails later, inside the comparison, as
  `TypeError: can't compare offset-naive and offset-aware datetimes`. Pass
  `tzinfo=UTC`.

### Build a window from an extent with `bounds`

You rarely want to widen dates by hand. `bounds` converts a `TemporalExtent` —
what extraction recorded — into the interval it actually denotes, and it is the
single place that conversion happens:

```python
from redstring.domain.interval import bounds
from redstring.domain.temporal import DatePrecision, TemporalExtent

extent = TemporalExtent(start_date=datetime(2023, 1, 1, tzinfo=UTC), precision=DatePrecision.YEAR)
bounds(extent)
# Bounds(lower=datetime(2023, 1, 1, tzinfo=UTC), upper=datetime(2024, 1, 1, tzinfo=UTC))
```

Note what happened: the extent names one date and no end date, and the window
is a *year* wide. Precision is not decoration — "2023" denotes all of 2023, and
an extent that stated `DatePrecision.MONTH` on the same date would denote
January only. Reproducing that rule in your own arithmetic is how a window
comes to disagree with the entities it is being compared against; call
`bounds` instead. See
[domain value types](../reference/domain-value-types.md) for the full
precision and uncertainty vocabulary.

Two more of its results are worth knowing before you use it as a window
builder:

- **`UncertaintyMarker.BEFORE` and `AFTER` open a bound.** An extent marked
  `BEFORE` 1900 gives `Bounds(None, 1900-01-01)`; marked `AFTER`, it gives
  `Bounds(1901-01-01, None)` — open above, and starting once 1900 is *over*.
  `CIRCA` and `APPROXIMATE` change nothing: they are claims about confidence,
  not about extent, and widening them would mean inventing a margin nobody
  chose.
- **`bounds` returns `None` for an extent that denotes no interval** — one with
  no dates at all, including an extent carrying only a `sequence_position`. It
  is not an error, and it is the same rule that keeps those entities out of
  every result in this guide. If you are building a window out of an extent,
  handle the `None`; `Bounds` is not `Bounds | None`, and passing `None` as an
  interval means something different on each method.

### The window is compared as an interval, not as two dates

Every method below compares your `Bounds` against each entity's `Bounds` with
the same interval arithmetic, and always in that direction — entity first,
window second. So a `Bounds` is not a filter on `start_date`: an entity dated
"2023" intersects a window covering March 2023, even though neither of its
stored dates falls inside it. That is the whole reason the predicate is not a
range test on two columns, and why this query pages the tenant and filters in
Python rather than pushing a `WHERE` clause into the store —
[ADR 0005](../adr/0005-temporal-inference-on-read.md) and the
[Neo4j store reference](../reference/neo4j-graph-store.md) cover what the port
does and does not do for you here.

## Ask for open-ended windows: Bounds(None, x) and Bounds(x, None)

Put `None` in a bound and that side runs to infinity:

```python
from datetime import UTC, datetime

from redstring.domain.interval import Bounds

up_to_2000 = Bounds(None, datetime(2000, 1, 1, tzinfo=UTC))   # everything before 2000
from_2000 = Bounds(datetime(2000, 1, 1, tzinfo=UTC), None)    # 2000 onwards
everything = Bounds(None, None)                               # every dated entity
```

`Bounds(None, None)` is the honest way to say "the whole timeline" to
`entities_in_interval`, and it is what the tests use to scan a tenant. On
`timeline` and `relations_in_interval` you can pass `interval=None` instead —
those two accept `Bounds | None`, and `None` there means "do not restrict".
`entities_in_interval` takes `Bounds`, not `Bounds | None`; pass it
`Bounds(None, None)`.

### `None` means infinity, not "now" and not "missing"

This is the one thing to get right about open windows, because the wrong
reading is the tempting one. `Bounds(x, None)` is **not** "from x until
today":

```python
after_2000 = Bounds(datetime(2000, 1, 1, tzinfo=UTC), None)
# an entity dated 2200 is DURING this window, today and in every future year
```

An implementation that read the open end as the current date would give a
different answer next century, and would answer differently for the same data
on two machines with different clocks. Nothing in `redstring.domain.interval`
reads a clock. The same applies downwards: `Bounds(None, x)` includes an
entity dated to the year 1, not merely one dated within recent memory.

Nor does `None` mean "I didn't supply this bound, so ignore the constraint" in
any sense narrower than infinity — those happen to coincide, which is why the
mistake survives so long. Where the two readings come apart is `bounds()`
returning `None` for an *extent*: that is "this extent denotes no interval at
all" and is a different value from `Bounds(None, None)`, which denotes every
instant. Do not pass the first where the second is meant.

### Open windows come out of `bounds` on their own

You do not have to construct these by hand, and usually should not.
`UncertaintyMarker.BEFORE` and `AFTER` produce exactly these shapes:

```python
from redstring.domain.interval import bounds
from redstring.domain.temporal import DatePrecision, TemporalExtent, UncertaintyMarker

before_1900 = TemporalExtent(
    start_date=datetime(1900, 1, 1, tzinfo=UTC),
    precision=DatePrecision.YEAR,
    uncertainty=UncertaintyMarker.BEFORE,
)
bounds(before_1900)   # Bounds(None, datetime(1900, 1, 1, tzinfo=UTC))
```

Note where the closed end lands. `BEFORE` stops where the named unit *begins* —
"before 1900" excludes 1900 rather than running to the end of it — while
`AFTER` starts once the unit is *over*, so an extent marked `AFTER` 1900 with
`YEAR` precision gives `Bounds(datetime(1901, 1, 1, tzinfo=UTC), None)`. The
asymmetry is deliberate and it is the only place precision widening and an open
bound interact. Feed extents through `bounds` rather than reproducing it.

An extent carrying an open marker *and* an `end_date` has said something
contradictory; the marker wins and `end_date` is dropped. `parse_temporal`
cannot build one, so you will only see this from a hand-built extent.

### How relations read against an open window

The relation is still entity-to-interval, and the ordinary rules hold with
`None` treated as infinity on whichever side it sits:

| Entity extent | Window | Relation |
|---|---|---|
| 1850 | `Bounds(None, 1900-01-01)` | `DURING` |
| 1950 | `Bounds(None, 1900-01-01)` | `AFTER` |
| 2050 | `Bounds(2001-01-01, None)` | `DURING` |
| 2200 | `Bounds(2001-01-01, None)` | `DURING` |
| 1950 | `Bounds(2001-01-01, None)` | `BEFORE` |
| anything dated | `Bounds(None, None)` | `DURING` |

Two consequences worth planning around:

- **Two windows open on the same side nest rather than overlap.** An entity
  dated "after 2000" against a window of "after 1900" is `DURING`, not
  `OVERLAPS` — they share `None` as an upper bound, and equal upper bounds with
  one lower bound below the other is containment. If you are filtering by
  relation, `OVERLAPS` will not find these.
- **`Bounds(None, None)` makes every dated entity `DURING`**, never `EQUALS`,
  unless the entity's own extent is open at both ends too — which no
  `TemporalExtent` can produce, since an extent with neither date has no bounds
  at all and is filtered out before any comparison. So a `relations=` filter of
  `{TemporalRelation.EQUALS}` against `Bounds(None, None)` returns nothing.

### Open bounds in `timeline` ordering

`timeline` sorts by interval, and the infinities sort outwards rather than as
very old or very recent dates: an entity open below precedes every dated one,
and an entity open above follows every entity whose start is known. There is no
sentinel datetime doing this — `datetime.min` would collide with a genuine year-1
date, which this parser produces from "1st century", and sort a genuinely
open-ended interval as merely very old.

See [domain value types](../reference/domain-value-types.md) for the full
uncertainty vocabulary, and
[ADR 0005](../adr/0005-temporal-inference-on-read.md) for why none of this is
pushed into the store — the [Neo4j store](../reference/neo4j-graph-store.md)
never sees your window, open-ended or otherwise.

## List the entities that fall in a window (entities_in_interval)

`entities_in_interval` is the base read. Give it a tenant and a window, get
back the tenant's dated entities whose extents intersect it:

```python
from datetime import UTC, datetime

from redstring.domain.interval import Bounds

window = Bounds(datetime(1900, 1, 1, tzinfo=UTC), datetime(2000, 1, 1, tzinfo=UTC))
found = await query.entities_in_interval(tenant_id, window)
[e.name for e in found]   # ['inside']
```

It is `async`, and it returns a `list[Entity]` — every match, held in memory.
The signature is:

```python
await query.entities_in_interval(
    tenant_id,               # positional; the only tenant read
    interval,                # positional; Bounds, not Bounds | None
    relations=INTERSECTING,  # keyword-only
    entity_type=None,        # keyword-only
)
```

`interval` is required and is not optional-shaped: pass `Bounds(None, None)`
for "the whole timeline", not `None`. (`timeline` and `relations_in_interval`
do take `interval=None`; this method does not.)

### Intersection is the default, and it is wider than "inside"

With no `relations=`, the filter is `INTERSECTING` — every `TemporalRelation`
except `BEFORE` and `AFTER`, which is exactly "the two extents share at least
one instant". So an entity dated 1990–2010 comes back from a window of
1900–2000 even though it outlives the window:

```python
straddling = TemporalExtent(
    start_date=datetime(1990, 1, 1, tzinfo=UTC),
    end_date=datetime(2010, 1, 1, tzinfo=UTC),
    precision=DatePrecision.YEAR,
)
# ... stored, then:
await query.entities_in_interval(tenant_id, Bounds(utc(1900, 1, 1), utc(2000, 1, 1)))
# -> [straddling entity]
```

If you meant "fell wholly inside", say so with
`relations={TemporalRelation.DURING}` — the
[next section](#filter-by-relation--the-relation-reads-entity-to-interval)
covers the direction those names are read in, which is the part that catches
people out.

The window is half-open, so adjacency is not intersection. An entity dated
1999 does **not** match `Bounds(2000-01-01, 2001-01-01)`: 1999 ends at the
instant 2000 begins, and only one of the two windows contains that instant.

### Precision widening happens on both sides

The entity's extent is converted with the same `bounds` you would use to build
a window, so an entity is matched by what it *denotes*, not by its two stored
dates. An entity recorded as "2023" — `start_date=2023-01-01`,
`end_date=None`, `DatePrecision.YEAR` — is returned by a window covering July
2023:

```python
await query.entities_in_interval(
    tenant_id, Bounds(utc(2023, 7, 1), utc(2023, 8, 1))
)   # -> [the entity dated 2023]
```

Neither stored date falls inside that window. This is the row a
`WHERE start_date >= ? AND end_date <= ?` misses, and it is the reason the
predicate is not pushed into the store at all — see
[ADR 0005](../adr/0005-temporal-inference-on-read.md) and the
[Neo4j store reference](../reference/neo4j-graph-store.md). Open markers work
the same way: an entity marked `AFTER` 1900 matches a window in the year 3000
and not one in 1800.

### What never comes back

Two kinds of entity are dropped before any comparison, silently and by design:

- an entity with `temporal=None`,
- an entity whose extent denotes no interval — including one carrying only a
  `sequence_position`.

Both are skipped even for `Bounds(None, None)`, which otherwise matches every
dated entity in the tenant. A sequence-only extent is real information, just
not information about *when*; see
[what never appears in results](#what-never-appears-in-results-undated-entities-sequence_position-only-extents).

There is also no cross-tenant read. Another tenant's entities are never
returned, whatever the window.

### Order of the result

Results come back **ascending by `Entity.id`** — the `GraphStore` port's total
order, preserved rather than re-sorted, so you can page the result yourself
using the last id you saw. It is deliberately *not* chronological: if you want
time order, call [`timeline`](#get-the-whole-timeline-in-time-order-timeline),
which sorts by interval.

### Narrowing the scan

`entity_type` is pushed down to the store rather than filtered afterwards, so
it reduces what gets read:

```python
await query.entities_in_interval(tenant_id, Bounds(None, None), entity_type="Person")
```

`page_size` (on the constructor) changes only the number of round trips, never
the answer. Neither is a limit: the method pages the tenant to exhaustion and
returns everything that matched. On a tenant large enough for that to matter,
see [Tune page_size](#tune-page_size-and-why-page_size--1-raises) and
[Narrow the scan with entity_type](#narrow-the-scan-with-entity_type).

## Filter by relation — the relation reads entity-to-interval

`relations=` on `entities_in_interval` narrows the default `INTERSECTING` set
to the relations you actually want. It is keyword-only and takes any collection
of `TemporalRelation`:

```python
from redstring.domain.interval import Bounds, TemporalRelation

window = Bounds(datetime(2000, 1, 1, tzinfo=UTC), datetime(2001, 1, 1, tzinfo=UTC))

inside = await query.entities_in_interval(
    tenant_id, window, relations={TemporalRelation.DURING}
)
spanning = await query.entities_in_interval(
    tenant_id, window, relations={TemporalRelation.CONTAINS}
)
```

### Read the name entity-to-interval, not interval-to-entity

This is the one thing to get right, and it is where the mistake is made. The
relation is computed **entity first, window second** — `relate_bounds(entity,
interval)` — so every name below describes what the *entity* did to the window
you asked about:

- `DURING` — the entity fell **inside** the window.
- `CONTAINS` — the entity **spanned** the window; the window fell inside it.
- `OVERLAPS` — they share instants but neither contains the other.
- `EQUALS` — the two intervals are identical on both bounds.
- `BEFORE` — the entity ended at or before the window began.
- `AFTER` — the entity began at or after the window ended.

So an entity dated 2000-06-01 against a window of all of 2000 is `DURING`, and
one dated 1900–2100 against the same window is `CONTAINS`. Asking for
`CONTAINS` when you meant "contained by the window" returns the long-lived
entities and drops exactly the ones you were after — and it returns a
plausible non-empty list, so nothing tells you.

### The six relations, and what the default leaves out

`TemporalRelation` has six members, deliberately coarser than Allen's thirteen:
`meets`, `starts` and `finishes` turn on exact endpoint equality, and an
endpoint produced by widening a year is an artefact of the precision rule
rather than something the text asserted.

The default is `INTERSECTING`, which is every relation **except** `BEFORE` and
`AFTER` — that is, "the two extents share at least one instant". Passing
`relations=INTERSECTING` explicitly is the same query as passing nothing:

```python
from redstring.temporal.query import INTERSECTING

INTERSECTING   # frozenset: DURING, CONTAINS, OVERLAPS, EQUALS
```

Which means the two disjoint relations are opt-in. Ask for them by name if you
want them, and expect what you get — `relations={TemporalRelation.BEFORE}`
against a narrow window returns most of the tenant's dated history, because
"before the window" is not a small set. See
[why filtering on AFTER or DURING returns an empty
list](#why-filtering-on-after-or-during-returns-an-empty-list) for the case
where the opposite happens.

### The comparison runs on denoted intervals, on both sides

`relations=` does not change what is being compared. The entity's extent still
goes through `bounds`, so precision widening and open markers apply before the
relation is decided:

- An entity recorded as "2023" (`DatePrecision.YEAR`, no `end_date`) denotes
  all of 2023. Against a window covering July 2023 it is `CONTAINS`, not
  `DURING` — the window is inside the *entity*.
- An entity marked `UncertaintyMarker.AFTER` 1900 denotes
  `Bounds(1901-01-01, None)`. Against a window in the year 3000 it is
  `CONTAINS`; against one in 1800 it is `AFTER`.
- Two intervals open on the same side nest rather than overlap, so an
  `OVERLAPS` filter will not find them.

`EQUALS` is tested first and requires both bounds to match exactly, which
after widening is less common than it looks: an entity dated "2023" equals a
window of `Bounds(2023-01-01, 2024-01-01)` and equals nothing else.

### Relations are total, so the filters partition

`relate_bounds` always returns exactly one relation for a pair — there is no
"unrelated" and no `None`. Every dated entity in the tenant therefore falls in
exactly one of the six buckets for a given window, so a set of relations
selects a disjoint slice and the six together are the whole dated tenant.
Undated entities are not a seventh bucket: they are dropped before any
comparison and no `relations=` value brings them back.

An empty `relations=` set is accepted and returns an empty list rather than
raising. That is worth knowing because it makes a typo'd or
programmatically-built filter fail silently.

### This filter is not the one on relations_in_interval

Both methods take a `relations=` keyword and they mean different things.
On `entities_in_interval` it filters entity-against-your-window and defaults
to `INTERSECTING`. On
[`relations_in_interval`](#infer-relations-between-entities-in-a-window-relations_in_interval)
it filters entity-against-*entity* and defaults to `INFERRED_RELATIONS` —
`BEFORE`, `CONTAINS`, `OVERLAPS`, `EQUALS`, with no `DURING` and no `AFTER`,
because an inferred edge is canonicalised so that only one direction of each
pair is emitted.

None of this reaches the store. The relation is decided in Python from the
denoted intervals, which is why the predicate is not pushed into a `WHERE`
clause — [ADR 0005](../adr/0005-temporal-inference-on-read.md) records the
decision, [domain value types](../reference/domain-value-types.md) the
vocabulary, and the [Neo4j store reference](../reference/neo4j-graph-store.md)
what the port does supply.

## Get the whole timeline in time order (timeline)

`timeline` is `entities_in_interval` sorted chronologically. Its window is
optional and keyword-only, so the whole dated tenant is the default question:

```python
everything = await query.timeline(tenant_id)
[e.name for e in everything]   # ['first', 'second', 'third']
```

The signature is:

```python
await query.timeline(
    tenant_id,          # positional; the only tenant read
    interval=None,      # keyword-only; Bounds | None, None means no restriction
    entity_type=None,   # keyword-only
)
```

Note the two differences from `entities_in_interval`: the window is a
*keyword* argument here, and it accepts `None`. There is no `relations=` — a
window on `timeline` always means intersection, the same `INTERSECTING`
default. If you want "fell wholly inside" in time order, call
`entities_in_interval(tenant_id, window, relations={TemporalRelation.DURING})`
and sort the result yourself, or narrow the window instead.

### Narrow it with a window

Pass `interval=` and only entities intersecting it are ordered:

```python
found = await query.timeline(
    tenant_id, interval=Bounds(utc(1900, 1, 1), utc(2000, 1, 1))
)
[e.name for e in found]   # ['in']   -- an entity dated 2050 is absent
```

`interval=None` is not `Bounds(None, None)` in code, but it is the same answer:
both mean "do not restrict". `None` takes the cheaper path — no relation is
computed per entity, only the "is it dated at all" test.

### The order is by interval, then by id

The sort key is: when the entity **begins**, then when it **ends**, then its
`id`. All three components matter.

- **The id is not decoration.** Two entities routinely carry the same extent —
  a document naming three things that happened in 1066 — and without the
  tie-break their order would be whatever the store handed back, which the
  `GraphStore` port does not promise to keep stable across adapters. With it,
  the three come back ascending by id, identically on every call and every
  adapter.
- **Ordering is over the *denoted* interval, not the stored dates.** Each
  extent goes through `bounds` first, so "2023" at `YEAR` precision begins on
  1 January 2023 and ends on 1 January 2024, and sorts against a March 2023
  date accordingly.

### Where the open-ended entities land

Infinities sort outwards, not as very old or very recent dates:

| Entity | Sorts |
|---|---|
| marked `BEFORE` 1900 (open below) | before every dated entity, including one dated to the year 1 |
| marked `AFTER` 1899 (open above) | after an entity dated 1900, and after every entity whose start is known |

There is no sentinel datetime doing this. Substituting `datetime.min` for
minus infinity would collide with a genuine year-1 date — which this parser
produces from "1st century" — and sort a genuinely open-ended interval as
merely very old. Substituting "today" would get it backwards, and would answer
differently tomorrow. Each end is ranked first and compared by value only when
the ranks agree.

### What it shares with every other read here

- **`async`, and a list.** Every match is held in memory; there is no
  streaming API.
- **Undated entities never appear**, including those whose extent carries only
  a `sequence_position`. That holds with `interval=None` too — the "whole
  timeline" is the whole *dated* timeline. See
  [what never appears in results](#what-never-appears-in-results-undated-entities-sequence_position-only-extents).
- **No cross-tenant read.** Another tenant's entities are never ordered in.
- **An empty tenant gives `[]`**, not an error.
- **`page_size` and `entity_type` behave as they do elsewhere** — the first
  changes only round trips, the second is pushed down to the store. A tenant
  of 25 entities read at `page_size=10` returns all 25, and a final page
  exactly filling `page_size` still terminates.
- **Results are yours to mutate.** Changing a returned entity's `properties`
  or its `temporal` does not reach the store; the next call reads the
  original.

### It is the input to relations_in_interval

[`relations_in_interval`](#infer-relations-between-entities-in-a-window-relations_in_interval)
calls `timeline` and infers over what it returns, using the same ordering
function. That sharing is deliberate: two definitions of "chronological" that
disagreed would put the timeline in one order and the relations derived from
it in another. So anything absent from `timeline` — undated, sequence-only,
outside the window, another tenant's — takes no part in any inferred edge, and
none of it is written back;
[ADR 0005](../adr/0005-temporal-inference-on-read.md) records why. The
vocabulary of extents and precision is in
[domain value types](../reference/domain-value-types.md), and what the store
does and does not supply in the
[Neo4j store reference](../reference/neo4j-graph-store.md).

## Infer relations between entities in a window (relations_in_interval)

`relations_in_interval` takes the entities `timeline` would return and reports
how each pair stands to the others in time:

```python
from datetime import UTC, datetime

from redstring.domain.interval import Bounds

relations = await query.relations_in_interval(
    tenant_id, Bounds(datetime(1800, 1, 1, tzinfo=UTC), datetime(2000, 1, 1, tzinfo=UTC))
)
[(r.source_name, r.relation, r.target_name) for r in relations]
# [('a', <TemporalRelation.BEFORE>, 'b')]
```

The signature is:

```python
await query.relations_in_interval(
    tenant_id,                       # positional; the only tenant read
    interval=None,                   # positional *or* keyword; Bounds | None
    relations=INFERRED_RELATIONS,    # keyword-only
    entity_type=None,                # keyword-only
    max_pairs=DEFAULT_MAX_PAIRS,     # keyword-only
)
```

`interval` is the odd one out among the three methods: on
`entities_in_interval` it is a required positional `Bounds`, on `timeline` it
is keyword-only, and here it is positional-or-keyword and accepts `None`.
`None` means "every dated entity in the tenant", which on a large tenant is
what `max_pairs` exists to refuse — see
[Handle ValueError from max_pairs](#handle-valueerror-from-max_pairs--default_max_pairs).

You get back a `list[InferredRelation]`, sorted and deduplicated, with one
edge per related pair and no inverses. Every one of them is computed on this
call and none is written anywhere;
[ADR 0005](../adr/0005-temporal-inference-on-read.md) is the decision, and
[Use an InferredRelation](#use-an-inferredrelation--and-why-you-cannot-store-one)
covers what the type deliberately cannot do.

### The window selects the participants; it does not clip them

The interval is a filter on *who takes part*, applied exactly as
`timeline(interval=...)` applies it — intersection, entity-to-window. It is
not a clipping region, and the relations between the entities that survive it
are computed from their **whole** extents:

```python
# 'a' 1900, 'b' 1950, 'far' 2500
relations = await query.relations_in_interval(
    tenant_id, Bounds(utc(1800, 1, 1), utc(2000, 1, 1))
)
# 'far' appears in no relation at all
```

Two consequences follow, and both change answers:

- **An entity that merely overlaps the window brings its whole extent in.**
  One dated 1990–2010 is `INTERSECTING` a window of 1900–2000, so it
  participates, and against another entity dated 2005 it is `CONTAINS` — a
  relation decided entirely outside the window you asked about.
- **Narrowing the window drops edges rather than shortening them.** There is
  no relation "between the parts of a and b that fell inside the window";
  either both endpoints are in the participant set or there is no edge.

### The relations here are entity-to-entity, not entity-to-window

This is the second `relations=` keyword in the guide and it means something
different from the
[first one](#filter-by-relation--the-relation-reads-entity-to-interval).
There, the comparison was each entity against your window. Here your window
has already done its work, and each relation compares **one entity against
another** — `source` against `target`, in that order. `a BEFORE b` says `a`
ended at or before `b` began; it says nothing about the window.

The default is `INFERRED_RELATIONS`, not `INTERSECTING`:

```python
from redstring.temporal.inference import INFERRED_RELATIONS

INFERRED_RELATIONS   # frozenset: BEFORE, CONTAINS, OVERLAPS, EQUALS
```

Four relations, not six. `AFTER` and `DURING` are missing because each pair is
reduced to a single edge: `a AFTER b` is emitted as `b BEFORE a`, and
`a DURING b` as `b CONTAINS a`, endpoints swapped. Both facts are still there,
stated once and always the same way round — which is what makes "how many
relations are there" independent of the order the entities came out of the
store. Asking for `relations={TemporalRelation.AFTER}` returns an empty list,
always, and it is the single most common surprise here; see
[why filtering on AFTER or DURING returns an empty
list](#why-filtering-on-after-or-during-returns-an-empty-list).

The inversion happens *after* the comparison, not before. That ordering is the
whole correctness argument: canonicalising first — sorting the pair and
calling the earlier one the source — made the "no `DURING`" guarantee an
argument about sort order rather than a property of the code, and the argument
was wrong. Two intervals sharing a lower bound sort shorter-first, so "2023"
against "2023–2025" compared shorter-to-longer as `DURING` and was then
discarded by the default filter, losing the edge entirely. Pairs of that shape
come straight out of the parser: a month and the year it opens, an event and
the era that begins with it.

### What takes part, and what silently does not

The participant set is exactly `timeline`'s result, so everything that guide
section excludes is excluded here too, and none of it is an error:

- entities with `temporal=None`,
- entities whose extent denotes no interval, including `sequence_position`-only
  extents,
- entities outside the window, or of another `entity_type`,
- another tenant's entities — there is no cross-tenant read.

One more exclusion is specific to this method: **an entity handed in twice is
deduplicated by id before any pair is formed.** A self-pair would compare an
interval with itself and report `EQUALS`, which reads as a genuine finding
about two entities rather than an artefact of the read. Deduplication is
first-occurrence-wins.

A tenant with fewer than two dated entities produces `[]`, as does an empty
tenant. An empty `relations=` set is accepted and also returns `[]`.

### The result is deterministic and totally ordered

`InferredRelation` is a `NamedTuple`, so the result sorts without a key
function, and `relations_in_interval` returns it sorted. The same graph gives
the same list, in the same order, on every call and through every adapter —
including for `OVERLAPS` and `EQUALS`, which have no earlier side and get
their direction from the same `order_key` `timeline` sorts by, with the entity
id as the final tie-break.

That sharing is deliberate. Two definitions of "chronological" that disagreed
would put the timeline in one order and the relations derived from it in
another.

`InferredRelation` is **not hashable** — `TemporalExtent` is a pydantic model
and is not — so `set(relations)` raises. Key on the endpoint ids if you need
set semantics.

### Cost

Inference is O(n²) in the dated entities that take part, on every call, in
Python. `entity_type` and a narrow `interval` are the two levers that reduce
`n`; `page_size` is not one, since it changes only round trips. `max_pairs`
does not reduce the cost — it refuses, before any comparison, so the refusal
cannot itself be slow.

The vocabulary of extents and precision is in
[domain value types](../reference/domain-value-types.md), what the store does
and does not supply in the
[Neo4j store reference](../reference/neo4j-graph-store.md) — it never sees
your window or computes a relation — and why none of this is persisted in
[ADR 0005](../adr/0005-temporal-inference-on-read.md).

## Why filtering on AFTER or DURING returns an empty list

If you called `relations_in_interval` with `relations={TemporalRelation.AFTER}`
or `{TemporalRelation.DURING}` and got `[]`, nothing went wrong with your data
or your window. **Those two relations are never produced by
`relations_in_interval`, for any tenant, at any window.** The facts they would
express are in the result already, stated the other way round:

```python
from redstring.domain.interval import TemporalRelation

await query.relations_in_interval(tenant_id, relations={TemporalRelation.AFTER})
# [] -- always

await query.relations_in_interval(tenant_id, relations={TemporalRelation.DURING})
# [] -- always
```

This applies only to `relations_in_interval`. On
[`entities_in_interval`](#filter-by-relation--the-relation-reads-entity-to-interval)
both names are perfectly usable — there the comparison is entity-to-*window*,
nothing is canonicalised, and `relations={TemporalRelation.DURING}` is the
normal way to ask "fell wholly inside".

### Ask for the inverse instead

Each pair of entities produces exactly one edge, and the direction is fixed:

| You wanted | Ask for | Read the result as |
|---|---|---|
| `a AFTER b` | `TemporalRelation.BEFORE` | `b BEFORE a` — the same fact, endpoints swapped |
| `a DURING b` | `TemporalRelation.CONTAINS` | `b CONTAINS a` |

So "which entities fall inside another entity's span" is a `CONTAINS` query,
and you read the containing entity off `source_name` / `source_entity_id` and
the contained one off `target_name` / `target_entity_id`:

```python
from redstring.domain.interval import TemporalRelation

nested = await query.relations_in_interval(
    tenant_id, relations={TemporalRelation.CONTAINS}
)
[(r.source_name, r.target_name) for r in nested]   # (container, contained)
```

The default, `INFERRED_RELATIONS`, is those four and only those four —
`BEFORE`, `CONTAINS`, `OVERLAPS`, `EQUALS` — so passing no `relations=` at all
already gives you every fact the method can state. Filtering is only ever
narrowing.

### Why one direction rather than both

`relate_bounds` is symmetric: `a BEFORE b` and `b AFTER a` are one fact about
one pair. Emitting both would double the output and make "how many relations
are there between these entities" depend on the order the entities came out of
the store. So each pair is reduced to a single edge, and the two directions
that get dropped are `AFTER` and `DURING` — emitted as their target's `BEFORE`
and `CONTAINS` with the endpoints swapped. Nothing is lost; every fact is
stated once and always the same way round.

`OVERLAPS` and `EQUALS` are their own inverses and have no earlier side, so
their direction comes from the same `order_key` `timeline` sorts by, with the
entity id as the final tie-break. That direction is *deterministic* rather than
meaningful: do not read `source` as "the earlier one" for those two.

### An empty filter is also an empty list

Two other ways to get `[]` from this method look the same from outside and are
not this:

- `relations=set()` — accepted, never raises, returns `[]`. A
  programmatically-built filter that came out empty fails silently.
- Fewer than two dated participants, after the window, `entity_type` and the
  undated exclusions have done their work. An entity with `temporal=None`, or
  one whose extent carries only a `sequence_position`, never takes part; see
  [what never appears in
  results](#what-never-appears-in-results-undated-entities-sequence_position-only-extents).

To tell the three apart, call `relations_in_interval` with the default
`relations=` and look at what comes back. If that is non-empty, your filter was
the problem; if it is empty, so was the participant set.

### The guarantee is structural, and it was not always

Worth knowing because it changes what you can rely on: the inversion happens
**after** the comparison, not before. An earlier version canonicalised first —
sort the pair by interval, call the earlier one the source — which made "no
`DURING` in the output" an argument about sort order rather than a property of
the code, and the argument was wrong. `order_key` puts the shorter of two
intervals sharing a lower bound first, and comparing shorter to longer yields
`DURING`, which the default filter then discarded, losing the edge entirely.
Pairs of that shape come straight out of the parser: "2023" against
"2023–2025", a month and the year it opens, an event and the era beginning with
it. Those edges are now present, as `CONTAINS`, with the longer extent as
`source`.

Because the reduction is a property of inference rather than of storage, none
of it is negotiable per call and none of it is written anywhere —
[ADR 0005](../adr/0005-temporal-inference-on-read.md) records why an inferred
edge is computed fresh every time, [domain value
types](../reference/domain-value-types.md) covers the relation vocabulary, and
the [Neo4j store reference](../reference/neo4j-graph-store.md) confirms the
store never computes a relation for you.

## Use an InferredRelation — and why you cannot store one

Every element of a `relations_in_interval` result is an `InferredRelation`, a
seven-field `NamedTuple` from `redstring.temporal.inference`:

```python
from redstring.temporal.inference import InferredRelation

for r in await query.relations_in_interval(tenant_id, window):
    r.source_entity_id   # EntityId
    r.target_entity_id   # EntityId
    r.relation           # TemporalRelation
    r.source_name        # str, the entity's name, so you can print without a re-read
    r.target_name        # str
    r.source_extent      # TemporalExtent | None -- what it was computed from
    r.target_extent      # TemporalExtent | None
```

Read it in field order and it states one fact: source, target, and how the
first stands to the second in time. `a BEFORE b` means `a` ended at or before
`b` began — [entity-to-entity, never
entity-to-window](#the-relations-here-are-entity-to-entity-not-entity-to-window).

Because it is a tuple, it unpacks and destructures:

```python
for source_id, target_id, relation, source_name, target_name, _, _ in relations:
    print(f"{source_name} {relation.name} {target_name}")
```

### It sorts, and it does not hash

Being a `NamedTuple` is what makes the result deterministic: a list of them
sorts with no key function, and `relations_in_interval` returns it sorted. The
same graph gives the same list in the same order on every call and through
every adapter.

It is **not hashable**, though, and that is not an oversight you can work
around with a `frozen=True` flag: `TemporalExtent` is a pydantic model and is
not hashable, so a tuple containing one is not either.

```python
set(relations)             # TypeError: unhashable type
{(r.source_entity_id, r.target_entity_id, r.relation) for r in relations}   # do this
```

Key on the endpoint ids and the relation when you need set semantics or a
dict. The result is already deduplicated — one edge per related pair — so a
set is usually only wanted for intersecting two queries.

### You cannot write one back, by construction

An `InferredRelation` is deliberately **not** a `Relationship`, and the
difference is not cosmetic. `GraphStore.upsert_relationship` takes a
`Relationship`, which requires an `id` (`RelationshipId`), a `tenant_id`, a
`relationship_type` and a `confidence`. An `InferredRelation` has none of
those:

```python
await store.upsert_relationship(inferred)   # never valid: no id, no tenant_id,
                                            # no relationship_type, no confidence
```

There is no `.to_relationship()` and no adapter that will take one. That is the
point — the missing `id` is what makes persisting an inferred edge by accident
impossible rather than merely discouraged, and `isinstance` tells an inferred
edge apart from one the event log actually recorded.

If you genuinely want a stored edge with the same shape you have to mint it
yourself — a fresh `RelationshipId`, the tenant, a `relationship_type` you
chose, a `confidence` you can defend — and at that point you own it. It is a
new assertion, not a cached read, and nothing in this library will refresh it.

### Why it is not stored for you

Three reasons, recorded in full in
[ADR 0005](../adr/0005-temporal-inference-on-read.md):

- **A stored edge can disagree with its inputs.** Re-extraction under a new
  model version is the supported way an entity's dates improve, and improving
  one extent invalidates every inferred edge touching it. There is no
  invalidation event and there will not be one, so stored edges would go stale
  silently — and a stale `PRECEDES` looks exactly like a fresh one.
- **The edges worth having are between documents.** Extraction sees one
  document at a time, so emitting inferred edges from extraction would produce
  the within-document subset while looking like the whole answer.
- **It puts a derived fact in the durable log.** The log is what the system
  knows; this is arithmetic over what it knows. Storing it means a replay can
  produce edges that disagree with the same arithmetic run today.

The cost is stated rather than hidden: inference is O(n²) in dated
participants, on every call, and `max_pairs` is what bounds it. The
[Neo4j store](../reference/neo4j-graph-store.md) has no notion of a temporal
relation at all — it stores `Relationship`s you gave it, and computes nothing.

### What to do instead of caching it

The two supported levers are the ones already in the signature: narrow
`interval` and `entity_type` so fewer entities take part. Holding the returned
list in a variable for the duration of a request is fine — it is a plain list
of tuples and nothing mutates it. What is not fine is treating it as a durable
index: the next re-extraction moves the extents underneath it, and the list
will not know.

If you want to show a user *why* an edge exists rather than just that it does,
`source_extent` and `target_extent` carry the two `TemporalExtent`s the
relation was computed from; the [next
section](#show-an-inferred-edges-working-source_extent--target_extent) covers
reading them, and [domain value
types](../reference/domain-value-types.md) covers what an extent records.

## Show an inferred edge's working (source_extent / target_extent)

An inferred edge with no visible derivation is indistinguishable from one a
document asserted, which is the confusion this whole package exists to avoid.
So every `InferredRelation` carries the two extents it was computed from:

```python
for r in await query.relations_in_interval(tenant_id, window):
    print(
        f"{r.source_name} {r.relation.name} {r.target_name}"
        f"  ({r.source_extent.original_text} / {r.target_extent.original_text})"
    )
# 'first BEFORE second  (1900 / 1950-1960)'
```

`source_extent` is the *source* entity's `TemporalExtent` and `target_extent`
the target's — the objects that came off `Entity.temporal`, not copies and not
a rendering. Everything an extent records is therefore available to show a
user: `start_date`, `end_date`, `precision`, `uncertainty`,
`original_text` (the span of text extraction read the dates out of, which is
usually what you want on screen), `sequence_position` and `publication_date`.
See [domain value types](../reference/domain-value-types.md) for what each of
those means.

### They follow the swap, so they always match the direction shown

The two extents are read off `source` and `target` *after* canonicalisation,
not before. When a pair comes out as `AFTER` or `DURING` the endpoints are
swapped and re-emitted as `BEFORE` or `CONTAINS` — and the extents swap with
them. So `source_extent` is always the extent of whatever `source_name` and
`source_entity_id` name, in every row, with no case to special-case:

```python
# 'year' is "2023"; 'era' is "2023-2025". Compared, this pair is DURING;
# it is emitted as CONTAINS with the longer extent as source.
(r,) = await query.relations_in_interval(tenant_id, None)
r.relation                       # <TemporalRelation.CONTAINS>
r.source_name                    # 'era'
r.source_extent.original_text    # '2023-2025'
```

This is the practical payoff of inverting after the comparison rather than
before; [why filtering on AFTER or DURING returns an empty
list](#why-filtering-on-after-or-during-returns-an-empty-list) covers the
guarantee itself.

### The extent is what was recorded, not what was compared

The relation was decided over the *denoted* interval, and the extent is the
input to that, not the output. An extent reading `start_date=2023-01-01`,
`end_date=None`, `precision=YEAR` was compared as all of 2023. If you are
showing a user why an edge exists, show the interval as well as the dates —
`bounds` is the same conversion the query used:

```python
from redstring.domain.interval import bounds

bounds(r.source_extent)   # Bounds(2023-01-01, 2024-01-01) -- what was compared
r.source_extent.end_date  # None -- what was recorded
```

Printing `start_date` and `end_date` alone is how an explanation comes to
disagree with the edge it is explaining: the dates say "2023-01-01, no end"
while the edge says the entity contains a window in July. `original_text` and
`precision` together are usually the honest short form.

### Both fields are typed `TemporalExtent | None`

The `None` is a `NamedTuple` default, not a case `relations_in_interval`
produces. Only dated entities take part — an entity with `temporal=None`, or
one whose extent denotes no interval, is dropped before any pair is formed —
so every row from a query has both extents populated. A type checker will
still make you handle it:

```python
if r.source_extent is not None:      # always true from relations_in_interval
    show(r.source_extent)
```

Hand-built `InferredRelation`s in your own test fixtures are where the default
actually applies.

### They are the live extents, so treat them as read-only

Store reads hand back entities you may mutate freely without reaching the
store, and these extents come from those entities. But `TemporalExtent` is a
pydantic model, and mutating one you got from an edge changes what the other
rows of the same result show — two edges touching the same entity carry the
same extent object. Render it, or build your own value from it; do not edit it
in place.

And do not treat the pair as a durable record of the derivation. Re-extraction
under a new model version is how an entity's dates improve, and it moves the
extent underneath any copy you kept — which is the same reason the edge itself
is never stored. [ADR 0005](../adr/0005-temporal-inference-on-read.md) records
that decision; the [Neo4j store](../reference/neo4j-graph-store.md) holds the
entity and its extent, and computes no relation over them.

## Handle the two failures a real tenant produces

Everything above returns `[]` rather than raising when there is nothing to
say — an empty tenant, a window nothing intersects, an empty `relations=`
filter. Only two things in this package actually raise once your calls are
well-formed, and they are worth handling deliberately because each is telling
you about a different thing:

| Raised | From | Means |
|---|---|---|
| `ValueError` | `relations_in_interval` (and `infer_relations`) | the participant set is too big for quadratic inference — `max_pairs` refused |
| `CursorStalledError` | any of the three methods | the store's cursor stopped advancing — an adapter bug, not your query |

```python
from redstring.temporal.query import CursorStalledError, TemporalQuery

try:
    relations = await query.relations_in_interval(tenant_id, window)
except ValueError:
    ...   # too many dated participants; narrow the question
except CursorStalledError:
    ...   # the adapter is misbehaving; do not retry
```

### Neither is a `RedstringError`

The library's exception gate covers what descends from `RedstringError`, and
neither of these does. `CursorStalledError` is a `RuntimeError` and the
`max_pairs` refusal is a plain builtin `ValueError`, so `except
RedstringError` catches neither. Catch them by name, or let them propagate.
This is the same "temporal is not in the public API" caveat as
[at the top of the guide](#before-you-start-temporal-is-not-in-the-public-api):
these names are reached by dotted path and are not gated.

`ValueError` is also what `TemporalQuery(store, page_size=0)` raises, at
construction rather than at query time — so a bare `except ValueError` around
a call site will not see it, and a bare one around both will not tell the two
apart. Keep construction outside the block.

### They call for opposite responses

The distinction is worth making in code, not just in a log line:

- **`ValueError` from `max_pairs` is about your question.** It is raised
  *before* any comparison, so nothing was computed and retrying identically
  will fail identically and just as fast. The fix is a narrower `interval`, an
  `entity_type`, or a deliberately raised cap. See
  [Handle ValueError from max_pairs](#handle-valueerror-from-max_pairs--default_max_pairs).
- **`CursorStalledError` is about the adapter.** It means `find_entities`
  ignored `after` or did not order by id, so the scan read the same page ten
  thousand times. Retrying makes it worse; there is no window narrow enough to
  work around it. See
  [Handle CursorStalledError](#handle-cursorstallederror-from-a-non-advancing-adapter-cursor).

Nothing partial escapes either one. `entities_in_interval` accumulates its
matches and returns them only after the scan completes, and `infer_relations`
refuses before it compares anything — so a raised call yields no result to
salvage, and no write has happened, because none of this writes anything at
all ([ADR 0005](../adr/0005-temporal-inference-on-read.md)).

### What is *not* one of these

Two other exceptions you may see from this code are neither, and neither is
the library refusing:

- `TypeError: can't compare offset-naive and offset-aware datetimes` — a naive
  `datetime` in your `Bounds`. `Bounds` is a bare `NamedTuple` and validates
  nothing, so it surfaces inside the comparison rather than at construction.
  Pass `tzinfo=UTC`; see
  [domain value types](../reference/domain-value-types.md).
- `TypeError: unhashable type` from `set(relations)` — `InferredRelation`
  contains a pydantic `TemporalExtent`. Key on the endpoint ids instead.

Errors your *store* raises pass straight through unchanged — a Neo4j
connection failure is a Neo4j exception, not a temporal one, because this
package only calls `find_entities` and does no error translation. The
[Neo4j store reference](../reference/neo4j-graph-store.md) covers what that
adapter raises.

## Handle ValueError from max_pairs / DEFAULT_MAX_PAIRS

`relations_in_interval` compares every dated participant against every other,
so the work is quadratic. `max_pairs` is the cap that refuses instead of
grinding:

```python
from redstring.temporal.inference import DEFAULT_MAX_PAIRS

DEFAULT_MAX_PAIRS   # 500_000 -- roughly a thousand dated entities

try:
    relations = await query.relations_in_interval(tenant_id, interval=None)
except ValueError as exc:
    print(exc)
# 1200 dated entities is 719400 pairs, over max_pairs=500000. Inference is
# quadratic and computed on read; narrow the entity set, or raise the cap
# knowingly.
```

`max_pairs` is keyword-only on both `relations_in_interval` and
`infer_relations`, and the exception is a plain builtin `ValueError` — not a
`RedstringError`, so
[`except RedstringError` will not catch it](#neither-is-a-redstringerror).

### It refuses before comparing anything

The count is checked first, so a refusal costs nothing: no relation was
computed, no partial list exists to salvage, and nothing was written (nothing
here ever is — [ADR 0005](../adr/0005-temporal-inference-on-read.md)). The
practical consequence is that **retrying the same call is pointless**. It will
fail identically, and just as fast.

The number in the message is the real pair count, `n * (n - 1) // 2`, where `n`
is the participants — so it tells you how far over you are, not merely that you
are over.

### `n` is the dated participants, not the tenant

The cap is applied after every exclusion this guide has described, and to the
*deduplicated* set:

- entities with `temporal=None`, and extents that denote no interval
  (including `sequence_position`-only ones), are already gone,
- `interval` and `entity_type` have already narrowed the set,
- entities repeated by id are collapsed first.

So a tenant of 100,000 entities of which 300 are dated is 44,850 pairs and
passes comfortably. A tenant where extraction dated most things is where this
fires. `page_size` has no bearing on it at all — it changes round trips, not
participants.

The two boundary cases are what you would want: exactly `max_pairs` is
allowed and one more is refused, and fewer than two participants is zero pairs,
which never trips the cap even at `max_pairs=0`.

### Fix it by narrowing, not by raising the cap

In order of preference:

```python
# 1. Ask about a window rather than all of history.
await query.relations_in_interval(
    tenant_id, Bounds(utc(1990, 1, 1), utc(2000, 1, 1))
)

# 2. Restrict the type -- pushed down to the store, so it also reads less.
await query.relations_in_interval(tenant_id, window, entity_type="Event")

# 3. Raise the cap, knowing what you are buying.
await query.relations_in_interval(tenant_id, None, max_pairs=5_000_000)
```

`interval=None` is the shape that reaches the cap: it means "every dated entity
in the tenant". Passing a window is usually both the correct question and the
cheap one.

Raising `max_pairs` does not make the computation faster — it only removes the
refusal. Ten times the cap is ten times the comparisons, in Python, on **every
call**, with no caching anywhere, because an inferred edge is recomputed fresh
each time and deliberately never stored
([ADR 0005](../adr/0005-temporal-inference-on-read.md); see also [why you
cannot store one](#use-an-inferredrelation--and-why-you-cannot-store-one)).
Lowering it is a reasonable thing to do too: a request-serving path can pass a
small `max_pairs` to get a fast refusal rather than a slow response.

### Why the default is where it is

`DEFAULT_MAX_PAIRS` is 500,000 — about a thousand dated entities. It is
deliberately a number a tenant reaches *by accident* rather than one anyone
asks for on purpose. Past it, the honest answer is that this shape of query
needs a store-side prefilter this library does not have, not that it needs more
patience: the interval predicate cannot be pushed into a `WHERE` clause,
because precision widens a bound and an uncertainty marker can make one
infinite from a field that is neither date column. The
[Neo4j store](../reference/neo4j-graph-store.md) stores an extent but computes
no relation over it, and
[domain value types](../reference/domain-value-types.md) covers the widening
rules that make the predicate what it is.

### Telling this `ValueError` apart from the other one

`TemporalQuery(store, page_size=0)` also raises `ValueError`, at construction.
A bare `except ValueError` wrapped around both cannot distinguish them, so keep
construction outside the block — or match on the message, which names
`max_pairs`. The other failure a real tenant produces,
[`CursorStalledError`](#handle-cursorstallederror-from-a-non-advancing-adapter-cursor),
is a different class and calls for the opposite response: this one is about
your question, that one is about your adapter.

## Handle CursorStalledError from a non-advancing adapter cursor

`CursorStalledError` means the store's paged read stopped making progress.
It is not about your window, your tenant or your data — it is a bug report
about the `GraphStore` you handed to `TemporalQuery`:

```python
from redstring.temporal.query import CursorStalledError

try:
    found = await query.timeline(tenant_id)
except CursorStalledError as exc:
    print(exc)
# scanning tenant <id> did not finish in 10000 pages. The cursor is not
# advancing -- an adapter's find_entities is either ignoring `after` or not
# ordering by id as the port requires.
```

The message names the two defects that produce it, because they are the only
two. All three methods scan through the same paged read, so it can come out of
`entities_in_interval`, `timeline` or `relations_in_interval` alike.

### What the scan does, and what it needs from the adapter

Each round trip is
`find_entities(tenant_id, entity_type=..., limit=page_size, after=cursor)`,
with `cursor` set to the **last entity id of the previous page**. The loop
stops when a page comes back shorter than `page_size`. That exit condition is
adapter-supplied data, so the loop is bounded: after `MAX_PAGES` — 10,000, five
million entities at the default `page_size` — it raises rather than continuing.

The bound exists because the alternative is a hang, and a hang in CI reads as
infrastructure trouble and gets retried rather than investigated. No tenant
reaches 10,000 pages by accident, so reaching it means the cursor stopped
advancing, not that the tenant is large.

Two clauses of the `GraphStore` port make the scan terminate, and the error
fires when an adapter honours neither:

- **`after` resumes strictly after that id.** An adapter that accepts `after`
  and ignores it returns the same first page forever — every page is full, the
  short-page exit never fires, and the same entities are yielded 10,000 times.
- **Results are ascending by `Entity.id`**, compared as the canonical
  lowercase hyphenated string. A cursor over an undefined order is not
  resumable: an adapter that pages in insertion order, or in whatever order
  the database felt like, can hand back a page whose last id it has already
  passed, and the scan revisits ground indefinitely.

Neither is optional and neither is a `TemporalQuery` invention — they are in
the port's `find_entities` contract, which every adapter agrees to. The
[Neo4j store reference](../reference/neo4j-graph-store.md) covers how that
adapter satisfies them.

### Do not retry, and do not narrow the query

This is the opposite response from
[the `max_pairs` `ValueError`](#handle-valueerror-from-max_pairs--default_max_pairs).
That one is about your question and a narrower `interval` or an `entity_type`
fixes it. This one is not:

- **Retrying re-runs the same 10,000 round trips** and fails the same way,
  slowly. It is the most expensive failure in this guide, because unlike the
  `max_pairs` refusal it fires *after* the work rather than before.
- **No window is narrow enough.** `interval` and `relations=` filter in Python
  after the scan, so they change nothing about the paging. `entity_type` is
  pushed down, but a broken cursor is broken for a filtered scan too.
- **A smaller `page_size` makes it worse** — the same defect now needs fewer
  entities to exhaust 10,000 pages. A larger one delays the symptom without
  fixing anything.

Nothing partial escapes: the methods accumulate matches and return only after
the scan completes, so a stalled scan yields no result to salvage. Nothing was
written either, because nothing here writes at all
([ADR 0005](../adr/0005-temporal-inference-on-read.md)).

The fix is in the adapter. Reproduce it directly against the store, outside
`TemporalQuery`:

```python
page = await store.find_entities(tenant_id, limit=2)
again = await store.find_entities(tenant_id, limit=2, after=page[-1].id)
assert [e.id for e in page] == sorted(e.id for e in page)   # ordering clause
assert page[0].id not in {e.id for e in again}              # `after` clause
```

If either assertion fails, that is the bug, and the compliance suite for
`GraphStore` is where the adapter should have caught it.

### Catch it by name — it is not a `RedstringError`

`CursorStalledError` subclasses `RuntimeError`, not `RedstringError`, so
`except RedstringError` will not see it, and it is reached by dotted path from
`redstring.temporal.query` like everything else in this guide
([temporal is not in the public API](#before-you-start-temporal-is-not-in-the-public-api)).

`except RuntimeError` catches it, but catching it at all is usually the wrong
move. There is no recovery available in the caller — the only correct
responses are to fail loudly and to fix the adapter. If you do catch it, do so
to attach context (which tenant, which adapter) and re-raise; treating it as a
transient and falling back to a narrower query converts a reproducible adapter
defect into an intermittent wrong answer.

Errors your store raises on its own pass straight through unchanged. A Neo4j
connection failure is a Neo4j exception, not a `CursorStalledError`: this
package calls `find_entities` and translates nothing. See the
[Neo4j store reference](../reference/neo4j-graph-store.md) for what that
adapter raises, and
[domain value types](../reference/domain-value-types.md) for the vocabulary of
the entities the scan is collecting.

## Tune page_size (and why page_size < 1 raises)

`page_size` is the one keyword argument on the constructor, and it sets how
many entities each `find_entities` round trip asks for:

```python
from redstring.temporal.query import DEFAULT_PAGE_SIZE, TemporalQuery

DEFAULT_PAGE_SIZE   # 500

query = TemporalQuery(store)                   # 500 per round trip
chatty = TemporalQuery(store, page_size=50)    # more trips, smaller pages
greedy = TemporalQuery(store, page_size=5000)  # fewer trips, bigger pages
```

It is keyword-only, it is fixed for the life of the object, and no method
takes a per-call override. If you want two page sizes, build two
`TemporalQuery` objects over the same store — construction touches no I/O.

### It never changes the answer

This is the property to rely on. Every method pages until the tenant is
exhausted, so `page_size` changes only the number of round trips:

```python
a = await TemporalQuery(store, page_size=4).timeline(tenant_id)
b = await TemporalQuery(store, page_size=5000).timeline(tenant_id)
a == b   # True -- same entities, same order
```

It is **not** a limit, not a `LIMIT`, and not a way to cap a large result. A
tenant of 25 entities read at `page_size=4` returns all 25, and a tenant that
exactly fills its final page still terminates rather than looping for one more.
If you want fewer results, narrow the `interval` or pass an `entity_type`; if
you want to bound the cost of `relations_in_interval`, that is
[`max_pairs`](#handle-valueerror-from-max_pairs--default_max_pairs), which
`page_size` has no bearing on — it changes round trips, not participants.

### `page_size < 1` raises `ValueError`, at construction

```python
TemporalQuery(store, page_size=0)    # ValueError: page_size must be at least 1, got 0
TemporalQuery(store, page_size=-1)   # ValueError: page_size must be at least 1, got -1
```

The reason is the paging loop. Each trip asks for `page_size` entities and
resumes from the last id of the page it got; a request for zero returns an
empty page, the cursor never advances, and the scan would page until
`MAX_PAGES` and then raise
[`CursorStalledError`](#handle-cursorstallederror-from-a-non-advancing-adapter-cursor)
— blaming the adapter for something the caller did. Refusing at construction
turns 10,000 useless round trips into an immediate error with the offending
value in the message.

Two practical consequences:

- **It fires before any query runs**, so it cannot appear mid-scan and there is
  no partial result. A `page_size` bug is visible the moment you build the
  object.
- **It is a plain builtin `ValueError`**, the same class `max_pairs` raises,
  and not a `RedstringError` — so
  [`except RedstringError` catches neither](#neither-is-a-redstringerror). A
  bare `except ValueError` wrapped around both construction and a call cannot
  tell them apart; keep construction outside the block, or match on the
  message, which names `page_size`.

`page_size=1` is legal. It is a page per entity and one round trip per entity,
which is slow but correct — useful for exercising an adapter's cursor.

### Choosing a value

There is no measurement in this repo that says 500 is optimal for your store;
it is chosen so a modest tenant is one or two round trips while a page is not a
memory event. Adjust it against your adapter, not against your data:

- **Lower it** when a page is expensive to materialise — a remote store with a
  row-size problem, or a memory ceiling you are close to. Note that the whole
  *result* is still held in memory: these methods return lists, not iterators,
  so a small `page_size` bounds the page, never the answer.
- **Raise it** when round trips dominate — a high-latency
  [Neo4j](../reference/neo4j-graph-store.md) connection where 500 rows and
  5,000 rows cost about the same per query.
- **Leave it alone** if the query feels slow and the tenant is heavily dated.
  That cost is the O(n²) inference and the Python-side interval arithmetic,
  which `page_size` does not touch. The predicate cannot be pushed into the
  store at all — precision widens a bound and an uncertainty marker can make
  one infinite ([ADR 0005](../adr/0005-temporal-inference-on-read.md),
  [domain value types](../reference/domain-value-types.md)) — so the levers
  that actually reduce work are `interval`, `entity_type` and `max_pairs`.

One interaction worth knowing: a smaller `page_size` makes a *broken* adapter
cursor fail sooner, because the same defect needs fewer entities to exhaust the
10,000-page bound. That is a diagnostic accident, not a fix — see
[CursorStalledError](#handle-cursorstallederror-from-a-non-advancing-adapter-cursor).

## Narrow the scan with entity_type

`entity_type` is keyword-only, defaults to `None`, and is accepted by all
three methods:

```python
await query.entities_in_interval(tenant_id, window, entity_type="Person")
await query.timeline(tenant_id, entity_type="Person")
await query.relations_in_interval(tenant_id, window, entity_type="Event")
```

Unlike `page_size` — which changes only round trips — this one changes the
answer: entities of any other type are absent from the result, and in
`relations_in_interval` they take no part in any pair.

### It is pushed down to the store, so it reads less

The filter is handed to `find_entities` on every page rather than applied to
what comes back, so it is the one lever here that reduces I/O as well as work.
On the [Neo4j store](../reference/neo4j-graph-store.md) it becomes an
`e.entity_type = $entity_type` clause backed by the
`(tenant_id, entity_type)` index; on the in-memory adapter it is an equality
test inside the same comprehension that does the paging.

That is the whole reason to prefer it to filtering the returned list yourself.
A post-filter reads the tenant and then discards it; this one never reads it.
It also composes with everything else — `interval`, `relations=` and
`max_pairs` all apply to what survives the type filter, so combining a narrow
window with a type is the cheap shape of every query in this guide.

### It is exact equality, on the canonical type

The match is `==` against `Entity.entity_type`, with no normalisation, no
case folding and no substring or prefix matching. `"Person"` does not match
`"person"` and does not match `"PersonOrOrg"`. There is no list form either —
one type per call. If you want two, make two calls and concatenate, or leave
`entity_type=None` and filter the result.

The field it matches is the *canonical* type, the one schema mapping settled
on. An entity also carries `original_entity_type` — whatever the extractor
first called it — and nothing here looks at that field. So an entity mapped
from `"HUMAN"` to `"Person"` is found by `entity_type="Person"` and never by
`entity_type="HUMAN"`. See
[domain value types](../reference/domain-value-types.md) for the entity
vocabulary.

### An unknown type is an empty result, not an error

There is no registry of valid types to check against, so a typo returns `[]`
from `entities_in_interval` and `timeline`, and `[]` from
`relations_in_interval` — the same value an honest empty window gives, and the
same value an empty `relations=` filter gives. Nothing tells you which
happened.

The way to tell them apart is to drop one constraint at a time:

```python
await query.timeline(tenant_id)                          # any dated entity at all?
await query.timeline(tenant_id, entity_type="Persson")   # [] -- the typo
```

If the first is non-empty and the second is not, the type string is the
problem.

### It does not rescue an undated entity

Type narrowing happens on the read; the dated-only rule happens after it, and
neither overrides the other. An entity of the right type with `temporal=None`,
or one whose extent denotes no interval (including a `sequence_position`-only
extent), is still dropped — see
[what never appears in results](#what-never-appears-in-results-undated-entities-sequence_position-only-extents).
So `entity_type="Person"` on a tenant whose people are all undated returns
`[]` however many people there are.

### Where it helps most

- **`relations_in_interval`.** Inference is O(n²) in participants, so halving
  `n` quarters the work, and the cap it may be tripping is
  [`max_pairs`](#handle-valueerror-from-max_pairs--default_max_pairs).
  `entity_type` is one of the two supported ways to get under it — a narrower
  `interval` is the other.
- **A tenant that is mostly one type.** The scan is linear in the tenant's
  entity count regardless of how few entities are dated, because the interval
  predicate cannot be pushed into the store at all: precision widens a bound
  and an uncertainty marker can make one infinite, from a field that is
  neither date column ([ADR 0005](../adr/0005-temporal-inference-on-read.md)).
  `entity_type` is the only part of these queries the store can help with.

If a single type is still too much, there is no third lever in this package —
the remaining option is a narrower `interval`, and past that the honest answer
is that the query needs a store-side prefilter this library does not have.

## What never appears in results (undated entities, sequence_position-only extents)

Every method in this guide answers a question about *when*, so an entity that
carries no answer to it is dropped before any comparison. This is silent and by
design: most entities in a graph are not events, and treating "this person has
no dates" as an error would make the common case the failure case.

An entity takes part only if `bounds(entity.temporal)` returns an interval.
That is the whole rule, and it excludes three shapes:

```python
from redstring.domain.interval import bounds
from redstring.domain.temporal import TemporalExtent

entity.temporal is None                              # never appears
bounds(TemporalExtent(sequence_position=3))          # None -- never appears
bounds(TemporalExtent(original_text="last summer"))  # None -- never appears
```

- **`temporal=None`.** Extraction found no temporal expression, or the entity
  is not the kind of thing that has dates.
- **An extent with only a `sequence_position`.** Real information — this event
  came third — but not information about *when*, and no interval comparison
  applies to an ordinal.
- **An extent with dates missing but other fields set.** `original_text`,
  `precision`, `uncertainty` or `publication_date` alone denote no interval
  either: `bounds` reads `start_date` and `end_date`, and returns `None` when
  both are absent. A `publication_date` is when the *document* was published,
  not when the entity happened, and nothing here promotes one to the other.

`bounds` returning `None` is not an error and never raises. If you call it
yourself, handle the `None` — see
[Build a window from an extent with `bounds`](#build-a-window-from-an-extent-with-bounds).

### No argument brings them back

There is no `include_undated=` and no relation that matches them. Each of these
returns nothing extra:

| You might try | What happens |
|---|---|
| `Bounds(None, None)` | every *dated* entity; the undated are still absent |
| `timeline(tenant_id)` with `interval=None` | the whole *dated* timeline |
| any `relations=` set, including all six | relations are computed from intervals; there is no interval to compare |
| `entity_type="Person"` | narrows the read, then the dated-only rule applies to what survives |

The six `TemporalRelation` members partition the *dated* entities of a tenant
for a given window. Undated entities are not a seventh bucket — they are gone
before `relate_bounds` is called, so no filter can select them.

In `relations_in_interval` the consequence is stronger: an undated entity takes
part in no pair, so it appears in no `InferredRelation`, as source or as
target. It also does not count towards
[`max_pairs`](#handle-valueerror-from-max_pairs--default_max_pairs), which is
why a tenant of 100,000 entities with 300 dated ones is 44,850 pairs rather
than billions.

### The failure this produces looks like a bug and is not

The symptom is an empty or short list from a tenant you know has data, with no
exception and no warning. Three different causes give the identical `[]`:

- nothing is dated,
- your window matched nothing,
- your `relations=` or `entity_type` filter matched nothing.

Drop constraints one at a time to tell them apart, starting from the widest
question this package can ask:

```python
await query.timeline(tenant_id)                         # every dated entity
await query.timeline(tenant_id, entity_type="Person")   # ... of one type
await query.timeline(tenant_id, interval=window)        # ... in a window
```

If the first is already `[]`, the tenant has no dated entities and no query in
this guide will return anything. To see how many of the tenant's entities are
dated at all, read the store directly — `find_entities` returns everything, and
`timeline` returns the dated subset:

```python
all_entities = await store.find_entities(tenant_id, limit=1000)
dated = await query.timeline(tenant_id)
len(all_entities), len(dated)
```

That is also the honest cost picture: the scan is linear in the *tenant's*
entity count while the answer is bounded by the dated subset, because the
interval predicate cannot be pushed into the store
([ADR 0005](../adr/0005-temporal-inference-on-read.md)).

### What to do with the undated ones

Nothing in `redstring.temporal` will order or relate them — that is the point
of the exclusion rather than a gap in it. If you need them:

- **Read them from the store.** `GraphStore.find_entities` returns every
  entity regardless of `temporal`, and the
  [Neo4j store](../reference/neo4j-graph-store.md) stores an extent as
  ordinary properties whether or not it holds dates.
- **Order sequence-only entities yourself** on `TemporalExtent.sequence_position`.
  It is a per-document ordinal, not a global clock, so comparing positions
  across documents is meaningless — which is exactly why this package refuses
  to do it for you.
- **Improve the dates rather than the query.** Re-extraction under a new model
  version is the supported way an extent gains dates; once it has them the
  entity appears in these results with no change to your calls.

See [domain value types](../reference/domain-value-types.md) for every field a
`TemporalExtent` records and which of them `bounds` reads.

## Related reading

- [ADR 0005: temporal inference is computed on read](../adr/0005-temporal-inference-on-read.md)
  — the decision underneath every section above: why an inferred edge is
  recomputed on each call, never written to the log, and why the O(n²) cost and
  `max_pairs` are the price of that rather than an unfinished optimisation.
- [Domain value types](../reference/domain-value-types.md) — the specification
  for `TemporalExtent`, `DatePrecision`, `UncertaintyMarker`, `Bounds`,
  `TemporalRelation`, `bounds` and `parse_temporal`. Read it for the widening
  rules that decide which entities a window matches, and for which fields
  `bounds` actually reads.
- [Neo4jGraphStore](../reference/neo4j-graph-store.md) — what the store does
  supply: `find_entities` paging, the `(tenant_id, entity_type)` index that
  `entity_type` uses, and how an extent is stored. It computes no relation and
  never sees your window.
- [Implement a store adapter](implement-a-store-adapter.md) — the `find_entities`
  cursor contract (`after` resumes strictly after that id; results ascend by
  `Entity.id`) whose violation is what
  [`CursorStalledError`](#handle-cursorstallederror-from-a-non-advancing-adapter-cursor)
  reports, and the compliance suite that catches it before `TemporalQuery` does.
- [ADR 0006: the public surface is gated](../adr/0006-the-public-surface-is-gated.md)
  — why `redstring.temporal` is reached by dotted path and what that costs you;
  see [before you start](#before-you-start-temporal-is-not-in-the-public-api).
