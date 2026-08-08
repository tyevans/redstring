# Neo4jGraphStore

`Neo4jGraphStore` is the Neo4j implementation of the `GraphStore` port. Every
Cypher string in the library lives in this one module; nothing above it knows a
graph database is involved.

It is one of two `GraphStore` adapters shipped with the library — see
[ADR 0002](../adr/0002-two-store-ports.md) for why there are two store ports at
all, and [the store-adapter how-to](../how-to/implement-a-store-adapter.md) for
writing a third.

## Installation and requirements

The adapter needs the `neo4j` extra:

```
uv add "redstring[neo4j]"
```

which installs `neo4j>=5.27,<6` (the async driver).

The server target is **Neo4j 5 community** — `neo4j:5-community` is what the
integration suite runs against.

- **No `apoc`.** Entity type is an indexed property rather than a per-type
  label, so `apoc.create.addLabels` is not needed, and traversal is a
  variable-length path rather than `apoc.path.subgraphAll`. Requiring a plugin
  would narrow which managed Neo4j offerings can host this library.
- **No enterprise-only features.** Relationship *uniqueness constraints* are
  enterprise-only, so relationship identity is served by an index plus a
  delete-before-create in the upsert query (see below). Nothing here needs
  multiple databases either; the optional `database` argument is passed to
  each session the store opens, and defaults to `None` — the server's default
  database.

## Import path

```python
from redstring.graph.adapters.neo4j import Neo4jGraphStore
```

`redstring.graph` re-exports **nothing**, on purpose, and its `__init__`
docstring says why: a package-level re-export would make
`import redstring.graph` pull in the `neo4j` driver, and that driver is an
optional extra. The same package holds `redstring.graph.adapters.memory`,
which needs no extra at all — one re-export would make the cheap adapter's
package unimportable without the expensive one's dependency.

(The package used to hold `client.py` and `queries.py` — a `Neo4jClient`
singleton and a module of loose Cypher constants. Both were deleted in
slice 9. Cypher now lives only in the adapter, and
`tests/unit/graph/test_neo4j_adapter_is_wired.py` scans `src/` for distinctive
Cypher keywords to keep it there.)

So importing a Neo4j-backed store is a deliberate act at a composition root,
by its full dotted path. It is *not* reachable as `redstring.Neo4jGraphStore`:
`Neo4jGraphStore` is absent from `redstring.__all__` precisely because the
top-level package must import cleanly without the extra installed.

`InMemoryGraphStore` **is** exported at the top level —
`from redstring import InMemoryGraphStore` — since it has no optional
dependency behind it. That asymmetry is the whole rule in one line: the public
surface carries the adapters that cost nothing to import, and the rest are
reached by path.

Everything else about the adapter — the `GraphStore` port, the domain types it
returns — *is* in `redstring.__all__`, so a composition root normally imports
one dotted path and takes the rest from the package root:

```python
from redstring import GraphStore, TenantId
from redstring.graph.adapters.neo4j import Neo4jGraphStore
```

Because it is not exported, the adapter is outside the public-surface gates
described in [quality gates](quality-gates.md): its constructor signature is
free to name `neo4j.AsyncDriver`, a type no export could mention.

## Construction

### `Neo4jGraphStore(driver, *, database=None)`

Wraps an **injected** `neo4j.AsyncDriver`. The store does not own it, and
`close()` will not close it.

```python
from neo4j import AsyncGraphDatabase
from redstring.graph.adapters.neo4j import Neo4jGraphStore

driver = AsyncGraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "secret"))
store = Neo4jGraphStore(driver)
```

| Parameter | Kind | Meaning |
|---|---|---|
| `driver` | positional | an already-constructed `neo4j.AsyncDriver` |
| `database` | keyword-only, default `None` | database name; `None` is the server's default |

`database` is stored and passed as `driver.session(database=...)` on every
query the store runs. There is exactly one path from the adapter to the
database — a private `_run(query, **parameters)` that opens a session, runs the
statement and drains the result — so no query can bypass the setting.

Construction opens no connection and issues no query: it stores the driver, the
database name, and the fact that it does **not** own the driver. The driver
dials lazily on first use, so a store built against an unreachable server
raises at the first read or write, not here.

Argument validation also happens before any session is opened. The unit gate
proves it by constructing the store around a driver whose `session()` raises,
and asserting that `find_entities(..., limit=-1)` still raises `ValueError` —
which is why those guard-clause tests run without a Neo4j server at all
(`tests/unit/graph/test_neo4j_adapter_is_wired.py`).

Use this form when the surrounding application owns driver lifecycle — one
driver shared by several stores, or a driver whose pool is configured
elsewhere. `close()` on such a store is a no-op; see
[`close()` and driver ownership](#close-and-driver-ownership) below. To have
the store build and own its driver instead, use
[`connect()`](#neo4jgraphstoreconnecturi--auth-databasenone).

### `Neo4jGraphStore.connect(uri, *, auth, database=None)`

Builds a driver of its own and **owns** it.

```python
store = Neo4jGraphStore.connect("bolt://localhost:7687", auth=("neo4j", "secret"))
```

| Parameter | Kind | Meaning |
|---|---|---|
| `uri` | positional | bolt/neo4j URI, passed straight to `AsyncGraphDatabase.driver` |
| `auth` | keyword-only, required | the driver's `(user, password)` tuple |
| `database` | keyword-only, default `None` | database name; `None` is the server's default |

A **classmethod, not a coroutine**. Its whole body is
`AsyncGraphDatabase.driver(uri, auth=auth)`, the ordinary constructor around
the result, and a flag recording that this store created the driver — so it is
called without `await`:

```python
store = Neo4jGraphStore.connect(uri, auth=auth)  # no await
await store.ensure_schema()  # the first await
```

Nothing is dialled, no credentials are checked and no schema is created here.
The neo4j driver connects lazily, so bad credentials or an unreachable server
surface at the first query, not at `connect()`. If you want a startup-time
failure, follow `connect()` with `ensure_schema()` — or with an actual query;
the integration suite deliberately does **not** trust
`driver.verify_connectivity()` for this, because it authenticates and returns
against a server that cannot yet answer `RETURN 1`.

Returns `Self`, so a subclass of the adapter gets its own type back rather than
`Neo4jGraphStore`.

There is no pool, timeout or encryption configuration on this signature. Those
are driver-level settings, and adding pass-through keywords would make the
adapter a second, drifting declaration of the driver's own options — if you
need them, build the driver yourself and use
[the constructor](#neo4jgraphstoredriver--databasenone) instead.

Use this form when the store *is* the application's connection to Neo4j: a
single-store composition root, a script, a worker process.

The only difference from the injected-driver form is disposal —
[`close()`](#close-and-driver-ownership) awaits `driver.close()` for a store
built by `connect()` and does nothing for one built around an injected driver.
Every other behaviour, including how `database` is applied, is identical.

An owning store must therefore be closed, and the integration suite pins that
it really is closed rather than merely flagged: it connects, uses the store,
`close()`s, and then asserts a further call warns
(`test_connect_owns_and_closes_its_driver`). The neo4j 5.x driver warns rather
than raising on use after close, so the warning is the observable evidence —
when a future driver promotes it to an error that test is where you will find
out.

That same test carries a lesson about skip guards worth repeating here: because
it is the one test in the module that builds its own driver, it originally
*failed* with `ServiceUnavailable` while its 102 neighbours skipped on an
absent Neo4j. It now takes the shared `neo4j_driver` fixture purely to inherit
the skip. A skip guard is only honest if every test in the module is behind it.

### `close()` and driver ownership

```python
await store.close()
```

A coroutine taking no arguments and returning `None`. It closes the driver
**only if `connect()` created it**; on a store built around an injected driver
it does nothing at all.

| Built by | `close()` does |
|---|---|
| `Neo4jGraphStore(driver)` | nothing — the driver stays open and usable |
| `Neo4jGraphStore.connect(uri, auth=...)` | `await driver.close()` |

### `async with`, which is how you should be calling it

The store is an async context manager, so the `try`/`finally` above has a
shorter and harder-to-forget spelling:

```python
async with Neo4jGraphStore.connect("bolt://localhost:7688", auth=("neo4j", "redstring")) as store:
    await store.ensure_schema()
    entities = await store.find_entities(tenant_id, entity_type="person", limit=20)
```

`__aenter__` returns the store itself. `__aexit__` calls `close()` and returns
`None`, which is falsy — so it **never suppresses**: an exception raised in the
body, and a cancellation delivered while the body is suspended, both propagate
after the driver is released. `close()` remains public and unchanged, for
callers whose lifetime is not a block.

Because exit goes through `close()`, ownership still decides. Entering a block
with a store built around an *injected* driver leaves that driver open on the
way out.

Ownership follows creation, and it is fixed at construction: the constructor
records that this store does *not* own its driver, and `connect()` is the only
thing that flips that. There is no parameter to override it, and no way to
transfer ownership afterwards — a caller that injected a driver keeps both the
right and the obligation to close it.

This is not a stylistic split, and the reason is recorded on the constructor
itself. The compliance suite is run against real Neo4j by
`tests/integration/graph/test_neo4j_store.py`, whose `new_store()` wraps **one
shared driver** in a fresh `Neo4jGraphStore` per store the suite asks for, and
whose `dispose()` calls `close()` on each. A hypothesis-driven property builds
and disposes a store per example. If `close()` closed an injected driver, the
first example would take the shared connection pool down with it and every
later example — and every later test sharing that driver — would fail. The
integration suite pins both halves:

- `test_close_does_not_close_a_driver_it_does_not_own` closes a wrapping store
  and then runs `RETURN 1` on the injected driver, asserting the answer is `1`.
  A liveness probe rather than a flag check: the observable claim is that the
  pool still serves.
- `test_connect_owns_and_closes_its_driver` connects, queries, closes, and
  asserts a further query **warns**. The neo4j 5.x driver emits a
  `DeprecationWarning` matching `closed` on use after close rather than
  raising, so the warning is the only observable evidence the close happened;
  `pytest.raises` there fails with `DID NOT RAISE`. When a future driver
  promotes it to an error, that test is where you find out.

`close()` is not re-entrancy protection and does not track whether it has
already run. Calling it twice on an owning store calls `driver.close()` twice,
which is the driver's business; calling it any number of times on a wrapping
store is free. There is no `__aenter__`/`__aexit__` on the store — dispose it
in a `finally`, as the wiring example at the end of this page does.

Because `close()` is a no-op for an injected driver, **a store built with the
constructor is not a resource you must dispose.** The thing that must be
disposed is the driver, and it is disposed by whoever built it.

### `ensure_schema()`

```python
await store.ensure_schema()
```

A coroutine taking no arguments and returning `None`. It runs the DDL
statements of the module-level `_SCHEMA` tuple — the constraints and indexes
tabulated in [the next section](#schema-created-by-ensure_schema) — through the
same `_run` path as every other query: one statement per round trip, one
session each, in tuple order, sequentially.

**When to call it.** Once during startup, after constructing the store and
before serving traffic:

```python
store = Neo4jGraphStore.connect(uri, auth=auth)
await store.ensure_schema()
```

It is **not** called implicitly — not by the constructor, not by `connect()`,
and not by any read or write. Nothing in the adapter checks whether the schema
exists, so a store whose `ensure_schema()` was never called is fully
functional and quietly unindexed: reads still return correct answers, and
`find_entities` degrades to a scan of every entity of every tenant. That
failure mode is a cost, not an error, which is why it will not show up in a
functional smoke test. See
[the `e.id IS NOT NULL` tenant-seek clause](#the-eid-is-not-null-tenant-seek-clause)
for what the plan looks like when the index is missing.

Uniqueness is the half that *does* fail loudly: without
`entity_tenant_id_unique` two upserts of one entity id can leave two `:Entity`
nodes, and `_write_blocking_keys`' `MERGE` can race into two `:BlockingKey`
nodes for one key. Treat schema creation as a precondition of using the store,
not as an optimisation.

The call needs schema-write privileges on the database. If your application
runs under a reduced-privilege user, run `ensure_schema()` from a migration
step under an administrative user instead and let the application skip it —
which is safe precisely because nothing calls it implicitly.

**Idempotence.** Every statement is `CREATE ... IF NOT EXISTS`, so re-running
the method against a database that already has the schema succeeds and changes
nothing. It is therefore safe on every process start, and safe to call
concurrently from several starting replicas. Two gates hold that:

- `tests/unit/graph/test_neo4j_adapter_is_wired.py` asserts `IF NOT EXISTS`
  appears in *every* entry of `_SCHEMA` — a statement added without it fails
  the commit gate rather than the next deployment. That check needs no server,
  which is the point: the DDL otherwise only runs where Neo4j is reachable.
- `tests/integration/graph/test_neo4j_store.py::TestNeo4jSpecifics::test_ensure_schema_is_idempotent`
  calls it twice against real Neo4j and requires neither call to raise.

The integration suite depends on the property in the ordinary way as well: its
`neo4j_driver` fixture creates the schema on the first test that gets a live
server and skips it afterwards, and the tests that assert *what* the schema
contains call `ensure_schema()` again first rather than relying on that having
happened.

Idempotence is about the statements, not about a memo — the method keeps no
"already done" flag and does not consult `SHOW CONSTRAINTS`. Calling it
repeatedly is cheap but not free: it is one round trip per statement every
time. Call it at startup, not per request.

**Index population is asynchronous.** The statements return before the indexes
reach `ONLINE`, so `ensure_schema()` returning does not mean the planner will
use what it created. Writes and reads are correct throughout; only *plans*
are affected. If you are about to assert a plan — or benchmark one — run
`CALL db.awaitIndexes()` first. A plan taken against a not-yet-online index
shows a scan and measures the population race rather than the query;
[ADR 0003](../adr/0003-blocking-keys-as-nodes.md) records a measurement that
reported `NodeIndexScan` for exactly this reason, and reversed to a seek once
the wait was added.

The adapter deliberately does not call `db.awaitIndexes()` for you: it would
turn startup into a wait proportional to existing data, for a guarantee no
correctness property needs.

## Schema created by `ensure_schema()`

Six statements, held in the module-level `_SCHEMA` tuple and run in tuple
order — three on `:Entity`, one on the relationship type, one each on the two
bookkeeping node labels. Every one is `CREATE ... IF NOT EXISTS`.

| Name | Kind | Target | Key |
|---|---|---|---|
| `entity_tenant_id_unique` | constraint (unique) | `(e:Entity)` | `(e.tenant_id, e.id)` |
| `entity_tenant_normalized_name` | index | `(e:Entity)` | `(e.tenant_id, e.normalized_name)` |
| `entity_tenant_type` | index | `(e:Entity)` | `(e.tenant_id, e.entity_type)` |
| `relationship_tenant_id` | index | `()-[r:RELATES_TO]-()` | `(r.tenant_id, r.id)` |
| `alias_ref_tenant_entity_unique` | constraint (unique) | `(a:AliasRef)` | `(a.tenant_id, a.entity_id)` |
| `blocking_key_tenant_key_unique` | constraint (unique) | `(k:BlockingKey)` | `(k.tenant_id, k.key)` |

Names are stable and are what `SHOW CONSTRAINTS` / `SHOW INDEXES` report;
`tests/integration/graph/test_neo4j_store.py` asserts both the names and the
property lists above against a live server, so a rename or a reordered key is
a test failure rather than a silent plan change.

### `entity_tenant_id_unique` — composite `(tenant_id, id)` uniqueness on `:Entity`

The first statement in `_SCHEMA`, and the only uniqueness constraint on
`:Entity`:

```cypher
CREATE CONSTRAINT entity_tenant_id_unique IF NOT EXISTS
FOR (e:Entity) REQUIRE (e.tenant_id, e.id) IS UNIQUE
```

The key is composite because **two tenants may legitimately hold the same
entity id**. A constraint on `id` alone would reject the second tenant's write,
and that arrangement — one id, two tenants — is exactly what the compliance
suite's tenant-isolation properties construct, so a single-property constraint
fails the suite rather than merely being over-strict in production.

It carries the write-side guarantee for `upsert_entities`, whose statement is
`MERGE (e:Entity {tenant_id: row.tenant_id, id: row.id}) SET e = row`. A
`MERGE` on an unconstrained pair is match-or-create, not upsert: two concurrent
upserts of one entity can each fail to match and each create, leaving two
`:Entity` nodes with the same `(tenant_id, id)` and every later read returning
whichever the planner reached first. The constraint is what makes that pair
identify at most one node.

It does **not** cover same-batch duplicates. Neo4j gives no ordering guarantee
between a row's write and a later row's read within one `UNWIND`, so
`upsert_entities` deduplicates in Python first — `{(e.tenant_id, e.id): row}`,
last write winning — before the query runs. The constraint protects against
concurrent *statements*; the dict protects against duplicate *rows*.

The constraint's backing index is also what the always-true tenant-seek
predicate aims at: it is on `(tenant_id, id)`, and Neo4j will not seek a
composite index on its leading component alone, so a tenant-only read needs a
predicate mentioning `id` to get `NodeUniqueIndexSeek` instead of a label scan.
See [the `e.id IS NOT NULL` clause](#the-eid-is-not-null-tenant-seek-clause) for
the measurement. That predicate's safety — `id` is never null — is itself a
consequence of this constraint.

Two gates hold the shape:

- `tests/unit/graph/test_neo4j_adapter_is_wired.py::TestSchemaStatementsAreWellFormed::test_the_uniqueness_constraint_is_composite`
  asserts the literal `(e.tenant_id, e.id) IS UNIQUE` appears in the statement,
  and runs with no server.
- `tests/integration/graph/test_neo4j_store.py` calls `ensure_schema()` and then
  `SHOW CONSTRAINTS YIELD name, properties`, requiring
  `entity_tenant_id_unique` to report exactly `["tenant_id", "id"]` — name,
  membership and **order**, so a reordered key that would silently change every
  plan is a test failure.

### `entity_tenant_normalized_name` and `entity_tenant_type` — tenant-leading entity indexes

The second and third statements in `_SCHEMA`, and the only two plain indexes
on `:Entity`:

```cypher
CREATE INDEX entity_tenant_normalized_name IF NOT EXISTS
FOR (e:Entity) ON (e.tenant_id, e.normalized_name)

CREATE INDEX entity_tenant_type IF NOT EXISTS
FOR (e:Entity) ON (e.tenant_id, e.entity_type)
```

They exist for exactly one method: the two optional filters of
`find_entities`. `name` compiles to `e.normalized_name = $name` and
`entity_type` to `e.entity_type = $entity_type`, both appended to a
`MATCH (e:Entity {tenant_id: $tenant_id})`, so each index's key is that query's
predicate in order.

**Indexes, not constraints.** Neither field identifies an entity: a tenant may
hold any number of entities with one normalized name (that is the input
consolidation exists to reduce) and enormous numbers sharing an entity type. A
uniqueness constraint on either would reject legitimate writes.

**Plain range indexes**, because the port's operations on both fields are
**equality only**. `find_entities` documents `name` as matching
`Entity.normalized_name` *exactly* — "no fuzziness, no substring" — so nothing
here needs a text or full-text index, and no query issues `CONTAINS`,
`STARTS WITH` or a regular expression against these properties. A range index
serves equality and would also serve an ordered scan, which no query asks for.

**`entity_type` is a property, not a label.** Encoding type as a per-type node
label would need either `apoc.create.addLabels` or dynamically-built Cypher,
and would buy nothing: the only type operation in the port is an equality
filter, which an indexed property answers. That choice is half of why the
adapter runs on stock Neo4j 5 community — see
[installation and requirements](#installation-and-requirements). It also keeps
the label set fixed, so `entity_tenant_type` is one index rather than one label
per type discovered at runtime.

`normalized_name` and `entity_type` are stored **natively** while
`properties`, `external_ids` and `temporal` are JSON text. That split is not
about how the fields are shaped, it is about these two being the fields the
port queries on; see
[property encoding](#property-encoding). Indexing a JSON-encoded field would
index the encoding, and an equality filter written against it would depend on
key order and separator whitespace.

**Why the filtered queries do not get the tenant-seek clause.** `find_entities`
appends `_TENANT_SEEK` only when no other clause was built —
`" AND ".join(clauses or [_TENANT_SEEK])`. With a `name` or `entity_type`
equality present the planner already has an indexed predicate to seek on, and
these are the indexes it seeks; adding `e.id IS NOT NULL` on top would
contribute a filter step and no seek. So the always-true clause and these two
indexes are two halves of one arrangement: every read of `:Entity` reaches an
index, by whichever route the filters allow. See
[the `e.id IS NOT NULL` tenant-seek clause](#the-eid-is-not-null-tenant-seek-clause)
for the unfiltered route and its measurement.

**Filters are appended, never written as `$name IS NULL OR e.normalized_name =
$name`.** The nullable-parameter spelling produces one query shape for all four
filter combinations, and it defeats both indexes: a predicate the planner
cannot resolve until runtime forces a label scan even where a seek was
available. Four query strings are the price of four plans.

The `after` cursor adds `e.id > $after`, which is served by the uniqueness
constraint's index rather than by either of these.

`tests/integration/graph/test_neo4j_store.py::TestNeo4jSpecifics::test_ensure_schema_creates_the_lookup_indexes`
calls `ensure_schema()`, reads `SHOW INDEXES YIELD name, properties`, and
requires:

```python
assert indexes["entity_tenant_normalized_name"] == ["tenant_id", "normalized_name"]
assert indexes["entity_tenant_type"] == ["tenant_id", "entity_type"]
```

Exact lists, so both membership and **order** are pinned — an index redeclared
as `(normalized_name, tenant_id)` would still satisfy every functional test in
the suite while making every read scan, and this is the assertion that catches
it. The same test pins the *absence* of `entity_tenant_blocking_keys`; see
[the blocking-key constraint](#blocking_key_tenant_key_unique--tenant_id-key-uniqueness-on-blockingkey).

### `relationship_tenant_id` — index on `(tenant_id, id)` over `:RELATES_TO`

The fourth statement in `_SCHEMA`, and the only schema object on a
relationship type:

```cypher
CREATE INDEX relationship_tenant_id IF NOT EXISTS
FOR ()-[r:RELATES_TO]-() ON (r.tenant_id, r.id)
```

It is an **index, not a constraint** — not by preference. Relationship
uniqueness constraints are a Neo4j **enterprise** feature, and this adapter
targets Neo4j 5 community (see
[installation and requirements](#installation-and-requirements)). There is no
community-edition DDL that can make `(tenant_id, id)` unique across
`:RELATES_TO` edges, so the database cannot be asked to enforce it.

The pattern is **undirected** — `()-[r:RELATES_TO]-()` — which is how a
relationship index is declared; direction lives in the data, not the index.
Being on the relationship type rather than a label is also why this is one
index and not one per relationship type: `relationship_type` is a *property* on
the single `:RELATES_TO` edge, so the index stays single-shaped no matter how
many relationship types a tenant extracts.

### Why uniqueness is enforced in the upsert query instead

`upsert_relationships` does it in Cypher. After matching both endpoints it
runs:

```cypher
OPTIONAL MATCH ()-[old:RELATES_TO {tenant_id: row.tenant_id, id: row.id}]->()
DELETE old
WITH row, s, t
CREATE (s)-[r:RELATES_TO]->(t)
SET r = row
```

Delete-then-create, rather than `MERGE`, and the reason is the same one that
decides the index key. **The old edge is found by id, not by pattern.** An
upsert may *redirect* a relationship onto different endpoints, and an edge
found by matching the new `(s)-[:RELATES_TO]->(t)` pattern would not be the
one that needs removing — the stale edge would survive on its old endpoints
and the id would exist twice. Looking it up by `(tenant_id, id)` is what makes
re-upserting an id replace rather than duplicate, and that `OPTIONAL MATCH` is
exactly what `relationship_tenant_id` serves. Without the index it is a scan of
every relationship in the database, on every relationship upsert.

The key is `(tenant_id, id)` and not `id` for the reason every other key here
leads with the tenant: the lookup always knows its tenant, and an id-only index
would be seeked across every tenant's edges.

A `MERGE` cannot substitute for the pair, either. `MERGE` on the *pattern*
has the redirect problem above; `MERGE` on an unconstrained key is
match-or-create rather than upsert, which is precisely the guarantee the
missing constraint would have supplied.

Three consequences worth carrying:

- **The enforcement is query-level, not database-level.** Nothing stops a
  hand-written Cypher statement outside the adapter from creating a second
  `:RELATES_TO` with an id that already exists, and no write would fail. That
  is one more reason all Cypher lives in this one module —
  `tests/unit/graph/test_neo4j_adapter_is_wired.py` scans `src/` for Cypher
  keywords outside the adapter and fails if any appear.
- **Duplicate rows in one batch are removed in Python, before the query.**
  `upsert_relationships` builds `{(r.tenant_id, r.id): r for r in
  relationships}` and keeps the last write, then passes the deduplicated rows
  to `UNWIND`. Within a single `UNWIND` Neo4j gives no ordering guarantee
  between one row's write and a later row's read, so the query's own
  delete-before-create cannot be relied on to collapse two rows that share an
  id. Same division of labour as on `:Entity`: the dict handles duplicate
  *rows*, the query handles a duplicate *already stored*.
- **The write reports what it wrote.** The statement ends
  `RETURN r.tenant_id AS tenant_id, r.id AS id`, and any input row absent from
  that result raises `MissingEntityError` — a `MATCH` drops a row with a
  missing endpoint *silently*, and the endpoint check and the write are
  separate implicit transactions (`_run` opens a session per query), so an
  entity deleted in between would otherwise let a caller be told a batch
  succeeded that was never written. The result set is keyed on the **pair**,
  not on `id` alone: keyed on `id`, one tenant's successful write would vouch
  for another tenant's dropped row carrying the same id, defeating the check
  in exactly the case it exists for. See
  [errors the adapter raises](#errors-the-adapter-raises).

`tests/integration/graph/test_neo4j_store.py::TestNeo4jSpecifics::test_ensure_schema_creates_the_lookup_indexes`
pins the index by name and exact property list —
`indexes["relationship_tenant_id"] == ["tenant_id", "id"]` — so membership and
order are both fixed, and an index reversed to `(id, tenant_id)` fails there
rather than silently turning every relationship upsert into a scan.

### `alias_ref_tenant_entity_unique` — `(tenant_id, entity_id)` uniqueness on `:AliasRef`

The fifth statement in `_SCHEMA`, and the first of the two on bookkeeping node
labels:

```cypher
CREATE CONSTRAINT alias_ref_tenant_entity_unique IF NOT EXISTS
FOR (a:AliasRef) REQUIRE (a.tenant_id, a.entity_id) IS UNIQUE
```

`:AliasRef` is one node per entity id per tenant — a *handle* for an entity in
the alias graph, not a copy of it. Alias edges run `:AliasRef` → `:AliasRef`,
never `:Entity` → `:Entity`, because the port allows an alias to name an entity
this tenant has not extracted yet: the merge fold must not depend on the
extraction fold having run. Consequently `upsert_alias` `MERGE`s both ends
rather than `MATCH`ing them, and an `:AliasRef` may exist for an id no
`:Entity` has.

**Unique, not merely indexed**, and the reason is that `MERGE`. The statement
merges on the pair **twice in one query** — once for the alias node, once for
the canonical node — and `MERGE` on an unconstrained key is match-or-create
rather than upsert:

```cypher
MERGE (a:AliasRef {tenant_id: $tenant_id, entity_id: $alias_entity_id})
MERGE (c:AliasRef {tenant_id: $tenant_id, entity_id: $canonical_entity_id})
WITH a, c
OPTIONAL MATCH (a)-[old:ALIAS_OF]->()
DELETE old
WITH a, c
CREATE (a)-[r:ALIAS_OF]->(c)
SET r = $row
```

Without the constraint, two concurrent `upsert_alias` calls naming one entity
can each fail to match and each create, leaving two `:AliasRef` nodes for one
`(tenant_id, entity_id)`. That forks the alias chain, and a forked chain is a
**wrong answer**, not a slow one: `resolve_entity_ids` walks
`-[:ALIAS_OF*1..]->` to the node with no outgoing edge, so with two copies of a
node the canonical id it returns depends on which copy the planner walked into.
Consolidation then merges entities into the wrong survivor.

The same uniqueness is what makes **at most one canonical parent per entity** a
property of the store rather than a hope — the store's half of
`ConsolidationLog`'s double-merge rule. The `OPTIONAL MATCH (a)-[old:ALIAS_OF]->()
DELETE old` only sees every existing edge because `a` is *the* node for that
pair; with a duplicate it would clear one node's edge and leave the other's, so
re-recording an entity against a different canonical would fork the chain
instead of replacing it.

Two further behaviours rest on the pair being the node's identity:

- **`remove_alias` deletes the edge, not the node.** The `:AliasRef` survives
  with no outgoing edge, which resolution reads as "not an alias" — the same
  answer as no node at all. That is only true if there is exactly one node for
  the id; with a duplicate, the surviving copy could still carry an edge.
- **A cycle surfaces as an error rather than a wrong answer.** Cypher's
  relationship-uniqueness rule terminates a cyclic variable-length path by
  returning nothing, so `resolve_entity_ids` sees "no chain end" while
  `EXISTS { (a)-[:ALIAS_OF]->() }` is true, and raises `AliasCycleError`. See
  [errors the adapter raises](#errors-the-adapter-raises).

The tenant leads the key for the same reason it leads every other key here:
`entity_id` alone would reject a second tenant recording an alias for an id the
first tenant already holds, and every alias query already knows its tenant —
`find_aliases` pins `tenant_id` on **both** ends of the pattern, and
`resolve_entity_ids` on the node it starts from.

`:AliasRef` nodes are cleaned up only by `delete_by_tenant`, which
detach-deletes them in a statement of their own; they are not counted in its
return value, which is entities removed. See
[`delete_by_tenant()`](#delete_by_tenant).

Unlike `entity_tenant_id_unique`, this constraint is **not** pinned by an
integration assertion on `SHOW CONSTRAINTS` — `test_ensure_schema_creates_the_uniqueness_constraint`
checks only the `:Entity` constraint by name. What holds the statement is the
unit gate over `_SCHEMA` (every entry carries `IF NOT EXISTS`) plus the
behavioural alias tests the compliance suite runs against a live server.

### `blocking_key_tenant_key_unique` — `(tenant_id, key)` uniqueness on `:BlockingKey`

The sixth and last statement in `_SCHEMA`, and the second of the two on
bookkeeping node labels:

```cypher
CREATE CONSTRAINT blocking_key_tenant_key_unique IF NOT EXISTS
FOR (k:BlockingKey) REQUIRE (k.tenant_id, k.key) IS UNIQUE
```

`:BlockingKey` is one node per `(tenant_id, key)` — a key, not a key
*occurrence* — and entities point at it along `:BLOCKED_BY`. A block is
therefore the set of entities on one node's incoming edges.

**Unique, not merely indexed**, and as with `:AliasRef` the reason is a
`MERGE`. The second statement of `_write_blocking_keys` runs

```cypher
UNWIND $rows AS row
MATCH (e:Entity {tenant_id: row.tenant_id, id: row.id})
UNWIND row.blocking_keys AS key
MERGE (k:BlockingKey {tenant_id: row.tenant_id, key: key})
MERGE (e)-[:BLOCKED_BY]->(k)
```

and `MERGE` on an unconstrained key is match-or-create, not upsert. Without the
constraint, two concurrent upserts naming one key can each fail to match and
each create, leaving two `:BlockingKey` nodes for one `(tenant_id, key)` with
the `:BLOCKED_BY` edges split between them. `find_by_blocking_key` anchors on
*a* key node and expands, so it would then return whichever half the planner
reached — a **partial block**, which is the dangerous shape: consolidation
reads a short block as "those entities are not candidates" and skips the merge,
rather than raising. There is no error and no missing row anywhere; two
entities simply stop being compared.

**It is also the lookup index.** `find_by_blocking_key` and
`find_by_blocking_keys` seek the key node by `(tenant_id, key)` and follow its
incoming `:BLOCKED_BY` edges rather than filtering entities:

```cypher
MATCH (k:BlockingKey {tenant_id: $tenant_id, key: $key})<-[:BLOCKED_BY]-(e:Entity)
RETURN e ORDER BY e.id
```

That seek is the whole point of the design. Consolidation asks for a block
*per entity*, so a lookup that scans the tenant is O(n) per entity and O(n²)
across one. Both methods are covered by
`test_tenant_scoped_reads_seek_rather_than_scan_the_label`, which `EXPLAIN`s
the query the adapter actually issued and requires a `NodeUniqueIndexSeek` or
`NodeIndexSeek` with no `NodeByLabelScan` — the results are identical either
way, so only the plan can see this.

Note that these two reads do **not** carry the `e.id IS NOT NULL` tenant-seek
predicate: the key node's constraint is the indexed predicate they seek on. It
is the same arrangement as the entity filters — every read reaches an index by
whichever route it has.

**No index on the `blocking_keys` list property**, deliberately. A Neo4j range
index over a list indexes the list as a *single value*, so it answers "which
entities have exactly this array" and cannot answer membership; measured on
5000 entities across 100 tenants, the plan for `$key IN e.blocking_keys` was
`NodeByLabelScan` + `Filter` **with and without** such an index — identical.
The integration suite pins the absence by name
(`assert "entity_tenant_blocking_keys" not in indexes`), so adding an index
that costs every write and buys nothing is a test failure. A full-text index
was considered and rejected: it does work on arrays but **tokenises**, and
blocking keys are opaque identifiers (`"A430"`, `"person:ad"`) that must match
exactly. The full argument is
[ADR 0003](../adr/0003-blocking-keys-as-nodes.md).

The property survives alongside the nodes because it is what `_entity_from`
decodes, and it is the only place `None` ("no keys known") and `frozenset()`
("known to have none") stay distinguishable — an edge set cannot express that
difference. See [property encoding](#property-encoding).

Two operational consequences of the key node being shared:

- **An upsert rebuilds an entity's edges, in two statements.**
  `_write_blocking_keys` first deletes every existing `:BLOCKED_BY` edge of
  every row — unconditionally, including rows whose `blocking_keys` is null,
  since an entity going from "has keys" to "has none" must lose its edges too —
  and only then creates the new ones. A stale key that kept matching would make
  `find_by_blocking_key` return entities that no longer carry it. The node
  itself is untouched: it is shared with every other entity blocked on that
  key, so deleting it on one entity's re-upsert would unblock the rest.
- **Nothing reaps a key node left with no incoming edge.** Only
  `delete_by_tenant` ever deletes `:BlockingKey`, and it deletes the tenant's
  whole set. See
  [orphaned `:BlockingKey` nodes](#orphaned-blockingkey-nodes-are-not-reaped-on-upsert)
  and BACKLOG **B62**.

Like `alias_ref_tenant_entity_unique`, this constraint is not pinned by an
integration assertion on `SHOW CONSTRAINTS` —
`test_ensure_schema_creates_the_uniqueness_constraint` checks only the
`:Entity` constraint by name. What holds it is the unit gate over `_SCHEMA`
plus the plan assertions above, which fail if the seek it backs is not
available.

### Why every index leads with `tenant_id`

All six statements in `_SCHEMA` have `tenant_id` as the **first** component of
their key — the three on `:Entity`, the relationship index, and the two
bookkeeping constraints. None of the six is single-property.

Because there is **no cross-tenant read in the port**. Every method on
`GraphStore` takes a `TenantId`, and every Cypher statement the adapter issues
pins it: reads as a node pattern (`MATCH (e:Entity {tenant_id: $tenant_id})`),
writes as part of the merge key, and `find_aliases` on **both** ends of its
pattern. There is no query for which a key not starting with `tenant_id` would
be the right key, so there is no useful index that does not start there.

**A composite index is only seekable on a prefix of its key.** Neo4j will not
seek `(normalized_name, tenant_id)` given a tenant and a name — it can seek on
`normalized_name` alone, or scan. Reversing any of these keys therefore turns
its query into either a whole-label scan or a seek over every tenant's rows
that shares that name, filtered afterwards. The results are identical either
way, which is exactly why nothing behavioural notices.

The cost is not a constant factor. A tenant-scoped read that reaches the wrong
index does work proportional to **other tenants' data**, so a tenant's own
query gets slower every time an unrelated tenant writes. That is the difference
between multi-tenancy working and not, and it is the measurement recorded on
`_TENANT_SEEK`: on 5000 entities across 100 tenants, the tenant-only read
planned as `NodeByLabelScan` + `Filter` — reading every entity of every
tenant — until a predicate mentioning `id` let the planner seek
`(tenant_id, id)`. See
[the `e.id IS NOT NULL` tenant-seek clause](#the-eid-is-not-null-tenant-seek-clause).

Within a tenant, the second component is what discriminates, and it is chosen
per query: `id` for identity and the `after` cursor, `normalized_name` and
`entity_type` for the two `find_entities` filters, `key` for a block, and
`entity_id` for an alias handle. Leading with the tenant does not weaken that
— the first component bounds the read to one tenant's rows, and the second
discriminates inside them.

Tenant-leading keys are also what let uniqueness be *per tenant*. Two tenants
may legitimately hold the same entity id, the same blocking key, or an alias
for the same id; a constraint keyed on the second component alone would reject
the second tenant's write, and that arrangement is precisely what the
compliance suite's tenant-isolation properties construct. So the ordering is
not only a performance decision — for the three constraints it is a
correctness one.

Two gates hold the ordering, and they catch different failures:

- `tests/unit/graph/test_neo4j_adapter_is_wired.py::TestSchemaStatementsAreWellFormed::test_every_entity_index_leads_with_the_tenant`
  asserts every `_SCHEMA` statement `FOR (e:Entity)` contains `(e.tenant_id`.
  It needs no server, so it runs in the commit gate.
- `tests/integration/graph/test_neo4j_store.py::TestNeo4jSpecifics::test_ensure_schema_creates_the_lookup_indexes`
  compares each index's `properties` from `SHOW INDEXES` against an **exact
  list**, so order is pinned as well as membership — a key redeclared as
  `(normalized_name, tenant_id)` fails there rather than silently making every
  read scan.

And `test_tenant_scoped_reads_seek_rather_than_scan_the_label` checks the
consequence rather than the declaration: it `EXPLAIN`s the query the adapter
actually issued for `find_entities`, `find_by_blocking_key` and
`find_by_blocking_keys`, requiring a `NodeUniqueIndexSeek` or `NodeIndexSeek`
and no `NodeByLabelScan`. That is the assertion to extend when a new read is
added — a correct answer proves nothing here, and only the plan can see it.

## Graph model

### Node and relationship labels

Three node labels and three relationship types, and no others. The label set is
**fixed** — nothing in the adapter derives a label from data, which is what
lets `entity_tenant_type` be one index rather than one per entity type
discovered at runtime, and is half of why no `apoc` is required.

| Label / type | Cardinality | Constant in the module |
|---|---|---|
| `:Entity` | one node per `(tenant_id, id)` | — (written literally) |
| `:RELATES_TO` | one edge per relationship, source entity → target entity | `EDGE` |
| `:AliasRef` | one node per `(tenant_id, entity_id)` | `ALIAS_NODE` |
| `:ALIAS_OF` | one edge per alias, alias handle → canonical handle | `ALIAS_EDGE` |
| `:BlockingKey` | one node per `(tenant_id, key)` | `KEY_NODE` |
| `:BLOCKED_BY` | one edge per (entity, key) pair, entity → key | `KEY_EDGE` |

The five type names other than `:Entity` are module-level constants
interpolated into the query strings, so a rename is one edit rather than a
grep. `:Entity` is spelled out in each statement because it appears inside
node patterns the queries build by hand.

`:Entity` is the only label that holds domain data. `:AliasRef` and
`:BlockingKey` are bookkeeping — a handle and a key respectively — and neither
carries a copy of anything on the entity, so neither can go stale relative to
it.

#### One relationship type

`:RELATES_TO` is the **only** edge type between entities, and it carries the
domain's `relationship_type` as a *property*. A native per-type edge label
would need either `apoc` or Cypher's dynamic-type syntax, and buys nothing
here: the port's only type operation is an equality filter, which a property
serves. Both filtering reads spell it the same way, as a clause rather than as
part of the pattern:

```cypher
AND ($any_type OR r.relationship_type IN $relationship_types)
```

`get_relationships_for` applies it to the matched edge and `neighbors` applies
it to every edge of a variable-length path
(`all(rel IN rels WHERE ... )`). `$any_type` is `relationship_types is None`,
so "no filter" is a parameter value rather than a second query string —
unlike the `find_entities` filters, this predicate is not on an indexed
property, so one shape costs no plan.

Keeping one type also keeps `relationship_tenant_id` single-shaped:
relationship lookup is one index however many relationship types a tenant
extracts.

The edge stores `source_entity_id` and `target_entity_id` as properties **as
well as** being drawn between those nodes. That redundancy is deliberate — a
read decodes a whole `Relationship` from the edge alone, with no second match
against the endpoint nodes just to learn which way round it goes.

Direction lives in the data, not in the type. The three `direction` values map
to three patterns relative to the anchored entity `e` (`_PATTERNS`):

| `direction` | Pattern |
|---|---|
| `"out"` | `(e)-[r:RELATES_TO]->()` |
| `"in"` | `(e)<-[r:RELATES_TO]-()` |
| `"both"` | `(e)-[r:RELATES_TO]-()` |

The undirected pattern between *distinct* nodes yields each edge once, and
`Relationship` forbids self-loops, so `"both"` cannot double-count on that
account. What it can do is match an edge twice when **both** endpoints are in
the requested `entity_ids`, which is why `get_relationships_for` returns
`DISTINCT r` — the result is a set of edges, so each must appear once.
`neighbors` returns `DISTINCT e` for the same reason, a node being reachable by
more than one path.

`delete_relationship` matches `()-[r:RELATES_TO {tenant_id, id}]->()` — an
arbitrary direction on an anonymous pair, since the id identifies the edge and
its endpoints are irrelevant to deleting it.

#### Aliases: `:AliasRef` and `:ALIAS_OF`

Aliases live on **their own nodes**, not as edges between `:Entity` nodes, for
two reasons that both come from the port.

An alias may name an entity this tenant has not extracted yet — a merge can be
recorded before the extraction that creates its entities is folded, which is
exactly the ordering aliases exist to survive. So `upsert_alias` `MERGE`s both
`:AliasRef` handles rather than `MATCH`ing entities; a `MATCH (:Entity)` there
would drop the write *silently*. An `:AliasRef` may therefore exist for an id
no `:Entity` has, and that is a normal state, not a dangling reference.

And resolution is **transitive**: merging `B` into `A` and then `A` into `C` is
two legal merges, and `B` must resolve to `C`. That wants a variable-length
path — `resolve_entity_ids` walks `-[:ALIAS_OF*1..]->` to the node with no
outgoing edge — which wants an edge rather than a property.

The `:ALIAS_OF` edge carries the whole `Alias`: `id`, `tenant_id`,
`canonical_entity_id`, `alias_entity_id`, `alias_name`,
`alias_normalized_name`, `merged_at`, `merge_reason`. `find_aliases` therefore
decodes from the edge and touches neither node's properties — it matches one
hop only (`-[r:ALIAS_OF]->`, not `*1..`), because the question it answers is
what *this* merge absorbed, not what the chain eventually resolves to.

The two name fields are read back with `.get`: Neo4j drops a property written
as null, and both names are legitimately absent when the absorbed entity's
extraction has not been folded yet.

`remove_alias` deletes the **edge**, leaving the `:AliasRef` node behind with
no outgoing edge. Resolution reads that as "not an alias" — the same answer as
no node at all — so leaving it is correct as well as cheap: deleting it would
first have to check for incoming edges from other aliases in the chain.
`delete_by_tenant` reaps them.

#### Blocking keys: `:BlockingKey` and `:BLOCKED_BY`

One node per `(tenant_id, key)` — a key, not a key *occurrence* — with every
entity carrying that key pointing at it along `:BLOCKED_BY`. A block is
therefore the set of entities on one node's incoming edges, which is what
`find_by_blocking_key` and `find_by_blocking_keys` read:

```cypher
MATCH (k:BlockingKey {tenant_id: $tenant_id, key: $key})<-[:BLOCKED_BY]-(e:Entity)
```

Anchoring on the key node is the point: it is the seek those reads get instead
of a scan, and it is why the keys are nodes at all rather than an indexed list
property. See
[blocking keys as nodes](#blocking-keys-as-nodes-rather-than-an-indexed-list-property)
below and [ADR 0003](../adr/0003-blocking-keys-as-nodes.md).

The edge carries no properties, and the key node carries only `tenant_id` and
`key`. Everything about the *entity* stays on `:Entity`, including its
`blocking_keys` list — which survives alongside the nodes because it is the
only place `None` ("no keys known") and an empty set ("known to have none")
stay distinguishable. See [property encoding](#property-encoding).

An upsert **rebuilds** an entity's `:BLOCKED_BY` edges and never touches the
key node, which is shared with every other entity blocked on that key. Nothing
reaps a key node left with no incoming edge; see
[orphaned `:BlockingKey` nodes](#orphaned-blockingkey-nodes-are-not-reaped-on-upsert).

### Property encoding

A Neo4j property is a primitive or a **homogeneous array**. It cannot hold
`{"a": {"b": [1, "two"]}}`, an empty dict, or an integer past 64 bits — all of
which `Entity.properties` legitimately contains. So the encoding rule is not
"complex fields get special treatment", it is two rules that between them cover
every field:

> **Everything the port queries on stays native. Everything else that is not a
> primitive becomes JSON text.**

Four pure functions do the whole job — `_entity_row` / `_entity_from`,
`_alias_row` / `_alias_from`, `_relationship_row` / `_relationship_from`, and
the public `rows_carrying_keys`. No I/O and no driver, so the encoding is
tested in the **default gate** rather than only where a server is reachable
(`tests/unit/graph/test_neo4j_adapter_is_wired.py::TestEncodingIsPureAndReversible`).

| Domain field | Stored as | Why |
|---|---|---|
| `Entity.properties`, `Relationship.properties` | `properties_json` — `json.dumps` text | nesting, mixed arrays, empty containers, big ints all round-trip |
| `Entity.external_ids` | `external_ids_json` — `json.dumps` text | same |
| `Entity.temporal` | `temporal_json` — `TemporalExtent.model_dump_json()`, `None` when absent | same, via pydantic; read back with `model_validate_json` |
| `normalized_name` | native string | queried by `find_entities(name=...)`, indexed |
| `entity_type` | native string | queried by `find_entities(entity_type=...)`, indexed |
| `blocking_keys` | native list of strings, **sorted**; `None` stays null | homogeneous, so it is storable; sorted for a stable stored form |
| `extraction_method` | its `.value` string | an enum has no native representation |
| `relationship_type` | native string | filtered by `get_relationships_for` and `neighbors` |
| every id (`id`, `tenant_id`, `source_entity_id`, …) | canonical UUID string | Neo4j has no UUID type, and the string is what indexes and orders |
| `Alias.merged_at` | ISO text, **not** a native `DateTime` | see below |
| `confidence`, `name`, `description`, `source_id`, `source_text`, `model` | native | already primitives |

#### Why the JSON fields are not indexed

An index over a JSON-encoded field would index the **encoding**: an equality
filter written against it would depend on key order and separator whitespace,
so two dicts equal in Python could compare unequal in Cypher. That is the other
half of why `normalized_name` and `entity_type` stay native — see
[the tenant-leading entity indexes](#entity_tenant_normalized_name-and-entity_tenant_type--tenant-leading-entity-indexes).
Nothing in the port asks a question *inside* `properties` or `external_ids`, so
nothing is given up.

#### What the JSON round trip is pinned against

`test_properties_survive_the_round_trip_exactly` is parametrised over the
shapes a Neo4j property cannot hold — `{}`, `{"empty_dict": {}, "empty_list":
[]}`, `{"mixed": [1, "two", None, True, {"k": []}]}`, `{"big": 2**70}`, deep
nesting, an empty string key, non-ASCII — and asserts the decoded dict **and
the type of every value**:

```python
assert [type(v) for v in decoded.properties.values()] == [type(v) for v in properties.values()]
```

The second assertion is not decoration. `==` on a dict does not tell `True`
from `1`, so an encoding that collapsed bools to ints would pass the first line
alone.

The complementary gate is `test_the_row_holds_only_values_neo4j_can_store`,
which walks a row built from a nested-dict entity and requires every value to
be `None`, a primitive, or a **homogeneous** list of strings. That is the check
which catches a new field added to `Entity` and copied into `_entity_row` raw:
the driver rejects such a value at write time, a failure only an integration
run would otherwise see. `_alias_row` has the same guard
(`test_the_alias_row_holds_only_values_neo4j_can_store`).

What the suite does *not* pin: there is no unit round-trip example for a
populated `temporal`. It goes out through `model_dump_json` and back through
`model_validate_json`, so pydantic carries it, but the JSON-column argument
above is asserted for `properties` and `external_ids` only.

#### `merged_at` is ISO text

`_alias_row` writes `alias.merged_at.isoformat()` and `_alias_from` reads it
back with `datetime.fromisoformat`, rather than handing the driver a `datetime`
and letting Neo4j store a temporal.

The driver returns a `neo4j.time.DateTime` whose conversion back gives a
`tzinfo` that is a fixed offset even when the value was written as UTC, and the
port compares `Alias` objects for **equality**. A native temporal therefore
fails for `+05:30` and passes for `+00:00` — the shape of bug that ships,
because the obvious test uses UTC.
`test_an_alias_round_trips_with_its_offset_intact` is parametrised over UTC, a
microsecond-bearing UTC value, `+05:30` and `-08:00`, and asserts both
`decoded == alias` and
`decoded.merged_at.utcoffset() == merged_at.utcoffset()`. The parametrisation
is what separates the two implementations; a UTC-only example cannot.

#### `blocking_keys` keeps apart two states the edges cannot

`Entity.blocking_keys` is three-valued to the store, and only one of those
values produces edges:

| Domain value | Stored | Decoded by `_entity_from` |
|---|---|---|
| `None` — "no keys known" | null, so Neo4j **drops the property** | `node.get("blocking_keys")` absent → `None` |
| `frozenset()` — "known to have none" | empty array, which *is* stored | `[]` → empty `frozenset` |
| `frozenset({"A430"})` | `["A430"]`, sorted | `frozenset({"A430"})` |

An edge set cannot tell the first two apart: an entity with no keys and an
entity whose keys are unknown have exactly the same `:BLOCKED_BY` edges. That
is why the list property survives alongside the
[`:BlockingKey` nodes](#blocking-keys-as-nodes-rather-than-an-indexed-list-property)
rather than being replaced by them — the nodes serve the lookup, the property
carries the value. `test_blocking_keys_distinguish_absent_from_empty` is
parametrised over all four cases for exactly that reason.

The distinction is deliberately **not** carried into the edge write.
`rows_carrying_keys` — public and pure so the default gate can reach it, since
everything around it is Cypher — filters on plain truthiness, dropping both
`None` and `[]`, because neither creates an edge. Its test is parametrised over
both falsy cases because an implementation testing `is not None` passes for one
and fails for the other, and `test_a_mixed_batch_keeps_only_the_keyed_rows`
pins that the filter selects the right rows out of a batch rather than merely
returning something non-empty.

The list is **sorted** on the way out (`sorted(entity.blocking_keys)`). The
domain type is a set either way, so order carries no meaning; sorting gives a
stable stored form, so re-upserting an unchanged entity writes the same array
rather than a permutation.

#### Null and absent are the same state coming back

Neo4j drops a property written as null, so a decoder cannot distinguish "stored
as null" from "never written". Every optional field is therefore read with
`.get` rather than `[...]`: `original_entity_type`, `description`, `source_id`,
`source_text`, `model` and `temporal_json` on the entity, and `alias_name`,
`alias_normalized_name`, `merge_reason` on the alias. Reading one with `[...]`
would raise on a legitimately absent property — an `Alias` has no
`alias_name` whenever the absorbed entity's extraction has not been folded yet,
which `test_an_alias_with_no_name_round_trips` pins.

The other side of that is `upsert_entities`' `SET e = row`, which replaces the
property set **wholesale**. A field absent from the new value must not survive
from the old one — that is what last-write-wins means — and null entries in
`row` remove theirs. An accumulating `SET e += row` would leave a cleared
`description` reading as its previous value forever.

### Blocking keys as nodes rather than an indexed list property

An entity's blocking keys are stored **twice**: as the native `blocking_keys`
list property on `:Entity`, and as `:BlockingKey` nodes joined to the entity by
`:BLOCKED_BY`. The nodes are what serve the lookup; the property is what
`_entity_from` decodes. Neither is redundant, and the two halves are documented
separately because they answer different questions —
[property encoding](#blocking_keys-keeps-apart-two-states-the-edges-cannot) for
the value, this section for the read path.

Both reads anchor on the key node — which
[`blocking_key_tenant_key_unique`](#blocking_key_tenant_key_unique--tenant_id-key-uniqueness-on-blockingkey)
indexes — and expand over its incoming edges, rather than filtering entities:

```cypher
MATCH (k:BlockingKey {tenant_id: $tenant_id, key: $key})<-[:BLOCKED_BY]-(e:Entity)
RETURN e ORDER BY e.id
```

`find_by_blocking_keys` is the same pattern under an `UNWIND $keys AS wanted`,
returning `wanted AS key, e ORDER BY key, e.id` — **one round trip for the
whole batch**, pinned by
`test_batch_reads_are_one_round_trip_each`. It seeds its result dict from the
requested keys first, so a key nothing carries maps to `[]` rather than being
absent: the caller iterates the result, not its request.

#### Why not an index on the list property

A Neo4j range index over a list property indexes **the list as a single
value**. It answers "which entities have exactly this array" and cannot answer
membership. Measured on 5000 entities across 100 tenants, the plan for
`$key IN e.blocking_keys` was `NodeByLabelScan` + `Filter` **with and without
such an index — identical**.

That row is the load-bearing one, and it is why the absence of the index is
pinned by name rather than merely left undone:
`test_ensure_schema_creates_the_lookup_indexes` asserts
`"entity_tenant_blocking_keys" not in indexes`. Without the measurement this
reads as "nobody got round to indexing it", and adding the index would cost
every write and buy nothing.

The cost the scan imposes is not a constant factor. `CandidateFinder._block`
calls the batched lookup **once per subject entity**, so a lookup that scans
the tenant is O(n) per entity and **O(n²) across a tenant** — acceptable while
nothing called it, and not acceptable in the slice that started to.

**A full-text index was considered and rejected.** It does work on arrays, but
it *tokenises*, and blocking keys are opaque identifiers (`"A430"`,
`"person:ad"`) that must match exactly.

The measured result, one tenant of 20 000 entities, warm, median of 15 runs:

| query | time | plan |
|---|---|---|
| `(:BlockingKey)<-[:BLOCKED_BY]-(:Entity)` | **4.18 ms** | `NodeUniqueIndexSeek` + `Expand(All)` |
| `$key IN e.blocking_keys` with the tenant seek | **19.89 ms** | `NodeUniqueIndexSeek` + `Filter` |

4.8× at that size, and the part that matters is that **the shapes differ, not
the constants**: the node form seeks one key node and expands its edges, the
property form seeks the tenant and filters every entity in it, so the gap grows
with tenant size. The timings are not re-measured by anything in the tree — the
benchmark was a throwaway script — but the *plans* are, by
`test_tenant_scoped_reads_seek_rather_than_scan_the_label`, which `EXPLAIN`s
the query the adapter actually issued for both blocking-key reads and requires
a `NodeUniqueIndexSeek` or `NodeIndexSeek` with no `NodeByLabelScan`. The
results are identical either way, so only the plan can see this. The full
argument, including its provenance, is
[ADR 0003](../adr/0003-blocking-keys-as-nodes.md).

#### The trap the decision creates: edges are rebuilt, not added to

Nodes mean a **second write path**, and it is not a one-line one.
`upsert_entities` calls `_write_blocking_keys` after writing the entity rows,
and that method **rebuilds** each entity's `:BLOCKED_BY` edges rather than
adding to them. A re-upsert that drops a key must stop matching on it;
otherwise `find_by_blocking_key` keeps returning an entity that no longer
carries the key, and consolidation proposes merges from evidence that has been
withdrawn — a candidate pair sharing a withdrawn key looks exactly like one
sharing a live key.
`test_find_by_blocking_key_reflects_the_latest_write` in the compliance suite
is what holds that honest: after re-upserting an entity whose only key changed
from `"old"` to `"new"`, `"old"` returns `[]` and `"new"` returns the entity.

Two statements, in order:

```cypher
UNWIND $rows AS row
MATCH (e:Entity {tenant_id: row.tenant_id, id: row.id})-[old:BLOCKED_BY]->()
DELETE old
```

```cypher
UNWIND $rows AS row
MATCH (e:Entity {tenant_id: row.tenant_id, id: row.id})
UNWIND row.blocking_keys AS key
MERGE (k:BlockingKey {tenant_id: row.tenant_id, key: key})
MERGE (e)-[:BLOCKED_BY]->(k)
```

**Two statements rather than one** because delete-then-create in a single query
needs `WITH DISTINCT` to undo the row multiplication `OPTIONAL MATCH` causes
over existing edges, which then reads as if the distinctness were about the
keys rather than about the plan. Two statements each say one thing.

**The delete pass covers every row**, including rows whose `blocking_keys` is
null. An entity going from "has keys" to "has none" must lose its edges just as
much as one going from one key to another, and that is the case a create-only
implementation gets wrong.

**The create pass is skipped when no row carries keys.** The rows are selected
by `rows_carrying_keys`, which is *public and pure* precisely so the default
gate can test it without a server — everything around it is Cypher. It filters
on plain truthiness, dropping both `None` and `[]`, because neither creates an
edge; its test is parametrised over both falsy cases, since an implementation
testing `is not None` passes for one and fails for the other.

So a batch upsert is **three round trips** — entities, stale key edges, new key
edges — or two when nothing in the batch carries keys. What matters is that the
count **does not grow with the batch**:
`test_upsert_entities_is_a_bounded_number_of_round_trips` asserts it at batch
sizes 5 and 50, because a fixed count asserted at one size cannot tell a
bounded implementation from a per-entity loop. Measured cost of the extra
statements: 500 entities carrying three keys each took 100.1 ms against 40.8 ms
for 500 carrying none — 2.45×, and it is two extra statements per *batch*
rather than two per entity. That bounding is the property to protect.

The key **node** is never touched by the rebuild. It is shared with every other
entity blocked on the same key, so deleting it on one entity's re-upsert would
unblock the rest — which is also why nothing reaps a node the delete pass has
just left childless. See
[orphaned `:BlockingKey` nodes](#orphaned-blockingkey-nodes-are-not-reaped-on-upsert).

#### Neither adapter's behaviour distinguishes the layout

`InMemoryGraphStore` keeps no key index at all — `find_by_blocking_key` walks
the tenant's entities and tests `key in entity.blocking_keys` — so a key exists
there only as a member of some entity's `frozenset`. Everything in this section
is therefore invisible to `tests/compliance/graph_store.py`, which is
adapter-agnostic by construction and can only state claims both adapters
satisfy (see [ADR 0002](../adr/0002-two-store-ports.md)). The shared suite
constrains what a read *returns*; the cost and the node-level layout are both
outside it.

That is the general rule for this part of the adapter: **a claim about Neo4j
storage layout cites an integration test in
`tests/integration/graph/test_neo4j_store.py` or is labelled unverified.** The
plan and round-trip assertions above meet that bar. See
[running the integration and mutation suites](../how-to/run-integration-and-mutation-suites.md)
for how to run them, and [quality gates](quality-gates.md) for what the
default, server-free gate covers instead.

### Orphaned `:BlockingKey` nodes are not reaped on upsert

Which leads to the one thing to know about this model in operation. Deleting an
entity's `:BLOCKED_BY` edges can leave a key node with **no incoming edge**,
and nothing removes it. The only statement in the adapter that ever deletes a
`:BlockingKey` is the third statement of `delete_by_tenant`, which drops the
tenant's whole key set.

An orphan matches nothing, so no read returns a wrong answer — the leak is
growth, not correctness. But a tenant whose entities churn their keys, which is
what re-extracting a changed document does, accumulates one node per distinct
key ever seen until the tenant is wiped. **The size is unmeasured**: nothing in
the suite counts key nodes after a re-upsert.

[ADR 0003](../adr/0003-blocking-keys-as-nodes.md) currently states that these
orphans *are* cleaned up. That sentence does not describe the code. Tracked as
BACKLOG **B62**, which is the entry to read before either adding the reap or
correcting the ADR.

Note that `delete_by_tenant` deletes key nodes by `tenant_id`, so its cleanup
is per tenant and complete for that tenant — see
[`delete_by_tenant()`](#delete_by_tenant).

## Query behaviour worth knowing

### The `e.id IS NOT NULL` tenant-seek clause

Reads that filter on nothing but `tenant_id` append the always-true predicate
`e.id IS NOT NULL`. `id` is part of the uniqueness constraint, so it is never
null and the clause changes no result. It changes the **plan**: measured on
5000 entities across 100 tenants, `MATCH (e:Entity {tenant_id: $t}) RETURN e
ORDER BY e.id` planned as `NodeByLabelScan` + `Filter` — reading every entity
of every tenant — while the same query with the clause planned as
`NodeUniqueIndexSeek`.

It is used only where no other indexed predicate is present. Alongside a
`normalized_name` or `entity_type` equality the planner already seeks the
better index, and the clause would add a filter step for nothing.

### Errors the adapter raises

- **`MissingEntityError`** — from `upsert_relationship` / `upsert_relationships`
  when an endpoint entity does not exist in that tenant. Endpoints are checked
  in one batch query up front so the error can name *which* endpoint is absent,
  in the caller's order (source before target within an edge). The write then
  reports what it wrote and the batch is re-checked, because a `MATCH` drops a
  row with an absent endpoint *silently* — the port's contract here is
  write-or-raise.
- **`AliasCycleError`** — from `resolve_entity_ids` when an id has an outgoing
  `:ALIAS_OF` edge but no chain end. Cypher's relationship-uniqueness rule
  terminates a cyclic variable-length path by returning nothing, so a cycle
  cannot hang the query; it surfaces as this error rather than a wrong answer.

`ValueError` and `TypeError` come from argument validation (`limit` negative,
`direction` not one of `out`/`in`/`both`, `depth` negative or not an `int`).

### Read-result isolation

The port requires that mutating a read result cannot change stored state. This
adapter gets it for free: every read decodes **fresh domain objects** out of
driver records, so there is no stored object to hand out. That is the one
respect in which a real database is easier to be correct in than a dictionary —
`InMemoryGraphStore` has to copy explicitly.

### `delete_by_tenant()`

Three statements, and the return value is **entities removed only**:

1. `MATCH (e:Entity {tenant_id}) DETACH DELETE e` — takes the tenant's
   relationships and `:BLOCKED_BY` edges with the entities, and its `count(e)`
   is what the method returns.
2. `:AliasRef` nodes for the tenant are detach-deleted separately. Alias
   bookkeeping is not an entity, so it is not counted — but it must go, or a
   wiped tenant replays its merges over aliases that survived and
   `delete_by_tenant` stops being a reset in exactly the case a rebuild needs
   it to be one.
3. `:BlockingKey` nodes for the tenant are detach-deleted. Step 1 removed the
   *edges*, not the key nodes.

## Running Neo4j locally

`docker-compose.test.yml` in the repo root defines the backend:

```
docker compose -f docker-compose.test.yml up -d neo4j
```

- image `neo4j:5-community`
- ports **7688** (bolt, mapped from 7687) and **7475** (http, from 7474)
- auth `neo4j/redstring`
- a healthcheck running `cypher-shell ... 'RETURN 1'` every 5s, 30 retries

**The ports are deliberately non-default** so the container cannot collide with
a local Neo4j install or with another project's container on 7687/7474. The
Postgres service in the same file moves for the same reason (5434).

The healthcheck exists because `up -d` returns when the container is *running*,
not when the server can *serve*; an immediate connect races store recovery.

### Integration-test environment variables

| Variable | Default |
|---|---|
| `KG_TEST_NEO4J_URI` | `bolt://localhost:7688` |
| `KG_TEST_NEO4J_USER` | `neo4j` |
| `KG_TEST_NEO4J_PASSWORD` | `redstring` |

The defaults match `docker-compose.test.yml`, so the suite needs no environment
at all against the supplied container.

### Running the integration suite

```
KG_COMPLIANCE_MAX_EXAMPLES=10 uv run pytest -m integration
```

`-m integration` is **required**: `addopts` deselects the marker so the commit
gate stays infra-free. `KG_COMPLIANCE_MAX_EXAMPLES` bounds how many examples
each hypothesis-driven compliance property draws (default 50); lowering it
trades coverage for wall time.

If Neo4j is not reachable the suite skips, and the skip probe runs an actual
`RETURN 1` and requires the answer to be `1` — a TCP connect, and even
`verify_connectivity()`, both succeed against a server that cannot yet serve.

See [running the integration and mutation suites](../how-to/run-integration-and-mutation-suites.md)
for the full procedure, and [quality gates](quality-gates.md) for what runs
without a backend.

## Wiring the adapter at a composition root

```python
from redstring.graph.adapters.neo4j import Neo4jGraphStore

async with Neo4jGraphStore.connect(
    "bolt://localhost:7688",
    auth=("neo4j", "redstring"),
) as store:
    await store.ensure_schema()
    entities = await store.find_entities(tenant_id, entity_type="person", limit=20)
```

The block is the recommended form: it closes the driver on every path out,
including cancellation, and it suppresses nothing. The equivalent
`try`/`finally` around `await store.close()` still works and is what a caller
whose lifetime is not a block should write.

If the driver is managed by the surrounding application, construct the store
around it instead and let the application close it:

```python
store = Neo4jGraphStore(driver)  # close() is then a no-op
```

## See also

- `redstring.ports.graph_store.GraphStore` — the port this implements
- `redstring.graph.adapters.memory.InMemoryGraphStore` — the in-process
  adapter, exercised by the same compliance suite in the default gate
- `tests/compliance/graph_store.py` — the shared suite both adapters must pass
- [ADR 0002: two store ports](../adr/0002-two-store-ports.md)
- [ADR 0003: blocking keys as nodes](../adr/0003-blocking-keys-as-nodes.md)
- [How-to: implement a store adapter](../how-to/implement-a-store-adapter.md)
- [How-to: run the integration and mutation suites](../how-to/run-integration-and-mutation-suites.md)
- [Reference: quality gates](quality-gates.md)
