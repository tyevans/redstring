# kg-builder re-architecture plan

Turn kg-builder into a library that constructs knowledge graphs, with
pluggable graph and vector storage — and nothing else. Clean breaks only:
no deprecation shims, no compatibility re-exports left behind.

This supersedes the original ring-migration plan (commits `edced18`,
`cf64765`), which assumed the existing package set was worth relocating.
Slices 0 and 0b from that plan are done and still stand; everything after
them is replaced.

## Global Constraints

These bind every slice. Implementers and reviewers are held to all of them.

### Testing

1. **Red/green TDD, no exceptions.** Write the test, *run it, watch it fail
   for the right reason*, then write the minimal code to pass. A test that
   passed the first time it ran proves nothing — delete it and start over.
   Report the observed failure message, not just "tested".
2. **Property-based tests with `hypothesis` wherever a property is easier to
   state than a table of examples.** Mandatory, not optional, for:
   - the store port-compliance suites (any valid sequence of writes then
     reads must round-trip; tenant isolation must hold for all inputs)
   - normalization and parsing (idempotence: `f(f(x)) == f(x)`)
   - similarity and scoring (bounds, symmetry, identity)
   Prefer a stated property over five hand-picked examples.
3. **Mutation testing on new domain and port logic — with cosmic-ray, not
   mutmut, wherever the logic is decorated.** mutmut 3.x refuses to mutate
   decorated functions. In a pydantic codebase nearly every invariant lives
   in a `@field_validator`, `@model_validator`, or `@property`, so mutmut
   reports a clean sweep while having tested none of them. Slice 2 proved
   this: 5 mutants generated, 5 killed, and all 5 came from the one
   undecorated function in the package.
   Use `cosmic-ray.toml` (already in the repo, kept for exactly this reason
   per `CLAUDE.md`) for decorated code; mutmut is fine for plain functions.
   Surviving mutants are findings: either the tests are too weak or the code
   is unreachable. Report the survivor count and what each survivor revealed.
   Do not chase 100% — chase "every survivor is understood". If a mutation
   tool cannot be scoped in reasonable time, say so plainly in the report;
   a silent skip turns the gate into theatre.
4. **No mocking what you own.** The in-memory adapters exist precisely so
   tests can use real implementations. Mock only genuinely external I/O
   (an HTTP call to an LLM). A test that asserts on a mock's call args and
   nothing else is a defect.
5. Tests must fail for exactly one reason. `pytest-randomly` randomises
   order; order-dependent tests are bugs to fix, never seeds to pin.

### Process

6. **The `pre-commit` hook is the gate.** Do not run ruff, bandit,
   lint-imports, or the full pytest suite as separate pre-commit steps —
   write the change and commit. Targeted `pytest <file>` during the TDD loop
   is expected and encouraged.
7. **Deferred work goes in `BACKLOG.md` in the same commit that defers it**,
   with the reasoning that made deferring right. Delete an entry in the
   commit that resolves it. Hard project rule (`CLAUDE.md`).
8. **Clean breaks.** No deprecation shims, no compatibility re-exports, no
   "keep the old path working just in case". Delete and move on.
9. **Never edit `pyproject.toml` dependency tables by hand** — use
   `uv add`, `uv add --optional <extra>`, `uv remove`.
10. **Coverage may not fall.** The ratchet enforces it. A deliberate drop
    requires editing `.coverage-baseline` in the same commit, with the
    reason in the message.
11. Small commits. Every hook run stays fast; every commit stays reviewable.

## What kg-builder is

**Given content, produce a knowledge graph.** Entity and relationship
extraction, entity consolidation, temporal enrichment, embeddings.

**The event log is the write model. The graph and vector stores are
projections.** Extraction, consolidation, and temporal enrichment emit
domain events; projections fold those events into a `GraphStore` and a
`VectorStore`. Both stores are derived and disposable — rebuildable by
replay.

This is the property that matters most: **LLM extraction is the slow,
expensive, non-deterministic step.** Once its results are events, you can
re-run consolidation with new thresholds, a new blocking strategy, or a new
merge policy by replaying the log — without paying for extraction again.
Backend swaps and graph rebuilds fall out of the same mechanism, and they
independently prove the store ports.

`eventsource-py` supplies the event store, bus, and projection machinery
(checkpoints, DLQ, replay). It is already a dependency: `context.py`
re-exports `eventsource.multitenancy`, `events/base.py` re-exports
`TenantDomainEvent`, and four modules call `register_event`.

**What we take from it, and what we don't.** Take the event store, the event
bus, and the projection system. **Skip the aggregate pattern** — a document
yielding ten thousand entities is not ten thousand transactional aggregates
with optimistic locking, and forcing that shape onto bulk extraction would
be a serious mistake.

## What kg-builder is not

Three scope cuts, decided deliberately:

| Not this | Why | Consequence |
|---|---|---|
| **A document sourcer** | Fetching, crawling, and cleaning source content is a different problem set | `scraping/` deleted; HTML preprocessors deleted; document parsing and object storage deleted |
| **An application** | Job tracking, review queues, and credential storage belong to the caller | `models/` deleted; the bespoke relational schema goes; provider config is passed in, not read from a DB |
| **An auth boundary** | It never was one | `User`/`Tenant`/`OAuthProvider` and the `OAUTH_*`/`APP_JWT_*` config deleted; `tenant_id` survives as a scoping key |

The library does not fetch anything. Callers hand it content they already
have.

**One nuance on state.** The library owns the event log — that is the whole
point of the write model. What it does not own is a bespoke relational
schema for jobs, queues, and credentials. The event store is reached through
`eventsource-py`'s port, so the caller still chooses the backend (in-memory,
SQLite, PostgreSQL) and the library remains runnable with no infrastructure
at all. SQLAlchemy therefore returns as a transitive dependency of the
event-store adapter — not as this library's persistence layer.

## Input contract

A caller-supplied value object, not a row the library owns:

```python
SourceDocument(
    id: str,                    # caller's identifier, used for provenance
    text: str,                  # already extracted and cleaned
    uri: str | None = None,
    title: str | None = None,
    published_at: datetime | None = None,
    metadata: dict = {},
)
```

Provenance becomes graph structure — `(:Entity)-[:EXTRACTED_FROM]->(:Source)`
— built from `SourceDocument`, never fetched.

## Ports

Two that matter. Both are defined at the level the library actually uses, so
they stay implementable by something that is not Neo4j.

```
GraphStore
  upsert_entity / upsert_entities
  upsert_relationship / upsert_relationships
  get_entity, find_entities(name?, type?, tenant)
  neighbors(entity_id, depth, rel_types?)
  merge_entities(canonical, others)      # alias edges + provenance
  delete_by_tenant(tenant)

VectorStore
  upsert(entity_id, vector, tenant, metadata)
  search(vector, k, tenant, filter?)     # ANN
  get(entity_id, tenant)
  delete(entity_id | tenant)
```

Supporting ports, each with a default that needs no infrastructure:
`LlmProvider`, `EmbeddingProvider`, `Cache` (in-memory default; Redis
optional), `Clock`.

The event store, event bus, checkpoint, and DLQ ports come from
`eventsource-py` — we do not redefine them. `GraphStore` and `VectorStore`
are written by projection handlers subscribed to the log, never by
extraction or consolidation directly.

```
SourceDocument
      |
   extraction / consolidation / temporal
      |  emit
      v
  [ event log ]  <- the write model, the only authority
      |  project
      +--> GraphStore    (derived, disposable, rebuildable)
      +--> VectorStore   (derived, disposable, rebuildable)
```

**The port must not leak Cypher.** `graph/queries.py` is Cypher templates
today; they become Neo4j adapter internals, never port vocabulary.

## Adapters

| Port | First | Then | Later |
|---|---|---|---|
| `GraphStore` | in-memory | Neo4j | SQL (sqlite/postgres) |
| `VectorStore` | in-memory | pgvector | Qdrant |

In-memory is the reference implementation and comes first, because it keeps
the port honest and gives the test suite a real backend for the first time
(BACKLOG B10). Every adapter runs the **same port-compliance test suite** —
that shared suite is the deliverable, not any one adapter.

SQLAlchemy does not vanish entirely: it survives *inside*
`vector/adapters/pgvector.py` and a future `graph/adapters/sql.py`, as an
optional extra. It stops being the library's persistence layer.

## Target layout

```
src/kg_builder/
  domain/           Entity, Relationship, Alias, SourceDocument, temporal
                    value objects, domain events. Pure; no I/O, no ORM.
  ports/            GraphStore, VectorStore, LlmProvider, EmbeddingProvider,
                    Cache, Clock.
  graph/adapters/   memory, neo4j, (sql)
  vector/adapters/  memory, pgvector, (qdrant)
  llm/              provider adapters, retry, rate limiting, circuit breaker
  extraction/       entity/relationship extraction; chunking and chunk-merge
  consolidation/    blocking, similarity, merge
  temporal/         parsing, inference, timelines
  pipelines/        the composed use cases; the public API
```

`preprocessing/` splits: HTML preprocessors are sourcing and go; the
sliding-window chunker and the entity mergers serve extraction and move into
`extraction/`.

## What gets deleted

| Path | LOC | Why |
|---|---|---|
| `scraping/` | 1,707 | Sourcing |
| `models/` | 3,992 | The library owns no relational state |
| `services/document_parser.py`, `services/storage/` | 630 | Sourcing |
| `preprocessing/preprocessors/` | ~400 | HTML boilerplate removal is sourcing |
| `db.py`, most of `config.py` and `encryption.py` | ~1,074 | No DB, no stored secrets |
| `services/sync_status.py` | — | Nothing to sync; the graph store is the store |

Roughly **7,800 lines deleted outright**, before counting what `services/`
sheds. Dependencies dropped from the core: `scrapy`, `trafilatura`,
`extruct`, `beautifulsoup4`, `boto3`, `unstructured`, `asyncpg`,
`psycopg2-binary`, and `sqlalchemy` (demoted to an optional extra).

`extraction/llm_extractor.py` imports `unstructured` — document parsing
inside extraction. Same boundary violation; extraction takes text.

## LLM strategy

Ollama-specific and OpenAI-specific extraction code goes. LangChain supplies
chat and embedding models; LangGraph orchestrates multi-step extraction;
deepagents provides an agentic extraction mode. `pydantic-ai` leaves
entirely, which resolves BACKLOG **B19** (the `==0.0.31` pin) and **B2**
(the Anthropic stub) by deletion.

### Verified test endpoint

`http://192.168.1.14:8080/v1` — OpenAI-compatible, confirmed serving:

| Model | Role | Verified |
|---|---|---|
| `qwen3.6-27b-mtp` | chat | `POST /v1/chat/completions` → 200 |
| `nomic-embed-text` | embeddings | `POST /v1/embeddings` → 200, **dim 768** |

Unlike the Ollama box — which lists `gpt-oss:20b` but cannot load it
(BACKLOG B12) — this endpoint actually serves, so **B12 becomes fixable**
and extraction accuracy can be measured for the first time.

Note: `qwen3.6-27b-mtp` returned 200 with an empty
`choices[0].message.content` on a trivial prompt. Confirm the response shape
before wiring it; do not assume `.content` is populated.

### Ports: LangChain goes behind them, not through them

```
LlmProvider.extract(text, schema, *, model?) -> structured result
EmbeddingProvider.embed(texts) -> list[vector]
EmbeddingProvider.dimension -> int
```

LangChain's `BaseChatModel` and `Embeddings` are **adapter
implementations**, never the port. `domain/` must import nothing from
`kg_builder` beyond `domain/`, and certainly not an LLM framework — leaking
`AIMessage` into a domain type would undo the layering. LangChain's
interfaces also move fast; a breaking change should touch one adapter.

### Embedding dimension is configuration, not a constant

`nomic-embed-text` is 768-dimensional. The codebase hardcodes 1024 (bge-m3)
at `config.py:228` and — worse — bakes it into the pgvector **column type**
at `models/extracted_entity.py:385` as `Vector(EMBEDDING_DIMENSION)`.

`VectorStore` therefore takes dimension as configuration and **rejects
vectors whose length disagrees with it**. A dimension mismatch is a silent
correctness catastrophe that surfaces only as poor search results, so the
in-memory adapter must enforce the same check pgvector would — otherwise the
compliance suite passes on mixed dimensions that a real store rejects.
Switching embedding models means a new collection, not an in-place change.

### `ExtractionMethod` must stop naming vendors

`domain/entity.py` currently carries `LLM_OLLAMA`, `LLM_OPENAI`,
`LLM_CLAUDE`, copied from the ORM. That is the wrong abstraction once
providers are LangChain adapters: the domain cares *how* an entity was
derived, not which vendor answered.

```
ExtractionMethod: LLM | PATTERN | SCHEMA_ORG | OPEN_GRAPH | HYBRID | MANUAL
```

with the concrete model recorded alongside as provenance
(`model: str | None`, e.g. `"qwen3.6-27b-mtp"`) — strictly more useful, since
it survives model upgrades and supports "re-extract everything the old model
touched". **This must land before slice 5b freezes the event schemas**: an
`ExtractionMethod` inside a persisted event is permanent.

### LangGraph: scoped, or it competes with the event log

LangGraph fits *within-run* orchestration — chunk → extract → merge → resolve
is genuinely a stateful graph, and that logic is hand-rolled today. The
boundary that must hold:

- **LangGraph state is ephemeral working state for one extraction run.**
- **The event log is durable truth.**

Do not use LangGraph's checkpointer as a second durable store; that
reintroduces exactly the dual-write problem this re-architecture exists to
remove. A run that dies is re-run, not resumed from a competing checkpoint.

### deepagents: a second mode, not the default path

Agentic extraction — plan, use tools, iterate — is slower, costlier, and less
reproducible than deterministic bulk extraction. Those are the right
trade-offs for some documents and the wrong ones for a million. It arrives
behind the same `LlmProvider` port, selected per run, *after* the
deterministic path works end to end. It must not become the default by
accident.

## Consolidation design

Consolidation is the part with the most real design work in it. It is not a
portability problem to be worked around — it gets built properly.

### Blocking

Blocking generates candidate pairs worth scoring. Today's four strategies are
Postgres implementations of two different ideas:

| Strategy | Really is | Ports as |
|---|---|---|
| `PREFIX` | key function | pure function on the entity |
| `ENTITY_TYPE` | key function | pure function on the entity |
| `SOUNDEX` | key function | pure function — `compute_soundex` is already Python (jellyfish); only the *lookup* used a Postgres generated column |
| `TRIGRAM` | similarity search | ANN via `VectorStore`, or a store-native capability |

So the port is two capabilities, not four:

```
BlockingKeyStrategy          # pure: entity -> set[key]
GraphStore.find_by_blocking_key(key, tenant)
```

Key-based blocking becomes pure domain logic that every adapter supports by
indexing keys. Fuzzy blocking goes through `VectorStore.search`, which is
where approximate matching already belongs. An adapter with a native fuzzy
index may override, but nothing depends on it having one.

### Merge strategies

`PropertyMergeStrategy` currently has five members applied per-property:
`PREFER_CANONICAL`, `PREFER_MERGED`, `UNION`, `LATEST`, `DEEP_MERGE`.

The *abstraction* ports now; most of the *implementations* are deliberately
deferred:

- **Implement now:** `PREFER_CANONICAL` as the default, and `UNION` for
  aliases — merging inherently produces alias sets, so that one is
  structural rather than optional.
- **Defer:** `PREFER_MERGED`, `LATEST`, `DEEP_MERGE`. `LATEST` needs a
  reliable updated-at on every property source; `DEEP_MERGE` needs nested
  dict semantics that are easy to get subtly wrong and hard to undo. Neither
  earns its complexity before there is a caller asking for it.

The port is shaped to accept the others later without redesign:

```
MergeStrategy:  resolve(property, canonical_value, other_values) -> value
```

Deferred strategies raise `NotImplementedError` with a message naming the
backlog entry, rather than silently degrading to a default.

### Merge undo

`undo_merge` and `split_entity` both exist today and both need to know what
a merge did.

With the event log as the write model, this stops being a storage question.
A merge is an `EntitiesMerged` event; undoing it is a compensating
`MergeUndone` event, and the projection folds both. The pre-merge state is
recoverable from the log — that is what a log is for. No history table, and
no displaced-value payload smuggled onto an alias edge either.

Note for slice 5b: slice 2's `Alias` type carries a `displaced` dict, added
when undo was still a storage problem. Once undo is a compensating event
that field is redundant with the log. Decide there whether the projection
still wants it as a read optimisation, and delete it if not.

## Slices

Slices 0 and 0b are complete. Dispatch the rest one at a time.

| # | Slice | Gate |
|---|---|---|
| 0 | **Stabilize.** ✅ 1798 passed, coverage baseline 60.79. | ✅ |
| 0b | **Land the temporal/strategy-router work.** ✅ 1930 passed, nothing skipped. | ✅ |
| 1 | **Scope cut.** Delete `scraping/`, document parsing, object storage, HTML preprocessors, and their tests and dependencies. Remove `unstructured` from `llm_extractor`. Pure deletion — no new abstractions, no new tests. Constraint 1 (red/green TDD) does not apply: nothing is being built. The gate is that the surviving suite is green, no test is orphaned or weakened to accommodate a deletion, and no surviving module imports a deleted one. | Full suite green; no orphaned tests; coverage baseline reset with justification |
| 2 | **Domain model.** `Entity`, `Relationship`, `Alias`, `SourceDocument`, temporal value objects as pure types. `DatePrecision`/`UncertaintyMarker` land here (BACKLOG B26). | Unit tests; no I/O in `domain/` |
| 3 | **`GraphStore` port + in-memory adapter + compliance suite.** The compliance suite is the real artifact. | Compliance suite green against memory |
| 4 | **Neo4j adapter.** Same compliance suite, no new tests of its own beyond Cypher specifics. | Compliance suite green against Neo4j |
| 5 | **`VectorStore` port + in-memory + pgvector adapters.** | Compliance suite green against both |
| 5b | **Event-sourcing foundation.** Rebuild `events/` on the domain model — the 67 existing classes are the raw material, but they are shaped for the old ORM and none is emitted today. Wire `eventsource-py`'s store and bus. Build the projection handlers that fold events into `GraphStore` and `VectorStore`, with checkpoints and DLQ. **Prove replay:** a test that projects a log into an empty in-memory store and gets a byte-identical graph is this slice's real deliverable. Skip the aggregate pattern. | Replay-equivalence test green; compliance suites still green |
| 6 | **Extraction onto the domain model.** Absorb chunkers and mergers. Provider config passed in, not loaded. Extraction **emits events** rather than writing to a store. | Targeted + `lint-imports` |
| 7 | **Consolidation onto the ports.** Key-based blocking as pure domain logic, fuzzy blocking via `VectorStore.search`; `MergeStrategy` port with the simple implementations only. Merges emit events; **undo becomes a compensating event, not displaced values on an edge** — see "Consolidation design". Prove it: merge, undo, and assert the projection matches the pre-merge graph. | Targeted + `lint-imports` |
| 8 | **Temporal onto the ports.** | Targeted + `lint-imports` |
| 9 | **Delete the relational layer.** `models/`, `db.py`, `sync_status`, SQL in `timeline_query`/`vector_ops`; trim `config.py` and `encryption.py`. | Full gate |
| 10 | **`pipelines/` + public API.** The composed use cases and a deliberate `kg_builder/__init__.py`. | Full gate |
| 11 | **Docs & meta.** ADR 0001 (this architecture), README rewrite, CHANGELOG with the breaking paths, `CLAUDE.md` structure block, import-linter contract rewrite. | Sweep clean |

Slice 1 is pure deletion and should land first — every later slice is
cheaper against a smaller tree.

## Risks

- **Port leakage.** If the in-memory adapter turns out to be much harder than
  Neo4j, the port is shaped like Cypher. That is the signal to redesign, and
  it is why in-memory comes first.
- **Coverage ratchet.** Slice 1 deletes ~7,800 lines; total coverage will
  move sharply and the baseline needs a deliberate edit with reasoning in the
  commit message.
- **Event schema is forever.** A persisted log cannot be refactored the way
  code can — a badly shaped event is permanent, or needs an upcaster. Slice
  5b must not rush the event shapes. The 67 existing classes are raw
  material, not a spec: they were written against the ORM, and none has ever
  been emitted, so there is no compatibility to preserve and no excuse for
  carrying their mistakes forward.
- **Projection lag is real.** Once the stores are derived, a read after a
  write is not guaranteed to see it. The compliance suites must state
  whether each store is read-your-writes or eventually consistent, and the
  pipelines must not assume the former.
- **Rebuild must be proven, not assumed.** A projection that has never been
  replayed from scratch does not work — it has only ever been fed live. The
  replay-equivalence test in slice 5b is the guard, and it must run in CI,
  not by hand.
- **`tenant_id` scoping** moves into the store ports (namespace, label, or
  collection per tenant). Every port method takes it; the compliance suite
  must prove isolation.

## Backlog interaction

The scope cuts resolve several open entries by deletion rather than repair:
B6 (auth vestiges), B7 (`db.py` shape), B24 (no migration path — there will
be no relational schema to migrate), B3 (`mark_sync_failed`), and most of
B10 (no database in tests — the in-memory adapters become the test backend).
Delete each entry in the slice that actually removes the code, not before.

B2 (Anthropic provider stub), B5, B11, B13–B23, B25, B27 are unaffected.
