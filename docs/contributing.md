# Contributing

## Setup

```bash
git clone https://github.com/tyevans/redstring
cd redstring
uv sync --all-extras
uv run pre-commit install
```

**`--all-extras`, not `--extra dev`.** The `dev` extra holds only the tooling;
`neo4j` and `llm` are separate, and a venv without them fails *collection* on
the modules that import them rather than skipping those tests. This has cost
this project two debugging sessions that presented as something else — a
mutation run reporting "0 survivors out of 426" (every mutant killed by an
import error, indistinguishable from an outstanding suite) and 47 phantom
mypy errors in files nobody had touched.

`uv add` and `uv remove` re-resolve and can silently narrow the venv back to
`dev`, so **re-sync with `--all-extras` after any dependency change**. Never
edit `pyproject.toml`'s dependency tables by hand.

## Quality gates run on commit — pytest does not

Every check — ruff, `mypy --strict`, bandit, and the layered import contract —
is wired into `pre-commit` and runs on `git commit`.

**Do not run those yourself first.** It duplicates work the hook already does,
and the hook often fixes the problem in place (re-`git add` and commit again
when it does). [Quality gates](reference/quality-gates.md) lists what each one
checks.

**pytest is the exception.** No hook runs the suite any more — it used to,
through a `pytest-coverage-ratchet` hook that duplicated CI's `pytest` job on
every commit, and was removed for exactly that redundancy. CI's `pytest` job
now carries the coverage floor that hook enforced, via `--cov-fail-under`
against `.coverage-baseline`. Run `uv run pytest` yourself before committing,
since nothing else will before CI does — and run
`uv run python scripts/coverage_ratchet.py` instead when the change might
raise coverage, so the new baseline lands in the same commit (see
[Quality gates](reference/quality-gates.md) for why CI cannot do that part).

Prefer many small commits over one large one. Each commit runs the lint, type,
security and import gate, so small commits keep each run fast and keep the
failure surface legible.

## Commit messages

Commits follow the **Conventional Commits** specification (`type(scope): description`) with structured SpecOps RFC-822 trailers for full traceability:

```text
feat(extraction): add stream parsing support

SpecOps-Task: TASK-0042
SpecOps-Story: US-0005
```

Enforced by `uv run spec-ops security check-trailers` and pre-commit hooks (governed by ADR-0010 and ADR-0012).

## SpecOps PMaC Backlog Management

Development and backlog progression are managed through **SpecOps** (Project Management as Code):

- **Tasks live in `docs/project/backlog/`** across three states: `proposed/`, `refined/`, and `complete/`.
- **Propose new work** using `uv run spec-ops task create` or by authoring a task markdown file in `docs/project/backlog/proposed/`. Never leave untracked TODO comments in code.
- **Priority Queue**: `docs/project/backlog/PRIORITY.md` maintains a strict, sequential priority queue synchronized automatically with disk state.
- **Historical Backlog**: Historical pre-SpecOps backlog items (B1–B170) remain archived in `BACKLOG.md` for lineage traceability.

## Worktree Isolation & Lifecycle

To prevent cross-agent interference and keep feature branches isolated (ADR-0005):

1. **Spawn a worktree**: Always work inside a dedicated worktree created via:
   ```bash
   uv run spec-ops worktree start <TASK_ID>
   ```
2. **Backlog Immutability**: Feature branches must **never** modify files in `docs/project/backlog/` directly. Backlog transitions occur upon integration via `uv run spec-ops queue complete <TASK_ID>` on dedicated `chore/backlog-*` branches.
3. **Hard Invariants**:
   - **File length limit (<500 lines)**: Strictly enforced by `uv run spec-ops health` (warns proactively at >=400 lines; ADR-0002).
   - **Blackbox frontdoors**: Tests must exercise public interfaces without private internal mock backdoors (ADR-0003).
   - **Lockfile immutability**: Never modify `uv.lock` by hand (ADR-0011).

## Testing

Four trees, and they are not interchangeable:

| Tree | In the default suite? | Needs |
|---|---|---|
| `tests/unit/` | yes | nothing external |
| `src/redstring/testing/` | **never collected directly** | subclassed by unit and integration modules |
| `tests/integration/` | no | backends from `docker-compose.test.yml` |
| `tests/accuracy/` | no | it is empty — see below |

"Default suite" means `uv run pytest` with no marker named — the selection CI
runs and enforces a coverage floor against, not the commit gate, which no
longer runs the suite at all.

`src/redstring/testing/` is the one to understand first. It is a *library*, not a
suite: the contract classes become tests only where an adapter's own module
subclasses them under a `Test*` name. **A regression on a shared contract goes
there**, not into one adapter's test file — fixed in `test_memory_store.py` it
is fixed for one adapter; added to the compliance module it is enforced
against every adapter that exists now and every one added later.

`tests/accuracy/` is empty and the `accuracy` marker names nothing. That is a
known gap (`BACKLOG.md` B12), not a suite you are failing to run: **no claim
about this library's extraction quality is backed by anything in this repo.**
Correct and accurate are different properties, and extraction can satisfy
every invariant while finding entities that are simply wrong.

See [Run the integration and mutation suites](how-to/run-integration-and-mutation-suites.md)
for the deliberate, non-default runs.

### Before you trust a test

The single most useful habit here: **ask what *other* implementation would
also pass this test.** If a plausible wrong one would, the input is the
problem, not the assertion.

`CLAUDE.md` carries a sixteen-row table of the shapes this project has
actually hit — a test using string *literals* hid `is` where `==` was meant
(CPython interns them); a *chain* graph hid first-found where shortest-path
was meant (on a chain they are the same function); ids from `uuid4()` hid a
composite key compared on one component. All were found by mutation testing
and essentially nothing else.

Three rules fall out of it, and they are worth internalising before writing
the first assertion:

- **When a key is a tuple, write one test where its components collide.** This
  is narrower than the general rule and it is the form that actually fires in
  time — it was violated by an implementer who had *just read the general
  rule*, because the habit of drawing ids from `uuid4()` survived contact with
  the principle.
- **Pin boundary values with `@example`.** A property test is a sampler, not a
  proof about a specific value, and `KG_COMPLIANCE_MAX_EXAMPLES` makes the
  budget tunable — so a boundary covered only by a property is covered
  non-deterministically.
- **Break the implementation on purpose and watch the property fail.** A
  property that stays green under a deliberate defect is worse than no
  property, because its existence is what stops anyone writing the test that
  would have worked.

## Architecture

`lint-imports` enforces a layered contract declared in `pyproject.toml`,
highest to lowest:

```
composition
extraction : consolidation : temporal : graph : vector : llm   (siblings)
projections
aggregates
events
ports
domain
```

Lower layers must not import higher ones, and the **siblings must not import
each other** — that band is where the load-bearing separations live. `llm`
sits *beside* `extraction` rather than beneath it so extraction can reach only
`ports.llm_provider` and never the LangChain adapter. `consolidation` and
`temporal` are siblings for the same reason: above `extraction` they could
reach `mapping.py`, which is how a second entity-id scheme gets born.

`containers = ["redstring"]` with `exhaustive = true`, so a **new top-level
package is a contract failure until it is placed deliberately.** That is the
point: decide where it sits, or argue the contract should change — which is an
ADR.

`lint-imports` only sees first-party imports, so it cannot catch a `langchain`
or `neo4j` import appearing where it should not.
`tests/unit/test_dependencies_stay_confined.py` is what covers that: a table of
four confined libraries and the one directory each may be imported from. **Add
a row when you add a client** — it is the only thing keeping the driver out of
`composition/`.

## Architecture decisions

Work that changes a public contract, a persistence format, the layer contract,
the entity/graph data model, merge semantics, or the shape of a port is not
complete until the architectural decision is authored and version-locked in git alongside code (governed by ADR-0001).

- **SpecOps ADRs**: New architectural decisions are authored under `docs/project/adrs/` with SpecOps YAML frontmatter and indexed in `docs/project/adrs/REGISTRY.md`.
- **Legacy ADRs**: Domain architecture decisions (ADR-0101 through ADR-0147) are published on the documentation site under [Decisions](adr/index.md).

Run a spec against the existing ADRs and say, for each related one, whether it
**stands**, is **amended**, or is **superseded**. Silence is not an answer.

## Preflight Verification Checklist

Before pushing a branch or opening a pull request, verify:

```bash
# 1. SpecOps invariant & health check (<500 lines per file, 0 numbering collisions)
uv run spec-ops health

# 2. Complete unit test suite & coverage ratchet floor (>=96.42%)
uv run pytest

# 3. Documentation build with strict link & anchor validation
uv run mkdocs build --strict

# 4. Dependency lockfile integrity
uv lock --check
```

## Releasing

Maintainers only — see `RELEASING.md` in the repository and [Library release workflow and SpecOps](explanation/library-release-workflow-and-specops.md).
