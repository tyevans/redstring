---
paths:
  - "tests/**/*.py"
  - "tests/compliance/**"
---

# Testing Conventions

How tests are organised, marked, and run in this repo, and which of those
choices are load-bearing rather than taste.

Four trees, and they are not interchangeable: `tests/unit/` (the commit gate),
`tests/compliance/` (shared port contracts, not itself collected),
`tests/integration/` (real backends from `docker-compose.test.yml`), and
`tests/accuracy/` (an empty marker package — see below before believing
otherwise).

The second frontmatter path is deliberate. `tests/compliance/` holds no
`test_*.py` files, so `tests/**/*.py` matches it only by accident of the glob
and a narrower pattern would silently stop applying these conventions to the
one place a defect propagates to every adapter at once.

Related:

- `docs/reference/quality-gates.md` — what the commit hook runs, and why not
  to run it yourself.
- `docs/how-to/run-integration-and-mutation-suites.md` — the deliberate,
  non-default runs.
- `docs/how-to/implement-a-store-adapter.md` — subclassing a compliance suite
  for a new adapter.
- `.claude/rules/recurring-defects.md` — the test shapes that pass while
  proving nothing.
- `.claude/rules/definition-of-done.md` — what must be true before a change
  lands.

## Structure

Four top-level trees, and the distinction between them is what test runs
where, against what:

| Tree | Collected by default? | Needs |
|---|---|---|
| `tests/unit/` | yes | nothing external |
| `tests/compliance/` | **never collected** | imported by unit and integration modules |
| `tests/integration/` | no (`-m` excludes it) | `docker-compose.test.yml` backends; `llm/` also a live LLM |
| `tests/accuracy/` | no | nothing — it is empty |

`tests/compliance/` is the one that breaks the pattern and the one worth
understanding first. It is a *library*, not a suite: `graph_store.py`,
`vector_store.py`, `cache.py` and `strategies.py`, with no `test_*.py` module
and no `Test*` class, so pytest's default collection walks past it. The
contract classes are named `GraphStoreCompliance`, `VectorStoreCompliance` and
`CacheCompliance`; they become tests only where an adapter's own module
subclasses them under a `Test*` name. One consequence follows from that and
governs where regressions land: a shared-contract defect fixed in an adapter's
test file is fixed for one adapter, while the same case added to the
compliance module is enforced against every present and future one. Put it in
the compliance module first, then specialise if an adapter genuinely differs.

### `tests/unit/`

Mirrors the package layout under `src/redstring/` one directory per package —
`aggregates/`, `consolidation/`, `domain/`, `events/`, `extraction/`,
`graph/`, `llm/`, `projections/`, `temporal/`, `vector/` — with the
cross-cutting surface gates (`test_composition.py`,
`test_public_surface_is_self_contained.py`, `test_end_to_end_example.py`)
sitting at the top level because they are about the package as a whole rather
than one module of it. This tree is the commit gate: it needs nothing
external, and it is what the pre-commit hook runs.

It is also where each adapter's compliance subclass lives. A contract class in
`tests/compliance/` is inert until a module here subclasses it under a `Test*`
name, and for the in-memory adapters that happens in exactly three files:

| File | Subclass | Contract |
|---|---|---|
| `tests/unit/graph/test_memory_store.py` | `TestMemoryStore` | `GraphStoreCompliance` |
| `tests/unit/vector/test_memory_store.py` | `TestMemoryVectorStore` | `VectorStoreCompliance` |
| `tests/unit/llm/test_memory_cache.py` | `TestMemoryCache` | `CacheCompliance` |

Each is a two-line class: the subclass plus whatever the contract requires it
to supply. The integration tree does the same thing for the real backends
(`TestNeo4jStore`, `TestPgVectorStore`), which is why the same contract runs
against both an adapter that hands back the object it was given and one that
rebuilds it from a row — the difference that catches an identity-vs-equality
defect no single adapter can.

So a new in-memory adapter is added by writing the subclass here, not by
copying cases out of the compliance module. And a case that only makes sense
for one adapter belongs in that adapter's file, below the subclass — anything
stated about the port itself goes up into `tests/compliance/`.

### `tests/compliance/`

Five modules and no tests of its own:

| Module | Holds |
|---|---|
| `graph_store.py` | `GraphStoreCompliance` — the `GraphStore` contract |
| `vector_store.py` | `VectorStoreCompliance` — the `VectorStore` contract |
| `cache.py` | `CacheCompliance` — the `Cache` contract |
| `strategies.py` | hypothesis strategies for the domain types those use |
| `__init__.py` | package marker |

**Nothing here is collected.** No module matches `test_*.py` and no class
matches `Test*`, so pytest walks past the directory and the contract classes
are inert until an adapter's own module subclasses one under a `Test*` name.
That is the mechanism, and it is deliberate: the suite runs once per adapter
that opts in, never once on its own, so there is no way for a contract to be
"passing" without an implementation behind it.

An adapter opts in by supplying one thing — `new_store()` for the two stores,
a `cache` fixture for the cache. `new_store()` must return an **empty** store,
freshly isolated on every call, because the property tests call it once per
generated example; hypothesis reuses the surrounding fixture across examples,
so a shared store would let example *n* decide example *n+1*. The example-based
tests get a `store` fixture the compliance class defines in terms of
`new_store()`, which is why the adapter implements exactly one method.

Both store suites read `KG_COMPLIANCE_MAX_EXAMPLES` (default 50) at import to
set `max_examples`. See the compliance-suite section below for why that is an
environment variable rather than a literal.

**A regression on a shared contract lands here first.** Fixing it in an
adapter's own test module fixes it for that adapter; adding the case here
enforces it against every adapter that exists now and every one added later.
Specialise downward afterwards only if an adapter genuinely differs — and if
it does, say in what way, because a port two implementations satisfy
differently is usually an under-specified port rather than two correct
adapters.

Three consequences worth stating outright, each of which this project has
already paid for:

- **A new read method needs its mutation-isolation test in this directory, in
  the same edit.** Search for `_mutate` and copy the shape. `_mutate` reaches
  into nested containers on purpose: a shallow-copying adapter passes a
  flat-dict mutation and fails a nested one, and the nesting is what gives the
  property teeth. Behavioural tests cannot see this defect at all — handing
  back the live internal object is correct on every read and wrong only
  afterwards.
- **State contracts in port terms, and assert them identically for both
  adapters.** `CacheCompliance` asserts `get` returns `str` and not `bytes`
  precisely because a Redis client left at its defaults returns `bytes` while
  the in-memory reference returns `str` — a caller comparing against a string
  literal would pass every unit test and never match in production. An
  in-memory reference that is *more forgiving* than the real backend is the
  failure mode the whole directory exists to prevent.
- **Generate identifiers rather than fixing them.** `strategies.py` draws
  tenant ids instead of hand-picking two, because tenant isolation must hold
  for any pair of distinct tenants, not the pair someone typed.

Keep time out of it. `CacheCompliance` takes caller-supplied epoch floats so
that "an event 90 seconds ago" is a number rather than a 90-second test.

`tests/integration/` splits by port — `graph/` (Neo4j), `vector/` (pgvector),
`llm/` — alongside `test_wheel_ships_the_domain_schemas.py`, which is a
packaging check rather than a backend one. Only the `llm/` subset needs a live
model; the rest needs containers.

`tests/accuracy/` contains `__init__.py` and nothing else. It collects zero
tests, and the `accuracy` marker is declared and unused. Do not read it as an
extraction-quality suite — that suite does not exist. Tracked as **B12**; the
choice is to build it or delete both the package and the marker, and until
then no claim about measured extraction quality is backed by anything in this
repo.

### `tests/integration/`

One subdirectory per port that has a real backend, plus one file that is not
about a backend at all:

| Path | Subclass / subject | Needs |
|---|---|---|
| `graph/test_neo4j_store.py` | `TestNeo4jStore(GraphStoreCompliance)`, `TestNeo4jSpecifics` | Neo4j from `docker-compose.test.yml` |
| `vector/test_pgvector_store.py` | `TestPgVectorStore(VectorStoreCompliance)`, `TestPgVectorSpecifics` | Postgres + pgvector from `docker-compose.test.yml` |
| `llm/test_live_endpoint.py` | the LangChain adapter against a real OpenAI-compatible server | a **live model** |
| `llm/test_live_pipeline.py` | chunk → extract → merge → emit against that model | a **live model** |
| `test_wheel_ships_the_domain_schemas.py` | `uv build` → throwaway venv → render all six bundled domains | `uv`, no backend |

Every module carries `pytestmark = pytest.mark.integration`, and `addopts`
excludes that marker, so none of this runs on commit. Start the backends
deliberately:

```bash
docker compose -f docker-compose.test.yml up -d
uv run pytest -m integration
```

The compose file is the whole infrastructure story: `neo4j:5-community` on
**7688** and `pgvector/pgvector:pg16` on **5434**, chosen off the defaults so
nothing collides with a local install or another project's containers.
Override with `KG_TEST_NEO4J_URI` / `KG_TEST_NEO4J_USER` /
`KG_TEST_NEO4J_PASSWORD` and `KG_TEST_POSTGRES_DSN`. Both services declare a
healthcheck because `up -d` returns when the container is *running*, not when
the server can serve — and Postgres's names the database, since `pg_isready`
with no arguments succeeds against the bootstrap server before `POSTGRES_DB`
exists. There is no `apoc`: the Neo4j adapter is plain Cypher, and requiring a
plugin would narrow which managed offerings can host this library.

**Only the `llm/` subset needs a live LLM**, and it is not in the compose
file — point it at whatever you are running with `KG_LLM_BASE_URL` and
`KG_LLM_MODEL`:

```bash
KG_LLM_BASE_URL=http://host:8080/v1 uv run pytest -m integration tests/integration/llm/
```

The two store subdirectories need only containers, and
`test_wheel_ships_the_domain_schemas.py` needs neither. It is marked
`integration` for cost, not infrastructure: it builds a wheel and creates a
virtualenv, seconds rather than milliseconds. It exists because
`domain_system_prompt(...)` reads YAML off disk, so in a source checkout every
other test passes whether or not those files are in the distribution — the
inputs-that-cannot-distinguish shape from
`.claude/rules/recurring-defects.md`. Run it before a release.

**Every skip probe must prove the backend can serve, not that it answers.**
This is the one convention to carry into any new integration module, and it is
the one this repo has already paid for: the accuracy suite probed Ollama's
*model listing*, the model was listed, it would not load, and eight tests
failed instead of skipping (**B12**). So Neo4j's probe runs `RETURN 1` and
requires the answer to be `1`; pgvector's creates the extension and
round-trips a vector through a temporary table, because `pgvector/pgvector`
ships the files but a database that never ran `CREATE EXTENSION` cannot store
one; and the LLM probe asks for a real completion with a generous token
budget, because the deployment lists every model it is configured for whether
or not the weights load, and a stingy budget would report "no LLM here" for a
reasoning model spending 150 tokens on chain of thought.

A store adapter here supplies the same one thing its in-memory counterpart
does — `new_store()` returning an empty, isolated store — and adds a
`Test*Specifics` class for what is true of that backend alone: schema
creation, encoding fidelity, query plans, connection handling. Anything
stated about the *port* goes up into `tests/compliance/`. Running the real
adapters against the same contract as the in-memory ones is what makes the
contract mean anything: pgvector rebuilds ids from a row where the in-memory
store hands back the object it was given, which is the difference that catches
an identity-vs-equality defect no single adapter can.

Two operational constraints follow from the shared backends and both have
their own subsections below: `tests/integration/graph/` must run serially
(`new_store()` calls `_wipe`, a real `MATCH (n) DETACH DELETE n` on the one
shared database), while `tests/integration/vector/` is parallel-safe because
its table name carries `PYTEST_XDIST_WORKER`. And a unit and an integration
subclass of the same compliance suite cannot share one pytest invocation.

See `docs/how-to/run-integration-and-mutation-suites.md` for the full runbook
and `docs/how-to/implement-a-store-adapter.md` for adding a backend.

### `tests/accuracy/`

**Empty.** The package contains `__init__.py` and nothing else; no module in
the repo carries `pytest.mark.accuracy`, and `uv run pytest -m accuracy
tests/accuracy/` collects **zero tests**. The `accuracy` marker is declared in
`pyproject.toml` and used by nothing except the `addopts` deselection that
excludes it.

Its own docstring still describes a suite that measures precision, recall and
F1 against documentation samples via a running Ollama instance. **That
description is stale and should not be believed.** Slice 6 deleted
`tests/accuracy/test_extraction_accuracy.py` — the only file that ever carried
the marker — because it exercised `OllamaExtractionService`, which no longer
exists. Nothing replaced it.

So: **no claim about this library's extraction quality is backed by anything in
this repo.** Every other tree here checks that the library is *correct* —
invariants hold, adapters agree on their ports, events replay to the same
graph. Correct and accurate are different properties, and extraction can
satisfy every invariant in `tests/unit/` while finding entities that are simply
wrong. There is currently no measurement that would notice.

Tracked as **B12**, which names the four pieces this would take and notes that
the environmental blocker is gone — `tests/integration/llm/` talks to a real
model and is green, so what is missing is the graded corpus rather than a
place to run it. Two things follow for anyone working near here:

- **Do not cite an accuracy number.** If a prompt, threshold or model choice is
  justified by "it extracts better", that justification is currently
  unmeasured. Say so, in `BACKLOG.md`, rather than implying a suite backs it.
- **The end state is a decision, not a default.** Either build the suite or
  delete both the package and the marker. Leaving an empty package named after
  a guarantee is the worse of the three options, because it reads from the
  directory listing as coverage that exists.

The suite's original skip probe is also the origin of the rule stated for
`tests/integration/` above: it probed Ollama's *model listing*, the model was
listed but would not load, and eight tests failed instead of skipping. If this
package is ever rebuilt, its probe must require a real completion.

## Port compliance suites

Three contract classes, each in its own module under `tests/compliance/`, each
made to run by being subclassed from an adapter's own test module under a
`Test*` name:

| Contract | Adapter supplies | Subclassed by |
|---|---|---|
| `GraphStoreCompliance` | `async new_store()` (+ `dispose()` if it owns a connection) | `TestMemoryStore`, `TestNeo4jStore` |
| `VectorStoreCompliance` | `async new_store()` (+ `dispose()`), may override `DIMENSION` | `TestMemoryVectorStore`, `TestPgVectorStore` |
| `CacheCompliance` | a `cache` fixture | `TestMemoryCache` |

The subclass is the whole opt-in — usually two lines:

```python
class TestMemoryStore(GraphStoreCompliance):
    async def new_store(self) -> GraphStore:
        return InMemoryGraphStore()
```

`new_store()` must return an **empty** store, isolated from every other, on
every call. The property tests call it once per generated example precisely
because hypothesis reuses the surrounding fixture across examples; a shared
store would let example *n* decide example *n+1*. The `store` fixture the
example-based tests use is defined by the contract class in terms of
`new_store()`, which is why an adapter implements one method rather than two.
`dispose()` is a no-op by default and must be overridden by any adapter
holding a driver or pool, or a run leaks one connection per example.

Anything specific to one adapter goes in a sibling `Test*Specifics` class in
the same file, never into the contract.

### `KG_COMPLIANCE_MAX_EXAMPLES` tunes a run, not a subclass

Both store suites set their hypothesis budget the same way:

```python
DEFAULT_MAX_EXAMPLES = int(os.environ.get("KG_COMPLIANCE_MAX_EXAMPLES", "50"))

compliance_settings = settings(
    deadline=None,
    max_examples=DEFAULT_MAX_EXAMPLES,
    suppress_health_check=[HealthCheck.too_slow],
)
```

That read happens at **module import**, before any subclass body executes, and
the resulting `settings` object is shared by every `@compliance_settings`
decorator in the file. So the variable is a **per-run** lever and nothing else:

```bash
KG_COMPLIANCE_MAX_EXAMPLES=10 uv run pytest -m integration
```

**"Turn the examples down for the slow adapter" is not achievable as written**
— tracked as **B10h**. The value is fixed for both subclasses of a suite by
the time either exists, so a Neo4j run and an in-memory run share it. At the
default of 50 that multiplies into roughly 750 database resets for the graph
suite, which is why the integration runbook leads with lowering it.

The obvious workaround is ruled out deliberately: an explicit
`settings(max_examples=...)` on a subclass **outranks every hypothesis
profile**, so hard-coding one would make `--hypothesis-profile` inert for
every adapter of that port, not just the slow one. Reading the environment is
what keeps the promise that an adapter opts in solely by implementing
`new_store()`. Fixing B10h properly means a hypothesis *profile* per adapter,
or a class attribute the shared decorator reads through a `settings` callable
that still lets a profile win — not reversing that reasoning. Slice 4 measured
the cost as negligible (25 s / 43 s / 66 s at 10 / 25 / 50 examples) and
correctly left it.

`DIMENSION = 8` on `VectorStoreCompliance` shows the shape that *does* work
per subclass: a plain class attribute, read through `self` inside the test
body, so an adapter can override it. `max_examples` cannot be done that way
because the decorator is evaluated at class-definition time.

Two consequences for how you read a run:

- **`max_examples` is environment-tunable, so no property here reliably covers
  a boundary.** Mutation runs lower it to 5. Pin `0`, `1`, empty and maximum
  values with `@example` alongside the property — `InMemoryVectorStore.search`
  had two `k` mutants that died on one cosmic-ray run and survived the next
  with nothing in the adapter changed, because `k=0` was only ever reached by
  a sampler.
- **A mutation or timing comparison is only meaningful at a fixed value.** Two
  runs at different budgets are not comparable, and the budget does not appear
  in pytest's output. Set it explicitly when the number matters.

`CacheCompliance` uses no hypothesis at all — its inputs are example-based and
time is a caller-supplied epoch float — so the variable does not reach it.

See `docs/how-to/implement-a-store-adapter.md` for writing a new subclass and
`docs/how-to/run-integration-and-mutation-suites.md` for the runbook.

### The coverage gate

`tests/unit/graph/test_compliance_coverage.py` and
`tests/unit/vector/test_compliance_coverage.py` are tests *about* the
compliance suites. Each derives the port's read methods by introspection and
fails when one of them has no mutation-isolation test and no tenant-isolation
test.

Derivation is from **return annotations, not names**:

| Module | Port | A method is a "read" if its return mentions |
|---|---|---|
| `tests/unit/graph/test_compliance_coverage.py` | `GraphStore` | `Entity`, `Relationship` or `Alias` |
| `tests/unit/vector/test_compliance_coverage.py` | `VectorStore` | `VectorRecord` or `VectorMatch` |

`_mentions` recurses through `typing.get_args`, so `list[Entity]` and
`dict[str, list[Entity]]` both count. `delete_relationship() -> bool` and the
`upsert_*` methods are excluded automatically, and a read method added to the
port is included automatically. Because both ports annotate under
`if TYPE_CHECKING`, each module passes a `_PORT_NAMESPACE` into
`typing.get_type_hints` — adding a domain type to a port signature means
adding it there, and that is the one thing introspection cannot infer.

**A new read method needs both tests in the same edit**, named to the
convention, and then neither of these modules needs touching:

```
test_<method>_returns_copies
test_<method>_never_crosses_tenants
```

`GraphStore` additionally carries two hand-written registries,
`ISOLATION_COVERAGE` and `TENANT_COVERAGE`, mapping eight methods to
differently-named tests that predate the convention. **Do not add to them.**
They exist so eight working tests did not have to be renamed, and they are
safe only because they are checked in both directions:
`test_registered_tests_exist_on_the_compliance_class` fails when a rename
empties an entry, and `test_the_registries_do_not_outlive_the_port` fails when
an entry names a method the port no longer has. That is this repo's rule about
exemption lists applied to itself — an entry matching nothing would otherwise
pass silently. `VectorStore` has no legacy registry; every test there is
already named to the convention, which is the state to keep.

`ISOLATION_EXEMPT` is empty in both modules and takes a method name mapped to
a reason, enforced non-blank by `test_exemptions_carry_a_reason`. An entry is
a visible decision that the method cannot hand back a mutable view of stored
state; an *absent* entry is the omission the gate exists to catch. There is
deliberately no tenant exemption list at all — every read path needs its own
proof.

Each module also guards the guard, because a detector that finds nothing
passes vacuously: the graph module asserts `len(read_methods()) >= 8` and the
vector module asserts `read_methods() == {"get", "search"}`. The vector module
carries two further guards of the same kind — `metadata_dicts()` must actually
generate the reserved `entity_type` key (it once could not: keys were drawn
from `st.text(max_size=6)` and the key is eleven characters, hiding a real
in-memory/pgvector divergence where a stored `{"entity_type": ["person"]}`
raised `TypeError: unhashable type: 'list'` instead of returning `[]`), and
`compliance_settings.max_examples` must equal `DEFAULT_MAX_EXAMPLES` so the
environment lever cannot be quietly hard-coded.

The reason all of this is executable rather than written down: four read
methods (`find_by_blocking_key`, `neighbors`, `find_by_blocking_keys`,
`get_relationships_for`) shipped in slice 3 with complete behavioural tests
and no isolation test, and a mutation run — not review — found each time that
a shallow copy passed everything. Behavioural tests cannot see the defect,
since handing back the live internal object is correct on every read and wrong
only afterwards. The rule was written down twice before it was made a test.

**Give every new store port this gate.** Copy the vector module — it is the
smaller of the two, having no legacy registry — and change the port, the
namespace, the domain types, and the vacuity assertion. See
`.claude/rules/definition-of-done.md` and
`docs/how-to/implement-a-store-adapter.md`.

## Markers

Four markers are declared in `pyproject.toml`, and only one of them is doing
real work:

| Marker | Applied to | Status |
|---|---|---|
| `integration` | every module under `tests/integration/` | **load-bearing** — it is what keeps real backends out of the commit gate |
| `unit` | seven tests in `tests/unit/test_jellyfish_import.py` | decorative; nothing selects on it |
| `accuracy` | nothing | declared and unused (**B12**) |
| `slow` | nothing | declared and unused |

The deselection is:

```toml
addopts = ["-m", "not accuracy and not integration"]
```

So **`integration` is excluded from the default run and therefore from the
commit gate**, which is what keeps `git commit` infra-free: no Neo4j, no
pgvector, no live model, no `docker compose up` before you can commit. A CLI
`-m` overrides this one, which is how the deliberate runs get at it:

```bash
uv run pytest -m integration
```

Three things follow that are easy to get wrong:

- **Marking a module `integration` is what excludes it, not putting it in
  `tests/integration/`.** Path has no effect on selection. Every module in
  that tree carries `pytestmark = pytest.mark.integration` at module level
  (`test_wheel_ships_the_domain_schemas.py` marks its single test function
  instead). A new integration module without the mark runs on commit and
  fails on the developer's machine, or worse, passes there because a
  container happens to be up.
- **The mark is about *cost and prerequisites*, not about touching a
  database.** `test_wheel_ships_the_domain_schemas.py` needs no backend at
  all; it is marked because building a wheel and creating a virtualenv takes
  seconds rather than milliseconds. If a test would make the commit gate a
  noticeably worse experience, mark it.
- **`unit` is not a filter.** Do not add it to new tests expecting it to mean
  anything — the default run is "everything not deselected", so an unmarked
  test in `tests/unit/` is already in the gate. The seven existing uses are
  historical.

`accuracy` and `slow` naming nothing is the state to resolve rather than
preserve. `accuracy` is tracked as **B12** (see `tests/accuracy/` above): the
suite it was declared for was deleted, and the marker plus the empty package
should either be rebuilt or removed together. `slow` has never been applied;
adding a test that deserves it is fine, but do not deselect on a marker no
test carries — an `-m "not slow"` run today is indistinguishable from a plain
one, which is the vacuous-check shape this repo has been bitten by elsewhere.

Selection interacts with the parallelism and multi-invocation constraints
below: `-m integration` alone still cannot be combined with the default run,
because a unit and an integration subclass of the same compliance suite in
one pytest process trips hypothesis's executor check. See *Ordering* for
both cases, and
`docs/how-to/run-integration-and-mutation-suites.md` for the runbook.

## Async

- `asyncio_mode = "auto"` — test functions may be `async def` without a
  decorator; no `@pytest.mark.asyncio` needed.
- `asyncio_default_fixture_loop_scope = "function"` — each test gets a fresh
  event loop. Do not rely on loop-scoped state surviving between tests.

## Ordering

`pytest-randomly` randomises test order and `pytest-xdist` runs tests in
parallel. **An order-dependent test is a bug in the test, not a reason to pin
the seed.** Shared mutable module state, a fixture that writes to a fixed
path, and a test that depends on another having run first will all surface
here — fix the cause.

That rule stands for everything the commit gate runs, and it is the rule
`tests/unit/` is written to. There are exactly **two documented exceptions**,
and both are properties of a *shared external backend* rather than of a test:
a real database has one namespace no matter how many workers address it, and
hypothesis attaches per-test state to a function object shared by two
subclasses. Neither is fixable by making a test order-independent, which is
why they are exceptions rather than counter-examples.

Both are constraints on how you *invoke* pytest for the integration tree, and
each has a subsection below:

| Constraint | Applies to | Symptom if ignored |
|---|---|---|
| run serially, no `-n auto` | `tests/integration/graph/` | 36 failures, measured (**B10f**) |
| unit and integration of one port need two invocations | either compliance suite | 21 graph / 13 vector `FailedHealthCheck` (**B10m**) |

Two things to hold onto before the detail. First, **neither touches the commit
gate**: `addopts` deselects `integration`, so the hook's parallel run never
reaches these tests, and nothing here is a reason to drop `-n auto` or pin a
seed for `tests/unit/`. Second, **both are traps rather than nuisances** —
each produces a large number of failures that name infrastructure or
hypothesis and read as flakiness, so the natural response is a retry rather
than an investigation. Both are filed for that reason.

The general rule survives intact: when you meet an ordering or parallelism
failure that is *not* one of these two, it is a bug in the test. Do not add a
third exception without measuring it and writing it down here.

### The integration suite runs serially

**Do not pass `-n auto` to a run that includes `tests/integration/graph/`.**

```bash
uv run pytest -m integration          # correct
uv run pytest -m integration -n auto  # 36 failures, none about the code
```

The cause is one function.
`tests/integration/graph/test_neo4j_store.py::_wipe` runs

```python
await session.run("MATCH (n) DETACH DELETE n")
```

and both `TestNeo4jStore.new_store` and `TestNeo4jSpecifics.store` call it, so
every test — and, because `new_store()` is called once per generated example,
every hypothesis example — empties the database first. There is exactly one
Neo4j database behind the whole suite, so under `pytest-xdist` each worker
deletes the other workers' data *mid-test*. **36 failures, measured rather
than predicted** (**B10f**).

The wipe is where it is on purpose: "delete everything regardless of tenant"
is a test affordance and the port deliberately does not offer one —
`delete_by_tenant` is its bulk removal. And it cannot simply be scoped to a
per-test tenant, because that is precisely what
`test_delete_by_tenant_removes_exactly_that_tenant` asserts about. The three
real fixes B10f names, in increasing cost, are a per-test tenant (weakens that
test), a database per worker (Neo4j community allows one database, so this
needs Enterprise or a container per worker), or `xdist_group` on the module so
one worker owns it — probably the right answer, and none of them free.

**pgvector has the same shape and is parallel-safe anyway**, which is the part
worth copying. `tests/integration/vector/test_pgvector_store.py` resets its
table between tests too; it avoids the collision by not sharing the table:

```python
TABLE = f"kg_vectors_test_{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"
```

`kg_vectors_test_main` outside xdist, `kg_vectors_test_gw0` and friends under
it. Each worker truncates only its own rows. That is one level cheaper than
`xdist_group` and strictly better — the tests stay parallel instead of being
serialised onto one worker — and it is available here and not for Neo4j only
because Postgres allows as many tables as you like while Neo4j community
allows one database. Writing that suite the natural way, against a shared
`kg_vectors`, would have reproduced B10f exactly.

Three things follow:

- **The commit gate is unaffected.** `addopts` deselects `integration` before
  xdist ever sees these tests, so nothing here is a reason to drop `-n auto`
  from the hook or from `tests/unit/`.
- **This is a direct trap for B10a** — getting the Neo4j adapter into a
  mutation or coverage run. The obvious implementation is `pytest -n auto`
  over both suites, and it will fail 36 times for a reason that reads as
  flakiness and invites a retry.
- **Reach for `KG_COMPLIANCE_MAX_EXAMPLES`, not for xdist**, when the graph
  suite is too slow. Lowering the example count is the supported lever; see
  the compliance-suite section above and
  `docs/how-to/run-integration-and-mutation-suites.md`.

A new backend-backed suite inherits this decision. If its reset touches state
another worker can see, either name that state per worker the way pgvector
does or state plainly that the module is serial — and measure it, rather than
assuming either way.

### Two adapters of one compliance suite need two invocations

**A unit and an integration subclass of the same compliance suite cannot share
one pytest process.** Measured, on both suites:

```
pytest -m "not accuracy" tests/unit/graph  tests/integration/graph   -> 21 failed
pytest -m "not accuracy" tests/unit/vector tests/integration/vector  -> 13 failed
```

Every one of the 34 failures is the same error:

```
hypothesis.errors.FailedHealthCheck: The method
GraphStoreCompliance.test_… was called from multiple different executors
```

Nothing is wrong with the code, the adapters, or the tests. Hypothesis attaches
its per-test state to the **function object**, and the `@given` methods live on
the shared base class rather than on the subclasses — so `TestMemoryStore` and
`TestNeo4jStore` (or `TestMemoryVectorStore` and `TestPgVectorStore`) are two
*executors* of one function, which is what that health check exists to report.
The mechanism that makes the compliance suites worth having — one function body
running against every adapter — is the mechanism that trips it.

Running either suite alone is fine, which is why this is invisible day to day:
`addopts` deselects `integration`, so the commit gate ever sees only the
in-memory subclass, and an explicit `-m integration` sees only the real one.
The failure appears the first time someone widens the selection to cover both,
which is a thing people do on purpose when they want one number out of both.

**Combine two runs with `coverage combine`, not one widened `-m`.**
`[tool.coverage.run] parallel = true` is already set, so each invocation writes
its own data file and they merge:

```bash
uv run coverage erase
uv run coverage run -m pytest                     # unit — the default selection
uv run coverage run -m pytest -m integration      # integration — serial, no -n auto
uv run coverage combine
uv run coverage report
```

The second invocation is also the one bound by the serial constraint above, so
the two exceptions compound: the combined run is two invocations, and one of
them may not be parallelised.

This is tracked as **B10m**, and it is filed rather than merely noted because
it is a **direct trap for B10a** — getting the Cypher-executing half of the
Neo4j adapter into a coverage or mutation number. That target is naturally
written as a single invocation over both trees, and it fails 34 times with an
error naming hypothesis, which reads as flakiness and invites a retry or a
suppression rather than an investigation.

Two fixes exist for a single invocation, and neither is currently taken:

- `suppress_health_check=[HealthCheck.differing_executors]` on both suites'
  shared `settings()`. Cheap, and it turns off a check that catches a real
  class of bug elsewhere — the same reasoning that applies to suppressing
  `function_scoped_fixture` applies here: suppressing a health check needs
  proof the condition cannot bite, not an argument that it probably will not.
- Stop sharing the function object, by generating the property tests per
  subclass in `__init_subclass__`. Correct, and a considerable amount of
  machinery for a problem only the CI target has.

So the rule for now: **one compliance suite, one adapter, one invocation.** If
you need both, run both and combine the results. See
`docs/how-to/run-integration-and-mutation-suites.md` for the runbook and
`docs/reference/quality-gates.md` for what the commit hook does instead.

## Property-based tests

`hypothesis` is available. Prefer it wherever a property is easier to state
than a table of examples — round-trips, invariants, normalisation, parsers,
merge/consolidation logic.

### Pin boundary values with `@example`

**A property test is a sampler, not a proof about a specific value.** Where a
guard names a value — `0`, `1`, empty, a maximum, either side of a bound —
write that value as an `@example` alongside the `@given`:

```python
@given(st.floats(allow_nan=False, allow_infinity=False).filter(lambda f: f < 0.0 or f > 1.0))
@example(confidence=-1e-9)
@example(confidence=1.0 + 1e-9)
@example(confidence=1.5)
@example(confidence=2.0)
def test_confidence_out_of_range_rejected(confidence): ...
```

Those four came from a real survivor: `st.floats().filter(...)` reaches the far
extremes readily and the immediate neighbourhood of `1.0` rarely, so a mutant
widening `Entity.confidence`'s bound to `<= 2.0` survived the property
entirely. The strategy *can* draw the killing value; on the runs that mattered
it did not.

**Here this is not merely good practice — the budget is environment-tunable, so
sampler coverage is non-deterministic by construction.** Both store compliance
suites read `max_examples` from `KG_COMPLIANCE_MAX_EXAMPLES` (default 50) at
import, and mutation runs lower it deliberately. Two
`InMemoryVectorStore.search` mutants — `k < 0` widened to `k <= 0` and to
`k < 1`, both making a legal `k = 0` raise — were *killed on one cosmic-ray run
and survived the next with nothing in the adapter changed between them*,
because `k = 0` was reachable only through a property drawing `k` from `0..12`.
The fix is `test_k_zero_returns_nothing_rather_than_raising` in
`tests/compliance/vector_store.py`, an example-based test that also asserts
`k=1` returns one row — so it distinguishes "asked for nothing" from "nothing
there" rather than passing on an empty store.

This failure shape is worse than the ones in
`.claude/rules/recurring-defects.md`, and worth recognising on sight: the input
*does* distinguish the implementations when it is drawn, so the code is not
under-tested in any way reading it reveals, and the same suite against the same
source gives different mutation results run to run. The natural reading of a
survivor that used to die — "something changed in the source" — is wrong.
**When a survivor's status changes without a source change, suspect a sampled
boundary before suspecting the code.**

Three further rules this repo has paid to learn:

- **State `max_examples` explicitly when a number matters.** Two runs at
  different budgets are not comparable, and the budget does not appear in
  pytest's output. Suites that need a bigger one say so locally —
  `tests/unit/domain/test_interval.py` uses 200–500, and
  `tests/unit/domain/test_temporal_parsing.py` 300 — rather than relying on
  the default holding.
- **Do not suppress `HealthCheck.function_scoped_fixture`.** One fixture serves
  every example of a `@given`, so example 7 sees what examples 1–6 left behind;
  that health check is hypothesis reporting the bug, and slice 5b spent a
  one-run-in-three `MissingEntityError` on a suppression added with a confident
  comment. Build the rig inside the test — which is exactly why the compliance
  suites take `new_store()` and call it per example instead of taking a
  `store` fixture.
- **Break the implementation on purpose and watch the property fail before
  trusting it.** A property whose two sides both run the code under test checks
  determinism, not correctness: slice 5b's three replay-equivalence properties
  all passed against a handler that wrote no relationships at all. A property
  that stays green under a deliberate defect is worse than none, because its
  existence is what stops anyone writing the test that would have worked.

Strategies for the domain types live in `tests/compliance/strategies.py` and
are reusable from unit tests. Two of its choices are load-bearing rather than
stylistic and should be preserved when extending it: `property_dicts` is
*recursive*, because a shallow-copying adapter passes a flat-dict mutation test
and fails a nested one; and identifiers are drawn rather than fixed, because
tenant isolation must hold for any pair of distinct tenants and not two
hand-picked UUIDs.

## Fixtures

- Subdirectory-specific fixtures go in a local `conftest.py`. There are two:
  `tests/unit/projections/conftest.py` and
  `tests/unit/consolidation/conftest.py`.
- **`tests/conftest.py` defines no fixtures at all**, and adding one there is
  a decision rather than a default. It holds two pytest hooks —
  `pytest_deselected` and `pytest_terminal_summary` — that report how many
  `integration` and `accuracy` tests the `addopts` marker expression removed
  and print the command that runs each. Nothing is skipped at collection by
  this file. It exists because pytest reports "N deselected" as a bare number:
  slice 4 landed a Neo4j `GraphStore` whose tests are all `integration`-marked,
  and a cosmic-ray mutant left in its source passed the full default suite
  because not one line of it ran. A root fixture is visible to every tree
  including `tests/integration/`, so put shared state where it is used.

Three conventions the two local `conftest.py` files establish, each
load-bearing:

- **Fixtures build real objects, not mocks.** The projections conftest wires
  `InMemoryEventStore`, `InMemoryGraphStore`, `InMemoryVectorStore`,
  `InMemoryCheckpointRepository` and `InMemoryDLQRepository` into a `Rig`, and
  mocks none of them. A mocked store cannot fail a replay-equivalence test,
  which would make the test worthless — and the coverage-ratchet section
  records the other half of this: a router whose 826-line test file supplied
  every input as a `MagicMock` scored high while proving nothing.
- **Prefer a plain builder function to a fixture where the test needs to vary
  the value.** `tests/unit/consolidation/conftest.py` exports `entity(...)` and
  `edge(...)` — module-level functions with keyword overrides, no
  `@pytest.fixture` anywhere in the file — because consolidation tests need
  several entities differing in one field, which a fixture expresses badly. Use
  a fixture for *setup that is the same every time* and a builder for *input
  the test is choosing*.
- **A fixture that hands back mutable state must hand back a fresh one.**
  `rig()` calls `fresh_rig()` per test rather than sharing one; under
  `pytest-randomly` a shared rig is an order-dependent failure, and under
  `@given` it is worse — see below.

**Do not reach for a fixture inside a `@given` test.** One function-scoped
fixture serves every example of a property, so example 7 sees what examples
1–6 left behind, and `HealthCheck.function_scoped_fixture` is hypothesis
reporting exactly that. This is why the compliance suites take an
adapter-supplied `new_store()` and call it per example instead of accepting a
`store` fixture. Build the rig inside the test.

Two shapes from `.claude/rules/recurring-defects.md` are fixture defects
specifically, and both are invisible in review:

- **A builder that passes every field never executes the type's defaults.**
  `entity()` above fills `id`, `tenant_id`, `source_id`, `name`,
  `normalized_name`, `entity_type`, `extraction_method` and `confidence`, so
  no test going through it can observe a wrong default on `Entity` — while the
  type's own signature openly invites direct construction. Construct the type
  directly in at least one test per public type.
- **Setup that runs against state a previous test left behind is unverified.**
  At least one test per stateful setup path must start from genuinely nothing,
  or the setup can be replaced with an empty iterable and nothing notices.

## Writing assertions

Write the assertion from the **documented contract**, before running the
code. A test written from observed output encodes the current behaviour as
the spec — including the bug. See `.claude/rules/recurring-defects.md` §4.

For a regression test, prove it red against the pre-fix source with
`git checkout HEAD~1 -- <paths>`, not `git stash`.

## Running

Do **not** run pytest as a separate step before committing — the pre-commit
hook runs the suite under `pytest-xdist` with the coverage ratchet. Run it
directly only when iterating on a specific failure, or when you deliberately
want a suite `addopts` excludes.

Iterating on one file:

```bash
uv run pytest tests/unit/extraction/test_schemas.py -x          # stop on first failure
uv run pytest tests/unit/extraction/test_schemas.py::test_name  # one test
```

The excluded suite is `integration`, and it is a separate invocation with no
`-n auto`:

```bash
docker compose -f docker-compose.test.yml up -d   # Neo4j on 7688, pgvector on 5434
uv run pytest -m integration
```

Three constraints, each argued above rather than here:

- **Serial.** `tests/integration/graph/` wipes one shared Neo4j database per
  test; `-n auto` gives 36 failures that read as flakiness (**B10f**). Use
  `KG_COMPLIANCE_MAX_EXAMPLES=10` when it is too slow, not xdist.
- **Separate.** Do not widen the selection to cover the unit and integration
  subclasses of one compliance suite in a single process — 34
  `FailedHealthCheck: called from multiple different executors` (**B10m**).
  `[tool.coverage.run] parallel = true` is set, so combine two runs instead:
  `coverage run -m pytest`, `coverage run -m pytest -m integration`,
  `coverage combine`.
- **The `llm/` subset needs a live model**, pointed at with `KG_LLM_BASE_URL`
  and `KG_LLM_MODEL`; the rest needs only the containers.

There is no accuracy suite to run. `-m accuracy tests/accuracy/` collects zero
tests — see `tests/accuracy/` above and **B12**.

See `docs/how-to/run-integration-and-mutation-suites.md` for the full runbook,
including mutation testing, and `docs/reference/quality-gates.md` for what the
commit hook does on your behalf.

## Coverage

`scripts/coverage_ratchet.py` compares total coverage against
`.coverage-baseline`; coverage may never fall. A deliberate drop means
editing `.coverage-baseline` in the same commit and justifying it in the
message. Deleting or weakening a test to get past the gate is a deferral —
it goes in `BACKLOG.md` in that same commit, naming the test and what it was
protecting.
