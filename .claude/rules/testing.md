---
paths:
  - "tests/**/*.py"
---

# Testing Conventions

## Structure

- `tests/unit/` — no external dependencies, fast, always run. Mirrors the
  package layout (`tests/unit/extraction/`, `tests/unit/graph/`, ...).
- `tests/accuracy/` — extraction quality measured against a live LLM. Slow,
  costs money, non-deterministic. Excluded from the default run.

## Markers

Declared in `pyproject.toml`:

- `unit` — unit tests
- `integration` — integration tests
- `accuracy` — extraction accuracy tests
- `slow` — tests that take a long time to run

`addopts = ["-m", "not accuracy"]` excludes the accuracy suite from the
default run, and therefore from the commit gate. A CLI `-m` overrides it.

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

## Property-based tests

`hypothesis` is available. Prefer it wherever a property is easier to state
than a table of examples — round-trips, invariants, normalisation, parsers,
merge/consolidation logic.

## Fixtures

- Shared fixtures in `tests/conftest.py`.
- Subdirectory-specific fixtures in a local `conftest.py`.

## Writing assertions

Write the assertion from the **documented contract**, before running the
code. A test written from observed output encodes the current behaviour as
the spec — including the bug. See `.claude/rules/recurring-defects.md` §4.

For a regression test, prove it red against the pre-fix source with
`git checkout HEAD~1 -- <paths>`, not `git stash`.

## Running

Do **not** run pytest as a separate step before committing — the pre-commit
hook runs the suite under `pytest-xdist` with the coverage ratchet. Run it
directly only when iterating on a specific failure:

```bash
uv run pytest tests/unit/extraction/test_schemas.py -x   # one file, stop on first failure
uv run pytest -m accuracy tests/accuracy/                # the excluded suite, deliberately
```

## Coverage

`scripts/coverage_ratchet.py` compares total coverage against
`.coverage-baseline`; coverage may never fall. A deliberate drop means
editing `.coverage-baseline` in the same commit and justifying it in the
message. Deleting or weakening a test to get past the gate is a deferral —
it goes in `BACKLOG.md` in that same commit, naming the test and what it was
protecting.
