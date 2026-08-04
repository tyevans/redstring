# kg-builder

Knowledge graph construction: entity extraction, preprocessing, scraping,
consolidation, and Neo4j sync.

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
uv sync --extra dev && uv run pre-commit install
```

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
extraction : graph : preprocessing : scraping : schemas   (independent siblings)
inference
models
events : db : cache : encryption : config : context
```

Lower layers must not import higher ones, and the sibling layers must not import
each other. Adding a cross-layer import means either the code is in the wrong
layer or the contract needs an explicit, argued change.

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
