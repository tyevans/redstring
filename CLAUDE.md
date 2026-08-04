# kg-builder

Knowledge graph construction: entity extraction, consolidation, and the
graph/vector stores those project into.

The library **never fetches content** — document sourcing is a different
problem set — and extraction **writes to no store**: it emits events, and
projections do the writing.

## Environment

`uv` manages everything. Never edit `pyproject.toml` dependency tables by hand —
use `uv add`, `uv add --optional <extra>`, `uv remove`.

Run anything project-scoped through `uv run`.

## Deferred work goes in BACKLOG.md — always

**Anything you notice and do not fix must land in `BACKLOG.md` in the same
commit that passes it by.** No exceptions, and no substitutes: not a TODO
comment, not a note in the PR body, not a line in a commit message, not a
sentence in chat that scrolls away.

This applies to every kind of deferral:

- a bug you found while doing something else
- a test you skipped, quarantined, deselected, or weakened
- a lint rule you ignored, and why ignoring it was correct
- a workaround, stub, or placeholder you left behind
- a decision you deliberately postponed, with the reasoning that made
  postponing it right
- scope you were asked for but could not finish

Write the entry so someone picking it up cold does not have to rediscover
what you already know: name the file and line, say what is actually wrong,
and say what you learned that made you defer rather than fix. An entry that
only says "clean up X" has thrown away the expensive part.

When you fix a backlog item, delete its entry in the same commit.

## Quality gates run on commit — do not run them yourself

Every quality check is wired into `pre-commit` and runs automatically on
`git commit`:

- whitespace / EOF / YAML / TOML / JSON / merge-conflict / large-file checks
- `ruff check --fix` and `ruff format`
- `bandit` (security, `src/` only)
- `lint-imports` (layered architecture contract)
- `pytest` under `pytest-xdist` with the coverage ratchet

**Do not run ruff, bandit, lint-imports, or pytest as separate steps before
committing.** It duplicates work the hook already does and burns time. Write the
change, then commit; the hook reports what is wrong and often fixes it in place
(re-`git add` and commit again when it does).

Prefer many small commits over one large one. Small commits make for happy
reviewers, and they keep each hook run fast.

First-time setup in a fresh clone:

```
uv sync --all-extras && uv run pre-commit install
```

**`--all-extras`, not `--extra dev`.** The `dev` extra holds only the tooling;
`neo4j`, `llm` and `eventsourcing` are separate, and a venv without them fails
*collection* on 17 test modules rather than skipping them. Slice 7 lost a
cosmic-ray run to a near-miss of this: a worktree synced with `--extra dev`
reported 0 survivors out of 426 mutants, every one "killed" by an import
error.

## Coverage ratchet

`scripts/coverage_ratchet.py` runs the suite and compares total coverage against
`.coverage-baseline`. Coverage may never fall; when it rises, the baseline is
raised and staged automatically. The baseline file is created on the first run
where the suite is green.

To accept a deliberate drop, edit `.coverage-baseline` in the same commit and say
why in the message.

## Architecture contract

`lint-imports` enforces a layered contract declared in `pyproject.toml`, highest
to lowest:

```
services
extraction : graph : vector : llm : schemas    (independent siblings)
projections
aggregates
models
events : db : cache : encryption : config : context
ports
domain
```

Lower layers must not import higher ones, and the sibling layers must not import
each other. Adding a cross-layer import means either the code is in the wrong
layer or the contract needs an explicit, argued change.

The sibling band is where the real work happens. `llm` sits *beside*
`extraction` rather than beneath it precisely because siblings may not import
each other: extraction can therefore reach `ports.llm_provider` and never the
LangChain adapter. Put `llm` any lower and the port stops meaning anything.

**`lint-imports` only sees first-party imports**, so it cannot catch a
`langchain*` import appearing where it should not. That is what
`tests/unit/llm/test_port_does_not_leak.py` is for — it parses every module
under `src/` and fails on a third-party leak outside the adapter package.
Any dependency the architecture deliberately confines to one module needs
that second kind of check; the contract alone will not do it.

## Mutation testing

Run on demand, not on commit — both are slow.

```
uv run mutmut run                                   # config in pyproject.toml
uv run cosmic-ray init cosmic-ray.toml session.sqlite
uv run cosmic-ray exec cosmic-ray.toml session.sqlite
uv run cr-report session.sqlite
```

Both are kept because mutmut 3.x will not mutate decorated functions and
cosmic-ray will.

### Never gate on a raw survivor count

**A large fraction of cosmic-ray's survivors are unkillable by construction,
and the proportion grows with how well-annotated the code is.** Measured on
`graph/adapters/memory.py`: 230 mutants, 78 survivors, of which **73 were
equivalent mutants** — 55 of those were annotations alone.

Every module here has `from __future__ import annotations`, so annotations are
strings that are never evaluated. cosmic-ray rewrites the `|` in `X | None` as
each of eleven other binary operators:

```
-    async def get_entity(self, ...) -> Entity | None:
+    async def get_entity(self, ...) -> Entity + None:
```

No test can kill those, in this codebase or any other using PEP 563. A gate on
raw survival percentage would therefore reward *deleting type annotations*.
Other routine equivalents: `if TYPE_CHECKING:` negated, `*,` turned into `/,`,
and comparisons on a value a guard clause has already narrowed (`>=` for `==`
over a three-element `Literal`).

Classify survivors before drawing any conclusion. The bar is **"every survivor
is understood"**, never a number. Group them by diff hunk first — the same
source line usually accounts for a dozen mutants.

### A zero-survivor run is the result most in need of suspicion

**Prove the harness works before believing any mutation result, and disbelieve
a perfect score first.** Slice 7's first run reported **0 survivors out of
426**, and a planner-only run before it reported 0 out of 45. Both were
worthless: the worktree had been synced without a required dependency, so every
mutant "died" on a collection error. `cr-report` showed
`WorkerOutcome.NORMAL, TestOutcome.KILLED` for all 426 — indistinguishable from
an outstanding suite.

This is the other half of "never gate on a raw survivor count". A high survivor
count merely needs classifying; a *zero* usually means the tests never ran.
Before reading a run: execute the configured `test-command` unmutated in the
same environment and require it green. cosmic-ray runs in a separate worktree
or clone (its `local` distributor mutates the working tree in place), and a
worktree is exactly where a missing extra goes unnoticed.

Hand-verifying a mutant has its own trap: CPython validates a `.pyc` on
`(mtime, size)`, so an edit that leaves the file the same size — `1.0` for
`2.0` — can keep stale bytecode loaded and the mutant never runs. Use
`PYTHONDONTWRITEBYTECODE=1`, and `dis.dis` on the loaded function when a
survivor looks impossible.

The survivors worth your attention are the ones where a test passes for an
accidental reason. Two real examples from slice 3, neither findable by
reading the code:

- `==` replaced by `is` on a string filter survived, because the tests queried
  with string *literals* and CPython interns those. A caller passing a
  runtime-built string would have got an empty result.
- `==` replaced by `<=` on a UUID endpoint comparison survived, because the
  test used random `uuid4`s — it would have caught the bug only about half
  the time, depending on how the ids happened to sort.

## Testing notes

- **When a test's input makes two candidate implementations agree, it is not
  testing the difference.** This project has hit the same shape thirteen times,
  and every one passed review while proving nothing:

  | Test used | Wrong implementation it could not distinguish |
  |---|---|
  | string *literals* (CPython interns them) | `is` where `==` was meant — a runtime-built string returns nothing |
  | random `uuid4`s | `<=` where `==` was meant — passes about half the time, depending on how ids sort |
  | a *chain* graph | first-found where shortest-path was meant — on a chain they are the same function |
  | results only, never the query plan | a full scan where an index seek was meant — same answers, catastrophic cost |
  | ids drawn from `uuid4()`, never colliding across tenants | a `(tenant_id, id)` key compared on `id` alone — one tenant's write vouches for another's |
  | a *small* integer (CPython caches -5..256) | `is not` where `!=` was meant — correct at a test dimension of 8, rejects every legitimate write at 768 |
  | a loop body reached with exactly *one* item left | `break` where `continue` was meant — identical on a one-element remainder, discards every later item in real input |
  | objects built only through a factory that passes every field | wrong defaults on the public type — the signature invites direct construction that no test performs |
  | a fixture reusing state a previous run left behind | setup that does nothing — the DDL loop can be replaced by an empty iterable and nothing notices |
  | duplicates built from a bare name, so they are *fully equal objects* | a partial tie-break where a total one was meant — "first wins" and "last wins" agree on equal objects, for any implementation |
  | a collection grouped *after* the deduplication being tested | any invariant about duplicates — every group is a singleton and the assertion cannot fail |
  | ids compared after a *store* handed them back | `is` where `==` was meant — both adapters happen to return the same object, and any adapter that rebuilds the id finds nothing |
  | two `len()` calls on collections under 257 items | `is not` where `!=` was meant — the two calls return the *same* int object, and the check inverts above the cache |

  **Four of the thirteen rows are identity-vs-equality**, and they are the
  ones to expect rather than to be surprised by. Three fired because the test
  value sat inside a CPython cache — interned strings, cached small ints, and
  `len()` on a short collection returning that same cached int. Test numeric
  bounds at a *realistic* magnitude: `nomic-embed-text` is 768, and a
  dimension check written with `is not` passes at 8 and rejects everything
  real.

  The fourth is not a cache at all and is the one to watch for next. Ids that
  come back **through a port** compared with `is` pass because both adapters
  in this repo happen to return the object they were handed — a property no
  port promises. The fix is not a bigger test value but a second adapter that
  behaves differently in the permitted way: a `GraphStore` returning
  equal-but-distinct ids is a real adapter, and a contract two implementations
  satisfy by accident is not a contract.

  When a length comparison is what is being checked, prefer a form with **no
  int comparison in it at all** — `zip(..., strict=True)` over
  `len(a) != len(b)`, and collecting the offending items over counting them.
  Both spellings this project has fixed were fixed that way, and neither can
  regress.

  The one-item-loop row is the chain-graph row in miniature: every test stated
  exactly one relationship, and on a one-element remainder `break` and
  `continue` are the same function. In real input it would have discarded
  every relationship after the first bad one. State a bad row *followed by a
  good one*, in every loop.

  The factory row is the one nothing else catches: if every test builds a type
  through a helper that passes all fields, the type's own defaults are never
  executed, while its signature openly invites direct construction. The
  fixture row is the one reviewers never look for: at least one test per
  stateful setup path must start from genuinely nothing, or the setup is
  unverified no matter how many tests depend on it.

  The last two rows are one shape, and it is the shape that keeps recurring:
  **the input was built by the same function the assertion was about.**
  Deduplicating input built by the deduplicator leaves nothing to deduplicate;
  a tie-break fed objects that are equal in every field cannot be observed at
  all. Both fired in slice 6 *inside tests written specifically to catch
  tie-break defects*, and one of them — a hypothesis property — read as a
  strong test right up until someone tried to break it.

  So: **before trusting a property, break the implementation on purpose and
  watch it fail.** A property that stays green under a deliberate defect is
  not evidence, and it is more dangerous than no property, because its
  existence is what stops anyone writing the test that would have worked.
  The same applies to reading a surviving mutant as "equivalent": in slice 6
  a `>` → `>=` survivor was equivalent only *because* the order was total, so
  the totality had to be asserted before the label was honest.

  **When a key is a tuple, write one test where its components collide.**
  This is narrower than the rule above, and it is the form that actually
  fires in time. The fifth row was written *during a fix round that cited
  this table*, by an implementer who had just read it: the principle was
  understood and the defect shipped anyway, because ids came from `uuid4()`
  the way every other test in the file makes them. Knowing to ask "what else
  would pass this?" did not survive contact with a habit; a rule naming the
  concrete action does. Composite keys in this codebase are almost always
  `(tenant_id, something)`, so the collision to force is almost always the
  same one.

- **A property test is a sampler, not a proof about a specific value. Pin
  boundaries as examples.** Two mutants in `InMemoryVectorStore.search` —
  `k < 0` widened to `k <= 0` and to `k < 1`, both making a legal `k=0`
  raise — were *killed on one cosmic-ray run and survived the next, with
  nothing about the adapter changed between them.* `k=0` was covered only by
  a property drawing `k` from `0..12`, so whether the boundary was tested at
  all depended on the sampler and on `KG_COMPLIANCE_MAX_EXAMPLES`, which
  mutation runs lower to 5.

  This is not the failure shape above — the input does distinguish the
  implementations when it is drawn. It is worse in one way: **coverage of the
  boundary is non-deterministic**, so the same suite against the same code
  gives different mutation results run to run, and the natural reading of a
  survivor that used to die is "something changed in the source." Nothing had.
  Where a guard names a specific value (`0`, `1`, empty, the maximum), write
  that value as an example alongside the property. `hypothesis` has
  `@example` for exactly this.

- **A test whose two sides share the implementation under test cannot
  distinguish it from a weaker one. Round-trip and equivalence properties need
  an independent oracle.** Slice 5b's replay-equivalence suite had all three
  properties the design asked for — wipe and replay, deliver everything twice,
  replay over a live projection — and all three passed against a handler that
  never applied an undo, a handler that never deleted a dropped edge, and a
  handler that never wrote relationships at all. Six handler mutants, three
  survived.

  The reason is structural, not an oversight: both sides of an equivalence run
  the same fold, so a fold that does *too little* leaves both sides agreeing on
  the same wrong state. Self-consistency is preserved exactly by the bugs that
  drop work. The fix was to record, independently of the projection, what the
  graph should hold, and assert against that — all six mutants then died.
  Whenever a test's expected value is produced by the code under test, it is
  checking determinism, not correctness.

- **`hypothesis` runs every example against one function-scoped fixture.** The
  fixture is created once for the whole `@given`, so example 7 sees whatever
  examples 1-6 left behind. This bit slice 5b as an intermittent
  `MissingEntityError` in about one run in three, and
  `suppress_health_check=[HealthCheck.function_scoped_fixture]` — added with a
  confident comment explaining why it was safe — is what hid it. That
  health check is hypothesis telling you about this exact bug; suppressing it
  needs proof the state cannot leak, not an argument that it probably will
  not. Build the rig inside the test instead.

  Before trusting a test, ask what *other* implementation would also pass it.
  If a plausible wrong one would, the input is the problem: pin the values so
  the candidates disagree (one neighbour sorting below the hub and one above,
  a diamond rather than a chain, a runtime-built string rather than a
  literal). These are found by mutation testing and essentially nothing else,
  which is why a surviving mutant in well-tested code deserves investigation
  rather than an "equivalent" label.

- **A new read method needs its mutation-isolation test in the same edit.**
  If a store method hands back objects the caller can mutate, there must be a
  test that mutates the result and asserts a later read is unaffected. This
  was learned expensively: across slice 3, four read methods
  (`find_by_blocking_key`, `neighbors`, `find_by_blocking_keys`,
  `get_relationships_for`) each shipped with full behavioural tests and no
  isolation test, and each time a cosmic-ray mutant — not review, not the
  property tests — found that a shallow copy passed everything. Four
  occurrences is one missing habit, not four mistakes. Behavioural tests do
  not imply this one: returning the live internal object is *correct* on
  every read and wrong only afterwards, so no assertion about the returned
  value can see the defect.

  For `GraphStore` this is enforced, not merely advised:
  `tests/unit/graph/test_compliance_coverage.py` derives the read-method list
  from the Protocol by introspection and fails if any lacks a registered
  isolation test and tenant-isolation test. **Give every store port the same
  gate** — a written rule is what failed the first four times.
- **Bound any loop whose exit depends on adapter-supplied data.** A cursor
  that fails to advance turns a `while True` pagination test into a hang. A
  test that hangs is worse than one that fails: in CI it reads as
  infrastructure trouble and gets retried rather than investigated. Bound the
  loop and fail with a message naming the cause.
- `pytest-randomly` randomises test order. Order-dependent tests are bugs; fix
  the test, do not pin the seed.
- `hypothesis` is available for property-based tests — prefer it wherever a
  property is easier to state than a table of examples.
- Markers: `unit`, `integration`, `accuracy`.
