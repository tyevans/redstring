# redstring

Build a knowledge graph out of documents you already have: extract entities and
relationships with a language model, consolidate duplicates, and keep a graph store
in step with the result.

You supply the documents. This library does not fetch anything.

## Install

```
uv add redstring                 # in-memory adapters, the fake provider
uv add "redstring[llm]"          # any OpenAI-compatible server, via langchain-openai
uv add "redstring[neo4j]"        # the Neo4j GraphStore adapter
```

Python 3.13+.

`redis` is a **core** dependency, not an extra: `pyproject.toml`'s `dependencies`
carries `redis[hiredis]>=5.3,<6`, so `redstring.llm.cache.redis.RedisCache` works on
the base install with nothing added. That may change — `BACKLOG.md` B61 proposes moving
`redis` behind an extra (the only remaining user is that one adapter, which imports
`redis.asyncio` inside a function), and B38 records the pin conflict that makes the
question live: `eventsource-py[all]` wants `redis>=8.0,<9.0`, which this range excludes.
If you change extras yourself, re-sync with `--all-extras` afterwards — `uv add` and
`uv remove` re-resolve and will silently narrow the venv (B45).

## Use

```python
from redstring import InMemoryGraphStore, SourceDocument, build_graph
from redstring.llm.adapters.langchain import LangChainLlmProvider
from langchain_openai import ChatOpenAI

# Any OpenAI-compatible server: llama.cpp, vLLM, Ollama's shim, OpenAI itself.
chat_model = ChatOpenAI(model="qwen3-30b", base_url="http://localhost:8080/v1", api_key="-")
store = InMemoryGraphStore()

report = await build_graph(
    SourceDocument(id="notes", text="Ada Lovelace worked with Charles Babbage."),
    provider=LangChainLlmProvider(chat_model),
    store=store,
    tenant_id=tenant_id,
)

print(report.domain, report.domain_confidence)  # which prompt ran, and how sure

people = await store.find_entities(tenant_id, entity_type="Person")
neighbours = await store.neighbors(people[0].id, tenant_id)
```

`docs/examples/build_a_graph.py` is the same composition, complete and runnable against
`FakeLlmProvider` — no server, no extra. It is executed by the test suite on every
commit, so it cannot go stale. Constructing the chat model above is the one step it does
not show, because it is the one step that is langchain's rather than this library's.

Pass `domain="literature_fiction"` to specialise the prompt to one of the bundled domain
schemas, or `domain=AUTO` to have the content classifier choose (one extra model call).
`AUTO` is the sentinel exported from `redstring` — `from redstring import AUTO` — not
the string `"auto"`, which is read as a domain id like any other.

**`AUTO` never raises.** It falls back to `encyclopedia_wiki` on three paths: a document
under 100 characters is not sent to the classifier at all, an answer below the confidence
threshold is replaced, and an `LlmProviderError` from the classifier call is caught. A
missing or unclassifiable document therefore costs you the default-ish prompt, not a
failed build.

Reading the result: `report.domain` is the domain whose prompt was used, and `None` means
the default prompt ran. `report.domain_confidence` is `None` when no classifier ran —
including every call that named its own `domain`, so filtering on `== 0.0` does not sweep
those up — and `0.0` specifically when the classifier gave up and fell back. A give-up is
**indistinguishable from a real choice by `report.domain` alone**: all three fallback
paths report `"encyclopedia_wiki"`, exactly as a confident classification of an
encyclopedia article would. The confidence is the only field that tells them apart.

## The public API

`from redstring import ...` — everything in that module's `__all__` is supported.
Anything reached through a dotted path is internal and may change. The module docstring
is the reference, and it says what is deliberately left out.

The surface is **closed**: every type an exported signature names is either exported too
or recorded with the package it comes from, and every `RedstringError` is either exported
or recorded as belonging to a capability that is not. That is a test
(`tests/unit/test_public_surface_is_self_contained.py`), not an intention. ADR
`docs/adr/0006-the-public-surface-is-gated.md` records why it is gated rather than curated.

Composition is exported in full: `build_graph`, `GraphBuildReport`, `AUTO` and `AutoDomain`
are all in `__all__`, so the snippet above needs no dotted path.

**Consolidation is not exported**, and the module docstring's "What is deliberately not
here" is the reason: consolidation and temporal inference are both real
(`redstring.consolidation`, `redstring.temporal`) and both tested, but neither has a
composed entry point yet, and exporting the classes would publish an API whose shape is
still being decided by the callers it does not have. So the consolidation how-to reaches
its entry point by dotted path, deliberately:

```python
from redstring.consolidation.service import ConsolidationService
```

Import it by path and expect movement. See
`docs/how-to/consolidate-duplicate-entities.md`.

### Not exported: the resilience stack and the caches

The same wording applies to the middleware around model calls. None of these are in
`__all__`, and all of them are reachable only by dotted path:

| Module | What it holds |
|---|---|
| `redstring.llm.retry` | `with_retry`, `ExtractionRetryPolicy`, `RetryExhausted` |
| `redstring.llm.rate_limiter` | `RateLimiter`, `RateLimitExceeded` |
| `redstring.llm.circuit_breaker` | `CircuitBreaker`, `CircuitState`, `CircuitOpen` |
| `redstring.llm.cache.memory` | `MemoryCache` — the default, no infrastructure |
| `redstring.llm.cache.redis` | `RedisCache`, `RedisCache.from_url` — for processes that must agree |

They are real and they are tested; what they do not have is a composed entry point, so
their shape is still being decided by callers they do not yet have. Reaching for them
means taking on **movement without notice** — a rename, a changed signature, or a move to
another module is not a breaking change here, because nothing in the promise covers them.
`CircuitOpen` and `RateLimitExceeded` are `RedstringError` subclasses that the public
surface deliberately does not export, and `tests/unit/test_public_surface_is_self_contained.py`
records both as middleware rather than letting the omission pass unnoticed.

`RedisCache` needs no extra — `redis` is a core dependency (see Install) — but it does
need a Redis; `MemoryCache` is what every one of these defaults to.

## How it fits together

```
SourceDocument            -> ExtractionPipeline    -> DocumentExtracted     -\
                                                                             >- GraphProjection -> GraphStore
ConsolidationService.merge/resolve/undo -> EntitiesMerged | MergeUndone     -/
```

There are two producers and one projection. Extraction **emits an event and writes
nothing**; consolidation is the same shape — it reads the graph to work out what a merge
would do, records that as an `EntitiesMerged` (or a `MergeUndone`), and writes to no store
of its own (`consolidation/service.py`'s module docstring; `docs/adr/0004-consolidation-emits-events.md`).
Both therefore fold through `GraphProjection`, which handles all three events, and the
store stays a projection of the log rather than a second source of truth. `build_graph` does both in one call for a caller who has no event store; a caller
who has one appends `report.event` and drives `redstring.projections.project` over the
feed instead. That separation is why a store can be rebuilt from the log.

### Consolidating duplicates

`ConsolidationService` (`redstring.consolidation.service`, dotted path — it is not
exported) has two ways in.

`resolve(subject, *, finder, adjudicator=None, high=..., low=...)` is the whole pipeline
in one call: **block** (the finder's candidates), **score** them, **band** each score
against `low` and `high`, **adjudicate** the band with one batched model call, and emit a
single `EntitiesMerged` covering everything that came out a merge. `subject` becomes the
canonical entity, so choosing which duplicate you pass is choosing which one survives.
Returning `None` is the ordinary outcome, not a failure — it means nothing was decided
worth merging. Without an `adjudicator` the band is **rejected, not merged**.

`merge(*, tenant_id, canonical_entity_id, merged_entity_ids, merge_reason=None)` is the
explicit path for when you have already decided, by hand or by some judgement of your own.
It skips blocking, scoring and adjudication entirely and emits the same `EntitiesMerged`.
`resolve` ends by calling it.

Both return the event, and its `event_id` is what reverses the merge:
`undo(*, tenant_id, merge_event_id)` **takes the merge's event id and nothing describing
what to restore.** The aggregate rehydrates its own merge history and writes the
restoration into `MergeUndone` itself — there is no graph read at all — because a caller
supplying what to restore would be a caller able to restore something that never happened.
An id naming no merge in effect raises `UnknownMergeError`, which covers "never happened"
and "already undone" as one case.

`docs/how-to/consolidate-duplicate-entities.md` is the end-to-end recipe, from a populated
graph through a merge to its undo. `docs/adr/0004-consolidation-emits-events.md` records
why this emits an event instead of writing to the store, and what collapsing the two would
cost.

### Tuning

Four constants decide how aggressive consolidation is. All four are module-level, all four
are dotted-path (nothing here is exported):

| Knob | Module | Default | What it does |
|---|---|---|---|
| `HIGH_SIMILARITY` | `redstring.consolidation.policy` | `0.92` | At or above, merge without asking a model |
| `LOW_SIMILARITY` | `redstring.consolidation.policy` | `0.75` | Below, never merge and never ask |
| `ADJUDICATION_BATCH_SIZE` | `redstring.consolidation.policy` | `10` | How many pairs go into one model call |
| `EMBEDDING_SEARCH_K` | `redstring.consolidation.candidates` | `50` | How many nearest vectors the embedding step asks for |

The first two are **per-call keyword arguments**, not just constants: `resolve(subject, *,
finder, adjudicator=None, high=HIGH_SIMILARITY, low=LOW_SIMILARITY)` takes both, so tuning
one run does not need a module edit. They are named constants rather than bare defaults
because the two values are only meaningful relative to each other, and overriding one of
them is how the band between them silently disappears. `decide` raises `ValueError` when
`low > high` rather than quietly emptying the band. Both bounds are inclusive from below:
`score == high` merges, `score == low` adjudicates.

**Without an `adjudicator`, the band is rejected, not merged.** The band exists precisely
because the score does not settle those pairs, so treating "nobody asked" as a yes would
merge exactly the pairs the model was there to protect. Narrowing the band — raising `low`
or lowering `high` — is therefore not the same knob in both directions when no adjudicator
is configured.

The other two are call-cost bounds. `ADJUDICATION_BATCH_SIZE` is what keeps the band
affordable; the ceiling exists because verdicts are re-paired **by position**, and a long
batch is where a model starts losing track of which answer belongs to which pair.
`EMBEDDING_SEARCH_K` does not decide which candidates are scored — the block does — it only
bounds how far down the ranking an embedding score is still found. A candidate in the block
but outside the top `k` gets *no* embedding feature rather than a zero, and `combined_score`
renormalizes over the features it has.

### Package table

| Package | What it is |
|---|---|
| `redstring.composition` | `build_graph` — the only module that holds both halves |
| `redstring.domain` | `Entity`, `Relationship`, `Alias`, similarity, temporal parsing |
| `redstring.ports` | `GraphStore`, `VectorStore`, `LlmProvider` |
| `redstring.graph` / `.vector` | Adapters: in-memory, Neo4j, pgvector |
| `redstring.llm` | Provider adapters, retry, rate limiting, circuit breaking, caching — only the adapters (`llm.adapters.langchain`, `llm.adapters.fake`) are part of the promise; the rest is internal, dotted-path only |
| `redstring.extraction` | Chunking, the pipeline, mapping, merging, domain prompting |
| `redstring.consolidation` | Deciding that two entities are one and undoing it: the `resolve()` block/score/band/adjudicate pipeline, the explicit `merge()`/`undo()`, emitting `EntitiesMerged` / `MergeUndone` and writing to no store |
| `redstring.temporal` | Interval inference and time-sliced queries |
| `redstring.aggregates` / `.events` / `.projections` | The write model and the read model |

Implementing a port of your own? `tests/compliance/` is a suite you can point at it;
it is what says whether you got the contract right.

## Development

### Setup

```
uv sync --all-extras && uv run pre-commit install
```

### Quality gates run on commit

Every quality gate — ruff, mypy `--strict`, bandit, the layered import contract, and
pytest under a coverage ratchet — runs on `git commit`; there is no separate step to run.
`docs/reference/quality-gates.md` lists what each one checks.

### Running the integration suite

```
docker compose -f docker-compose.test.yml up -d neo4j postgres
KG_COMPLIANCE_MAX_EXAMPLES=10 uv run pytest -m integration
```

Three things about that pair of lines.

**Start the backends explicitly.** Nothing starts them on demand, and `up -d` returns
when the container is *running*, not when the server can serve — which is why both
services carry a healthcheck (`cypher-shell 'RETURN 1'`, `pg_isready -U postgres -d
kgbuilder_test`). Name only the service you need: `up -d neo4j` for the graph tests,
`up -d postgres` for pgvector.

**`-m integration` is mandatory.** `pyproject.toml` sets
`addopts = ["-m", "not accuracy and not integration"]` so the commit gate needs no
infrastructure, and a CLI `-m` is what overrides it. Without the flag the suite is
deselected and pytest reports success having run none of it.

**`KG_COMPLIANCE_MAX_EXAMPLES=10`** is the documented prefix: the compliance properties
default to 50 examples each, which is a long wait against a real server.

Compose supplies **Neo4j and pgvector only** — no LLM. Most of the suite needs neither.

### What each integration test needs

Nothing under `-m integration` needs all three backends, so bringing up one container and
running the whole marker is a legitimate thing to do.

| Test | Needs | Without it |
|---|---|---|
| `tests/integration/graph/test_neo4j_store.py` | the `neo4j` service only | skips |
| `tests/integration/vector/test_pgvector_store.py` | the `postgres` service only | skips |
| `tests/integration/llm/test_live_endpoint.py`, `tests/integration/llm/test_live_pipeline.py` | a live OpenAI-compatible server — **the only two that do** | skips |
| `tests/integration/test_wheel_ships_the_domain_schemas.py` | neither — it builds a wheel and installs it into a throwaway venv | always runs |

The two store files skip on a **probe that does real work**, not on a connection: Neo4j is
asked for `RETURN 1` and Postgres is made to create the `vector` extension and store a row,
because a server still recovering its store files accepts connections and authenticates,
and a Postgres without the extension accepts them too and then cannot hold a single vector.
The LLM probe asks for a completion and requires non-empty content back for the same
reason — `BACKLOG.md` B12 is the standing example of the weaker check, where a model listing
said yes, the weights would not load, and eight tests failed instead of skipping.

The LLM pair reads `KG_LLM_BASE_URL` and `KG_LLM_MODEL`; compose supplies no model, so
these are the tests you point at a server of your own.

The wheel test is the one with no backend and no skip. It is marked `integration` because
it costs seconds rather than milliseconds — `uv build --wheel`, a fresh venv, an install,
and then `domain_system_prompt` asked for all six bundled domains from `site-packages`
rather than the checkout. It needs the network only if `uv` has to fetch a build backend it
has not cached. Run it before a release.

### Pointing the suite at your own backends

Four environment variables, all read at import time with the compose file's values as
defaults — so with nothing set, the suite and `docker-compose.test.yml` already agree.

| Variable | Default | Read by |
|---|---|---|
| `KG_TEST_NEO4J_URI` | `bolt://localhost:7688` | `tests/integration/graph/test_neo4j_store.py` |
| `KG_TEST_NEO4J_USER` | `neo4j` | same |
| `KG_TEST_NEO4J_PASSWORD` | `kgbuilder` | same |
| `KG_TEST_POSTGRES_DSN` | `postgresql://postgres:kgbuilder@localhost:5434/kgbuilder_test` | `tests/integration/vector/test_pgvector_store.py` |

Postgres is **one variable, not three**: everything — user, password, host, port and
database — travels in the DSN, and the database name matters, since the pgvector tests
expect a database the `vector` extension can be created in. Neo4j is split into three
because the driver takes the auth pair separately from the URI.

The ports are **7688 and 7475 for Neo4j and 5434 for Postgres**, not the defaults
7687/7474 and 5432. That is deliberate, and the compose file says why: they avoid
colliding with a local install of either server, and with another project's test
containers — 5433 is skipped too, for the same reason. A test run therefore cannot
silently talk to your development database, and you can leave a local Neo4j running while
the suite works.

7475 is Neo4j's HTTP browser, published for poking at the container by hand; no variable
reads it, because the tests connect over Bolt only. The Neo4j credentials above are
`NEO4J_AUTH: neo4j/kgbuilder` from the compose file — change one and you must change the
other.

Point these at a scratch backend and nothing else. The Neo4j suite resets with a real
`MATCH (n) DETACH DELETE n` — **the whole database, not one tenant** — because the
compliance properties generate their own tenant ids and `new_store()` never learns them,
so there is nothing to scope the wipe to. The Postgres suite truncates and drops its
tables, and needs a database in which `CREATE EXTENSION vector` succeeds.

### Serial only

No `-n auto` (`BACKLOG.md` B10f), and not in the same invocation as the unit suite
(B10m). `docs/how-to/run-integration-and-mutation-suites.md` is the full recipe,
including the mutation runs.

### Test markers

Three markers are declared in `pyproject.toml`: `unit`, `integration`, `accuracy`.
`addopts = ["-m", "not accuracy and not integration"]` deselects the last two, which is
what keeps the commit gate infra-free and fast; a CLI `-m` is what overrides it, one
suite at a time.

`accuracy` **has no suite**. `tests/accuracy/` is an empty package — slice 6 deleted its
only file along with the service it measured — so `uv run pytest -m accuracy` collects
zero tests and reports success. That is the marker's whole current meaning: a name kept
so the gap stays visible. `BACKLOG.md` B12 says what closing it takes, and the expensive
part is a hand-annotated corpus, not the harness. Nothing else here measures whether
extraction is any *good*: correct and accurate are different properties, and a pipeline
can satisfy every invariant while finding the wrong entities.

(`slow` is declared too, and is likewise unused.)

## Further reading

`BACKLOG.md` is the index of everything known and not fixed, grouped by what a reader
would search for — wrong answers, unverified claims, performance, the test suite itself,
capabilities deliberately not built, and tooling. Its `B` numbers are stable handles
cited from `src/` and `tests/`, so a comment naming `B10f` resolves to a real entry.
Anything deferred lands there in the same commit that passes it by.

`docs/plans/ring-migration.md` is the history: what this library was before it became a
library, which commit range rebuilt it, and where the deleted parts can be recovered
from. It also indexes the closed backlog entries that shipped code still cites, so those
pointers keep resolving after the entry itself is gone.

`docs/adr/` holds the decisions that are expensive to revisit — among them why
consolidation emits an event rather than writing to a store
(`0004-consolidation-emits-events.md`), why there are two store ports
(`0002-two-store-ports.md`), why temporal relations are inferred on read
(`0005-temporal-inference-on-read.md`), why the public surface is gated rather than
curated (`0006-the-public-surface-is-gated.md`), and why `composition` is the only top
layer (`0007-composition-is-the-only-top-layer.md`).

The task-shaped guides live in `docs/how-to/`. The two this page points at repeatedly:

- `docs/how-to/consolidate-duplicate-entities.md` — a populated graph through blocking
  and scoring, a merge you can audit, and its undo.
- `docs/how-to/run-integration-and-mutation-suites.md` — the full commands for
  everything the commit gate deliberately leaves out, integration and mutation both.

The rest are worth knowing exist: authoring a domain schema, driving projections from an
event store, hardening model calls, implementing a store adapter, querying a timeline,
rebuilding a projection, and using the write model.

`docs/reference/quality-gates.md` is the per-gate reference — what each hook checks, the
configuration it reads, and why running it by hand duplicates work. `docs/reference/`
also documents the events, the aggregates, the domain value types, the domain-schema
YAML, and the Neo4j store.
