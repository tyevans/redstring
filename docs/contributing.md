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

## Quality gates run on commit

Every check — ruff, `mypy --strict`, bandit, the layered import contract, and
pytest under a coverage ratchet — is wired into `pre-commit` and runs on
`git commit`.

**Do not run them yourself first.** It duplicates work the hook already does,
and the hook often fixes the problem in place (re-`git add` and commit again
when it does). [Quality gates](reference/quality-gates.md) lists what each one
checks.

Prefer many small commits over one large one. Each commit runs the full gate,
so small commits keep each run fast and keep the failure surface legible.

## Commit messages

Not conventional commits. An imperative sentence, capitalised, no trailing
period, no `feat:`/`fix:` prefix:

```
Add BACKLOG.md and require deferred work to land in it
Fix all 42 pre-existing test failures
Configure ruff, bandit, coverage ratchet
```

The subject says *what changed*. The body says what it cost and what you
learned — counts, file tables and survivor lists belong there, because a
commit message is immutable and correctly scoped to a moment, unlike an ADR or
a doc page.

## Deferred work goes in `BACKLOG.md`

**Anything you notice and do not fix lands in `BACKLOG.md` in the same commit
that passes it by.** No exceptions and no substitutes: not a TODO comment, not
a line in the PR body, not a sentence in review that scrolls away.

Write the entry so someone picking it up cold does not have to rediscover what
you already know — name the file and line, say what is actually wrong, and say
what you learned that made you defer rather than fix. An entry that only says
"clean up X" has thrown away the expensive part.

When you fix an entry, delete it in the same commit.

## Testing

Four trees, and they are not interchangeable:

| Tree | In the commit gate? | Needs |
|---|---|---|
| `tests/unit/` | yes | nothing external |
| `tests/compliance/` | **never collected directly** | subclassed by unit and integration modules |
| `tests/integration/` | no | backends from `docker-compose.test.yml` |
| `tests/accuracy/` | no | it is empty — see below |

`tests/compliance/` is the one to understand first. It is a *library*, not a
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
`composition.py`.

## Architecture decisions

Work that changes a public contract, a persistence format, the layer contract,
the entity/graph data model, merge semantics, or the shape of a port is not
complete until the decision is written down. See [Decisions](adr/index.md) for
the conventions — above all that **numbers are allocated at merge, not at
drafting**, and that renumbering means the filename, the title, and every
inbound citation in one commit.

Run a spec against the existing ADRs and say, for each related one, whether it
**stands**, is **amended**, or is **superseded**. Silence is not an answer.

## Releasing

Maintainers only — see `RELEASING.md` in the repository.
