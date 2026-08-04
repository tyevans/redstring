# Backlog

Deferred work. Every deficiency found and not fixed on the spot lands here,
with enough detail that picking it up does not require rediscovering it.

Status of the tree as of the last update: **2336 tests pass, 0 fail, 0 skipped**
in the default gate, plus **196 `integration` tests** — 118 against a real Neo4j
(slices 4 and 7), 70 against real pgvector (slice 5), and 8 against a live
`qwen3.6-27b-mtp` (slice 6, `KG_LLM_BASE_URL`). The first two need
`docker-compose.test.yml`. The default-gate count falls because slice 7 deleted
`services/consolidation/` and its 9706 lines of source and tests, replacing
them with 116 tests over `kg_builder/consolidation/`.

Slice 7 rebuilt consolidation on the ports (`kg_builder/consolidation/`),
closing B34, B10b and B40. Slice 5b added the event log, the two aggregates and
the projections, and moved the project onto eventsource-py 0.9.1+ (see B38);
slice 6 rebuilt extraction on the `LlmProvider` port and deleted the
vendor-specific extractors, `kg_builder.inference` and `kg_builder.preprocessing`
(the count falls because ~1400 lines of Redis-mocking transport tests were
replaced by ~300 against a real in-memory `Cache`). The replay-equivalence suite
runs in the default gate, not under `integration`. Note that the two suites
must be run in **separate pytest invocations**; see B10m. Full `pre-commit`
gate green (now
including `mypy`, see B30), nothing skipped at collection. The `integration`
suite is deselected by default (see B10a); a run prints what it deselected and
how to run it. There is no longer an `accuracy` suite — see B12. (Slice 1 of the ring migration deleted
document sourcing -- scraping, storage, document parsing, and HTML
preprocessors -- which accounts for the earlier drop in count.)

Ordering within a section is roughly by priority. Ordering between sections
is not meaningful.

---

## 1. Unlanded features

### B47. Three timeline modules were deleted, not ported — slice 8

Slice 8 deleted ~1700 lines rather than porting them. All three are recoverable
from `d49f56b`, which is the last commit that had them:

| Module | Ref |
|---|---|
| `project_timeline_query.py` (677 lines) | `d49f56b:src/kg_builder/services/project_timeline_query.py` |
| `timeline_export.py` (630 lines) | `d49f56b:src/kg_builder/services/timeline_export.py` |
| `timeline_cache.py` (428 lines) | `d49f56b:src/kg_builder/services/timeline_cache.py` |
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
  fake Redis, which is the shape B41 already records as worthless.

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
  `d49f56b:src/kg_builder/services/temporal_parser.py::_calculate_confidence`.
- **Named eras.** The old parser dated "medieval period" to 500-1500 CE,
  "renaissance" to 1400-1600 and "ancient" to 1-500. Those are claims about
  historiography, not about the text, and the patterns were unanchored, so any
  passing use of the word "renaissance" became a dated event spanning two
  centuries. Century patterns are kept because "19th century" does name a span
  the text is asserting.

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
exchange rate. Measure on a real corpus first. The hypothesis suite in
`tests/unit/domain/test_temporal_parsing.py` sets `deadline=None` on the two
text-generating properties for this reason; if that is ever tightened, this is
what will trip it.

### B51. `test_delay_between_retries` asserts on wall-clock time under xdist

`tests/unit/llm/test_retry.py::TestRetryTiming::test_delay_between_retries`
asserts `0.15 <= second_delay <= 0.25` for a 0.2s backoff. It failed once
during slice 8 with `second_delay == 0.298` -- not a regression in the retry
policy, which slept the amount it was asked to, but `pytest-xdist` scheduling
the coroutine's resumption late on a loaded machine.

An upper bound on how long `asyncio.sleep` takes to return is not a property of
this code and cannot be made one; the machine can always be busier. What the
test is really for is that the delay *grows*, so the fix is to assert the shape
(`second_delay > first_delay`, and each at least its nominal value) and drop
the ceilings. Left alone here because slice 8 touched nothing in `llm/` and a
green-run-dependent edit is how a flake becomes two flakes.

Note the failure mode this has in common with B45: a timing assertion that
fails intermittently in CI reads as infrastructure trouble and gets retried
rather than investigated.

### B52. The model is no longer asked to normalise dates itself

The deleted `TemporalEventProperties` (still present on the legacy
`extraction/schemas.py`, which slice 9 owns) asked the model for six temporal
fields: `temporal_expression`, `event_date`, `end_date`, `is_approximate`,
`temporal_qualifier` and `sequence_position`. The new `ExtractedEntity` in
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

### B48. Temporal query is a full tenant scan

`temporal/query.py::TemporalQuery.entities_in_interval` pages
`GraphStore.find_entities` over the whole tenant and applies the interval
predicate in Python. It composes rather than adding a port method, deliberately
— see that module's docstring for the argument — but the cost is linear in the
tenant's entity count regardless of how few entities are dated.

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

### B53. The Neo4j adapter does not store or index `Entity.temporal`

Extraction now populates `Entity.temporal`, and `temporal/query.py` reads it
back off entities that `GraphStore.find_entities` returned. That works against
`InMemoryGraphStore`, which round-trips the whole domain object. It has **not**
been verified against `Neo4jGraphStore`: if the adapter drops the field on
write, every temporal query answers `[]` against Neo4j and the same code
answers correctly in every unit test, because the unit tests use the memory
adapter and the integration suite is deselected by default (B10a).

Slice 8 did not add it because the field's storage shape is a real decision
that interacts with B48: flattening `TemporalExtent` into node properties makes
the indexed range prefilter B48 wants possible, while storing it as a JSON blob
makes it impossible. Making that choice here, without the query cost measured,
would fix the harder decision in passing.

**Check this first when temporal work resumes**, and note that the compliance
suite is where the gap should be closed -- a round-trip assertion on `temporal`
in the shared suite covers every adapter at once, which is the point of it
having one.

### B54. 793 of `temporal_parsing.py`'s 850 mutants were never run

Slice 8 ran cosmic-ray over `domain/interval.py` (217, all classified),
`temporal/inference.py` (95, all classified) and **the precision logic only**
of `domain/temporal_parsing.py` (57 of 850). The remaining 793 cover the
uncertainty patterns, the marker stripping, the range and period regexes, the
ambiguity probe and `render_temporal`.

They were not run because each mutant costs ~70 seconds: it re-runs the whole
80-test file, which includes two hypothesis properties at 300 examples and a
`dateparser` import. 850 x 70s is about seventeen hours. The 57 that were run
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

### B43. A merge plans against a graph read outside its concurrency window

`consolidation/service.py::merge` reads `get_relationships_for` *before*
loading the aggregate, so the edge set it plans against can be stale by the
time the append happens. Deliberate: the read model is a projection and lags
the log by construction, so no ordering of the two steps makes the graph
authoritative, and doing the read inside the aggregate's window would widen
the window without making it correct.

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

### B45. No wrapper enforces a green baseline before a mutation run

**The rule is written down; the enforcement is not.** CLAUDE.md's "A
zero-survivor run is the result most in need of suspicion" carries the
reasoning and the habit -- run the configured `test-command` unmutated in the
same environment and require it green before reading any result. What is left
here is only the automation.

The incident, kept because it is the argument for automating it: slice 7's
first cosmic-ray run reported **0 survivors out of 426**, and a planner-only
run before it reported 0 out of 45. Both were worthless -- the worktree had
been synced with `uv sync --extra dev`, `jellyfish` was absent, and every
mutant "died" on a collection error. `cr-report` showed `WorkerOutcome.NORMAL,
TestOutcome.KILLED` for all 426, which is exactly what a perfect suite looks
like. The real run, once the environment was fixed, had 136 survivors from 28
source lines, four of them genuine defects.

A habit that has already been forgotten once is a habit, not a control.
Wanted: a `scripts/mutation.py` that does the baseline, the init, the exec and
the report, and **refuses to start if the baseline is red**. Two things to
decide first, which is why it is not done:

- Whether mutmut gets the same wrapper. Both tools are kept deliberately (see
  the section above), and two half-wrappers would be worse than none.
- Where the baseline runs. cosmic-ray's `local` distributor mutates the
  working tree, so the run belongs in a worktree or clone -- and a worktree is
  exactly where a missing extra goes unnoticed, so the baseline must run
  *there*, not in the main tree where it would pass.

### B46. Two tests still describe SQL blocking, which nothing does any more

`tests/unit/test_blocking_indexes.py` checks that a migration declares
`pg_trgm` and a set of blocking indexes; `tests/unit/test_soundex_column.py`
checks jellyfish's soundex "as a reference for the PostgreSQL soundex
function". Both describe the *relational* blocking implementation that slice 7
replaced: blocking keys are now computed in `domain/blocking.py` and looked up
through `GraphStore`, trigram matching went to `VectorStore.search`, and
nothing in `src/` reads either the extension or the column.

**Not deleted here because the migration and the ORM columns they describe are
still present**, and removing those is slice 9's. Deleting the tests first
would leave the migration unexercised in the window between; deleting both is
one change, in the slice that owns it.

Two details worth keeping for whoever does:

- `test_soundex_column.py` guards on `try: import jellyfish` with a `skipif`.
  That guard is dead as of slice 7 -- jellyfish is a required dependency now,
  not the `nlp` extra -- so the skip can never fire.
- Its assertions are about jellyfish's behaviour rather than this project's,
  and `tests/unit/domain/test_blocking.py` now covers the parts that matter,
  including the cases where jellyfish's own output is unusable
  (`soundex("2024") == "2000"`).

### B41. `RedisCache` has no test against a real Redis

`llm/cache/redis.py` is the only `Cache` adapter with no run of
`tests/compliance/cache.py` behind it. `MemoryCache` passes the suite in the
default gate; `RedisCache` passes nothing.

**Why this is riskier than it looks.** The compliance suite exists precisely
because the two adapters' natural implementations disagree, and three of its
cases were written against divergences this adapter *could* have:
`get` returning `bytes` rather than `str` (a client left at its default does),
`ZCOUNT` being inclusive at the boundary where a `>` comparison is not, and two
hits at one instant collapsing into one sorted-set member. Each was reasoned
about and coded for, and none is *verified*.

**What to do.** Add `tests/integration/llm/test_redis_cache.py` subclassing
`CacheCompliance`, with a `cache` fixture pointing at the `redis` service in
`docker-compose.test.yml` and a skip probe that round-trips a key — not a TCP
connect, for the reason `tests/integration/vector/test_pgvector_store.py`
spells out. Give each xdist worker its own key prefix; B10f is the same hazard
one layer down.

**Why deferring is safe rather than merely convenient.** Nothing in the library
constructs a `RedisCache`: both `RateLimiter` and `CircuitBreaker` default to
`MemoryCache`, so a caller reaches it only by importing it and passing it
explicitly. The single-process path — which is every current caller — is fully
covered.

### B42. `extraction/schema.py` and `extraction/schemas.py` both exist

One character apart, and they are unrelated.

- `schema.py` (new, slice 6) is what a model is *asked* for: `Extraction`,
  `ExtractedEntity`, `ExtractedRelationship`. Its field descriptions are prompt,
  not documentation — they reach the model as part of the JSON schema.
- `schemas.py` (old) is the ORM-shaped `ExtractedEntitySchema` /
  `ExtractionResult` family, kept alive only by
  `services/extraction/temporal_enrichment.py` (which imports
  `ExtractedEntitySchema` and `TemporalEventProperties` at module scope) and by
  `extraction/strategy_router.py`.

An import of the wrong one type-checks in plenty of places, because both hold
a class with "entity" in its name and a `properties` dict.

**Why not renamed now.** Renaming the new one buries the good name under the
dead one. Renaming the old one touches a module slice 8 is about to rewrite for
temporal, and every rename is a merge conflict against that work. It resolves
itself when `schemas.py` dies with the relational layer in slice 9 — the fix is
to *delete*, not to rename, and doing it early costs a conflict for a few weeks
of ambiguity.

If it bites before then, rename the old one to `orm_schemas.py`: it has the
smaller import surface (two modules) and is the one leaving.

### B39. The legacy orchestrator lost its chunk/extract/merge branch

`services/extraction/orchestrator.py:179` used to route through
`PreprocessingPipeline` when `settings.PREPROCESSING_ENABLED` was set. Slice 6
deleted `preprocessing/pipeline.py` and `preprocessing/factory.py`, so that
branch called nothing but deleted code and went with them; the orchestrator now
always takes the legacy single-call path.

**Why deleting was right rather than porting.** The orchestrator is
slice-9 legacy with **zero tests** — it is not in `tests/unit/services/`
at all — and every one of its three extraction paths (`_extract_with_provider`,
the pipeline branch, `_extract_with_llm_legacy`) reaches a module this slice
deletes. Rewriting a branch of an untested, doomed class onto the new pipeline
would have produced code with no test to prove it works and a deletion date
three slices out.

The replacement is `extraction/pipeline.py`, which does chunk/extract/merge on
domain types and emits `DocumentExtracted`. Nothing calls it from the legacy
service layer on purpose: wiring it in is slice 10's public-API work, and doing
it here would put an event-emitting pipeline behind a class that writes to the
ORM.

`settings.PREPROCESSING_ENABLED` (`config.py:239`) is now read by nothing. Left
in place rather than removed, because `config.py` is a single `Settings` object
shared by the legacy services and pruning it one key at a time invites a merge
conflict per slice; it goes wholesale in slice 9.

### B3. `mark_sync_failed` does not persist anything

`services/sync_status.py:252` — the method only logs. Its own comment lists
what it should do: store the error, increment a retry counter, set
`next_retry_at`, emit a failure event. `retry_failed_syncs` therefore cannot
distinguish "never synced" from "failed repeatedly".

### B5. Timeline events do not populate involved entities

`services/timeline_query.py:640` — `involved_entities=[]` with a TODO to
populate from relationships.

---

## 2. Architecture and library shape

### B6. Auth vestiges from knowledge-mapper

This is a library with no auth, still carrying an application's auth surface.
Slice 1 of the ring migration plan.

- `config.py` declares `OAUTH_ISSUER_URL`, `OAUTH_CLIENT_ID`,
  `OAUTH_CLIENT_SECRET`, `OAUTH_REDIRECT_URI`, `OAUTH_SCOPES`,
  `OAUTH_USE_PKCE`, and a full `APP_JWT_*` block (private/public keys,
  algorithm, issuer, expiry, key id).
- `models/user.py`, `models/user_tenant_membership.py`, and
  `models/oauth_provider.py` exist only because the knowledge-graph models
  declare SQLAlchemy relationships to them and the mapper registry will not
  resolve without them.
- Decision already taken: strip these, replace the relationships with plain
  `tenant_id` scoping columns. `tenant_id` stays as a scoping key, not auth.

### B7. `db.py` is FastAPI-shaped, not library-shaped

`init_db()` and `close_db()` embed example FastAPI `startup_event` /
`shutdown_event` handlers; the module assumes an application lifecycle a
library does not own. Needs reshaping into a session provider the caller
drives.

### B8. Cross-context coupling that the ring migration must break

Recorded here so they are not "discovered" again mid-migration:

- `services/consolidation` reaches directly into the embedding services.
- `services/extraction/orchestrator.py` pulls `preprocessing` directly.

### B9. Import-linter contract is not exhaustive

`pyproject.toml` sets `exhaustive = false`, so a new top-level package is
silently unconstrained. Revisit when the per-context contract lands.

### B24. No schema migration tooling — added columns have no migration path

**This is the most consequential open item.** There is no `alembic/`, no
`migrations/`, and Alembic is not even a dependency; the migrations were left
behind in knowledge-mapper. Meanwhile the ORM has grown columns that no
database will have:

- `ScrapingJob.enable_timeline_extraction` (slice 0)
- `ExtractedEntity.start_date`, `end_date`, `date_precision`,
  `uncertainty_marker`, `original_temporal_text`, `sequence_position`,
  `publication_date` (slice 0b)

Nothing catches this, because the test suite has no database at all (B10).
The models and any real Postgres are now out of sync with no mechanism to
reconcile them. Either adopt Alembic here or document that schema ownership
stays with the consuming application — but decide, because "the ORM says so"
is currently the only record that these columns exist.

### B27. `child_of` relationship normalization is ambiguous

`extraction/schemas.py::normalize_relationship_type` had `"child_of"` as a
duplicate dict key — mapped to `"part_of"` alongside `belongs_to`/`member_of`,
then again to `"related_to"` alongside `sibling_of`/`parent_of`. The second
won, so `child_of` has always normalized to `related_to` and the first entry
was dead code.

The dead entry was removed to keep behaviour identical, because no test pins
it and both groupings are semantically defensible. Someone who knows the
intended taxonomy should decide whether `child_of` is a containment
relationship (`part_of`) or a generic association (`related_to`) — and then
add the test that was missing.

### B26. `DatePrecision` / `UncertaintyMarker` live in the ORM layer

They are defined in `models/extracted_entity.py` and re-exported from
`schemas/timeline.py`, because `models` sits below `schemas` in the
import-linter contract and needs them for its temporal columns.

That is correct for the current layering but not their real home. As of
slice 2, `kg_builder.domain.temporal` now also has copies of both enums —
required so the new `TemporalExtent` value object doesn't depend on the ORM
layer. The `models/extracted_entity.py` / `schemas/timeline.py` originals are
intentionally left in place until slice 9 deletes the relational layer;
until then the definitions exist in two places. Delete the originals and
re-point any remaining internal references to `kg_builder.domain.temporal`
in slice 9.

---

## 3. Test suite

### B10. No database anywhere in the test suite

**Partially addressed in slice 3.** `InMemoryGraphStore`
(`src/kg_builder/graph/adapters/memory.py`) is a real, contract-enforcing
`GraphStore` backend, and `tests/compliance/graph_store.py` is the shared
suite every adapter must pass — so graph storage is now genuinely exercised
rather than only constructed.

**Further addressed in slice 4.** `Neo4jGraphStore`
(`src/kg_builder/graph/adapters/neo4j.py`) passes the identical compliance
suite against a real Neo4j from `docker-compose.test.yml`, so the port is now
demonstrably implementable against a graph database and not merely against a
dictionary. `tests/integration/` exists and the `integration` marker is used.
What remains uncovered:

- **The vector store.** No `VectorStore` port, no in-memory adapter, no
  compliance suite. That is slice 5, and it is the larger half of this item.
- Everything in the original list below still stands for the SQL paths.

There is no sqlite, no `create_async_engine`, no `sessionmaker`, and no
integration fixture. Consequences:

- Nothing exercises the SQL in `vector_ops`, `blocking`, `merge_service`,
  `timeline_query`, `project_timeline_query`, or `sync_status` — the queries
  are only ever constructed, never run.
- Column `default=` values cannot be observed, because SQLAlchemy applies
  them at INSERT. Two tests were rewritten to assert the *declared* default
  instead (see B17).
- The `integration` marker is declared in `pyproject.toml` but no test uses
  it, and `tests/integration/` does not exist. **Both done in slice 4.**

### B10a. The Cypher-executing half of the Neo4j adapter is not in the gate

**How this was found, because it is the important part.** A cosmic-ray run was
interrupted and left a mutant in `graph/adapters/neo4j.py`:

```
-    if limit is not None and limit < 0:
+    if not limit is not None and limit < 0:
```

The full suite passed with it applied — 2026 tests green, gate clean. The
adapter's 106 tests are all `integration`-marked and deselected by `addopts`,
so **not one line of that module executed in the default run.** Corrupt source
in an integration-only module was invisible.

Two things were done about it, and one was not.

Done: `tests/unit/graph/test_neo4j_adapter_is_wired.py` now runs every part of
the adapter that needs no server — argument validation (against a driver that
raises if touched, so it also proves no I/O happens before the guard), the
pure encode/decode functions, signature conformance against the port, and a
check that Cypher has not leaked out of the adapter. That mutant is now killed
by the default gate. The module is **not** in `[tool.coverage.run] omit`, so
the ratchet measures the remainder honestly rather than hiding it: the adapter
reads **60%** in the default run, and the 47 uncovered lines are precisely the
query bodies. The baseline was lowered 68.07 → 67.96 to accept that, which is
the number to watch — when the combined run below lands, it should go back up
rather than the omission coming back.

Also done: `tests/conftest.py` prints what a run deselected and how to run it,
so `pytest` ends with `106 'integration' tests -- uv run pytest -m integration`
instead of a bare `114 deselected`.

**Not done, and this is the entry:** the queries themselves, the schema DDL,
tenant isolation, traversal and the query-plan assertions still only run with
Docker up. What is needed is a second coverage run over `-m integration`
combined with the default run's data (`coverage combine`; `parallel = true` is
already set, so the files already accumulate). Deferred because making the
commit hook conditional on Docker turns a deterministic gate into a flaky one
— the right shape is a separate CI target that starts the compose file, runs
both suites, and combines, not a change to the hook. Slice 5 hits this again
with pgvector, so solve it once, there.

### B10e. The Neo4j adapter's mutation coverage is unestablished

A cosmic-ray run over `src/kg_builder/graph/adapters/neo4j.py` completed **16
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
2. **Each mutant runs the whole 106-test integration suite against a live
   Neo4j**, about 16 s, so a full run is 1.5–2 hours and needs the container
   up throughout. `KG_COMPLIANCE_MAX_EXAMPLES` is already the lever; a
   narrower per-mutant command (the compliance suite only, not the adapter
   specifics) would cut it further without losing killing power.

The session config used is worth recreating rather than rediscovering:
`module-path` pointed at the single file, and `test-command` was
`env KG_COMPLIANCE_MAX_EXAMPLES=5 ./.venv/bin/pytest -x -q --no-header -p no:randomly -m integration tests/integration`
(the `-m integration` is required — `addopts` deselects it otherwise, and the
run then silently mutates code no test executes, which is how B10a happened).

### B10f. The integration suite cannot run under xdist

`tests/integration/graph/test_neo4j_store.py::_wipe` runs
`MATCH (n) DETACH DELETE n` on the one shared Neo4j database before every
test. Under `pytest-xdist` each worker does that to the others' data
mid-test, which produced **36 failures that say nothing about the code**.
Measured, not predicted.

**The constraint today: run `-m integration` serially. No `-n auto`.** The
default gate is unaffected — `addopts` deselects `integration`, so the xdist
run the hook does never reaches these tests.

**This is a direct trap for two pieces of scheduled work**, which is why it is
filed rather than merely known:

- **B10a** proposes a combined coverage run over the unit and integration
  suites. `parallel = true` is already set for coverage, and the obvious way
  to write that CI target is `pytest -n auto` over both. That target will fail
  36 times for a reason that looks like flakiness.
- **Slice 5's pgvector suite** would hit the identical problem the moment it
  truncated a shared table between tests, which is the natural way to write
  it. It does not: `tests/integration/vector/test_pgvector_store.py` puts
  `PYTEST_XDIST_WORKER` into the table name, so each worker truncates only its
  own rows and the module is parallel-safe. That is the third fix below, one
  level cheaper — a table per worker rather than a database per worker — and
  it is available in Postgres precisely because it allows as many tables as
  you like, which Neo4j community does not do for databases.

The real fixes, in increasing order of cost: give each test its own tenant and
scope the wipe to it (weakens `test_delete_by_tenant_removes_exactly_that_tenant`,
which is why slice 4 did not); give each xdist worker its own database
(`PYTEST_XDIST_WORKER` into the database name — Neo4j community allows one
database, so this needs Enterprise or a container per worker); or mark the
module `xdist_group` so one worker owns it, which keeps the other suites
parallel and is probably the right answer.

### B10g. `upsert_relationships` is atomic in Neo4j, where the port permits partial writes

`kg_builder/ports/graph_store.py:142` says a `MissingEntityError` part-way
through **leaves earlier elements written**. `graph/adapters/memory.py`
behaves that way. `graph/adapters/neo4j.py::upsert_relationships` validates
every endpoint in one query before writing anything, so on a dangling edge it
writes **nothing** — strictly stronger than the contract.

Nothing pins either behaviour: the compliance suite asserts that the error is
raised, never what survived it, so the two adapters differ on an axis no test
covers. That is exactly the kind of difference that gets depended on by
accident.

**What a caller may rely on:** that `MissingEntityError` was raised and that
the batch is not *fully* written. **What a caller may not rely on:** which
of the earlier elements landed. Code that retries the whole batch after the
error is correct against both adapters; code that skips the prefix it assumes
was written is correct against neither.

Resolving it means choosing: pin the weak contract in the compliance suite
(and accept that Neo4j is gratuitously stronger), or strengthen the port to
all-or-nothing (and make the in-memory adapter validate up front, which is a
few lines). The second is the better contract for replay, and it is cheap
because the adapter that would find it hard already does it.

### B10h. `KG_COMPLIANCE_MAX_EXAMPLES` cannot be tuned per adapter subclass

`tests/compliance/graph_store.py:87` reads `DEFAULT_MAX_EXAMPLES` from the
environment at **module import**, and line 93 bakes it into the shared
`settings()`. By the time a subclass body executes, the value is fixed — so
"tune `max_examples` down for the slow adapter" is not achievable as written.
It is tunable per *run* (`KG_COMPLIANCE_MAX_EXAMPLES=10 uv run pytest ...`)
and not per class.

An explicit `settings(max_examples=...)` on the subclass is deliberately ruled
out by the suite's own comment at line 82: it outranks `--hypothesis-profile`,
which would make the profile inert for **every** adapter, not just that one.
That reasoning is right and should not be reversed casually.

Slice 4 measured the cost and it was nothing — 25 s / 43 s / 66 s at 10 / 25 /
50 examples over 106 tests — so this was correctly not fixed then. **Slice 5
meets the same wall**, and a pgvector adapter with a per-example schema reset
may not get off as lightly.

What would have to change: make the per-class value a hypothesis *profile*
rather than a `settings()` argument — register one profile per adapter at
import and have each subclass select it — or give the suite a class-level hook
(e.g. a `max_examples` class attribute the shared decorator reads through a
`settings` callable) that still leaves `--hypothesis-profile` outranking it.
Either is a change to slice 3's suite, which is why slice 4 did not make it
unilaterally.

### B10i. The `EXPLAIN` tests run against an empty database and do not pin the negative

`tests/integration/graph/test_neo4j_store.py:205`
(`test_tenant_scoped_reads_seek_rather_than_scan_the_label`). The claim it
encodes was measured at 5000 entities across 100 tenants; the `store` fixture
wipes first, so the planner sees zero nodes.

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

### B10j. `_schema_ready` is module-level mutable test state

`tests/integration/graph/test_neo4j_store.py:101`. **Harmless today and
deliberately left alone**: `ensure_schema` is idempotent, `_wipe` does not
drop indexes, so a stale `True` cannot cause a missing index within a process,
and each xdist worker would have its own copy anyway (see B10f — that suite
does not run under xdist regardless).

It is recorded only because it is the same shape that produced **B10d**:
module-level mutable state in a test file, correct until collection order or
process reuse changes underneath it. If B10f is resolved by giving each worker
its own database, revisit this at the same time — that is the change that
would make the cached flag mean something different per worker.

### B10k. The pgvector adapter has no ANN index, so search is linear in a tenant

`src/kg_builder/vector/adapters/pgvector.py::_schema_statements`. There is
deliberately no `hnsw` or `ivfflat` index on `embedding`, and
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

### B10l. A vector with non-zero components can still have a zero norm

`src/kg_builder/domain/vector.py::is_zero_vector` asks whether every component
is zero. `cosine_score` needs the **norm** to be non-zero, and those are not
the same question: `[1e-200, 1e-200]` has non-zero components and a squared
norm that underflows to `0.0`, so the port's guard accepts it on `upsert` and
`search` then raises `ValueError` from a code path documented as unreachable.
`tests/unit/domain/test_vector.py::test_a_vector_whose_norm_underflows_is_treated_as_zero`
pins the current behaviour.

The band is far narrower in float32, where pgvector stores: components below
about `1e-19` square to zero there, and `<=>` against such a row yields NaN,
which sorts unpredictably and would then fail `VectorMatch`'s `0..1` bound.
**So the two adapters fail differently on the same input** — the in-memory one
raises at search time, pgvector produces a NaN score — which is the kind of
divergence the shared suite exists to prevent, and the suite does not catch it
because `tests/compliance/strategies.py::vector_components` excludes
subnormals to avoid intermittent failures in unrelated properties.

**Deferred rather than fixed because the fix requires a decision, not a
check.** Guarding on the norm instead of the components is two lines; deciding
*what threshold* is another matter, because "an embedding of magnitude 1e-160"
is not a thing any real model produces and picking a number without a caller
to serve invents a contract. The honest resolution is probably to reject on
the float32 norm being zero (which is what any adapter would hit) and say so
in the port. Nothing depends on this today: real embeddings are unit-norm or
close to it.

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

### B10n. `cosine_score`'s upper clamp is not reachable from float64 input

`src/kg_builder/domain/vector.py::cosine_score` ends with
`min(1.0, max(0.0, ...))`. A cosmic-ray mutant changing `min(1.0, …)` to
`min(2.0, …)` **survived**, and the survivor is understood rather than
equivalent: the clamp is genuinely unenforced by any test.

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
`<=>` internally. So both of its mutants (`min(1.0, …)` → `min(2.0, …)` and
`max(0.0, …)` → `max(-1.0, …)`) survive for the same reason, and the clamp is
dead code against **pgvector 0.8.5 specifically**.

Both clamps are kept, and this is the argument to preserve. The guarantee is
needed at precisions and backends this repo does not yet have: any store that
reports a raw cosine, or computes the mapping itself without clamping, hands
`VectorMatch` a value its `le=1` bound rejects outright — turning a
one-ulp rounding artefact into a hard `ValidationError` for the caller. A
Qdrant adapter is the next candidate.

Resolving this means either constructing an input that reaches a clamp — which
may not exist in float64 or in pgvector, in which case the honest answer is a
comment recording the measurement — or moving the clamp into a single shared
helper both call, so one test covers both. Do **not** resolve it by deleting a
clamp.

### B10o. `min_score` evaluates the distance operator twice per row, and no plan test covers it

`src/kg_builder/vector/adapters/pgvector.py::_search_sql` inlines `_SCORE`
in both the `SELECT` list and the `min_score` predicate, because SQL cannot
reference a select alias from `WHERE`:

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

### B10c. `neighbors` at a large `depth` is unbounded work

`src/kg_builder/graph/adapters/neo4j.py` — traversal is one
`-[rels:RELATES_TO*1..N]-` pattern. Cypher's relationship-uniqueness rule
terminates cycles, so the *result* is always correct and finite, but the
number of paths explored can grow exponentially with `N` in a dense graph
even though the number of distinct neighbours cannot. The compliance suite
only reaches `depth=99` on a three-node graph, so nothing here is slow today.

The fix is not a smaller depth limit — it is to stop enumerating paths, e.g.
expanding level by level with a visited set server-side. That was not done
because the port asks for one round trip and the plain-Cypher forms that
avoid path enumeration either need apoc (`apoc.path.subgraphNodes`) or a
`CALL {}` loop that is harder to read than the win justifies at current
scale. Revisit if slice 8's temporal traversal raises typical depths above
about 3.

### B10c1. Hop distance from `neighbors` — deliberately not added

`kg_builder/ports/graph_store.py::neighbors` returns entities without how far
away they are. **This is a decided deferral, not an oversight**, taken with
the trade-off explicit: the need is speculative (slice 8 *may* want it), the
port had just been through review, and widening a contract that three
adapters must implement on speculation is worse than retrofitting later. It
knowingly cuts against "change the port before the second adapter exists",
because the retrofit here is mechanical rather than structural.

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
distance, not first-found, is the contract worth pinning, and a diamond-shaped
graph (two paths of different length to the same node) is the case that
separates them.

### B10d. Legacy service tests still poison `sys.modules` at import time

`tests/unit/services/test_neo4j_errors.py` set `sys.modules["neo4j"]` to a
`MagicMock` at module level and never restored it, so *every* test collected
afterwards saw a fake `neo4j` — deselected modules included, since pytest
imports before it deselects. Slice 4 hit this as
`TypeError: object MagicMock can't be used in 'await' expression` raised from
the real adapter's driver, in a test that had nothing to do with that file.
That one is fixed: the originals are saved and restored once the module under
test has been exec'd.

**Slice 5 will probably hit this, so here is how to recognise it.** The
symptom never points at the cause. You get, in a test you just wrote, against
a library you are using correctly:

```
TypeError: object MagicMock can't be used in 'await' expression
AttributeError: 'MagicMock' object has no attribute '<something real>'
TypeError: 'MagicMock' object is not subscriptable
```

...or a mock that silently returns another `MagicMock` where you expected a
real value, so an assertion fails with a nonsense comparison instead of
raising. The tell is that **the fake object is a library you never mocked**.
Confirm it in one line before debugging anything else:

```python
import neo4j; print(neo4j.__file__)   # a real path, or "<MagicMock ...>"
```

It is also **order-dependent**: `pytest-randomly` reshuffles collection, so
the same test passes and fails between runs, and running the file alone
always passes. That combination reads as flakiness or infrastructure trouble,
which is the trap — do not pin the seed, find the poisoner.

**Which modules, and what each replaces.** Six are still unfixed:

| module | replaces in `sys.modules` |
|---|---|
| `tests/unit/extraction/test_circuit_breaker.py` | `kg_builder.config`, `redis`, `redis.asyncio` |
| `tests/unit/extraction/test_retry.py` | `kg_builder.config` |
| `tests/unit/services/test_neo4j_schema.py` | `kg_builder.services.neo4j` |
| `tests/unit/services/test_neo4j_tenant.py` | `kg_builder.services.neo4j` |
| `tests/unit/services/test_neo4j_queries.py` | `kg_builder.services.neo4j` |
| `tests/unit/services/test_neo4j_errors.py` | `kg_builder.events.scraping` |

**`redis` is the one to watch for slice 5** — it is a real installed package
being replaced process-wide, the same shape as the `neo4j` bug, and any new
test touching redis inherits the fake.

None is breaking anything *today*, which is exactly why they are worth
writing down. They exist because these modules load their subject with
`importlib` to dodge a heavy `__init__.py`; the fix is the same save/restore
applied in `test_neo4j_errors.py`, or `monkeypatch.setitem(sys.modules, ...)`
in a fixture so pytest undoes it. Apply when slices 7 and 9 delete the
services they cover — or sooner, for `redis`, if slice 5 trips on it.

### B11. `AsyncMock` misuse still warns in two tests

`tests/unit/services/test_embedding_cache.py` — `test_batch_set_uses_pipeline`
and `test_batch_set_redis_error` still emit `RuntimeWarning: coroutine
'AsyncMockMixin._execute_mock_call' was never awaited` from
`embedding_cache.py:275`. Redis pipelines queue commands synchronously and
only `execute()` is awaited, so `mock_pipeline.setex` should be a
`MagicMock`. Tests pass; the warning is real.

### B12. There is no accuracy suite any more

Slice 6 deleted `tests/accuracy/test_extraction_accuracy.py`, the only file
carrying the `accuracy` marker. It measured `OllamaExtractionService`, which no
longer exists. `tests/accuracy/` is now an empty package and the marker is
declared with nothing using it; both are left in place because a replacement is
wanted, not because they are doing anything.

The environmental blocker this entry used to describe is **resolved**: the
reference endpoint moved to `http://192.168.1.14:8080/v1` (llama-swap), and
`qwen3.6-27b-mtp` serves real completions. Slice 6 added
`tests/integration/llm/`, which talks to it and is green. So a live model is
available again — what is missing is the graded corpus.

**What a replacement needs, learned in slice 6.** Assertions must be about
*structure*, not taste. `tests/integration/llm/test_live_pipeline.py`
deliberately asserts that Ada Lovelace is extracted and that edges resolve, and
deliberately does **not** assert which `entity_type` the model assigns her —
that changes between model versions, and a suite that pins it fails on every
upgrade for no reason. Accuracy work is precision/recall against a corpus with
known answers, and it wants a tolerance band rather than equality.

Note also that the two suites cannot share a probe: the integration probe asks
for one completion and skips, whereas an accuracy run wants to *fail* loudly if
the model is missing, or the number it reports is silently "no data".

### B13. Five unused-variable findings in tests

`F841` at `tests/unit/schemas/test_project.py:292`,
`tests/unit/services/consolidation/test_string_similarity.py:569,579,587`,
`tests/unit/services/test_embedding_service.py:384`. An assigned-but-unused
result is often an assertion someone forgot to write — worth reading each
rather than deleting the variable.

### B14. Coverage is 60.79%

The ratchet prevents regression but does not drive this up. The least-covered
areas are the ones with no database (B10).

---

## 4. Code health

### B15. 98 ruff findings outstanding (pre-existing rule sets)

Repo-wide, excluding the files already cleaned. The README's claim of "~617"
is stale — the ruff configuration changed since it was written. As of slice
2b, `uv run ruff check src tests` run standalone (not scoped to a commit's
touched files) still finds these under rule sets that were already selected
before 2b. They pre-date the tightening and sit in files pre-commit has not
re-linted yet: the `ruff-check` hook lints whole files, but only files that
get staged in a commit. Anyone who touches one of the listed files will hit
these on commit and must fix them there (slice 2b did exactly this for
`cache.py`, `config.py`, `db.py`, `encryption.py`, and a handful of
`models`/`schemas` files it happened to touch).

| Rule | Count | Rule | Count |
|---|---|---|---|
| `E501` line-too-long | 40 | `RUF022` unsorted-`__all__` | 9 |
| `B904` raise-without-from | 12 | `RUF059` unused-unpacked-variable | 6 |
| `F841` unused-variable | 5 | `RUF012` mutable-class-default | 5 |
| `B007`/`B905`/`RUF013`/`RUF043` | 3 each | others | 14 |

`RUF012` and `RUF013` are the ones most likely to be hiding real defects.

The nine new rule sets added in slice 2b (`ANN`, `ASYNC`, `DTZ`, `ERA`, `PT`,
`PTH`, `RET`, `TC`, `TID`) are **not** in this table — they are fully clean
across `src/` and `tests/`, either fixed directly or covered by the
per-file-ignore ratchet in `pyproject.toml`.

### B16. 14 Pydantic v1-style `class Config` blocks

`PydanticDeprecatedSince20` warnings across 16 sites in
`schemas/extraction_provider.py`, `schemas/consolidation.py`,
`schemas/timeline.py`, `schemas/document.py`, `schemas/scraping.py`.
Replace with `ConfigDict`. Removed in Pydantic v3.

---

## 5. Deliberately deferred decisions

These were decided against *for now*, with reasons. Revisit consciously.

### B36. Free-form event payload dicts can hold a NUL that `jsonb` refuses

`domain/entity.py` (`properties`, `external_ids`) and
`domain/relationship.py` (`properties`) are `dict[str, Any]` with no
validation, and they reach the event log as payloads. Postgres `jsonb`
**cannot store a NUL character in text** -- it rejects the write outright --
so an entity carrying one is accepted by every in-memory adapter and refused
by the first persistent event store it reaches.

This is not hypothetical here: slice 5 hit exactly this on
`VectorRecord.metadata`, found it with a round-trip property test, and fixed
it in `domain/vector.py::_reject_nul` rather than in either adapter, so every
adapter would reject it identically.

Deferred rather than fixed in slice 5b for one reason: `_reject_nul` is
private to `domain/vector.py`, and sharing it means either a cross-module
private import or a new home for it, and slice 5b's permanent surface was
worth keeping minimal. Deferring is safe in a way that deferring a *schema*
decision is not -- adding the rejection later only refuses data that could
never have been persisted anyway, so no stored event becomes invalid.

To fix: move `_reject_nul` somewhere both can reach (a `domain/_json.py`, or
onto `domain/normalization.py`), and apply it in field validators on the three
fields above. The `VectorRecord` docstring explains why stripping or escaping
the NUL was rejected in favour of raising.

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
  `GraphStore.delete_entity`, which slice 3 deliberately did not add, and
  would make the fold non-idempotent under at-least-once delivery: the delete
  half of a redelivered event would remove entities a later event had added.
- Having the aggregate compute the retraction from its own replayed state is
  the right answer and is cheap -- the `Document` aggregate already replays
  every `DocumentExtracted` -- but it needs a decision about what "the same
  entity across two extraction runs" means (id equality is not it; ids are
  freshly generated per run), and that is slice 6's question.

Take it up in slice 6, when extraction actually emits.

### B33. `events/consolidation.py` and `events/scraping.py` are dead schema

40 ORM-shaped event classes, none of which has ever been emitted, kept alive
only by `services/neo4j_errors.py` (one, `Neo4jSyncFailed`). Slice 5b deleted
the five modules with no consumers at all -- `documents`, `extraction`,
`inference`, `projects`, `relationships` -- and stopped `events/__init__.py`
re-exporting the survivors. Slice 7 deleted `events/consolidation.py` in the
commit that removed its last consumer, `services/consolidation/merge_service.py`.

`scraping.py` cannot go here because deleting it means rewriting
`neo4j_errors.py`, which is slice 9. Delete it in the commit that removes that
consumer; nothing else needs to happen first. Note `events/base.py` exists only
to serve it.

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
survives as a source-less directory under a package path. Slice 6 left two
(`inference/`, `inference/providers/`) and slice 7 left two more
(`services/consolidation/`, and the mirrored test directories) before noticing.

Harmless in itself -- Python 3 will not import from a `__pycache__` without
its source -- but it is this entry's trap in miniature: a directory that looks
like a package, holds bytecode for modules that no longer exist, and is
invisible to every check the gate runs. Slice 9 deletes a much larger tree and
will produce a crop of them.

Delete them in the same commit, and note that doing so produces **no diff**,
so the commit needs another reason to exist. To find them:

```sh
find src tests -type d -name __pycache__ -printf '%h\n' | sort -u |
  while read -r dir; do
    [ -z "$(find "$dir" -maxdepth 1 -name '*.py' -print -quit)" ] && echo "$dir"
  done
```

### B38. The `eventsourcing` extra no longer pulls `eventsource-py[all]`

`pyproject.toml` declares `eventsourcing = ["eventsource-py>=0.9.1,<0.11"]`.
It used to be `eventsource-py[all]>=0.5.0`, and the `[all]` was dropped in
slice 5b, not by preference but because it cannot currently resolve:

```
eventsource-py[all]>=0.9.1  requires  redis>=8.0,<9.0
kg-builder                  requires  redis[hiredis]>=5.3,<6
```

`redis` is a direct dependency here for `cache.py` and `services/
embedding_cache.py`, so this is a real conflict rather than a lockfile
accident. Dropping `[all]` costs nothing today -- slice 5b is in-memory only,
by decision, and the base package carries the store, bus, projections and
aggregates. It costs something the moment a Kafka, RabbitMQ, Redis or
PostgreSQL adapter is wanted (slices beyond 10), because each lives behind an
extra.

The `<0.11` cap is separate and deliberate: this is a pre-1.0 library whose
entire API changed between 0.5 and 0.9, and the slice 5b bump is the evidence.
Without a cap the version under test drifts from the version pinned -- 0.10.0
was already resolving under a bare `>=0.9.1` -- and 0.11 would arrive with no
one deciding to take it. Raise the cap deliberately, with the suite green
under the new version, rather than discovering it in a failed CI run.

To fix the extra, in order of preference: widen kg-builder's `redis` pin to
`<9` and
verify `cache.py` and `embedding_cache.py` against redis-py 8 (the 5->8 API is
largely source-compatible, but neither module is covered against a real
server, so this needs the integration suite that B10 asks for); or take the
narrow extras actually wanted (`eventsource-py[postgresql]`) rather than
`[all]`.

### B17. Column defaults do not hold at construction time

`ExtractedEntity.is_canonical` and `ScrapingJob.enable_timeline_extraction`
declare `default=`, which SQLAlchemy applies at INSERT. With no database in
the suite (B10), an unflushed instance reads `None`. Their tests now assert
the declared default rather than instance state.

If these invariants should hold on construction, that is a model-level change
to make once, across all models, rather than per-column. Relevant to the ring
migration: a domain entity should carry its invariants without needing a
session.

### B18. `UP042` is ignored project-wide

Rewriting `class X(str, Enum)` as `enum.StrEnum` changes `str(X.A)` from
`"X.A"` to `"a"`, silently altering every f-string and log line holding a
member. The idiom appears at **33 sites**. This is a behaviour migration to
make wholesale with tests, not a drive-by autofix. Rationale is recorded in
`pyproject.toml`.

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

The real fix is upstream and is B33's territory: `properties` and
`external_ids` have no value schema at all. Give them one and this rule stops
firing on its own. Until then, `noqa` with this note beats a lie in the
signature, and it must not become a per-file ignore -- B30 forbids adding
`domain/` to that list, deliberately.

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

### B21. README is stale

It describes the pre-stabilization state: "1764 passed, 42 failed", "~617
lint findings", "117 of 118 modules import cleanly", and a "Known gaps"
section now superseded by this file. Rewrite when the ring migration lands
(slice 11).

### B22. No documentation infrastructure

No `docs/` beyond `docs/plans/` and an empty `docs/adrs/`, no ADRs, no
mkdocs, no CHANGELOG. The ring migration creates ADR 0001 and a CHANGELOG
with the breaking-path entries; general docs remain absent.

### B23. `.claude/skills/migrating-modules-to-rings/sweep.sh` is not portable

Hardcodes `eventsource` as the root package and allowlists
`docs/superpowers/`, which does not exist here. Parameterise for
`kg_builder` before the first move slice.


### B30. Legacy-package ruff/mypy exemption ratchet (slice 2b)

`pyproject.toml`'s `[tool.ruff.lint.per-file-ignores]` and `[tool.mypy]
exclude` both carry a matching list of legacy packages
(`models`, `services`, `inference`, `extraction`, `preprocessing`, `graph`,
`schemas`, `events`) plus mirrored test directories
(`tests/unit/{extraction,models,schemas,services}/**`) for the ruff side.
This list may only shrink — delete a package's entry in the same commit that
deletes the package (slices 6-9). `domain/`, `ports/`, and every package
created after slice 2b get full strictness from birth and must never be
added here.

### B31. `InferenceProvider.close` trips B027, silenced with `noqa`

`inference/providers/base.py:427` — `close()` is an intentional no-op default
in a template-method style base class (subclasses override it to release
HTTP connections; most don't need to). B027 (empty method in an ABC without
`@abstractmethod`) flags this, but making it `@abstractmethod` would force
every subclass to implement a trivial no-op, and adding `inference/` to the
`B` per-file-ignores list is against the ratchet policy in B30 (list may only
shrink). Discovered incidentally while fixing B29 in the same file — this
predates that change and pre-commit only surfaces it when the file is
touched. Silenced with an inline `noqa` rather than fixed, since `inference/`
is scheduled for deletion in slice 6/9; revisit only if the package survives
longer than expected.
