# Backlog

Deferred work. Every deficiency found and not fixed on the spot lands here,
with enough detail that picking it up does not require rediscovering it.

## How to read this file

**Sections group entries by what a reader would search for**, not by when they
were filed. Ordering within a section is roughly by priority; ordering between
sections is not meaningful.

**The `B` numbers are opaque identifiers, not a taxonomy.** The `B10*` family
in particular is fifteen entries whose only shared property is that they were
found in slices 3-5, and the sub-lettering conveys nothing. Renumbering was
considered in slice 11 and **deliberately rejected**: fifteen of the open ids
are cited by name in about twenty-eight files under `src/` and `tests/`, so
renumbering means editing shipped source for a cosmetic gain that the section
headings already deliver. Treat a number as a stable handle and nothing more.

**Closed entries are deleted, and a closed entry's *lesson* is moved rather
than dropped.** `docs/plans/ring-migration.md` indexes B10b, B10d, B26, B33,
B34, B40, B55 and B56 — what each was and where its reasoning lives now — so
those pointers resolve. Later closures whose lesson was a recurring defect are
in `.claude/rules/recurring-defects.md` under "Local instances", which is
where a future author will actually meet them; a file about open work is not
a place anyone reads for them.

**A "— closed" heading is a deferral, not a closure.** Five entries carried
one, several for two slices, and the effect was a file whose length overstated
what was outstanding. When you close an entry: move any lesson to its home,
repoint the citations (the deletions that prompted this rule left six, one of
which was actively *wrong* — a how-to still telling a reader to build a test
module that exists), and delete the entry in the same commit.

## State of the tree

The default gate collects **1995 tests**, plus **245 `integration` tests** and
**4 `accuracy` tests** — against a real Neo4j (slices 4, 7), real pgvector
(slice 5), a live `qwen3.6-27b-mtp` (slice 6, `KG_LLM_BASE_URL`), and a built
wheel (slice 10). The first two need `docker-compose.test.yml`. Both extra
suites are deselected by default (B10a); a run prints what it deselected and
how to run it. What the accuracy suite can and cannot tell you is B12.

**The gate is green.** Verified under its own conditions
(`uv run python scripts/coverage_ratchet.py`, which is `pytest -q -n auto
--cov`): **1995 passed**, no failures. The hypothesis deadline flake that made
this paragraph say otherwise was fixed at the class level rather than the
instance — `tests/conftest.py` now registers a suite-wide `deadline=None`
profile, and `tests/unit/test_hypothesis_deadline_policy.py` fails if a
`deadline=` reappears in a `settings()` decorator and makes the profile inert
for that test.

Coverage is **94.05%** (`.coverage-baseline`), held exactly by the run above.
Do not read the movement from 93.69% as a quality trend; see B14 for why the
number moves for reasons that are not test quality.

**Note that the two compliance suites must be run in separate pytest
invocations** — see B10m.

As of slice 9 there is no ORM, no session, no SQLAlchemy and no schema this
library expects a caller to have migrated. Persistence is `GraphStore` and
`VectorStore`, the write model is `aggregates/` + `events/`, the read model is
`projections/`, and the types are `domain/`. The import-linter contract is
`exhaustive`, so a new top-level package fails the gate until it is placed on a
layer.

---

## 1. Wrong answers in shipped code

Entries here can produce an incorrect result for a caller. They are first
because nothing else in this file costs a user anything.

### B43. A merge plans against a graph read outside its concurrency window

`consolidation/service.py::merge` reads `get_relationships_for` *before*
loading the aggregate, so the edge set it plans against can be stale by the
time the append happens. Deliberate: the read model is a projection and lags
the log by construction, so no ordering of the two steps makes the graph
authoritative, and doing the read inside the aggregate's window would widen
the window without making it correct. See ADR 0004.

**The staleness has three consequences, not two, and only the third matters.**

**1. A redirection for an edge that has since gone -- harmless.**
`upsert_relationship` recreates it and `delete_relationship` returns `False`
for an absent id. Both are idempotent by the port's contract.

**2. An edge that appeared after the read keeps pointing at the absorbed
entity -- self-healing.** The extraction fold resolves each endpoint through
the alias table before writing, so the next `DocumentExtracted` touching that
document moves it onto the canonical entity. Until then it is a visibility
gap: `get_relationships(canonical)` does not report it.

**3. That same edge can become a permanent parallel edge -- and nothing
repairs it.** If the canonical entity already has an edge with the same
`(source, target, relationship_type)`, resolving the late edge's endpoint
produces a *second* edge making the same claim under a different id.
Deduplication lives only in `plan_redirections`, which by definition never saw
that edge, and the fold writes by id. **Re-extraction creates the duplicate
rather than resolving it** -- consequence 2's repair is what produces this
one.

That is exactly the state `duplicate_preference` and the whole tie-break
argument exist to prevent, and no later merge, undo or replay removes it.

Reproduced and pinned in `tests/unit/consolidation/test_known_gaps.py`, which
asserts the wrong answer on purpose. The shape:

```
doc-1 extracted and projected      canonical -> outsider  "worked_on"
doc-2 extracted, NOT yet projected  absorbed -> outsider  "worked_on"
merge absorbed into canonical       0 redirections planned
doc-2 projected                     the edge is still on `absorbed`
doc-2 re-extracted                  endpoint resolved -> two edges, one claim
```

No race is needed to build it. "An edge exists in the log that the graph read
cannot see" is the ordinary state of a projection, not a timing accident.

**Which fix addresses which consequence -- this is the part that changes what
to measure.**

- **Have the merge fold resolve `EntitiesMerged` redirections against the
  store.** Addresses consequence 2 and **not** consequence 3: it resolves the
  endpoint and leaves the duplicate, which is precisely what re-extraction
  already does. Cheap, and it moves a decision into the fold where it cannot
  be audited. **On its own it is not enough.**
- **Re-read and re-plan on an `ExpectedVersion` conflict.** The only one that
  addresses consequence 3, because it puts the late edge in front of
  `plan_redirections`, which is the only code that deduplicates. It needs the
  repository to surface the conflict in a form the service can act on, which
  it does not today.

So the deferral stands, but what to measure is narrower than "how often is
the read stale". Measure **how often a late edge duplicates one the canonical
already has**, since that is the only case the cheap fix cannot reach.

Two smaller facts about the same window, recorded here so they are not
rediscovered:

- **An undo overwrites concurrent edits.** `MergeUndone` upserts every `before`
  relationship, so an edge legitimately modified between the merge and the
  undo is restored to its pre-merge value. That is probably correct for a
  compensating event -- an undo's job is to reproduce the pre-merge graph, and
  the round-trip test asserts exactly that -- but it is a real choice and is
  now stated on `ConsolidationService.undo`.
- **`plan_redirections` is order-independent, not instant-independent.** Its
  docstring's claim that the plan is a function of the graph is true of the
  graph *at the moment of the read*; this entry is the weaker half, and
  `planning.py` now points here.

### B32. Re-extraction cannot remove an entity a previous run found

`events/document.py` -- `DocumentExtracted` carries the whole result of one
extraction run, and the projection folds it with `upsert_entities`. So a
second run over the same document under a newer model, finding *fewer*
entities than the first, leaves the dropped ones in the graph forever. The
graph converges on the union of every run, not on the latest one.

This was accepted rather than missed. The alternatives all cost more than the
problem is currently worth:

- Emitting a `DocumentEntitiesRetracted` alongside means extraction must diff
  against the previous run, which means reading the projection from the write
  path -- exactly the coupling event sourcing is being adopted to remove.
- Making the projection delete-then-insert per document would need
  `GraphStore.delete_entity`, which does not exist and will not (ADR 0002),
  and would make the fold non-idempotent under at-least-once delivery: the
  delete half of a redelivered event would remove entities a later event had
  added.
- Having the aggregate compute the retraction from its own replayed state is
  the right answer and is cheap -- the `Document` aggregate already replays
  every `DocumentExtracted` -- but it needs a decision about what "the same
  entity across two extraction runs" means. Id equality is not it; ids are
  derived per run by `uuid5` over the extracted name, so a renamed or
  re-described entity is a different id.

**Slice 11 note:** the previous version of this entry said "take it up in
slice 6, when extraction actually emits." Slice 6 happened and did not take it
up. It is open on its own merits now, not scheduled.

### B35. `GraphProjection.reset()` raises instead of truncating

`projections/graph.py` and `projections/vector.py` --
`_truncate_read_models` raises `NotImplementedError`. Neither port has a
cross-tenant delete, deliberately: "there is no cross-tenant read, ever", and
the same argument forbids a cross-tenant delete. So `reset()`, which the
library's `CheckpointTrackingProjection` offers, cannot be honoured.

Raising was chosen over a silent no-op because a rebuild over a store that
still held the old rows looks successful while carrying stale entities that
nothing will ever remove -- a worse failure than a loud one. Callers wipe with
`delete_by_tenant(tenant_id)` per tenant, which is what the replay tests do.

The real fix is a `rebuild(tenant_id)` entry point on the projection that
wipes and replays one tenant, which needs a tenant-filtered feed read
(`FeedReadOptions.tenant_id` already exists) and belongs with whatever slice
first needs to rebuild a single tenant in anger.

---

## 2. Things that are unverified

Code that may well be correct, with nothing that would tell us if it were not.
These are the entries most likely to become section 1 without warning.

### B12. The accuracy suite — built, and what it still cannot tell you

**Closed by building it rather than by deleting the marker**, which was the
choice the entry framed. `tests/accuracy/` now holds a scorer, a graded corpus
and a live-model test module, and `-m accuracy` collects four tests where it
collected zero.

The design decision worth keeping is the split. "Measure extraction accuracy"
reads as one job needing a model, a corpus and a metric at once, and that
reading is why the entry stayed open for eleven slices. It is two jobs:
deciding whether a predicted entity *is* an expected one, which needs nothing
and is where a wrong answer is silent, and getting predictions, which needs
everything. The scorer and corpus are pure and run in the commit gate through
`tests/unit/accuracy/`; only `test_extraction_accuracy.py` needs an endpoint.

That split is also what makes a live number believable. An accuracy suite fails
silently in two directions and both look like results — measuring nothing gives
F1 = 0.0 and reads as a bad model, comparing the corpus against itself gives
1.0 and reads as a good one. `test_harness.py` pins an exactly-right answer, an
empty answer and a *wrong* answer against `FakeLlmProvider`. The third is the
load-bearing one: a self-comparison cannot produce a false positive whatever
the model says.

**What it still cannot do**, which is the part to carry forward:

- **Five documents is not a benchmark.** It catches a regression; it cannot
  rank two models, and an F1 quoted from it is not a figure anyone should
  publish. Growing the corpus is the obvious next step and needs a second
  grader more than it needs more documents — the grading rule that a second
  person gets wrong is stated at the top of `corpus.yaml`.
- **The floors are regression floors.** Set where a real fall trips them, not
  where the current endpoint sits. Raising them to track a good model turns
  the suite into a test of that model.
- **One negative document carries the whole hallucination check.**
  `empty-negative` grades nothing, so recall is vacuous and precision is the
  only movable metric. Every other document rewards finding things. If that
  document ever acquires a graded entity, the only test of its kind is retired
  silently — `test_the_negative_document_grades_nothing` exists to stop that.
- **It does not settle ADR 0011.** It can show that off-schema extraction got
  worse; it cannot show that constraining to the schema would be better.

### B53. `Entity.temporal` round-trips through Neo4j but no shared test says so

**Rewritten in slice 11; the previous version of this entry was false.** It
claimed the Neo4j adapter "does not store or index `Entity.temporal`" and that
every temporal query would answer `[]` in production. Checked against the live
Neo4j container in slice 11:

```
ROUND TRIPS EXACTLY: True
temporal query hits: ['Ada']
```

The adapter encodes `temporal` as `temporal_json` alongside `properties` and
`external_ids`, and `TemporalQuery.entities_in_interval` returns the entity —
including the precision-widening case, a YEAR-precision `2023` matching a
June-July 2023 interval, which exercises both the JSON round-trip and
`domain/interval.py`.

**What is actually missing is the test.** `tests/compliance/graph_store.py`
contains no assertion about `temporal` at all, so the storage above is correct
by accident of implementation rather than by contract, and a future adapter can
drop the field and pass the suite. Close it in the shared suite — a round-trip
assertion there covers every adapter at once, which is the point of having one.

**The indexing half of the original entry stands.** `temporal` is a JSON blob,
so it cannot serve an indexed range prefilter, and that interacts with B48:
flattening `TemporalExtent` into node properties would make B48's prefilter
possible, while the JSON blob makes it impossible. Do not change the storage
shape without settling B48 at the same time.

*The lesson is worth more than the entry: this claim survived two slices
because it was plausible and nobody ran it. It took ninety seconds to check.*

### B10a. The Cypher-executing half of the Neo4j adapter is not in the gate

**How this was found, because it is the important part.** A cosmic-ray run was
interrupted and left a mutant in `graph/adapters/neo4j.py`:

```
-    if limit is not None and limit < 0:
+    if not limit is not None and limit < 0:
```

The full suite passed with it applied — gate clean. The adapter's tests are all
`integration`-marked and deselected by `addopts`, so **not one line of that
module executed in the default run.** Corrupt source in an integration-only
module was invisible.

Two things were done about it, and one was not.

Done: `tests/unit/graph/test_neo4j_adapter_is_wired.py` now runs every part of
the adapter that needs no server — argument validation (against a driver that
raises if touched, so it also proves no I/O happens before the guard), the
pure encode/decode functions, signature conformance against the port, and a
check that Cypher has not leaked out of the adapter. That mutant is now killed
by the default gate. The module is **not** in `[tool.coverage.run] omit`, so
the ratchet measures the remainder honestly rather than hiding it: the adapter
reads **61%** in the default run (re-measured in slice 11), and the 65
uncovered statements are precisely the query bodies. Slice 4 accepted a small
baseline reduction for that, deliberately.

Also done: `tests/conftest.py` prints what a run deselected and how to run it,
so `pytest` ends with `197 'integration' tests -- uv run pytest -m integration`
instead of a bare deselection count.

**Not done, and this is the entry:** the queries themselves, the schema DDL,
tenant isolation, traversal and the query-plan assertions still only run with
Docker up. What is needed is a second coverage run over `-m integration`
combined with the default run's data (`coverage combine`; `parallel = true` is
already set, so the files already accumulate). Deferred because making the
commit hook conditional on Docker turns a deterministic gate into a flaky one
— the right shape is a separate CI target that starts the compose file, runs
both suites, and combines, not a change to the hook.

**Two traps are waiting for whoever writes that target**, and both make it fail
in ways that look like flakiness rather than like the bug they are: it must be
**two invocations combined**, not one widened marker expression (B10m), and it
must not use `-n auto` over the Neo4j suite (B10f).

### B10e. The Neo4j adapter's mutation coverage is unestablished

A cosmic-ray run over `src/redstring/graph/adapters/neo4j.py` completed **16
of 289 mutants (5.5%)** before being interrupted: 11 killed, 5 survived, and
all 5 survivors were `ReplaceBinaryOperator_BitOr_*` — the `|` in `X | None`
annotations, unkillable under `from __future__ import annotations` and exactly
the equivalent class CLAUDE.md describes. So nothing of concern was found, and
also nothing much was looked at. **Do not read the adapter as mutation-tested.**

Two things to fix before re-running, both learned the hard way:

1. **cosmic-ray mutates tracked source in place and a killed process leaves
   the mutant behind.** One escaped into the working tree here. Run it from a
   `git worktree` or a copy, or wrap it so the file is restored on exit —
   `git diff --quiet` afterwards is the minimum check.
2. **Each mutant runs the whole integration suite against a live Neo4j**, about
   16 s, so a full run is 1.5–2 hours and needs the container up throughout.
   `KG_COMPLIANCE_MAX_EXAMPLES` is already the lever; a narrower per-mutant
   command (the compliance suite only, not the adapter specifics) would cut it
   further without losing killing power.

The session config used is worth recreating rather than rediscovering:
`module-path` pointed at the single file, and `test-command` was

```
env KG_COMPLIANCE_MAX_EXAMPLES=5 ./.venv/bin/pytest -x -q --no-header -p no:randomly -m integration tests/integration
```

The `-m integration` is required — `addopts` deselects it otherwise, and the
run then silently mutates code no test executes, which is how B10a happened.

### B54. 793 of `temporal_parsing.py`'s 850 mutants were never run

Slice 8 ran cosmic-ray over `domain/interval.py` (217, all classified),
`temporal/inference.py` (95, all classified) and **the precision logic only**
of `domain/temporal_parsing.py` (57 of 850). The remaining 793 cover the
uncertainty patterns, the marker stripping, the range and period regexes, the
ambiguity probe and `render_temporal`.

They were not run because each mutant costs ~70 seconds: it re-runs the whole
file, which includes two hypothesis properties at 300 examples and a
`dateparser` import. 850 × 70s is about seventeen hours. The 57 that were run
found a real defect -- the quarter arithmetic, closed in `44e213d` -- so the
remainder is likely to be worth the time rather than not.

**How to make it affordable** rather than just waiting: give the session a
narrower `test-command`. The round-trip properties are the expensive part and
they exercise `render_temporal` and the partial-date strategies; a session
aimed at the uncertainty patterns can run against the marker tests alone in a
second or two per mutant. Split by target, not by patience.

The mechanism for scoping is worth keeping: cosmic-ray has no line filter, so
`init` the full session and then `DELETE FROM mutation_specs` / `work_items`
for the rows whose `start_pos_row` is outside the range of interest.

### B10i. The `EXPLAIN` tests run against an empty database and do not pin the negative

`tests/integration/graph/test_neo4j_store.py::test_tenant_scoped_reads_seek_rather_than_scan_the_label`.
The claim it encodes was measured at 5000 entities across 100 tenants; the
`store` fixture wipes first, so the planner sees zero nodes.

**Recorded as a strengthening opportunity, not a defect** — deliberately not
fixed in the slice-4 fix round, for two reasons that still hold. The
discrimination is *structural* rather than statistical: a Neo4j composite
index needs a predicate on every one of its properties, so `tenant_id` alone
cannot use `(tenant_id, id)`, and that does not depend on cardinality. And the
test is fail-safe: it asserts operator names directly
(`NodeUniqueIndexSeek`/`NodeIndexSeek` present, `NodeByLabelScan` absent)
rather than comparing two runs, so a regressed planner fails rather than
passing vacuously.

What it does not do is show the seek survives at a scale where the cost
matters, and **nothing asserts the plan *is* a label scan without
`_TENANT_SEEK`**. If a later change makes the predicate redundant, the test
keeps passing while the reason for it is forgotten. The strengthening is to
run the same `EXPLAIN` against the query with `_TENANT_SEEK` removed and
assert `NodeByLabelScan` *is* present — that is the assertion that makes the
pair discriminating, and it is the only one that would notice the predicate
becoming unnecessary.

Note the measurement prerequisite from ADR 0003: `CALL db.awaitIndexes()`
before any plan assertion, or you measure the index-population race instead.

### B10o. `min_score` evaluates the distance operator twice per row, and no plan test covers it

`vector/adapters/pgvector.py::_search_sql` inlines `_SCORE` in both the
`SELECT` list and the `min_score` predicate, because SQL cannot reference a
select alias from `WHERE`:

```
SELECT entity_id, metadata, 1 - (embedding <=> $2::vector) / 2 AS score
...
  AND ($5::float8 IS NULL OR 1 - (embedding <=> $2::vector) / 2 >= $5)
```

So a query with `min_score` set computes `<=>` twice for every row in the
tenant. Postgres does not common-subexpression-eliminate this.

**Not fixed, and the reason is scale, not difficulty.** A `LATERAL (SELECT …)`
or a subselect with the predicate on the outer query evaluates it once, and is
about four lines. At the corpus sizes anything here has been measured against
it is invisible, and the rewrite makes the statement materially harder to read
against a port rule (`WHERE` before `LIMIT`) that a reader currently checks by
looking at it. Doing it on suspicion, before a profile says the distance
operator is hot, trades clarity for nothing.

**The part that is worth doing sooner is the test, not the fix.** Both plan
assertions in `tests/integration/vector/test_pgvector_store.py` run with
`min_score=None`, so the second `<=>` never appears in any plan that is
asserted on. A later change to that branch — including a well-meant rewrite
of exactly the kind above — would not be caught by anything. Add an `EXPLAIN`
case with `min_score` set before touching it, and make the assertion count
occurrences of the operator so a rewrite that claims to evaluate it once has
to prove it.

### B10n. `cosine_score`'s upper clamp is not reachable from float64 input

`domain/vector.py::cosine_score` ends with `min(1.0, max(0.0, ...))`. A
cosmic-ray mutant changing `min(1.0, …)` to `min(2.0, …)` **survived**, and the
survivor is understood rather than equivalent: the clamp is genuinely
unenforced by any test.

Searched, not assumed: over roughly 2 × 10^6 random vectors (dimensions 2–768,
magnitudes to 10^6) the unclamped value never exceeded 1.0. The reason is that
the overshoot is about one ulp of the *ratio* `dot / magnitude`, and the
`(1 + ratio) / 2` that follows halves it into the ulp below 1.0, where it
rounds away. Slice 0's `cosine_similarity` did exceed its bound because it
returned the raw ratio with no such halving.

**`PgVectorStore.search`'s clamp is in the same position, and was measured
too.** Over 4000 random 8-dimension vectors, each queried against itself and
against its negation, `1 - (embedding <=> $1) / 2` returned exactly `0.0` and
exactly `1.0` at the extremes and never stepped outside — pgvector clamps
`<=>` internally. So both of its mutants survive for the same reason, and the
clamp is dead code against **pgvector 0.8.5 specifically**.

Both clamps are kept, and this is the argument to preserve. The guarantee is
needed at precisions and backends this repo does not yet have: any store that
reports a raw cosine, or computes the mapping itself without clamping, hands
`VectorMatch` a value its `le=1` bound rejects outright — turning a one-ulp
rounding artefact into a hard `ValidationError` for the caller. A Qdrant
adapter is the next candidate.

Resolving this means either constructing an input that reaches a clamp — which
may not exist in float64 or in pgvector, in which case the honest answer is a
comment recording the measurement — or moving the clamp into a single shared
helper both call, so one test covers both. Do **not** resolve it by deleting a
clamp.

---

## 3. Performance and scale

Nothing here is slow at any size this project has measured. Each entry records
the shape of the cost and what would have to be true before paying to fix it.

### B48. Temporal query is a full tenant scan

`temporal/query.py::TemporalQuery.entities_in_interval` pages
`GraphStore.find_entities` over the whole tenant and applies the interval
predicate in Python. It composes rather than adding a port method,
deliberately — see ADR 0005 and that module's docstring — but the cost is
linear in the tenant's entity count regardless of how few entities are dated.

**What to do when that stops being acceptable, and why it is not "add a
`temporal_overlaps` filter to `find_entities`".** The predicate is not a range
test on two columns: precision widens a bound (`2023` at `YEAR` precision
denotes all of 2023 even though `end_date` is `None`), and
`UncertaintyMarker.BEFORE`/`AFTER` make a bound infinite. Reimplementing that in
Cypher *and* in the memory adapter *and* in any future SQL adapter gives three
copies of a rule that lives in `domain/interval.py`, and they will diverge
silently — a wrong answer here looks exactly like a correct one.

The shape that works: add a port method returning a deliberate **superset** —
a cheap, index-assisted bound on the raw `start_date`/`end_date` columns, widened
by the largest precision unit (one year) so it cannot exclude a true match — and
keep `interval.relate` as the exact filter over what comes back. Then the
adapters implement only a range scan, which they cannot get subtly wrong, and
the semantics stay in one place. That method needs the compliance gate's
mutation-isolation and tenant-isolation tests, and an `EXPLAIN` assertion in the
Neo4j adapter after `CALL db.awaitIndexes()` (see B10i).

**This is blocked on a storage decision, not just on effort.** `temporal` is
stored as a JSON blob today (B53), and a JSON blob cannot serve the indexed
range bound above. Flattening `TemporalExtent` into node properties is the
enabling change, and it is the reason slice 8 did not add the field's storage
shape in passing.

### B10k. The pgvector adapter has no ANN index, so search is linear in a tenant

`vector/adapters/pgvector.py::_schema_statements`. There is deliberately no
`hnsw` or `ivfflat` index on `embedding`, and
`tests/integration/vector/test_pgvector_store.py::test_there_is_no_ann_index_on_the_embedding`
asserts its absence so adding one is a decision rather than a drive-by
optimisation.

**Why it was left out, which is the expensive part to rediscover.** An ANN
index and a tenant filter interact badly, and both outcomes look correct:

- the planner uses the vector index, takes the `k` globally nearest rows and
  drops other tenants' afterwards. A tenant holding 1% of the table gets a
  handful of results, or none, for a query with thousands of genuine
  neighbours. This violates the port's "filters are applied before `k`" rule
  and **no test that only inspects results can see it** — it is
  indistinguishable from a tenant with little data.
- the planner filters by tenant first, does not touch the vector index at all,
  and the index costs write throughput to be never read.

Scanning within the tenant is exact, which is also why the pgvector adapter
passes the compliance suite's *exact* tier and not merely its recall tier.

**What it costs:** search is `O(rows in this tenant)`. Measured shape, not
measured cost — nothing here has profiled it. At the 50 rows/tenant used in
the plan test it is irrelevant; at 10^6 rows in one tenant it will not be.

**The three ways out, in increasing order of cost.** (1) `LIST`-partition the
table by `tenant_id` and build an ANN index per partition: the index then only
ever sees one tenant, so post-filtering cannot lose rows. Costs partition
management and a bound on tenant count. (2) pgvector 0.8's iterative index
scan (`SET hnsw.iterative_scan = relaxed_order`) — the container already runs
0.8.5 — which keeps pulling from the index until `k` rows survive the filter.
Costs a session GUC the adapter would have to own, and recall becomes
configuration. (3) A partial ANN index per large tenant, which does not scale
past a few tenants. Whoever takes this on should extend the compliance suite's
recall tier first: it currently passes trivially because both adapters are
exact.

### B50. `dateparser` costs ~250ms on a first call and is on the extraction path

**Its import cost is already handled**: `import dateparser` is inside
`_parse_natural` rather than at module scope, because at module scope every
importer of `extraction/mapping.py` paid ~250ms whether or not any document
contained a date. That surfaced as two hypothesis properties in unrelated test
files exceeding their 200ms deadline. What follows is about the *call* cost,
which remains.

`_parse_natural` is the last strategy `parse_temporal` tries, so it is reached
only by text the regex strategies and `dateutil` all declined -- but that
includes every entity whose temporal expression is unparseable, which in a real
document is most of them. Measured at ~270ms for the first call in a process
(language-detection tables) and materially less after, but not free.

It has not been optimised because there is no measurement of how often
extraction reaches it in practice, and the obvious fix (restrict
`dateparser`'s `languages` to `["en"]`, or gate it behind a cheap "does this
look like a date at all" pattern) trades recall for latency without knowing the
exchange rate. Measure on a real corpus first.

**It has now trapped a second file, which is the part to note.** Slice 9's
first deletion commit failed the gate on
`tests/unit/extraction/test_merging.py::TestProperties::test_merging_the_same_chunk_twice_equals_merging_it_once`
at **299ms** against the 200ms default, in a commit that changed nothing in
that file. Every property in that class builds its input through
`map_extraction`, so all seven are exposed and *which one* fails is decided by
which property an xdist worker happens to draw first -- meaning the failure
moves between runs and between files.

**Nine properties now set `deadline=None` because of this** — seven in
`tests/unit/extraction/test_merging.py` and two in
`tests/unit/domain/test_temporal_parsing.py` (verified in slice 11). So the
blast radius is "any hypothesis property whose input passes through
`map_extraction`", not two named tests, and a new one inherits the trap
silently. That is the argument for actually measuring and fixing the call
cost rather than continuing to drop deadlines: the third occurrence will be in
a file whose author has no idea `dateparser` is involved.

### B10c. `neighbors` at a large `depth` is unbounded work

`graph/adapters/neo4j.py` — traversal is one `-[rels:RELATES_TO*1..N]-`
pattern. Cypher's relationship-uniqueness rule terminates cycles, so the
*result* is always correct and finite, but the number of paths explored can
grow exponentially with `N` in a dense graph even though the number of distinct
neighbours cannot. The compliance suite only reaches `depth=99` on a three-node
graph, so nothing here is slow today.

The fix is not a smaller depth limit — it is to stop enumerating paths, e.g.
expanding level by level with a visited set server-side. That was not done
because the port asks for one round trip and the plain-Cypher forms that
avoid path enumeration either need apoc (`apoc.path.subgraphNodes`) or a
`CALL {}` loop that is harder to read than the win justifies at current
scale. Revisit if temporal traversal raises typical depths above about 3.

---

## 4. The test suite itself

Traps in the harness. Several of these are filed specifically because they
cause failures that read as infrastructure trouble and get retried rather than
investigated.

### B10m. Two adapters of one compliance suite cannot run in the same pytest invocation

Measured, on both suites:

```
pytest -m "not accuracy" tests/unit/graph tests/integration/graph   -> 21 failed
pytest -m "not accuracy" tests/unit/vector tests/integration/vector -> 13 failed
```

Every failure is `hypothesis.errors.FailedHealthCheck: The method
GraphStoreCompliance.test_… was called from multiple different executors`.
Hypothesis attaches its per-test state to the **function object**, and the
`@given` methods live on the shared base class, so `TestMemoryStore` and
`TestNeo4jStore` — or `TestMemoryVectorStore` and `TestPgVectorStore` — are two
executors of one function. Running either suite alone is fine, which is why
this has never been seen: `addopts` deselects `integration`, so the default
gate runs only the in-memory subclass and an explicit `-m integration` runs
only the real one.

**This is a direct trap for B10a**, which is the reason it is filed rather
than merely noted. The combined coverage run B10a asks for is naturally
written as one invocation over both suites, and it will fail 34 times with an
error that names hypothesis and looks like flakiness. B10a should be
implemented as **two invocations combined by `coverage combine`** — which
`parallel = true` already supports — not as one invocation with a widened
marker expression.

The other fixes, if a single invocation is ever wanted: add
`suppress_health_check=[HealthCheck.differing_executors]` to both suites'
shared `settings()` (cheap, and suppresses a check that exists to catch a real
class of bug elsewhere), or stop sharing the function object by generating the
property tests per subclass in `__init_subclass__` (correct, and a
considerable amount of machinery for a problem only the CI target has).

### B10f. The integration suite cannot run under xdist

`tests/integration/graph/test_neo4j_store.py::_wipe` runs
`MATCH (n) DETACH DELETE n` on the one shared Neo4j database before every
test. Under `pytest-xdist` each worker does that to the others' data
mid-test, which produced **36 failures that say nothing about the code**.
Measured, not predicted.

**The constraint today: run `-m integration` serially. No `-n auto`.** The
default gate is unaffected — `addopts` deselects `integration`, so the xdist
run the hook does never reaches these tests.

**This is a direct trap for B10a**, whose obvious implementation is
`pytest -n auto` over both suites. That target will fail 36 times for a reason
that looks like flakiness.

Slice 5's pgvector suite would have hit the identical problem the moment it
truncated a shared table between tests, which is the natural way to write it.
It does not: `tests/integration/vector/test_pgvector_store.py` puts
`PYTEST_XDIST_WORKER` into the table name, so each worker truncates only its
own rows and the module is parallel-safe. That is the third fix below, one
level cheaper — a table per worker rather than a database per worker — and it
is available in Postgres precisely because it allows as many tables as you
like, which Neo4j community does not do for databases.

The real fixes, in increasing order of cost: give each test its own tenant and
scope the wipe to it (weakens `test_delete_by_tenant_removes_exactly_that_tenant`,
which is why slice 4 did not); give each xdist worker its own database
(`PYTEST_XDIST_WORKER` into the database name — Neo4j community allows one
database, so this needs Enterprise or a container per worker); or mark the
module `xdist_group` so one worker owns it, which keeps the other suites
parallel and is probably the right answer.

### B10h. `KG_COMPLIANCE_MAX_EXAMPLES` cannot be tuned per adapter subclass

`tests/compliance/graph_store.py` reads `DEFAULT_MAX_EXAMPLES` from the
environment at **module import** and bakes it into the shared
`compliance_settings`. By the time a subclass body executes, the value is fixed
— so "tune `max_examples` down for the slow adapter" is not achievable as
written. It is tunable per *run*
(`KG_COMPLIANCE_MAX_EXAMPLES=10 uv run pytest ...`) and not per class.

An explicit `settings(max_examples=...)` on the subclass is deliberately ruled
out by the suite's own comment: it outranks `--hypothesis-profile`, which would
make the profile inert for **every** adapter, not just that one. That reasoning
is right and should not be reversed casually.

Slice 4 measured the cost and it was nothing — 25 s / 43 s / 66 s at 10 / 25 /
50 examples — so this was correctly not fixed then, and slice 5's pgvector
adapter did not make it worse.

What would have to change: make the per-class value a hypothesis *profile*
rather than a `settings()` argument — register one profile per adapter at
import and have each subclass select it — or give the suite a class-level hook
(e.g. a `max_examples` class attribute the shared decorator reads through a
`settings` callable) that still leaves `--hypothesis-profile` outranking it.
Either is a change to slice 3's suite, which is why slice 4 did not make it
unilaterally.

### B10j. `_schema_ready` is module-level mutable test state

`tests/integration/graph/test_neo4j_store.py`. **Harmless today and
deliberately left alone**: `ensure_schema` is idempotent, `_wipe` does not
drop indexes, so a stale `True` cannot cause a missing index within a process,
and each xdist worker would have its own copy anyway (see B10f — that suite
does not run under xdist regardless).

It is recorded only because it is the same shape that produced B10d:
module-level mutable state in a test file, correct until collection order or
process reuse changes underneath it. If B10f is resolved by giving each worker
its own database, revisit this at the same time — that is the change that
would make the cached flag mean something different per worker.

### B37. Hand-applied mutants can be masked by a stale `__pycache__`

Found the hard way in slice 5b. Verifying a mutant by editing a source file,
running pytest, and reverting works **except** when the edit leaves the file
the same size and lands within the same mtime second -- `==` for `is`, `1.0`
for `2.0`, `> 1` for `> 2`. CPython validates a `.pyc` on `(mtime, size)`, so
the interpreter keeps running the *previous* bytecode and the result is a lie
in whichever direction is least helpful: a mutant that appears to survive was
never loaded at all.

It cost about an hour, presenting as `Entity` accepting `confidence=1.5`
against a validator whose source plainly rejected it. `dis.dis` on the loaded
function is what settles it -- the constant in the bytecode was `2.0` while
the file said `1.0`.

Two mitigations, neither yet adopted as a rule:

- `PYTHONDONTWRITEBYTECODE=1` for any hand-mutation session. Cheap, and the
  slice 5b verifications were all re-run under it.
- Consider setting it in the `pytest` pre-commit hook's environment. That
  would also close the same hazard for anyone bisecting or reverting by hand.
  Not done here because it slows every run slightly and the trade needs
  measuring rather than guessing.

cosmic-ray itself is not obviously affected -- it runs each mutant in a fresh
subprocess and its survivors in slice 5b matched hand verification once the
cache was cleared -- but nothing proves it is immune either, and a run that
disagrees with a hand check should suspect this first.

**Deleting a package leaves its `__pycache__` behind, and `git status` stays
clean.** `git rm -r` removes the tracked `.py` files; the ignored `.pyc` tree
survives as a source-less directory under a package path. Slices 6, 7 and 9
each produced a crop — nine in total, slice 9's eight being the largest — all
deleted in the commit that found them.

Harmless in itself -- Python 3 will not import from a `__pycache__` without
its source -- but it is this entry's trap in miniature: a directory that looks
like a package, holds bytecode for modules that no longer exist, and is
invisible to every check the gate runs.

Delete them in the same commit, and note that doing so produces **no diff**,
so the commit needs another reason to exist. To find them:

```sh
find src tests -type d -name __pycache__ -printf '%h\n' | sort -u |
  while read -r dir; do
    [ -z "$(find "$dir" -maxdepth 1 -name '*.py' -print -quit)" ] && echo "$dir"
  done
```

### B14. The coverage number moves for reasons that are not test quality

Coverage is **93.69%**, up from the 60.79% this entry was filed at. Almost
none of that rise came from writing tests. It came from slices 6-10 deleting
tens of thousands of lines of legacy source, and the ratchet raising the
baseline behind each deletion. Slice 10 alone added 4.16 points by deleting
`encryption.py` (B58) — 127 statements at 0% coverage.

**That is worth stating because it cuts both ways, and slice 9 saw both.**
Deleting `models/` and `schemas/` *lowered* the ratio even though nothing
usefully tested was removed: declarative ORM columns execute at import, so
those packages scored very high while proving almost nothing. Deleting
`services/` and `config.py`'s dead keys *raised* it, because that code was
barely covered.

So the number is a ratio whose denominator has been changing faster than its
numerator, and neither direction has meant much about the tests. The ratchet
is still worth having -- it stops a regression inside a stable tree -- but it
should not be read as a quality trend, and a movement of either sign during a
deletion slice needs the argument in the commit message rather than a reflex
to add tests or lower the bar.

**A 93.69% baseline is now high enough to be its own hazard.** It is close
enough to 100 that a genuinely useful deletion can be blocked by a ratchet
that has nowhere left to go, and small enough movements are now within the
noise of which branches an xdist run happens to take. Expect to argue a drop
in the commit message rather than to satisfy the number.

---

## 5. Capabilities deliberately not built, with the route back

Nothing here is a defect. Each is a decision to *not* have something, recorded
with what it would cost to change the answer — because the expensive part of
each is the argument, not the code.

### B58. If this library ever encrypts, it needs a port -- not the file that was deleted

Slice 10 deleted `encryption.py` (467 lines, 127 statements, 0% coverage, no
importer in `src/` or `tests/`) and dropped `cryptography` from the dependency
table with it. **Recoverable from `e063faa` (verified in slice 11).** Three
things decided it, and only the first is the obvious one:

1. **No caller, and no seam for one.** `GraphStore` and `VectorStore` are ports
   over *domain objects*; encryption is a property of *storage*. To have a
   place it would have to live inside each adapter -- Neo4j, pgvector and two
   in-memory stores, where it is pointless -- or be a decorator over a port,
   which nobody has argued for. `encrypt_dict_field`/`decrypt_dict_field` say
   what it was actually built for: an ORM column, in the layer slice 9 deleted.
2. **It is functionally incompatible with slice 7.** An encrypted
   `normalized_name` cannot be indexed or blocked on, so `find_by_blocking_key`
   returns nothing and consolidation stops working. That is not a preference
   about layering; encrypting the fields that matter breaks a capability this
   library already has.
3. **It predates the architecture and was never fitted to it.** Its exception
   hierarchy rooted in `EncryptionError(Exception)` rather than
   `RedstringError`, which every module written or ported in slices 2-10 does.

**What the work would be, if the answer turns out to be yes.** A port declaring
what is encrypted and when, an adapter per store implementing it, and a
compliance suite -- the same three pieces `GraphStore` has. The HKDF derivation
and Fernet wrapping at the ref are the easy forty lines of that; the expensive
parts are key rotation, the migration story for data already written, and
deciding which fields can be encrypted without losing the searchability
consolidation depends on. Note also that encryption at rest is normally the
deployment's job: Neo4j and Postgres both do it.

Keeping 467 untested lines and a core `cryptography` dependency in order to
defer the question was the worst of the three branches.

### B47. Three timeline modules were deleted, not ported

Slice 8 deleted ~1700 lines rather than porting them. All three are recoverable
from `d49f56b`, which is the last commit that had them — **all five paths
verified resolvable in slice 11**:

| Module | Ref |
|---|---|
| `project_timeline_query.py` (677 lines) | `d49f56b:src/redstring/services/project_timeline_query.py` |
| `timeline_export.py` (630 lines) | `d49f56b:src/redstring/services/timeline_export.py` |
| `timeline_cache.py` (428 lines) | `d49f56b:src/redstring/services/timeline_cache.py` |
| `test_timeline_export.py` (566 lines) | `d49f56b:tests/unit/services/test_timeline_export.py` |
| `test_timeline_cache.py` (512 lines) | `d49f56b:tests/unit/services/test_timeline_cache.py` |

**What each did that nothing else now does, so it is not rediscovered:**

- `project_timeline_query.py` aggregated timelines "across all scraping jobs
  within a project" and ranked entities by cross-job mention count. Both of its
  organising concepts — the scraping job and the project — were deleted in
  slice 1. It also had a `merge_overlapping_events` pass that collapsed
  near-identical events from different jobs into one; that is *consolidation*
  under another name, and doing it at query time means it is invisible and
  unauditable, which is the failure `EntitiesMerged` exists to prevent. Nothing
  was lost that slice 7's `ConsolidationLog` does not do better.
- `timeline_export.py` owned CSV, JSON and iCalendar renderers, plus column
  ordering, RFC 4180 quoting and an iCalendar `VEVENT` writer with escaping for
  `,;\` and CRLF folding at 75 octets. **This is the only genuine loss.** A
  caller who wants an `.ics` file must now write that escaping themselves, and
  it is fiddly and easy to get subtly wrong. It is still not the library's job:
  the library's job is to answer "what happened when", and it now does so with
  domain objects a caller can render however they like. If an export helper is
  ever wanted back, take the iCalendar escaping from the ref above rather than
  rewriting it.
- `timeline_cache.py` was a Redis-backed memoiser for query results, with key
  construction from the filter set and explicit invalidation on write. It
  predates the `Cache` port entirely and talked to `get_redis_client` directly.
  Caching a query result is the caller's policy — it depends on the caller's
  read/write ratio and staleness tolerance, neither of which this library can
  know. Note that its 512-line test file was ~90% `unittest.mock` against a
  fake Redis, which is the shape `recurring-defects.md` (g) records as
  worthless.

### B49. The temporal parser dropped confidence, parse method and named eras

`parse_temporal` returns a `TemporalExtent` alone. The deleted
`TemporalParserService.parse` returned a `TemporalParseResult` carrying three
things that have no home on `TemporalExtent`, all dropped deliberately:

- **`confidence`** (0.0-1.0, from which of four strategies matched, penalised by
  uncertainty and coarseness) and **`parse_method`**. Both were copied into a
  `TemporalEnrichmentResult` field and never read again outside a log line and
  that dataclass's own tests -- nothing made a decision with either. The
  semantic part of the same information survives on `TemporalExtent.precision`
  and `.uncertainty`, which `domain/interval.py` actually consults. If "how sure
  are we about this date" is ever wanted: the old number was a table of
  hand-tuned constants rather than a calibrated probability, so resurrecting it
  adds a figure that looks meaningful and is not. The table is at
  `d49f56b:src/redstring/services/temporal_parser.py::_calculate_confidence`
  (verified resolvable in slice 11).
- **Named eras.** The old parser dated "medieval period" to 500-1500 CE,
  "renaissance" to 1400-1600 and "ancient" to 1-500. Those are claims about
  historiography, not about the text, and the patterns were unanchored, so any
  passing use of the word "renaissance" became a dated event spanning two
  centuries. Century patterns are kept because "19th century" does name a span
  the text is asserting.

### B52. The model is no longer asked to normalise dates itself

The deleted `TemporalEventProperties` asked the model for six temporal fields:
`temporal_expression`, `event_date`, `end_date`, `is_approximate`,
`temporal_qualifier` and `sequence_position`. The `ExtractedEntity` in
`extraction/schema.py` asks for two: the expression as the text writes it, and
`sequence_position`.

The four that went were all the model doing work the parser does better and
more consistently. `event_date`/`end_date` asked it to emit ISO 8601, which it
does unevenly and which then has to be parsed anyway; `is_approximate` and
`temporal_qualifier` are detectable from the expression by
`detect_uncertainty`, and asking twice invites the two answers to disagree --
`_determine_uncertainty` in the deleted enrichment service existed solely to
adjudicate between them, with a hand-written precedence table.

**What has not been checked** is whether a smaller model extracts temporal
expressions *worse* when it is no longer prompted to think about
normalisation -- the six-field schema may have been doing some of its work as a
chain of thought. There is no accuracy suite to measure that with (B12), so it
is an open question rather than a settled one. If temporal recall looks poor on
a real corpus, this is the first thing to try reverting.

**Slice 11 correction:** the previous version of this entry said the six-field
schema was "still present on the legacy `extraction/schemas.py`, which slice 9
owns". Both that file and `TemporalEventProperties` were deleted in slice 9 and
neither exists anywhere in the tree. The recoverable copy is under
`src/redstring/extraction/schemas.py` at `66f589d` or earlier.

### B57. Extraction is not constrained to a domain's vocabulary, only prompted with it

Slice 10 wired domain-aware prompting: `domain_system_prompt` renders a
`DomainSchema` into the `system_prompt` `ExtractionPipeline` already took, and
`build_graph(..., domain=AUTO)` lets `ContentClassifier` choose it. What that
gives the model is a *description* of the domain's entity and relationship
types. It does not constrain the output to them: the JSON Schema the server
decodes against comes from `extraction.schema.Extraction`, whose `entity_type`
is a bare `str` (verified in slice 11).

**The function that looked like it did this is gone, and could not have.**
`prompt_generator.generate_json_schema` built a JSON Schema `dict` with the
domain's type ids as an `enum` -- but `LlmProvider.extract` takes a pydantic
*class*, so there was no parameter to pass a dict to, and the dict it built
named its fields `type`/`source`/`target` where `Extraction` uses
`entity_type`/`source_name`/`target_name`. A model that obeyed it would have
produced output `map_extraction` cannot read. Recoverable from `e063faa`
(verified in slice 11).

**What it would actually take.** Either a per-domain pydantic model built at
runtime (`pydantic.create_model` with `entity_type: Literal[...]`), threaded
through `ExtractionPipeline` as a schema argument rather than a prompt one --
which makes `map_extraction` generic over the schema it maps, since it reads
`Extraction`'s field names today. Or a validation pass after extraction that
drops or re-labels out-of-vocabulary types, which needs a decision about
whether an unexpected type is a finding or a defect.

Not obviously worth it: a domain schema's entity list is a *hint* about what
matters, and a hard enum turns everything the domain author did not think of
into "custom" or into nothing. Measure first -- B12's accuracy suite is the
place -- rather than assuming constrained decoding extracts better.

### B44. Rejected merge candidates are discarded, not recorded

`consolidation/service.py::resolve` drops every candidate the policy or the
model rejected. Nothing records that the pair was considered, what it scored,
or what the model said about it.

That is the data needed to tune `HIGH_SIMILARITY` and `LOW_SIMILARITY`, which
are currently inherited numbers with no measurement behind them on this
corpus. Without the rejections there is no way to answer "how many real
duplicates fall below the low threshold" except by re-running the whole
pipeline with a wider band -- and the model calls that band costs are exactly
what the thresholds exist to avoid spending twice.

**Not simply "emit an event for it".** A `MergeRejected` on the consolidation
stream would put one event per considered pair into a permanent, replayed log
that already grows with a tenant's merge history, to record something with no
effect on the graph. The right home is probably a projection or a metrics
sink, which is a decision about a piece of infrastructure this project does
not have yet.

Interim: `ScoredCandidate` already carries the per-signal features, so a
caller wanting this today can call `CandidateFinder.candidates` itself and log
what it sees before handing the survivors to `resolve`.

### B18b. `architecture.md` not imported from `eventsource-py`

Five of the six rules in `~/workspace/eventsource-py/.claude/rules/` were
imported into `.claude/rules/`. `architecture.md` was not. It is ~16KB and
almost entirely an inventory of *that* project's rings, module homes, and ADR
numbers (`domain/types.py` contents, which locations are "settled" vs.
"transitional", `AggregateTypeNotSetError`, ADRs 0030–0046) — none of which
exists here.

The transferable part is the Dependency Rule itself, and redstring already
declares its layered contract in `pyproject.toml`, enforced by
`lint-imports`, and summarised in `CLAUDE.md`. Restating it a third time in a
rules file would be defect shape §2 — a redundant declaration site with
undocumented precedence — in the file that warns against it.

**The trigger has fired and this is now actionable.**
`docs/plans/ring-migration.md` landed and the migration it describes is
finished, so the per-ring guidance the entry was waiting for — "what belongs
in `domain/`", "why *this* import is forbidden" — is knowable rather than
speculative. The contract in `pyproject.toml` expresses it only as a graph,
and `CLAUDE.md`'s layer section is already the longest thing in that file.

The rule to write is redstring's own, derived from its rings, not a port of
eventsource's. Its content is the reasoning `pyproject.toml` carries inline
about *why each sibling is a sibling* — `llm` beside `extraction` so
extraction can reach only the port, `consolidation` and `temporal` beside it
so neither can reach `mapping.py` — which is currently readable only by
someone who thinks to open a config file.

### B28. Three property-merge strategies deferred

`PropertyMergeStrategy` has five members. The re-architecture keeps the
abstraction (`MergeStrategy.resolve(property, canonical, others)`) but
implements only `PREFER_CANONICAL` (the default) and `UNION` (structural —
merging inherently produces alias sets).

Deferred, each raising `NotImplementedError` naming this entry rather than
silently falling back:

- `PREFER_MERGED` — trivial to implement, but no caller wants it yet.
- `LATEST` — needs a trustworthy updated-at on every property source. The
  current model has one timestamp per entity, not per property, so "latest"
  is not actually answerable today.
- `DEEP_MERGE` — nested-dict semantics for `properties`, `extracted_data`,
  and `external_ids`. Easy to get subtly wrong, and wrong deep merges are
  hard to undo because the pre-merge shape is not recoverable from the
  result.

Implement when a caller needs one, not before. The port shape accepts them
without redesign.

### B10c1. Hop distance from `neighbors` — deliberately not added

`ports/graph_store.py::neighbors` returns entities without how far away they
are. **This is a decided deferral, not an oversight**, taken with the trade-off
explicit: the need was speculative, the port had just been through review, and
widening a contract that three adapters must implement on speculation is worse
than retrofitting later. It knowingly cuts against "change the port before the
second adapter exists", because the retrofit here is mechanical rather than
structural.

Both adapters can supply it cheaply, which is what makes the deferral safe:

- **In-memory** (`graph/adapters/memory.py`) already carries the hop count —
  its BFS frontier is `deque[tuple[EntityId, int]]` and `hops` is in hand at
  the moment a neighbour is appended. It is thrown away, not computed.
- **Neo4j** needs `min(length(p))`. It is *not* free in the current shape:
  the query is `RETURN DISTINCT e ORDER BY e.id`, and `DISTINCT` collapses
  exactly the paths that carry the length. The form is

  ```cypher
  MATCH p = (origin)-[rels:RELATES_TO*1..N]-(e:Entity)
  WHERE ...
  RETURN e, min(length(p)) AS hops
  ORDER BY e.id
  ```

  — an aggregation grouped by `e`, replacing the `DISTINCT`. Cheap, but a
  different query rather than an extra return column.

Whoever adds it must also extend `tests/compliance/graph_store.py`: shortest
distance, not first-found, is the contract worth pinning, and a **diamond**-shaped
graph (two paths of different length to the same node) is the case that
separates them. On a chain they are the same function, which is why a chain
would pass while shipping first-found semantics.

### B10. What each store is exercised against — the map, not a gap

**This entry no longer describes a gap in the suite; it describes the map.**
Its original claim -- "there is no sqlite, no `create_async_engine`, no
`sessionmaker`, and no integration fixture" -- was resolved in the only way
that was ever going to work: slice 9 deleted the SQL. There is no ORM, no
session and no schema left to exercise, so "no database in the test suite" has
no subject.

The six modules it named as having unexercised SQL -- `vector_ops`,
`blocking`, `merge_service`, `timeline_query`, `project_timeline_query`,
`sync_status` -- are all deleted. Their capabilities were rebuilt on the ports
and are covered by the compliance suites: blocking in `domain/blocking.py` and
`GraphStore.find_by_blocking_keys`, similarity in `VectorStore.search`,
merging in `consolidation/`, timelines in `temporal/`.

| Port | In-memory | Real backend |
|---|---|---|
| `GraphStore` | `graph/adapters/memory.py`, default gate | Neo4j, `-m integration` (slices 4, 7) |
| `VectorStore` | `vector/adapters/memory.py`, default gate | pgvector, `-m integration` (slice 5) |
| `Cache` | `MemoryCache`, default gate | Redis, `-m integration` (slice 11) |
| `LlmProvider` | `FakeLlmProvider`, default gate | live model, `-m integration` (slice 6) |

Every port now has both tiers; the `Cache` row was the last gap, closed in
slice 11 by running `CacheCompliance` against a real Redis -- which promptly
found a bug (`recurring-defects.md` (g)). What remains is structural — about *how* the integration suites
run rather than whether they exist: B10a, B10f, B10m.

---

## 6. Tooling, packaging and hygiene

### B18. `UP042` is ignored project-wide

Rewriting `class X(str, Enum)` as `enum.StrEnum` changes `str(X.A)` from
`"X.A"` to `"a"`, silently altering every f-string and log line holding a
member. This is a behaviour migration to make wholesale with tests, not a
drive-by autofix. Rationale is recorded in `pyproject.toml`.

**The idiom appears at 8 sites, not the 33 this entry claimed** (re-counted in
slice 11; the 33 was measured before slices 6-10 deleted most of the
codebase). The eight are `BlockingKeyStrategy`, `DatePrecision`,
`UncertaintyMarker`, `PropertyMergeStrategy`, `MergeDecision`, `CircuitState`,
`TemporalRelation` and `ExtractionMethod` — every one of them in `domain/`
except `MergeDecision` and `CircuitState`.

At eight sites this is now a genuinely small job, and the reason to do it is
sharper than tidiness: **several of these are persisted in event payloads**, so
their `str()` form is a wire format. Doing the migration deliberately, with a
test pinning the serialised value of each member before and after, is cheap
now and gets more expensive with every event written.

### B42. `ANN401` is silenced on `domain/merge_strategy.py::resolve`

Three `# noqa: ANN401` on `resolve` and `_union`. Silencing is correct here
rather than a shortcut, and the reasoning is worth keeping because the obvious
fixes are both wrong:

- **Narrow the type.** The values are entries of `Entity.properties` and
  `Entity.external_ids`, declared `dict[str, Any]`, holding whatever an
  extraction found. Any narrower annotation is a claim the function cannot
  honour.
- **Use a `TypeVar`.** It would say the output type matches the input, which
  is true for `PREFER_CANONICAL` and false for `UNION` -- that one returns a
  *list* of them. A signature that is wrong for half the enum is worse than
  `Any`.

The real fix is upstream: `properties` and `external_ids` have no value schema
at all. They are no longer entirely unconstrained -- `domain/json_safety.py`
refuses a NUL anywhere inside them, because a JSON column cannot hold one --
but that is one rule about text, not a schema, and `Any` is still what the
annotation says. Give them a value schema and this rule stops firing on its
own. Until then, `noqa` with this note beats a lie in the
signature, and **it must not become a per-file ignore**: both legacy exemption
lists emptied in slice 10 and mypy's `exclude` key was deleted outright, so
there is no list left to be added to — which makes adding one back a visible
decision rather than an edit.

### B27. `child_of` normalization — the code this described no longer exists

**Rewritten in slice 11. The original entry was stale and pointed at nothing.**
It named `extraction/schemas.py::normalize_relationship_type`, describing a
duplicate dict key that mapped `"child_of"` to `"part_of"` and then again to
`"related_to"`. Both the function and the file were deleted in slice 9. There
is no relationship-type normalization anywhere in `src/` today.

The only surviving trace is `child_of` as a declared relationship type id in
`extraction/domains/schemas/literature_fiction.yaml`, where it is a domain
author's vocabulary entry and not a normalization rule.

**Kept, reduced, because one question outlived the code:** the library no
longer normalizes relationship types at all, and nothing decided that it
should not — the capability left with the file it happened to live in. If
relationship-type normalization is ever wanted back, the taxonomy question is
still open (is `child_of` containment or association?), and the answer needs
to be a test, which is what was missing the first time.

### B62. Orphaned `:BlockingKey` nodes are not reaped on upsert, and ADR 0003 says they are

Found while writing `docs/reference/neo4j-graph-store.md` against the source.

`graph/adapters/neo4j.py::_write_blocking_keys` deletes an entity's previous
`:BLOCKED_BY` edges and then merges new ones. It never deletes a key node that
the delete has just left with no incoming edges. The only place a
`:BlockingKey` node is ever removed is `delete_by_tenant`, which wipes the
whole tenant's keys.

So a tenant that re-upserts entities with churning keys — which is what a
re-extraction of a changed document does — accumulates one node per distinct
key ever seen, and only a full tenant wipe clears them. An orphan matches
nothing, so no read is wrong; it is a growth problem, not a correctness one.

ADR 0003's "The trap this decision creates" section states "Orphaned
`:BlockingKey` nodes are cleaned up because an orphan matches nothing and
leaving it would be a slow leak." **That sentence does not describe the
code.** Either the reap was intended and dropped, or the sentence was written
about `delete_by_tenant` and reads as if it were about the upsert path.

Deferred rather than fixed because the size of the leak is unmeasured: nobody
has run a churning-key workload against the Neo4j container, and the obvious
fix (a `WHERE NOT EXISTS { (k)<-[:BLOCKED_BY]-() } DELETE k` pass after the
delete statement) adds a third statement to every batch upsert, whose write
cost ADR 0003 measured carefully. Measure the leak before paying for it, and
correct the ADR either way.

### B65. `reference/domain-value-types.md` and the outline it was written against

**Closed for both pages.** Every section the two module-map tables promise now
exists, written against the code rather than against the outline -- which the
original entry warned was three slices stale and wrong at least once.

Kept, reduced, because the *finding* outlives the work and will recur. The
broken anchors these pages carried were not renamed headings: most pointed at
sections that existed nowhere, because both pages were written from an outline
whose content was never filled in. The links were the only surviving trace of
it, and repairing them made the gap invisible again -- which is why this
entry existed at all.

**Two claims in those dead links were wrong rather than missing**, and that is
the transferable half:

- the anchor for `VectorMatch.score` encoded the mapping as `(1 - cosine) / 2`,
  a *distance*, where `domain/vector.py` says `(1 + cosine) / 2`. The two
  differ on every input.
- writing the temporal section produced a fresh one -- that a widened one-day
  extent has coincident endpoints. `widen` returns the first instant *after*
  the unit, half-open on purpose, so it does not.

The first survived because a link nobody can follow is a claim nobody
re-checks. The second was caught only by running the code while writing the
paragraph. **Write a reference section against the source, then execute the
examples**; a plausible paragraph about arithmetic nobody re-derived is how
both of these got in.

`mkdocs build --strict` is now the gate for the first kind -- it fails on an
anchor that resolves nowhere, and it caught two of mine while this was being
written. Nothing gates the second kind but running the code.

### B72. `is_valid_source` normalizes differently from the loader that filled the list

`extraction/domains/models.py`. `RelationshipTypeSchema.normalize_type_lists`
stores `valid_source_types` / `valid_target_types` lowercased, stripped, with
spaces and hyphens turned into underscores. `is_valid_source` and
`is_valid_target` normalize the argument with **`.lower().strip()` only**, so
the string that was written in the YAML does not match itself:

```python
schema = RelationshipTypeSchema(
    id="loves", description="...", valid_source_types=["Main Character"]
)
schema.valid_source_types  # ['main_character']
schema.is_valid_source("Main Character")  # False
schema.is_valid_source("main_character")  # True
```

Recurring-defect §2 exactly: one fact -- "how an entity type id is spelled" --
with two normalizers and nothing that fails when they disagree.

**Who is actually hurt.** Entity type *ids* are normalized on load, so a
caller passing an `EntityTypeSchema.id` is fine, and that is what the bundled
schemas and every test do. The caller who is not fine is one passing an
`Entity.entity_type`, which is free-form text straight from the model where
"Main Character" is an ordinary answer -- and `DomainSchema.validate_relationship`
is a public helper that invites exactly that.

**Not fixed on the spot because it is a behaviour change on a public helper,
not a typo.** Sharing one normalizer makes calls that return `False` today
start returning `True`, which is the intended answer but is still a change a
caller could be relying on. The fix is to lift the loader's transform into a
named function in the same module and call it from both -- there is no case
for two -- and to add the test that would have caught it: assert
`is_valid_source(x)` for the *same string* `x` that was passed to the
constructor, which no current test does.

Found while writing the relationship-type section of
`reference/domain-schema-yaml.md` (B65), and documented there as observable
behaviour rather than left for the next reader to trip over.

### B67. No way to find entities that were never consolidated

Reported downstream. `Entity` carries no consolidation state, so "resolve
whatever has not been resolved yet" is a scan of every entity in the tenant.
Consolidation is a problem this library claims to own, and the incremental case
— documents arriving continuously, consolidation running periodically — is the
normal one rather than an exotic one.

Do not solve it by adding a mutable `consolidated: bool` to `Entity`. The write
model is event-sourced and `Entity` is a value handed to a projection; a flag on
it would be state the log does not own, and the first replay would have to
reconstruct it anyway. The candidates worth weighing are a projection that
maintains an unresolved set from `DocumentExtracted` and `EntitiesMerged`, or a
store-level query over "entities with no alias and no merge event". Both are
real designs; neither is a field.

### B68. `project()` cannot scope to a stream or category

Reported downstream, who are using `tenant_filter` as the workaround. A shared
event store replays every foreign event through every projection, which is
correctness-neutral and cost-linear in other people's traffic. The workaround
is real but is a filter applied after delivery, not a narrower subscription.

Whether this is redstring's to fix depends on what `eventsource-py` exposes —
check before designing, because a scoped subscription in the library that
filters client-side is the same cost with more code.

### B70. `eventsource-py` floor was too low, and the library was published with it

**Fixed, and recorded here because the shipped `0.1.0` carries the wrong
floor.** Reported downstream as trivial; it is trivial to fix and was not
trivial in effect.

`pyproject.toml` declared `eventsource-py>=0.9.1,<0.11` while
`projections/base.py` forwards `retry_policy`, `tracer` and `tenant_filter` to
`DeclarativeProjection.__init__`. Measured across both versions rather than
assumed — and the downstream report is right in substance and slightly off in
detail, which matters for anyone pinning:

```
0.9.1:  (checkpoint_repo, dlq_repo, enable_tracing, *, tenant_filter)
0.10.0: (checkpoint_repo, dlq_repo, enable_tracing, *, retry_policy, tracer, tenant_filter)
```

`tenant_filter` existed in 0.9.1. Only `retry_policy` and `tracer` are new, so
the failure is `TypeError: unexpected keyword argument 'retry_policy'` at
**projection construction**, not at import — `import redstring` succeeds and the
first `GraphProjection(...)` does not.

The floor is now `>=0.10.0`. What remains open is the general case: nothing
proves a declared floor actually works, because CI resolves fresh and gets the
newest release. A test that installs the declared floor into a temporary venv
and constructs a projection would close it, and is the same shape as
`test_wheel_contents.py` — slow, `integration`-marked, and the only kind of
check that measures the claim rather than a proxy for it.

**Update (slice 11).** That test now exists as
`tests/integration/test_declared_floors_work.py`, and it covers
`eventsource-py` only. **The general case is still open** — no other declared
floor is proved, and each one is a separate `>=` that CI never resolves to.
The cap also moved to `<0.12`, tested against 0.11.0; the floor deliberately
did **not** move with it, because a floor states what the library needs and
nothing here needs anything 0.11.0 added.

### B71. The confined-dependency table is hand-kept, so a new driver is silent

`tests/unit/test_dependencies_stay_confined.py` carries `CONFINEMENTS`, four
rows naming a third-party client and the one directory it may be imported
from. Every row is guarded in both directions — a row whose directory has
stopped importing its library fails, and a leak outside it fails — so the rows
that *exist* cannot rot.

**What nothing catches is a fifth library with no row at all.** Add
`uv add --optional graph some-driver`, import it from `composition.py`, and the
suite stays green: the file only knows about what someone listed. That is
`recurring-defects.md` §2 — the set of confined dependencies is declared in two
places (`pyproject.toml`'s optional-dependency tables and this tuple) with
nothing failing when they disagree — and it is the same shape the test itself
exists to fix, one level up.

The reason it was left: deriving the list from `pyproject.toml` is not the
one-liner it looks like. `[project.optional-dependencies]` names
*distributions* (`langchain-openai`, `neo4j`), not import names
(`langchain_openai`, `neo4j`), and the mapping between them is only
discoverable from installed package metadata (`importlib.metadata.packages_distributions()`),
which needs the extra installed — the condition `--all-extras` exists to
guarantee and that CLAUDE.md records losing a mutation run to. It also has no
opinion about *which* directory a given distribution belongs in, so the
directory column stays hand-written regardless; the derivable part is only
"every optional distribution has a row", which is the half that matters.

A cheaper 80% is a test asserting the set of top-level packages imported
anywhere under `src/` and not in the stdlib and not first-party is a subset of
the union of the table's `packages` plus a small allowed-everywhere set
(`pydantic`, `eventsource`, `jellyfish`, `yaml`). That needs no metadata and
fails the day an unlisted client appears. Worth doing before the fifth adapter,
not after.
