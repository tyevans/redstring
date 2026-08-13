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

The default gate collects **2209 tests**, plus **250 `integration` tests** and
**4 `accuracy` tests** — against a real Neo4j (slices 4, 7), real pgvector
(slice 5), a live `qwen3.6-27b-mtp` (slice 6, `KG_LLM_BASE_URL`), and a built
wheel (slice 10). The first two need `docker-compose.test.yml`. Both extra
suites are deselected by default (B10a); a run prints what it deselected and
how to run it. What the accuracy suite can and cannot tell you is B12.

**The gate is green.** Verified under its own conditions
(`uv run python scripts/coverage_ratchet.py`, which is `pytest -q -n auto
--cov`): **2209 passed**, no failures. The hypothesis deadline flake that made
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
wipes and replays one tenant. **The read half of that now exists** —
`project(..., tenant_id=...)` (B68) — so what is left is the wipe: a caller
still has to call `delete_by_tenant` on both stores itself, which is what the
replay tests do. Composing the two is what `rebuild` would be, and it belongs
with whatever slice first needs it in anger.

---

## 2. Things that are unverified

Code that may well be correct, with nothing that would tell us if it were not.
These are the entries most likely to become section 1 without warning.

### B119. The caller-owned resource checks are written in the form upstream refuted

Three tests assert that an adapter handed a driver or pool leaves it alone,
and all three ask the *wrong question*:

| File | The assertion |
|---|---|
| `tests/integration/vector/test_pgvector_store.py:518` | `assert await pool.fetchval("SELECT 1") == 1` |
| `tests/integration/graph/test_neo4j_store.py:256` | `session.run("RETURN 1 AS one")` |
| `tests/integration/chunks/test_postgres_store.py:760` | same shape |

Each runs a query against the caller's resource after the adapter's `close()`
and takes success as proof the adapter did not close it.

**eventsource-py measured that exact form and found it does not bite.** Its
0.13.0 changelog records writing its `CallerOwnedResourceCase` this way first
and watching the ownership mutant *survive*: a disposed SQLAlchemy
`AsyncEngine` happily opens a new connection, so "can it still run a query"
answers yes either way. The observable that worked was **pool identity**.

For `asyncpg.Pool` and `neo4j.AsyncDriver` the query form plausibly does bite,
because both close explicitly rather than swapping a pool underneath. But
that is an argument, and CLAUDE.md's rule is that a passing check you have
never seen fail is not yet evidence. Break each one on purpose — make the
adapter close the resource it was handed — and watch the test fail, or
replace the query with an identity check.

Two things make this worth doing rather than filing and forgetting. It is
three copies of one fact (`recurring-defects.md` §2), all `integration`-marked
so none runs in the commit gate; and `src/redstring/testing/lifetime.py`
holds only `NoOpLifetime`, a double mixin, with no lifetime *contract* suite
at all — so the natural fix is one shared case in the shipped suite, which is
also the shape eventsource landed.

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

### B53. `Entity.temporal`'s storage shape is settled with B48, not before

**The missing test is written.** `tests/compliance/graph_store.py` now asserts
that a fully-populated `TemporalExtent` round-trips field for field, and that
an entity with no extent comes back with `None` rather than an empty one --
both by example rather than by sampler, since `entities()` draws a temporal
only about half the time and `max_examples` is environment-tunable and lowered
to 5 by mutation runs. Proved able to fail by making the in-memory adapter drop
the field. Every adapter is now held to it, which was the point of putting it
in the shared suite.

The original entry's storage claim was **false** and was corrected in slice 11:
the Neo4j adapter does encode `temporal` as `temporal_json`, and
`TemporalQuery.entities_in_interval` returns the entity, including the
precision-widening case. *That claim survived two slices because it was
plausible and nobody ran it. It took ninety seconds to check.*

**What remains open is the indexing half, and it is a joint decision with
B48.** `temporal` is a JSON blob, so it cannot serve an indexed range
prefilter. Flattening `TemporalExtent` into node properties would make B48's
prefilter possible; the blob makes it impossible. Do not change the storage
shape without settling B48 at the same time -- and note that the round-trip
test above is now what would catch a flattening that loses `precision`, which
is the field `domain/interval.py` needs and the one a timestamp column would
silently drop.

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

### B54. `temporal_parsing.py`'s mutants: 391 verified, 459 not

**Progress, with the mechanism in the tree.** Slice 8 ran the precision logic.
Four scoped sessions have since run the range and partial-date region, the
period/century region, all of `render_temporal`, and the first third of
`widen` -- and **each found something**. What remains is `parse_temporal`, the
absolute/natural strategies, most of `widen`, and a band nobody has looked at.

**Run one with the wrapper**, which makes this affordable and is why the
remainder is a matter of time rather than of design:

```
uv run python scripts/mutation.py cosmic-ray \
    --config cosmic-ray-render.toml --rows 600:609 --session widen.sqlite
```

`--config` narrows the `test-command` (and the baseline with it, so the
narrower command is proved green first); `--rows` deletes the mutants outside
the region, since cosmic-ray has no line filter. Against the default command a
mutant costs ~70s and the whole module is seventeen hours; scoped to the
render/widen classes it is ~7s, and 159 mutants took about two hours on a
machine also running the test suite.

**Where the mutants actually are.** Re-measured from a fresh
`cosmic-ray init` at the commit that closed the render region -- the previous
version of this table was measured before the file moved, and its line ranges
had drifted far enough to matter. A `--rows 530:599` run aimed at
"`render_temporal` and `widen`" got all of the first and **a third** of the
second, because `widen` now ends at 609 rather than 599. Re-measure the bands
before choosing one; do not trust the numbers below to survive an edit to the
module.

| Region | Lines | Mutants | Status |
|---|---|---|---|
| periods / centuries | 321-362 | 176 | **run** |
| ranges | 207-273 | 161 | **run** |
| `render_temporal` | 530-586 | 126 | 27 verified, **99 timed out** |
| `widen` | 587-609 | 113 | 33 timed out, **80 unrun** |
| `parse_temporal` | 462-529 | 77 | unrun |
| partial dates | 274-320 | 71 | **run** |
| unnamed band | 363-388 | 68 | **unrun, and never named** |
| absolute / natural | 389-461 | 42 | unrun |
| header / imports | 1-123 | 14 | unrun |
| uncertainty + stripping | 124-206 | 2 | run |

Two rows correct this entry's own premises. The last is the older correction:
it once named the uncertainty patterns first among what was unrun, and they
are **two mutants**, because cosmic-ray mutates operators and that region is a
table of compiled regexes. The **unnamed band** is the newer one -- 68
mutants, eighth of ten regions by size, that no version of this table has ever
listed. It was invisible because the bands were written from the module's
section headings rather than from the measurement, so a region between two
headings had nowhere to appear.

**The range run: 268 mutants, 22 survivors, all classified.**

- **16 equivalent by construction** -- `_Parsed | None` in two return
  annotations, rewritten as `+`, `%`, `^`, `**` and so on. PEP 563 makes
  annotations strings that are never evaluated; unkillable here and anywhere.
- **1 equivalent** -- `name != "September"` as `is not`, over month names that
  are module-level literals and therefore interned. It is CLAUDE.md's row-one
  trap sitting in the tree, equivalent only because every operand is a
  literal, and it would stop being equivalent the moment a spelling arrived
  from anywhere else.
- **4 test gaps, now closed** -- a year range and a month range with *equal*
  endpoints (`end < start` widened to `<=` returned `None` and nothing
  noticed), and a quarter range *starting* at Q3 or Q4. The last is the
  instructive one: `(first - 1) * 3 + 1` and `(first >> 1) * 3 + 1` agree for
  Q1 and Q2 and differ for Q3 and Q4, and the existing cases were `Q1-Q2` and
  `Q2-Q4` -- so the range's *end* was covered at Q4 while its *start* was
  blind, which reading the parameters does not reveal.
- **1 real defect, fixed** -- see below.

**The defect: `_MONTH_NUMBERS` carried a spelling `_MONTH` could not produce.**
The spelling table has exactly one conditional, whose entire purpose is to add
"Sept" for September. The `_MONTH` pattern accepted `Sep(?:tember)?` and not
"Sept", so that entry was **unreachable** -- "Sept 2024" fell through every
pattern to `dateparser`, resolved differently against the two probe dates, and
raised `AmbiguousReferenceDateError` instead of parsing. Two declarations of
one fact with nothing failing while they disagreed, and it could only have
been found this way: mutating the branch changed nothing observable, because
no input reached it.

Fixed in the pattern rather than by deleting the entry -- "Sept" is ordinary
text and the table's intent was plainly to accept it -- with
`test_every_spelling_the_table_maps_is_one_the_pattern_accepts` as the gate,
proved red by reverting the pattern.

**The period/century run: 176 mutants, 28 survivors, all classified.**

- **11 equivalent by construction** -- the `_Parsed | None` return annotation
  again.
- **2 equivalent, and worth understanding rather than pattern-matching** --
  `base + 1` rewritten as `base | 1` and `base ^ 1`. `base` is
  `(century - 1) * 100`, always a multiple of 100 and therefore always even,
  so bit 0 is clear and all three spellings agree for *every* century. Not
  "equivalent on the inputs we test": equivalent, full stop.
- **15 test gaps, now closed.** Four on the `century < 1` guard and eleven on
  the portion arithmetic.

**The century arithmetic could not be tested at the 19th century at all**, and
that is the finding worth carrying. `(19 - 1) * 100` is 1800, which shares no
set bit with 1, 33, 34, 66, 67 or 100 -- so `base + k`, `base | k` and
`base ^ k` are *the same number* for every constant in the table. And
`century - 1` equals `century ^ 1` for any odd century. Every existing case
used the 19th century, which is the natural example for a library that reads
historical text, and it made eleven mutants unkillable. The 20th century
(base 1900) breaks every one of those coincidences.

The guard needed its own boundary: `century < 1` widened to `< 2` rejects
"early 1st century", and the first version of that test used plain
"1st century" -- which `_CENTURY` matches and which never reaches the guard at
all, so it passed against the mutant. **A boundary test has to reach the
branch the boundary is in.**

**The render run: 159 mutants, 7 survivors, all classified.** The smallest
survivor count of the four, and the classification is most of the value.

- **4 equivalent by construction** -- the `str | None` return annotation on
  `render_temporal`, the PEP 563 shape again.
- **1 equivalent, and provably so** -- `start != datetime(start.year,
  start.month, start.day, ...)` rewritten as `>`. The two differ only where
  `start` is *below* the midnight of its own date, which no datetime is.
- **1 equivalent for the declared type** -- `precision is DatePrecision.YEAR`
  as `==`. Enum identity and equality agree; the two would part only for an
  argument the annotation forbids.
- **1 test gap, now closed** -- and it is CLAUDE.md's row about intervals
  whose bounds never coincide, second instance, in a different module.

**The gap: `end.year <= start.year` survived being rewritten as `is`.**
`TestRenderDeclines` *does* carry a year-range case, `2023-01-01` to
`2023-06-01` -- but June 1 is not the first of its year, so the *first* clause
of the `or` answers and the comparison is never what decides. Identity is
false for every distinct `int` object, so under the mutant `"2023-2023"`
rendered as a range the parser then refuses to read back. Closed with a
coincident-endpoint case, proved by hand-applying the mutant under
`PYTHONDONTWRITEBYTECODE=1`.

Note what is *not* testable there: `TemporalExtent` rejects `end < start` at
construction, so the `<` half of `<=` is unreachable and a coincident case is
the whole of what an assertion can reach.

**212 of the mutants counted as "run" above were timeouts, and are not.**
The `render` session recorded 152 kills of which **132 were timeouts**, and
the `widen` session recorded 80 kills of which **all 80** were. cosmic-ray
records a timeout as `KILLED` and `cr-report` does not distinguish it, so both
read as clean runs -- "0 survivors" out of 80 is the shape this file's whole
mutation section is about, arrived at by a third route the baseline check
cannot see.

The cause was machine load: these ran alongside several other projects' test
suites, at a load average of ~100 on 16 cores, and the config allowed 30s per
mutant. A hand-applied mutant from the timed-out set fails with a `TypeError`
in the **first second** and still would have timed out, because the rest of
the command could not finish in 30s under that load -- so the honest reading
is that most of the 212 were probably killed on the merits and none of them
can be shown to have been.

**Not re-run, deliberately.** A re-run on the same machine produces another
set of results nobody can trust, and `timeout` is now 120s against a
measurement taken under load rather than a clean one. Re-run both regions
when the machine is quiet, and set the timeout from a measurement taken
there:

```
uv run python scripts/mutation.py cosmic-ray \
    --config cosmic-ray-render.toml --rows 530:599 --session render.sqlite
uv run python scripts/mutation.py cosmic-ray \
    --config cosmic-ray-render.toml --rows 600:609 --session widen.sqlite
```

`scripts/mutation.py` now refuses a session whose kills are mostly timeouts,
so a repeat announces itself rather than reading as a clean sweep. The two
sessions above are what proved that guard fires; `periods.sqlite`, run on an
idle machine, has zero timeouts and is what proved it does not fire on a good
run.

The 7 render survivors are **not** affected -- they ran the suite and passed,
which is what a survivor is. The classification and the fix stand; the
coverage claim does not.

**The century tail (363-369) was never mutated at all, and its test could not
have killed anything there.** `test_a_century_is_a_range` used one input,
"19th century" -- the exact case CLAUDE.md's bit-pattern row says cannot
distinguish that arithmetic, in the same file where `TestCenturyPortions`
carries twelve lines of comment explaining why. One lesson, two call sites,
applied to one. A 20th-century case is now in, proved by hand-applying
`(century - 1)` as `(century ^ 1)`: the 20th fails, the 19th passes. The 68
mutants there still want a session.

**What to expect from the remaining 247.** Four regions run, four findings,
all the same shape: arithmetic or a comparison exercised at exactly one value,
where a wrong implementation happens to agree. `widen`'s remaining 80 are
precisely that kind, and the unnamed 363-388 band has never been looked at.

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

### B80. `PROPERTY_WEIGHT = 0.6` is a judgement, and nothing in the repo can settle it

`src/redstring/domain/lexical.py` scores a query against an entity's name, its
`normalized_name`, and each string value in `properties` — the last multiplied
by `PROPERTY_WEIGHT = 0.6`. The *shape* is defensible and tested: a name is
what an entity is, a property is something recorded about it, so a property
match is weaker evidence and must score below the same match on the name.
`test_a_property_can_match_but_scores_below_the_same_match_on_the_name` pins
that ordering.

**The number is not tested and cannot be, here.** Any value in `(0, 1)` passes
every test in `tests/unit/domain/test_lexical.py`, because the tests assert the
ordering and the bound, which is all that is knowable without graded data.
0.6 was chosen, not measured.

Why it was not settled now rather than deferred: the only graded corpus in the
repo is `tests/accuracy/`, and it grades **extraction** over five hand-graded
documents. Fitting a retrieval weight against it would be worse than leaving
the guess visible — five documents cannot separate 0.5 from 0.7, and a number
carrying a fitted provenance invites the next author to trust it in a way the
bare guess does not.

What would settle it: a graded *retrieval* corpus — queries paired with the
entities that should come back, ranked — of a size where nDCG@10 separates
candidate weights beyond noise. That is the same corpus B81 needs, so the two
should be picked up together; building it once answers both. Until then, treat
0.6 as a placeholder with a test-pinned ordering around it, and do not tune it
against anything smaller.

### B81. No retrieval accuracy suite exists, so "hybrid beats semantic" is an argument

`tests/accuracy/` measures **extraction** — precision, recall and F1 over five
hand-graded documents, scoring whether the right entities came out of a
document. Nothing measures retrieval. So the claim the whole hybrid design
rests on — that fusing a lexical channel with the semantic one returns better
results than the semantic one alone — is currently reasoning, not a result.

What *is* tested is that the machinery is correct:
`tests/unit/composition/test_retrieval.py` pins filter-before-k, the dangling
skip, tenant isolation, the modes, and the component scores.
`tests/unit/domain/test_fusion.py` pins the fusion arithmetic. None of that is
evidence about ranking quality, and it cannot be — `FakeEmbeddingProvider`'s
vectors come from a hash, so two texts about the same subject are as far apart
as two unrelated ones. **Every retrieval test in the gate is structural by
construction.**

What it needs: queries paired with the entities that should come back, ranked,
over a corpus big enough for nDCG@10 to separate configurations beyond noise;
and a live embeddings endpoint, so it belongs under `-m accuracy` beside the
extraction suite rather than in the commit gate. Reuse the split that suite
already proved — a pure scorer in the gate, the model-dependent half behind
the marker — and prove the harness before believing a number, because a
retrieval scorer fails silently in the same two directions (measuring nothing
reports 0 and reads as a bad model; scoring the corpus against itself reports
1 and reads as a good one).

This is the same corpus B80 needs to settle `PROPERTY_WEIGHT`. Build it once
and both close.

**`ChunkRetriever` (ADR 0038) is a third instance of the same unmeasured
claim.** It fuses a semantic channel over `StoredChunk.embedding` with a BM25
lexical one, by the identical reciprocal-rank-fusion shape `Retriever` uses
for entities, and nothing in this repository measures whether that fusion
beats either chunk channel alone any more than it measures the entity case.
`tests/unit/composition/test_chunk_retrieval.py` pins the machinery --
overfetch, tenant isolation, the modes, the component scores -- which is the
same
structural coverage this entry already describes as not evidence about
ranking quality. The graded corpus this entry calls for, once built, should
be asked both questions rather than only the entity one.

---

## 3. Performance and scale

Nothing here is slow at any size this project has measured. Each entry records
the shape of the cost and what would have to be true before paying to fix it.

### B123. The graph feature's neighbour key is a name, so two distinct neighbours sharing one inflate it

`consolidation/candidates.py::CandidateFinder._neighbour_names` returns
`normalize_name(neighbour.name)` and `_graph_feature` takes the Jaccard of
those. It compares names because ids **cannot** answer the question — see ADR
0034 and that method's docstring: `extraction.mapping.entity_id_for`
namespaces every id by `source_id`, so two extractions of one neighbour have
different ids by construction and an id comparison scored every
cross-document duplicate `0.0`.

What the name key costs: two *genuinely different* neighbours who share a
name — two people called "John Smith", a company and the town it is named
after — read as agreement. The graph feature then reports overlap that is not
there.

**Why it was accepted rather than fixed.** It is the same fallibility
`string_similarity` already documents ("fooled by two different people with
the same name") reaching a second feature, it is bounded by the graph weight
(0.2 by default), and every alternative key available today is worse: the id
is provably useless here, and `(entity_type, normalized_name)` — the obvious
sharpening — only helps when the two collide on name and differ on type,
which is the *rarer* half of the collision. Do that one anyway if you touch
this; it is a two-line change and strictly better, it just does not close the
entry.

**What would close it:** a neighbour key that survives both the document
namespacing and a name collision. The honest one is the neighbour's own
consolidated identity — i.e. run consolidation on the neighbours first and
key on canonical id — which is the ordering problem B124 is the first half
of. Do not reach for blocking keys as the key: `blocking_keys_for` is
deliberately lossy, so it collides *more* than the name does, not less.

### B124. The graph feature does not resolve neighbours through aliases

`_neighbour_names` reads edges with `get_relationships` and fetches the
entities those name, without passing the ids through
`GraphStore.resolve_entity_ids` first. So when consolidation has already
merged two neighbours, the edge still carries the absorbed entity's id and
this method reads the absorbed entity's *name*.

Mostly invisible, which is why it was left: two merged neighbours usually
have similar names, and `normalize_name` plus the set collapse means
"Charles Babbage" absorbed into "Charles Babbage" already counts once. It
bites when a merge joined two genuinely different spellings — "Chas Babbage"
absorbed into "Charles Babbage" — where the subject's side reports one name
and the candidate's the other, and the pair scores as disagreement on a point
the graph has already settled.

**What to do:** resolve `neighbour_ids` before `get_entities`, then fetch the
canonical ids. `_block` already does exactly this one line up, so the shape is
in the file. It costs a third store round-trip per candidate on top of the two
the signal now takes, which is the only reason it is not already there —
`use_graph_signal`'s docstring prices the feature and this would raise it by
half again for a case nobody has measured the frequency of.

**Measure before paying.** Count, over a real corpus, how many neighbour ids
on scored edges are aliases at all. If it is near zero the entry is closable
as "not worth a round trip" rather than by doing the work — and record the
number, because that is the fact this entry is missing.

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

**A second instance of the same cost: the chunk store's semantic scan.**
`ChunkStore.semantic_candidates` (ADR 0038) scans a tenant's chunks exactly,
for the identical reason -- an ANN index over a multi-tenant table either
crosses tenant boundaries or is built per tenant, and ADR 0012's argument does
not depend on which table carries the vector. Nothing here is a new decision;
it is the same trade, paid twice, and the three ways out above apply to
`chunks/adapters/postgres.py` unchanged.

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

### B125. The coverage ratchet measures a sampled quantity, so it ratchets to the luckiest run

`scripts/coverage_ratchet.py` raises `.coverage-baseline` whenever a run
exceeds it and stages the new value. That is only sound if total coverage is a
function of the tree, and it is not: the suite runs under `pytest-randomly`
and `-n auto`, and the compliance suites draw hypothesis examples, so which
lines execute varies run to run.

**Measured, three consecutive runs on an unchanged tree at 8460e88: 96.30,
96.27, 96.30.** The committed baseline was **96.33** — above all three,
because the run that happened to earn it was the one that got lucky. Cutting
0.5.0 then failed the gate on a commit touching only a version string and the
CHANGELOG, reporting `96.30% is below the baseline of 96.33%`. `TOLERANCE` is
0.01 and the observed spread is ~0.06, so the slack is a factor of six too
small for the noise it is absorbing.

Baseline history shows this has happened before and was papered over:
`96.24 -> 96.32 -> 96.30 -> 96.33`, and a ratchet cannot lower itself, so the
0.4.0 release edited 96.32 down to 96.30 by hand. Same failure, same manual
correction, not recorded as a defect either time.

**`TOLERANCE` is now 0.1, which is the cheapest of the three fixes below and
all that has been done.** Lowering the baseline by hand was tried first and
does not work: `TOLERANCE` governs the raise as well as the failure, so at
0.01 the next run measured over the lowered value and the ratchet staged the
high-water mark straight back into the same commit. An unstable quantity
ratcheted to its maximum converges on its maximum. Widening the slack fixes
both directions, because the ratchet now declines to chase noise upward.

What it costs, stated plainly: a real regression under 0.1pp now passes. That
is the width of the noise, so it was not being detected before either — 0.01
bought false precision rather than sensitivity — but the window is real and
this entry stays open until the measurement is deterministic enough to close
it.

**What would fix it properly.** In rough order of cost:

- ~~Raise `TOLERANCE` to cover the measured spread.~~ **Done.** Treats the
  symptom; the number remains noisy and the window remains 0.1pp wide.
- Take the *minimum* of N runs before ratcheting up. Correct, and it makes the
  commit hook N times slower, which is the whole reason not to.
- Make the measurement deterministic: pin the hypothesis seed for the coverage
  run specifically, or exclude the sampled suites from the number. Note this
  conflicts with `.claude/rules/testing.md`'s "order-dependent tests are bugs,
  do not pin the seed" — the rule is about *tests*, and this would be pinning
  the *measurement*, but the distinction needs stating in the script or the
  next reader will correctly object.

**Do not close this by raising the baseline back.** The number is not the
problem; ratcheting an unstable quantity is.

### B115. The graded corpus is single-chunk, so nothing gates cross-chunk extraction

All five documents in `tests/accuracy/corpus.yaml` are between 81 and 134
characters, and `SlidingWindowChunker`'s default window is 3000 — so every one
of them is **one chunk**. Confirmed by running the chunker over the corpus,
not inferred from the lengths.

That makes the accuracy suite structurally unable to observe anything about
chunk *n+1*, which is now two shipped mechanisms:
`extraction/carryover.py` acts only on chunk 2 onwards, and a gleaning pass
(`extraction/gleaning.py`) has no cross-chunk behaviour to be compared over.
Both are proved wired and correct by `tests/unit/extraction/`, and neither is
proved to *help* by anything that runs.

**The trap is that the suite passes either way and reads as coverage.** A
carryover regressed to a no-op would clear every floor in
`test_extraction_accuracy.py`, because on a one-chunk document a working
carryover and an absent one produce byte-identical prompts. This was measured
by accident: an A/B run of the two settings over a 2831-character document
returned identical entities, identical relationships and identical
mis-spellings, and the reason was that the document was one chunk — the
"result" was a control that had been mistaken for a measurement.

What it takes: **one graded document long enough to split at the default
window** — 4000+ characters — written so that later paragraphs refer to
entities the earlier ones named in full ("Lovelace" after "Ada Lovelace",
"the Engine" after "the Analytical Engine"). That is the only shape that makes
the carryover's claim falsifiable. Grading it is the expensive part and is the
reason this is filed rather than done: `corpus.yaml`'s rules say grade what
the text states, and a 4000-character document has enough marginal entities
that a second grader would disagree with the first on a dozen of them. Budget
the grading, not the writing.

### What an off-corpus measurement showed, and exactly how far it goes

Worth keeping, because whoever writes the graded document should know what to
expect and what the trap is. A 2831-character Lovelace/Babbage passage,
chunked at 900/100 into three chunks, `qwen3.6-27b-mtp`, each arm run twice:

| | entities | relationships | fragment pairs | one name under two types |
|---|---|---|---|---|
| `carryover_entities=0` | 53 | 54 | 8 | 4 |
| `carryover_entities=32` | 51 | 58 | 7 | 0 |

Both repeats of each arm were **byte-identical**. That is not a suspicious
result here — `LangChainLlmProvider.openai_compatible` defaults to
`temperature=0.0`, so identical prompts give identical completions — and it
settles something useful: the run-to-run noise floor on this rig is *zero*, so
the whole of the difference above is attributable to the carryover and none of
it to sampling.

**It does not follow that this generalises.** Zero variance means repeating
the run tells you nothing new; it does not turn one document into a sample.
The direction matches what the mechanism predicts, and that is all it is.

The clearest signal is the last column, not the first. The off arm produced
the *same name under two different entity types* four times — `Analytical
Engine`, `Engine`, `1871` and `funding` each appearing twice with different
types, which is two ids for one thing — and the on arm produced none. That is
the defect a `(name, entity_type)` carryover is shaped to fix, and it is worth
grading for explicitly: **a graded document should contain at least one entity
whose type a later chunk would plausibly assign differently.**

The off arm also emitted `ine`, a truncated fragment, and
`article on the Analytical Engine` as an entity in its own right. Neither is
about the carryover; both are worth remembering when grading, because a corpus
that never sees them cannot measure whether anything fixed them.

**A second ceiling, found by running B57's measurement: entity recall is
1.000 on every document in both arms.** There is no headroom, so this corpus
cannot detect a recall *improvement* by construction -- which is a separate
problem from the single-chunk one and disqualifies it for exactly the
mechanism most likely to need measuring, ADR 0029's gleaning pass. A document
added for B115 must therefore satisfy three things at once: long enough to
split at the default window, containing entities a later chunk would plausibly
re-name or re-type, and containing entities a good model plausibly *misses*.
The third is the hardest to write on purpose and the easiest to forget.

Related: **B12** on what the corpus can and cannot tell you at all.

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

### B90. `dump_shape`'s chunk oracle pins ids and order, not `entity_ids`

`tests/unit/projections/conftest.py::dump_shape` reduces the chunk store to
`{source_id: [chunk.id, ...]}` per tenant, compared against
`log_builder.py::BuiltLog.expected_shape`. That pins which passages survive a
re-chunk and in what order -- and, because chunk ids are content-addressed
over `(source_id, text)`, the id list also pins the text each chunk holds. It
does not pin `entity_ids`: a handler that wrote every chunk with
`entity_ids=[]` would pass every test using this oracle, since nothing
compares against `StoredChunk.entity_ids` at all.

Add `entity_ids` to both the shape `dump_shape` produces and
`_TenantMirror.chunks` in `log_builder.py`, which currently records only the
id list (`log_builder.py:165` onward). The mirror already has what it needs
per chunk revision (`_chunks` builds each `StoredChunk` with its own
`entity_ids`); it is the reduction into `expected_shape()` and `dump_shape`
that drops the field.

Deferred rather than fixed because it surfaced during an unrelated review pass
and the fix touches the oracle two suites share
(`test_replay_equivalence.py`-style tests and anything else building a
`BuiltLog`) -- worth doing deliberately rather than as a drive-by.

### B78. The embeddings probe is unbounded, and only its *count* was fixed

`tests/integration/llm/test_live_embeddings.py::_serving` reaches the endpoint
through `LangChainEmbeddingProvider.openai_compatible`, which passes **no
`timeout` and no `max_retries`** to `OpenAIEmbeddings`
(`src/redstring/llm/adapters/langchain_embedding.py:105`). It therefore
inherits the openai client's defaults — 600 s, two retries — so a single probe
against an address that blackholes rather than refuses can take ~30 minutes.

**What was fixed was the multiplier, not the term.** The probe now runs in a
module-scoped `live` fixture (one call for 13 tests, verified with
`--setup-show`), and CI selects `-m "integration and not live"` so it never
runs there at all. What remains: a developer running `-m integration` locally
with `KG_EMBED_BASE_URL` unset, whose network drops packets to
`192.168.1.14` instead of refusing them, waits out one unbounded probe with
no output explaining why.

The fix is a `timeout` (and `max_retries=0`) on the probe's provider — but
note the shape of the decision, because it is not purely a test change:
`openai_compatible` is a *public constructor* and exposes neither knob, so
either the probe stops using it (losing the property the module's docstring
argues for — that the probe embeds through the real adapter rather than
listing models, per B12) or the constructor grows a `timeout` parameter, which
is a change to a shipped signature and wants the argument in
`recurring-defects.md` §2 about not adding a parameter "for flexibility".
`test_live_endpoint.serving()` sidesteps this entirely by probing with a raw
`httpx.post(..., timeout=180.0)` and never touching the adapter; that it is
allowed to and this one is not is the actual asymmetry to resolve.

Related: CLAUDE.md's standing rule that a test hanging is worse than a test
failing, because in CI it reads as infrastructure and gets retried.

---

## 5. Capabilities deliberately not built, with the route back

Nothing here is a defect. Each is a decision to *not* have something, recorded
with what it would cost to change the answer — because the expensive part of
each is the argument, not the code.

### B122. The coverage ratchet measures a moving number, and cannot be lowered

Two findings, and the second is the one that will waste someone's afternoon.

**The measurement moves between runs.** The #39 gate run measured 96.32 and
wrote it as the baseline. Two runs over a tree differing only in a version
string and a CHANGELOG then measured **96.2968** — twice, identically — and
`TOLERANCE` in `scripts/coverage_ratchet.py` is `0.01`, so a commit changing
no code could not pass its own gate. The number moves by roughly one unit of
~7100 (6009 statements + 1120 branches), and a baseline written from the high
end of that spread rejects everything after it.

**The cause was not established.** Candidates in the order worth checking: the
suite runs `-n auto` with `parallel = true`, so worker assignment varies with
`pytest-randomly`'s seed and coverage is combined over a worker set that is
not fixed; two tests skip, and a varying skip condition would move the number;
and a stale `.coverage.*` combining in would inflate rather than deflate,
which fits 96.32 being the outlier. The diagnostic that settles it is cheap
and was **not run**: snapshot `Coverage.get_data()`'s missing-line set,
re-run, diff per file. That names the wobbling lines instead of inferring
them.

**Lowering the baseline by hand does not work, and fails silently.** The
script auto-raises — `if total > baseline + TOLERANCE: write_baseline(total)`
— and `write_baseline` runs `git add`. So editing `.coverage-baseline` down
and committing produces a commit containing the *measured* value, not the
edited one, with the hook reporting `Passed` either way. This was tried while
cutting 0.4.0: 95.00 was written, and 96.30 is what landed. CLAUDE.md and
`.claude/rules/testing.md` both say "to accept a deliberate drop, edit
`.coverage-baseline` in the same commit" — **that instruction cannot be
followed as written** while the auto-raise exists, and both files should say
so once this is resolved.

Widening `TOLERANCE` to cover the measured spread (0.05 would) fixes the
lock-out but not the drop. A deliberate drop needs the auto-raise to be
skippable — an environment variable the script reads, or raising only when
the gain exceeds a margin larger than the noise. Do both, and find the lines
first: a tolerance alone hides the variance, and finding the lines without a
tolerance leaves the next lucky run to write another unreachable baseline.

### B121. Should `BoundaryPreferenceChunker` become the default split?

B120 upstreamed `research-team`'s boundary heuristic as a second `Chunker`
rather than as a replacement, and left `SlidingWindowChunker` the default in
both `index_documents` and `ExtractionPipeline`. That was the conservative
half of the decision, not an argument that the default is right.

The case for switching: the new chunker is better on every axis measured —
it reaches a boundary anywhere in the window rather than in its last 500
characters, it recognises a sentence ending the text or followed by a closing
quote, and it is a lossless partition at zero overlap. The case against, and
the only one: chunk ids are content-addressed over the passage text, so
changing the default re-keys every chunk of every re-ingested document. No
migration is needed — `replace_source` deletes what the new split does not
contain — but a caller storing citations as `(source_id, chunk_id)` finds
them dangling, and nothing tells them.

What has to be decided: whether that lands in a minor release with a note, or
waits for a major. What is already true and worth not re-deriving: the two
chunkers report different `chunker_type`s, so the chunking signature differs
and a re-index is correctly recorded rather than suppressed as a repeat.

`research-team` also holds `Span`/`quote` in `application/corpus_spans.py` —
offsets resolved on demand against retained source text. Deliberately **not**
upstreamed: it depends on the caller holding the document, which is their
composition decision and not something the `Chunker` port implies.

### B116. The carryover bound is recency-only, and evicts the protagonist

`extraction/carryover.py` keeps the most recently *first seen*
`DEFAULT_CARRYOVER_ENTITIES` (32) mentions and drops the rest. That is the
right side to keep for the defect it was built for — an unresolved short form
refers to something named nearby — and it is the wrong side for the entity a
long document is actually about, which is named in full in paragraph one and
by surname for the next eighty chunks.

Deliberately not fixed, and the reason is that the obvious fix is worse.
"Refresh a mention's position each time it recurs" was written, tested, and
rejected: it makes an omnipresent entity permanently most-recent and evicts
exactly the genuinely new entities the bound exists to admit.
`tests/unit/extraction/test_carryover.py::test_a_repeated_mention_does_not_refresh_its_position`
is that decision, pinned — it is the one case where the two implementations
disagree, and every other test in the file passes under both.

What a real fix looks like: a **frequency-weighted** bound, keeping the *k*
most-mentioned alongside the *n* most-recent, which needs a mention count per
key (cheap — the dict is already there) and a split of the budget between the
two halves (not cheap — it is a number nobody has evidence for). Do not pick
that split by intuition; it needs the multi-chunk graded document from **B115**
to be measurable at all, which is why that entry blocks this one.

### B117. The merge keeps one mention's description and discards the rest

`extraction/merging.py` folds duplicate entities with
`max(domain.preference)`, so the winning mention's `description` is the one
that survives and every other mention's is dropped. Overlapping windows mean a
boundary entity is described two or three times, each time from a different
sentence, and the fold keeps one of them.

Microsoft GraphRAG does the opposite: it concatenates every mention's
description and runs an LLM summarisation pass over the accumulation. That is
the shape to copy, and it is **not** a change to `merging.py` — an
LLM call inside the fold would put a model call somewhere this library has
deliberately kept model-free, and would make an order-independent pure
function neither. The route back is a second projection or a consolidation-time
step that reads the descriptions the log already carries, which is where
`docs/adr/0004-consolidation-emits-events.md` says a judgement belongs.

Not urgent: `description` is not read by anything that ranks or resolves
today, so what is lost is text a caller might display. It becomes urgent the
moment a description reaches an embedding or a lexical channel, because at
that point the fold is silently choosing what the corpus can be searched by.

### B92. Corpus statistics are recomputed per query, not maintained incrementally

`lexical_candidates` counts `n_docs`, `avg_doc_length` and per-term document
frequencies at query time — `count(*)`, `avg()`, and a per-term count scoped
to the requested terms — rather than reading from counters kept in step with
writes (`docs/adr/0024-bm25-over-the-chunk-corpus.md`).

Deliberately not built speculatively: maintaining counters correctly across
`upsert_many`, `replace_source` and both delete paths is real code with its
own failure modes (a counter that drifts from the rows it describes is worse
than no counter), and nothing has measured `count(*)`/`avg()` per query as a
cost centre at any scale this repository has exercised. If it becomes one,
the fix is counters updated by the same writes that touch `<table>_terms`,
not a cache invalidated by a schedule.

### B93. The truncation tie-break plan test EXPLAINs a hand-reconstructed proxy query, not the adapter's own SQL

`tests/integration/chunks/test_postgres_store.py::test_lexical_candidates_truncation_tie_break_is_unfalsifiable_by_plan_alone`
proves the `matched` CTE's `, chunk_id ASC` is load-bearing by forcing a
`HashAggregate` (`enable_sort = off`) and EXPLAINing a query built by hand to
resemble `_candidates_sql()`, rather than EXPLAINing the string that method
actually returns. It exists because dropping the tie-break stayed green even
with `enable_indexscan`/`enable_bitmapscan` off — a `GroupAggregate`'s
incidental sort was supplying the order the assertion wanted, which is
exactly the kind of accidental-pass this table's rows warn about.

A hand-reconstructed proxy can drift from the statement it stands for: an
edit to `_candidates_sql()` (an added predicate, a rewritten join) is not
guaranteed to be mirrored into the test's copy, and when the two diverge the
test keeps asserting a plan shape the adapter no longer produces while
reading as green. Minor rather than a correctness hole, because real
candidate correctness is asserted immediately after by a separate,
non-EXPLAIN test — this only weakens the *plan* assertion, not the *result*
one. Fix is to `EXPLAIN` the literal string `_candidates_sql()` returns
(with parameter placeholders bound the same way the adapter binds them)
rather than a restated query.

**That fix is now cheap, and the pattern is already in the same file.**
`test_get_by_entity_uses_the_gin_index` needed the identical guarantee and
solved it with a `_RecordingPool` that intercepts `.fetch()`, so the statement
EXPLAINed is by construction the one the adapter issued — no restatement to
drift. Reuse it here. This was noticed while re-reviewing that test and not
folded in, because doing so changes what a passing integration test proves
and deserves its own commit rather than riding along in a fix round for
unrelated findings.

### B94. Generated Postgres index names can exceed the 63-byte NAMEDATALEN, and truncation is silent

`chunks/adapters/postgres.py`'s DDL builds index names by interpolating the
configured table name — `{table}_terms_term_idx` and its siblings — and the
table name is validated only as a bare identifier up to 62 characters. A
long but legal table name pushes the generated index name past Postgres's
63-byte `NAMEDATALEN`, and Postgres truncates silently rather than erroring,
so two differently-named tables with a shared long prefix could collide on
the same truncated index name.

**Pre-existing, not introduced by this branch** — the naming scheme predates
the chunk-lexical work and is unchanged by it; filed now because Task 6/7
review is what noticed it. Fix, if picked up, is either shortening the
generated suffixes or hashing the table name into the index name so length
is bounded regardless of what the caller configures.

### B76. A relationship says which document stated it, not which sentence

**Half closed.** `Relationship.source_id` exists and `map_extraction` fills
it, so "which document stated this edge" is answerable and the endpoints are
no longer better provenanced than the edge between them. Reported downstream
(research-team); it is the sharper form of an entry this file carried as B20,
deleted in the slice 1 scope cut (`7083e71`) without being fixed. Do not
re-close this one that way either.

**What is left is `source_text`, and it is not the same size of change.**
`Entity.source_text` exists because `ExtractedEntity` asks the model for it.
`ExtractedRelationship` (`extraction/schema.py:84`) has four fields and no
span, so the text is not dropped in mapping — it is never requested. Adding
it means:

- a field on the extraction schema, which changes every prompt's output shape
  and costs tokens on every extraction, for every caller, whether or not they
  want spans;
- an accuracy run to answer the question that decides the whole thing —
  whether the model returns a **quoted** span or a paraphrase. A paraphrase in
  a field named for a quotation is worse than an empty field: it reads as
  evidence and is generation. `tests/accuracy/` is where that gets measured,
  and nothing else can answer it.

If it lands, it is optional like every other field reaching the event log, and
`DocumentExtracted`'s rule 4 shows the shape the compatibility question takes.

A cheaper alternative that was **not** taken and should be weighed first: a
caller who wants the sentence can already store the chunk and index it by
`source_id`. That gets a paragraph rather than a span, and it costs the
library nothing.

### B77. `build_graph` has no progress callback, and bulk ingest is opaque

Reported downstream (research-team), and the older, vaguer form of this was
B16 — deleted in `7ef7a03` along with `models/` rather than closed. Same
caution as B76: the entry went, the gap did not.

`build_graph` (`composition.py:166`) is one `await` that chunks a document,
makes one model call per chunk, merges, projects, and returns a
`GraphBuildReport`. A caller ingesting a corpus sees nothing until the
document is done, and the downstream UI streams token-level deltas
everywhere else, so this is the one blocking box in it.

**The shape is not obvious and that is why this is deferred rather than
done.** Three candidates:

- **A callback** — `on_progress: Callable[[BuildProgress], None] | None`.
  Cheapest, and it has the failure mode a callback always has: it runs inside
  the extraction loop, so a caller that blocks in it blocks extraction, and a
  caller that raises in it kills a run that was otherwise fine. If this is the
  choice, the callback's exceptions must be swallowed and the contract must
  say so — progress reporting must not be able to fail an ingest.
- **An async generator** — `build_graph_streaming(...)` yielding progress and
  finally the report. Composes with an async UI without the reentrancy
  problem, and costs a second entry point on the public surface, which
  `__all__` makes a visible decision (ADR 0006).
- **An `asyncio.Queue` the caller passes in.** Decouples cleanly, and pushes
  lifecycle onto the caller.

Whatever it yields must be *chunk* granularity, not token: this library never
holds the token stream — `LlmProvider.extract` returns a parsed object — so
promising anything finer would be a promise the port cannot keep. Chunks
completed out of chunks total, plus the phase (extracting / merging /
projecting), is what is actually knowable here.

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

### B57. Constrained decoding is built and measured: it changes nothing here

**Both halves are closed.** `extraction/constrained.py` builds the enum-bearing
schema, `build_graph(..., constrain_to_domain=True)` wires it, and
`docs/adr/0030-a-domain-schema-may-constrain-when-asked.md` records the design.
The measurement this entry demanded has been run **twice**, and the second run
retracts the first.

**Current answer, against the thinking-off baseline ADR 0031 established:**

| | entity tp/fp/fn | relationship tp/fp/fn |
|---|---|---|
| unconstrained | 12 / 3 / 0 | 5 / 6 / 1 |
| constrained | 12 / 3 / 0 | 5 / 6 / 1 |

**Identical** -- not close, identical, down to which entity types each document
produced. A model that is not reasoning its way into inventing types already
answers in the schema's vocabulary, so the enum has nothing left to forbid.
The flag costs nothing and buys nothing here. It stays off for that boring
reason, and stays *available* for the unchanged reason: a caller with a coarse
schema and a model that wanders may still want it, and nobody here can measure
that for them.

**What follows is the first measurement, kept because being wrong this way is
the lesson.** It ran with the model thinking, and read a confounder as a
mechanism.

Graded corpus, `qwen3.6-27b-mtp`, `temperature=0.0`, **thinking on**:

| | entity tp | entity fp | entity fn | rel tp | rel fp | rel fn |
|---|---|---|---|---|---|---|
| unconstrained | 12 | **8** | 0 | 5 | **6** | 1 |
| constrained | 12 | **13** | 0 | 5 | **7** | 1 |

Recall is identical and perfect in both arms. Precision is worse constrained.

**The mechanism is the part worth keeping, because it is the opposite of the
intuition.** An enum does not only forbid the types outside it -- it *advertises*
the types inside it, and the model treats the list as a checklist. On
`newsroom-event`, an 81-character sentence about a summit, the unconstrained
run emitted four entity types and the constrained run emitted **all nine the
`news_journalism` schema declares** -- inventing a `claim`, a `date`, a
`quote`, a `source` and a `statistic` the text does not contain. The entire
5-point rise in false positives is that one document.

So the trade is not "coverage for consistency" as ADR 0030 and 0011 both
describe it. It is that, *plus* a hallucination pressure proportional to how
many types the schema declares and how few of them the document contains.
A nine-type schema against a one-sentence document is the worst case, and it
is not a rare one.

**And that mechanism explains nothing, which is the correction.** The false
positives it was invented to account for were the *reasoning trace* inventing
entities; they vanished when thinking was turned off, not when the constraint
was removed. The "checklist" story was persuasive enough to reach an ADR, this
entry and a documentation warning before the confounder surfaced a day later.

**A mechanism inferred from one measurement is a hypothesis, however well the
story fits.** When a result comes with a satisfying explanation, the
explanation is the part to distrust -- it is what stops you looking for the
variable you did not control.

**Two limits of the instrument, which matter more than the result.**

1. **Entity recall is 1.000 on every document in both arms**, so this corpus
   *cannot* detect a recall improvement -- there is no headroom. Anything
   whose benefit is recall (ADR 0029's gleaning, above all) is unmeasurable
   here for a reason entirely separate from B115's single-chunk problem. A
   graded document needs entities a good model plausibly misses.
2. The four false positives on `newsroom-quote` are present in both arms and
   are unexamined. Some are likely grading gaps rather than hallucinations --
   grading rule 3 says omission is a claim, and a 134-character sentence
   naming a plant closure arguably states a `claim` and an `event` nobody
   graded. Before quoting these precision numbers as a baseline, read the
   eight and decide which are the corpus's fault.

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

### B28. `DEEP_MERGE`

**Shrunk further.** `PREFER_MERGED` and `MOST_RECENTLY_OBSERVED` (which this
entry called `LATEST`) are implemented — see
[ADR 0035](docs/adr/0035-provenance-is-a-value-object.md). The reason they
could not be implemented before was never the missing timestamp this entry
named: `resolve` took bare *values*, so any strategy needing more than the
value was unanswerable by construction. It now takes `PropertyClaim`s, each
carrying the `Provenance` of the entity that made it.

All four implemented strategies, including these two, are now reached through
`consolidation/planning.py`'s `plan_properties`, called from
`ConsolidationService.merge` — see
[ADR 0036](docs/adr/0036-a-merge-resolves-the-canonical-entitys-fields.md).

**`DEEP_MERGE` remains deferred for its original reason, which none of that
touches.** Nested-dict semantics for `properties` and `external_ids` are easy
to get subtly wrong, and a wrong deep merge is effectively unrecoverable
because the pre-merge shape is not derivable from the result. It raises
`NotImplementedError` naming this entry rather than falling back to
`PREFER_CANONICAL`, which would write the canonical value while the caller
believed it asked for something else. Implement when a caller needs it, with an
undo path in hand — not before.

### B128. `Relationship` has no `Provenance`, and should not share `Entity`'s

`Entity.provenance` exists; `Relationship` still carries a bare `confidence`
and `source_id` on the type itself. **This asymmetry is deliberate and is not
the thing to fix.** A relationship has no `extraction_method` and no `model` —
nothing asks the model *how* an edge was derived — so forcing it into
`Provenance` would mean either fields that are always `None` on one of the two
users of the type, or a base class earning its keep across two subclasses.
Neither is better than the asymmetry.

What is genuinely missing is `observed_at`. An edge cannot answer "when was
this observed", so the ordering `MOST_RECENTLY_OBSERVED` performs over
properties has no counterpart for relationships, and a future merge that had to
choose between two contradictory edges would be back where `resolve` started.
The cheapest honest shape is probably a second, smaller value object rather
than a shared one — decide that when a caller needs the ordering, and record
which way it went.

Relates to **B76**, which is the other half of the same gap from the other
side: B76 is about *which sentence* stated an edge, this is about *when* the
library was told. Neither subsumes the other, and B76's warning applies here
too — it is the sharper form of an entry that was once deleted rather than
fixed.

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

### B83. The lexical retrieval channel does not consult aliases

`Retriever`'s lexical channel generates candidates with
`GraphStore.find_by_blocking_keys` and scores them against
`Entity.name`, `Entity.normalized_name` and the string values in
`Entity.properties`. **An `Alias` is none of those.** Alias-ness is an edge,
not a field on `Entity` (see
`docs/adr/0002-two-store-ports.md`), so a query matching an alias name
retrieves nothing today even though the store knows the alias exists.

The surface to build on is already there and is exercised:
`GraphStore.find_aliases(canonical_entity_id, tenant_id)` and
`resolve_entity_ids(entity_ids, tenant_id)`, both on the `AliasStore`
capability, both covered by `GraphStoreCompliance`.

**Why it was deferred rather than added.** The blocking side is easy — alias
names could carry blocking keys and enter the candidate set. What is *not*
decided is what a match on an alias should **return**, and the answer is not
obvious:

- Returning the **alias entity** is literally what matched, and it is
  usually not what the caller wants — they searched for a name and want the
  thing it names.
- Returning the **canonical** entity is what they want, and it makes the
  result inconsistent with the query in a way the caller cannot see: the
  returned `Entity.name` is not the string that matched, so a UI highlighting
  the match has nothing to highlight, and two aliases of one canonical
  collapse into a single result whose `lexical` score belongs to neither name.
- Returning **both** doubles some results and needs a rule for ranking a
  canonical against its own alias.

That is `domain.preference`'s territory —
`docs/adr/0010-one-total-order-for-preference.md` settles which mapping of a
thing survives, and this is the same question asked at read time rather than
at merge time. Deciding it inside the retrieval work would have meant either
extending that total order or growing a second one beside it, which is exactly
the "second entity-id scheme gets born" shape the layer contract is arranged
to prevent.

So: settle the return semantics first, ideally as an amendment to 0010,
*then* wire the candidates. Whichever way it goes, `ScoredEntity` probably
needs a field naming the string that actually matched — without it the caller
cannot tell an alias hit from a name hit, and that is the distinction the
whole question turns on.

### B91. `domain/tokenize.py` does no stemming

`tokenize` splits on non-alphanumerics and casefolds, but "running" and "run"
are different terms to it, and that recall cost is real for the BM25 channel
built on top of it (see
`docs/superpowers/plans/2026-08-07-chunk-lexical-channel.md`).

Deferred rather than added because a stemmer is a language model — English-only,
and a new dependency — and two implementations of "the Porter stemmer" differ
at the edges, which is exactly the adapter-divergence shape
`domain/tokenize.py`'s own docstring exists to prevent, reintroduced one level
up. Postgres's `english` text search configuration stems, and using it instead
of this module was rejected for the same reason: the in-memory adapter has no
equivalent, and the two stores would then disagree about what a term is.

If this is picked up, it is a single domain-owned implementation added to
`tokenize.py` (or a sibling module both adapters call), never a per-adapter
one — a Postgres-side stemmer and an in-memory approximation of it is the same
divergence with different code.

---

## 6. Tooling, packaging and hygiene

### B126. The version is one fact with two declaration sites

`pyproject.toml`'s `version` and `src/redstring/__init__.py`'s `__version__`
(line 293 as of 0.7.0) both declare it, and nothing derives one from the
other. `tests/unit/test_version_is_declared_once.py::test_the_two_declaration_sites_agree`
is the only thing keeping them equal — which is `.claude/rules/recurring-defects.md`
§2 with a test bolted on rather than the second site removed.

**It has already fired in anger.** Cutting 0.7.0 bumped `pyproject.toml`
alone, because `RELEASING.md` step 1 named only that file, and the commit
failed the gate. So the test works; the point of this entry is that it is
catching an omission the design permits rather than one a mistake introduced.
Note the failure mode had the release *not* been caught: a wheel whose
metadata says 0.7.0 and whose `redstring.__version__` says 0.6.0, which no
consumer could reconcile and which cannot be replaced once uploaded, since
PyPI filenames are not reusable.

**The fix is `importlib.metadata.version("redstring")`**, making the
distribution metadata the single site and `__version__` a read of it.
Deliberately not done here, for one reason worth checking before someone
does it: `__version__` currently resolves in a source checkout with the
package merely importable, and `importlib.metadata` needs it *installed* —
which it always is under `uv run`, but the editable-install and wheel paths
should both be proven before the literal is deleted. `tests/integration/
test_wheel_contents.py` is where that proof belongs, since a claim about the
artifact can only be falsified by the artifact (see the `py.typed` incident
in `recurring-defects.md`).

Half-fixed in the meantime: `RELEASING.md` step 1 now names both files, so
the instructions no longer walk you into the failure. That is documentation
holding a fact in agreement, which is the weaker half of the answer.

### B87. `PostgresChunkStore` tells callers to install `redstring[pgvector]`

`src/redstring/chunks/adapters/postgres.py::connect` re-raises its guarded
`import asyncpg` with "install `redstring[pgvector]`, the extra that carries
it". That extra is named for a *different* port, so the message reads as a
mistake to anyone who wanted only a chunk store and never a vector one.

Deferred rather than fixed, and the reasoning is the part worth keeping. The
obvious fix -- `uv add --optional postgres asyncpg` -- creates a second extra
whose contents are byte-for-byte the first one's single requirement, which is
`recurring-defects.md` §2: two declaration sites for one fact, with nothing
failing when they drift (a floor bumped on one and not the other is silent,
and every caller of the other extra then gets the old asyncpg). Two extras
naming one dependency also means `all` has to list both or quietly stop
meaning "everything".

What would make it right is renaming the extra to `postgres` and keeping
`pgvector` as an alias for one release -- a packaging change with a
deprecation window, which is more than this adapter should carry. Do it when
something else touches the extras; until then the message is honest about
what to install even though the name is wrong about why.

The same confusion is stated as fact elsewhere: `src/redstring/__init__.py:65`
calls `asyncpg` "a core dependency" in the module's own reference prose. It is
not -- it is what the `pgvector` extra installs, same as `neo4j`. Predates this
branch; fix both when the extras get their proper rename.

### B42. `ANN401` is silenced on `domain/merge_strategy.py::resolve`

One `# noqa: ANN401`, on `resolve`'s return. It was three, on `resolve` and
`_union`, before `resolve` started taking `PropertyClaim`s: the parameters that
carried a bare `Any` are now `Sequence[PropertyClaim]`, and `_union` is
`Sequence[Any] -> list[Any]` — ANN401 flags a bare `Any`, not one nested inside
a generic, so that one needs no suppression at all. Only `resolve`'s return
remains, and it is the one that cannot be narrowed. Silencing is correct here
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

**The ADR half of this is closed.** It used to claim the opposite -- "orphaned
`:BlockingKey` nodes are cleaned up because an orphan matches nothing and
leaving it would be a slow leak" -- describing code that has never existed.
ADR 0003 now carries a section headed "Orphaned `:BlockingKey` nodes are NOT
reaped on upsert", which says so and says the earlier revision was wrong. What
is left below is the leak itself, not a disagreement between the ADR and the
tree.

Still deferred because the size of the leak is unmeasured: nobody
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

**Both halves now have a gate, and the second one is new.**
`mkdocs build --strict` fails on an anchor that resolves nowhere, and caught
two of mine while this was being written.
`tests/unit/test_reference_map_tables_are_honest.py` fails when the map table
names a section the page does not have, or when the two orders disagree --
which is the check whose absence let fourteen rows stand over five sections
for several slices. Proved by adding a row for a section that does not exist
and watching it fail.

Nothing gates the *wrong claim* except running the code while you write the
paragraph, which is the habit to keep.

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

### B68. `replay()` scopes by tenant and aggregate type, not by stream or category

**Closed, upstream.** `eventsource-py` 0.12.0 added
`FeedReadOptions.aggregate_type` (its ADR 0052) and its `replay` pushes both
`tenant_id=` and `aggregate_type=` into the adapter's query. This project
writes two aggregate types — `Document` and `ConsolidationLog` — so a rebuild
of the read models can now ask for the first and skip the second, which is the
half of this entry that was open. The local driver this entry was written
against is deleted; see ADR 0020.

**Category scoping is still open and is a different question.**
`GlobalEventFeed` has no `read_category`; `EventStore` does, and taking it
would mean `replay` accepting a narrower port than the one it documents, or
accepting both and branching. Neither is obviously right, and nobody has asked
for it — a caller wanting one stream can pass `from_position`, a tenant and an
aggregate type. Do not add it speculatively; the reason to wait is that the
branch would be untestable against the in-memory feed without also deciding
what happens when both `tenant_id` and `categories` are given. It is now
upstream's call to make, not this project's, and the argument above is what to
send them if it comes up.

The remaining cost is unchanged and is upstream's too: `tenant_id` is a *read*
filter only. A projection constructed with `tenant_filter` still applies its
own filter after delivery, so a caller who sets both pays for one and gets no
benefit from the other. That is harmless and slightly confusing;
`docs/how-to/drive-projections-from-an-event-store.md` says which to reach for.

### B73. `ReplayReport.failures` is unbounded, and holds live tracebacks

**Closed, upstream, and it is worth reading how.** This entry declined to cap
the list, and said why: a cap that silently truncated would reproduce the exact
defect the field was added to fix — an operator told "3 failed" who cannot
reach the third. It named the two honest shapes instead, a cap that *reports*
what it dropped (`failures_truncated: int`) or a callback that streams
failures rather than accumulating them, and deferred choosing between them
until someone had a replay failing at that scale.

`eventsource-py` 0.12.0 took **both** — `max_failures` with
`failures_truncated`, and `on_failure=` firing for every failure whether
retained or not — which is the right answer to a two-good-options question
when neither costs the other. The local driver is deleted; see ADR 0020.

Recorded rather than deleted because of what it demonstrates. The entry was
written as "here is what is wrong, here is what I learned that made deferring
right, here are the shapes a fix would take", per this file's own rule, and
that is what an upstream implementer could act on. An entry reading "cap the
failures list" would have thrown the expensive part away and got a silent
truncation back.

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

**Update (eventsource 0.12.0).** Range is now `>=0.12.0,<0.13`, and this is
the first bump where the floor moves for the stated reason rather than the
cap dragging it: `redstring.projections` imports `StoreProjection` from
`eventsource.application.projections`, which 0.11.0 does not have, so the
library genuinely needs 0.12.0. See ADR 0020. The floors test's assertion
changed meaning with it — it used to prove *our* forwarding worked at the
floor, and now proves the class exists there and its `ProjectionOptions`
still accepts every option we pass. The general case remains open, and it is
worth noting upstream closed *its* version of this in the same release
(`make floors`, resolving `lowest-direct` into a throwaway environment) and
found eight of eleven declared floors wrong. That is the shape to copy here
when the general case gets picked up: a resolver flag beats a hand-written
test per dependency.

### B71. Confining a *fifth* library — closed by the cheap 80%, and what is left

**Closed as the entry proposed.**
`tests/unit/test_dependencies_stay_confined.py::test_every_third_party_import_is_accounted_for`
walks `src/`, collects every top-level import that is neither stdlib nor
first-party, and fails on any not covered by a `CONFINEMENTS` row or by the
new `ALLOWED_EVERYWHERE` set. Adding an unlisted client is now a red test
naming it, rather than a green suite. Proved by importing `httpx` from
`composition.py` and watching it fail.

`ALLOWED_EVERYWHERE` is the complement of the confinements: dependencies with
no single home because the library is built on them everywhere. It is
deliberately *not* a `Confinement` with `directory="."` — a confinement to the
whole tree confines nothing, and would make that row's two direction-guards
pass vacuously.

**What is still not derived, and why that is the right trade.** The set of
optional distributions in `pyproject.toml` is not checked against the table.
Doing so means mapping distribution names (`langchain-openai`) to import names
(`langchain_openai`), which is only discoverable from installed metadata —
needing the extra installed, the exact condition a `--extra dev` sync silently
breaks, and therefore a check that would fail for environmental reasons in the
one situation it most needs to be believed. The new test needs nothing
installed. It answers "is there an import here that no rule covers", which is
the half that matters; the other half is "is there a declared extra nobody
imports", which is a packaging tidiness question rather than a leak.

### B72. A failed `verify` is unrecoverable, because the tag is spent

`verify` runs after `publish-pypi`, and v0.2.0 showed what happens when it
fails on a *good* release: the artifact is on PyPI, correct and attested, and
the workflow is red. **Re-running it cannot help.** PyPI never permits reusing
a filename, so the publish step fails on a second attempt no matter what, and
the only ways back to green are pushing a new version or leaving a red run
next to a good release.

The retry-and-refresh fix makes the false failure much less likely without
changing that: any *other* failure in `verify` — a genuinely broken wheel, a
dependency that will not resolve, a smoke test that catches something real —
lands in the same unrecoverable place, and those are the cases where you most
want to act rather than shrug.

Three shapes worth weighing, none obviously right:

- **Split `verify` into its own `workflow_dispatch` workflow** taking a
  version, so it is re-runnable against an already-published artifact. Cheap,
  and it makes the check *more* useful — you can run it against any past
  release, which is the only way to notice an artifact that rotted because a
  dependency yanked a version.
- **Keep it in the pipeline but `continue-on-error`,** with a separate
  notification. Turns a blocking red into an advisory one, which is honest
  about what it can do post-publish; the risk is that an advisory failure is
  one nobody reads.
- **Move the smoke test before publish,** against the built wheel from
  `build`. `tests/integration/test_wheel_contents.py` already does much of
  this. It cannot check the thing `verify` exists to check — that the *index*
  serves an installable artifact — so this narrows the gate rather than
  moving it.

The first is probably right, and the reason to write it down rather than do it
now is that it is a change to the release pipeline made immediately after a
release, which is the worst time to touch one.

---

### B79. No workflow job declares `timeout-minutes`, so the ceiling is six hours

Not one job across `ci.yml`, `release.yml` and `docs.yml` sets
`timeout-minutes`, so every one inherits GitHub's 6-hour default. Verified:
`grep -n "timeout-minutes" .github/workflows/*.yml` returns nothing.

This was found because it *failed to fire*. The `integration` job spent ~90
minutes per run waiting out embedding probes against an unreachable address
(B78) for a full day, on green runs as well as red, and nothing capped it —
6 hours is far enough above the real ~35-minute cost that the job would have
had to be an order of magnitude wrong before the default noticed.

The reason to file rather than fix now: **the right number is not obvious and
a wrong one is worse than none.** The `integration` job's honest duration is
still being established (it was ~35 min before `317e7a5` and should return to
roughly that with `-m "integration and not live"` — but that is a prediction,
not a measurement, and the next run is the first data point). A cap set below
the real distribution turns a slow-but-fine run into a red X that reads as a
test failure, which is the same misdiagnosis in the other direction.

So: take two or three runs of the post-fix `integration` job, set the cap at a
generous multiple of the observed p100, and do the same per job rather than
one blanket value — `lint` and `import-linter` finish in under a minute and
want a cap in single digits, where `integration` does not.

Note this interacts with `release.yml`, which calls `ci.yml` via
`workflow_call`: a cap on the reusable workflow's jobs applies to the release
pipeline too, which is the case where an unbounded hang is most expensive
(B72 — a failed release consumes the version number).

### B84. `_resolve`'s round-trip saving is an untested optimisation

`src/redstring/composition/retrieval.py:209`. `_resolve` seeds `resolved`
from the entities the lexical channel already holds, so only the ids it did
not supply are fetched. The docstring makes that an explicit cost claim ("in
one round trip for the unknown", "only the ids it did not supply are
fetched") and no test holds it: replacing that line with `resolved = {}` --
so every `HYBRID` retrieve issues a `get_entities` round trip it does not
need -- leaves the retrieval suite **green**, measured. Correct output,
unverified cost, which is `recurring-defects.md` §3 in its mildest form.

The template is in the same file:
`test_a_lexical_only_mode_makes_no_embedding_call` wraps the provider in a
counting subclass, and the file already builds a duck-typed store wrapper
(`RebuildingGraphStore`) that a `get_entities` counter can copy. Roughly
eight lines. Left out of the I1--I4 fix wave only because the review scored
it Minor and the wave was scoped to the four Important findings.

### B85. `entity_types` means two different things on the two retrieval channels

`retrieve`'s docstring says "`entity_types` restricts both channels" without
qualification. It restricts them differently:

- the **lexical** channel compares `entity.entity_type` from the graph
  (`src/redstring/composition/retrieval.py:181`);
- the **semantic** channel filters on the *vector record's*
  `metadata["entity_type"]`, via `entity_type_of`.

Two consequences, neither documented nor tested. A vector upserted without
`entity_type` metadata has `entity_type_of(...) is None`, so any non-`None`
`entity_types` excludes it from the semantic channel even when the graph
entity carries the wanted type -- and in `HYBRID` the entity still arrives
lexically, so it *looks* like it works while the semantic contribution is
silently missing and the rank changes. And the comparison is case-sensitive
on both sides while `domain/blocking.py`'s `entity_type_key` normalizes, so
`entity_types=["Person"]` matches nothing here while blocking treats
`"Person"` and `"person"` as one type.

Both follow from the ports rather than being bugs in the `Retriever`, which
is why this is a docs-or-semantics decision rather than a fix: either
qualify the docstring (cheapest, and honest), or normalize the comparison and
say in `ports/vector_store.py` what a record missing the key means. Do not
"fix" it by having the semantic channel consult the graph -- that is a second
round trip per query on the path the vector filter exists to avoid.

### B86. Two retrieval tests pass on the adapter's guarantee rather than the `Retriever`'s

`tests/unit/composition/test_retrieval.py`. Both were asked for by the spec
and both are worth keeping; what is worth recording is that neither is
load-bearing as written, so nobody re-derives that later.

- `test_entities_are_compared_by_equality_not_identity` builds a
  `RebuildingGraphStore` returning equal-but-distinct entities, which is the
  right construction -- but there is no `is` comparison on an `Entity`
  anywhere in `Retriever` for it to catch, and it runs in `HYBRID` against an
  *empty* vector store, so only `find_by_blocking_keys` is rebuilt and
  `get_entities`'s rebuild path is never exercised. Pointing it at
  `RetrievalMode.SEMANTIC` (where `_resolve` fetches) would make it mean
  something, and is a one-line change.
- `test_mutating_a_result_cannot_change_what_a_later_retrieve_returns` is
  green because `InMemoryGraphStore` returns deep copies, which
  `GraphStoreCompliance` already enforces. There is no implementation of
  `Retriever` that fails it without also failing the compliance suite.

Neither is a defect to fix under time pressure; the entry exists so the next
reader does not mistake either for evidence about the `Retriever`.

### B95. Nothing executes the code blocks in `docs/how-to/*`

`tests/unit/test_end_to_end_example.py` executes `docs/examples/build_a_graph.py`
and is the mechanism behind the repo's public-surface gate ("the end-to-end
example imports nothing but `redstring`"). Nothing equivalent runs the
fenced Python in `docs/how-to/*.md`. This is how
`docs/how-to/rank-passages.md`'s only example called
`index_documents(..., chunks=chunks, ...)` against a parameter actually named
`store` and shipped anyway — `mkdocs --strict` checks links, not Python, and
the how-to's imports were all in `__all__`, so the public-surface gate gave
it zero protection despite the how-to satisfying every condition that gate
checks for. Fixed for this one instance in the final review pass; the
mechanism gap that let it ship is what this entry tracks. A fix extracts
each how-to's fenced block(s) the way `test_end_to_end_example.py` extracts
`build_a_graph.py`'s, and executes them in the commit gate.

**Widen it past `docs/how-to/`.** A review found the same shape in the three
most-read pages in the repo: `README.md`, `docs/getting-started.md` and
`docs/installation.md` each constructed `LangChainLlmProvider(chat_model)`,
missing the required keyword-only `model=`, so all three raised `TypeError`
on the first line a real-provider user copies. Three sites drifted *together*
— `docs/how-to/consolidate-duplicate-entities.md` had it right — which is the
tell that no mechanism was watching any of them. Fixed in the same commit as
this note; the executor this entry describes has to cover `README.md` and
`docs/*.md`, not just the how-to directory, or it would have caught none of
the three.

### B101. `CandidateSource` and `MergeAdjudicator` have no compliance suite

ADR 0025 declared both protocols and stated two obligations a substitute can
violate without erroring:

- `candidates` returns results best first **under a total order**. The default
  breaks score ties by ascending entity id as a string so two runs over one
  graph agree. A substitute sorting on score alone leaves a cutoff falling
  inside a tie to be decided by whatever order its backend returned, which
  surfaces as an intermittently different merge rather than as a failure.
- `adjudicate` returns **exactly one verdict per candidate, positionally
  aligned**, `None` where it has no answer. A short list silently records an
  answer about one pair against another; `False` in place of `None` turns a
  provider outage into a corpus that appears to hold no duplicates.

Both are prose in a protocol docstring. Every other multi-implementation
contract here is a shared body under `tests/compliance/` subclassed per
adapter, and `.claude/rules/recurring-defects.md` §1 is precisely about what
happens without one — two implementations diverge and nothing fails, because
each one's tests assert its own behaviour.

**Not built now, deliberately, and the reason is the part worth keeping:**
there is exactly one implementation of each protocol. A compliance suite
written against a single implementation gets tuned until that implementation
passes, which is the failure this project has recorded twice (the tier-2
banner in `tests/compliance/vector_store.py` says the same thing about a tier
that has never run against an adapter that could fail it). The suite is worth
writing when the *second* implementation appears, and its two cases are the
two bullets above — a tie forced to occur, and an adjudicator returning a
short list.

`tests/unit/consolidation/test_substitution.py` covers the seam for the
defaults' sake and says in its own docstring what it does not prove.

### B102. `Retriever`'s overfetch default of 3 is reasoned, not measured

`Retriever.__init__` takes `overfetch=3`, multiplying what each channel is
asked for before RRF truncates to `k`. The *direction* is not in doubt: an
entity neither channel returned cannot be promoted by fusion, and RRF
demonstrably ranks a consistent runner-up above two channel-leaders
(`test_rank_fusion_promotes_a_consistent_runner_up` asserts that arithmetic).
Asking each channel for exactly `k`, as the code did, therefore dropped
candidates the fusion rule says should win.

**The number 3 is a guess.** Nothing here measures recall@k against a ground
truth at 1, 2, 3 or 5, because there is no retrieval evaluation corpus --
`tests/accuracy/` grades extraction, not retrieval. So the tests assert the
*request* (`k * overfetch` per channel) and the fusion arithmetic, and neither
can tell you whether 3 buys materially more than 2 or leaves recall on the
table at 5.

What a fix needs: a small graded query set in the shape of
`tests/accuracy/corpus.yaml` -- queries with known-relevant entity ids -- and
a measurement of recall@k across overfetch values, on a corpus large enough
that the channels disagree. Related: **B10k** (no ANN adapter exists, so
nothing here has ever run against a store that can miss a neighbour) and
**B86** (two retrieval tests already pass on the adapter's guarantee rather
than the `Retriever`'s).

Until then the cost is stated in the docstring -- a wider `VectorStore.search`
and a wider blocking-key scan per query -- and `overfetch=1` restores the
previous behaviour exactly.

### B103. The only production adapters live on paths the package calls unsupported

`redstring/__init__.py` states the contract plainly: anything reached through
a dotted path "is internal and may change without notice, including in a patch
release." The adapters a caller actually deploys are all reached that way --
`redstring.llm.adapters.langchain.LangChainLlmProvider`,
`LangChainEmbeddingProvider`, `Neo4jGraphStore`, `PgVectorStore` -- while the
two *exported* providers are `FakeLlmProvider` and `FakeEmbeddingProvider`.

So the README's primary quickstart imports from an explicitly unsupported
path, and the supported surface is the one nobody ships. The reason for not
exporting them is good and should not be reversed: exporting
`LangChainLlmProvider` makes `import redstring` pull LangChain in, and the
extras exist so a caller pays only for the backends they use.

**The obvious fix is blocked by the architecture contract, which is why this
is filed rather than done.** A `redstring.adapters` namespace re-exporting the
extra-gated adapters would import `llm`, `graph` and `vector` -- three
siblings forbidden from importing each other -- so it could only sit on
`composition`. And `pyproject.toml`'s contract says exactly what to do with
such a candidate: "ask what it composes; a candidate that cannot name such a
pair is a piece of one half placed above it for convenience." A re-export
shim composes nothing. CLAUDE.md separately records that `context`, a
re-export shim, was deleted in slice 10.

Three routes, none free:

1. **A module-level `__getattr__` on `redstring`** raising a helpful
   `ImportError` naming the extra, with the names declared under
   `TYPE_CHECKING` so checkers still resolve them. Smallest, adds no module to
   the contract, and keeps `import redstring` lazy. The cost is that
   `__all__` stops being the literal list of what a caller may use, which the
   three public-surface gates are built around -- so those gates need to
   learn about it, and ADR 0006 needs amending rather than merely citing.
2. **A stability promise attached to the existing dotted paths**, stated in
   `__init__.py` and enforced by a test that those four import paths still
   resolve. No new module, no contract change; the promise becomes prose plus
   one gate rather than membership of `__all__`.
3. **Accept it and say so in the README**, which is the status quo made
   honest rather than a fix.

Route 2 is the cheapest thing that removes the contradiction, and route 1 is
the one that gives a caller what they actually want. Either needs an ADR,
because "the public surface is `__all__` and nothing else" is ADR 0006's
decision and both routes qualify it.

### B104. `DomainSchemaRegistry` is a process-global singleton with two caches

`src/redstring/extraction/domains/registry.py` is visibly from a different era
than the code around it -- the docstring style ("This module provides a
singleton registry...", `Attributes:`/`Usage:` blocks) matches nothing else
under `src/`, and `extraction/prompt_generator.py` records that this module's
*sibling* singleton was deleted in slice 10 for the reasons that apply here.

Concretely:

- **Two layers of global state for one object.** A class-level `_instance`
  with double-checked locking, *plus* `@lru_cache(maxsize=1)` on
  `get_domain_registry()`, plus `reset_registry_cache()` existing to clear
  both.
- **Three jobs in one class.** Loading (`load_schemas`), lifecycle
  (`get_instance`, `ensure_loaded`, `hot_reload`), and querying (`get_schema`,
  `list_domains`, `get_schemas_for_entity_type`, `__len__`, `__iter__`,
  `__contains__`).
- **`get_instance(force_new=True)` returns a non-singleton**, from a method
  whose name promises the opposite. It is a testing hatch in production code,
  and `reset_instance()` is a second one.
- **The mutable global is unreachable from the public API.**
  `domain_system_prompt` is the only exported consumer and reads the
  process-wide registry, so a caller with their own schema *directory* cannot
  point it there -- they must load a `DomainSchema` and pass the object form.
  That works, and is not what the argument's docstring implies.
- **`get_schema` raises `KeyError`**, translated to `UnknownDomainError` at
  the boundary. Correct as written, but the error type a caller sees depends
  on which door they came in by.

**Nothing has gone wrong because of it**, which is why it has survived: the
bundled schemas are read-only and loaded once, so the singleton's hazards are
all latent. `list_available_domains()` is now exported and goes through the
same global, which raises the stakes slightly.

The fix is an ordinary object -- `SchemaRegistry(schema_dir)` with `get` and
`list`, no singleton, no `lru_cache`, no `force_new`, no `reset_*` -- and
`domain_system_prompt` taking an optional `registry` defaulting to a
module-level instance over the bundled directory. That removes the global
*and* gives a caller with their own schema directory a supported path, which
is the part with user-visible value. Sized medium-to-large because
`hot_reload` and `get_schemas_for_entity_type` need callers found or deleted
first.

### B100. ADR 0007 cites `redstring.projections.project`, which does not exist

`docs/adr/0007-composition-is-the-only-top-layer.md:86,362,446` name
`redstring.projections.project` as the caller's escape hatch for driving a
projection over their own event feed. There is no such callable:
`src/redstring/projections/__init__.py` exports the three projection classes
and nothing else, and `redstring/__init__.py`'s own docstring records that
`project`/`replay` left this surface in the 0.12.0 upstreaming — a caller
writes `from eventsource import replay`.

The live copies of this claim (`README.md`, `composition/build_graph.py`'s
module docstring) were fixed in the commit that filed this. The ADR was not,
because ADR bodies are immutable records of a decision as taken —
`.claude/rules/definition-of-done.md` item 2. What is deferred is the
*mechanism* question, not a text edit: an ADR whose prose names a symbol that
has since been deleted is indistinguishable from one that is current, and
`mkdocs --strict` checks links rather than identifiers. The two options are
an "Amended by" note on 0007 pointing at the rename, or a gate that greps ADR
bodies for `redstring.`-prefixed dotted paths and fails when one does not
resolve against the installed package. The second would also have caught this
one in the slice that caused it, and would cover every future ADR for free.
Prefer it.

### B96. Nothing asserts `docs/adr/*.md` and `docs/adr/index.md` agree

Every ADR file is supposed to have a matching row in `docs/adr/index.md`'s
table, by convention (`.claude/rules/recurring-defects.md` §6 argues for
exactly this kind of two-declaration-site risk generally). Nothing checks
it: ADR 0024 shipped with no index row for it, `mkdocs.yml`'s nav made the
page reachable so `mkdocs --strict` was silent, and the omission was found
only by a review reading both files side by side. Fixed for 0024 in the
final review pass. A fix is a small test — glob `docs/adr/000*.md` and
`docs/adr/0[1-9]*.md` for ADR numbers, parse the index table's numbers out
of its first column, and assert the two sets are equal — living in
`tests/unit/` next to the other doc-consistency checks (e.g. wherever
`mkdocs --strict`'s invocation lives in the gate, if it does).

### B97. Same chunk id, changed text diverges between the adapters

`chunks/adapters/postgres.py`'s `_TERMS_ON_CONFLICT` is `DO NOTHING` and
`_ON_CONFLICT` deliberately omits `doc_length` from its `SET` clause, both
justified by content addressing: a chunk id fixes its text (via
`chunk_id(source_id, text)`), so a write reusing an existing id is assumed to
be writing the same text, and the term index and length can never need
updating. That argument is correct only for callers who build ids with
`chunk_id`; nothing enforces that they do. `StoredChunk.id` is a
caller-supplied `str`, and `ports/chunk_store.py`'s `upsert_many` promises
unqualified last-write-wins on `(tenant_id, id)`.

A caller using self-assigned (non-content-addressed) ids that re-writes one
id with different text gets, from `InMemoryChunkStore`, ranking over the
*new* text (it tokenizes at query time), and from `PostgresChunkStore`,
ranking over the *old* text and the *old* `doc_length` — the `text` column
updates on conflict while the term rows and `doc_length` do not, because
`_TERMS_ON_CONFLICT`/`_ON_CONFLICT` assume it can't happen. That is
`.claude/rules/recurring-defects.md` §1, silently, and the compliance suite
cannot see it because its `_chunk` helper always builds ids the real
content-addressed way. Cheap fix: state the constraint as prose on
`ChunkStore.upsert_many` in the port ("a chunk id is content-addressed over
`(source_id, text)`; re-using an id for different text is outside the
contract"). Thorough fix: make it executable, either a compliance case that
asserts the adapters agree after such a write (would currently fail on
Postgres) or `chunk_id`-derived validation on `StoredChunk` construction.

**Update:** the cheap half landed — the prose is now on
`ChunkWriter.upsert_many` in `src/redstring/ports/chunk_store.py`, as part of
the chunk-semantic-channel work (see
`docs/adr/0038-the-chunks-vector-lives-on-the-chunk.md`), because the new
`embedding` column inherits the same assumption `doc_length` and the term
index already made. The executable half is still open, and closing it needs
a decision this entry hasn't made: whether the port promises last-write-wins
on *derived* state (`doc_length`, the term index, and now `embedding`) for a
same-id-different-text write, or whether that write is simply outside the
contract and the compliance suite should assert the adapters both leave the
old derived state in place. Those are different contracts, and picking one is
what a test would pin — not something to infer from what's convenient to
implement.

### B98. `PostgresChunkStore.lexical_candidates` is three unsynchronised reads

It acquires one connection and issues the corpus-statistics query, the
document-frequency query, and the candidate query as three separate
statements with no wrapping transaction. Under concurrent writes, the
`n_docs` and `avg_doc_length` returned can describe a corpus that never
coexisted with the returned `doc_frequencies` — a write landing between the
first and third statement changes what "the corpus" means mid-read.
`InMemoryChunkStore` is atomic by construction (no interleaving possible
within one event loop turn), so this is a real adapter divergence, but it is
in something `ports/chunk_store.py` does not pin: the port says nothing
about snapshot consistency across the three parts of `lexical_candidates`'s
answer. Nothing in the suite can observe it (single-threaded tests). Fix is
either a `REPEATABLE READ` transaction around the three statements, or a
sentence in the port stating plainly that the three are not guaranteed to be
a consistent snapshot — so a caller relying on it knows not to.

### B99. `avg_doc_length` is computed by two different arithmetics

`InMemoryChunkStore` computes `sum(lengths) / n` in Python float.
`PostgresChunkStore` computes `avg(doc_length)` in SQL `numeric` and the
adapter rounds the result to float. The compliance suite requires adapter
scores to be **exactly** equal (not `pytest.approx`), and `avg_doc_length`
feeds every BM25 score through the length-normalisation term. For the
fixtures currently in the suite (13/4 = 3.25) both arithmetics are exact, and
in general both are *correctly rounded*, so a disagreement needs a
double-rounding case, which is rare — but the failure mode when it happens
is an intermittently red cross-adapter test with nothing in the source
changed, a shape this project has already been bitten by once (the
`k=0`-sampler note in `CLAUDE.md`'s Testing notes section, about
`InMemoryVectorStore.search`). Fix: `avg(doc_length::float8)` on the
Postgres side makes both adapters do the same float arithmetic instead of
routing one of them through `numeric`.

### B107b. `LlmProvider` and `EmbeddingProvider` have no declared lifetime

ADR 0028 deliberately stopped at the four store-shaped ports.
`tests/unit/test_ports_declare_the_block_form.py::TestOnlyTheStoreShapedPortsAreClaimed`
asserts the two provider ports are *not* `AsyncClosable`, so the exclusion is a
decision rather than an omission -- but it is an open one.

What makes them different, and why copying the decision across would be wrong
rather than merely premature: `LangChainProvider` and the embedding adapters
hold an HTTP client, so `close()` there is not the honest no-op it is on
`InMemoryGraphStore`. It is a real release with a real ownership question --
did the adapter build the client, or was it handed one? -- and that is the
question `RedisCache.owns_client` answers and that B108 had to answer for
`CircuitBreaker` and `RateLimiter`. Grant the pair before answering it and the
adapter ships four methods that look like a lifetime and are not one.

So the order is: decide ownership on the provider adapters, then extend 0028
and delete the exclusion test in the same commit.

### B114. `index_documents` takes a whole `ChunkStore` and drives a `ChunkWriter`

`src/redstring/composition/index_documents.py:114` declares `store: ChunkStore`
and does one thing with it: `ChunkProjection(store)`, which ADR 0026 narrowed
to `ChunkWriter`. So the composition entry point is the wider of the two, and
the narrowing stops one line short of the front door.

It is exempt in `tests/unit/test_collaborators_declare_their_capability.py`
rather than narrowed, because the argument is genuinely two-sided and the gate
landed in a test-only wave that did not own `src/`. For narrowing: the function
uses one capability, and `build_graph`'s `chunks: ChunkStore | None` is in the
same position for the same reason. Against: this is the public surface a caller
hands their corpus to, the docstring says "The corpus. Any `ChunkStore`", and a
caller who then wants to *read* the corpus holds the whole port anyway -- so
narrowing here buys a smaller signature and no removed authority, unlike
`CandidateFinder`, where the point was declining `TenantPurge`.

Decide it deliberately: either narrow both entry points to `ChunkWriter` and
delete the exemption, or record in the exemption's reason that the whole port
is the intended front-door contract. Do not leave it as neither. Note that
narrowing changes a signature in `redstring.__all__`'s closure, so check
`tests/unit/test_public_surface_is_self_contained.py` in the same edit.

### B130. `docs/reference/events.md` documents four events; there are five

`DocumentChunked` (`src/redstring/events/document.py:175`) is in
`KG_EVENT_TYPES`, is registered, and has **no section on the events reference
page** — no payload table, no field notes, no entry anywhere except the
`event_version`/`aggregate_type` table this branch just corrected. Every other
event has a `## <Name>` section running to a few hundred lines.

Found while bumping `DocumentExtracted.event_version` to 2: the version table
said "in full" and listed four rows, and the fifth had to be added to make the
correction true. That the omission survived is the point — the page's
per-event sections are hand-written prose with **no gate tying them to
`KG_EVENT_TYPES`**, which is the same shape the tuple itself exists to prevent
in `tests/unit/events/test_schema.py`. So the fix is two things, and the second
is the one worth having: write the missing section, *and* add a test that every
name in `KG_EVENT_TYPES` appears as a heading in that page. Without it the next
event will be undocumented in the same silent way and nothing will say so.

### B131. `Consolidator.merge` and `Consolidator.resolve` cannot override `merge_policy` per call

`ConsolidationService.merge` takes a per-call `policy:` argument (Task 4 of
`2026-08-13-merged-properties`), but `Consolidator.merge` and
`Consolidator.resolve` in `src/redstring/composition/build_graph.py` do not
forward one -- only `Consolidator.__init__`'s `merge_policy` is wired through,
per that task's brief. A caller using the composed entry point therefore gets
one property-merge policy for the consolidator's whole lifetime, with no way
to widen or narrow it for a single call the way the service itself allows.
Deferred rather than added speculatively: the brief scoped Task 4 to the
`__init__` pass-through, and adding a `policy:` parameter to both `merge` and
`resolve` (and to `ConsolidationReport`, if the resolution should be visible
there too) is its own decision about the composed surface, not a mechanical
extension of this task.

### B132. `StoredChunk` accepts a zero-norm `embedding` at construction; only the store rejects it

`ChunkStore.upsert_many`/`replace_source` now reject a zero-norm `embedding`
at the write seam (`src/redstring/chunks/adapters/memory.py`, mirroring
`InMemoryVectorStore.upsert_many`), and `semantic_candidates` rejects a
zero-norm query vector -- both per `src/redstring/ports/chunk_store.py` and
pinned in `src/redstring/testing/chunk_store.py`. But `StoredChunk`
(`src/redstring/domain/chunk.py`) itself has no such validator, so
`StoredChunk(embedding=[0.0, 0.0, 0.0, 0.0], ...)` constructs cleanly and the
zero vector is only ever caught later, at whichever store the chunk is handed
to -- a caller building one for a test, a queue, or any path that never
reaches a store sees no error at all. `VectorRecord` has the identical
question and the same answer (validated only at `VectorStore.upsert`, not at
construction); this is one open question, not two.

Deferred rather than decided in Task 6: moving the guard onto the domain type
is a wider change than the store contract this task was scoped to -- it
would make `StoredChunk`/`VectorRecord` construction itself capable of
raising over a field whose only present consumer is the store adapters, and
touches both `domain/chunk.py` and `domain/vector.py` with their own
compliance-suite implications (a pydantic `field_validator` needs its own
test, and every existing test constructing a `StoredChunk`/`VectorRecord`
with a real embedding would need auditing for whether it ever passes a
plausible-looking zero by accident, e.g. an uninitialised `[0.0] * dimension`
placeholder). Whether that duplication of the check is worth paying for the
earlier, cheaper failure is a call for whoever owns `domain/`.

### B133. `backfill_lexical_index` reads the whole table into Python, unscoped and unbatched

`PostgresChunkStore.backfill_lexical_index()`
(`src/redstring/chunks/adapters/postgres.py`) runs
`SELECT id, tenant_id, text FROM {table}` with no `WHERE`, no `LIMIT`, and no
paging, then builds the whole `doc_lengths`/`term_rows` JSON payload in
memory before sending it back in one statement. It works for the corpus
sizes the integration suite and any repo-scale deployment exercise today,
but it does two things a real corpus will not tolerate: it re-touches every
row in the table on every call, including rows already correct (there is no
way to backfill only the rows a migration actually left behind, because
nothing marks which rows predate the term index), and it holds the entire
table's `text` in Python at once, which is a straightforward OOM on a corpus
sized in the hundreds of thousands of chunks or more.

Deferred rather than fixed here because scoping it correctly is its own
design question, not a one-line change: tenant-scoping alone helps only a
multi-tenant deployment with many small tenants, and batching needs a cursor
or a keyset-paginated loop plus a decision about whether a batch failure
partway through leaves some rows backfilled and others not (this method is
currently one statement, and the whole point of that shape elsewhere in this
adapter is that a crash mid-write cannot leave the corpus in a state that
never existed -- a batched backfill gives that property up on purpose and
should say so explicitly if it does). Whoever picks this up should decide
batch size, whether it takes an optional `tenant_id` filter, and whether a
partial run is idempotent to resume (it should be, given `ON CONFLICT DO
NOTHING` on the term rows and an unconditional `UPDATE` on `doc_length`, but
that needs a test once batching exists, not an assumption).

### B134. `_SELECT_COLUMNS` builds its `real[]` cast with a substring replace

`_SELECT_COLUMNS = _COLUMNS.replace("embedding", "embedding::real[] AS
embedding")` in `src/redstring/chunks/adapters/postgres.py` works today
because `"embedding"` appears exactly once in `_COLUMNS` and nowhere else in
it. It is not robust to the column list changing: a future column whose name
*contains* `embedding` as a substring (`embedding_model`, say) would also get
silently rewritten to `<name>::real[] AS <name>`, which is either a SQL
error (if the column is not a `vector`) or a quietly wrong cast (if it
happens to be one). `_COLUMNS` and `_SELECT_COLUMNS` are both plain
interpolated strings built once at import time, not composed from a list of
column names each independently markable as "needs a read-side cast", so
there is no structural way for the current shape to express "only this one
column, not any column containing this substring".

Not fixed here because no second column needs a read-side cast yet, and the
correct fix is a shape change -- `_COLUMNS` becoming a sequence of
`(name, select_expression)` pairs, or similar -- that is worth doing once
there is a second case to design it against, rather than speculatively.
Whoever adds the next `vector`-typed or otherwise cast-needing column should
either make that change or, at minimum, add a test asserting `_COLUMNS` does
not contain `"embedding"` as a substring of any other column name.

### B135. `test_semantic_candidates_query_applies_min_score_before_limit` only checks presence, not direction

`tests/unit/chunks/test_postgres_schema.py::test_semantic_candidates_query_applies_min_score_before_limit`
asserts `"$3" in where_clause`, which passes whether the adapter actually
tests `>= $3`, `<= $3`, or even `$3 IS NOT NULL` with no comparison at all --
any wiring of the `min_score` parameter into the `WHERE` clause satisfies it,
including a wrong one. The port's contract (`min_score` is an inclusive
lower bound: a candidate scoring exactly `min_score` survives) is pinned
only by the shared integration case,
`ChunkStoreCompliance.test_semantic_candidates_applies_min_score_before_limit`
in `src/redstring/testing/chunk_store.py`, which does assert the boundary is
inclusive against a real Postgres. The unit test's job was meant to be
catching a regression before the server-requiring suite runs; as written it
cannot.

Not fixed here because tightening it needs either a stronger string
assertion (`"score >= $3" in sql`, or similar, which is brittle to
reformatting the SQL) or executing the statement's `WHERE` clause against a
small fixture with a fake connection, which is more machinery than this
adapter's other unit-level SQL-shape tests use. Whoever picks this up should
decide which tradeoff is worth it before touching the assertion.

### B136. `StoredChunk` now carries a vector on every read, wanted or not

ADR 0038 put `embedding` on `StoredChunk` rather than in a second store, and
every `ChunkReader` method -- `get`, `get_by_source`, `get_by_entity` -- hands
the whole row back, vector included, whether the caller asked
`semantic_candidates` a question or not. A caller reading `get_by_source` over
a large document to display its text, or to feed the lexical channel alone,
now pays to carry one `dimension`-length float vector per chunk across the
port for every chunk it reads, with no way to ask for the row without it.

Not fixed here: a projection that selects columns is a port change --
`ChunkReader`'s methods would need either a second return shape or a
column-selection argument, and either is a real widening of the contract, not
a drive-by fix alongside the capability that created the cost. More to the
point, nothing has measured the width as a cost. `InMemoryChunkStore` pays
nothing extra (Python already holds the object); `PostgresChunkStore` pays a
`real[]` column read on every row, and whether that shows up at any corpus
size this repository has exercised is unmeasured. Whoever picks this up
should measure `get_by_source` over a document with hundreds of chunks before
deciding whether a column-selecting variant is worth the port change.
