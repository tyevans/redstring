# Backlog

Deferred work. Every deficiency found and not fixed on the spot lands here,
with enough detail that picking it up does not require rediscovering it.

Status of the tree as of the last update: **1966 tests pass, 0 fail**,
full `pre-commit` gate green (now including `mypy`, see B30), nothing skipped
at collection. The accuracy suite is deselected by default (see B12). (Slice
1 of the ring migration deleted document sourcing -- scraping, storage,
document parsing, and HTML preprocessors -- which accounts for the drop from
the previous count.)

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

### B10a. Integration-only code is invisible to the coverage ratchet

`src/kg_builder/graph/adapters/neo4j.py` is fully exercised, but only by
`tests/integration/`, which `addopts` excludes from the default run and
therefore from `scripts/coverage_ratchet.py`. It is listed in
`[tool.coverage.run] omit` so the ratchet does not read "0% covered" for code
that has 106 passing tests against it.

The cost of that omission: a genuinely untested branch added to the adapter
would not be caught by the commit gate. What is needed is a second coverage
run over `-m integration` whose data is combined with the default run's
(`coverage combine` — `parallel = true` is already set, so the data files
already accumulate rather than overwrite). It was deferred because the
integration run needs Docker, and making the ratchet conditional on Docker
being up turns a deterministic gate into a flaky one. The right shape is
probably a separate `make coverage-full` for CI that starts the compose file,
runs both, and combines — not a change to the commit hook. Slice 5 will hit
this again with pgvector, so it is worth solving once, then.

### B10b. `find_by_blocking_key` scans the tenant on Neo4j

`src/kg_builder/graph/adapters/neo4j.py` —
`$key IN e.blocking_keys` cannot use an index. A Neo4j range index over a list
property indexes the list as a single value, so it serves equality on the
whole array and not membership; measured with `EXPLAIN` on 5000 entities, a
`(tenant_id, blocking_keys)` index left the plan unchanged. The index was
therefore *not* created — it would cost write throughput for nothing.

The query is narrowed to one tenant by the `_TENANT_SEEK` predicate, so it
reads a tenant rather than the database, which is acceptable while tenants
are small. If consolidation makes this hot, the two real options are a
full-text index on `blocking_keys` (Lucene, works on arrays, but changes
matching semantics — it tokenises) or promoting blocking keys to their own
nodes, `(e:Entity)-[:BLOCKED_BY]->(:BlockingKey {tenant_id, key})`, which is
graph-native and exactly indexed. The node form is the better answer and was
deferred only because it complicates the upsert: a re-upsert must delete the
entity's previous key edges, and
`test_find_by_blocking_key_reflects_the_latest_write` is the test that
enforces it.

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

### B10d. Legacy service tests still poison `sys.modules` at import time

`tests/unit/services/test_neo4j_errors.py` set `sys.modules["neo4j"]` to a
`MagicMock` at module level and never restored it, so *every* test collected
afterwards saw a fake `neo4j` — deselected modules included, since pytest
imports before it deselects. Slice 4 hit this as
`TypeError: object MagicMock can't be used in 'await' expression` raised from
the real adapter's driver, in a test that had nothing to do with that file.
That one is fixed: the originals are saved and restored once the module under
test has been exec'd.

Six sibling modules still do the same thing to other names and are not fixed:
`test_circuit_breaker.py` (`kg_builder.config`, `redis`, `redis.asyncio`),
`test_retry.py` (`kg_builder.config`), `test_neo4j_schema.py`,
`test_neo4j_tenant.py`, `test_neo4j_queries.py` (each
`kg_builder.services.neo4j`), and `test_neo4j_errors.py` itself for
`kg_builder.events.scraping`. None is breaking anything *today*, which is
exactly why they are worth writing down: the failure is silent, arrives in an
unrelated test, and depends on collection order — `pytest-randomly` can make
it appear and disappear between runs. They exist because these modules load
their subject with `importlib` to dodge a heavy `__init__.py`; the fix is the
same save/restore, or `monkeypatch.syspath_prepend`-style fixtures, applied
when slices 7 and 9 delete the services they cover.

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
