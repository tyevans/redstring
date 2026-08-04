# kg-builder re-architecture plan

Turn kg-builder into a library that constructs knowledge graphs, with
pluggable graph and vector storage — and nothing else. Clean breaks only:
no deprecation shims, no compatibility re-exports left behind.

This supersedes the original ring-migration plan (commits `edced18`,
`cf64765`), which assumed the existing package set was worth relocating.
Slices 0 and 0b from that plan are done and still stand; everything after
them is replaced.

## What kg-builder is

**Given content, produce a knowledge graph.** Entity and relationship
extraction, entity consolidation, temporal enrichment, embeddings — written
through a `GraphStore` port and a `VectorStore` port.

## What kg-builder is not

Three scope cuts, decided deliberately:

| Not this | Why | Consequence |
|---|---|---|
| **A document sourcer** | Fetching, crawling, and cleaning source content is a different problem set | `scraping/` deleted; HTML preprocessors deleted; document parsing and object storage deleted |
| **An application** | Job tracking, review queues, and credential storage belong to the caller | `models/` deleted; SQLAlchemy leaves the core; provider config is passed in, not read from a DB |
| **An auth boundary** | It never was one | `User`/`Tenant`/`OAuthProvider` and the `OAUTH_*`/`APP_JWT_*` config deleted; `tenant_id` survives as a scoping key |

The library does not fetch anything. Callers hand it content they already
have.

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

## Slices

Slices 0 and 0b are complete. Dispatch the rest one at a time.

| # | Slice | Gate |
|---|---|---|
| 0 | **Stabilize.** ✅ 1798 passed, coverage baseline 60.79. | ✅ |
| 0b | **Land the temporal/strategy-router work.** ✅ 1930 passed, nothing skipped. | ✅ |
| 1 | **Scope cut.** Delete `scraping/`, document parsing, object storage, HTML preprocessors, and their tests and dependencies. Remove `unstructured` from `llm_extractor`. Pure deletion — no new abstractions. | Full suite green; coverage baseline reset with justification |
| 2 | **Domain model.** `Entity`, `Relationship`, `Alias`, `SourceDocument`, temporal value objects as pure types. `DatePrecision`/`UncertaintyMarker` land here (BACKLOG B26). | Unit tests; no I/O in `domain/` |
| 3 | **`GraphStore` port + in-memory adapter + compliance suite.** The compliance suite is the real artifact. | Compliance suite green against memory |
| 4 | **Neo4j adapter.** Same compliance suite, no new tests of its own beyond Cypher specifics. | Compliance suite green against Neo4j |
| 5 | **`VectorStore` port + in-memory + pgvector adapters.** | Compliance suite green against both |
| 6 | **Extraction onto the domain model.** Absorb chunkers and mergers. Provider config passed in, not loaded. | Targeted + `lint-imports` |
| 7 | **Consolidation onto the ports.** Blocking is rebuilt on ANN + normalized-name keys (see Risks). | Targeted + `lint-imports` |
| 8 | **Temporal onto the ports.** | Targeted + `lint-imports` |
| 9 | **Delete the relational layer.** `models/`, `db.py`, `sync_status`, SQL in `timeline_query`/`vector_ops`; trim `config.py` and `encryption.py`. | Full gate |
| 10 | **`pipelines/` + public API.** The composed use cases and a deliberate `kg_builder/__init__.py`. | Full gate |
| 11 | **Docs & meta.** ADR 0001 (this architecture), README rewrite, CHANGELOG with the breaking paths, `CLAUDE.md` structure block, import-linter contract rewrite. | Sweep clean |

Slice 1 is pure deletion and should land first — every later slice is
cheaper against a smaller tree.

## Risks

- **Blocking is the one thing that does not port for free.**
  `consolidation/blocking.py` generates merge candidates with SQL soundex and
  trigram indexes. Over a graph store that has to become ANN from the vector
  store plus normalized-name keys. Prototype this before committing to slice
  7 — it is the only place where "just use a port" might not survive contact.
- **Merge undo needs a record.** `merge_service` supports undo, which
  requires knowing what was merged. Either it becomes graph structure
  (aliases already are) or it is returned to the caller. Decide in slice 7;
  do not let it silently resurrect a history table.
- **Port leakage.** If the in-memory adapter turns out to be much harder than
  Neo4j, the port is shaped like Cypher. That is the signal to redesign, and
  it is why in-memory comes first.
- **Coverage ratchet.** Slice 1 deletes ~7,800 lines; total coverage will
  move sharply and the baseline needs a deliberate edit with reasoning in the
  commit message.
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
