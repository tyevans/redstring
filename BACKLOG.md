# Backlog

Deferred work. Every deficiency found and not fixed on the spot lands here,
with enough detail that picking it up does not require rediscovering it.

Status of the tree as of the last update: **1852 tests pass, 0 fail**,
full `pre-commit` gate green, nothing skipped at collection. The accuracy
suite is deselected by default (see B12). (Slice 1 of the ring migration
deleted document sourcing -- scraping, storage, document parsing, and HTML
preprocessors -- which accounts for the drop from the previous count.)

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

That is correct for the current layering but not their real home: they are
domain value objects and belong in `temporal/domain/` once the ring migration
lands. Move them there in the `temporal/` slice.

---

## 3. Test suite

### B10. No database anywhere in the test suite

There is no sqlite, no `create_async_engine`, no `sessionmaker`, and no
integration fixture. Consequences:

- Nothing exercises the SQL in `vector_ops`, `blocking`, `merge_service`,
  `timeline_query`, `project_timeline_query`, or `sync_status` — the queries
  are only ever constructed, never run.
- Column `default=` values cannot be observed, because SQLAlchemy applies
  them at INSERT. Two tests were rewritten to assert the *declared* default
  instead (see B17).
- The `integration` marker is declared in `pyproject.toml` but no test uses
  it, and `tests/integration/` does not exist.

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

### B15. 159 ruff findings outstanding

Repo-wide, excluding the files already cleaned. The README's claim of "~617"
is stale — the ruff configuration changed since it was written.

| Rule | Count | Rule | Count |
|---|---|---|---|
| `E501` line-too-long | 69 | `RUF012` mutable-class-default | 16 |
| `B904` raise-without-from | 13 | `RUF013` implicit-optional | 11 |
| `RUF022` unsorted-`__all__` | 10 | `RUF059` unused-unpacked-variable | 6 |
| `F841` unused-variable | 5 | `SIM102` collapsible-if | 4 |
| `SIM118` in-dict-keys | 4 | others | 21 |

`RUF012` and `RUF013` are the ones most likely to be hiding real defects.
48 are autofixable with `--unsafe-fixes`; review, do not bulk-apply.

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
