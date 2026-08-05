# Use the pgvector VectorStore

This page shows how to run `PgVectorStore` — the `VectorStore` adapter backed
by Postgres with the [`vector`](https://github.com/pgvector/pgvector)
extension — against a real server: starting a backend, constructing the store,
creating its schema, writing and searching, and shutting down.

**When to use this page.** Reach for pgvector when embeddings must outlive the
process, when more than one process reads them, or when a tenant holds more
vectors than you want resident in memory. Those three reasons are the whole
list.

`InMemoryVectorStore` is a complete implementation rather than a test double:
both adapters are subclasses of the same compliance suite in
`tests/compliance/vector_store.py`, and both pass its *tier 1* — exact
membership, exact ordering, exact scores — not just the recall tier an
approximate backend would be held to. So a single-process job that rebuilds
its index each run needs nothing on this page, and moving to Postgres will not
change an answer it already gets.

It will not speed one up either. This adapter deliberately builds **no ANN
index**, so a search scans the querying tenant's rows and costs time linear in
that tenant's data — the same asymptotics as scoring every vector in memory,
with a network hop added. [ADR 0012](../adr/0012-no-ann-index-in-a-multi-tenant-vector-store.md)
records why (an ANN index over a multi-tenant table takes the globally nearest
`k` and *then* drops other tenants' rows, which violates the port's
filter-before-`k` rule while still looking plausible), and BACKLOG B10k lists
the three ways out. Pick pgvector for durability, sharing and memory
footprint; do not pick it expecting sublinear search.

Both adapters are interchangeable behind the port, so this is a composition-
site decision and nothing above it changes. See
[ADR 0002](../adr/0002-two-store-ports.md) for why the port exists at all, and
[implement a store adapter](implement-a-store-adapter.md) if you are writing a
third.

**Prerequisites.** A Postgres server where the `vector` extension is
*available* — the files installed — and a role that may run
`CREATE EXTENSION`. A server that merely has the extension shipped but has
never created it cannot store a single vector; `ensure_schema()` runs the
`CREATE EXTENSION IF NOT EXISTS` for you, which is why the privilege matters.

**There is no extra to install.** `asyncpg` is a base dependency of
`redstring`, not an optional one — unlike the Neo4j adapter, which needs
`redstring[neo4j]`. The adapter drives asyncpg directly; there is no ORM and
no SQLAlchemy import anywhere in this library.

**Reachability today.** `PgVectorStore` is *not* in `redstring.__all__`, so
it is reached by a dotted path:

```python
from redstring.vector.adapters.pgvector import PgVectorStore
```

Per [ADR 0006](../adr/0006-the-public-surface-is-gated.md), a dotted path is
internal and may change without notice, including in a patch release. What is
public is the `VectorStore` port — write your code against that, keep the
import above at one composition site, and the day the adapter is exported you
change one line. The types it hands back (`VectorRecord`, `VectorMatch`,
`EntityId`, `TenantId`) are all exported; see
[domain value types](../reference/domain-value-types.md).

## Prerequisites: a Postgres with the `vector` extension available

You need a Postgres server where the
[`vector`](https://github.com/pgvector/pgvector) extension is **available** —
its files installed on the server — and a role that may run
`CREATE EXTENSION`. Availability alone is not enough: `pgvector/pgvector:pg16`
ships the extension, but a database that has never run `CREATE EXTENSION`
cannot store a single vector. `ensure_schema()` runs
`CREATE EXTENSION IF NOT EXISTS vector` for you on the first call, which is
exactly why the privilege — not just the files — is a prerequisite. On a
managed Postgres that means checking `vector` is on the provider's allow-list
and that your role can create it; on a locked-down server, have a superuser
create the extension once and the store's `CREATE EXTENSION IF NOT EXISTS`
becomes a no-op.

Nothing else is required. Postgres 16 is what the repo's test backend runs
(`pgvector/pgvector:pg16`); no plugin, no ANN index build, no schema
migrations of your own.

### No extra to install: `asyncpg` is a base dependency, not an optional one

`asyncpg` sits in `[project.dependencies]`, so `uv add redstring` is the whole
install — there is no `redstring[pgvector]` to remember, and asking for one is
an error rather than a no-op. The optional extras are `neo4j`, `llm`, and `all`
(which is just the other two); the vector adapter is in none of them. This is
deliberately unlike the Neo4j `GraphStore`, which does need
`redstring[neo4j]`.

The adapter drives asyncpg directly. There is no ORM, no first-party
SQLAlchemy import anywhere under `src/`, and no SQLAlchemy dependency declared
by this project — six statements, three of them shaped around pgvector
operators and two of them (`EXPLAIN`, and an `unnest` batch insert) exactly the
SQL an ORM makes harder to write.

Be precise about that last claim, because the stronger version is false:
**SQLAlchemy is still importable in your environment.** `eventsource-py` is a
base dependency of `redstring` and requires SQLAlchemy in *its* base
dependencies, so it is in the lockfile of every install. The distinction that
matters is "nothing here imports it and nothing here asks for it", not "it is
absent" — a reader who assumes the latter will be surprised by `uv.lock`.

Practically: the only thing that gates this adapter is a reachable server, not
a package. If `from redstring.vector.adapters.pgvector import PgVectorStore`
raises `ImportError`, the install is broken; it is never a missing extra.

### Reachability today: `PgVectorStore` is not in `redstring.__all__`

`InMemoryVectorStore` is exported. `PgVectorStore` is not, so today you reach
it by a dotted path:

```python
from redstring.vector.adapters.pgvector import PgVectorStore
```

Per [ADR 0006](../adr/0006-the-public-surface-is-gated.md), `redstring.__all__`
*is* the promise, and anything reached by a dotted path is internal: it may
move, be renamed, or change signature without notice, including in a patch
release. That is a statement about the import line, not about the adapter's
maturity — it runs the same compliance suite, at the same tier, as the
exported in-memory one.

Everything that *crosses* the boundary is public, which is what makes the one
unexported name cheap to absorb:

- the **`VectorStore` port** itself — annotate against it, never against
  `PgVectorStore`;
- the values crossing it — `VectorRecord`, `VectorMatch`, `EntityId`,
  `TenantId` (see [domain value types](../reference/domain-value-types.md));
- the errors it raises — `DimensionMismatchError`, and `RedstringError` as the
  base of every deliberate error here. So the `except` clauses of step 4 need
  no internal import either.

Confine the dotted import to one composition site and the exposure is a single
line:

```python
from redstring import VectorStore
from redstring.vector.adapters.pgvector import PgVectorStore  # the one line


async def build_store(dsn: str) -> VectorStore:
    store = await PgVectorStore.connect(dsn, dimension=768)
    await store.ensure_schema()
    return store
```

The day the adapter is exported, that is the line you change and nothing else;
substituting `InMemoryVectorStore` for a test is the same edit. That
interchangeability is what [ADR 0002](../adr/0002-two-store-ports.md) exists
for, and what [implement a store adapter](implement-a-store-adapter.md) asks of
a third implementation.

## Step 1: Start a Postgres that ships pgvector

You need a reachable server, a database, and a role that may run
`CREATE EXTENSION`. Either option below gives you all three; pick A to get
going in a minute, B if the data has to live somewhere you already run.

### Option A: the repo's test backend (`docker compose -f docker-compose.test.yml up -d postgres`, port 5434)

`docker-compose.test.yml` in this repo declares a Postgres for exactly this
purpose. Start it, and wait for it to be ready:

```console
$ docker compose -f docker-compose.test.yml up -d --wait postgres
```

Three details of that command are worth knowing before the first time it
surprises you.

**Name `postgres` explicitly.** The compose file also declares a `neo4j`
service for the `GraphStore` integration tests. A bare `up -d` starts both, so
naming the service is what keeps you from running a graph database you did not
ask for.

**`--wait`, because `up -d` returns before the server can serve.** Without it,
`up -d` returns when the container is *running*, and a Postgres still doing
first-boot initialisation accepts TCP connections seconds before it will answer
a query — so an immediate `connect()` races, and the failure surfaces as a
connection error that reads like a bad DSN rather than a timing problem. The
service declares a healthcheck for this (`pg_isready -U postgres -d
redstring_test`, every 5s, up to 30 retries), and `--wait` is what makes Docker
block on it instead of you.

The `-d redstring_test` inside that healthcheck is load-bearing, and worth
copying if you ever write your own: a bare `pg_isready` succeeds against the
bootstrap server *before* `POSTGRES_DB` has been created, so it reports healthy
while the database you are about to connect to does not exist yet.

**The published port is 5434, not 5432.** The mapping is `5434:5432` —
5432 inside the container, 5434 on your host — chosen (along with 7688 for
Neo4j) so the container cannot collide with a local Postgres install or with
another project's container.

The image is `pgvector/pgvector:pg16`, so the `vector` extension files are
already present and nothing has to be installed into the container. It runs as
the default superuser `postgres`, which settles the `CREATE EXTENSION`
privilege question from the prerequisites outright: `ensure_schema()` can
create the extension itself on first call, and you need run nothing by hand.

Password and database name are fixed in the compose file
(`POSTGRES_PASSWORD: redstring`, `POSTGRES_DB: redstring_test`), so the DSN is
fully determined:

```
postgresql://postgres:redstring@localhost:5434/redstring_test
```

```python
store = await PgVectorStore.connect(
    "postgresql://postgres:redstring@localhost:5434/redstring_test",
    dimension=768,
)
await store.ensure_schema()
```

That exact DSN string is also the default the integration suite falls back to
when `KG_TEST_POSTGRES_DSN` is unset — so a backend that works for your
application works for `pytest -m integration` with no environment variable at
all. See
[run the integration and mutation suites](run-integration-and-mutation-suites.md)
for the same backend from the suite's side.

**This is a development and test convenience, not a deployment story.** The
service declares no named volume, so its data sits in an anonymous one and
`docker compose -f docker-compose.test.yml down -v` discards the lot —
convenient for resetting between experiments, and a reason not to point
anything you care about at port 5434. Anything durable belongs on your own
server; see Option B.

### Option B: your own server (`CREATE EXTENSION vector` privileges required)

Any Postgres works provided the `vector` extension is **available** — its
files installed on the server — and your role may create it. Confirm both
before writing any code:

```console
$ psql "$DSN" -c "SELECT name, default_version FROM pg_available_extensions WHERE name = 'vector'"
```

An empty result means the extension is not installed on the *server*, and no
amount of privilege will fix it: install the `pgvector` package for your
Postgres build, or on a managed service check the provider's allow-list of
extensions.

A row means you can proceed to the privilege question, which is separate.
`ensure_schema()` issues `CREATE EXTENSION IF NOT EXISTS vector` on every call
(see step 3), so the role the store connects as needs to be able to run it:

```console
$ psql "$DSN" -c "CREATE EXTENSION IF NOT EXISTS vector"
```

If that succeeds, you are done — the statement is idempotent, and having run
it by hand simply makes the store's copy a no-op. If it fails on privileges,
have a superuser (or the provider's console) run it once against the database
you will use. The store's `CREATE EXTENSION IF NOT EXISTS` then finds the
extension already present and succeeds without needing the privilege itself.
Note that the extension is per-*database*: creating it in `postgres` does
nothing for `myapp`.

Two privileges, then, and they are separate questions: `CREATE EXTENSION` (or
a superuser who has already run it for you), and ordinary `CREATE` on the
schema the store writes into — `ensure_schema()` is the only DDL this library
runs, and it issues `CREATE TABLE IF NOT EXISTS` plus one
`CREATE INDEX IF NOT EXISTS` beside the extension. There is no migration tool
to install and no plugin to load. There is also no ANN index to build, and
that is deliberate rather than an omission: see
[ADR 0012](../adr/0012-no-ann-index-in-a-multi-tenant-vector-store.md).

Two choices are yours rather than the server's, and both are covered in
step 2. Give the store a `table` name of its own if the database is shared —
it must be a bare lowercase identifier, unquoted and unqualified by a schema,
so the store lands in whatever schema your `search_path` resolves to; set that
on the role or in the DSN rather than expecting the store to qualify the name.
And run **one table per embedding dimension**: `vector(n)` bakes the dimension
into the column type, so a 768-dimension store and a 1024-dimension one cannot
share a table, and pointing one at the other's table raises
`DimensionMismatchError` from `ensure_schema()` (step 4).

Postgres 16 is what this project tests against — `pgvector/pgvector:pg16` —
and the adapter uses no syntax newer than that. Once your server answers,
point the integration suite at it with `KG_TEST_POSTGRES_DSN` to confirm it
satisfies the same compliance suite the in-memory adapter does; see
[run the integration and mutation suites](run-integration-and-mutation-suites.md).

## Step 2: Construct the store

Two constructors, and the difference between them is **who owns the connection
pool** — which is the same thing as deciding what `close()` does.

### `PgVectorStore.connect(dsn, dimension=..., table=...)` — the store owns and closes the pool

```python
from redstring.vector.adapters.pgvector import PgVectorStore

store = await PgVectorStore.connect(
    "postgresql://postgres:redstring@localhost:5434/redstring_test",
    dimension=768,
    table="kg_vectors",
)
```

`connect()` is an `async` classmethod: it imports `asyncpg`, builds a pool of
its own with `asyncpg.create_pool(dsn, **pool_options)`, and hands back a store
that has recorded it owns that pool. `dsn` is positional; `dimension` is
keyword-only and required; `table` is keyword-only and defaults to
`kg_vectors`. Both keywords are validated by the same constructor the injected
form uses, so an invalid `dimension` or `table` raises `ValueError` *after* the
pool has been created — call it with values you have already settled.

**Owning the pool is the whole difference, and it is observable through
`close()`.** On a store built this way, `close()` closes the pool; on a store
built by passing a pool in, `close()` does nothing to it. The integration
suite pins both halves — one test closes an injected-pool store and then
proves the pool still answers `SELECT 1`, the other closes a `connect()`-built
store and requires the next `search()` to fail. Ownership follows whoever
created the pool, exactly as it does on the Neo4j adapter.

Reach for this constructor when the store is the only thing in the process
talking to that database — a worker, a one-shot ingest job, a script. It is
also the shape to use inside a composition function, because the caller then
has one object to shut down rather than two:

```python
from redstring import VectorStore
from redstring.vector.adapters.pgvector import PgVectorStore


async def build_store(dsn: str) -> VectorStore:
    store = await PgVectorStore.connect(dsn, dimension=768)
    await store.ensure_schema()
    return store
```

**Pool options pass straight through.** Any further keyword argument goes to
`asyncpg.create_pool` untouched — `min_size`, `max_size`, `command_timeout`,
`ssl`, `server_settings` and the rest. The adapter deliberately does not
restate asyncpg's signature; narrowing it here would mean maintaining a copy
that goes stale against the driver. Two consequences follow from *not*
restating it: asyncpg's own defaults apply (a pool of 10 connections unless you
say otherwise), and a misspelled option is asyncpg's `TypeError`, not a
`redstring` error.

```python
store = await PgVectorStore.connect(dsn, dimension=768, min_size=1, max_size=4)
```

**`connect()` creates a pool, not a schema.** It runs no DDL and issues no
query of its own — the first statement this library sends is the one
`ensure_schema()` sends, which is step 3. So a successful `connect()` says the
DSN parsed and asyncpg could establish its initial connections; it says nothing
about the `vector` extension, the table, or its dimension. If the call hangs or
refuses, the problem is reachability — see the troubleshooting note on port
5434 versus 5432, and on `up -d` returning before the server can serve.

Whatever you built it with, pair it with `close()` at shutdown (step 6). The
call is the same on both constructors precisely so that swapping between them
is a one-line change and never an edit to your teardown.

### `PgVectorStore(pool, dimension=..., table=...)` — you injected the pool, `close()` leaves it alone

```python
import asyncpg

from redstring.vector.adapters.pgvector import PgVectorStore

pool = await asyncpg.create_pool(dsn)
store = PgVectorStore(pool, dimension=768, table="kg_vectors")
```

The plain constructor is not `async` and connects to nothing: it wraps a pool
you already have. `pool` is positional; `dimension` is keyword-only and
required; `table` is keyword-only and defaults to `kg_vectors`. Both keywords
are validated here — this is the constructor `connect()` itself calls — so an
invalid `dimension` or `table` raises `ValueError` immediately.

**`close()` is a no-op on an injected pool.** Ownership follows whoever created
the pool, exactly as it does on the Neo4j adapter: you made it, you close it.
That is not a documented intention, it is a pinned one — the integration suite
closes an injected-pool store and then asserts the pool still answers
`SELECT 1`, and separately closes a `connect()`-built store and requires the
next `search()` to raise on a closed pool. The two tests sit side by side in
`tests/integration/vector/test_pgvector_store.py` because either alone would
pass against a store that always closed, or never closed.

Reach for this constructor whenever the pool outlives the store:

- **the pool is shared** with the rest of your application, and the store is
  one consumer of it among several;
- **stores are short-lived** — built per request, per job, per test — against
  a pool that is not;
- **you need pool options this adapter would otherwise pass through blind**,
  or a pool built by a framework or connection manager you do not control.

The compliance suite is the motivating case, and it is worth knowing because it
is what proved the split necessary. It builds a *new store per test* — and per
hypothesis example within a property test — against one pool created by a
fixture, then calls `dispose()` on each, which calls `close()`. Without the
ownership split the first disposal would take the pool down, and every test
after it would fail on a closed pool rather than on anything about pgvector.
(The fixture is function-scoped, not session-scoped: an asyncpg pool binds to
the event loop that created it, and the project's
`asyncio_default_fixture_loop_scope` is `function`. One pool per *test* is
still one pool across all of a property test's examples, which is the case the
split exists for.)

Nothing else differs between the two constructors. An injected-pool store runs
the same `ensure_schema()`, the same reads and writes, and passes the same
compliance suite at the same exact tier; the *only* observable difference is
what `close()` does to the pool.

So call `close()` at shutdown regardless of which constructor you used
(step 6). On an injected-pool store it releases nothing, and that is the point:
the teardown code reads the same either way, so swapping `connect()` for an
injected pool — or back — stays a one-line change.

```python
store = PgVectorStore(pool, dimension=768, table="kg_vectors")
await store.ensure_schema()
try:
    ...
finally:
    await store.close()   # the pool is still yours, and still open
    await pool.close()    # you created it, so you close it
```

Both `store.dimension` and `store.table` are readable back off the store, which
is the cheap way to confirm at a composition site that a shared pool is being
handed the table and dimension you meant.

### Choosing `dimension`: it must match your embedding model exactly (e.g. 768 for nomic-embed-text)

`dimension` is keyword-only, required, and has no default — because there is no
defensible one. It is the output width of the embedding model that feeds the
store, and nothing else:

| Model | `dimension` |
|---|---|
| `nomic-embed-text` | 768 |
| `bge-m3` | 1024 |
| OpenAI `text-embedding-3-small` | 1536 |

Take the number from your model's documentation, or measure it once —
`len(await embedder.embed("probe"))` — and pass that. A non-positive value is
rejected at construction with `ValueError` (`dimension must be positive, not
0`); every other integer is accepted, because the store has no way to know
which model you are about to use.

**Getting this wrong is a correctness problem, not a tuning one, and it fails
quietly.** Vectors of the wrong shape do not surface as an exception somewhere
downstream — they surface as mediocre search results, which read as a mediocre
embedding model rather than as a bug. That asymmetry is why the store refuses
rather than coerces, and why `DimensionMismatchError` exists as a named error
instead of a `ValueError`.

The value is enforced in two places, and it is worth knowing they are separate
checks:

- **On every vector crossing the port.** `upsert`, `upsert_many` and `search`
  each raise `DimensionMismatchError(expected=…, actual=…)` when
  `len(vector) != store.dimension`. This is done **client-side, before the
  round trip**, deliberately: Postgres would reject a wrong length too, but as
  an opaque `expected 8 dimensions, not 3` that names neither the store nor
  the model. The same check also rejects a zero vector (with `ValueError` —
  cosine is undefined at the origin). Step 5 covers both.
- **On the table, once, at `ensure_schema()`.** The column is declared
  `vector(n)`, so the dimension is baked into the *column type*.
  `ensure_schema()` reads the declared width back out of `pg_attribute` and
  raises `DimensionMismatchError` if an existing table disagrees, rather than
  letting your first insert fail. Step 4 is entirely about that case.

Two consequences follow, and both are decisions to take now rather than to
discover later:

**Changing embedding model means a new store and a new table — never an
in-place rewrite.** Give each model its own table name (`kg_vectors_768`,
`kg_vectors_nomic`) and backfill into it; see *Choosing `table`* below and the
one-table-per-dimension note in the operational section.

**The dimension check is not a model check, and cannot be.** Two different
models that both emit 768 components pass every check on this page while
producing vectors that are not comparable with each other — the store sees
matching lengths and has nothing else to go on. Neither this adapter nor the
port can detect it, so *segregating tables by model, not merely by width, is
the caller's job*. It is the one failure mode on this page with no error
message attached to it.

`store.dimension` reads the value back, which is the cheap assertion to make at
a composition site where the embedder and the store are wired up separately.
For the shape of the check in your own adapter — and why a length comparison
here is written to avoid `is not` — see
[implement a store adapter](implement-a-store-adapter.md).

### Choosing `table`: bare lowercase identifiers only, no quoting, no schema qualification

`table` is keyword-only and defaults to `kg_vectors`. It must match
`^[a-z_][a-z0-9_]{0,62}$` — a leading lowercase letter or underscore, then
lowercase letters, digits and underscores, 63 characters at most. Anything
else raises `ValueError("table must be a bare lowercase identifier, not …")`
at construction, on both constructors, before any connection is used.

What that excludes, in the words of the cases the unit suite pins:

| Rejected | Why it is not a bare identifier |
|---|---|
| `kg vectors` | whitespace |
| `public.kg_vectors` | schema-qualified |
| `KgVectors` | uppercase |
| `9lives` | leading digit |
| `""` | empty |
| `"x" * 64` | longer than Postgres's 63-byte limit |
| `kg_vectors"; DROP TABLE users; --` | quoting, and the reason the rule exists |

Accepted, by the same suite: `kg_vectors`, `_v`, `kg_vectors_test_gw0`, and any
63-character run of the allowed alphabet.

**Why the rule is a whitelist rather than an escaping routine.** Postgres has
no parameter form for an identifier, so unlike every value this adapter sends,
the table name is *interpolated into the SQL string* — it reaches
`CREATE TABLE`, `CREATE INDEX`, the `unnest` insert, the search, and both
deletes as text. The adapter therefore proves the name is boring rather than
trying to escape an arbitrary one: the set of names a vector store needs is
small, and a rejected name is a far clearer failure than a subtly mis-escaped
one. Pass a name from configuration and validate it at start-up, not from
per-request input.

**No quoting means the identifier is folded, so pick the folded form
yourself.** Because the name is never wrapped in double quotes, Postgres
down-cases it — `KgVectors` would become `kgvectors` and the store would then
disagree with a table someone created as `"KgVectors"`. Requiring lowercase up
front removes the whole class of question.

**No schema qualification means the table lands wherever `search_path`
resolves** — normally `public`. The store will not qualify the name for you and
there is no `schema=` argument. If it belongs elsewhere, put the schema on the
connection rather than in the name:

```python
store = await PgVectorStore.connect(
    dsn,
    dimension=768,
    table="kg_vectors",
    server_settings={"search_path": "embeddings,public"},
)
```

`server_settings` is asyncpg's, passed straight through by `connect()` (see
above); `ALTER ROLE … SET search_path` or `?options=-csearch_path%3D…` in the
DSN do the same job. Whichever you choose, apply it consistently — the same
bare name under two `search_path`s is two different tables, and neither the
store nor `ensure_schema()` can tell.

**Give each embedding dimension — and each model — its own table.** The column
is `vector(n)`, so the dimension is part of the column type and a 768 store
cannot share a table with a 1024 one; `ensure_schema()` raises
`DimensionMismatchError` when it finds a table declared at another width
(step 4). Segregating by *model* is the caller's job on top of that, since two
768-dimension models pass every check while producing incomparable vectors. A
name that says both is the cheapest defence:

```python
store = await PgVectorStore.connect(dsn, dimension=768, table="kg_vectors_nomic")
```

One database can hold as many of these as you like; nothing in the adapter is
shared between tables.

**A distinct name is also how concurrent work stays isolated.** The integration
suite gives every xdist worker its own table —
`f"kg_vectors_test_{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"` — so a
worker truncating between tests cannot delete rows another worker is reading.
That is the pattern to copy for anything that shares a database: parallel test
runs, a staging tenant, a backfill written beside a live table.

`store.table` reads the value back, next to `store.dimension`, which is the
cheap assertion to make at a composition site where a pool is shared and the
name came from configuration.

Constructing a store connects nothing and creates nothing — no DDL has run
yet. That is step 3.

## Step 3: Run `ensure_schema()` before the first read or write

Constructing the store ran no SQL. `ensure_schema()` is the first statement
this library sends, and it is the only DDL it ever runs:

```python
store = await PgVectorStore.connect(dsn, dimension=768, table="kg_vectors")
await store.ensure_schema()
```

Call it once at start-up, before the first `upsert`, `get` or `search`.
Skipping it does not degrade anything gracefully — the table does not exist,
so the first write fails with an `UndefinedTableError` from asyncpg.

There is no migration tool to install and no separate DDL script to keep in
step with the code: the statements are built from `store.dimension` and
`store.table`, so the schema is a function of the store you constructed.

### What it creates: the extension, the table, and the two `tenant_id`-leading btrees

Four things, in one pooled connection:

1. **`CREATE EXTENSION IF NOT EXISTS vector`** — which is why the
   `CREATE EXTENSION` privilege is a prerequisite (step 1). On a database
   where a superuser already created the extension, this is a no-op.
2. **The table**, `CREATE TABLE IF NOT EXISTS`, with columns `tenant_id uuid`,
   `entity_id uuid`, `embedding vector(n)`, `entity_type text`, and
   `metadata jsonb NOT NULL DEFAULT '{}'::jsonb`.
3. **The primary key `(tenant_id, entity_id)`** — the pair, not the entity id
   alone. That is the composite the tenant-isolation properties rest on: a key
   on `entity_id` alone would let one tenant's write replace another's, and
   two tenants holding the same entity id is a legal arrangement here.
4. **A second btree, `<table>_tenant_type_idx` on `(tenant_id, entity_type)`**,
   for the port's type filter.

Both indexes **lead with `tenant_id`**, and that is the load-bearing part
rather than an incidental column order: every query this adapter issues filters
on `tenant_id` first, so a leading `tenant_id` turns a tenant-scoped read into
an index seek instead of a scan across every tenant's rows. Correct answers do
not distinguish the two — this project has already been bitten once by a
Neo4j query that returned exactly the right rows after scanning the whole
database — so the primary key's column *order* is pinned by an integration
test that reads it back out of `pg_index`, not by one that reads results. The
secondary index's column order is fixed in the DDL the unit tests read.

Two things `ensure_schema()` deliberately does **not** create:

- **No index on `embedding`.** No `hnsw`, no `ivfflat`. An integration test
  asserts every index on the table uses the `btree` access method, so adding
  one is a decision someone has to come and argue for rather than a drive-by
  optimisation. [ADR 0012](../adr/0012-no-ann-index-in-a-multi-tenant-vector-store.md)
  is that argument; BACKLOG B10k holds the three ways out.
- **No third index on `tenant_id` alone.** Either of the two above already
  serves a tenant-only lookup from its leading column.

`entity_type` being a real column is worth knowing when you look at the table
by hand: it is a *projection* of `metadata["entity_type"]`, written from the
metadata while the metadata is stored whole. The JSON stays the source of
truth. Filtering `metadata->>'entity_type'` on every row would be a per-row
JSON parse the planner cannot index, which is why the column exists — but
filter through the port's `entity_types=[…]` argument rather than reaching for
the column yourself.

### Why it is idempotent and safe to call on every startup

Every statement it sends carries `IF NOT EXISTS` — the extension, the table,
and the secondary index alike. A unit test walks the DDL tuple and asserts
`IF NOT EXISTS` appears in each statement individually, and an integration test
calls `ensure_schema()` twice in a row and requires the second call not to
raise. So it is safe to run unconditionally on every process start, in every
replica — no "have I migrated yet?" flag, no first-run branch:

```python
async def build_store(dsn: str) -> VectorStore:
    store = await PgVectorStore.connect(dsn, dimension=768)
    await store.ensure_schema()   # every start, not just the first
    return store
```

Idempotent is not the same as verified, and the distinction cost this project a
real defect. Every other integration test in the module runs against a table an
earlier run had already created, so **the DDL loop could have executed nothing
at all and the whole suite would still have passed** — cosmic-ray proved it by
replacing the statement iterable with `[]`, and the mutant survived. The test
that kills it is the one that drops the table first and starts from genuinely
nothing (`test_ensure_schema_creates_the_table_from_nothing`), and it asserts
the table is *usable* — upsert, get, and a type-filtered search — not merely
present. If you write your own schema step, that is the shape of test it needs;
see [implement a store adapter](implement-a-store-adapter.md).

Two limits on what "idempotent" promises here:

- **It is not a migration.** `CREATE TABLE IF NOT EXISTS` will not alter a
  table that already exists, so it cannot add a column or widen one. Changing
  the schema means a new table.
- **It does not silently accept a mismatch.** After running the DDL,
  `ensure_schema()` reads `atttypmod` for the `embedding` column back out of
  `pg_attribute` and raises `DimensionMismatchError(expected=<declared>,
  actual=<store.dimension>)` when the two disagree. That check runs on *every*
  call, not only the first, so a store pointed at another model's table fails
  at start-up rather than on its first insert. That is step 4.

## Step 4: Handle a dimension mismatch on an existing table

`DimensionMismatchError` is exported from `redstring`, so catching it needs no
internal import:

```python
from redstring import DimensionMismatchError
```

It is raised from two different places for two different reasons, and telling
them apart is the whole of this step: **`ensure_schema()` raises it about a
table**, once, at start-up; **`upsert`, `upsert_many` and `search` raise it
about a vector**, client-side, on every call.

### Why `vector(n)` bakes the dimension into the column type

The embedding column is declared `vector(768)`, not `vector`. The width is part
of the *column type*, the same way `varchar(20)` is — so a table created for
768-component vectors cannot hold 1024-component ones, and no configuration on
the store changes that. This is why the rule stated in step 2 is a hard one:
**one table per embedding dimension**, and in practice one table per model,
since two 768-dimension models pass every check while producing incomparable
vectors.

`ensure_schema()` cannot fix a table that disagrees. `CREATE TABLE IF NOT
EXISTS` does nothing when the table is there, and this library runs no other
DDL — there is no migration step that would `ALTER` the column, and adding one
would not help anyway: re-typing the column means re-embedding every row, which
is a backfill only you can perform.

### What `DimensionMismatchError` from `ensure_schema()` means, and the two ways out

After running its DDL, `ensure_schema()` reads the declared width straight back
out of the catalogue:

```sql
SELECT atttypmod FROM pg_attribute
WHERE attrelid = $1::regclass AND attname = 'embedding' AND NOT attisdropped
```

and raises when it differs from `store.dimension`. So the error means exactly
one thing: **a table with this name already exists and was created for a
different dimension.** Your store is pointed at another model's data.

```python
store = PgVectorStore(pool, dimension=768, table="kg_vectors")
await store.ensure_schema()
# redstring.domain.exceptions.DimensionMismatchError:
#     expected a vector of dimension 1024, got 768
```

**Read the two numbers carefully, because on this call `expected` is the
table.** `raised.value.expected` is the width declared on the existing column
(1024 above) and `raised.value.actual` is the `dimension` you constructed the
store with (768). That is the opposite way round from the same error raised by
`upsert` or `search`, where `expected` is the store's dimension and `actual` is
the length of the vector you passed. Both readings are natural in place —
"expected" is whatever the thing being checked against says — but code that
inspects the attributes has to know which call it caught.

Two ways out, and they are the only two:

1. **Point this store at its own table.** Almost always the right answer, and a
   one-line change:

   ```python
   store = PgVectorStore(pool, dimension=768, table="kg_vectors_nomic")
   ```

   Both tables then live in the same database, the old model's rows keep
   serving whatever still reads them, and nothing needs re-embedding.

2. **Retire the old table and re-embed into a new one.** Choose this when the
   old data is genuinely dead. Create the new table under a new name, backfill
   by re-embedding the source documents with the new model, cut readers over,
   then drop the old table. Do not `DROP TABLE` first and re-run
   `ensure_schema()` in the hope of reusing the name: that discards the
   embeddings before the replacements exist, and the store cannot regenerate
   them — this library never fetches content, and re-embedding is your
   pipeline's job.

There is deliberately no third way. The store will not widen the column, will
not truncate or pad your vectors to fit, and will not fall back to a different
table on its own.

The check runs on **every** `ensure_schema()` call, not only the first, which
is what makes it useful: a replica that starts with a stale `dimension` in its
configuration fails at start-up with a message naming both numbers, rather than
serving traffic until its first write. Both directions are pinned by the
integration suite at a realistic 768 — a table one component *larger* than the
store fails as loudly as one smaller, because a check that caught only the
too-small case would let the too-large one through to fail on an insert, which
is precisely the error this exists to replace.

The check is also silent on a table that does not exist yet: `atttypmod` comes
back `None` and nothing is raised, which is the fresh-database path from
step 3.

### The same error from `upsert`/`search`: a wrong-length vector, checked client-side

The other source is a vector, not a table. `upsert`, `upsert_many` and `search`
each validate every vector against `store.dimension` before touching the pool:

```python
store = PgVectorStore(pool, dimension=768, table="kg_vectors")
await store.search([0.1, 0.2], tenant_id)
# DimensionMismatchError: expected a vector of dimension 768, got 2
```

Here `expected` is the store's dimension and `actual` is `len(vector)`.

**The check is client-side on purpose**, and it is not merely a faster version
of what Postgres would do:

- Postgres's own complaint is an opaque `expected 8 dimensions, not 3` that
  names neither the store, the table, nor the model — and it costs a round
  trip to obtain.
- Postgres would not reject a **zero** vector at all. `<=>` against the origin
  yields NaN, which sorts unpredictably and would make ranking depend on the
  query plan. The same guard therefore raises `ValueError("a zero vector has
  no direction; cosine is undefined for it")` for that case — a `ValueError`,
  not a `DimensionMismatchError`, since the length was fine.

`upsert_many` validates **every element of the batch you passed**, including
records that a later record in the same call supersedes, and it does so before
deduplicating or writing anything. So a batch either lands whole or raises
having written nothing, and a record cannot escape validation by being replaced
— which is the behaviour `InMemoryVectorStore` has too, and the shared
compliance suite pins for both.

Because the same guard runs in both adapters, a wrong-length vector fails
identically in your unit tests against `InMemoryVectorStore` and in production
against Postgres. That is the property [ADR 0002](../adr/0002-two-store-ports.md)
is for, and the shape [implement a store adapter](implement-a-store-adapter.md)
asks a third implementation to reproduce — note in particular that the length
comparison there is written to avoid `is not`, because a store built at a
realistic 768 sits outside CPython's small-integer cache and an identity
comparison would reject every legitimate write.

**Do not catch this one and retry.** A wrong-length vector is a wiring bug:
either the embedder and the store were constructed from different numbers, or
two models are feeding one store. The fix is at the composition site, and
`store.dimension` — readable off the store — is the cheap assertion to make
there. There is no embedding port in this library to compare it against — the
width is a number you hold — so assert it against the constant your pipeline
embeds with:

```python
EMBEDDING_DIMENSION = 768  # nomic-embed-text

assert store.dimension == EMBEDDING_DIMENSION
```

In the projection path, a vector of the wrong length is treated as a **poison
event** rather than a transient failure, for the same reason: retrying it
cannot succeed. See
[drive projections from an event store](drive-projections-from-an-event-store.md).

## Step 5: Write and read

With the schema in place, the store is the `VectorStore` port and nothing more.
Write with `upsert` or `upsert_many`, read one record back with `get`, and rank
with `search`:

```python
from redstring import TenantId, EntityId, VectorRecord

await store.upsert(
    entity_id,
    embedding,                       # len(embedding) == store.dimension
    tenant_id,
    metadata={"entity_type": "person", "name": "Ada Lovelace"},
)

record = await store.get(entity_id, tenant_id)     # None if this tenant has no such id
matches = await store.search(query_vector, tenant_id, k=5, entity_types=["person"])
```

Everything below is port behaviour, not pgvector behaviour: `InMemoryVectorStore`
answers identically, and the shared compliance suite in
`tests/compliance/vector_store.py` is what holds the two together.

### Upsert semantics: last-write-wins, metadata replaced wholesale rather than merged

`upsert` is `upsert_many` with one record — literally, it builds a
`VectorRecord` and delegates — so everything here holds for both.

**The key is the ordered pair `(tenant_id, entity_id)`**, which is the table's
primary key. Writing the same key twice leaves one row holding the later
value: the insert carries
`ON CONFLICT (tenant_id, entity_id) DO UPDATE SET embedding = EXCLUDED.embedding,
entity_type = EXCLUDED.entity_type, metadata = EXCLUDED.metadata`. So an upsert
is idempotent, replaying one is safe, and a projection can be rebuilt from the
event log without a wipe.

Because the key is the *pair*, two tenants holding the same entity id are two
rows and neither write can touch the other's. That is not incidental: it is the
composite the tenant-isolation properties rest on, and a key on `entity_id`
alone would let one tenant's write vouch for another's.

**`metadata` is replaced wholesale, never merged.** The update sets
`metadata = EXCLUDED.metadata`. There is no `||` merge, and `metadata=None` on
`upsert` means the empty mapping (`metadata or {}`), not "leave what was
there":

```python
await store.upsert(entity_id, embedding, tenant_id, metadata={"a": 1, "b": 2})
await store.upsert(entity_id, embedding, tenant_id, metadata={"a": 9})

(await store.get(entity_id, tenant_id)).metadata
# {"a": 9}      — not {"a": 9, "b": 2}
```

This is a correctness requirement rather than a simplification. A merge would
let a key that a later event *removed* survive in the store, so the result of a
replay would depend on the order and grouping of the events it happened to
see — the projection would stop being a function of the log. Wholesale
replacement makes each write state the record's complete metadata, which is
what makes replay deterministic.

If you want the old keys, do the merge yourself, explicitly, at the level that
knows whether the removal was intended:

```python
current = await store.get(entity_id, tenant_id)
merged = {**(current.metadata if current else {}), **new_metadata}
await store.upsert(entity_id, embedding, tenant_id, metadata=merged)
```

**The embedding is replaced too**, on the same conflict clause, so re-upserting
a key with a new vector re-points it rather than accumulating versions. There
is no history in this table; the event log is the history.

**`entity_type` follows the metadata automatically.** The column is written on
every insert *and* on every update from `entity_type_of(record.metadata)` —
the port's single reading of the convention — so it can never drift from the
JSON. A write whose metadata drops the key sets the column back to `NULL`, and
the record then matches no type filter. Only a `str` counts: absent, `None`,
`7`, `["person"]` and `{"x": 1}` all yield no type rather than an error or a
coercion, so the record is still stored and still found by an unfiltered
search. That rule lives in `ports/vector_store.py` precisely because the two
adapters once wrote their own and diverged — pgvector nulling non-strings while
the in-memory store raised `TypeError` on a stored list.

**What comes back is yours.** `get` decodes the row into a fresh
`VectorRecord`, so mutating the vector list or the metadata dict it hands back
cannot change stored state, and a later read is unaffected. The vector also
round-trips *exactly*: the read casts `embedding::real[]` rather than letting
pgvector render the value as text, because the text form rounds to seven
significant digits (`128.390625` comes back as `128.39062`) even though the
stored `float4` is exact. That asymmetry was found by a round-trip property,
not by reading documentation — worth knowing if you query the column yourself.

`get` returns `None` when *this tenant* has no such id, which is not the same
question as whether the id exists in the table.

### Batch upserts collapse repeated `(tenant_id, entity_id)` keys

`upsert_many` sends the whole batch as **one statement** — `unnest` over five
arrays — so a thousand-record batch is a single round trip, not a thousand.

Repeated keys within one call are collapsed before the insert, keeping the
last occurrence, which is the same last-write-wins rule that applies across
calls. That collapse is required rather than an optimisation: Postgres refuses
to let one statement affect a row twice (`cannot affect row a second time`),
and `upsert_many([record, record])` is an ordinary call.

Validation runs over **the list you passed**, before any deduplication:

```python
await store.upsert_many([bad_length_record, good_record_with_same_key])
# DimensionMismatchError — even though the second record supersedes the first
```

The ordering matters and is pinned. Deduplicating first would let a rejected
record disappear because a later one happened to replace its key, so the same
call would raise against Postgres and succeed in memory — exactly the
divergence the compliance suite exists to catch, and it once missed it because
the test used two distinct keys. A batch therefore lands whole or writes
nothing.

### Search: filters apply before `k`, results are exact, scores are on the port's 0..1 scale

One statement does filter, then rank, then limit, in that order:

```sql
WHERE tenant_id = $1
  AND ($3 OR entity_type = ANY($4::text[]))
  AND ($5::float8 IS NULL OR score >= $5)
ORDER BY score DESC, entity_id::text ASC
LIMIT $6
```

**Filters apply before `k`.** SQL gives that for free, since `WHERE` runs ahead
of `ORDER BY` and `LIMIT` — and it is only free because there is no ANN index.
A store that took the `k` nearest and then filtered would return fewer than `k`
results while matching records sat further down the ranking: correct-looking,
wrong, and indistinguishable from a tenant with little data. That is the
reasoning behind [ADR 0012](../adr/0012-no-ann-index-in-a-multi-tenant-vector-store.md),
and the reason searches here are **exact** rather than approximate — this
adapter passes the compliance suite's exact tier for the same reason the
in-memory one does.

`entity_types=None` means no filter; `entity_types=[]` matches nothing. A
record whose `metadata["entity_type"]` is absent, `None`, a number, a list or
an object matches no type filter at all — only a `str` counts, and `7` is not
coerced to `"7"`.

**Scores are `(1 + cosine) / 2`, higher meaning more similar** — `1.0` for
identical direction, `0.5` for orthogonal, `0.0` for opposite. In SQL that is
`1 - (embedding <=> $2::vector) / 2`, since `<=>` is pgvector's cosine
*distance*. `min_score` reads on that same scale and drops results scoring
strictly below it; note `min_score=0.0` is **not** a no-op, and getting the
scale backwards is a silent inversion that returns the worst matches first
while still looking like a ranking. The compliance suite pins the numbers
against `redstring.domain.vector.cosine_score`, not merely their order.

Ties break on ascending `entity_id` as its canonical lowercase hyphenated
string, so the ordering is total and `k` cuts through a tie the same way on
both adapters.

Cost is linear in the *querying tenant's* rows, served by the primary key's
leading `tenant_id` — never a scan across other tenants.

### Rejected inputs: zero vectors, negative `k`

Three rejections, all before the pool is touched:

| Input | Raises |
|---|---|
| `len(vector) != store.dimension` (upsert, upsert_many, search) | `DimensionMismatchError(expected=store.dimension, actual=len(vector))` |
| a vector whose every component is zero | `ValueError("a zero vector has no direction; cosine is undefined for it")` |
| `search(..., k=-1)` | `ValueError("k must not be negative")` |

A zero vector is rejected because cosine is undefined at the origin and
pgvector's `<=>` yields NaN there, which sorts unpredictably — ranking would
depend on the query plan. Postgres would not reject it at all, which is half of
why the guard is client-side (step 4 is the other half).

`k=0` is legal and returns `[]` without querying, whatever the tenant holds.
That boundary is written as an explicit example rather than left to a property
drawing `k` from a range: whether a sampler reaches `0` depends on the seed and
on `KG_COMPLIANCE_MAX_EXAMPLES`, and two mutants widening `k < 0` to `k <= 0`
died on one mutation run and survived the next because of it.

None of these are worth catching and retrying — each is a wiring bug at the
composition site, and a retry cannot succeed. See
[implement a store adapter](implement-a-store-adapter.md) for reproducing the
same guards in a third implementation, and
[domain value types](../reference/domain-value-types.md) for `VectorRecord`,
`VectorMatch` and the id types crossing the port.
