# redstring

Knowledge graph construction: entity extraction, consolidation, and the
graph/vector stores those project into.

The library **never fetches content** — document sourcing is a different
problem set — and extraction **writes to no store**: it emits events, and
projections do the writing.

## Rules

Conventions and workflow rules live in `.claude/rules/`:

- `recurring-defects.md` — **read before writing or reviewing code.** Six
  defect shapes, with a quick checklist.
- `definition-of-done.md` — what "done" means per kind of work.
- `testing.md`, `commits.md`, `user-trust.md`.

## Environment

`uv` manages everything. Never edit `pyproject.toml` dependency tables by hand —
use `uv add`, `uv add --optional <extra>`, `uv remove`.

Run anything project-scoped through `uv run`.

**`uv add` and `uv remove` re-sync, and can silently narrow the installed
extras back to `dev`.** Re-sync with `--all-extras` after any dependency
change. This one behaviour is the root cause of both incidents recorded further
down this file: slice 7's phantom "0 survivors out of 426" mutation run, and
slice 9's 47 phantom mypy errors in files nobody had touched. Neither presented
as a packaging problem — one looked like an outstanding test suite and the
other like a type regression.

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
`neo4j` and `llm` are separate, and a venv without them fails *collection* on
the modules that import them rather than skipping them. Slice 7 lost a
cosmic-ray run to a near-miss of this: a worktree synced with `--extra dev`
reported 0 survivors out of 426 mutants, every one "killed" by an import
error.

## Mutation testing

Run on demand, not on commit — both are slow.

```
uv run python scripts/mutation.py mutmut            # preferred: guards the baseline
uv run python scripts/mutation.py cosmic-ray

uv run mutmut run                                   # config in pyproject.toml
uv run cosmic-ray init cosmic-ray.toml session.sqlite
uv run cosmic-ray exec cosmic-ray.toml session.sqlite
uv run cr-report session.sqlite
```

Both are kept because mutmut 3.x will not mutate decorated functions and
cosmic-ray will. `scripts/mutation.py` wraps both: it builds a detached
worktree under `.mutation/worktree`, syncs it `--all-extras`, runs that tool's
own configured test command *there*, and **refuses to start unless the result
is green with a positive pass count**. Use it rather than the raw commands —
the paragraphs below explain what it is protecting you from, and it has both
of this file's mutation incidents encoded in one refusal.

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

**`scripts/mutation.py` is that check, and it refuses rather than warns.** Note
what it insists on beyond a zero exit code: a *positive pass count*. "0 failed"
and "0 collected" are the same exit status, and the incident above is the
second one — so a wrapper checking only `returncode == 0` would have let slice
7's run through and read as a control while being none.

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
  testing the difference.** This project has hit the same shape eighteen times,
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
  | an expectation written in terms of the constant under test (`start + INSTANT`) | *any* value of that constant, including one that makes the interval empty |
  | asserting only the *precision* of a parsed date, never where it lands | every wrong arithmetic — "Q3" moving to January, April or August all keep MONTH precision |
  | intervals whose bounds never *coincide* | direction derived from a sort — the shorter of two extents sharing a lower bound sorts first, and its edge is silently dropped |
  | only the **19th** century, whose base 1800 shares no set bit with 1, 33, 34, 66, 67 or 100 | `+` written as `\|` or `^` — the same number at that base, and eleven mutants unkillable until a case used the 20th |
  | a *month range* whose endpoints differ | `end < start` widened to `<=` — "1900-1900" becomes unparseable and every other range still works |

  The interval row was the campaign's only Critical, and its lesson is
  structural rather than about inputs: **an invariant that holds because of an
  argument about sort order is not enforced, it is inferred.** Direction was
  derived from `order_key`, so the module's stated invariant ("`DURING` never
  appears") was true only for inputs where the sort happened to put the
  container first. Canonicalising from the computed relation instead makes it
  true by construction. When the fix landed, the same reasoning turned up a
  second time in the same file — a map entry that no test could reach, whose
  justification was also an argument about the sort.

  The corollary is a habit worth having: **when you fix something that rested
  on an incidental property, grep for the second instance before closing.**
  It was there both times this project looked.

  The two rows before it are one lesson from different angles: **an assertion
  has to be
  independent of the thing it checks, and it has to check every claim the code
  makes.** Writing the expectation as `start + INSTANT` makes the test true by
  construction for any `INSTANT`, zero included. And a parsed date is two
  separate claims — the value and the precision — so a suite that asserts one
  of them leaves the other to nine surviving mutants. Write the literal you
  expect, and assert every claim the function makes rather than the one that
  was convenient.

  **The century row is the one to read if you think you know this list.**
  It is not an interned string or a cached small int — it is a *bit pattern*
  in the most natural example anyone would pick. `(century - 1) * 100` for the
  19th century is 1800, and 1800 shares no set bit with any constant in the
  table beside it, so `base + k`, `base | k` and `base ^ k` are the same
  number and `century - 1` equals `century ^ 1` for any odd century. A library
  that reads historical text will reach for the 19th century every time. The
  lesson generalises past bits: **when a test's example is the one the domain
  makes obvious, ask what that example is quietly making true.**

  **Four of the eighteen rows are identity-vs-equality**, and they are the
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

## Coverage ratchet

`scripts/coverage_ratchet.py` runs the suite and compares total coverage against
`.coverage-baseline`. Coverage may never fall; when it rises, the baseline is
raised and staged automatically. The baseline file is created on the first run
where the suite is green.

To accept a deliberate drop, edit `.coverage-baseline` in the same commit and say
why in the message.

Deleting well-covered legacy code lowers the ratio while removing nothing that
was tested — slices 7, 8 and 9 all hit this. Justify the movement in the commit
message rather than adding tests to paper over it. Slice 9's two drops were
declarative ORM columns (which execute at import, so they scored high while
proving nothing) and a router whose 826-line test file supplied every input as
a `MagicMock`.

## An exemption list must fail when what it exempts is gone

**A per-file lint or type exemption that matches no file passes silently.**
ruff and mypy both accept a pattern for a deleted path without complaint, so a
shrinking-exemption ratchet stops shrinking and nobody is told.

Slice 9 ran the experiment by accident and got both answers at once. Deleting
`models/extracted_entity.py` **failed the gate** — because one exemption list
lived in a test that asserts every entry still names a real file. The ruff and
mypy ignore lists in `pyproject.toml` carried three deleted paths for one and
two commits, and nothing said a word. Same hazard, same commit, opposite
outcomes, decided entirely by whether someone had written the staleness check
as a test.

**Both legacy exemption lists are now empty, and the empty one is deleted.**
As of slice 10, ruff's `per-file-ignores` carries no legacy entry and mypy
carries no `exclude` at all — `--strict` covers every module in the package.
The mypy key was deleted rather than kept empty, per the paragraph below: an
exclusion over an empty set excludes nothing, and a staleness guard written
over it would pass vacuously. Re-adding either is now a visible decision in
review rather than an edit to an existing list.

So: **every exemption list needs a test that its entries still match something.**
And when a list empties, decide deliberately between two different things —
keeping it empty (no exceptions admitted, and adding one is visible in review)
or deleting it. An *exclusion* over an empty set is the one to delete: it
excludes nothing, and any guard written over it passes vacuously.

The same reasoning applies to a check that finds nothing. `exhaustive = true`
on the import contract caught zero violations, which is indistinguishable from
the option being inert — slice 9 proved it bites by adding a throwaway package,
watching the contract break, and removing it. **A passing check you have never
seen fail is not yet evidence.**

### The command that measures an exemption must not be subject to it

Before deleting an exemption you have to know what it hides, and the obvious
way to find out is silently wrong:

```
$ uv run ruff check --select ANN,TC src/redstring/events/
All checks passed!
```

That result was **unconditional**. `per-file-ignores` applies on top of
`--select`, and the ignore for that path was exactly `["ANN", "TC"]`, so the
command could not have reported a finding whatever the code said. Deleting the
entry for real surfaced ten. Same for mypy: naming files explicitly on the
command line bypasses `exclude`, so it answers a different question than the
configured run does.

**Delete the entry and run the configured gate.** That is the only measurement
that means anything — it is the failure-shape rule applied to a lint
invocation rather than a test, and the two spellings of "prove it can fail"
are the same instruction.

What the ten findings turned out to be is its own lesson. Nine were false
positives from a *misconfiguration*, not from strictness: ruff's
`runtime-evaluated-base-classes` exists to stop TC00x moving pydantic field
annotations into a type-checking block, but ruff matches the base class **as
written in the file**, not through the MRO — and every event here declares
`TenantDomainEvent`, not `pydantic.BaseModel`. Applying ruff's own suggested
fix left the module importable and broke 23 tests with
`PydanticUserError: not fully defined`, because pydantic resolves field
annotations at schema-build time. **An import smoke test passes; only using the
model catches it.** The fix was to name the real base in that setting.

So an exemption can hide a misconfiguration rather than debt, and then it
absorbs that misconfiguration indefinitely — which is a better argument for
removing exemptions promptly than any amount of accumulated strictness debt.

## The public API is gated, not curated

`redstring.__all__` is the whole promise; anything reached by a dotted path is
internal. Three tests keep that honest, and each exists because the other two
cannot see its failure:

1. **Every exported name's signature mentions only exported types.** Ruff's
   F822 catches unresolvable `__all__` entries and is blind to this.
2. **Every `RedstringError` subclass is exported or listed** against the
   capability whose export would bring it. A *signature* gate cannot see
   exceptions — removing `MissingEntityError` from `__all__` passes check 1.
3. **The end-to-end example imports nothing but `redstring`.** Without it the
   example can reach into an adapter module and pass while the surface is
   empty.

Two things learned building check 1, both of which will recur:

- **It must walk the MRO.** `GraphProjection` declares no `__init__`, so a
  body-only check reported it clean while the constructor a caller actually
  calls — `StoreProjection`'s — took five foreign types.
- **Exporting one name pulls its closure.** `Entity` obliges `TemporalExtent`,
  which obliges `DatePrecision`. Exporting `DomainSchema` alone would have
  satisfied the letter of the finding and left it unconstructible. Expect the
  next capability exported to bring its own closure; the gate makes that
  visible at the moment it happens, which is the point of it.

## Architecture contract

`lint-imports` enforces a layered contract declared in `pyproject.toml`, highest
to lowest:

```
composition
extraction : consolidation : temporal : graph : vector : llm   (siblings)
projections
aggregates
events
ports
domain
```

**`composition` is the top layer and holds one module.** `extraction` may not
import `projections` — that is what keeps a store reference out of the
pipeline — but something has to hold both or the library ships two halves and
a diagram. `build_graph` is that something. A second module wanting in here
should have to say what it composes.

`cache`, `config` and `context` left the line in slice 10 with their modules:
a settings object, a Redis singleton and a re-export shim, none with a caller.

`containers = ["redstring"]` with **`exhaustive = true`**: a new top-level
package is a contract failure until it is placed deliberately. That is the
point — decide where it sits, or argue the contract should change.

**There is no `services` layer, and adding one back needs an argument.** It was
the top layer until slice 9 deleted it: the write model is `aggregates` +
`events`, the read model is `projections`, and persistence is the two ports.
`models`, `db` and `schemas` went with it — there is no ORM and no session for
a layer to be built around.

Lower layers must not import higher ones, and the sibling layers must not import
each other. Adding a cross-layer import means either the code is in the wrong
layer or the contract needs an explicit, argued change.

The sibling band is where the real work happens, and each membership is
load-bearing:

- `llm` sits *beside* `extraction`, not beneath it, so extraction can reach
  only `ports.llm_provider` and never the LangChain adapter.
- `consolidation` is a sibling rather than above extraction. It needs nothing
  from extraction — the tie-break both use moved down to `domain.preference`
  when consolidation became its third caller — and placing it above would let
  it reach `mapping.py`, which is how a second entity-id scheme gets born.
- `temporal` likewise. Above `extraction` it could reach `mapping.py`, and the
  temptation there is specific: inferred edges would acquire a path into
  `DocumentExtracted`, which is exactly the persistence decision
  `temporal/inference.py` argues against.

`pyproject.toml` carries the full reasoning inline. Keep this block in step
with it — a stale layer diagram in binding instructions sends the next author
to a package that does not exist.

**`lint-imports` only sees first-party imports**, so it cannot catch a
`langchain*` or `neo4j` import appearing where it should not. That is what
`tests/unit/test_dependencies_stay_confined.py` is for — it parses every module
under `src/` and fails on a third-party leak outside the directory that library
is confined to. It carries a **table**, currently four rows: `langchain*`/
`openai` in `llm/adapters/`, `neo4j` in `graph/adapters/`, `asyncpg` in
`vector/adapters/`, `redis` in `llm/cache/`.

**A new third-party client adds a row, in the same commit.** Three of those
four were confined by convention alone until slice 11, each correctly placed
and each one commit from not being — which is `recurring-defects.md` §3 exactly:
a rule that holds only because nobody has broken it is indistinguishable from
no rule. Every row is guarded in both directions, so a row naming a directory
that has stopped importing its library fails rather than passing forever.
