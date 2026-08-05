# 0007. No ANN index in a multi-tenant vector store

## Status

Accepted, slice 5 (the pgvector adapter). Pinned by three integration tests and
one BACKLOG entry (B10k); revisiting it means arguing with
`src/redstring/vector/adapters/pgvector.py`'s module docstring first.

## Context

`PgVectorStore` is the second `VectorStore` adapter, and the obvious build for
it is an `hnsw` or `ivfflat` index on the `embedding` column. That is what
every pgvector tutorial does, and it is what makes vector search sublinear.
This library does not have one, and the reason is not performance
indifference — it is that the index and the tenant filter interact badly, in a
way that produces plausible results rather than an error.

### The port rule this protects: filters are applied before `k`

`VectorStore.search` in `src/redstring/ports/vector_store.py` says it outright:

> **Filtering happens before `k` is applied.** A store that took the `k`
> nearest and *then* filtered would return fewer than `k` results while
> matching records existed further down the ranking — correct-looking and
> wrong, and indistinguishable from a small corpus. This is the single most
> important sentence in this port for an adapter over an approximate index.

That last clause is the whole of this ADR. An ANN index is exactly what would
turn `PgVectorStore` into "an adapter over an approximate index", and the
sentence is addressed to it in advance.

Three separate things count as filters under that rule, and they are not
equally visible:

- `tenant_id`, which is a **positional, required argument** rather than an
  optional predicate. There is no cross-tenant read on this port at all, so
  the rule is not an edge case for an unusual query — it governs every
  `search` call the library makes.
- `entity_types`, read through `entity_type_of` against
  `metadata["entity_type"]`.
- `min_score`, which drops results scoring strictly below it.

The port also promises `k` is a *ceiling with a stated cause*: "never more than
`k` results; fewer only when the tenant holds fewer matching records." A
short list is therefore a claim about the corpus, not about the plan — which
is what makes outcome A below a lie rather than a degradation. The tie-break
by ascending `entity_id` exists for the same reason: `k` cutting through a tie
must cut the same way on every backend.

### What an `hnsw` or `ivfflat` index on `embedding` would do

An `hnsw` or `ivfflat` index is built over one column: `embedding`. It knows
the vector space and nothing else — not `tenant_id`, not `entity_type`, not
`min_score`. Its whole value is that it answers "nearest `k` to this vector"
without visiting every row, and that is precisely the operation this port is
never allowed to perform on its own.

The statement it would have to serve is `_search_sql`, which is a single
`SELECT` with three filters in `WHERE`, an `ORDER BY score DESC,
entity_id::text ASC`, and a `LIMIT`. Written that way, the port's rule is a
consequence of SQL's own evaluation order — `WHERE` runs before `ORDER BY` and
`LIMIT`, so filtering necessarily happens before `k` is applied. The
`_search_sql` docstring says outright that this holds *only while there is no
ANN index*.

That caveat is the entire decision. An ANN index does not add a faster way to
execute the statement above; it offers the planner a way to execute a
**different** statement, one where the ordering and the truncation are
performed *inside* the index scan, before the filters have been seen. The
ordering and the `LIMIT` stop being separate steps the `WHERE` precedes and
become the index's own traversal. So the planner has two choices, and — this
is what makes it dangerous rather than merely slow — both are defensible
plans that return no error either way.

#### Outcome A: index-then-filter — plausible results, silently short of `k`

The planner walks the vector index for the globally nearest rows, then applies
`WHERE` to what comes back. The filters are no longer a precondition of the
ranking; they are a sieve over an already-truncated list. Rows belonging to
other tenants, rows of the wrong `entity_type`, and rows below `min_score` are
dropped *after* the cut, and nothing goes back to the index to replace them.

The consequence scales with how thin a slice of the table the tenant owns. A
tenant holding 1% of the rows sees roughly 1% of any batch the index returns
survive the `tenant_id` predicate — a handful of results, or none, for a query
with thousands of genuine neighbours in that tenant. `entity_types` and
`min_score` compound it, because each is another predicate the index never
saw.

This is exactly what `VectorStore.search` forbids: "a store that took the `k`
nearest and *then* filtered would return fewer than `k` results while matching
records existed further down the ranking." And the damage is not that the
query is wrong — it is that the query is *unfalsifiable from its output*. The
port promises `k` is a ceiling with one stated cause: fewer than `k` results
means "the tenant holds fewer matching records." Under this plan that sentence
becomes a lie the caller has no way to detect. A consolidation pass looking
for near-duplicates finds none and concludes there are none.

Two properties make it worse than an ordinary bug:

- **It is not deterministic across environments.** Whether the planner picks
  this plan depends on statistics, tenant skew, `k`, and the pgvector version.
  A staging database with three tenants and a production one with four hundred
  can run identical code and take different plans, so the failure appears at
  the moment the corpus grows past where anyone tested.
- **It degrades rather than breaks.** No exception, no empty result to
  investigate, no log line. `search` returns a well-formed, correctly ordered,
  correctly scored list of `VectorMatch`es. Every one of them is a true
  neighbour. The defect is entirely in what is *absent*.

The exits in BACKLOG B10k are all ways of denying the planner this plan rather
than ways of detecting it: partitioning by `tenant_id` gives each index one
tenant's rows, so post-filtering has nothing left to discard, and pgvector
0.8's iterative scan makes the index keep producing candidates until `k`
survive the filter.

#### Outcome B: filter-then-scan — the index is dead write cost

The other plan is the one this adapter already gets without an index: resolve
`tenant_id` first — through the primary key's leading column — and rank what
survives. The vector index is never opened, because once the tenant predicate
has narrowed the row set, an access method that can only order the *whole*
table by distance has nothing to contribute to it.

The answers here are entirely correct. The port's rule holds, `k` means what
it promises, and `search` returns exactly what the in-memory adapter would.
That is the point: this outcome is not a bug, and it is the reason an ANN
index cannot be justified as a hedge. It buys nothing on the read side and
charges for it on the write side.

What it charges is not nominal. `upsert_many` is built for batches — one
`INSERT ... SELECT * FROM unnest(...)` over five arrays, thousands of rows in
a single round trip — and every one of those rows would have to be inserted
into the HNSW graph or assigned to an `ivfflat` list. Embedding ingestion is
the write path this library actually has, so the index would be paid on the
hottest write and read on none. `ivfflat` adds a second cost of the same kind:
its lists are built from the data present when the index is created, so it
also needs periodic rebuilding to stay useful — maintenance for a structure
no query consults.

Two things follow, and they are why this outcome is documented at all rather
than dismissed as harmless:

- **It is not a stable state.** The planner chooses per query, from
  statistics. The same index that is inert for a tenant holding a large share
  of the table can look attractive for a query where the tenant predicate
  seems cheap to apply afterwards — and that is outcome A. A deployment
  observing outcome B has not avoided outcome A; it has been given a plan that
  happens to be right today, on this data. There is no setting that pins it,
  which is what makes "add the index and watch for problems" unworkable: the
  failure mode it would be watching for is silent by construction (see the
  next section).
- **It hides the absence of a benefit.** A team that adds the index and
  measures no regression concludes it is fine. It is fine, and it is also
  doing nothing — which is the harder thing to notice, and which leaves the
  index in place to be selected later on different statistics.

So the honest reading of the pair is that an ANN index on this table is either
wrong or useless, decided by the planner rather than by the author, one query
at a time. The exits in BACKLOG B10k all remove that choice: partitioning and
per-tenant partial indexes give the index a row set that is already one
tenant, and `hnsw.iterative_scan` makes the index keep producing candidates
until `k` survive the filter. None of them is "add the index and hope for
outcome B".

### Why no results-only test can catch outcome A

The natural objection to all of the above is that the compliance suite already
tests for it. `VectorStoreCompliance.test_filters_are_applied_before_k` puts
six nearer records of one `entity_type` in front of two matching ones, asks
for `k=2` of the matching type, and requires both to come back; a store that
took the `k` nearest and filtered afterwards returns an empty list. That test
is real, it is shared by both adapters, and it would not detect outcome A.

The reason is that a results-only assertion has to compare what came back
against what *should* have come back, and under outcome A the two differ only
by rows the caller was never told existed. `search` returns a list of
`VectorMatch`es that is well-formed, correctly ordered, correctly scored, and
entirely composed of genuine neighbours in the right tenant. Nothing in it is
wrong. The defect is the absence of rows, and the port's own contract makes
absence unremarkable: "never more than `k` results; fewer only when the tenant
holds fewer matching records." A caller receiving three results cannot
distinguish "the index cut to `k` before the tenant predicate was applied"
from "this tenant genuinely holds three matching records", because the port
tells it the second reading is the normal one.

The compliance test escapes that only because it constructs the corpus itself
and therefore knows the true answer. Three things stop that construction from
transferring to outcome A:

- **The oracle does not exist for `tenant_id`.** The compliance test can seed
  eight records and name the two that must survive an `entity_types` filter.
  The equivalent for tenants would have to seed one tenant's rows *and* enough
  of every other tenant's rows to make the index return mostly foreign
  candidates — and then assert against a count the test computed from the same
  seeding. It is buildable, and it is the shape `CLAUDE.md` warns about: an
  expectation written in terms of the thing under test.
- **The dataset that makes it fire is the dataset that makes it exact.**
  Outcome A requires the planner to choose the vector index, which on tens of
  rows it will never do — a sequential scan is genuinely cheaper, and the
  answers are exact. The compliance suite's tier 1 states this outright: tens
  of vectors, "where every sensible backend falls back to a sequential scan
  and *is* exact". So the tier that owns filter-before-`k` runs precisely at
  the size where an ANN index is inert, and the tier that uses a larger corpus
  claims only recall — the true nearest neighbour appearing somewhere in the
  top-k — which outcome A satisfies comfortably while dropping everything
  else.
- **The plan is not a property of the code.** Even at a size where the index
  is attractive, whether the planner takes it depends on statistics, tenant
  skew, `k`, and the pgvector version. A results test that happened to catch
  it once would pass on the next `ANALYZE`. That is worse than no test, by the
  standard this project already applies to non-deterministic coverage:
  something that is green today and red tomorrow with no source change reads
  as flake and gets muted.

Note that `min_score` is not a way out either, and the compliance suite
explains why in place: it is monotone in the score, so it can only ever remove
a suffix of the ranking, and "filter then take `k`" and "take `k` then filter"
agree on it. Only a filter that cuts *anywhere* in the ranking — `tenant_id`,
`entity_types` — can expose the ordering, and `tenant_id` is the one no test
can supply an independent oracle for.

What follows is not "this cannot be tested" but "this cannot be tested from
the results". The plan is observable, deterministic under `EXPLAIN`, and
directly expresses the thing at issue: whether the filters are evaluated below
the `Limit` or above it. So the pins below assert over the query plan, and one
of them asserts the *absence of the index* outright — the cheapest possible
statement of the decision, and the only one that cannot be satisfied by a
plan that happens to be right today.

## Decision

**`PgVectorStore` creates no index on `embedding`.** Search scans **within the
tenant** — the tenant predicate is served by a btree, and the ranking is a
brute-force distance computation over the rows that survive it.

The decision is stated as an absence, and that is the shape it has to keep.
There is no configuration flag, no `create_index=True` escape hatch, and no
"add it if your deployment is large" advice: the failure mode is outcome A,
which no caller can observe from results, so an option to enable it would be
an option to silently break the port for whoever set it. Adding the index is
a change to this ADR and to the test that forbids it, not a deployment
choice.

### What is built instead: `PRIMARY KEY (tenant_id, entity_id)` and the `(tenant_id, entity_type)` index

`PgVectorStore._schema_statements` returns the DDL **as data** — a tuple of
strings rather than a sequence of `execute` calls — so a test with no server
can read what would be created and assert over it. `ensure_schema` runs
`CREATE EXTENSION IF NOT EXISTS vector` and then each statement in turn, every
one written `IF NOT EXISTS`, so bringing a store up against an existing table
is a no-op rather than an error.

The tuple has two entries, and between them they define exactly two btrees and
nothing else:

```sql
CREATE TABLE IF NOT EXISTS kg_vectors (
  tenant_id uuid NOT NULL,
  entity_id uuid NOT NULL,
  embedding vector(<dimension>) NOT NULL,
  entity_type text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (tenant_id, entity_id)
)

CREATE INDEX IF NOT EXISTS kg_vectors_tenant_type_idx
ON kg_vectors (tenant_id, entity_type)
```

Both lead with `tenant_id`, and that is the whole substitute for an ANN index.
It turns a tenant-scoped read into a seek to one tenant's rows rather than a
scan of every tenant's — the trap slice 4 hit on Neo4j, where correct results
hid a whole-database scan no behavioural test could see. The work an ANN index
would have saved, ordering by distance, is still done row by row; it is just
done over one tenant's slice instead of the table. That is the trade this ADR
makes: the tenant predicate is indexed, the ranking is not.

Each btree carries a second job as well, which is why neither is redundant
with the other.

**The composite primary key does more than order the rows.** Keyed on
`(tenant_id, entity_id)`, the same `entity_id` under two tenants is two
distinct rows — a key on `entity_id` alone would have rejected the second
tenant's write, which is the arrangement the tenant-isolation properties
depend on most. It is also the conflict target `upsert_many` names:
`ON CONFLICT (tenant_id, entity_id) DO UPDATE`, so last-write-wins is per
tenant rather than global. And `deduplicate` has to collapse a batch on that
same key before the insert, because Postgres refuses to let one statement
affect a row twice. The key is therefore load-bearing for isolation,
idempotence and batching at once, not merely for lookup speed.

**The `(tenant_id, entity_type)` index covers the port's one indexable
filter.** `entity_type` is a real `text` column, written on every upsert from
`entity_type_of(record.metadata)` — the port's single reading of the
`metadata["entity_type"]` convention. The alternative, filtering on
`metadata->>'entity_type'`, is a per-row JSON parse the planner cannot index.
The `metadata` jsonb is still stored whole and remains the source of truth;
the column is a projection of it, which is why a metadata write replaces both
in the same `DO UPDATE`.

The column is deliberately nullable, and the null means something: a record
whose metadata carries no string `entity_type` gets `NULL`, and `NULL =
ANY($4)` is never true, so such a record matches no type filter and asking
never raises. That is the port's stated rule, not an accident of the column
type — `entity_type_of` returns `None` for an absent, numeric, list or object
value, and the in-memory adapter reads it the same way.

There is deliberately **no third index on `tenant_id` alone**: either of the
two above serves a bare tenant predicate from its leading column, and a third
would be write cost for a read nothing performs. And there is deliberately no
index on `embedding` — the subject of this ADR, pinned by
`test_there_is_no_ann_index_on_the_embedding` below, which asserts the set of
access methods on the table is exactly `{"btree"}`.

One thing the DDL fixes that is worth naming here because it constrains
deployment rather than performance: `vector(n)` bakes the dimension into the
column type, so a store built for 768 dimensions cannot share a table with one
built for 1024. `ensure_schema` reads the declared typmod back from
`pg_attribute` and raises `DimensionMismatchError` rather than letting the
first insert fail with a Postgres error naming neither store nor model. One
table per embedding model; see
[How to use the pgvector store](../how-to/use-the-pgvector-store.md) for the
operational form of that.

### Why the SQL then satisfies the port without any further mechanism

`_search_sql` is a single `SELECT`: three predicates in `WHERE`, `ORDER BY
score DESC, entity_id::text ASC`, then `LIMIT`. Filter-before-`k` is not
implemented anywhere in this adapter — it falls out of SQL's own evaluation
order, because `WHERE` runs before `ORDER BY` and `LIMIT`. All three filters
ride in that `WHERE`: the tenant, the optional `entity_types` list (with a
"no filter" boolean parameter beside it, so an empty list still means "match
nothing"), and `min_score`.

The tie-break is the port's documented total order, `entity_id::text`
ascending, so `k` cutting through a tie cuts the same way here as in memory.

That inheritance is the load-bearing consequence of the decision, and its
docstring says so: it holds **only while there is no ANN index**. With one,
the ordering and the truncation can move inside an index scan that has never
seen the `WHERE` clause, and the guarantee stops being a property of SQL and
becomes a property of whichever plan the planner picked today.

Two smaller behaviours lean on scanning as well. `search` returns `[]` for
`k == 0` without issuing a query, because the port promises that regardless
of what the tenant holds. And `_check` rejects a zero vector client-side:
`<=>` against one yields NaN, which sorts unpredictably — under a scan that is
a value the adapter refuses, but it is exactly the kind of thing whose
observable effect would otherwise depend on the plan.

### The consequence for the compliance suite: exact, not merely recall

`tests/compliance/vector_store.py` is the executable definition of the port,
and it anticipated this decision rather than recording it. Its module
docstring states the contract in **two tiers**, and the reason it needs two is
precisely the adapter this ADR declines to build: an approximate index "may
omit a true neighbour, and the omission is a legitimate implementation choice,
not a bug."

- **Tier 1 — exact behaviour on a small dataset.** Tens of vectors, "where
  every sensible backend falls back to a sequential scan and *is* exact." It
  asserts exact membership, exact ordering and exact scores. `k` respected,
  filters applied before `k`, the `entity_id` tie-break, self-similarity:
  all of it lives here.
- **Tier 2 — recall on a larger dataset.** The honest weaker claim: the
  single true nearest neighbour appears somewhere in the returned top-k. Not
  its rank, not the rest of the list.

Both tiers bind every adapter; the weaker one is not an escape hatch. There is
deliberately **no `is_approximate` capability flag**, and the suite says why:
a flag that lets an adapter opt out of correctness tests "gets set once, for a
good reason, and from then on the suite is silent about the thing it was
written to check." An adapter that cannot pass tier 1 on ten vectors is not a
`VectorStore`.

Because it scans, `PgVectorStore` is **exact**, and this is the concrete
payoff of the decision. It does not merely clear the weaker tier; it passes
tier 1 for the same reason the in-memory adapter does, which is what makes the
two genuinely interchangeable rather than interchangeable within a tolerance.
`test_scores_agree_with_the_domain_score_function` compares every score
against `cosine_score` itself — not merely the same *order*, the same
*number*, because `min_score` is a value the caller carries between adapters.
The only slack anywhere is `SCORE_TOLERANCE`, and it is there for float4
storage rather than for approximation: the *stored vector* is still asserted
exactly equal, because float32-representable components survive a float32
column unchanged. Weaken the shared claim exactly as far as the backend
genuinely forces, and no further.

The flip side is that tier 2 currently proves nothing, and the suite says so
in its own banner rather than leaving a reader to assume otherwise. Every
adapter in this tree is exact — in-memory scans brute-force, and pgvector has
no ANN index on purpose — so nothing there has ever run against a store that
*can* miss a neighbour. Its passing is evidence about the tests, not about
recall. This ADR is the reason that is true, which is why the honest thing is
to keep saying it in both places.

That is what makes the last exit below a prerequisite rather than a follow-up.
Tier 2 is one query over one deterministically seeded corpus; a real recall
claim needs many queries, a stated recall@k target, and a failure message
reporting the measured rate rather than the single miss that tripped it.
Written after an approximate adapter exists, it becomes a test tuned until
that adapter passes, which is not a test. A permitted-divergence tier is a
placeholder for evidence, and it has to be filled in before the divergence
arrives.

## How the decision is pinned

A decision recorded only in prose is a decision until the next person who
reads a pgvector tutorial. Three tests in
`tests/integration/vector/test_pgvector_store.py` hold this one, and they are
integration tests by necessity: two of them ask the Postgres planner a
question, and there is no planner without a server. They need the container,
and `-m integration` — `addopts` excludes the marker so the commit gate stays
infra-free, which means **none of these three run on `git commit`**. That is a
real gap and worth knowing about: the cheapest of them, the index-absence
check, is the one most likely to matter and the one least likely to be run by
someone adding an index in a hurry.

Each pins a different thing, and the ordering below is from "the index is not
there" to "the plan behaves as the port requires" — the second does not imply
the first, and the first does not imply the third.

### `test_there_is_no_ann_index_on_the_embedding` — every access method on the table is btree

The bluntest possible statement of the decision, and the cheapest. It calls
`store.ensure_schema()` — so what it inspects is the schema the adapter itself
would create, not a table the test hand-rolled — then asks the catalogue what
access methods exist on it, joining `pg_index` to `pg_class` (for the *index*
relation, via `indexrelid`) to `pg_am`, restricted to indexes whose
`indrelid` is this store's table:

```python
assert {row["amname"] for row in methods} == {"btree"}
```

**Set equality rather than a search for `"hnsw"`**, and that matters in both
directions.

- It does not enumerate what is forbidden. `not in {"hnsw", "ivfflat"}` would
  pass for an access method that does not exist yet — a future pgvector index
  type, or a third-party one — and the argument in Context is about
  *approximate nearest-neighbour search*, not about two names. Anything that
  is not a btree has to justify itself here.
- It fails if the btrees stop being there. An assertion that can only ever
  pass is the exemption-list hazard from `CLAUDE.md` in assertion form: a
  check nobody has seen fail is not yet evidence. Drop the
  `(tenant_id, entity_type)` index and this test goes red, which is correct —
  the decision is not "no ANN index", it is "these two btrees *instead of* an
  ANN index", and half of that is a claim about what exists.

It is also the one pin that is a property of the **schema** rather than of a
plan. The other two ask the planner a question, and the planner's answer
depends on statistics, row counts and version; this one is true or false
regardless. That makes it the test most worth running and the one most likely
to be skipped, since it needs the container like the others (`-m integration`,
excluded from `addopts`, so it does not run on `git commit`).

Its docstring is deliberately an argument rather than a description — it
restates the outcome-A failure and ends "whoever adds it has to come here and
argue with this docstring first." That sentence is the whole mechanism. The
test cannot prove the absence is *correct*; this ADR and
`src/redstring/vector/adapters/pgvector.py`'s module docstring do that. What
it does is make the absence **load-bearing**, so an ANN index cannot arrive as
a line in a performance commit — it arrives as a deliberate edit to a red
test, with a docstring pointing at the reasoning it has to defeat. See
[How to implement a store adapter](../how-to/implement-a-store-adapter.md) for
the same move generalised: an adapter over a genuinely approximate index owes
the compliance suite a stronger recall tier first.

Note what it does not do: it says nothing about query plans. A table carrying
exactly the right indexes can still be queried badly — the planner is free to
sequential-scan it — and that is the next test's job.

### `test_a_tenant_scoped_search_seeks_rather_than_scanning_the_table` — 20k rows over 400 tenants, no `Seq Scan`

The previous test says the ANN index is absent. This one says the thing built
*instead of* it actually works — that "scan within the tenant" is a seek to
one tenant's slice and not a euphemism for scanning the table.

400 tenants × 50 rows go in through `upsert_many` in a single call — 20,000
`VectorRecord`s, one statement, which is why seeding a dataset this size is
affordable in an integration test at all. Then `ANALYZE` on the table, then
`EXPLAIN` on the adapter's own search statement via the shared `_explain`
helper. Two assertions:

```python
assert "Seq Scan" not in plan, (
    f"a tenant-scoped search reads every row of every tenant:\n{plan}\n"
    f"The primary key leads with tenant_id; the query must seek on it."
)
assert "Index" in plan
```

Both are needed, and neither implies the other. `"Seq Scan" not in plan` is
the negative claim; `"Index" in plan` is the positive one, and it exists so
the test cannot pass by the plan degenerating into something that is merely
*not* a sequential scan. The failure message names the cause rather than the
symptom — a reader who trips it is told which index was supposed to serve the
predicate, not just that a scan appeared.

This is the pin the whole Decision leans on. "No ANN index" is only defensible
because the tenant predicate is served for free by
`PRIMARY KEY (tenant_id, entity_id)`'s leading column. If the planner scanned
the whole table anyway, the Consequences section's cost claim —
`O(rows in this tenant)` — would be wrong by a factor of the tenant count, and
the honest reading of this ADR would become "no index at all, on any
predicate". The 400-tenant shape is chosen for exactly that: it makes one
tenant's rows 0.25% of the table, so a plan that seeks and a plan that scans
differ by roughly 400× in rows touched while returning identical results.

Which is why it has to be an `EXPLAIN` test. This is the trap slice 4 hit on
Neo4j, asserted rather than assumed: a full scan and an index seek return the
same rows in the same order with the same scores, so no assertion over
`search`'s output can tell them apart. Correct results hid a whole-database
scan there, and the only reason it is not hiding one here is that something
reads the plan.

Two properties of the dataset are load-bearing rather than incidental:

- **The size buys the planner a real choice.** The docstring says it plainly:
  "on a table of ten rows a sequential scan is genuinely correct and the
  assertion would prove nothing about production." A plan assertion is
  evidence only where the plan was decided; below that size this test would be
  asserting that the planner is wrong.
- **`ANALYZE` runs before the `EXPLAIN`.** Without it the planner works from
  stale or default statistics and the plan reflects a table that does not
  exist. Seeding without analysing would make the result depend on autovacuum
  timing — an intermittent test, which by this project's standard is worse
  than none.

The vectors themselves are deliberately dull — `[float(index % 7) + 1.0,
0.0, ...]`, seven distinct directions cycling — because nothing here is about
distances. The query is planned, never executed (`ANALYZE false` in
`_explain`), so the ranking never happens and the test does not measure the
machine it runs on.

Adding a fourth test of this shape means seeding into the per-worker table:
`TABLE` carries `PYTEST_XDIST_WORKER`, so this test's 20,000 rows land in one
worker's `kg_vectors_test_gw*` and no other worker sees them. A shared table
would reproduce BACKLOG B10f exactly.

### `test_the_search_plan_filters_before_it_limits` — `Limit` above the filter, read off the plan

The last pin is the one that checks the port's rule itself, in the only
representation where the rule is expressible. The index-absence test says the
ANN index is not there; the 20k-row test says the tenant predicate seeks. This
one says the *ordering* is right: that the filters are evaluated below the
`Limit` rather than applied to what a `Limit` already committed to — outcome A,
named in the plan instead of guessed at from a short result list.

500 rows go in through one `upsert_many`. `tenant_id` is
`uuid4() if index % 5 else tenant`, so one row in five belongs to the tenant
under test and the other four each belong to a tenant of their own;
`entity_type` alternates `"person"`/`"place"`. Then `ANALYZE`, then `EXPLAIN`
through the shared `_explain` helper with `entity_types=["person"]` — so the
plan carries a type filter as well as the tenant one, which is the whole point:
`min_score` is monotone in the score and cannot expose an ordering error, while
`entity_types` cuts anywhere in the ranking.

Four assertions, in the order the plan is read:

- **the first line of the plan, stripped, is exactly `Limit`.** Not "contains
  a `Limit`" — the top. Anything sitting above it is a node that runs *after*
  `k` has been taken, which is the shape of a post-filter.
- **there is at least one `Index Cond`.** A guard against the plan degenerating
  into something that satisfies the remaining string checks by accident.
- **the first `Index Cond` mentions `tenant_id`.** The tenant predicate is
  resolved by an index, not re-checked afterwards.
- **both `tenant_id` and `entity_type` appear below the `Limit`**, found by
  splitting the plan text on `"Limit"` and searching the remainder.

Two deliberate weakenings, and both are the test being honest about what the
*contract* requires rather than about what the planner happened to emit on the
day it was written:

- **It does not assert which node evaluates `entity_type`.** On a
  well-populated table the planner folds it into the same `Index Cond`; on a
  nearly empty one it leaves it as a `Filter` beneath the scan. Both satisfy
  the port — the rule is about *where* relative to the `Limit`, not about
  which access path. Pinning the richer plan made this test pass or fail
  according to whether the 20k-row test had already run in the same worker's
  table, and `pytest-randomly` randomises that. An order-dependent test is a
  bug in the test, and the fix is to assert the contract rather than the plan.
- **The table is seeded rather than left near-empty.** An earlier version ran
  against a single row, where `assert conditions` leaned on the planner still
  preferring the primary key to a sequential scan of nothing. That assertion
  was not *wrong*; it was not testing anything the planner had to decide — the
  same "the check had no room to fail" hazard as the two pins above, in its
  third form.

What survives is a string search over `EXPLAIN` output, which is a coarse
instrument, and it is worth being plain that it is coarse in the safe
direction: it will not notice a plan that is merely inefficient, and it will
notice the one structural change this ADR forbids. If an ANN index were added
and the planner took it, the ordering and the truncation would move inside the
index scan and the filters would surface above the `Limit` — the first
assertion fails, with the plan in the message.

### Why these are EXPLAIN tests and why the datasets are large

Three choices are shared by the pins above, and each is easy to get wrong in
the same direction: a test that passes, stays green forever, and proves
nothing.

**Why the plan rather than the results.** Everything this ADR is about is
invisible in `search`'s output. A full scan and an index seek return the same
rows, in the same order, with the same scores. An index-then-filter plan
returns a shorter list of rows that are all genuinely correct, and the port
itself says a short list is normal — "fewer only when the tenant holds fewer
matching records." So there is no assertion over `list[VectorMatch]` that
separates any of these, which is the argument made at length in *Why no
results-only test can catch outcome A*. The plan is the only artefact where
the difference is expressible at all, and — unlike the plan actually chosen in
production — it is deterministic once the statistics are fixed. The two
`EXPLAIN` tests are not a stronger version of a behavioural test; they are the
only version that exists.

**Why the datasets are large.** A plan assertion is evidence only where the
planner had a real choice to make. The seek test's docstring puts it directly:
"on a table of ten rows a sequential scan is genuinely correct and the
assertion would prove nothing about production." Asserting `"Seq Scan" not in
plan` against ten rows is asserting that the planner should be wrong, and it
would pass only for as long as the planner stayed wrong. So
`test_a_tenant_scoped_search_seeks_rather_than_scanning_the_table` seeds
400 tenants × 50 rows and `test_the_search_plan_filters_before_it_limits`
seeds 500 rows across a 1-in-5 tenant split, and both run `ANALYZE {TABLE}`
before explaining — without that the planner works from default statistics
describing a table that does not exist, and the result would depend on
autovacuum timing rather than on the schema.

The shape of the data is chosen too, not just the size. 400 tenants makes one
tenant's rows 0.25% of the table, so seek and scan differ by roughly 400× in
rows touched; the 1-in-5 split plus an alternating `entity_type` gives the
second test a filter that cuts *through* the ranking rather than off its end.
The vectors are deliberately dull — seven directions cycling through
`float(index % 7) + 1.0` — because none of this is about distances, and
`ANALYZE false` means the ranking is never computed anyway.

This is the same requirement as the compliance suite's two tiers, seen from
the other side. Tier 1 is small *because* every backend is exact at tens of
vectors; these tests are large *because* nothing about indexing is decided at
tens of rows. A test that ran here at compliance-suite scale would be checking
a decision the planner never took.

The size cuts the other way as well, and it is a real cost rather than a free
win: two tests seeding 20,500 rows are not commit-gate tests. `-m integration`
is excluded from `addopts`, so none of the three runs on `git commit` — which
matters most for the cheap one, the index-absence check, since it is the pin a
performance commit would trip.

**Why the adapter's own SQL.** `_explain` builds nothing. It calls
`store._search_sql()` and prefixes it, so the plan asserted is the plan the
port actually runs; a query restated in the test would drift from the adapter
silently and keep passing while asserting something about a statement nobody
executes. It explains with `ANALYZE false, COSTS false, VERBOSE true` — a
planning question, not a timing one, so these tests do not measure the machine
they run on, and `COSTS false` keeps the asserted text free of numbers that
change with every seed. Reaching through a private name is the deliberate
trade: the alternative is a public accessor on the adapter that exists only
for tests.

One piece of scaffolding to know before adding a fourth test of this shape:
`TABLE` carries `PYTEST_XDIST_WORKER`, so each worker owns its own
`kg_vectors_test_*` and truncates only that. Any new test here will seed
heavily, and seeding into a shared table would reproduce BACKLOG B10f exactly
— the Neo4j suite's 36 failures from workers wiping each other's data
mid-test. Seed into the per-worker table, `ANALYZE` it, and explain the
adapter's own statement.

## Consequences

The decision buys correctness that is stable across environments — every
`search` obeys filter-before-`k` because SQL's evaluation order says so, not
because a plan happened to come out right — and it buys the exact compliance
tier rather than the recall one. It is paid for in exactly one place, and that
place is worth stating precisely rather than waving at.

### The cost: search is linear in one tenant's rows

Ranking is `O(rows in this tenant)` rather than logarithmic: every row matching
the `WHERE` clause has its distance computed, then the whole set is ordered and
truncated. The tenant predicate itself is *not* linear — that is the seek the
primary key's leading `tenant_id` buys, and
`test_a_tenant_scoped_search_seeks_rather_than_scanning_the_table` is what
stops the claim quietly becoming `O(rows in the table)`. So the cost is bounded
by one tenant's slice, not by the corpus, and the 400-tenant shape in that test
is the difference between those two readings.

Whether that matters is entirely a question of the largest tenant, not of the
table:

- At the plan test's 50 rows/tenant it is irrelevant, and would be at a
  thousand.
- At 10^6 rows in a single tenant it will not be. Nothing about the number of
  *other* tenants changes that — a 400-tenant deployment where one tenant holds
  everything has the same problem as a single-tenant one.

The other two filters do not reduce the linear term, and it is worth being
exact about which one might. `entity_types` is a predicate on an indexed
column, so a selective type filter genuinely narrows the set whose distances
get computed — that is what `(tenant_id, entity_type)` is for, and it is
useful precisely because it leads with the tenant rather than standing alone.
`min_score` does not: it is written as
`($5::float8 IS NULL OR 1 - (embedding <=> $2::vector) / 2 >= $5)`, a
predicate over the score itself, so every candidate row's distance has already
been computed by the time it is evaluated. It trims the result, never the
work. Reaching for a tighter `min_score` to make a slow search faster is the
natural move and the wrong one.

The work is also CPU in Postgres rather than I/O — a distance computation per
row at the column's declared dimension — so a search over a large tenant
competes with writes on the same server. `upsert_many` is the counterweight on
that side: one `INSERT ... SELECT * FROM unnest(...)` over five arrays for a
whole batch, because embedding ingestion is where this adapter is asked to
move volume.

What the decision does **not** cost is worth naming too, because it is what an
ANN index would have charged in exchange: there is no index build, no
`ivfflat` list rebuild as the corpus shifts, no HNSW graph insertion on every
upsert, and no recall parameter for an operator to get wrong. Outcome B above
is the reminder that those costs are paid whether or not the index is ever
read.

The operational form of this trade — sizing a deployment by its largest
tenant, and what the schema guarantees regardless — is in
[How to use the pgvector store](../how-to/use-the-pgvector-store.md). The three
ways out, and what each costs, are below and in BACKLOG B10k.

### What has and has not been measured

The *shape* is known; the cost is not. That distinction is the whole of this
section, and it is worth holding on to, because "linear in one tenant's rows"
reads like a benchmark result and is not one.

**What has been established is structural, and all of it comes from `EXPLAIN`
rather than from a clock.** Three claims, one per pin: the table carries no
access method but btree; a tenant-scoped search seeks rather than sequentially
scanning 20,000 rows across 400 tenants; and the filters are evaluated below
the `Limit` rather than above it. Those are claims about plans. They are
deterministic once the statistics are fixed — which is why the two plan tests
run `ANALYZE {TABLE}` before explaining — and they are exactly the claims the
Decision depends on. Nothing in them is a claim about speed.

**What has not been established is any number.** Nothing here has been
profiled. There is no latency figure at any tenant size, no crossover point at
which an approximate index would start to win, and no measurement of what a
distance computation costs per row. The BACKLOG entry says the same thing in
the same words — "measured shape, not measured cost" — and the two should stay
in step.

The integration tests deliberately cannot supply a number, and two properties
of them are the reason:

- **`_explain` runs with `ANALYZE false`.** The statement is planned and never
  executed, so no ranking is ever computed. A plan is a question about the
  planner; a timing is a question about the machine the suite happens to run
  on, and asserting over the second would be a flaky test rather than
  evidence.
- **`VectorStoreCompliance.DIMENSION` is 8.** Every integration test in this
  module builds its vectors at eight components, including the 20,000-row seed.
  A real embedding is 768 or 1536, and the per-row work in the ranking scales
  with that. So even a timing taken from these tests would be a measurement of
  the wrong workload — a hundred-fold off in the one dimension that matters
  for the linear term.

The nearest thing to a cost test in the file is
`test_upsert_many_is_one_statement`, and it is worth being precise about what
it proves: it monkeypatches `pool.execute`, counts statements for 250 records,
and asserts the count is exactly 1. That is a claim about **round-trip
structure**, not about throughput — it catches a refactor that turns the batch
insert back into a loop, which is a shape regression visible without a clock.
It says nothing about how long the insert takes, and it is on the write path
rather than the read path this decision constrains.

So treat "linear" as an argument about the plan. It states the *shape* of the
cost curve and says nothing about where on that curve any deployment sits.
Two things follow for anyone who believes they have outgrown this decision:

- **Measure the largest tenant before reaching for an exit.** The cost is
  bounded by one tenant's slice, not by the corpus, so the number that decides
  this is rows-in-the-largest-tenant at the production embedding dimension —
  not table size, not tenant count. Every exit below costs something
  permanent, and picking one on the strength of "linear sounds slow" trades a
  known-exact store for a configurable one on no evidence.
- **A measurement has to include recall, not just latency.** Every exit makes
  the store approximate in some regime, and the compliance suite cannot
  currently tell you what that costs: tier 2 has never run against a store
  that can miss a neighbour, so its passing is evidence about the tests rather
  than about recall. That is why strengthening it is listed below as a
  prerequisite and not as a follow-up.

The operational view of the same trade — what to watch, and what the schema
guarantees regardless — is in
[How to use the pgvector store](../how-to/use-the-pgvector-store.md); the task
form, with the exits and their costs, is BACKLOG B10k.

## The three exits, in increasing order of cost

This decision is not a claim that ANN search is unusable here forever. It is a
claim that an ANN index *on this table, as it stands* leaves the planner a
choice between outcome A and outcome B, and that neither the caller nor the
test suite can see which one it took. So an exit is not "add the index and
watch"; every exit below is a way of **removing the planner's choice**, so that
the index either cannot see another tenant's rows or cannot stop before `k` of
this tenant's have survived the filter.

They are recorded as a task in BACKLOG B10k, in the same order and with the
same costs — if one of them is taken, the entry and this section are deleted
together, and `src/redstring/vector/adapters/pgvector.py`'s module docstring
is the third place that has to change in the same commit.

Two things apply to all three, and both are easy to skip past on the way to the
interesting engineering.

- **None of them is free of the correctness argument in Context.** Each buys
  sublinear search by making the store *approximate* in some regime, which
  moves `PgVectorStore` out of the compliance suite's exact tier — and the
  exact tier is what currently makes it and the in-memory adapter genuinely
  interchangeable rather than interchangeable within a tolerance. That is a
  real loss, not a formality.
- **The trigger is a measurement nobody has taken.** The cost is bounded by
  rows in the *largest tenant*, at the production embedding dimension — not
  table size and not tenant count. Nothing in this repository has profiled it
  (see *What has and has not been measured*), so "linear sounds slow" is not a
  reason to take any of these.

### 1. `LIST`-partition by `tenant_id`, ANN index per partition

The cheapest of the three in conceptual terms, and the one that attacks the
problem at its root rather than working around it. Declare the table
`PARTITION BY LIST (tenant_id)`, give each tenant a partition, and build an
`hnsw` or `ivfflat` index **on each partition** rather than on the parent.

Why that removes the choice rather than betting on it: outcome A exists only
because the index ranks rows belonging to tenants the query never asked for,
and then the `tenant_id` predicate discards them after the cut. A per-partition
index has no such rows in it. Partition pruning resolves `tenant_id = $1` to a
single partition *before* any index is opened, so the index that gets walked
contains exactly one tenant's vectors and there is nothing for a post-filter
to lose. The `k` nearest in that index are the `k` nearest in that tenant.
Outcome A does not become unlikely; it stops being expressible.

The current schema is already shaped for it, which is the one piece of luck
here. Postgres requires the partition key to be part of every unique
constraint, and `PRIMARY KEY (tenant_id, entity_id)` already leads with
`tenant_id` — so the key survives partitioning unchanged, and with it
`upsert_many`'s `ON CONFLICT (tenant_id, entity_id) DO UPDATE`, which routes
through the parent to the right partition. The `(tenant_id, entity_type)`
index becomes a partitioned index. Nothing about the port's shape or
`_search_sql` changes.

What it costs is not the DDL; it is that **a partition is a schema object and
a tenant is data**. Everything downstream of that follows:

- **Tenant creation becomes DDL.** `ensure_schema` today is idempotent,
  `IF NOT EXISTS` throughout, and runs once per store. Under this exit, a
  first write for an unknown tenant needs `CREATE TABLE ... PARTITION OF ...`
  first — which means the write path either takes a lock and creates
  partitions on demand, or the library grows a provisioning call it does not
  currently have. The port has no concept of "register a tenant"; adding one
  is a public-surface change, gated by `__all__` and its three tests.
- **Tenant count becomes bounded.** Planning time grows with the number of
  partitions, and each partition carries its own indexes and relation
  overhead. A few hundred is comfortable; the 400-tenant shape used in
  `test_a_tenant_scoped_search_seeks_rather_than_scanning_the_table` is
  already at the scale where this wants measuring. Tens of thousands of
  small tenants is the case this exit is worst at — which is exactly the
  case where the linear scan it replaces was cheapest, since the cost is
  bounded by the *largest* tenant's rows.
- **Small tenants pay ANN's costs for none of its benefit.** The index is
  built and maintained per partition regardless of size, and on a tenant
  holding fifty rows the planner will sequential-scan the partition anyway.
  That is outcome B, retail: write cost on every upsert for an index no query
  opens. Per-tenant index build is a knob nobody wants to own.
- **Deleting a tenant, or rebalancing, is a migration.** `DETACH PARTITION`
  is cheap; deciding when to is not, and there is no existing operational
  surface for it.

Two consequences for the tests and the compliance suite, both of which are
work rather than notes.

`test_there_is_no_ann_index_on_the_embedding` must change, and how it changes
is the interesting part. It restricts to indexes whose `indrelid` is this
store's table, and a partitioned table's leaf indexes hang off the *partition*
relations — so the existing assertion would keep passing, green and blind,
while every partition carried an `hnsw` index. That is the exemption-list
hazard from `CLAUDE.md` in its purest form: a check whose scope quietly
stopped covering the thing it was written for. Whoever takes this exit
rewrites that test to walk `pg_inherits` before touching the DDL, not after.

And the store stops being exact. An `hnsw` or `ivfflat` index may omit a true
neighbour by design, so `PgVectorStore` leaves the compliance suite's tier 1
and lands in tier 2 — which today passes trivially, because no adapter in this
tree can miss a neighbour. That is why the prerequisite below is a
prerequisite: the tier has to be able to fail before it is asked to judge an
adapter that can.

Recorded as option (1) in BACKLOG B10k, and this is the option the entry means
by "the index then only ever sees one tenant". Taking it deletes that entry,
this section, and the corresponding paragraph of
`src/redstring/vector/adapters/pgvector.py`'s module docstring in one commit;
the operational consequences belong in
[How to use the pgvector store](../how-to/use-the-pgvector-store.md), which
currently promises "no ANN index build, no schema plugin" as part of its setup
story.

### 2. pgvector 0.8 iterative index scan (`hnsw.iterative_scan`)

`SET hnsw.iterative_scan = relaxed_order` keeps pulling from the index until
`k` rows survive the filter. Costs a session GUC the adapter would have to own,
and recall becomes configuration.

### 3. Per-tenant partial ANN index

Works, and does not scale past a few tenants.

### Prerequisite for any of them: strengthen the compliance suite's recall tier

Do this *first*, before an approximate adapter exists to be judged by it. The
tier today is one test, one corpus, one query, and it passes trivially. A real
recall claim needs many queries, a stated recall@k target, and a failure
message reporting the measured rate rather than the single miss that tripped
it. Writing it after the adapter exists means tuning the test until the adapter
passes, which is not a test.

## Related

- BACKLOG B10k — the same reasoning as a task, with the exits and their costs.
- [ADR 0002 (two store ports)](0002-two-store-ports.md) — why `VectorStore` has
  a recall tier at all, and why merging it with `GraphStore` would have made
  the weaker contract win.
- `src/redstring/vector/adapters/pgvector.py` module docstring — the decision
  at the point of use, alongside the other three choices in that adapter.
- [How to use the pgvector store](../how-to/use-the-pgvector-store.md) — the
  operational view.
- [How to implement a store adapter](../how-to/implement-a-store-adapter.md) —
  what an adapter over a genuinely approximate index owes the compliance suite.
