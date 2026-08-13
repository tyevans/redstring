# Implement a store adapter

This guide walks through adding a new backend behind one of `redstring`'s
six ports — `GraphStore`, `VectorStore`, `ChunkStore`, `Cache`,
`EmbeddingProvider`, or `LlmProvider` — and proving it correct against the
shared compliance suites in `src/redstring/testing/`.

Follow it when you want to store the graph in something other than the two
adapters that ship (`redstring.graph.adapters.memory` and
`redstring.graph.adapters.neo4j`), keep vectors somewhere other than
`redstring.vector.adapters.memory` or `.pgvector`, retain passages somewhere
other than `redstring.chunks.adapters.memory` or `.postgres`, coordinate the
LLM transport through something other than the default in-process cache,
embed text through something other than the LangChain embedding adapter, or
speak to a model without going through the LangChain adapter.

The work is the same shape every time:

1. Write the class against the `Protocol` in `src/redstring/ports/`.
2. Subclass the compliance suite for that port and give it a way to build a
   fresh, empty instance.
3. Add the read-method isolation and tenant tests the coverage gates require.
4. Run the suite — serially, one adapter per invocation, in a venv synced with
   `--all-extras`.
5. Add the tests the port cannot specify, and place the module so
   `lint-imports` agrees with where you put it.

Two things are worth knowing before you start, because they determine how much
of this is optional (none of it) and how much is mechanical (most of it). The
ports are `runtime_checkable` `Protocol`s, so there is no base class to inherit
and no import from your adapter into the library — structural conformance is
all that is asked at type-check time. And precisely because the type checker
asks so little, **the compliance suite is the contract**: idempotent writes,
read-your-writes, strict tenant scoping, and returning copies rather than live
internal state are all requirements a `Protocol` cannot express and a passing
`mypy --strict` run will not notice.

Steps 1–4 are the required path for every adapter. Step 5 is what stops a
correct adapter from being an unusable one. Steps 1 and 2 are the two that are
specific to writing an adapter, so they get the long sections below; steps 3
to 5 are already defined elsewhere in the repository, and the "Steps 3 to 5"
section at the end of this page says where each one lives rather than
restating it here. A
procedure copied into a second place is a procedure that will disagree with
itself.

## Before you start

Have a venv synced with **`uv sync --all-extras`** — not `--extra dev`. The
`neo4j` and `llm` extras are separate, and a venv without them fails
*collection* on the modules that import them rather than skipping them, which
reads as a green run that quietly tested nothing.

Decide which port you are implementing before you write anything, because the
opt-in mechanics differ: `GraphStore`, `VectorStore` and `ChunkStore`
compliance builds a store per hypothesis example through a `new_store()`
method you implement, `Cache` compliance takes a `cache` fixture,
`EmbeddingProvider` compliance takes a `provider` fixture, and `LlmProvider`
has no compliance suite at all.

### What the compliance suites are (and why the port docstring is not the contract)

The ports in `src/redstring/ports/` are `runtime_checkable` `Protocol`s. That
buys structural conformance and nothing more: `mypy --strict` will confirm your
`get_entity` has the right signature and cannot confirm it scopes by tenant,
returns a copy, or sees a write that has already returned.

The executable definition lives in `src/redstring/testing/`, and each suite's own
docstring says so — "**Every `GraphStore` adapter must pass this suite
unchanged.** It is the executable definition of the port; the prose in
`redstring.ports.graph_store` describes what these tests enforce." Read the
port docstring for intent and the suite for the requirement. Where they appear
to disagree, the suite wins and the docstring is a bug.

Four requirements the `Protocol` cannot state, all asserted by the suites:

- **Read-your-writes.** Once an `upsert_*` call has returned, its effect is
  visible to the next read on the same store. There is no "eventually" inside a
  store. Lag belongs between the event log and the projection — see
  [Drive projections from an event store](drive-projections-from-an-event-store.md)
  — never here.
- **Tenant scoping.** Every read is scoped by `tenant_id`, and no read may see
  another tenant's rows.
- **Copies, not live internal state.** A read that hands back the stored object
  is correct at the moment of the read and wrong immediately afterwards, so no
  assertion about the returned value can catch it. This is why step 3 exists as
  a gate rather than as advice.
- **Idempotent writes.** Upserting the same thing twice leaves the store in the
  state applying it once would. This is not a nicety: a store is a projection
  of the event log, and projection handlers replay.

Two files in `src/redstring/testing/` are not suites: `strategies.py` supplies the
hypothesis strategies (including `vectors(dimension)`, which generates
float32-representable components and excludes the zero vector, since cosine is
undefined at the origin and the port rejects it), and `__init__.py` states the
package is deliberately not collected — no module matches `test_*.py` and no
class matches `Test*`, so a suite runs only where an adapter subclasses it.

### The six ports and what each requires

Every port in `src/redstring/ports/` has a row here, and every compliance
suite in `src/redstring/testing/` appears in the second column.
`tests/unit/test_the_adapter_guide_names_every_compliance_suite.py` is what
keeps that true in both directions: a suite added without a row fails, and a
row naming a suite that has been deleted fails too.

| Port | Compliance suite | You supply | Shipped adapters |
|---|---|---|---|
| `GraphStore` | `redstring.testing.graph_store.GraphStoreCompliance` | `new_store()`, optionally `dispose(store)` | `graph.adapters.memory`, `graph.adapters.neo4j` |
| `VectorStore` | `redstring.testing.vector_store.VectorStoreCompliance` | `new_store()` returning a store of `self.DIMENSION` | `vector.adapters.memory`, `vector.adapters.pgvector` |
| `ChunkStore` | `redstring.testing.chunk_store.ChunkStoreCompliance` | `new_store()`, optionally `dispose(store)` | `chunks.adapters.memory`, `chunks.adapters.postgres` |
| `Cache` | `redstring.testing.cache.CacheCompliance` | a `cache` fixture | `llm.cache.memory.MemoryCache`, `llm.cache.redis.RedisCache` |
| `EmbeddingProvider` | `redstring.testing.embedding_provider.EmbeddingProviderCompliance` | a `provider` fixture | `llm.adapters.fake_embedding`, `llm.adapters.langchain_embedding` |
| `LlmProvider` | *(none)* | adapter-specific tests plus the leak gate | `llm.adapters.fake`, `llm.adapters.langchain` |

**`GraphStore`** is the largest surface — entities, aliases, relationships and
neighbour traversal, plus `delete_by_tenant`. `find_by_blocking_key` and
`find_by_blocking_keys` are there because blocking keys are nodes rather than
an index; see [ADR 0003](../adr/0003-blocking-keys-as-nodes.md).

**`VectorStore`** is `upsert`/`upsert_many`, `get`, `search`, `delete`,
`delete_by_tenant`, and a `dimension` property fixed at construction. Its
suite states the exactness contract in two tiers — exact behaviour on tens of
vectors, recall-only on the larger dataset — and offers no `is_approximate`
opt-out. [ADR 0012: no ANN index in a multi-tenant vector
store](../adr/0012-no-ann-index-in-a-multi-tenant-vector-store.md) is the
reasoning behind the shape; [Use the pgvector
store](use-the-pgvector-store.md) is the worked backend.

**`ChunkStore`** is the passage corpus: `upsert_many`, `replace_source`,
`get`, `get_by_source`, `get_by_entity`, `lexical_candidates`,
`delete_by_source` and `delete_by_tenant`, declared as four capability
protocols (`ChunkWriter`, `ChunkReader`, `LexicalCandidateSource`,
`ChunkPurge`) composed into one. Three of its rules have no analogue in the
other store ports. Ids are **content-addressed** — `chunk_id(source_id, text)`
— so the same passage of the same document under two tenants has the *same
id*, which makes a `(tenant_id, id)` key compared on `id` alone a live defect
here rather than a `uuid4()`-improbable one. `replace_source` is **one
atomic operation**, not an `upsert_many` followed by a `delete`, and an empty
`chunks` argument legally means "this source now has no chunks". And
`lexical_candidates` returns term statistics and **does not rank** — ranking
is `domain/chunk_ranking.py`, so that two adapters cannot disagree about
relevance. Because `chunk_index` is not unique, `get_by_source` orders by
`(chunk_index, id)`.

**`EmbeddingProvider`** is two properties and one method: `model`,
`dimension`, and `embed(texts)`. The contract its suite exists to hold is
**positional** — one vector per input, in input order — because an adapter
that batches, retries a partial failure, or deduplicates identical texts can
return the right vectors in the wrong order and a caller zipping them onto
entities will store the mismatch without raising. Note that the suite compares
by **cosine, not `==`**: a real server's floating-point accumulation depends
on how a batch was packed, so the same text embedded alone and inside a batch
differs in the low bits. `dimension` is declared by the provider (see [ADR
0017](../adr/0017-the-embedding-provider-port.md)) and is realistic, not
small — `FakeEmbeddingProvider` defaults to 768 on purpose, because a fake at
width 8 invites a check written with `is not` that passes on every integer
CPython caches and rejects every real vector.

**`Cache`** is deliberately not a general cache. Alongside `get`/`set`/
`increment`/`delete`/`close` it carries `record_hit(key, *, at, ttl_seconds)`,
`count_hits(key, *, since)` and `oldest_hit(key, *, since)` — a hit window for
the LLM transport's resilience, with **time passed in as an epoch float** so a
test asserting "an event 90 seconds ago" is a number rather than a 90-second
sleep. Two things its suite pins that a reference implementation would let you
miss: `get` returns `str`, not `bytes` (a Redis client at its defaults returns
bytes and would match no string literal in production), and a missing key is
`None`, not an error. See [ADR 0013: resilience behind the cache
port](../adr/0013-resilience-behind-the-cache-port.md) and [Harden model
calls](harden-model-calls.md).

**`LlmProvider`** is one property and one method: `model`, and
`extract(text, schema, *, system_prompt=None)`. It has no compliance suite
because there is nothing to store and nothing to read back. What stands in for
one is covered in step 2 — the no-leak gate, and the rule that empty output
raises (`EmptyCompletionError`, `MalformedCompletionError`) rather than
returning an empty result. [ADR 0008: the two non-store
ports](../adr/0008-the-two-non-store-ports.md) explains why `Cache` and
`LlmProvider` are as small as they are; [ADR
0002](../adr/0002-two-store-ports.md) explains why the two stores are separate
ports rather than one.

## Step 1: Write the adapter against the Protocol in `src/redstring/ports/`

Open the port module and write a plain class with the same methods. There is
nothing to inherit — all six ports are `runtime_checkable` `Protocol`s, so
conformance is structural. `InMemoryGraphStore` does not import `GraphStore`
at all; it is a `GraphStore` because its methods match.

**Four of the six ports are composed from smaller capability protocols**, and
the other two are single capabilities rather than an oversight: `GraphStore`
is five ([ADR 0016](../adr/0016-graph-store-is-five-capabilities.md)),
`ChunkStore` four and `Cache` two ([ADR
0026](../adr/0026-chunk-store-and-cache-are-capabilities-too.md)),
`VectorStore` three ([ADR
0027](../adr/0027-vector-store-is-three-capabilities-and-so-is-every-collaborator.md)),
while `LlmProvider` and `EmbeddingProvider` are one method and a property
apiece with nothing to slice. The decomposition changes nothing for an adapter
implementing a whole port — you write every method either way — and it lets a
caller depend on only the slice it uses.

Put the module where the layer contract expects it: `graph/adapters/`,
`vector/adapters/`, `chunks/adapters/`, `llm/cache/`, `llm/adapters/`. The
`lint-imports` contract in `pyproject.toml` is the authority on where a new
module may sit, and it runs on commit.

Four things hold for every method on all three store ports:

- **Everything is `async`**, including the in-memory adapters. A synchronous
  reference implementation would make callers write two code paths.
- **Every method is tenant-scoped.** `tenant_id` is a parameter, not ambient
  state, and there is no cross-tenant read. Key your storage on
  `(tenant_id, id)` and compare *both* components — a key compared on `id`
  alone lets one tenant's write vouch for another's, and it passes every test
  whose ids come from `uuid4()`.
- **Writes are idempotent, last-write-wins.** Stores are projections and
  handlers replay, so upserting the same id twice leaves one row holding the
  later value. Deletes return `bool` (`False` for an absent id) or a count,
  and never raise on something already gone.
- **Reads return copies.** `InMemoryGraphStore` calls `model_copy(deep=True)`
  on the way in *and* on the way out, closing both directions: handing out a
  reference lets a caller mutate stored state, and keeping the caller's object
  lets them mutate it afterwards. A database-backed adapter gets this for
  free by deserialising; an in-process one has to do it deliberately. Step 3
  is the gate that checks you did.

### Every adapter owes a lifetime: `close()`, `__aenter__` and `__aexit__`

`GraphStore`, `VectorStore`, `ChunkStore` and `Cache` all declare the release
trio, because every capability protocol inherits
`redstring.ports.lifecycle.AsyncClosable` ([ADR
0028](../adr/0028-a-capability-declares-its-own-release.md)). So this is not a
choice your adapter makes — the port already promised it, and an adapter
offering only `close()` is the odd one out rather than the norm.

```python
async def close(self) -> None:
    """Release the pool. Safe to call twice."""
    if self._owns_pool:
        await self._pool.close()


async def __aenter__(self) -> Self:
    return self


async def __aexit__(
    self,
    exc_type: type[BaseException] | None,
    exc: BaseException | None,
    tb: TracebackType | None,
) -> None:
    await self.close()
```

Four rules, each of which one of the shipped adapters exists to demonstrate:

- **`__aenter__` returns the store**, not `None`, so `async with ... as store`
  binds something usable.
- **`__aexit__` returns `None` and never suppresses.** The annotation is
  `None` rather than `bool` on purpose: `bool` invites `return True`, which
  reads as "handled" and silently eats every exception raised in the body —
  including the `CancelledError` of a request that timed out, whose caller
  would then be told the work completed.
- **Exit goes through `close()`, so ownership still decides.** An adapter
  handed a driver or pool it did not create leaves it open, inside a block as
  much as outside one — `Neo4jGraphStore` flips that flag only in `connect()`,
  and `RedisCache` takes `owns_client=False` by default. Without the ownership
  check, a per-example `dispose` in the compliance suite takes the whole
  session's shared connection down with it and every later example fails on a
  closed connection.
- **An adapter that owns nothing writes all three anyway, as honest no-ops.**
  `InMemoryGraphStore` holds dictionaries the interpreter already owns, so
  "release what you hold" is satisfied by doing nothing — and it must still
  *work* after its own block, because "drop everything on close" is the
  available over-implementation. `MemoryCache` deliberately does discard state,
  which is right for expiring state and wrong for a store. Keeping the
  in-memory adapters out of the promise would be the same thing as not making
  it: a caller cannot write one lifetime discipline against a port whose
  adapters disagree about whether it has one.

`tests/unit/test_ports_declare_the_block_form.py` and
`tests/unit/test_adapters_close_on_block_exit.py` are the gates. Their subject
set is derived structurally — every class satisfying a capability — so a fifth
adapter is caught by being written rather than by someone remembering the
file, and `LlmProvider` and `EmbeddingProvider` are asserted *not* to be
closable so that "not yet" and "deliberately not" stay distinguishable.

For what the block form looks like from the caller's side, see the worked
sections in [the Neo4j graph store reference](../reference/neo4j-graph-store.md),
[Use the pgvector store](use-the-pgvector-store.md) and [Harden model
calls](harden-model-calls.md).

### GraphStore

Beyond entities and relationships, the port carries the alias surface —
`upsert_alias`, `remove_alias`, `find_aliases`, `resolve_entity_ids` — and the
blocking-key reads. Two behaviours are easy to miss on a first pass:

- **Dangling edges are not permitted.** `upsert_relationship` raises
  `MissingEntityError` when either endpoint is absent from that tenant, and
  `upsert_relationships` is **atomic**: a failure writes nothing at all, not
  even the elements before the offending one. Validate the whole batch before
  writing any of it — Neo4j does this in one query because per-element round
  trips would be the expensive way to write it, and the in-memory adapter
  makes two passes. This used to be the opposite, and the weaker promise is
  what let those two adapters differ in a way nothing asserted; see
  [ADR 0019](../adr/0019-batch-relationship-writes-are-atomic.md). Do not make
  your adapter more permissive than the port — an adapter that accepts a
  dangling edge is useless as a reference, because tests written against it
  pass there and fail on Neo4j.
- **There is no `delete_entity`, and there will not be one.** A merged entity
  survives as an alias node. `delete_relationship` and `delete_by_tenant` are
  the only removals.

`find_entities` states a **total order** that is part of the contract, not an
implementation detail: ascending by `Entity.id` compared as its canonical
lowercase hyphenated string. Because every UUID renders to the same
fixed-width hex, that coincides with unsigned big-endian ordering of the
128-bit value, so a backend may index the text or the native UUID and satisfy
it identically. `after` resumes strictly after that id and **need not still
exist** — that is what makes a page resumable across deletes. A cursor over an
undefined order is not resumable, so do not leave the ordering to whatever the
backend returns.

Argument validation is part of the contract too: a negative `limit` or `depth`
raises `ValueError`, a `direction` outside `{"out", "in", "both"}` raises
`ValueError`, `depth=0` yields `[]`, an unknown id yields `[]` rather than
raising, and `relationship_types=[]` matches nothing while `None` means no
filter. The batch reads (`get_entities`, `find_by_blocking_keys`,
`get_relationships_for`) exist so consolidation is one round trip — implement
them as one query, not a loop over the singular form, and note that
`get_relationships_for` returns a *set* of edges, so an edge with both
endpoints in the input appears once.

### VectorStore

`dimension` is fixed at construction and every dimension mismatch raises
`DimensionMismatchError`, in **every** adapter including the in-memory one.
A zero vector raises `ValueError` on the way in and in `search`, because cosine
is undefined at the origin and backends express that incompatibly (pgvector
yields NaN, which sorts unpredictably).

`score` is **cosine mapped onto 0..1 by `(1 + cosine) / 2`, higher meaning more
similar** — `1.0` identical, `0.5` orthogonal, `0.0` opposite. Several vector
databases report a distance instead; an adapter that passes one through
returns plausible nonsense rather than an error. `min_score` reads on that same
scale.

Two rules decide whether your `search` is correct rather than merely
plausible:

- **Filter before applying `k`.** Taking the `k` nearest and *then* filtering
  by `entity_types`/`min_score` returns fewer than `k` while matching records
  sit further down the ranking — correct-looking, wrong, and
  indistinguishable from a small corpus.
- **Break score ties by ascending `entity_id`** as its canonical lowercase
  hyphenated string, so `k` cutting through a tie returns the same members on
  every backend.

Do not write your own reading of the `entity_type` metadata convention. Call
`redstring.ports.vector_store.entity_type_of`, which lives with the port
because the two adapters wrote their own and diverged: pgvector nulled every
non-string (its column is `text`) while the in-memory store compared the raw
value against a `set` and raised `TypeError: unhashable type: 'list'` on a
stored `{"entity_type": ["person"]}`. A record whose `entity_type` is absent,
`None`, a number, a list or an object matches no type filter and never raises.

Storage may be float32 — pgvector's `vector` is float4 — so do not rely on a
float64 component surviving `upsert` then `get` bit-for-bit.

### Cache and LlmProvider

For `Cache`, the two rules that catch people are that `get` returns `str | None`
(a Redis client at its defaults returns `bytes`; decode) and that the window
methods take caller-supplied epoch floats — `record_hit(key, *, at, ttl_seconds)`,
`count_hits(key, *, since)`, `oldest_hit(key, *, since)`. Never call
`time.time()` inside an adapter: it puts the clock inside the thing under test,
and on a cluster with drift a Redis adapter would read a different clock from
its caller.

For `LlmProvider`, expose `model` as provider-qualified and versioned
provenance (`"ollama/qwen3.6-27b-mtp"`), supply no default `system_prompt` of
your own, and **raise rather than return an empty result** —
`EmptyCompletionError` for no usable content, `MalformedCompletionError` for
content that does not validate. Only a successfully parsed schema instance
holding nothing means "this document had no entities". Keep every
`langchain*` import inside `redstring/llm/adapters/`; the gate in step 2
parses `src/` and fails on a leak.

When the class is written, `isinstance(store, YourPort)` is a cheap smoke check
— the compliance suite opens with exactly that assertion — but it only proves
the method names exist. Everything above is step 2's job.

## Step 2: Opt into the compliance suite

Opting in is a subclass. You supply one thing — a way to build a fresh store,
or a `cache` fixture — and inherit every assertion. Do not copy tests out of
`src/redstring/testing/` into your module and do not override one to relax it: the
suite is what makes adapters interchangeable, and an adapter that needs a
weakened test has found either a bug in itself or a genuine gap in the port.

Nothing in `src/redstring/testing/` is collected on its own. No module there matches
`test_*.py` and no class matches `Test*`, so a suite runs only where an adapter
subclasses it under a `Test*` name.

### GraphStore: subclass `GraphStoreCompliance` and implement `new_store()`

```python
from redstring.testing.graph_store import GraphStoreCompliance


class TestMemoryStore(GraphStoreCompliance):
    async def new_store(self) -> GraphStore:
        return InMemoryGraphStore()
```

That is the whole opt-in for an adapter with no infrastructure — it is
`tests/unit/graph/test_memory_store.py` in full. The base class provides a
`store` fixture in terms of `new_store()`, so the example-based tests need
nothing else, and the suite's first test is
`isinstance(store, GraphStore)`.

#### `new_store()` must return an empty, mutually isolated store — it is called once per hypothesis example

Two requirements, both load-bearing. **Empty**: a store carrying rows from a
previous test makes "find returns what I wrote" true for the wrong reason.
**Isolated from every other store `new_store()` has returned**: two stores
sharing a dict is exactly the defect
`TestMemoryStoreSpecifics.test_two_stores_share_nothing` exists to catch.

The property tests call `new_store()` once *per generated example* rather than
once per test, because hypothesis creates a function-scoped fixture once for
the whole `@given` and reuses it across examples — so a store built in the
fixture would let state from example *n* decide example *n+1*. An adapter over
a real backend implements the emptiness by wiping: `TestNeo4jStore.new_store`
calls `_wipe(...)` and `TestPgVectorStore.new_store` calls
`_truncate(...)`, each before handing the store back.

The cheap-looking alternative — a fresh random tenant per store instead of a
real wipe — does not work here: the compliance suite generates its own tenant
ids and `new_store()` never learns them, so it cannot scope to one.

Budget for the wipe. `new_store()` runs `KG_COMPLIANCE_MAX_EXAMPLES` times per
property test — 50 by default, which for the graph suite is roughly 750
database resets in a run. Step 4 covers turning it down.

#### Optional `dispose(store)` for adapters holding connections

`dispose` is a no-op on the base class, and an in-memory adapter leaves it
alone — a store that is garbage needs no release. Override it when
`new_store()` acquired something:

```python
    async def dispose(self, store: GraphStore) -> None:
        assert isinstance(store, Neo4jGraphStore)
        await store.close()
```

Without it a run leaks one connection per hypothesis example. The suite calls
it through an `asynccontextmanager`, so it runs whether the test body completes
or raises; `tests/unit/graph/test_memory_store.py::TestComplianceHarness`
asserts both paths and that the default really is a no-op, because that hook
firing is a property of the suite rather than of any adapter.

`VectorStoreCompliance` has the identical hook, and `TestPgVectorStore`
overrides it the same way (`assert isinstance(store, PgVectorStore)`, then
`await store.close()`).

Note what the two shipped integration adapters dispose of and what they do
not: `dispose` closes the *store*, while the driver or pool itself is owned by
the fixture that created it and closed there once per test. That only works
because the adapter distinguishes the two — `Neo4jGraphStore.close` releases
the driver only `if self._owns_driver`, and `PgVectorStore.close` does the
same for its pool, so calling `dispose` per example against a shared
connection is safe. Make your adapter's `close` do the same and pin it with a
test: both adapters carry a
`test_close_does_not_close_a_pool_it_does_not_own` (or `_a_driver_`) that
closes the store and then issues a trivial query on the injected connection.
Without that ownership check, the first hypothesis example takes the whole
session's pool down with it and every later example fails on a closed
connection.

#### Passing fixture-supplied state (drivers, DSNs) via an autouse fixture, because `new_store()` takes no arguments

`new_store()` takes no arguments on purpose — the suite's contract is that an
adapter supplies exactly one thing. When you need a driver, pool or DSN, stash
it on the instance with an autouse fixture:

```python
class TestPgVectorStore(VectorStoreCompliance):
    @pytest.fixture(autouse=True)
    def _pool(self, pool: asyncpg.Pool[Any]) -> None:
        self._shared_pool = pool

    async def new_store(self) -> VectorStore:
        await _truncate(self._shared_pool)
        return PgVectorStore(self._shared_pool, dimension=self.DIMENSION, table=TABLE)
```

`TestNeo4jStore` does the same one level up, with a `_driver` fixture that sets
`self._shared_driver` from the module's `neo4j_driver` fixture. The autouse
fixture is deliberately plain (not async, returning `None`): its whole job is
the assignment, and pytest resolves it before every test in the class,
including the property tests, so `new_store()` always finds the attribute set.

Three things to get right in the fixture underneath.

**Make it function-scoped.** Both an asyncpg pool and the Neo4j async driver
bind to the event loop that created them, and `asyncio_default_fixture_loop_scope`
is `function` in `pyproject.toml` — a session-scoped one is a `ScopeMismatch`
at best and a wrong-loop hang at worst. One connection per *test* still covers
all of that test's hypothesis examples, which is the case that costs anything.

**Put the skip probe there, and make it prove the backend can serve.** Both
fixtures call a `_probe()` that returns a live connection or `None`, and
`pytest.skip` on `None` with a message naming the address and the
`docker compose -f docker-compose.test.yml up -d …` line that starts it. The
probe runs real work — `RETURN 1` for Neo4j, and for pgvector a `CREATE
EXTENSION` plus a vector round-tripped through a temporary table, because
`pgvector/pgvector:pg16` ships the extension files while a database that has
never run `CREATE EXTENSION` cannot store a vector. A TCP connect proves
neither.

**Do schema setup once per session, not once per test.** Both modules guard
`ensure_schema()` behind a module-level `_schema_ready` flag inside the
function-scoped fixture — the connection is per test, the DDL is not.

The connection is owned by the fixture and closed in its `finally`, which is
why `dispose` closing the *store* is safe: see the previous subsection on the
ownership check that keeps a per-example `dispose` from taking the shared
connection down with it.

The other reason the autouse indirection is needed at all is that the reset
lives on the *test* side. `_truncate` and `_wipe` are module functions taking
the pool or driver, not adapter methods — "delete every tenant's rows" is a
test affordance, and a production store should not offer one. `new_store()`
needs the connection to call them, and it takes no arguments, so the instance
attribute is the only channel.

### VectorStore: subclass `VectorStoreCompliance`, honour `self.DIMENSION`

```python
class TestMemoryVectorStore(VectorStoreCompliance):
    async def new_store(self) -> VectorStore:
        return InMemoryVectorStore(dimension=self.DIMENSION)
```

The `new_store` / `dispose` mechanics are identical to `GraphStore` — a fresh,
empty, mutually isolated store per hypothesis example, `dispose` a no-op
unless you acquired a connection. One rule is specific to this port: build the
store at **`self.DIMENSION`**, never at a literal.

`DIMENSION` is `8`, and small on purpose — the properties are about the store,
not about the embedding model, and a small dimension keeps the 200-vector
recall test cheap enough to run against a real database once per example. Read
it off the class rather than repeating the number, as
`tests/integration/vector/test_pgvector_store.py` does with a module-level
`DIMENSION = VectorStoreCompliance.DIMENSION`, so the value moves in one
place. The suite draws every vector through `self._vectors()` — which is
`strategies.vectors(self.DIMENSION)` — asserts `store.dimension ==
self.DIMENSION`, and builds its hand-written query vectors as
`[1.0, 0.0, *([0.0] * (self.DIMENSION - 2))]`, so a subclass that raised
`DIMENSION` would still be tested coherently.

Because the suite is fixed at 8, two boundaries are invisible to it and belong
in your adapter-specific tests (step 5). `TestDimensionIsComparedByValue` in
`tests/unit/vector/test_memory_store.py` carries both for the in-memory store:
a write of the correct length at **dimension 768** — cosmic-ray found that
`len(vector) is not 768` survives the whole suite, because CPython caches ints
only to 256 and at 8 the identity check is accidentally right — and
`dimension=1`, which the port permits and nothing else pins.

#### The two tiers: exact behaviour on small datasets, recall-only on the large one

The suite states the exactness contract in two tiers, and **every test belongs
to exactly one**. The tiers are marked in the file with banner comments, so
when you add a test you are choosing a tier whether you notice or not. The
split exists because an exact suite would pass in-memory and be
flaky-to-wrong against a real index — an adapter that "passes compliance"
while quietly returning the wrong neighbours is the worst outcome available.

**Tier 1 — exact behaviour, on tens of vectors.** Every sensible backend falls
back to a sequential scan at that size and *is* exact, so these assert exact
membership, exact ordering and exact scores: `k` respected (`search` returns
`ids[:4]` in order over ten vectors whose angles are spaced `π/12` apart, far
enough that consecutive scores differ by much more than `SCORE_TOLERANCE`),
`entity_types` and `min_score` applied before `k`, the ascending-`entity_id`
tie-break over four *identical* vectors where score decides nothing, a vector
matching itself at `1.0`, and every score agreeing with
`domain.vector.cosine_score`.

**Tier 2 — recall, on 200 vectors.** The honest weaker claim: an approximate
index may reorder the middle of the list or drop a mid-ranked neighbour; what
it may not do is lose the *true* nearest one. Exactly one test makes the
claim — `test_the_true_nearest_neighbour_is_within_the_top_k` — and it asserts
only that the true nearest entity id is somewhere in the returned top 10,
alongside `len(found) == 10` and scores descending. Not its rank, not the rest
of the list. The corpus is seeded deterministically (`random.Random(20260803)`)
rather than generated by hypothesis: the claim is about recall over a
realistic corpus, shrinking over 200-vector corpora costs a great deal to
learn nothing, and a fixed seed makes a failure reproducible without a
counterexample database — which matters most for the adapter that needs a
container.

**Know before you trust it: tier 2 currently passes trivially.** Every adapter
in this tree is exact — the in-memory one scans brute-force, and the pgvector
one carries no ANN index on purpose (BACKLOG B10k has the reasoning and the
cost). Nothing in tier 2 has ever run against a store that *can* miss a
neighbour, so "it passes" is evidence about the tests rather than about
recall. This is the same rule as everywhere else in the project: a check you
have never seen fail is not yet evidence.

Which gives the rule for adding a genuinely approximate adapter — Qdrant, or
pgvector once B10k is taken on. **Strengthen tier 2 first, before the adapter
exists to be judged by it.** One query over one corpus is not a recall claim;
a real one needs many queries, a stated recall@k target, and a failure message
reporting the measured rate rather than the single miss that tripped it.
Writing that afterwards means tuning the test until the adapter passes, which
is not a test.

Practically, when you add a test to this suite: if it names an exact result,
it goes in tier 1 and its dataset must be small and well-separated. If it can
only be stated as "the answer is in there somewhere", it goes in tier 2 and
needs the same determinism the existing one has. A test asserting exact
ordering over 200 random vectors belongs to neither and will eventually fail
on a backend that is within its rights.

#### There is no `is_approximate` opt-out flag, and why

There is deliberately no capability flag — no `is_approximate`, no
`supports_exact_search`, no marker class — that lets an adapter skip tier 1.
The suite's own docstring says so in as many words, and the reason is worth
having in front of you before you reach for one: **a flag that lets an adapter
opt out of correctness tests is how adapters quietly stop being
interchangeable.** It gets set once, for a good reason, by someone who knows
exactly which tests they are turning off — and from then on the suite is
silent about the thing it was written to check, for every future reader who
sees a green run. **An adapter that cannot pass tier 1 on ten vectors is not a
`VectorStore`.**

Tier 1 is already the accommodation. The two-tier split *is* the concession to
approximate backends: everything an ANN index is allowed to get wrong lives in
tier 2, and tier 1 is confined to dataset sizes where every sensible backend
falls back to a sequential scan and is exact anyway. An opt-out on top of that
would not be excusing approximation, it would be excusing a store that returns
wrong answers on ten rows.

So when your adapter fails a tier-1 test, the flag you want does not exist and
the failure is telling you one of three things:

- **The port's semantics are not implemented.** Filters applied after `k`
  rather than before, a distance passed through where a `(1 + cosine) / 2`
  score was expected, or a missing `entity_id` tie-break. All three produce
  results that look right and rank wrong; see step 1.
- **You forced an index into the query path.** This is the case
  [ADR 0012](../adr/0012-no-ann-index-in-a-multi-tenant-vector-store.md) is
  about. Adding an `hnsw` or `ivfflat` index does not give the planner a
  faster way to run the existing statement — it offers a *different* one,
  where ordering and truncation happen inside the index scan before the
  `WHERE` clause has been seen, so `tenant_id` becomes a sieve over an
  already-truncated list. `PgVectorStore` therefore ships with no ANN index,
  and `tests/integration/vector/test_pgvector_store.py::test_there_is_no_ann_index_on_the_embedding`
  asserts its absence so that adding one is a decision rather than a drive-by
  optimisation. BACKLOG **B10k** carries the cost of that decision and what it
  would take to revisit.
- **The backend genuinely cannot be exact at ten vectors.** Then the honest
  move is to argue the contract, not to weaken it silently. Change the suite —
  in a commit that says which claim is being given up and what replaces it,
  and that strengthens tier 2 first, per the previous subsection — or accept
  that this backend does not sit behind this port.

[ADR 0012: no ANN index in a multi-tenant vector
store](../adr/0012-no-ann-index-in-a-multi-tenant-vector-store.md) has the
full argument for why the shipped stores are exact, and [Use the pgvector
store](use-the-pgvector-store.md) covers what that means operationally.

#### float32 storage: exact-vector equality, tolerance on scores

pgvector's `vector` is float4, so the suite meets storage where it is rather
than pretending otherwise. `strategies.vector_components` draws with
`width=32`, bounded to ±1e3, `allow_subnormal=False`. Each of those is
load-bearing: `width=32` restricts generation to values single precision holds
exactly, so "the stored vector equals the written one" is a claim *every*
adapter can meet; the magnitude bound keeps the sum of squares far from
float32 overflow, so a norm is never `inf` and cosine is never NaN for a
reason unrelated to the property under test; and subnormals are excluded
because they square to zero, so a vector of them has no direction and the
port **rejects** it (`domain.vector.has_zero_norm`). Drawing one would make
an unrelated property fail on a legitimate `ValueError` from the guard rather
than on the thing it was written to check. The guard's own band is pinned by
examples instead — `test_a_vector_whose_norm_underflows_is_rejected_too` in
the compliance suite, and `TestHasZeroNorm` in
`tests/unit/domain/test_vector.py`. If you generate your own
vectors in adapter-specific tests, use `strategies.vectors(dimension)` rather
than reinventing those three bounds.

**The stored vector is still compared with `==`.** A float32-representable
value survives a float32 column unchanged, so there is no excuse for `get`
returning something different from what `upsert` took — and the round-trip
property asserts equality on the whole `VectorRecord`, not just its vector.
The pgvector module pins it a second time with a hand-written vector of
exactly representable values,
`[-1.5, 0.25, 0.0, 1024.0, -0.0625, 3.5, -7.0, 0.5]`, because a property test
is a sampler and negative, fractional and integral magnitudes surviving the
column together is worth stating as an example.

Scores are the opposite, and only scores. They run through float32 storage
and, in a database adapter, float32 arithmetic too, so every score assertion
uses `pytest.approx(..., abs=SCORE_TOLERANCE)` with `SCORE_TOLERANCE = 1e-5` —
roughly an order of magnitude of headroom over float32's ~1e-7 relative error,
and still far tighter than any ranking mistake.

Note where the tolerance stops. It applies to a score's *value*, never to
ordering: tier 1 asserts the exact top-`k` in exact order, and that is fair to
demand of a float32 adapter only because the datasets are built so that
consecutive scores differ by far more than `SCORE_TOLERANCE` —
`test_search_returns_the_exact_top_k_in_order` spaces ten vectors by `π/12`
for exactly that reason. So do not round or quantise scores inside your
adapter to make a comparison pass. The tolerance is already there for the
precision, and rounding is what turns a well-separated ranking into a tie the
`entity_id` tie-break then decides.

### ChunkStore: subclass `ChunkStoreCompliance` and implement `new_store()`

Mechanically identical to the two suites above — a fresh, empty, mutually
isolated store per hypothesis example, `dispose` a no-op unless `new_store()`
acquired a connection:

```python
class TestMemoryChunkStore(ChunkStoreCompliance):
    async def new_store(self) -> ChunkStore:
        return InMemoryChunkStore()
```

`tests/integration/chunks/test_postgres_store.py::TestPostgresChunkStore` is
the same class with the autouse `_pool` fixture and a `_truncate` in
`new_store`, exactly as `TestPgVectorStore` does it.

Build chunks through `ChunkStoreCompliance._chunk`, which sets the id to the
real `chunk_id(source_id, text)` rather than an arbitrary string. That is not
tidiness: the content-addressed id is what makes
`test_two_tenants_hold_the_same_chunk_id_independently` a genuine collision,
and it is the single most important case in the file — a suite whose ids come
from `uuid4()` cannot observe a composite key compared on `id` alone.

`tests/unit/chunks/test_compliance_coverage.py` is this port's introspective
gate, the same shape as the graph and vector ones: it derives the read-method
list from the Protocol and fails until each has
`test_<method>_returns_copies` and `test_<method>_never_crosses_tenants` on
the compliance class.

### EmbeddingProvider: subclass `EmbeddingProviderCompliance` and supply a `provider` fixture

```python
class TestFakeEmbeddingProvider(EmbeddingProviderCompliance):
    @pytest.fixture
    def provider(self) -> EmbeddingProvider:
        return FakeEmbeddingProvider()
```

**Put the fixture on the class, not at module scope.**
`EmbeddingProviderCompliance` declares its own `provider` placeholder that
raises `NotImplementedError`, and a fixture on the class shadows one in the
module while a module-level one does not shadow the base class's. The
placeholder is deliberate — a subclass that forgot to supply an adapter must
fail rather than silently test nothing — but it means a module-level fixture
looks like it works and yields `NotImplementedError` from the base class.
`tests/integration/llm/test_live_embeddings.py::TestLiveEmbeddings` records
that, and runs the whole suite unchanged against a real server.

Two things about the suite are worth knowing before you write the adapter.
**Every multi-text case uses inputs that differ from each other**, because a
suite embedding `["a", "a", "a"]` cannot observe a reordering — three
identical inputs give three identical vectors and every permutation passes.
And equality is by **cosine with a tolerance**, not `==`, because batch
composition perturbs the low bits on a real server; `test_order_is_preserved`
keeps that honest by asserting the *wrong* pairings are dissimilar as well as
the right ones being similar, so an adapter returning
similar-but-wrong vectors cannot pass on the tolerance alone.

### Cache: subclass `CacheCompliance` and supply a `cache` fixture

`Cache` opts in differently from the two store ports. There is no
`new_store()` and no `dispose()` — just a fixture named `cache` at module
scope, and a subclass with an empty body:

```python
@pytest.fixture
def cache() -> MemoryCache:
    return MemoryCache()


class TestMemoryCache(CacheCompliance):
    """The compliance suite, run against the in-process adapter."""
```

That is `tests/unit/llm/test_memory_cache.py`'s whole opt-in. The suite asks
for a fixture rather than a factory method because a `Cache` has no
per-example state problem to solve: every test names its own keys and its own
epoch offsets, so one fresh instance per test is enough, and pytest's ordinary
function-scoped fixture already gives that.

Two mechanical points. **The fixture must be at module scope**, not a method
on the subclass: `CacheCompliance`'s tests live in nested classes —
`TestKeyValue`, `TestCounters`, `TestHitWindows`, `TestExpiry`,
`TestLifecycle` — and those collect through the subclass while resolving
`cache` by name the way any test does. **Return a fresh instance**, not a
module-level singleton; `TestLifecycle` closes the cache, and the next test
would inherit a closed one.

The suite exists for one reason, stated in its own docstring and learned twice
in this project already: **an in-memory reference that is more forgiving than
the real backend lets a caller pass its tests on behaviour production does not
have.** So the requirement is not "behave sensibly" but "behave the same",
and the awkward cases are stated in port terms and asserted identically for
every adapter. The ones a fresh implementation is most likely to get
differently:

- **`get` returns `str`, never `bytes`.** A Redis client left at its defaults
  returns bytes, so a caller comparing against a string literal would match in
  every `MemoryCache` test and never match in production. `RedisCache.from_url`
  therefore builds its client with `decode_responses=True`, and
  `test_a_value_comes_back_as_str_not_bytes` is what holds it there.
- **A missing key is `None`, and a missing counter is zero** — so the first
  `increment` returns `1`, not `None` and not `0`, and deleting a counter
  starts it again from `1`.
- **`get` and `increment` share one key space.** A counter reads back as its
  decimal string (`"2"`), and `set("failures", "4")` then `increment` returns
  `5`. That is how a circuit breaker resets to a known count, so an adapter
  storing counters in a private encoding fails here rather than in a caller.
- **A hit window and a value can share a key without colliding.** Redis would
  reject a `ZADD` onto a string key with `WRONGTYPE`, which `MemoryCache`
  cannot reproduce — so this is asserted rather than left to an error path
  nothing tests. `delete(key)` must clear both halves.
- **The window is sliding, inclusive at the lower bound, and does not collapse
  duplicates.** Hits before `since` are not counted; a hit exactly *at*
  `since` is (Redis `ZCOUNT min max` is inclusive, a naive `>` is not); and two
  hits at the very same instant are two hits, which the obvious sorted-set
  encoding keyed on the timestamp gets wrong by making the second an update.
- **A TTL is applied when a counter is created, not refreshed on every
  increment.** `test_a_counters_ttl_is_not_refreshed_by_later_increments`
  pins it: `EXPIRE` on each hit gives a failure count that only ever grows, so
  a breaker eventually opens on failures minutes apart — which reads as
  flapping infrastructure rather than as a bug in the breaker.
- **Sub-second TTLs are real TTLs.** `set("brief", "value", ttl_seconds=0.05)`
  must actually expire. A Redis adapter reaching for `EX` truncates that to
  zero whole seconds and gets *no* expiry at all, which is why `RedisCache`
  uses `px=max(1, int(ttl_seconds * 1000))`.
- **`close` is safe to call twice**, and closing a client the adapter does not
  own is not its business — `RedisCache` takes `owns_client=False` by default,
  because a shared client closed by whichever component finished first is a
  bug that surfaces only under shutdown.

Three of those expiry tests are the only place the suite sleeps, at 0.15s and
below; everything about time *windows* is passed in, per the next subsection.

Two smaller things worth copying from the reference module. Assert the port
directly — `assert isinstance(MemoryCache(), Cache)` — as a cheap check that
the method names line up before any behaviour is exercised. And when an
adapter needs a case the port does not state, put it beside the subclass
rather than inside the suite: `MemoryCache` adds that its lazy expiry prunes
the one series which grows with *traffic* (asserted through `oldest_hit`, not
by reading a private list), and that `increment` on a key holding
something unnumeric raises `ValueError` — because Redis raises there, and
silently resetting to `1` would hide a caller that had mixed `set` and
`increment` as a failure count that quietly restarts.

The Redis-side subclass **exists**: `tests/integration/llm/test_redis_cache.py`
runs `CacheCompliance` against a real Redis from `docker-compose.test.yml`,
with a skip probe that round-trips a key rather than opening a socket and a
per-xdist-worker key prefix so two workers do not share a keyspace. That is
the same shape step 4 describes for the two integration store adapters.

**It is worth knowing what that run found on its first pass**, because it is
the argument for every sentence above. `RedisCache` had been the one adapter
excused from its port's shared suite, and it recorded a rate-limit hit with a
member keyed on `id(self)` — the cache object's address, constant for its
whole life — under a comment correctly stating that the member had to be
unique per *hit* or two hits at the same instant would collapse into one.
They did, so `count_hits` under-reported exactly when a burst is what a caller
is trying to detect. `MemoryCache` cannot exhibit it at all, because it
appends to a list. See `.claude/rules/recurring-defects.md` (g).

#### Time is a caller-supplied argument (`at=`, `since=`), never a sleep

`record_hit(key, *, at, ttl_seconds)`, `count_hits(key, *, since)` and
`oldest_hit(key, *, since)` take epoch floats from the caller. The suite
anchors on `NOW = 1_700_000_000.0` and offsets from it, so "an event 600
seconds ago" is `NOW - 600` — a number, not a ten-minute test. Import that same
constant in adapter-specific tests (`from redstring.testing.cache import NOW`)
rather than inventing another anchor; `tests/unit/llm/test_memory_cache.py`
does exactly that.

**Never call `time.time()` inside an adapter to fill one of these in.** Two
reasons, and the second is not about tests: it puts the clock inside the thing
under test, so every window test would have to sleep, and on a cluster with
drift a Redis adapter would be reading a *different* clock from its caller —
a real bug, not merely an awkward test. The port docstring makes the same
argument under "Time is passed in, never read".

`MemoryCache` goes one step further and takes a `clock` parameter it *ignores*,
documented as being there so a reader who expects to find a clock finds the
explanation instead of a hidden `time.time()`. Copy the intent if not the
parameter: the absence of a clock is the design, and it is worth saying so
where someone would otherwise add one.

The division is worth being precise about, because the suite does both.
**Window positions are arguments** — `at`, `since` — and are never slept for;
that is what lets `test_hits_before_the_window_are_not_counted` place one hit
at `NOW - 600` and one at `NOW` and finish instantly. **Expiry is the backend's
own clock**, which the caller cannot supply, so the three `TestExpiry` cases
really do `await asyncio.sleep(...)`, at 0.15s, 0.15s and 0.06 + 0.10s — short
enough to keep the suite fast, long enough for the assertion to be real. An
adapter is free to measure those deadlines however it likes: `MemoryCache`
uses `time.monotonic()` at write time, which is not an epoch clock and does
not have to be, because no caller ever names a TTL deadline.

Two consequences for your adapter. Sub-second TTLs must genuinely expire —
`ttl_seconds=0.05` is the case a Redis adapter reaching for `EX` truncates to
zero whole seconds and silently turns into *no* expiry, so use millisecond
precision (`px=max(1, int(ttl_seconds * 1000))`, as `RedisCache` does). And if
you find yourself sleeping to move a hit window, you have implemented the port
wrongly: the window methods take the time you want, so ask for it.

### LlmProvider: no compliance suite — what stands in for one

There is nothing to store and nothing to read back — no write to observe, no
tenant to scope, no copy to leak — so there is no `LlmProviderCompliance` to
subclass, and adding one would mean asserting that a fake returns what the
test scripted. Three other things carry the weight instead, and an
`LlmProvider` adapter is not done without all three: the no-leak gate below,
the two behavioural rules in the subsection after it, and a set of
adapter-specific tests driven by a scripted transport rather than a mock.

That last one is where most of the assurance actually lives, and
`tests/unit/llm/test_langchain_adapter.py` is the pattern to copy. It builds a
`ScriptedChatModel` — a real LangChain `BaseChatModel` returning a canned
`AIMessage` — and drives the adapter through it, so the tests exercise the
adapter's own parsing rather than a mock's return value. Everything the port
promises is stated there against that scripted transport: the schema goes out
as a `json_schema` response format, a system prompt becomes a separate turn
from the text, text blocks are joined rather than rejected, blank input is
refused before a model is called at all.

Two of those cases are worth knowing before you write your own adapter,
because neither is derivable from the port's prose. Content arrives as a `str`
for most models and as a **list of blocks** for others, and a reasoning model's
`reasoning_content` is not an answer — join the text blocks, ignore the
thinking. And requesting a JSON schema routes `langchain-openai` through the
openai SDK's parsing path, which *raises* `LengthFinishReasonError` on a
truncation instead of returning a message; the adapter translates that into
`EmptyCompletionError`, because letting it escape would put `openai` into this
library's public failure contract by accident. Expect one of these per
transport, and expect to find it against a live server rather than by reading
the SDK.

#### `tests/unit/test_dependencies_stay_confined.py`: every client stays in its directory

**This is the gate you are most likely to need and least likely to think of**,
because a store adapter is exactly the kind of change that brings a new driver
into the tree. Each port exists so that a breaking change in someone else's
library touches one file, and that guarantee is one `from neo4j import ...`
away from being false. Nothing else in the gate would notice: a leaked import
is not a test failure, not a lint finding, and not a `lint-imports` violation,
because that contract is over first-party packages only. So this file is the
whole enforcement.

It is a table, one row per confined library:

| Library | Permitted directory | Port |
|---|---|---|
| `langchain*`, `openai` | `llm/adapters/` | `llm_provider`, `embedding_provider` |
| `neo4j` | `graph/adapters/` | `graph_store` |
| `asyncpg` | `vector/adapters/`, `chunks/adapters/` | `vector_store`, `chunk_store` |
| `redis` | `llm/cache/` | `cache` |

**A new adapter with a new driver adds a row**, and that is the whole edit —
the three checks below are parametrised over the table. Until the row exists,
nothing stops the driver appearing in `composition/`.

`test_nothing_outside_the_permitted_directory_imports_it` walks every `.py`
under `src/redstring/` except those beneath the row's directory, parses each
with `ast`, and collects every imported module belonging to the row's
packages. It asserts the resulting mapping is `{}` — reporting
*every* offending path and the names each one imported, rather than the first
one found, because a leak is usually a family of them and one failure per run
would take as many runs to clear.

Two details of how it looks are the parts that matter if you copy it.

**It reads source text, never imports the modules.** A module that imports its
client lazily inside a function still leaks the types into its signatures,
and importing everything to inspect it would need every optional extra
installed — the failure mode `--all-extras` exists to avoid. Because the check
is `ast.walk`, it sees imports at *any* nesting depth: inside
`if TYPE_CHECKING:`, inside a function body, inside a `try`. That is not
theoretical, and it is what the second guard below actually proves —
`adapters/langchain.py` has **no** top-level LangChain import at all. Its
`BaseChatModel` and `BaseMessage` imports sit under `TYPE_CHECKING`, and
`langchain_openai` is imported inside `from_openai_compatible` so that an
`openai`-shaped dependency is not paid at module import. A check that only
looked at `tree.body` would find nothing there and report the directory clean.

**Relative imports are deliberately out of scope.** `ImportFrom` nodes are
counted only at `node.level == 0`, so `from .langchain import ...` is not a
finding — a first-party relative import cannot be the third-party dependency
this is about, and `lint-imports` owns that question anyway.

**Matching is on the top-level package plus its `name_*` family**, not a bare
`startswith`, and `test_belongs_to_does_not_match_by_bare_prefix` pins both
halves. Without the family clause `langchain_core` goes unrecognised and the
check under-reports; with a bare prefix a module called `redistribute` reads as
a `redis` import. The second failure is the nastier one — a *false* leak
report, in a check whose entire value is being believed.

The rest of what to copy is that each row comes with **two guards on itself**,
because a leak check is a search for something that should not be there and so
passes trivially when it searches nothing at all:

- `test_the_permitted_directory_exists` fails when a row names a directory that
  is not there — which would exempt nothing, exclude nothing, and let the leak
  check go on passing.
- `test_the_permitted_directory_really_does_import_it` asserts something under
  that directory *does* import the library. This is the staleness guard
  [ADR 0014](../adr/0014-exemption-lists-are-empty-and-must-stay-falsifiable.md)
  requires of every exemption: a row naming a library nobody imports any more
  passes forever and protects nothing, so removing a dependency becomes a
  visible decision rather than a silently inert rule.

Plus one guard shared by every row: `test_the_walk_finds_the_library` asserts
the walk found more than fifty files and that `ports/llm_provider.py` is among
them, so a wrong `SOURCE_ROOT` fails loudly rather than passing over an empty
list.

`directories` is a tuple because `asyncpg` genuinely backs two ports — the
pgvector `VectorStore` and the Postgres `ChunkStore` are two adapters sharing
one driver. **Every named directory is guarded in both directions**, so a
second entry that stops importing the library fails rather than quietly
widening the first.

Note what a row is scoped to. It is a whole **directory**, not a file list, so
`llm/adapters/fake.py` sits inside its row and imports no LangChain — which is
the point of a fake — and a second transport adapter added tomorrow needs no
edit at all.

A passing check you have never seen fail is not yet evidence, so all three
kinds were watched failing on purpose before this was believed: a planted
`import neo4j` in `composition/`, a row pointed at a directory that does not
exist, and a row pointed at a real directory that does not import its library.
See [quality gates](../reference/quality-gates.md).

#### `model` is provenance, and empty output is an error

**`model` is provider-qualified and versioned** — `"ollama/qwen3.6-27b-mtp"`,
not `"qwen"`. It is not configuration: the value lands on `Entity.provenance.model` and
into a durable event log where "re-extract everything the old model touched"
has to stay answerable, and the provider is the only thing that knows its own
identity, so it exposes it rather than making each caller pass a string it
might get wrong. `LangChainLlmProvider.__init__` takes it explicitly for that
reason — no chat model knows which server it was pointed at — and
`from_openai_compatible` composes it as `f"{provider}/{model}"`.

**Empty output raises; it is never an empty result.** An extraction that
returned nothing and an extraction that failed are indistinguishable
downstream, and the first is a legitimate answer while the second erodes a
knowledge graph silently. So:

| Situation | What the adapter must do |
|---|---|
| Response holds no usable content (empty, whitespace-only, budget spent on `reasoning_content`, truncated) | raise `EmptyCompletionError`, carrying `finish_reason` when the transport reported one |
| Content came back but does not validate against the schema | raise `MalformedCompletionError`, naming the model and the schema |
| The safety layer declined | raise `RefusedCompletionError` |
| The schema validated and holds nothing | **return it** — this is "the document had no entities" |

All four are asserted for the LangChain adapter, and the first, second and
fourth again for the fake. The last row and the first are two halves of one
claim: the empty answer is only meaningful because every failure raises.

`RefusedCompletionError` is a **sibling** of `EmptyCompletionError`, not a
subclass, and one test asserts exactly that non-subclass relationship so a
shared base cannot make the distinction pass vacuously. The two call for
opposite reactions — a truncation is a token-budget problem worth retrying, a
refusal is a permanent property of that content and retrying spends tokens to
be refused again. All three are `LlmProviderError`, so a caller keeps one
`except` and the pipeline's `skip_failed_chunks` works without knowing a new
type exists; add your adapter's new failure mode to that family rather than
beside it.

One consequence for testing anything *downstream* of a provider: use
`FakeLlmProvider` from `redstring.llm.adapters.fake`, not an `AsyncMock`. It
takes **payload dicts** and validates them against the caller's schema through
the same gate the real adapter uses, so a test cannot smuggle a pre-built
schema instance past validation — which is what would let a malformed-output
test pass while extraction handles nothing. Its `EMPTY` sentinel and a payload
validating to an empty result stay distinguishable, for the reason the table
above exists. Program it with `by_substring={...}` rather than `script=[...]`
for anything about chunking or merging: with a positional script, permuting
the chunks permutes which answer each chunk receives, so an order-independence
test would pass against a merge that is not order-independent at all.

[ADR 0008: the two non-store ports](../adr/0008-the-two-non-store-ports.md)
has the rest of the reasoning for why this port is one property and one
method.

## Steps 3 to 5: where the rest of the path is defined

The remaining three steps are required, and none of them is written out here.
Each is already defined somewhere that a change to the rule would be made, and
a second copy on this page would be the copy that goes stale — one fact, one
declaration site.

**Step 3 — the isolation and tenant tests.** Every read method on a store port
needs a `test_<method>_returns_copies` and a
`test_<method>_never_crosses_tenants` on the compliance class. Follow that
naming and there is nothing to configure: `tests/unit/graph/`,
`tests/unit/vector/` and `tests/unit/chunks/test_compliance_coverage.py`
derive the read-method list from the Protocol by introspection and fail until
both exist. The rule, and the four read methods that shipped without it, are
in `.claude/rules/definition-of-done.md` under "New port adapter"; the reason a
behavioural test cannot substitute is in the "Copies, not live internal state"
bullet above.

**Step 4 — running the suite.** Sync with `uv sync --all-extras`, run one
adapter per pytest invocation, and reach for `KG_COMPLIANCE_MAX_EXAMPLES`
rather than `-n auto` when a real backend makes the run slow. All three are
constraints of the *runner* rather than of your adapter, and each has a
measured failure behind it (36 parallel failures on the shared Neo4j database;
34 `FailedHealthCheck: called from multiple different executors` when a unit
and an integration subclass of one suite share a process). The runbook is
[Run the integration and mutation
suites](run-integration-and-mutation-suites.md).

**Step 5 — the tests the port cannot specify, and where the module sits.**
Schema creation, encoding fidelity, connection ownership and query plans go in
a `Test*Specifics` class beside your compliance subclass; anything true of the
*port* goes up into `src/redstring/testing/` instead. Placement is decided by the
`lint-imports` contract in `pyproject.toml`, which runs on commit — and if your
adapter brought a new driver with it, the dependency-confinement table in
`tests/unit/test_dependencies_stay_confined.py` needs a row — the one gate you
are most likely to miss, covered in step 2 above.

## Related reading

- [ADR 0002: two store ports](../adr/0002-two-store-ports.md) — why `GraphStore`
  and `VectorStore` are separate.
- [ADR 0008: the two non-store ports](../adr/0008-the-two-non-store-ports.md) —
  why `Cache` and `LlmProvider` are as small as they are.
- [Quality gates](../reference/quality-gates.md) — what runs on commit, and what
  you must run yourself.
- [Run the integration and mutation suites](run-integration-and-mutation-suites.md).
