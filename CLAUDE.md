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

## Testing notes

- `pytest-randomly` randomises test order. Order-dependent tests are bugs; fix
  the test, do not pin the seed.
- `hypothesis` is available for property-based tests — prefer it wherever a
  property is easier to state than a table of examples.
- Markers: `unit`, `integration`, `accuracy`.
