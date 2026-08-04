# Backlog

Deferred work. Every deficiency found and not fixed on the spot lands here,
with enough detail that picking it up does not require rediscovering it.

Status of the tree as of the last update: **2070 tests pass, 0 fail** in the
default gate, plus **106 `integration` tests** against a real Neo4j from
`docker-compose.test.yml` (slice 4). Full `pre-commit` gate green (now
including `mypy`, see B30), nothing skipped at collection. The accuracy and
integration suites are deselected by default (see B12, B10a); a run now prints
what it deselected and how to run it. (Slice 1 of the ring migration deleted
document sourcing -- scraping, storage, document parsing, and HTML
preprocessors -- which accounts for the earlier drop in count.)

Ordering within a section is roughly by priority. Ordering between sections
is not meaningful.

---

## 1. Unlanded features

### B2. Anthropic extraction provider is a stub

`extraction/registry.py:179` — "Creator for Anthropic extraction services
(placeholder)". The `anthropic` extra is declared in `pyproject.toml` and
advertised in the README, but the provider does not work.

### B3. `mark_sync_failed` does not persist anything

`services/sync_status.py:252` — the method only logs. Its own comment lists
what it should do: store the error, increment a retry counter, set
`next_retry_at`, emit a failure event. `retry_failed_syncs` therefore cannot
distinguish "never synced" from "failed repeatedly".

### B4. Relationship extraction in `llm_extractor` is rudimentary

`extraction/llm_extractor.py:385` — "a placeholder for more sophisticated
relationship extraction".

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

### B25. `get_strategy_router` reintroduces the global state the factory avoids

`extraction/strategy_router.py` carries an explicit design note that
`ExtractionStrategyRouterFactory` exists so there is "no hidden global
state". `get_strategy_router` / `reset_strategy_router` were added because
`tests/unit/extraction/test_strategy_router.py` requires a singleton, and the
singleton keeps whichever inference provider it was first constructed with.

The tension is real and documented at the definition site. Resolve it by
deciding which accessor is the supported one, then deleting the other and its
tests — do not leave both as equals.

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

### B10b. Model blocking keys as nodes — decided, scheduled for slice 7

**The design is decided; do not re-litigate it. Implement in slice 7.**
Blocking keys become nodes:

```cypher
(e:Entity)-[:BLOCKED_BY]->(:BlockingKey {tenant_id, key})
```

**Why the property form cannot work, with the evidence, so slice 7 need not
re-measure it.** `find_by_blocking_key` asks `$key IN e.blocking_keys`. A
Neo4j range index over a list property indexes **the list as a single value**,
so it answers "which entities have exactly this array" and cannot answer
membership. Measured with `EXPLAIN` on **5000 entities across 100 tenants**:

| query | plan |
|---|---|
| `WHERE $key IN e.blocking_keys`, with a `(tenant_id, blocking_keys)` index | `NodeByLabelScan` + `Filter` |
| the same, without the index | `NodeByLabelScan` + `Filter` — *identical* |
| with `_TENANT_SEEK` added | `NodeUniqueIndexSeek` + `Filter` |

The index made no difference to the plan, so `src/kg_builder/graph/adapters/
neo4j.py` deliberately does **not** create one: it would cost write
throughput on every upsert and buy nothing. `_TENANT_SEEK` narrows the scan
from the whole database to one tenant, which is the best plain Cypher can do
and is why this is survivable in the meantime.

**Why it matters more than "a scan is a bit slow".** Consolidation does not
look up one key — it blocks a whole tenant and then, for each candidate,
fetches that candidate's block. A per-entity lookup that scans the tenant is
**O(n) per entity and therefore O(n²) across a tenant**. That is the real
cost, and it is why the acceptable-today reading expires exactly when
consolidation lands.

The rejected alternative, recorded so it is not revisited: a full-text index
on `blocking_keys` works on arrays but **tokenises**, which changes matching
semantics — blocking keys are opaque identifiers (`"A430"`, `"person:ad"`)
and must match exactly.

**The one implementation trap.** A re-upsert must delete the entity's
*previous* key edges before writing the new ones, or a stale key keeps
matching.
`tests/compliance/graph_store.py::test_find_by_blocking_key_reflects_the_latest_write`
is the test that enforces it, and it is the reason this is a second write
path rather than a one-line change. That is what made it wrong to land
speculatively in slice 4.

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

### B12. Accuracy suite cannot run in this environment

Deselected from the default run via `addopts = ["-m", "not accuracy"]`, which
is what its own docstring always intended. When run explicitly with
`-m accuracy` it now skips honestly, because the fixture verifies the model
can serve rather than merely that it is listed.

The blocker is environmental: Ollama at `192.168.1.14:11434` lists
`gpt-oss:20b`, but `POST /v1/chat/completions` returns 500 "model failed to
load, this may be due to resource limitations". Nothing in this repo can fix
that. It also means extraction accuracy is currently unmeasured.

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

### B19. `pydantic-ai` pinned to `==0.0.31`

`extraction` and `inference.providers` use the pre-1.0
`pydantic_ai.models.openai.OpenAIModel` API. Unpinning means porting both.

---

## 6. Documentation

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
