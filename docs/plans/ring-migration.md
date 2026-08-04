# Ring migration plan — kg-builder

Dissolve the current flat package layout into bounded contexts, each with
DDD rings. Clean breaks only: no deprecation shims, no re-export bridges
left behind after a slice completes. History preserved with `git mv`.

Adapted from the `eventsource-py` `migrating-modules-to-rings` skill. The
differences from that campaign are called out in "Deviations" at the end.

## Decisions taken up front

1. **Bounded contexts, each with rings** — not one global ring set.
2. **Stabilize before moving** — slice 0 makes the suite green so every later
   slice has a trustworthy gate.
3. **Strip auth vestiges** — `User`, `UserTenantMembership`, `OAuthProvider`,
   OAuth/JWT settings, and FastAPI-shaped session helpers go. `tenant_id`
   survives as a plain scoping column, not a foreign key into an auth model.

## Target layout

```
src/kg_builder/
  shared/          shared kernel + cross-cutting infrastructure
  llm/             supporting: LLM/inference provider access
  embedding/       supporting: embedding generation, cache, vector ops
  preprocessing/   core: chunk / clean / merge source content
  extraction/      core: entity + relationship extraction from content
  consolidation/   core: entity resolution, similarity, merge
  graph/           core: Neo4j projection and query
  scraping/        core: web acquisition
  documents/       core: uploaded-document acquisition and parsing
  temporal/        core: temporal parsing, inference, timelines
  pipelines/       cross-context orchestration (the only place contexts meet)
```

Each context carries the rings it needs:

```
<context>/
  domain/       entities, value objects, exceptions, pure policy. stdlib + pydantic only.
  ports/        Protocols / ABCs. interface only, zero implementation.
  application/  use-case orchestration. depends on domain + ports, never adapters.
  adapters/     drivers, wire formats, storage formats, ORM rows, HTTP clients.
```

Small supporting contexts (`llm`, `embedding`) may have only `ports/` and
`adapters/`. A context with no `domain/` is a signal worth questioning, not a
rule violation.

### Dependency rules

- Within a context: `adapters` → `application` → `ports` → `domain`.
- `shared` sits below every context; nothing in `shared` may import a context.
- Core and supporting contexts are **independent siblings** — they must not
  import each other. Where one genuinely needs another's capability, it
  declares a narrow Protocol in its own `ports/` and `pipelines/` wires the
  concrete implementation in.
- `pipelines` sits above everything and may import any context's `ports`,
  `application`, and `adapters`.

That last rule is the load-bearing one. Today `services/consolidation`
imports embeddings directly, `scraping.pipelines` reaches into extraction via
a hook, and `services/extraction/orchestrator` pulls preprocessing. Each of
those becomes a port + a wiring line in `pipelines`.

## Ring assignment

Signals used: interface-only → `ports`; stdlib/pydantic only → `domain`;
touches a driver, wire format, or storage format → `adapters`; use-case
orchestration → `application`.

### `shared/`

| Current | Destination |
|---|---|
| `config.py` (minus OAuth/JWT settings) | `shared/adapters/config.py` |
| `context.py` | `shared/domain/context.py` |
| `db.py` (reshaped, FastAPI helpers dropped) | `shared/adapters/db.py` |
| `cache.py` | `shared/adapters/cache.py` |
| `encryption.py` | `shared/adapters/encryption.py` |
| `events/base.py` | `shared/domain/events.py` |
| `models/tenant.py`, `models/project.py` | `shared/adapters/orm/` |
| `schemas/project.py` | `shared/domain/project.py` |
| `events/projects.py` | `shared/domain/events_projects.py` |
| new: `CachePort`, `ClockPort`, `UnitOfWorkPort` | `shared/ports/` |

Deleted outright: `models/user.py`, `models/user_tenant_membership.py`,
`models/oauth_provider.py`, and the OAuth/JWT half of `config.py`.

### `llm/`

| Current | Destination |
|---|---|
| `inference/providers/base.py` (7 abstract methods) | `llm/ports/provider.py` |
| `inference/providers/ollama.py` | `llm/adapters/ollama.py` |
| `inference/providers/factory.py` | `llm/adapters/factory.py` |
| `models/inference_provider.py`, `models/inference_request.py` | `llm/adapters/orm/` |
| `events/inference.py` | `llm/domain/events.py` |
| `extraction/rate_limiter.py`, `extraction/circuit_breaker.py` (redis) | `llm/adapters/` |
| `extraction/retry.py` | `llm/domain/retry.py` |

Rate limiting, retry, and the circuit breaker are LLM-transport concerns, not
extraction concerns — moving them here is the one non-obvious reassignment in
this plan and is worth challenging during slice 3.

### `embedding/`

| Current | Destination |
|---|---|
| `services/embedding.py` | `embedding/ports/provider.py` + `embedding/adapters/http.py` |
| `services/openai_embedding.py` | `embedding/adapters/openai.py` |
| `services/embedding_cache.py` | `embedding/adapters/cache.py` |
| `services/vector_ops.py` | `embedding/adapters/pgvector.py` |

### `extraction/`

| Current | Destination |
|---|---|
| `extraction/base.py`, `extraction/registry.py` | `extraction/ports/` |
| `extraction/schemas.py`, `extraction/schema_org.py`, `extraction/classifier.py`, `extraction/prompts.py`, `extraction/prompt_generator.py`, `extraction/strategy_router.py` | `extraction/domain/` |
| `extraction/domains/**` (models, loader, registry, YAML schemas) | `extraction/domain/domains/` |
| `extraction/ollama_extractor.py`, `openai_extractor.py`, `llm_extractor.py` | `extraction/adapters/` |
| `extraction/factory.py` | `extraction/adapters/factory.py` |
| `services/extraction/orchestrator.py` | `extraction/application/orchestrator.py` |
| `models/extracted_entity.py`, `models/extraction_provider.py` | `extraction/adapters/orm/` |
| `schemas/extraction_provider.py` | `extraction/domain/provider.py` |
| `events/extraction.py`, `events/relationships.py` | `extraction/domain/events.py` |

`services/extraction/temporal_enrichment.py` is resolved in slice 0 — it does
not import today. If it survives, it lands in `temporal/application/`.

### `preprocessing/`

| Current | Destination |
|---|---|
| `preprocessing/base.py`, `preprocessing/pipeline.py` (Protocols) | `preprocessing/ports/` |
| `preprocessing/schemas.py`, `preprocessing/exceptions.py` | `preprocessing/domain/` |
| `preprocessing/pipeline.py` (orchestration half) | `preprocessing/application/pipeline.py` |
| `preprocessing/chunkers/**` | `preprocessing/domain/chunkers/` (pure) |
| `preprocessing/preprocessors/trafilatura_preprocessor.py` | `preprocessing/adapters/` |
| `preprocessing/preprocessors/passthrough_preprocessor.py` | `preprocessing/domain/` |
| `preprocessing/mergers/simple_merger.py` (jellyfish) | `preprocessing/adapters/` |
| `preprocessing/mergers/llm_merger.py` | `preprocessing/adapters/` (consumes `llm` port) |
| `preprocessing/factory.py` | `preprocessing/adapters/factory.py` |

`pipeline.py` is split — it holds both a Protocol and the orchestration loop.

### `scraping/`

| Current | Destination |
|---|---|
| `scraping/items.py`, `middlewares.py`, `pipelines.py`, `runner.py`, `settings.py`, `spiders/**` | `scraping/adapters/` (all Scrapy) |
| `scraping/hooks.py` (dispatcher Protocol) | `scraping/ports/dispatcher.py` |
| `models/scraping_job.py`, `models/scraped_page.py` | `scraping/adapters/orm/` |
| `schemas/scraping.py` | `scraping/domain/` |
| `events/scraping.py` | `scraping/domain/events.py` |

The `set_extraction_dispatcher` hook is replaced by a real port that
`pipelines` satisfies.

### `documents/`

| Current | Destination |
|---|---|
| `services/document_parser.py` | `documents/application/parser.py` + `documents/ports/parser.py` |
| `services/storage/object_storage.py` | `documents/adapters/s3.py` (behind `documents/ports/storage.py`) |
| `models/uploaded_document.py` | `documents/adapters/orm/` |
| `schemas/document.py` | `documents/domain/` |
| `events/documents.py` | `documents/domain/events.py` |

### `consolidation/`

| Current | Destination |
|---|---|
| `services/consolidation/string_similarity.py`, `combined_scoring.py`, `graph_similarity.py` | `consolidation/domain/` |
| `services/consolidation/embedding_similarity.py` | `consolidation/application/` (consumes `EmbeddingPort`) |
| `services/consolidation/blocking.py` | split: SQL half → `adapters/`, policy half → `domain/` |
| `services/consolidation/merge_service.py` | `consolidation/application/merge.py` + `adapters/orm` |
| `models/entity_alias.py`, `merge_history.py`, `merge_review_queue.py`, `consolidation_config.py` | `consolidation/adapters/orm/` |
| `schemas/consolidation.py`, `schemas/similarity.py` | `consolidation/domain/` |
| `events/consolidation.py` | `consolidation/domain/events.py` |

### `graph/`

| Current | Destination |
|---|---|
| `graph/client.py` (neo4j driver) | `graph/adapters/neo4j_client.py` |
| `graph/queries.py` | `graph/domain/queries.py` (Cypher templates, no driver) |
| `services/neo4j.py`, `neo4j_errors.py`, `neo4j_schema.py`, `neo4j_tenant.py`, `neo4j_queries.py` | `graph/adapters/` |
| `services/sync_status.py` | `graph/application/sync_status.py` |
| new: `GraphPort` | `graph/ports/graph.py` |

### `temporal/`

| Current | Destination |
|---|---|
| `services/temporal_parser.py` (dateparser) | `temporal/adapters/parser.py` behind `temporal/ports/parser.py` |
| `services/temporal_relationship_inference.py` | `temporal/domain/inference.py` |
| `services/timeline_query.py`, `project_timeline_query.py` | `temporal/adapters/` (SQL) + `temporal/application/` |
| `services/timeline_cache.py` | `temporal/adapters/cache.py` |
| `services/timeline_export.py` | `temporal/application/export.py` |
| `schemas/timeline.py` | `temporal/domain/timeline.py` |

## Slices

Dispatch one at a time. Foundation first — later slices import its output.
Targeted tests per slice; full gate at slice end, not after every edit.

| # | Slice | Gate |
|---|---|---|
| 0 | **Stabilize.** ✅ Done. Triaged the 42 failures to seven root causes and fixed all of them. 1798 passed, coverage baseline 60.79 recorded. | Full suite green ✅ |
| 0b | **Land the temporal/strategy-router WIP.** The four modules in `tests/conftest.py::collect_ignore` are 99 tests of unlanded feature work, not stabilization damage — see "Temporal work" below. Implement, then empty `collect_ignore`. | Full suite green, zero collection skips |
| 1 | **Strip auth.** Delete `models/user.py`, `user_tenant_membership.py`, `oauth_provider.py`; rewrite relationships onto plain `tenant_id`; strip OAuth/JWT from `config.py`; reshape `db.py` into a library session provider. | Full suite green |
| 2 | **Foundation: `shared/`.** Create the ring tree, move shared-kernel modules, write `shared/ports/`, rewrite the import-linter contract to the per-context form. | `lint-imports` + full suite |
| 3 | **`llm/` + `embedding/`.** Define both ports; move providers, retry/rate-limit/breaker, embedding adapters. Add identity tests before the move, retarget after. | targeted + `lint-imports` |
| 4 | **`preprocessing/`.** Split `pipeline.py`. | targeted + `lint-imports` |
| 5 | **`extraction/`.** Largest single slice (7.5k LOC). | targeted + `lint-imports` |
| 6 | **`consolidation/`.** Split `blocking.py` and `merge_service.py`. | targeted + `lint-imports` |
| 7 | **`graph/`.** Introduce `GraphPort`. | targeted + `lint-imports` |
| 8 | **`scraping/` + `documents/`.** Replace the `hooks` dispatcher with a port. | targeted + `lint-imports` |
| 9 | **`temporal/`.** | targeted + `lint-imports` |
| 10 | **`pipelines/`.** Wire the cross-context composition that the port extractions in slices 3–9 deferred. Delete the now-empty `services/`, `models/`, `schemas/`, `events/`, `inference/` packages. | full gate |
| 11 | **Docs & meta.** ADR 0001 (ring architecture), `docs/adrs/index.md`, README rewrite, `CLAUDE.md` structure + contract block, CHANGELOG `**BREAKING:**` entry naming every retired path. | `sweep.sh` clean |

Slices 3–9 are independent of each other once slice 2 lands, but each one
touches `pyproject.toml`'s import-linter contract and the root `__init__.py`.
Those two files are the shared seam: re-read immediately before each edit and
keep edits surgical and single-line.

## Temporal work

Temporal is a first-class core context in this plan, not a small supporting
one — it is a capability the project wants, so the `temporal/` ring set is
built to be extended rather than merely relocated.

Four test modules are skipped at collection because the code they exercise
was never finished. They are unlanded features, not extraction damage:

| Module | Tests | Missing |
|---|---|---|
| `unit/extraction/test_temporal_schemas.py` | 14 | `TemporalEventProperties`, `is_temporal_relationship_type` in `extraction/schemas.py` |
| `unit/models/test_extracted_entity_temporal.py` | 31 | Temporal columns on `ExtractedEntity` (none exist today) plus `DatePrecision`/`UncertaintyMarker` re-exports |
| `unit/services/extraction/test_temporal_enrichment.py` | 19 | The enrichment service itself; the module does not import |
| `unit/extraction/test_strategy_router.py` | 35 | `get_strategy_router`, `reset_strategy_router`, `route_extraction_strategy` — only `get_strategy_router_factory` exists |

`DatePrecision` and `UncertaintyMarker` already exist in
`schemas/timeline.py`, so two of these are partly re-export work. The
`ExtractedEntity` temporal columns are a genuine schema addition.

Slice 0b should land these against the now-green gate, before any code
moves — writing a feature into a package that is about to be dissolved is
cheaper than writing it across a half-migrated tree.

## Per-slice mechanics

1. Extract ports/domain/adapters pieces **while the old package stays put**
   and re-exports them. Add identity tests (`new.X is old.X`) now.
2. `git mv` whole files. Verify `git status` shows `R` renames, not
   delete+add. Mirror the unit-test tree; retarget the identity tests.
3. Add a `pytest.raises(ModuleNotFoundError)` guard for the retired path,
   using `importlib.import_module(...)` so the sweep doesn't flag it.
4. Run the sweep for the retired package before closing the slice.

Commit small and often — every quality gate runs in `pre-commit`, so smaller
commits mean faster hook runs. Do not run ruff/bandit/lint-imports/pytest as
separate pre-commit steps; write the change and commit.

## Sweep

`.claude/skills/migrating-modules-to-rings/sweep.sh` hardcodes `eventsource`
as the root package. Slice 2 parameterises it for `kg_builder` (and drops the
`docs/superpowers/` allowlist entry, which does not exist here). Sweep the
whole repo, denylist not allowlist. In `pyproject.toml` check three spots:
the import-linter contract module lists, `[tool.mutmut] paths_to_mutate`, and
pytest test-selection args. Also check `cosmic-ray.toml`.

## Deviations from the eventsource-py skill

| Skill assumes | Here |
|---|---|
| One global ring set | Per-context rings (see decisions) |
| Existing `docs/adrs/` with numbers to increment | None; this campaign creates ADR 0001 |
| mkdocs nav to update | No mkdocs; README + `CLAUDE.md` only |
| `make check`, `validate_examples.py`, Docker integration suite | None exist; gate is `pre-commit` + `uv run pytest` |
| Sibling ring campaigns to coordinate with | Single-branch campaign, no open PRs |
| Green baseline | Red baseline — hence slice 0 |

## Risks

- **Slice 0 is the whole schedule's risk.** The 42 failures have never been
  diffed against a knowledge-mapper baseline, so it is unknown whether they
  are extraction damage or pre-existing. If they turn out to be deep, slice 0
  grows and everything after it slips.
- **The coverage ratchet.** Deleting auth models and broken WIP changes total
  coverage, possibly downward. Slices 0 and 1 will need a deliberate
  `.coverage-baseline` edit with the reason in the commit message.
- **`extraction/` at 7.5k LOC** is the one slice that may need splitting
  again once its internals are read closely.
- **Reassigning retry / rate-limit / circuit-breaker into `llm/`** is a
  judgement call, not a mechanical classification. Revisit at slice 3.
