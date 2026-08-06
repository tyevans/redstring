# Run the integration and mutation suites

Two families of tests in this repo are deliberately outside the commit gate,
and neither runs unless you ask for it:

- the **integration suite** (`-m integration`), which exercises the `GraphStore`
  and `VectorStore` adapters against the real Neo4j and Postgres containers in
  `docker-compose.test.yml`, talks to a live OpenAI-compatible endpoint under
  `tests/integration/llm/`, and builds and installs a wheel to prove the bundled
  domain schemas are packaged;
- the **mutation suites** (`mutmut` and `cosmic-ray`), which measure whether the
  unit tests can tell a correct implementation from a broken one.

Both are excluded by `addopts = ["-m", "not accuracy and not integration"]` in
`pyproject.toml`, which is what keeps the commit gate infra-free and fast — see
[quality gates](../reference/quality-gates.md) for what does run on commit. A
CLI `-m` overrides that setting, which is why every command below passes one.

This guide gives the exact invocations, the environment variables each suite
reads, and the four failure modes this project has actually hit — all of
which look like flakiness and none of which are. Those are not gathered into a
troubleshooting section, because each belongs with the invocation that provokes
it: [Step 3](#step-3-run-it-serially---m-integration-never--n-auto) and
[Step 4](#step-4-do-not-combine-it-with-the-unit-suite-in-one-invocation) of
Part 1, [reading the result](#reading-the-result-skipped-is-honest-failed-means-the-backend-is-reachable-but-wrong),
and [Step 4](#step-4-read-the-report--a-zero-survivor-run-means-the-harness-is-broken)
of Part 2.

Run the integration suite when you have touched an adapter
([Neo4j](../reference/neo4j-graph-store.md),
[pgvector](use-the-pgvector-store.md), `RedisCache`), when you have
[implemented a store adapter](implement-a-store-adapter.md) of your own, or
before a release. Run a mutation suite when you want evidence that a module's
tests are worth something; the standards for reading the result are in
[`.claude/rules/testing.md`](https://github.com/tyevans/redstring/blob/main/.claude/rules/testing.md) and `CLAUDE.md`,
and the short version is that **a count is never the answer** — every survivor
has to be understood, and a run reporting zero survivors is almost always a
broken harness rather than a perfect suite.

## Before you start: sync with `--all-extras`

```
uv sync --all-extras
```

Do this first, every time, in whatever checkout you are about to run in —
including a fresh `git worktree` made for a cosmic-ray run.

`--all-extras`, not `--extra dev`. `dev` holds only the tooling; every backend
is its own extra (`[project.optional-dependencies]` in `pyproject.toml`) —
`neo4j`, `pgvector`, `redis`, `llm` — and the suites below need them:

- `tests/integration/graph/test_neo4j_store.py` imports `neo4j`. Its `_probe`
  helper treats a missing driver the same way it treats an unreachable server —
  it returns `None` and the module skips — so a venv without the `neo4j` extra
  reports a **green run of zero Neo4j tests**, not an error.
- both mutation runners execute the *unit* suite
  (`uv run pytest -x -q --no-header -p no:randomly tests/unit`, configured as
  cosmic-ray's `test-command` and mutmut's `runner`), and modules under
  `tests/unit/` import the adapters directly. A missing extra fails
  *collection* rather than skipping, so every mutant dies on the import error
  and the report reads as a perfect score.

Note the two failure shapes: a missing extra makes the integration suite
quietly *smaller* and makes a mutation run quietly *perfect*. Neither surfaces
as a packaging problem.

The trap that motivates the instruction is `uv add` and `uv remove`, which
re-sync as a side effect and can silently narrow the installed extras back to
`dev`. **Re-sync with `--all-extras` after any dependency change**, and never
edit the dependency tables in `pyproject.toml` by hand. Slice 7 lost a
cosmic-ray run to exactly this: a worktree synced with `--extra dev` reported
0 survivors out of 426 mutants, every one "killed" by an import error, and the
result was indistinguishable from an outstanding test suite. Slice 9 hit the
same cause presenting as 47 mypy errors in files nobody had touched.

A worktree is where this goes unnoticed most easily, because cosmic-ray's
`local` distributor mutates the working tree in place and so is normally run
from a second checkout — one that never went through the project's first-time
setup:

```
uv sync --all-extras && uv run pre-commit install
```

That is also the whole install for a fresh clone; see the
[README](https://github.com/tyevans/redstring/blob/main/README.md) and
[quality gates](../reference/quality-gates.md).

Two things `--all-extras` does *not* give you, so that a skip later is not a
surprise:

- **Containers.** An extra installs a *driver*, never a server. With
  `--all-extras` the Neo4j, pgvector and Redis suites all import cleanly and
  then skip, because nothing is listening. Part 1 brings the containers up.
- **A model server.** The `llm` extra installs the LangChain transport, not an
  endpoint. `test_live_endpoint.py` and `test_live_pipeline.py` probe
  `KG_LLM_BASE_URL` with a real completion and skip when no model answers.
  Those two are the only tests in `tests/integration/llm/` that need a model —
  `test_redis_cache.py` sits in the same directory because `RedisCache` lives
  under `llm/`, and needs the container rather than the endpoint.

Verify the sync took before running anything expensive:

```
uv run python -c "import neo4j, asyncpg, redis, langchain_openai; print('extras ok')"
```

If that line raises, every result you read afterwards is about your virtualenv
rather than about the code.

## Part 1 — the integration suite

### Why it is not in the commit gate

`addopts = ["-m", "not accuracy and not integration"]` in `pyproject.toml`
deselects the marker, so the commit hook needs no Docker, no model server and
no network — see [quality gates](../reference/quality-gates.md) for what it
does run. That is a deliberate trade, and it has a cost this project has
already paid once.

An interrupted cosmic-ray run left a mutant in
`src/redstring/graph/adapters/neo4j.py`:

```
-    if limit is not None and limit < 0:
+    if not limit is not None and limit < 0:
```

**The full suite passed with it applied.** The adapter's own tests are all
`integration`-marked, so not one line of that module executed in the default
run, and corrupt source in an integration-only module was invisible
(**B10a**).

Two things were done about that, and one deliberately was not:

- `tests/unit/graph/test_neo4j_adapter_is_wired.py` now exercises every part
  of the adapter that needs no server — argument validation (against a driver
  that raises if touched, which also proves no I/O happens before the guard),
  the encode/decode functions, signature conformance against the port, and a
  check that Cypher has not leaked out of the adapter. That mutant dies in the
  default gate now.
- `tests/conftest.py` ends every run with a line naming what the deselection
  removed and the command that runs it — `197 'integration' tests -- uv run
  pytest -m integration` rather than a bare deselection count.
- **Not done:** the queries, the schema DDL, tenant isolation, traversal and
  the query-plan assertions still run only with Docker up. The module is not
  in `[tool.coverage.run] omit`, so the ratchet measures the hole honestly
  instead of hiding it: the adapter reads **61%** in the default run, and its
  65 uncovered statements are precisely the query bodies.

Closing that last gap means a separate CI target that brings the compose file
up and `coverage combine`s an integration run with the default one — not a
commit hook made conditional on Docker, which trades a deterministic gate for
a flaky one. Two traps wait for whoever writes it, and both look like
flakiness: it must be **two invocations combined** rather than one widened
marker expression ([B10m](#step-4-do-not-combine-it-with-the-unit-suite-in-one-invocation)),
and it must not put `-n auto` over the Neo4j suite
([B10f](#step-3-run-it-serially---m-integration-never--n-auto)).

Until then, running this suite by hand after touching an adapter is not
optional politeness — it is the only thing that executes the Cypher.

### Step 1: bring up `docker-compose.test.yml` and wait for the healthchecks

```
docker compose -f docker-compose.test.yml up -d neo4j postgres redis
```

**Name the services.** `up -d` with no arguments works today, because the file
declares exactly these three — `neo4j` (`neo4j:5-community`), `postgres`
(`pgvector/pgvector:pg16`) and `redis` (`redis:7`) — but the three suites skip
independently, and naming what you started is what makes a later skip legible:
"I never started Postgres" and "Postgres is up and the adapter is broken"
produce very different next steps, and only one of them is about the code.

The ports are deliberately off the defaults — Neo4j on **7688** (plus 7475 for
the browser), Postgres on **5434**, Redis on **6381** — so nothing collides
with a local install or with another project's containers. That is also why the
defaults baked into the tests are `bolt://localhost:7688`, port 5434 and
`redis://localhost:6381/0` rather than 7687/5432/6379; see
[Step 2](#step-2-point-the-suite-at-the-containers).

Redis earns the non-default port twice over, because **its suite calls
`flushdb` between tests**. On 6379 that is a compliance run reaching whatever
Redis happens to be on the machine and erasing it. The other two only wipe a
database they created; this one wipes whatever it is pointed at.

There is no `apoc`, and that is a design commitment rather than an omission:
the [Neo4j store](../reference/neo4j-graph-store.md) is plain Cypher — entity
type is a property rather than a label, and traversal is a variable-length path
— so the suite runs against any managed Neo4j, not only ones that let you
install plugins.

Then **wait for the healthchecks**, because `up -d` returns when the container
is *running*, not when the server can *serve*:

```
docker compose -f docker-compose.test.yml ps
```

Require `(healthy)` in the STATUS column, not `Up`. Every service declares a
check that probes the server rather than the process, on a 5 s interval with 30
retries — so allow up to about two and a half minutes on a cold image pull, and
treat anything longer as a real failure rather than slowness:

- Neo4j runs `cypher-shell -u neo4j -p redstring 'RETURN 1'`. Authenticating is
  not enough; a server still recovering its store files accepts a connection.
- Postgres runs `pg_isready -U postgres -d redstring_test`, with the database
  named on purpose. Bare `pg_isready` succeeds against the bootstrap server
  before `POSTGRES_DB` has been created, which is precisely the window you are
  trying to wait out.
- Redis runs `redis-cli ping | grep -q PONG`, matching the reply rather than
  trusting the exit status — `redis-cli` exits 0 for a server that answers with
  `LOADING` while it reads an RDB file back in.

The Redis *suite* goes further than its healthcheck, and for a reason worth
copying: its `_probe` writes a key and reads it back rather than pinging,
because the suite is about what values come back. A server that accepts
connections but rejects writes — a replica, an instance at `maxmemory` — would
otherwise fail every test instead of skipping.

To block rather than poll by eye:

```
docker compose -f docker-compose.test.yml up -d --wait neo4j postgres redis
```

Strictly, you do not have to wait — the suites probe and skip rather than
fail. That is the reason to wait anyway: a run started too early **skips
silently and reads as a pass**. The credentials the containers come up with
(`neo4j/redstring`, `postgres/redstring`, database `redstring_test`) are the
defaults the tests already assume, so if the healthchecks are green there is
nothing further to configure.

### Step 2: point the suite at the containers

**Against the compose containers you set nothing.** Every connection detail is
an environment variable whose default is the compose file's own configuration,
so Step 1 is the whole of the setup:

| Variable | Default | Read by |
|---|---|---|
| `KG_TEST_NEO4J_URI` | `bolt://localhost:7688` | `tests/integration/graph/test_neo4j_store.py` |
| `KG_TEST_NEO4J_USER` | `neo4j` | same |
| `KG_TEST_NEO4J_PASSWORD` | `redstring` | same |
| `KG_TEST_POSTGRES_DSN` | `postgresql://postgres:redstring@localhost:5434/redstring_test` | `tests/integration/vector/test_pgvector_store.py` |
| `KG_TEST_REDIS_URL` | `redis://localhost:6381/0` | `tests/integration/llm/test_redis_cache.py` |

The two Neo4j credential variables are separate rather than folded into the
URI because the driver takes an `auth` tuple; the Postgres side is one DSN
because `asyncpg.create_pool` takes one, and Redis is one URL because
`redis.asyncio.from_url` takes one.

**Point `KG_TEST_REDIS_URL` only at a Redis you are willing to lose.** The
fixture calls `flushdb` before each test, so the database in that URL is
emptied — including database `0` of a shared instance, which is what a bare
`redis://host` means. There is no variable for the pgvector
table name or the vector dimension — the table is derived
(`kg_vectors_test_<worker>`, see below) and the dimension comes from
`VectorStoreCompliance.DIMENSION`, which both adapters' compliance suites
share.

Set the variables when you are pointing at a backend you brought up yourself —
a managed Neo4j, a shared Postgres, a second container on another port:

```
KG_TEST_NEO4J_URI=bolt://neo4j.internal:7687 \
KG_TEST_NEO4J_USER=neo4j \
KG_TEST_NEO4J_PASSWORD=... \
uv run pytest -m integration tests/integration/graph
```

```
KG_TEST_POSTGRES_DSN=postgresql://kg:...@pg.internal:5432/redstring_test \
uv run pytest -m integration tests/integration/vector
```

```
KG_TEST_REDIS_URL=redis://redis.internal:6379/9 \
uv run pytest -m integration tests/integration/llm/test_redis_cache.py
```

**Each is read once, at module import**, into a module-level constant
(`NEO4J_URI`, `NEO4J_AUTH`, `DSN`). They must therefore be in the environment
of the `pytest` process — prefixing the command as above, or exporting before
you start it. Changing one mid-session, or from inside a fixture or
`conftest.py` that runs after collection, has no effect.

Three things to know before you point either variable somewhere real.

**The Neo4j suite wipes the whole database before every test**, with
`MATCH (n) DETACH DELETE n` — not scoped to a tenant, not scoped to a label.
Never point `KG_TEST_NEO4J_URI` at a database whose contents you want. The wipe
lives in the test rather than on the adapter deliberately: "delete everything
regardless of tenant" is a test affordance, and the
[port's](implement-a-store-adapter.md) bulk removal is `delete_by_tenant`. It
also runs on the shared driver between hypothesis examples, so a property test
wipes many times, not once.

**The pgvector suite is narrower**: it owns a table named
`kg_vectors_test_<worker>` — `kg_vectors_test_main` outside xdist,
`kg_vectors_test_gw0` and friends under it, from `PYTEST_XDIST_WORKER` — and
truncates only that. It does, however, run `CREATE EXTENSION IF NOT EXISTS
vector` during the probe, so the role in `KG_TEST_POSTGRES_DSN` needs enough
privilege to create an extension the first time (superuser on the compose
container; on a managed Postgres, have someone create it once and the
`IF NOT EXISTS` makes the probe a no-op). See
[use the pgvector store](use-the-pgvector-store.md) for what the schema
contains.

**The Redis suite is the bluntest of the three**: it calls `flushdb`, so it
owns the whole database rather than a table or a tenant. There is deliberately
no key-prefix scheme — the suite asserts on counters and TTLs under keys it
chooses itself, and threading a prefix through would mean editing the shared
compliance body to accommodate one adapter, which is the defect rather than the
fix. See [implement a store adapter](implement-a-store-adapter.md) for why a
compliance suite is run unchanged or not at all.

**A wrong value skips rather than fails.** All three suites probe first and
`pytest.skip` when the probe cannot be answered — Neo4j must return `1` from
`RETURN 1`, Postgres must round-trip a real `vector` through a temp table,
Redis must round-trip a value — and the probe swallows every exception, so a
typo'd host, a bad password and a stopped container are indistinguishable from
each other and all read as green.
The skip message names the URI or DSN actually used; read it, and confirm it is
the backend you meant. This is the `0 passed, N skipped` shape discussed under
[reading the result](#reading-the-result-skipped-is-honest-failed-means-the-backend-is-reachable-but-wrong).

### Step 3: run it serially — `-m integration`, never `-n auto`

```
uv run pytest -m integration
```

That is the whole command, and the two things about it that matter are the
flag you pass and the flag you must not.

**`-m integration` is what selects anything at all.** `addopts` in
`pyproject.toml` is `["-m", "not accuracy and not integration"]`; a `-m` on the
command line overrides it. Without one you get the default run — the unit
suite — and the only sign that the integration tests existed is the summary
line `tests/conftest.py` prints, naming the marker, the count and the command
that runs it.

Narrow by path when you are working on one adapter, keeping the marker:

```
uv run pytest -m integration tests/integration/graph
uv run pytest -m integration tests/integration/vector
```

**Do not add `-n auto`.** `tests/integration/graph/test_neo4j_store.py` runs
`MATCH (n) DETACH DELETE n` against the one shared database before every test
(`_wipe`, from an `autouse` fixture), so under `pytest-xdist` each worker
deletes the other workers' data mid-test. This is measured, not predicted:
**36 failures that say nothing about the code** (**B10f**). They look exactly
like flakiness — different tests fail on different runs, and re-running
serially makes them go away — which is why it is worth knowing the cause
before you see it rather than after.

The obvious escape is not available. The wipe is unscoped because
`test_delete_by_tenant_removes_exactly_that_tenant` needs a genuinely empty
database to mean anything, and a database per worker needs Neo4j Enterprise —
Community allows exactly one. The pgvector suite *is* parallel-safe, by the
one-level-cheaper version of that trick: its table is
`kg_vectors_test_{PYTEST_XDIST_WORKER}` (`kg_vectors_test_main` outside xdist),
so each worker truncates only its own rows. Postgres lets you have as many
tables as you like; that is the entire difference. So `-n auto` over
`tests/integration/vector` alone is safe, and over the suite as a whole is not.

None of this touches the commit gate, whose own `-n auto` is fine: `addopts`
deselects `integration` before xdist ever sees a test. The trap is for whoever
builds the combined-coverage CI target B10a asks for — see
[why it is not in the commit gate](#why-it-is-not-in-the-commit-gate), and note
that the same target has a second trap in
[Step 4](#step-4-do-not-combine-it-with-the-unit-suite-in-one-invocation).

Expect the run to take minutes rather than seconds, since it is serial and the
compliance suites are property-based; if that is too slow while iterating, turn
the example count down rather than reaching for xdist — see
[`KG_COMPLIANCE_MAX_EXAMPLES`](#tune-the-run-with-kg_compliance_max_examples).

### Step 4: do not combine it with the unit suite in one invocation

Widening the marker expression so that one `pytest` run covers both the
in-memory adapter and the real one is the natural next thought after Step 3,
and it does not work:

```
# WRONG — 21 failures, none of them about the code
uv run pytest -m "not accuracy" tests/unit/graph tests/integration/graph

# WRONG — 13 more, same cause
uv run pytest -m "not accuracy" tests/unit/vector tests/integration/vector
```

Those counts are measured, not predicted (**B10m**). Every failure is

```
hypothesis.errors.FailedHealthCheck: The method
GraphStoreCompliance.test_… was called from multiple different executors
```

The cause is in `tests/compliance/`. `GraphStoreCompliance` and
`VectorStoreCompliance` are shared base classes — that is the whole point of
them, and what stops two adapters diverging — so their `@given` methods are
defined **once**, on the base. Hypothesis attaches its per-test state to the
*function object*, and a subclass does not get its own. Put `TestMemoryStore`
(`tests/unit/graph/test_memory_store.py`) and `TestNeo4jStore`
(`tests/integration/graph/test_neo4j_store.py`) in one process and the same
function has two executors, which is exactly what that health check is for.
`TestMemoryVectorStore` and `TestPgVectorStore` are the same pair one layer
over.

This has never been seen in normal use, and the reason is worth knowing: each
suite alone is fine. `addopts` deselects `integration`, so the commit gate runs
only the in-memory subclass, and an explicit `-m integration` runs only the
real one. **The single invocation is the only way to produce it** — which is
why it will find whoever writes the combined-coverage CI target rather than
whoever runs the suites by hand.

Run two invocations instead:

```
uv run pytest tests/unit
uv run pytest -m integration
```

If what you actually want is coverage over both — which is what B10a asks for,
see [why it is not in the commit gate](#why-it-is-not-in-the-commit-gate) —
**combine the coverage data, not the invocations**. `parallel = true` is
already set under `[tool.coverage.run]` in `pyproject.toml`, so each run leaves
its own data file and `coverage combine` merges them. That target has a second
trap in [Step 3](#step-3-run-it-serially---m-integration-never--n-auto): no
`-n auto` over the Neo4j suite.

Two other fixes exist and neither is in place, deliberately. Adding
`suppress_health_check=[HealthCheck.differing_executors]` to the shared
`compliance_settings` is a one-line change that disables a check written to
catch a real class of bug everywhere else in the suite — and this project's
standing rule is that suppressing a hypothesis health check needs proof the
state cannot leak, not an argument that it probably will not (see
[`.claude/rules/testing.md`](https://github.com/tyevans/redstring/blob/main/.claude/rules/testing.md)). Generating the
property tests per subclass in `__init_subclass__` is correct and is a
considerable amount of machinery for a problem only the CI target has.

The constraint is not specific to the store suites: it applies to any port
whose compliance suite grows a second implementation, so
[implementing a store adapter](implement-a-store-adapter.md) of your own means
your adapter's tests join the `integration` invocation, not the unit one.

### Tune the run with `KG_COMPLIANCE_MAX_EXAMPLES`

The compliance suites are property-based, and the number of examples each
property draws is the run's main cost knob:

```
KG_COMPLIANCE_MAX_EXAMPLES=10 uv run pytest -m integration
```

Both `tests/compliance/graph_store.py` and `tests/compliance/vector_store.py`
read it into `DEFAULT_MAX_EXAMPLES` and default to **50**. Measured cost on the
graph suite: **25 s / 43 s / 66 s at 10 / 25 / 50**. The reason it is not
linear is where the time goes — a real backend calls `new_store()` once per
example, so at 50 a Neo4j run is roughly **750 database resets**, and those
dominate.

Use 10 while iterating on an adapter and the default before a release or
whenever you are about to believe a green run. Setting it *higher* is
worthwhile when you are hunting a suspected ordering or filtering bug, since
the properties are samplers and more draws is the only thing that widens the
search.

Three things about the variable itself.

**It is read at module import**, so it has to be in the environment of the
`pytest` process — prefixed as above, or exported before you start it. Same
constraint as the connection variables in
[Step 2](#step-2-point-the-suite-at-the-containers).

**It is a per-*run* knob, not per-adapter.** The value is baked into the shared
`compliance_settings` when the module is imported, long before a subclass body
executes, so you cannot turn it down for the slow backend alone and leave the
in-memory adapter at 50 (**B10h**). That is a known limitation with a
deliberate cause: an explicit `max_examples` inside a `settings()` decorator
outranks every hypothesis profile, so hard-coding one in the shared suite would
make `--hypothesis-profile` inert for *every* adapter. Reading it from the
environment keeps the suite's promise that an adapter opts in solely by
implementing `new_store()` — see
[implement a store adapter](implement-a-store-adapter.md). Fixing B10h means
per-adapter hypothesis *profiles* or a class-level hook the shared decorator
reads through a callable, not a `settings(max_examples=...)` on the subclass.

**It is not a substitute for parallelism, and parallelism is not available.**
If the run is too slow, lower this rather than reaching for `-n auto`, which
produces [36 Neo4j failures](#step-3-run-it-serially---m-integration-never--n-auto).

`tests/unit/vector/test_compliance_coverage.py::TestTheSuiteIsTunable` asserts
that `compliance_settings.max_examples` still tracks `DEFAULT_MAX_EXAMPLES`, so
a hard-coded value reintroduced into the suite fails the commit gate rather
than silently ignoring your environment.

Finally, the knob has a hazard attached, and it is the reason mutation runs set
it low (`KG_COMPLIANCE_MAX_EXAMPLES=5` in the recorded cosmic-ray
`test-command`, where each mutant otherwise costs a full ~16 s integration
run). **Lowering it changes which boundary values get drawn at all.** Two
mutants in `InMemoryVectorStore.search` — `k < 0` widened to `k <= 0` and to
`k < 1` — died on one run and survived the next with nothing in the adapter
changed, because `k=0` was covered only by a property drawing `k` from `0..12`.
So a low value makes a mutation result *non-deterministic*, and the natural
misreading of a survivor that used to die is "something changed in the source."
See
[survivors worth investigating](#survivors-worth-investigating),
and pin boundaries as `@example`s rather than trusting the sampler
([`.claude/rules/testing.md`](https://github.com/tyevans/redstring/blob/main/.claude/rules/testing.md)).

### What each subdirectory needs; the `llm` subset needs `KG_LLM_BASE_URL`, not Docker

Four subdirectories, three different prerequisites, and one of them is not a
container. Work out which you have before reading a skip count:

| Path | Needs | Skips when |
|---|---|---|
| `tests/integration/graph/` | the `neo4j` container **and** the `neo4j` extra | `RETURN 1` is not answered with `1` |
| `tests/integration/vector/` | the `postgres` container (`asyncpg` is a hard dependency, so no extra) | the DSN does not answer, or a real `vector` will not round-trip |
| `tests/integration/llm/` | a live OpenAI-compatible endpoint and the `llm` extra — **no Docker** | no model returns a real completion |
| `tests/integration/test_wheel_contents.py` | `uv`, a few seconds, and the network only if a build backend is uncached | **never** — it builds and installs a wheel |

**Every probe here proves the backend can *serve*, not that a port answered**,
and all three are that strict for one recorded reason: the accuracy suite once
probed Ollama's model *listing*, the model was listed but would not load, and
eight tests failed where they should have skipped (**B12**). So Neo4j must
answer `RETURN 1 AS one` with `1`; Postgres must `CREATE EXTENSION IF NOT
EXISTS vector`, then insert `'[1,2,3]'` into a temp `vector(3)` column and read
it back, because `pgvector/pgvector:pg16` ships the extension files while a
database that has never run `CREATE EXTENSION` still cannot store a vector; and
the LLM endpoint must return non-empty content from a real chat completion.

Steps [1](#step-1-bring-up-docker-composetestyml-and-wait-for-the-healthchecks)
and [2](#step-2-point-the-suite-at-the-containers) cover the two container
subsets, and `--all-extras` covers `neo4j`. The remaining two subsets are the
ones that surprise people.

**The `llm` subset needs no container at all — it needs a model.** Nothing in
`docker-compose.test.yml` serves one, so bringing the compose file up and
running `-m integration` leaves `tests/integration/llm/` skipping, and the run
still reads as green. Two variables point it at a server, both read at module
import in `tests/integration/llm/test_live_endpoint.py`:

```
KG_LLM_BASE_URL=http://192.168.1.14:8080/v1 \
KG_LLM_MODEL=qwen3.6-27b-mtp \
uv run pytest -m integration tests/integration/llm/
```

Those two values are the defaults, so on the machine that hosts the model you
set nothing. `KG_LLM_BASE_URL` is the OpenAI-compatible *root* — the probe
POSTs to `{BASE_URL}/chat/completions` — so include the `/v1` and omit the
trailing slash. There is no API-key variable: the adapter is constructed with
`api_key="local"`, which is what a local server ignores and what the OpenAI
client refuses to start without.

`test_live_pipeline.py` imports `BASE_URL`, `MODEL` and `serving()` from
`test_live_endpoint.py` rather than re-reading the environment, so both files
skip and unskip together — you cannot end up with the adapter tests live and
the pipeline tests dark.

Two details of the probe are load-bearing, and both exist because of a real
failure:

- **A model listing would not do.** This deployment is `llama-swap`, which
  lists every model it is configured for whether or not the weights load — the
  B12 shape again, one step worse.
- **The budget is 2000 tokens** (`PROBE_MAX_TOKENS`), for a question whose
  answer is the word "OK". A reasoning model spends roughly 150 completion
  tokens getting there, nearly all of it chain of thought, and a stingy probe
  would report "no LLM here" for a server running perfectly.

That budget is also the subject of the subset's sharpest test:
`test_a_starved_token_budget_raises_rather_than_extracting_nothing` builds a
second provider with `max_tokens=16`, where the verified `qwen3.6-27b-mtp`
failure is that the server answers HTTP 200 with `content` empty and the whole
budget spent inside `reasoning_content`. The adapter must raise
`EmptyCompletionError` — see
[harden model calls](harden-model-calls.md). Everything else in the subset is
deliberately weak on *what* the model says and strict on the plumbing: names
present in the sentence, `provider.model == "openai-compatible/<MODEL>"`,
edges whose endpoints resolve. Which `entity_type` a model picks is taste, it
changes between versions, and it is the accuracy suite's problem.

**The wheel test needs no backend and never skips**, which makes it the one
result in this suite with no environmental reading at all:

```
uv run pytest -m integration tests/integration/test_wheel_contents.py
```

It builds a wheel with `uv build --wheel`, installs it into a throwaway
environment, and asks that installed copy for all six bundled domains —
`academic_research`, `business_corporate`, `encyclopedia_wiki`,
`literature_fiction`, `news_journalism`, `technical_documentation`. All six on
purpose: a packaging rule that caught five and missed the sixth is exactly the
partial failure a single-domain check would report as success. The probe script
asserts `"site-packages" in redstring.__file__`, so a wheel shadowed by the
source tree fails rather than passing for the wrong reason, and it imports only
`redstring`'s public surface — which also catches a dependency the wheel's
metadata does not pull in.

It is marked `integration` for cost, not for infrastructure, and it is the one
to run before a release: `domain_system_prompt()` reads
`extraction/domains/schemas/*.yaml` off disk, those files are simply *there* in
a source checkout, and so every other test in the repository passes whether or
not they are in the distribution. The failure it guards is silent and total —
a `KeyError` on every domain id for every installed user, with the whole suite
green.

### Reading the result: skipped is honest, failed means the backend is reachable but wrong

The two outcomes carry very different information, and the dangerous one is
the one that looks green.

**A skip is honest: it claims nothing about the adapter.** Every backend-facing
module probes first and calls `pytest.skip` when the probe cannot be answered —
`RETURN 1` returning `1` for Neo4j, a real `vector` round-tripped through a
temp table for Postgres, a non-empty completion for `tests/integration/llm/`.
The probes swallow every exception, so a stopped container, a typo'd host, a
wrong password and a missing extra are indistinguishable from one another and
all produce the same result:

```
========== 197 skipped in 1.42s ==========
```

**`0 passed, N skipped` is the shape to be suspicious of.** It is what a
successful run of nothing looks like. Before believing a clean integration run,
confirm the passed count moved — and read the skip reasons, which name the
exact URI or DSN that did not answer:

```
uv run pytest -m integration -rs
```

`-rs` is not in `addopts`, so you have to ask for it. The messages are written
to be actionable (`Neo4j at bolt://localhost:7688 did not answer 'RETURN 1'.
Start it with docker compose -f docker-compose.test.yml up -d neo4j`), and the
URI in the message is the one the process actually used — which is how you
catch an environment variable that never reached the `pytest` process, since
all of them are read at module import
([Step 2](#step-2-point-the-suite-at-the-containers)).

The skip guards are per-module and complete on purpose. Both adapter suites
have one test that builds its own driver or pool (`test_connect_owns_and_closes_its_driver`,
`test_connect_owns_and_closes_its_pool`), and each requests the fixture it does
not otherwise need purely for the skip — because without it that one test
*fails* while the other 102 skip, which is exactly how the gap was found. If
you add a test that connects for itself, depend on `neo4j_driver` or `pool`
too: a skip guard is only honest if every test in the module is behind it.

**A failure means the opposite of a skip: the backend answered a real probe, so
it is serving, and the suite is telling you about your code.** That is the
whole point of probing with a query rather than a connect — the accuracy suite
once probed Ollama's model *listing*, the model was listed but would not load,
and eight tests failed where they should have skipped (**B12**). A failure here
should be read as a finding about the adapter, the Cypher, or the schema, not
as infrastructure noise.

Three exceptions, all with a name and a cause:

- **36 Neo4j failures, varying run to run** — you passed `-n auto`, and the
  workers are wiping each other's data
  ([B10f](#step-3-run-it-serially---m-integration-never--n-auto)).
- **21 or 13 `FailedHealthCheck: ... multiple different executors`** — the unit
  and integration suites are in one invocation
  ([B10m](#step-4-do-not-combine-it-with-the-unit-suite-in-one-invocation)).
- **A single `DeadlineExceeded` that passes on its own** — hypothesis's 200 ms
  default deadline is not survivable under contention for a property doing real
  work, and the shape recurred three times before it was fixed at the class
  level. `tests/conftest.py` now registers a suite-wide `deadline=None`
  profile, so this can only reach you two ways: you passed
  `--hypothesis-profile=strict`, which opts back in deliberately, or someone
  put `deadline=` back into a `settings()` decorator, which outranks the
  profile — `tests/unit/test_hypothesis_deadline_policy.py` exists to fail
  when they do. Neither is a finding about the backend.

Everything else that fails, failed for a reason in the code. Re-run one test
with `-x` and read it.

Two more results that are neither, and both mean something:

- **`0 passed, 0 skipped`, "no tests ran"** — you did not pass `-m
  integration`, or you passed a path that has none. `addopts` deselects the
  marker, so the summary block `tests/conftest.py` prints at the end of every
  default run (`197 'integration' tests -- uv run pytest -m integration`) is
  the confirmation that they exist and did not run.
- **The wheel test failing** — `tests/integration/test_wheel_contents.py`
  never skips, so it is the one result in this suite with no environmental
  reading at all. It builds and installs a wheel; a failure means the bundled
  domain schemas are not packaged, which is silent and total for installed
  users.

### Tear down

```
docker compose -f docker-compose.test.yml down -v
```

That removes the containers, the network and the volumes, and gives back ports
**7688**, **7475** and **5434** — which is the only thing tearing down is
strictly *needed* for, since those are the ports another checkout of this
project would want.

Between runs, prefer stopping to tearing down:

```
docker compose -f docker-compose.test.yml stop
docker compose -f docker-compose.test.yml start   # then wait for (healthy) again
```

`stop` keeps the containers and their state, so `start` is seconds rather than
a fresh image start plus up to two and a half minutes of healthcheck retries
([Step 1](#step-1-bring-up-docker-composetestyml-and-wait-for-the-healthchecks)).
It is the right verb while you are iterating on an adapter.

**Use `-v` whenever you use `down`.** Neither service declares a named volume,
so both keep their data in the image's *anonymous* volumes; `down` without `-v`
detaches those rather than deleting them, and the next `up -d` creates fresh
ones. You get the empty database either way and accumulate an orphaned volume
each time. Nothing warns you, and `docker volume ls` is where it shows up.

Stale data is not a correctness concern, which is why this is a convenience
decision rather than a hygiene one. Every suite arrives at its own clean slate:
the Neo4j suite runs `MATCH (n) DETACH DELETE n` before **every** test and
between hypothesis examples, the pgvector suite truncates
`kg_vectors_test_<worker>`, and both call `ensure_schema()` once per pytest
process behind a module-level `_schema_ready` flag — every DDL statement in
both adapters is `IF NOT EXISTS`, so it is an optimisation, not a correctness
device.

That last point is the one reason to reach for `-v` deliberately rather than
out of tidiness. **DDL that runs against a warm database proves less than DDL
that runs against an empty one**, and this project has the receipt: cosmic-ray
replaced the pgvector schema loop's iterable with `[]` and the mutant survived
the whole suite, because every test worked against a table an earlier run had
created. The fix was
`test_ensure_schema_creates_the_table_from_nothing`, which drops
`kg_vectors_test_<worker>_fresh` itself and so does not depend on how you tore
down. **Neo4j has no equivalent** — constraints and indexes are database-wide,
so its `ensure_schema` tests
(`test_ensure_schema_creates_the_uniqueness_constraint`,
`test_ensure_schema_creates_the_lookup_indexes`) assert against whatever the
volume already holds, and only a run started from a genuinely fresh volume
proves the five DDL statements did the creating. Do a `down -v` before the run
you intend to believe on that point — before a release, and after touching
`SCHEMA_STATEMENTS` in `src/redstring/graph/adapters/neo4j.py`. It is the
"at least one test per stateful setup path must start from nothing" rule from
[`.claude/rules/testing.md`](https://github.com/tyevans/redstring/blob/main/.claude/rules/testing.md), enforced by hand
for the one adapter that cannot enforce it itself.

Two subsets are unaffected by any of this: `tests/integration/llm/` needs a
model server rather than a container, and the wheel test builds and installs
into a throwaway environment of its own. Tearing the compose file down makes
`tests/integration/graph/` and `tests/integration/vector/` **skip**, not fail
— so a habitual teardown plus a later forgetful `-m integration` produces the
`0 passed, N skipped` shape that reads as green
([reading the result](#reading-the-result-skipped-is-honest-failed-means-the-backend-is-reachable-but-wrong)).

## Part 2 — the mutation suites

A mutation run answers one question: **would the tests notice if the code were
wrong?** It applies a small defect to the source — a flipped comparison, a
deleted statement, a swapped operator — runs the unit suite, and records
whether anything failed. A mutant the suite kills is evidence; a mutant that
survives is a test you thought you had.

Neither runner is on the commit gate. Both are slow, both are read by a human
rather than by a threshold, and the number they print is
[never the deliverable](#step-5-classify-every-survivor-never-gate-on-a-count).

### Why both mutmut and cosmic-ray are kept

They overlap heavily and are kept anyway, for one concrete reason: **mutmut
3.x will not mutate decorated functions and cosmic-ray will.** A codebase whose
interesting code sits under `@field_validator`, `@dataclass` and pytest
decorators would have its most defect-prone surface silently exempted by mutmut
alone — and an exemption you cannot see is the failure shape this project
keeps hitting (see
[quality gates](../reference/quality-gates.md)).

Their configuration lives in two places, and they are configured to run the
same thing:

| | Runner | Configured in | Tests it runs |
|---|---|---|---|
| mutmut | `paths_to_mutate = ["src/redstring/"]` | `[tool.mutmut]` in `pyproject.toml` | `uv run pytest -x -q --no-header -p no:randomly` over `tests/unit/` |
| cosmic-ray | `module-path = "src/redstring"` | `cosmic-ray.toml` | `uv run pytest -x -q --no-header -p no:randomly tests/unit` |

Three flags are common to both commands and each earns its place:

- **`-x`** — a mutant is killed by the first failure; running the rest of the
  suite tells you nothing and costs everything.
- **`-p no:randomly`** — `pytest-randomly` reorders every run, and a mutation
  session compares thousands of runs against each other. Order-dependence is a
  bug in the test ([`.claude/rules/testing.md`](https://github.com/tyevans/redstring/blob/main/.claude/rules/testing.md)),
  but a session is not where you want to discover one.
- **`tests/unit`** — the path, not a marker. `addopts` still applies, so
  `integration` and `accuracy` are deselected; the unit suite is what runs.

That last choice has a consequence worth stating plainly: **mutants in
integration-only code are unkillable by the default configuration.** The Cypher
bodies in `src/redstring/graph/adapters/neo4j.py` execute only with Docker up
(**B10a**), so a session over that module needs an integration `test-command`
instead — the one recorded under **B10e**:

```
env KG_COMPLIANCE_MAX_EXAMPLES=5 ./.venv/bin/pytest -x -q --no-header -p no:randomly -m integration tests/integration
```

The `-m integration` there is not optional: without it `addopts` deselects
everything the adapter has, and the session mutates code no test executes —
which is a zero-survivor run manufactured on purpose.

### Step 1: prove the harness works — run the configured `test-command` unmutated

**`scripts/mutation.py` now does this for you, and refuses to start if the
baseline is not green.** Prefer it to the manual sequence below:

```
uv run python scripts/mutation.py cosmic-ray     # baseline, init, exec, report
uv run python scripts/mutation.py mutmut         # baseline, then mutmut run
uv run python scripts/mutation.py cosmic-ray --baseline-only   # just the check
```

It creates a detached worktree under `.mutation/worktree`, syncs it with
`--all-extras`, runs *that tool's own configured command* there, and stops if
the result is anything other than a green run with a positive pass count. The
last clause is the one that matters: a run that exits 0 having collected
nothing looks identical to a passing suite, and is exactly what produced the
incident below.

It wraps **both** tools deliberately — wrapping one would leave the other as
the unguarded path, and the run someone reaches for in a hurry is the one that
needs the guard.

The manual sequence, still correct and worth knowing:

```
uv sync --all-extras
uv run python -c "import neo4j, asyncpg, redis, langchain_openai; print('extras ok')"
uv run pytest -x -q --no-header -p no:randomly tests/unit
```

Run the *configured* command — `test-command` in `cosmic-ray.toml`, which is
`runner` in `[tool.mutmut]` plus the `tests/unit` path — in the *same checkout*
you are about to mutate, and require it green before you start. Not a variation
of it, not the same tests from your main tree: a mutation session's numbers are
meaningful only relative to a baseline you have seen pass, in the environment
that will produce them.

If you have retargeted the session at integration-only code, the baseline is
the `test-command` you retargeted it with — the B10e variant from
[why both are kept](#why-both-mutmut-and-cosmic-ray-are-kept), containers up
and all — not this one. The rule is "the configured command", not "the unit
suite".

This is the whole of the precaution, and it is not paranoia. Slice 7's first
cosmic-ray run reported **0 survivors out of 426**; a planner-only run before
it reported 0 out of 45. Both were worthless. The worktree had been synced with
`--extra dev`, `jellyfish` was absent, every mutant died on a collection error,
and `cr-report` showed `WorkerOutcome.NORMAL, TestOutcome.KILLED` for all 426
— *character for character what a perfect suite looks like*. The real run, once
the environment was fixed, had 136 survivors over 28 source lines, four of them
genuine defects.

**Nothing in either tool checks this for you**, which is why the wrapper does.
Its two open questions were settled the way the incident argues for: both tools
get it, and the baseline runs **in the worktree**, never in the main tree where
it would pass regardless of what the worktree is missing.

The refusal is a pure function of the baseline's exit code and output, and
`tests/unit/test_mutation_wrapper_refuses_a_bad_baseline.py` exercises the
refusals rather than the happy path — a guard nobody has watched fire is
indistinguishable from one that cannot.

### Step 2: run mutmut

```
uv run mutmut run
```

**There are no arguments to remember**, and that is deliberate: everything is
in `[tool.mutmut]` in `pyproject.toml`, so the command in the docs and the
command in your shell cannot drift apart.

```toml
[tool.mutmut]
paths_to_mutate = ["src/redstring/"]
tests_dir = ["tests/unit/"]
runner = "uv run pytest -x -q --no-header -p no:randomly"
also_copy = ["pyproject.toml", "tests/conftest.py"]
```

Three of those four keys carry a decision worth knowing before you read a
result.

**`tests_dir` is `tests/unit/`, so integration-only code cannot be measured
here.** Combined with `addopts`, the suite mutmut runs is exactly the commit
gate's, which is the same scoping cosmic-ray uses and the same limitation:
mutants in the Cypher bodies of `src/redstring/graph/adapters/neo4j.py` are
unkillable by this configuration because no unit test executes them (**B10a**).
Retargeting mutmut at the integration suite means editing `tests_dir` and
`runner` together, and then
[Step 1](#step-1-prove-the-harness-works--run-the-configured-test-command-unmutated)
is the *retargeted* command with the containers up.

**`runner` carries `-x -q --no-header -p no:randomly`** for the reasons given
under [why both are kept](#why-both-mutmut-and-cosmic-ray-are-kept): the first
failure is the kill, and `pytest-randomly` reordering every one of thousands of
runs is not something you want to be diagnosing mid-session.

**`also_copy` exists because mutmut runs from a copied tree.** It copies the
package and the tests, not the repository, so anything the suite reads from the
project root has to be named: `pyproject.toml` for `addopts` and the marker
registrations, `tests/conftest.py` for the shared fixtures. **A mutmut run that
fails wholesale — every mutant "killed", or a collection error on the first —
should be checked against that list before anything else is suspected.** It is
the same failure shape as a missing extra, arriving by a different route, and
it produces the same false perfect score
([Step 4](#step-4-read-the-report--a-zero-survivor-run-means-the-harness-is-broken)).

Scope the run. `paths_to_mutate` is the whole package, and a full session over
it is not something to start casually — cosmic-ray's arithmetic in
[Step 3](#step-3-run-cosmic-ray-in-its-own-worktree) applies here too, since
both runners pay one suite execution per mutant. Narrow `paths_to_mutate` to
the module you changed, or name it on the command line:

```
uv run mutmut run src/redstring/domain/preference.py
```

Read the results with:

```
uv run mutmut results          # survivors, grouped by file
uv run mutmut show <id>        # the diff for one mutant
uv run mutmut browse           # interactive, if you prefer it
```

`mutmut results` is the list you then work through in
[Step 5](#step-5-classify-every-survivor-never-gate-on-a-count) — every
survivor classified, no count treated as a verdict.

State lands in `.mutmut-cache` and `mutants/`, both gitignored, and **mutmut is
incremental against that cache**. That is a convenience while iterating on one
module and a hazard the moment the tests change: a mutant recorded as killed by
a test you have since weakened stays recorded as killed. Delete both before a
run whose numbers you intend to write down.

```
rm -rf .mutmut-cache mutants/
```

Unlike cosmic-ray, mutmut does **not** need its own worktree — it mutates the
copy under `mutants/`, not your working tree. It is therefore the cheaper of
the two to reach for on a single module, which is most of what either is used
for here. What it will not do is mutate a decorated function, so a clean mutmut
run over a module full of `@field_validator`s has said less than it appears to;
that is the entire reason
[Step 3](#step-3-run-cosmic-ray-in-its-own-worktree) exists.

### Step 3: run cosmic-ray in its own worktree

**`[cosmic-ray.distributor] name = "local"` mutates the working tree in
place.** cosmic-ray edits your tracked source, runs the suite, and writes the
file back — so an interrupted session leaves a mutant behind in a file git
considers yours. Run it from a `git worktree` or a fresh clone, never from the
checkout you are editing:

```
git worktree add ../redstring-mutation
cd ../redstring-mutation
uv sync --all-extras
uv run pytest -x -q --no-header -p no:randomly tests/unit   # Step 1, here
```

The sync in that worktree is not a formality. A new worktree has no venv, and
it is exactly where a `--extra dev` sync goes unnoticed and manufactures a
zero-survivor run — see
[before you start](#before-you-start-sync-with---all-extras). Run
[Step 1](#step-1-prove-the-harness-works--run-the-configured-test-command-unmutated)
*here*, in the worktree, not in the main tree where it would pass regardless.

Then the three commands, which are the ones `cosmic-ray.toml`'s own header
comment records:

```
uv run cosmic-ray init cosmic-ray.toml session.sqlite
uv run cosmic-ray exec cosmic-ray.toml session.sqlite
uv run cr-report session.sqlite
```

`init` builds the mutant list and runs nothing — the cheap way to find out how
large the job is before committing to it. `exec` is resumable against the same
`session.sqlite`, so an interrupted run continues rather than starting over,
and `cr-report` can be read against a partial session.

The config it reads is four lines and each is worth knowing:

```toml
[cosmic-ray]
module-path = "src/redstring"
timeout = 60.0
test-command = "uv run pytest -x -q --no-header -p no:randomly tests/unit"
excluded-modules = []
```

`timeout` bounds **a single mutant**, not the session — it is the escape from
an infinite loop a mutant introduced, nothing more. `excluded-modules` is empty
and should stay that way for the reason every exemption list in this repo is
empty: an entry is a visible decision in review, and a list nobody can see is
the failure shape this project keeps paying for (see
[quality gates](../reference/quality-gates.md)).

**Scope the session before you start it.** `module-path` is the whole package,
and a session over the whole package is not a thing to run — the arithmetic
says so. `domain/temporal_parsing.py` alone has **850 mutants**, of which 793
have never been run (**B54**), because each costs about 70 seconds: the mutant
re-runs the whole file, including two hypothesis properties at 300 examples
and a `dateparser` import. 850 × 70 s is roughly seventeen hours. Every
session this project has actually completed — `domain/interval.py` (217, all
classified), `temporal/inference.py` (95, all classified) — was scoped by
pointing `module-path` at one file.

Two sharper scoping levers when one file is still too big:

- **Narrow the `test-command`, not just the target.** B54's remaining 793 are
  affordable if the session runs the marker tests alone rather than the
  round-trip properties that dominate the 70 s. Split by target, not by
  patience — and remember that the narrowed command is then what
  [Step 1](#step-1-prove-the-harness-works--run-the-configured-test-command-unmutated)
  has to be green on.
- **cosmic-ray has no line filter.** To mutate part of a file, `init` the full
  session and then delete the rows outside the range of interest from
  `mutation_specs` and `work_items` in the session database, keying on
  `start_pos_row`. That is how the 57 precision-logic mutants of
  `temporal_parsing.py` were run, and the one that found a real defect — the
  quarter arithmetic, closed in `44e213d`.

Retargeting at integration-only code means changing `test-command` as well as
`module-path`, and the `-m integration` is not optional — see
[why both are kept](#why-both-mutmut-and-cosmic-ray-are-kept) for the B10e
command and the containers it needs up throughout.

Two things to check when the run ends, both learned by being bitten:

- **`git diff --quiet`.** A killed `exec` process leaves its mutant in the
  source, and one escaped into the working tree during the B10e session. This
  is the minimum check, and it is the reason for a separate worktree rather
  than a preference about tidiness. If a mutant *has* escaped, `git checkout --
  <path>` is the whole recovery; the danger is committing it, which is exactly
  how a corrupt `neo4j.py` once passed the full suite (**B10a**).
- **`session.sqlite`.** `.gitignore` covers `cosmic-ray.sqlite*` and
  `.cosmic-ray/` — not the filename in the documented commands — so a session
  database named `session.sqlite` shows up as untracked and is easy to commit
  by accident. Name it `cosmic-ray.sqlite` when you start, or delete it when
  you are done reading it.

Then read the report, with the prior that a good-looking number is the one to
distrust: [Step 4](#step-4-read-the-report--a-zero-survivor-run-means-the-harness-is-broken).

### Step 4: read the report — a zero-survivor run means the harness is broken

```
uv run cr-report session.sqlite        # cosmic-ray
uv run mutmut results                  # mutmut
```

`cr-report` prints one block per job — the mutation as a diff, its worker
outcome and its test outcome — then a summary with the job total, the number
complete, the surviving mutants and a survival rate. Read the summary first,
and read it with one prior in mind:

> **A perfect score is the result most likely to be false.**

**A high survivor count merely needs classifying. A zero usually means the
tests never ran.** That asymmetry is the whole of this step. Both of slice 7's
phantom runs — 0 out of 426, and 0 out of 45 for a planner-only session —
reported `WorkerOutcome.NORMAL, TestOutcome.KILLED` for every job, which is
character for character what a genuine kill prints. The outcome codes cannot
distinguish "an assertion failed" from "collection failed", because from
cosmic-ray's side both are a non-zero exit from the `test-command`. Nothing in
the report can tell you which you are looking at; only
[Step 1](#step-1-prove-the-harness-works--run-the-configured-test-command-unmutated)
can, and it has to have been run in the checkout that produced the report.

Three cheap checks on a result before you believe it:

- **Does the elapsed time make sense?** Mutant count × unmutated suite runtime
  is the floor. A 426-mutant session that finished in minutes did not run a
  suite 426 times. This is the single most reliable tell, because the phantom
  runs are fast for the same reason they are perfect: a collection error costs
  a fraction of a real run.
- **Did the mutant count move?** `init` reports it and it changes only when
  the source does. A count that shifted after a dependency change or a fresh
  worktree is the environment talking, not the code.
- **Did anything survive that survived last time?** The annotation mutants of
  [Step 5](#step-5-classify-every-survivor-never-gate-on-a-count) are
  unkillable in this codebase, so a session over a well-annotated module that
  reports *none* of them has almost certainly not run the suite. Their absence
  is a smoke alarm; their presence is the expected background.

The equivalent for mutmut is `mutmut results`, and the failure has the same
shape from a different cause: mutmut runs from a copied tree, so a missing
entry in `also_copy` fails collection for every mutant and prints a clean
sheet ([Step 2](#step-2-run-mutmut)). Check the same three things.

**Record partial runs as partial, with the denominator.** An interrupted
session is a legitimate result — `exec` is resumable and `cr-report` reads a
half-finished database quite happily — but the number that matters is the one
it did not cover. The B10e session over
`src/redstring/graph/adapters/neo4j.py` completed **16 of 289 mutants
(5.5%)**: 11 killed, 5 survived, and all 5 survivors were
`ReplaceBinaryOperator_BitOr_*` — the `|` in `X | None` annotations. So nothing
of concern was found and also nothing much was looked at, which is why the
backlog entry says in so many words **do not read the adapter as
mutation-tested**. A survivor list without its denominator reads as a verdict;
with it, it reads as the sample it is. The same applies to B54's 57 of 850 on
`domain/temporal_parsing.py` — that sample found a real defect in the quarter
arithmetic, and says nothing whatever about the other 793.

What the report is *not* is a score. Take the survivor list into
[Step 5](#step-5-classify-every-survivor-never-gate-on-a-count) and account for
every entry; the survival rate `cr-report` prints is a property of how well
annotated the module is at least as much as of how well tested it is.

### Step 5: classify every survivor; never gate on a count

**The bar is "every survivor is understood", never a number.** There is no
target percentage in this repo and adding one would be actively harmful — see
below for why a survival-rate gate rewards deleting type annotations.

Work through them like this:

1. **Group by diff hunk.** The same source line usually accounts for a dozen
   mutants; the list is much shorter than it looks.
2. **Label each group** equivalent or not, with a reason you could defend.
3. **For every non-equivalent survivor, write the test that kills it** — or a
   `BACKLOG.md` entry saying what is unenforced and what you learned, in the
   same commit that passes it by.

Be sceptical of your own "equivalent" labels. In slice 6 a `>` → `>=` survivor
was equivalent only *because* the ordering was total — so the totality had to
be asserted before the label was honest. An equivalence argument that depends
on an unasserted property is a finding, not a dismissal.

#### Equivalent mutants you will see every run

Measured on `graph/adapters/memory.py`: **230 mutants, 78 survivors, 73 of them
equivalent — 55 from annotations alone.** The proportion grows with how well
annotated the code is. The recurring classes:

- **PEP 563 annotations.** Every module here has
  `from __future__ import annotations`, so annotations are strings that are
  never evaluated. cosmic-ray rewrites the `|` in `X | None` as each of eleven
  other binary operators:

  ```
  -    async def get_entity(self, ...) -> Entity | None:
  +    async def get_entity(self, ...) -> Entity + None:
  ```

  No test can kill those, here or in any other PEP 563 codebase. **This is the
  reason not to gate on a survival rate**: the cheapest way to improve the
  number would be to delete type annotations.
- **`if TYPE_CHECKING:` negated.** The block is false at runtime either way,
  so nothing observable changes — and `memory.py` has one, as does most of
  the package.
- **`*,` turned into `/,`** — keyword-only made positional-only. The mutant
  only *permits* calls nobody makes; every existing caller still type-checks
  and still runs.
- **Comparisons a guard clause has already narrowed.** `_touches` in
  `graph/adapters/memory.py` dispatches on
  `direction: Literal["out", "in", "both"]` with `if direction == "out"` then
  `if direction == "in"` then a bare `return`. Widening the second `==` to
  `>=` or `!=`-plus-inversion is unkillable for the values the annotation
  admits, because the first branch has already removed `"out"` and only
  `"both"` remains.

The classification, not the count, is the deliverable — so write the reason
down next to each group. "Annotation" is a sufficient reason; "probably
equivalent" is not, and the difference is exactly the slice 6 `>` → `>=`
survivor that was equivalent only because an unasserted ordering happened to
be total.

#### Survivors worth investigating

The ones that matter are where a test passes **for an accidental reason** —
where the test's input happens to make the correct and the broken
implementation agree. Neither review nor property tests find these; mutation
testing and essentially nothing else does. Real examples from this repo:

- `==` replaced by `is` on a string filter survived, because the tests queried
  with string *literals* and CPython interns those. A caller passing a
  runtime-built string would have got an empty result.
- `==` replaced by `<=` on a UUID endpoint comparison survived, because the
  test used random `uuid4`s — it would have caught the bug about half the time,
  depending on how the ids happened to sort.
- The pgvector schema loop's iterable replaced with `[]` survived, because
  every test ran against a table an earlier run had created — the DDL was
  never observed doing anything (see [tear down](#tear-down)).
- `min(1.0, …)` widened to `min(2.0, …)` in `domain/vector.py::cosine_score`
  survived, and the survivor is **understood but not equivalent**: the clamp is
  genuinely unenforced, and roughly 2 × 10^6 random vectors failed to produce
  an input that reaches it (**B10n**). That is what an honest non-equivalent
  label looks like — a measurement and a backlog entry, not a test bolted on
  and not a clamp deleted.

The full catalogue of input shapes that make two implementations agree is the
table in [`.claude/rules/testing.md`](https://github.com/tyevans/redstring/blob/main/.claude/rules/testing.md) and
`CLAUDE.md`. Read a survivor against it before concluding anything: the
question is always **what other implementation would also pass this test?**

### Hand-verifying a survivor: `PYTHONDONTWRITEBYTECODE=1` and `dis.dis`

The obvious way to check a survivor — edit the source, run pytest, revert — has
a trap that costs about an hour when you meet it cold (**B37**):

```
PYTHONDONTWRITEBYTECODE=1 uv run pytest -x -q -p no:randomly tests/unit/...
```

**CPython validates a cached `.pyc` on `(mtime, size)` only.** An edit that
leaves the file the same size and lands within the same mtime second — `1.0`
for `2.0`, `==` for `is`, `> 1` for `> 2` — keeps the *previous* bytecode
loaded, so the mutant never runs and the result is a lie in whichever direction
is least helpful. In slice 5b it presented as `Entity` accepting
`confidence=1.5` against a validator whose source plainly rejected it.

When a survivor looks impossible, settle it by disassembling what is actually
loaded rather than reading the file again:

```python
import dis
from redstring.domain.entity import Entity

dis.dis(Entity.__init__)
```

That is what closed the slice 5b hour: the constant in the bytecode was `2.0`
while the file said `1.0`.

cosmic-ray is not obviously affected — it runs each mutant in a fresh
subprocess, and its slice 5b survivors matched hand verification once the cache
was cleared — but nothing proves it immune. **A run that disagrees with a hand
check should suspect this first**, and `find src -name __pycache__ -exec rm -rf
{} +` costs nothing before deciding the tool is wrong.
