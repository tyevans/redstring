# kg-builder

Knowledge graph construction library, extracted from the `knowledge-mapper` application.

## Status

**Early extraction — not yet a clean library.** The code was copied out of
`knowledge-mapper/backend/app` and rewritten to import from `kg_builder.*` instead of
`app.*`. It has not been reshaped into a stable public API, and not all modules import
cleanly yet. The goal of this first pass was to get the content into one place.

`knowledge-mapper` is unchanged and remains the working implementation.

## Layout

| Package | Contents |
|---|---|
| `kg_builder.extraction` | LLM entity extraction: providers (Ollama, OpenAI, Anthropic), domain registry, prompts, schema.org, classification, strategy routing, rate limiting, retry, circuit breaker |
| `kg_builder.preprocessing` | Content preprocessing pipeline: chunkers, preprocessors, mergers |
| `kg_builder.graph` | Neo4j client and query layer |
| `kg_builder.inference` | Inference provider abstraction |
| `kg_builder.scraping` | Scrapy spiders, pipelines, middlewares, runner |
| `kg_builder.services` | Neo4j sync/schema/tenant helpers, embeddings, vector ops, entity consolidation, document parsing, temporal parsing/inference, timeline query/export/cache, object storage |
| `kg_builder.models` | SQLAlchemy models for the knowledge-graph domain |
| `kg_builder.schemas` | Pydantic request/response schemas |
| `kg_builder.events` | Domain event definitions |
| `kg_builder.config` / `.db` / `.cache` / `.encryption` / `.context` | Shared infrastructure |

## What was deliberately left behind

Auth and platform concerns stay in `knowledge-mapper`: OAuth/Keycloak clients, JWKS,
app tokens, token revocation, tenant resolution and context middleware, Row-Level
Security, user/tenant/oauth models, FastAPI routers and app, Alembic migrations,
Celery app and tasks, and the event-sourcing infrastructure (aggregates, stores,
projections, handlers, outbox, subscriptions). Only the event *definitions* were copied.

`tenant_id` remains a parameter throughout — it is a scoping key here, not auth.

The `Tenant`, `User`, `UserTenantMembership`, and `OAuthProvider` ORM models *were* kept
— not for auth, but because the knowledge-graph models declare SQLAlchemy relationships
to them and the mapper registry will not resolve without them.

## State

```
uv sync --extra all --extra dev
uv run pytest        # 1764 passed, 42 failed
uv run python -c "import kg_builder.extraction, kg_builder.graph, kg_builder.scraping"
```

117 of 118 modules import cleanly.

## Known gaps

- `kg_builder.services.extraction.temporal_enrichment` does not import — it references
  `TemporalEventProperties`, which does not exist in `extraction/schemas.py`. This was
  already broken in knowledge-mapper (uncommitted work-in-progress at extraction time).
- Four test modules are skipped at collection for the same reason; see `tests/conftest.py`.
- The 42 remaining test failures cluster in `test_vector_ops`, `test_scraping_job_timeline`,
  `test_embedding_cache`, and the accuracy suite. These have **not** been diffed against a
  knowledge-mapper baseline — its virtualenv was left alone to keep that repo untouched —
  so it is unverified whether they are pre-existing or extraction damage.
- `extraction/worker.py` was dropped; it depended on event-sourcing aggregates and the
  event store. Its re-exports were removed from `kg_builder.extraction.__init__`, as was
  its test module.
- `config.py` and `db.py` are verbatim copies of the application's versions and still
  carry OAuth/JWT settings and FastAPI-oriented session helpers this library does not need.
- `services/__init__` no longer re-exports `tenant_context` / `app_token_service`.
- `scraping.pipelines` no longer dispatches Celery directly; register a callable via
  `kg_builder.scraping.hooks.set_extraction_dispatcher`.
- `ruff check` reports ~617 pre-existing lint findings, untouched.
- `pydantic-ai` is pinned to `==0.0.31` and `documents` (`unstructured`) is excluded from
  the `all` extra — it pulls numba/llvmlite, which do not build on Python 3.13.
