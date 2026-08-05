# Quality gates

Reference for every automated check redstring enforces, and for the
configuration each one reads.

The gates are not a suite of independent tools that happen to be installed.
They are one pipeline with a single entry point: `git commit` runs
`pre-commit`, `pre-commit` runs the formatters, the type checker, the security
scanner, the architecture contract and the test suite behind a coverage
ratchet, and `fail_fast: true` stops at the first one that complains. Nothing
in this page needs to be run by hand as a pre-commit step — running it
separately duplicates work the hook already does.

Two things sit outside that pipeline because they are too slow for it: the
integration suite, which needs the backends in `docker-compose.test.yml`, and
the two mutation-testing runners. Both are excluded from the default run and
invoked deliberately.

Every claim on this page is sourced from a file in the repository:

| What it configures | Where it lives |
| --- | --- |
| Hook set, order, and `fail_fast` | `.pre-commit-config.yaml` |
| ruff, mypy, bandit, pytest, coverage, mutmut, import-linter | `pyproject.toml` |
| Coverage ratchet behaviour | `scripts/coverage_ratchet.py`, `.coverage-baseline` |
| cosmic-ray session settings | `cosmic-ray.toml` |
| Integration backends | `docker-compose.test.yml` |
| Compliance-suite example count | `tests/compliance/graph_store.py`, `tests/compliance/vector_store.py` |

When this page and one of those files disagree, the file is right — say so in
an issue rather than reading around it.

## Scope and how to read this page

This is reference material: it states what each gate does and what its knobs
mean, not how to accomplish a task with it. For the procedures — bringing the
test backends up, running the excluded suites, driving a mutation session and
reading its output — see
[How to run the integration and mutation suites](../how-to/run-integration-and-mutation-suites.md).

Two of the design decisions behind these gates are argued elsewhere rather
than restated here:

- Why the ruff and mypy exemption lists are empty, and why an exemption list
  needs a test that its entries still match a real file:
  [ADR 0014](../adr/0014-exemption-lists-are-empty-and-must-stay-falsifiable.md).
- What makes a test able to fail, and the catalogue of failure shapes this
  project has actually shipped: [`.claude/rules/testing.md`](https://github.com/tyevans/redstring/blob/main/.claude/rules/testing.md).

[`README.md`](https://github.com/tyevans/redstring/blob/main/README.md) has the short version — first-time setup and
the handful of commands most sessions need.

Throughout, "the gate" means the default `git commit` run: `pytest` with
`addopts = ["-m", "not accuracy and not integration"]` applied, under the
coverage ratchet, plus the lint, type, security and architecture hooks. A
check described as running "outside the gate" is one you have to ask for.

## Environment setup

Two commands make a fresh clone able to run the gates:

```
uv sync --all-extras && uv run pre-commit install
```

`uv` manages the environment in full. Project-scoped commands run through
`uv run`, and `pyproject.toml`'s dependency tables are edited with `uv add`,
`uv add --optional <extra>` and `uv remove` rather than by hand.
`requires-python` is `>=3.13`, and ruff's `target-version` is `py313`.

### `uv sync --all-extras`, not `--extra dev`

The `dev` extra holds tooling only — pytest, ruff, mypy, bandit,
import-linter, coverage, hypothesis, the two mutation runners and the type
stubs. The backend dependencies live in separate extras (`neo4j`, `llm`; see
[Dependency groups](#dependency-groups-neo4j-llm-all-dev)), and a venv without
them does not *skip* the modules that import them — it fails **collection** on
them. What that looks like is not a packaging error:

- A worktree synced with `--extra dev` reported **0 survivors out of 426**
  cosmic-ray mutants. Every mutant had "died" on an import error, and
  `cr-report` showed `WorkerOutcome.NORMAL, TestOutcome.KILLED` for all of
  them — indistinguishable from a suite with nothing left to catch.
- The same shape produced 47 mypy errors in files nobody had touched, which
  read as a type regression.

Both times the environment was the cause and neither symptom pointed at it.

### `uv add` and `uv remove` re-sync, and can narrow the extras back

Both commands re-resolve and re-install, and the install they leave behind can
be silently narrowed to `dev`. **After any dependency change, re-sync with
`--all-extras`.** This is the single behaviour behind both incidents above.

### `uv run pre-commit install`

`.pre-commit-config.yaml` sets `default_install_hook_types: [pre-commit]`, so
`pre-commit install` with no arguments installs exactly the `pre-commit` hook
and no others — there is no commit-msg or pre-push stage in this repo. Until
that command has been run in the clone, `git commit` runs no gate at all: the
checks are not wired into anything else, so an uninstalled hook is a silent
absence rather than a visible failure.

Every local hook runs its tool as `uv run <tool>` (`ruff`, `mypy`, `bandit`,
`lint-imports`, `python scripts/coverage_ratchet.py`), so the versions the
gate uses are the ones in the synced environment — not whatever is on `PATH`.
A tool missing from the venv fails the hook rather than falling through to a
system copy.

### Verifying the environment

The environment is sound when the default gate is green:

```
uv run pytest
```

That is the same invocation the ratchet drives, with
`addopts = ["-m", "not accuracy and not integration"]` applied. Run it before
trusting any result that depends on the environment being complete — in
particular before reading a mutation session, where a green unmutated
`test-command` in the *same* environment is the precondition for the numbers
meaning anything. See
[How to run the integration and mutation suites](../how-to/run-integration-and-mutation-suites.md).

## Dependency groups: `neo4j`, `llm`, `all`, `dev`

Four extras are declared in `[project.optional-dependencies]`. Only one of
them is about the gates; the other three are about what the library can talk
to.

| Extra | Contents | What needs it |
| --- | --- | --- |
| `neo4j` | `neo4j>=5.27,<6` | `redstring.graph.adapters.neo4j` |
| `llm` | `langchain-core>=0.3,<2`, `langchain-openai>=0.2,<2` | `redstring.llm.adapters.langchain` |
| `all` | `redstring[neo4j,llm]` | a deployment wanting both backends |
| `dev` | the toolchain (below) | running the gates |

Everything else is a hard runtime dependency in `[project] dependencies` —
pydantic, asyncpg, httpx, numpy, python-dateutil, dateparser, pyyaml,
`redis[hiredis]`, jellyfish, and `eventsource-py>=0.9.1,<0.11`.
`eventsource-py` is deliberately *not* an extra: `redstring.__init__`
exports `build_graph`, `Document`, `DocumentExtracted` and the two
projections, all of which need it, and a public API that fails to import
without an extra is not a public API.

### `neo4j` and `llm` are backends, not features

Both extras exist because their adapter is one implementation of a port, and
the port is what the rest of the library depends on. Neither package is
imported outside its adapter module:

- `redstring.graph` re-exports nothing on purpose, so `import
  redstring.graph` does not pull the driver in — reaching an adapter is a
  deliberate act at the composition root.
- `langchain*` may be imported only under `redstring/llm/adapters/`, and
  `tests/unit/llm/test_port_does_not_leak.py` parses every module under
  `src/` to enforce that. `lint-imports` cannot see it: the contract only
  reasons about first-party imports.

`LangChainLlmProvider.openai_compatible` defers its `langchain_openai` import
to call time and re-raises a missing one as
`ImportError: … install redstring[llm]`, so an absent extra names itself
rather than surfacing as a `ModuleNotFoundError` three frames down. That is
the exception. In general a missing extra is **not** a graceful skip: the
adapter module imports its package at module scope, and pytest fails
*collection* on it.

The `llm` extra is one transport for every deployment — `langchain-openai`
speaks to any OpenAI-compatible server (llama.cpp, llama-swap, vLLM, Ollama's
shim, OpenAI itself). Both extras use ranges rather than pins, because the
point of the port is that a breaking upstream change touches exactly one
adapter file.

### `dev` is tooling only

`dev` holds `pytest` (with `pytest-asyncio==1.3.0` pinned, plus `pytest-cov`,
`pytest-xdist`, `pytest-randomly`), `hypothesis`, `coverage[toml]`, `ruff`,
`mypy`, `bandit[toml]`, `import-linter`, `pre-commit`, the two mutation
runners (`mutmut`, `cosmic-ray`), and the `types-*` stub packages mypy needs
under `--strict`.

It contains **no backend**. `uv sync --extra dev` therefore produces an
environment where every gate appears runnable and several of them are
measuring the wrong thing — see
[Environment setup](#uv-sync---all-extras-not---extra-dev) for the two
incidents this caused. `uv sync --all-extras` installs all four, and is the
only supported development sync.

`uv add --optional <extra> <package>` is how an extra gains a member; the
tables are never edited by hand. Both `uv add` and `uv remove` re-sync as a
side effect and can narrow the installed set back to `dev`, so re-run
`uv sync --all-extras` after any dependency change.

### `all` exists for consumers, not for this repo

`all = ["redstring[neo4j,llm]"]` is a self-referential extra: it installs the
package's own `neo4j` and `llm` extras and nothing else. It is the handle a
downstream project uses (`pip install redstring[all]`); a clone of this
repository wants `--all-extras`, which is a superset — it adds `dev`.

## Pre-commit configuration

`.pre-commit-config.yaml` declares the whole gate: two settings at the top of
the file, one pinned upstream repo, and a `local` repo of six hooks that run
out of the project's `uv` environment.

```yaml
default_install_hook_types: [pre-commit]
fail_fast: true
```

### `default_install_hook_types: [pre-commit]`

`pre-commit install` with no `--hook-type` argument installs the hook types
named here. This repo names exactly one, so `uv run pre-commit install` wires
up `.git/hooks/pre-commit` and nothing else — no `commit-msg`, no `pre-push`,
no `prepare-commit-msg` stage exists. Every check on this page therefore runs
at one moment: after `git commit` is issued and before the commit object is
written.

Two consequences worth stating:

- **`git commit --no-verify` skips the entire gate**, not part of it. There is
  no second stage to catch what the first one missed.
- **A clone where `pre-commit install` has not been run has no gate at all.**
  Nothing else invokes these checks, so their absence is silent — commits
  succeed and look normal. This is the reason
  [Environment setup](#uv-run-pre-commit-install) treats the install command
  as part of first-time setup rather than an optional convenience.

### `fail_fast: true`

`pre-commit` stops at the first failing hook instead of running the rest.
Hooks execute in file order, so the run is a cheapest-first pipeline:

1. the `pre-commit-hooks` set (whitespace, EOF, syntax, parse checks)
2. `ruff check --fix`, then `ruff format`
3. `mypy`
4. `bandit`
5. `lint-imports`
6. the pytest + coverage ratchet

The ordering is the point. A file with a syntax error fails `check-ast` in
milliseconds rather than after a full test run, and a lint violation is
reported before mypy spends time on a file ruff is about to rewrite. The cost
is that **one commit attempt reports one problem**: with `fail_fast` on, a
green `ruff` says nothing about mypy, and a green mypy says nothing about the
suite. Expect to commit, fix, and commit again — and read a passing hook as
"this hook passed", never as "everything below it would have".

`fail_fast` also interacts with the auto-fixing hooks. `trailing-whitespace`,
`end-of-file-fixer`, `mixed-line-ending --fix=lf`, `ruff check --fix` and
`ruff format` all modify files in place and then fail, because `pre-commit`
fails any hook that changed the working tree. That failure is not a defect
report: the fix is already applied. Re-`git add` the files and commit again.

### The two repo blocks

| Block | `rev` | What it holds |
| --- | --- | --- |
| `https://github.com/pre-commit/pre-commit-hooks` | `v6.0.0` | the twelve stock file/syntax checks |
| `local` | — | ruff (×2), mypy, bandit, import-linter, coverage ratchet |

The upstream repo is pinned to a tag; `pre-commit` builds it into its own
cached environment, so its version is independent of the project venv.

Everything in the `local` block is the opposite, by design. Each `entry` is
`uv run <tool>` with `language: system`, which means `pre-commit` creates no
environment and the tool resolves through `uv` into the synced project venv.
The config carries the reasoning inline:

> Everything below runs out of the project's uv environment so the versions
> match what `uv run` uses locally and in CI.

So the ruff that formats your commit is the ruff in `[dev]`, and bumping it is
a `uv add` rather than a `rev` edit. The trade is that a venv missing a tool
fails the hook instead of silently falling through to a system copy — which is
the behaviour you want, and another reason `uv sync --all-extras` is the only
supported sync.

Every `local` hook also sets `require_serial: true`. `pre-commit` would
otherwise shard the file list across processes and run several copies of each
tool concurrently; for tools that already parallelise internally (`mypy`'s
cache, `pytest-xdist` under the ratchet) that is contention rather than
speed-up, and for the in-place fixers it is a race on the same files.

Per-hook file filters, `pass_filenames`, and which hooks see the staged file
list at all are covered in
[Hook file filters and `pass_filenames` behaviour](#hook-file-filters-and-pass_filenames-behaviour).

## Hooks from `pre-commit-hooks` v6.0.0

Twelve stock hooks from `https://github.com/pre-commit/pre-commit-hooks`,
pinned at `rev: v6.0.0`. Only one carries an argument; the other eleven run at
their defaults. Because `fail_fast: true` and this block is declared first,
these are the cheapest checks in the pipeline and the ones most likely to be
the only thing a failed commit reports.

They divide into three kinds: fixers that rewrite the file, parsers that
refuse to let an unparseable file reach a slower tool, and content checks that
catch a mistake no downstream tool is looking for.

### The fixers

| Hook | What it does |
| --- | --- |
| `trailing-whitespace` | strips whitespace at end of line |
| `end-of-file-fixer` | ensures the file ends in exactly one newline |
| `mixed-line-ending` (`args: [--fix=lf]`) | rewrites CRLF and CR to LF |

All three modify the working tree and then **fail the hook**, because
`pre-commit` fails any hook that changed a file. That failure is not a finding
— the correction is already on disk. Re-`git add` and commit again. With
`fail_fast` on, expect this to cost one commit attempt and no more.

`mixed-line-ending` is the only hook in the block with an argument.
`--fix=lf` makes the choice explicit rather than leaving it to the hook's
default behaviour of inferring the majority ending per file: inference means a
file that arrives mostly-CRLF gets *normalised to CRLF*, and the repository
would then hold both endings, each one locally self-consistent. Naming `lf`
makes the whole tree LF regardless of what any individual file arrived as.

### The parsers

`check-ast` compiles every staged Python file. `check-yaml`, `check-toml` and
`check-json` parse their formats. Nothing here inspects meaning — a valid
file passes however wrong its contents are.

Ordering is what makes them worth their place. A file with a syntax error
fails `check-ast` in milliseconds instead of surfacing as a ruff parse error,
a mypy crash, or a pytest collection failure minutes later. The format parsers
matter more here than in most repos: `pyproject.toml` configures ruff, mypy,
bandit, pytest, coverage, mutmut and the import contract, and
`.pre-commit-config.yaml` configures the gate itself, so a malformed one of
those breaks every later hook with an error that does not name the cause.

### The content checks

- **`check-merge-conflict`** — fails on `<<<<<<<`, `=======`, `>>>>>>>`
  markers. In Python these are usually a syntax error too, so `check-ast`
  would also catch them; in Markdown, YAML front matter, or a data fixture
  nothing else would.
- **`check-case-conflict`** — fails on paths that differ only in case. Linux
  (this project's stated platform) permits `Entity.py` and `entity.py` side by
  side; macOS and Windows checkouts of the same commit get one file, chosen
  arbitrarily. The failure lands on whoever clones, not whoever committed, so
  the check has to run where the commit is made.
- **`check-added-large-files`** — fails on a newly added file over the default
  **500 kB**. No `args` are set, so that default is what applies, and it
  checks *added* files only — a tracked file that grows past the limit is not
  re-flagged. The realistic hazard here is a committed model artifact, a
  `session.sqlite` from a cosmic-ray run, or a coverage data file.
- **`debug-statements`** — fails on `breakpoint()`, `pdb`/`ipdb`/`pudb`
  imports and `set_trace()` calls, found by walking the AST rather than by
  grep. This is the one content check with no other backstop: a stray
  `breakpoint()` is valid Python, passes ruff and mypy, and in the suite hangs
  the run under `pytest-xdist` rather than failing it. A hang reads as
  infrastructure trouble and gets retried instead of investigated — the same
  failure mode [`.claude/rules/testing.md`](https://github.com/tyevans/redstring/blob/main/.claude/rules/testing.md)
  warns about for unbounded loops.
- **`check-docstring-first`** — fails when a module has code before its
  docstring, which silently demotes the intended docstring to a bare string
  expression. `__doc__` becomes `None` and nothing else in the pipeline
  notices.

### What this block does not filter

Every hook here uses its upstream `types`/`files` defaults, so the Python
hooks see staged Python files and the format parsers see their own
extensions — none of them is scoped to `src/` or `tests/` by this config. The
project's own scoping (`bandit`'s `exclude: ^tests/`, `import-linter`'s and
the ratchet's `files:` patterns) applies only to the `local` block; see
[Hook file filters and `pass_filenames` behaviour](#hook-file-filters-and-pass_filenames-behaviour).

These hooks also run in `pre-commit`'s own cached environment built from the
pinned tag, not in the project venv. They are therefore the only checks on
this page that still work in a clone synced without `--all-extras` — which is
worth remembering when a commit passes the first block and fails oddly in the
second.

## Local hooks run through `uv`

The `local` repo block holds six hooks. None of them declares a
`pre-commit`-managed environment: every one sets `language: system` and an
`entry` beginning `uv run`, so the tool that executes is the one in the synced
project venv. Bumping any of them is a `uv add`, not a `rev` edit, and a venv
missing the tool fails the hook rather than falling through to a system copy.

All six also set `require_serial: true` — `pre-commit` would otherwise shard
the staged file list and run several copies concurrently, which is contention
for tools that already parallelise internally and a race for the ones that
rewrite files in place.

| Hook `id` | `entry` |
| --- | --- |
| `ruff-check` | `uv run ruff check --fix --force-exclude` |
| `ruff-format` | `uv run ruff format --force-exclude` |
| `mypy` | `uv run mypy` |
| `bandit` | `uv run bandit -c pyproject.toml -q` |
| `import-linter` | `uv run lint-imports` |
| `pytest-coverage-ratchet` | `uv run python scripts/coverage_ratchet.py` |

They run in that order, and under `fail_fast: true` the order is the pipeline:
the two sub-second fixers, then the type checker, then the security scan and
the architecture contract, then the suite. A green hook says nothing about the
ones below it.

### `ruff check --fix --force-exclude`

Runs first of the six and **rewrites files**. Like the stock fixers, a run
that changed anything fails the hook with the correction already on disk —
re-`git add` and commit again. Placing it before `mypy` is deliberate: there
is no value in type-checking a file ruff is about to reformat.

`--force-exclude` is the load-bearing flag. `pre-commit` passes explicit
filenames, and ruff normally treats an explicitly named file as an override of
its own `exclude` configuration — which would let a path ruff is configured to
skip get linted anyway, purely because it happened to be staged.
`--force-exclude` makes the configured exclusions win over the argument list,
so the hook and a bare `uv run ruff check` answer the same question. This is
the same hazard [ADR 0014](../adr/0014-exemption-lists-are-empty-and-must-stay-falsifiable.md)
describes from the other direction: a command that bypasses the configured
scope is measuring something other than the gate.

The rule selection, the single `UP042` ignore, the test-only per-file ignores
and the banned-API entries are covered in the ruff sections below.

### `ruff format --force-exclude`

Formatting only, and separate from the check hook because the two do different
things — `ruff check --fix` applies lint autofixes, `ruff format` normalises
layout. Same `types_or: [python, pyi]` filter, same in-place-and-fail
behaviour, same reason for `--force-exclude`.

### `mypy`

`pass_filenames: false`. The hook invokes `uv run mypy` with **no arguments**,
so mypy resolves its own scope from `[tool.mypy] files = ["src/redstring"]`
and checks the whole package on every run rather than the staged subset. Two
reasons that is the right shape:

- Type errors are non-local. Editing a return type breaks callers in files
  nobody staged, and a filename-scoped run would not look at them.
- Naming files on the command line **bypasses `exclude`**, so a
  filename-passing hook would silently answer a different question than the
  configured run does. There is no `exclude` in this project any more (see
  [ADR 0014](../adr/0014-exemption-lists-are-empty-and-must-stay-falsifiable.md)),
  which makes the point moot today and makes it worth keeping
  correct for the day someone re-adds one.

The configuration is `strict = true` plus `warn_unreachable`,
`warn_return_any`, `disallow_untyped_defs` and the pydantic plugin; details in
[mypy configuration](#mypy-configuration-files-strict-warn_unreachable-warn_return_any-pydantic-plugin-asyncpg-override-no-exclude).
The `types_or: [python, pyi]` filter still decides *whether* the hook runs —
a commit touching no Python skips it — it just does not decide what gets
checked.

### `bandit -c pyproject.toml -q`

The security scan, and the only hook whose scope is set in two places at once:

- `-c pyproject.toml` points bandit at `[tool.bandit]`, which sets
  `exclude_dirs = ["tests", ".venv", "build", "dist"]`.
- the hook adds `exclude: ^tests/`, a `pre-commit`-level filter.

Both say "not tests", and the duplication is not redundant. `exclude_dirs` is
a *path* exclusion bandit applies to what it walks; the hook's `exclude` stops
staged test files being passed as arguments in the first place. Keeping both
means the hook and a bare `uv run bandit -c pyproject.toml -r src/` agree, and
that neither one alone is what makes tests exempt.

Tests are exempt because bandit's checks are about *shipped* code: `assert` is
B101 and is the entire vocabulary of the suite, and hard-coded credentials in
a fixture are fixtures. `-q` suppresses the per-file progress output, so the
hook prints only findings.

`types: [python]` — note the singular key, not `types_or` — so `.pyi` stubs do
not reach it.

### `lint-imports`

The architecture contract, run with `pass_filenames: false` for the same
reason as mypy: an import contract is a property of the whole package, and a
violation is a *pair* of modules, only one of which is likely staged.

`files: ^(src/|pyproject\.toml$)` is what decides whether it runs at all — the
source tree, or the file the contract itself is declared in. A commit touching
only tests or docs skips it. Including `pyproject.toml` in that pattern is the
part worth noticing: the contract's layer list lives there, so editing the
*rules* re-runs the check against the existing code, not only the other way
round.

What it enforces — `root_packages`, `containers`, `exhaustive = true` and the
layer order — is in
[import-linter contract](#import-linter-contract-root_packages-containers-exhaustive-layer-order).
It sees first-party imports only, so it cannot catch a `langchain` import
appearing outside the adapter package; `tests/unit/llm/test_port_does_not_leak.py`
is what covers that, and it runs in the hook below.

### `python scripts/coverage_ratchet.py`

The last and slowest hook: the full default suite under coverage, with a
one-way ratchet on the total. `pass_filenames: false` — the script builds its
own `pytest` invocation and would ignore arguments anyway.

`files: ^(src/|tests/|pyproject\.toml$|scripts/coverage_ratchet\.py$)` scopes
when it runs. Source, tests, the file holding the pytest and coverage
configuration, and the ratchet script itself — a documentation-only commit
does not pay for a test run. The last two entries are the same instinct as
import-linter's: changing the *gate* must re-run the gate.

Because it is the last hook, `fail_fast` means it only ever executes on a
commit where the five checks above it are already clean. Its own contract —
the baseline file, `TOLERANCE`, the exact pytest arguments, the exit codes,
and how to accept a deliberate drop — is
[documented below](#the-coverage-ratchet-contract-scriptscoverage_ratchetpy).

### None of these should be run as a separate pre-commit step

Running `ruff`, `mypy`, `bandit`, `lint-imports` or `pytest` by hand *before*
committing duplicates work the hook is about to do. Write the change, then
commit; the hook reports what is wrong and frequently fixes it in place. The
exception is diagnosis — when a hook has already failed and you want a tighter
loop on one file — and there the caveat above applies: an invocation that
names files explicitly is not always asking the configured question. Prefer
many small commits, which keeps each hook run short.

## Hook file filters and `pass_filenames` behaviour

Every hook answers two separate questions, and confusing them is the usual
source of surprise:

1. **Does this hook run on this commit?** — decided by `types`, `types_or`,
   `files` and `exclude` against the staged paths. No match, no run.
2. **What does it look at once it runs?** — decided by `pass_filenames`.
   Default `true` means `pre-commit` appends the matching staged paths to
   `entry`; `false` means the tool is invoked bare and resolves its own scope
   from configuration.

A hook can therefore be triggered by one file and check the entire package.
That is exactly what `mypy`, `lint-imports` and the ratchet do.

### The filters, as configured

| Hook | Filter | `pass_filenames` | Sees |
| --- | --- | --- | --- |
| `pre-commit-hooks` block (12) | upstream defaults | `true` | staged files of its own type |
| `ruff-check` | `types_or: [python, pyi]` | `true` | staged `.py` / `.pyi` |
| `ruff-format` | `types_or: [python, pyi]` | `true` | staged `.py` / `.pyi` |
| `mypy` | `types_or: [python, pyi]` | **`false`** | `[tool.mypy] files` |
| `bandit` | `types: [python]`, `exclude: ^tests/` | `true` | staged non-test `.py` |
| `import-linter` | `files: ^(src/\|pyproject\.toml$)` | **`false`** | the whole contract |
| `pytest-coverage-ratchet` | `files: ^(src/\|tests/\|pyproject\.toml$\|scripts/coverage_ratchet\.py$)` | **`false`** | the whole suite |

Note `bandit` uses `types` (singular) and the ruff hooks use `types_or`: the
ruff hooks accept `.pyi` stubs, bandit does not. No `local` hook sets `files`
*and* passes filenames, so the two mechanisms never interact here.

### Why three hooks pass no filenames

Each of the three checks a property that is not a property of one file:

- **mypy** — a changed return type breaks callers in files nobody staged. A
  filename-scoped run would not look at them.
- **import-linter** — a violation is a *pair* of modules, and only one of them
  is likely in the commit.
- **the ratchet** — the number it compares against `.coverage-baseline` is
  total coverage of the package. Restricting the run to staged tests would
  compute a different, meaningless number.

There is a second reason for mypy specifically: **naming files on the command
line bypasses `exclude`.** A filename-passing mypy hook would answer a
different question than the configured run. This project currently has no
`exclude` (see
[ADR 0014](../adr/0014-exemption-lists-are-empty-and-must-stay-falsifiable.md)),
which makes it moot today and worth keeping correct.

The trade is cost. These three run in full whenever their filter matches, so a
one-line source edit pays for a whole-package type check, the whole contract,
and the whole suite. That is what `files:` is buying back: a docs-only commit
matches none of the three patterns and skips all of them.

### `--force-exclude` is the filename-passing counterpart

The ruff hooks *do* pass filenames, and ruff normally treats an explicitly
named file as an override of its own `exclude` — a staged path ruff is
configured to skip would get linted anyway, purely because `pre-commit` named
it. `--force-exclude` on both ruff hooks makes the configured exclusions win
over the argument list, so `uv run ruff check` and the hook agree.

`bandit` reaches the same place by different means: the hook's
`exclude: ^tests/` stops test files being passed as arguments at all, and
`-c pyproject.toml` supplies `exclude_dirs = ["tests", ".venv", "build",
"dist"]` for whatever bandit walks itself. Neither alone is what makes tests
exempt.

The general shape — an invocation that names files explicitly is not always
asking the configured question — is the lint-side spelling of the rule in
[ADR 0014](../adr/0014-exemption-lists-are-empty-and-must-stay-falsifiable.md).
It applies to hand-run diagnosis too: `uv run ruff check src/redstring/foo.py`
is not the gate, and `uv run mypy src/redstring/foo.py` is a different check
from the one that runs on commit.

### Filters decide when, not whether, a check is authoritative

`files:` scoping means a commit can be green without several hooks having
executed. Editing only `docs/` runs the stock block and nothing else; editing
only `tests/` runs everything except `import-linter`. Combined with
`fail_fast: true` — which stops at the first failure and skips the rest — a
passing commit is evidence about the hooks that ran, not about the pipeline.
The whole pipeline runs on demand:

```
uv run pre-commit run --all-files
```

That ignores staging and the `files:` patterns' practical effect, since every
tracked file is offered to every hook. It is the check to run before a merge,
and after any edit to `.pre-commit-config.yaml` itself — which, notably, is
matched by none of the three `files:` patterns above.

## ruff configuration

`[tool.ruff]` in `pyproject.toml` is four keys, and the lint rules are chosen
in `[tool.ruff.lint]` below them. Both ruff hooks — `ruff check --fix
--force-exclude` and `ruff format --force-exclude` — read this configuration,
and `--force-exclude` is what makes the hook and a bare `uv run ruff check`
answer the same question.

```toml
[tool.ruff]
target-version = "py313"
line-length = 100
src = ["src", "tests"]
```

### `target-version = "py313"`

Matches `requires-python = ">=3.13"`. It is not only a compatibility floor:
the `UP` (pyupgrade) rules rewrite code to the newest form the target permits,
so this key is what makes ruff propose 3.13-era syntax rather than something a
3.9 target would consider unsafe. Raising `requires-python` without raising
this leaves the package free to use syntax ruff will never suggest; lowering
it silently turns off a family of autofixes.

### `line-length = 100`

One number, read by both tools — `ruff format` wraps to it and `E501` reports
past it. Because the formatter runs in the same pipeline, a line over the
limit is normally corrected rather than reported; the findings that survive
are the ones the formatter cannot break, such as a long string literal, a URL
in a comment, or a `# noqa`-bearing line.

### `src = ["src", "tests"]`

Tells the isort rules (`I`) where first-party code lives, so imports resolve
to the right section. It works with `[tool.ruff.lint.isort]
known-first-party = ["redstring"]`: `src` covers the layout, the explicit
name covers the package. Without both, `import redstring` in a test can be
sorted as third-party, and the resulting churn shows up as a formatting
diff nobody asked for.

### Selected rule families

`select` names nineteen families. There is no blanket `ALL`, so a family not
listed here is off, and adding one is a deliberate edit rather than a
side-effect of a ruff upgrade.

| Code | Family | Why it is on |
| --- | --- | --- |
| `E`, `W` | pycodestyle errors / warnings | baseline style |
| `F` | pyflakes | unused names, undefined names, `__all__` entries that resolve to nothing (F822) |
| `I` | isort | import order |
| `B` | flake8-bugbear | mutable defaults, loop-variable capture, `assert False` |
| `C4` | flake8-comprehensions | needless `list()`/`dict()` wrapping |
| `UP` | pyupgrade | modern syntax for the target version |
| `SIM` | flake8-simplify | collapsible conditionals, redundant `bool()` |
| `RUF` | ruff-specific | including RUF012 mutable class attrs |
| `ANN` | flake8-annotations | every function annotated — the input mypy `--strict` needs |
| `ASYNC` | flake8-async | blocking calls inside `async def` |
| `DTZ` | flake8-datetimez | naive datetimes |
| `ERA` | eradicate | commented-out code |
| `PT` | flake8-pytest-style | fixture and `raises` conventions |
| `PTH` | flake8-use-pathlib | `os.path` for `pathlib` |
| `RET` | flake8-return | redundant `else` after `return`, unnecessary assignment |
| `TC` | flake8-type-checking | imports used only in annotations |
| `TID` | flake8-tidy-imports | the banned-API mechanism |

Four of them are load-bearing rather than stylistic, and the config says so
inline:

- **`DTZ`** — "tz-aware is a domain invariant here". Temporal extents and
  event timestamps are compared and ordered across the package, and a naive
  datetime does not fail, it compares wrongly. `TID`'s banned-api entries
  extend this to the two spellings `DTZ` cannot see; both are covered in
  [per-file-ignores and banned-api](#ruff-per-file-ignores-and-banned-api-tests-annb011dtz001-datetimeutcnow-ban).
- **`ANN`** — mypy `--strict` rejects an untyped `def` in `src/`, but ruff
  reports it first and much faster. The two overlap deliberately, and `ANN` is
  the reason `tests/**` needs a per-file ignore rather than a global one.
- **`TC`** — moving an annotation-only import into a `TYPE_CHECKING` block is
  correct for ordinary classes and *breaks pydantic*, which resolves field
  annotations when it builds a schema. Keeping `TC` on and configuring
  `runtime-evaluated-base-classes` is what makes the family safe here; see
  [flake8-type-checking](#ruff-flake8-type-checking-runtime-evaluated-base-classes).
- **`ERA`** — commented-out code is the one finding on this list with no
  runtime consequence and the longest half-life. Git holds the old version.

### The single `ignore`: `UP042`

`ignore` has exactly one entry, and it is a *deferral with a stated reason*,
not a disagreement with the rule:

```toml
ignore = [
    "UP042",
]
```

UP042 wants `class X(str, Enum)` rewritten as `enum.StrEnum`. That is a
behaviour change wearing a style fix's clothing: `str(X.A)` goes from `"X.A"`
to `"a"`, so every f-string, log line and serialised value holding a member
changes silently, and nothing about the rewrite fails a type check. The
`(str, Enum)` idiom is used at multiple sites across the package, which makes
the migration a single deliberate change made with tests — not something
`ruff check --fix` should apply file by file while you are committing an
unrelated edit.

Two things follow from that framing:

- **The ignore is global, not per-file.** It belongs in `ignore` rather than
  `per-file-ignores` because it is not an exemption for legacy code — it
  applies to a new enum written today, for the same reason.
- **It is the only entry.** The legacy per-file exemption lists are empty and
  the mypy `exclude` key is deleted (see
  [ADR 0014](../adr/0014-exemption-lists-are-empty-and-must-stay-falsifiable.md)),
  so `UP042` is the whole of what this project declines to enforce on `src/`.
  A second entry appearing here is a visible decision in review rather than an
  edit to an existing list.

When the migration happens, deleting the entry and running the configured gate
is the measurement that counts — a `--select UP042` run *through* the ignore
cannot report a finding whatever the code says.

## ruff per-file-ignores and banned-api

Two small blocks sit under `[tool.ruff.lint]`, and together they are the whole
of what the rule selection above is adjusted by. One narrows three rules for
`tests/**`; the other bans two attributes that no selected rule can reliably
see.

### `[tool.ruff.lint.per-file-ignores]`

There is exactly one pattern, and it is `tests/**`:

```toml
[tool.ruff.lint.per-file-ignores]
"tests/**" = ["B011", "ANN", "DTZ001"]
```

Nothing under `src/` is exempted from anything. The legacy per-package
exemption list is empty — every entry was deleted in the commit that repaired
or removed its package, and the reasoning is in
[ADR 0014](../adr/0014-exemption-lists-are-empty-and-must-stay-falsifiable.md).
The block is kept empty rather than deleted so that adding a `src/` exemption
is a visible decision in review.

**`ANN`** — flake8-annotations, off for tests only. Under
`[tool.mypy] files = ["src/redstring"]` the type checker never looks at
`tests/`, so an annotated test function is checked by nothing; requiring
`-> None` on every one of them produces thousands of findings and no signal.
The rule stays on for `src/`, where it is the fast front end to mypy
`--strict`'s `disallow_untyped_defs`.

**`B011`** — bugbear's `assert False`. Under `python -O` an `assert` is
compiled away, so `assert False` in shipped code silently becomes a no-op and
bugbear wants `raise AssertionError` instead. Tests are the one place the
concern does not apply: pytest never runs optimised, and the suite's entire
vocabulary is `assert`. This is the same reasoning that exempts `tests/` from
bandit (whose B101 flags `assert` outright).

**`DTZ001`** — `datetime()` called without `tzinfo`. `DTZ` is selected because
tz-awareness is a domain invariant here: a naive datetime does not raise, it
compares wrongly. Tests need the naive form for two distinct reasons, and
neither is a relaxation of the invariant:

- `hypothesis`'s `st.datetimes(min_value=..., max_value=...)` **requires naive
  bounds**. `tests/compliance/strategies.py` and
  `tests/unit/domain/test_temporal.py` both build strategies this way
  (`min_value=datetime(1800, 1, 1)`, `min_value=datetime(1, 1, 1)` and
  similar), and there is no tz-aware spelling of those arguments.
- A strategy that *generates* naive datetimes is how the rejection path gets
  tested at all. Proving the domain refuses a naive value means constructing
  one.

The ignore is `DTZ001` specifically — the bare-constructor rule — and not the
`DTZ` family. `DTZ003` (`datetime.utcnow()`) stays enforced in `tests/` as
well as `src/`, which is the point of listing the code rather than the prefix:
the two exemptions above are about *constructing* a known-naive value on
purpose, not about the deprecated APIs that hand you one by accident.

### `[tool.ruff.lint.flake8-tidy-imports.banned-api]`

`TID` is selected largely to carry these two entries:

```toml
"datetime.datetime.utcnow".msg = "Naive and deprecated since 3.12 — use datetime.now(UTC)."
"datetime.datetime.utcfromtimestamp".msg = "Naive — use datetime.fromtimestamp(ts, UTC)."
```

Both functions return a **naive** datetime whose value is UTC — the worst
combination available, because the value is right and the type does not say
so. Both are deprecated from Python 3.12.

The reason this is a banned *attribute* rather than a reliance on `DTZ003` is
recorded in the config and is worth repeating: **DTZ003 catches the call form,
not a bare reference.** `datetime.utcnow()` is reported; `default_factory=
datetime.utcnow` is not, and the second spelling is how it reached this
codebase (BACKLOG B29, since closed). A pydantic field defaulted that way
produces a naive timestamp on every model construction, at a site where no
call appears in the source at all. Banning the attribute catches both shapes.

The ban applies everywhere — there is no `tests/**` carve-out for it, and the
`DTZ001` comment above says so explicitly. Nothing in `src/` or `tests/`
currently references either name.

Because these are configured as banned *APIs* rather than as a lint rule of
their own, the `msg` is the whole error text a violation prints. Each one
names the replacement, which is the difference between a finding someone fixes
and a finding someone `# noqa`s.

## ruff flake8-type-checking `runtime-evaluated-base-classes`

`TC` (flake8-type-checking) reports an import that is used only inside
annotations and offers to move it into an `if TYPE_CHECKING:` block. That is a
correct and useful rewrite for ordinary classes — the package this repo uses it
in most is `ports/`, where a Protocol's parameter types cost an import at
runtime for nothing. It is *wrong* for a pydantic model, and wrong in a way no
other gate catches.

`[tool.ruff.lint.flake8-type-checking]` is the one setting that keeps the
family safe here:

```toml
[tool.ruff.lint.flake8-type-checking]
runtime-evaluated-base-classes = [
    "pydantic.BaseModel",
    "eventsource.domain.tenant_events.TenantDomainEvent",
]
```

A class whose base is listed here is treated as *runtime-evaluated*: ruff stops
reporting TC001/TC002/TC003 on the imports feeding its field annotations,
because removing them would break the class rather than tidy it.

### Why pydantic needs the exemption

Every module in the package carries `from __future__ import annotations`, so
annotations are strings and most of them are never evaluated. Pydantic is the
exception: it resolves a model's field annotations when it **builds the
schema**, which happens on first use of the model, not at import.

So an annotation-only import moved into a `TYPE_CHECKING` block leaves the
module importable and the model broken:

```
>>> import redstring.events.merge     # succeeds
>>> MergeUndone(...)                   # PydanticUserError: not fully defined
```

That is the measured outcome, not a hypothesis. Moving `from uuid import UUID`
into a type-checking block in `src/redstring/events/merge.py` — a fix ruff
offers — kept the import working and failed **23 tests** with
`PydanticUserError: MergeUndone is not fully defined`, because
`merge_event_id: UUID` is a field.

The general lesson is worth carrying past this setting: **an import smoke test
passes; only using the model catches it.** A verification step that stops at
"the module imports" is blind to this entire class of defect.

### Why the second entry exists

`pydantic.BaseModel` alone is not enough, because **ruff matches the base class
as written in the file, not through the MRO.** The events in
`src/redstring/events/` declare `TenantDomainEvent`:

```python
@register_event
class EntitiesMerged(TenantDomainEvent): ...
```

`TenantDomainEvent` is itself a pydantic model, but ruff cannot see that — it
reads the source text of the base, not its ancestry. Every event class in the
package therefore collected TC001/TC002/TC003 findings on its field
annotations until the fully qualified `eventsource.domain.tenant_events.
TenantDomainEvent` was added alongside `pydantic.BaseModel`.

Two practical consequences:

- **Names must be fully qualified as ruff resolves them.** `TenantDomainEvent`
  on its own would not match.
- **A new pydantic base class needs an entry.** Any base introduced between
  `BaseModel` and a concrete model — a shared mixin, a second event base — is
  invisible to ruff until it is named here, and the symptom is a TC finding
  whose suggested autofix breaks the model at use.

### How this relates to the exemption lists

This is a *configuration* entry, not an exemption. Nothing is silenced: TC
stays enabled for every file, including the events package, and it still
reports annotation-only imports on classes that are not runtime-evaluated. The
setting corrects ruff's model of the code rather than excusing the code from a
rule — which is why it lives here and not in `per-file-ignores`, whose only
entry is `tests/**`.

It is also the origin story for
[ADR 0014](../adr/0014-exemption-lists-are-empty-and-must-stay-falsifiable.md).
The `events/` package had a `per-file-ignores` entry of exactly
`["ANN", "TC"]`, and measuring what it hid was misleading in the way the ADR
describes: `ruff check --select ANN,TC src/redstring/events/` printed
`All checks passed!` **unconditionally**, since `per-file-ignores` applies on
top of `--select`. Deleting the entry and running the configured gate surfaced
ten findings, nine of which were this misconfiguration rather than debt.

An exemption can therefore hide a *misconfiguration* rather than technical
debt, and then absorb it indefinitely — a better argument for removing
exemptions promptly than any amount of accumulated strictness.

## mypy configuration

`[tool.mypy]` in `pyproject.toml` is six keys and one override block. The hook
runs `uv run mypy` with `pass_filenames: false`, so every setting here decides
both what the gate checks and what a bare `uv run mypy` checks — the two are
the same command.

```toml
[tool.mypy]
python_version = "3.13"
files = ["src/redstring"]
strict = true
warn_unreachable = true
warn_return_any = true
disallow_untyped_defs = true
plugins = ["pydantic.mypy"]
```

### `files = ["src/redstring"]`

The package, and only the package. `tests/` is not type-checked at all, which
is what makes ruff's `ANN` per-file ignore for `tests/**` coherent rather than
a hole: an annotation on a test function would be read by nothing, so
requiring one produces findings with no consumer. See
[per-file-ignores](#ruff-per-file-ignores-and-banned-api-tests-annb011dtz001-datetimeutcnow-ban).

Because the hook passes no filenames, `files` is the *only* thing that decides
scope. A one-line edit type-checks the whole package — which is the correct
cost, since a changed return type breaks callers in files nobody staged.

### `python_version = "3.13"`

Matches `requires-python = ">=3.13"` and ruff's `target-version = "py313"`.
mypy resolves version-conditional branches and standard-library signatures
against this number, so leaving it behind the real floor makes the checker
reason about a Python the package does not run on.

### `strict = true`

Turns on the whole strict family in one key — `disallow_any_generics`,
`disallow_untyped_calls`, `disallow_incomplete_defs`, `no_implicit_optional`,
`warn_redundant_casts`, `warn_unused_ignores`, `check_untyped_defs`,
`strict_equality` and the rest. Two of those are worth naming for how they
interact with the rest of this page:

- **`warn_unused_ignores`** makes a `# type: ignore` that no longer suppresses
  anything a *failure*. It is the same instinct as
  [ADR 0014](../adr/0014-exemption-lists-are-empty-and-must-stay-falsifiable.md)'s rule about
  exemption lists — a suppression that has outlived its cause must say so
  rather than sit there passing.
- **`strict_equality`** rejects a comparison between types that can never be
  equal, which is a real defect class in a codebase whose domain keys are
  wrapped ids and enums.

`disallow_untyped_defs = true` is set explicitly even though `strict` already
implies it. The redundancy is harmless and states the intent that survives if
`strict` is ever narrowed.

### `warn_unreachable = true`

Reports code mypy proves cannot execute. Not in the strict family, and enabled
separately because unreachable code here is usually a *type* mistake wearing a
dead-branch costume: a `None` check after a non-optional narrowing, an
`isinstance` against a type the signature already excludes, an `else` after an
exhaustive `Literal` match. Each of those passes every other gate — ruff sees
valid syntax, the tests never enter the branch, and coverage counts it as an
uncovered line among many.

It also pairs with the mutation-testing note in `CLAUDE.md`: a branch no test
can reach is a branch whose mutants are unkillable by construction. Failing on
it at type-check time is cheaper than classifying survivors later.

### `warn_return_any = true`

Fails when a function annotated with a concrete return type actually returns
`Any`. Also outside `strict`, and the reason it matters here is the untyped
dependency below: the moment a value crosses out of `asyncpg`, it is `Any`,
and without this key it would flow into a declared `list[Entity]` return with
no complaint. The annotation would then be documentation rather than a
checked claim — the same failure shape as a test whose expectation is written
in terms of the thing under test.

### `plugins = ["pydantic.mypy"]`

Pydantic 2.13 types well on its own; the plugin adds checks plain mypy cannot
perform. `[tool.pydantic-mypy]` configures it:

```toml
[tool.pydantic-mypy]
init_typed = true
init_forbid_extra = true
warn_required_dynamic_aliases = true
warn_untyped_fields = true
```

**`init_typed`** is the load-bearing one. It synthesises each model's
`__init__` from its field types, so a constructor *call* is checked against
them. Without it `Entity(confidence="high")` type-checks clean and fails only
at runtime — which defeats the point of typing a domain model whose whole job
is enforcing invariants. `init_forbid_extra` rejects a keyword no field
declares (a typo'd field name is otherwise silently dropped);
`warn_untyped_fields` catches a bare `x = 3` in a model body, which pydantic
treats as a class attribute rather than a field.

This is the second place pydantic's runtime behaviour has to be told to the
tooling. The first is ruff's `runtime-evaluated-base-classes` — see
[that section](#ruff-flake8-type-checking-runtime-evaluated-base-classes) —
and both exist because a pydantic model does more at runtime than its source
text suggests.

### The `asyncpg` override

One `[[tool.mypy.overrides]]` block, and it is the only relaxation anywhere in
the mypy configuration:

```toml
[[tool.mypy.overrides]]
module = ["asyncpg.*"]
ignore_missing_imports = true
```

`asyncpg` ships no `py.typed` marker, so mypy cannot see its types at all and
`--strict` fails on the import rather than on any use. The alternative is a
stub package that would have to track asyncpg's releases; the scope that buys
is one module — `redstring.vector.adapters.pgvector` is the only importer in
`src/` — and that module's own signatures are fully annotated, so the
untyped surface stops at the adapter boundary.

Note what the override does *not* do: it silences the missing-import error,
not the values. Everything crossing out of `asyncpg` is `Any`, and
`warn_return_any` above is what stops that `Any` being laundered into a
declared return type. The two settings are a pair — removing either one makes
the adapter's annotations unchecked assertions.

Every other third-party dependency is typed or has a stub in `[dev]`
(`types-dateparser`, `types-python-dateutil`, `types-pyyaml`), which is why
the override list has one entry rather than several.

### There is no `exclude`

The key is **deleted**, not empty. Slice 10 emptied it by fixing the last
fourteen findings in `extraction/` rather than by deleting the package, and
the empty key was then removed on the reasoning in
[ADR 0014](../adr/0014-exemption-lists-are-empty-and-must-stay-falsifiable.md):
an exclusion over an empty set excludes nothing, and a staleness guard written
over it would pass vacuously. `--strict` therefore covers every module under
`src/redstring`, and re-adding an exclusion is a visible decision in review
rather than an edit to an existing list.

This asymmetry with ruff is deliberate. ruff's `per-file-ignores` is *kept*
with its one `tests/**` entry, because it is a live mechanism with a current
member; mypy's `exclude` had no members left. Empty-and-kept versus deleted is
the choice [ADR 0014](../adr/0014-exemption-lists-are-empty-and-must-stay-falsifiable.md)
asks you to make explicitly, and the two files show the
two answers.

One consequence for hand-run diagnosis, and it is the mypy spelling of the
`--force-exclude` hazard above: **naming files on the command line bypasses
`exclude`.** `uv run mypy src/redstring/foo.py` is a different check from the
configured run, and it would remain different if an exclusion were ever added
back. The measurement that means anything is the configured invocation —
`uv run mypy`, no arguments, exactly as the hook issues it.

## bandit configuration

`[tool.bandit]` in `pyproject.toml` is a single key:

```toml
[tool.bandit]
exclude_dirs = ["tests", ".venv", "build", "dist"]
```

No `skips`, no `tests`, no `targets`, and no severity or confidence floor — so
bandit runs its **default profile in full** over whatever it decides to scan,
and reports every finding at every severity. There is not one `# nosec` comment
anywhere in `src/` or `tests/`.

The hook is the fourth of the six `local` hooks:

```yaml
- id: bandit
  entry: uv run bandit -c pyproject.toml -q
  language: system
  types: [python]
  exclude: ^tests/
  require_serial: true
```

`-c pyproject.toml` is what makes bandit read `[tool.bandit]` at all (the
`bandit[toml]` extra in `[dev]` supplies the TOML reader). `-q` suppresses the
per-file progress banner so the hook prints findings and nothing else.
`pass_filenames` is left at its default, so bandit is invoked on the staged
paths — this is a filename-passing hook, unlike `mypy`, `lint-imports` and the
ratchet. `types: [python]` is the singular key, so `.pyi` stubs never reach it.

### Src-only is asserted twice

Tests are excluded by two independent mechanisms:

| Mechanism | Where | What it stops |
| --- | --- | --- |
| `exclude: ^tests/` | `.pre-commit-config.yaml` | staged test files being *passed* as arguments |
| `exclude_dirs = ["tests", …]` | `[tool.bandit]` | bandit *walking* into a test directory itself |

The duplication is not redundancy. The hook's `exclude` only governs the
argument list `pre-commit` builds; `exclude_dirs` governs what bandit discovers
when it is given a directory (`uv run bandit -c pyproject.toml -r src`). Keeping
both means the hook and a hand-run recursive scan agree about scope, and that
neither one alone is what makes tests exempt.

Tests are exempt because bandit's checks are about *shipped* code, and two of
them fire on the entire idiom of a test suite:

- **B101 (`assert_used`)** — `assert` is compiled away under `python -O`, so it
  is a real finding in a library and the whole vocabulary of a suite. This is
  the same reasoning behind ruff's `B011` per-file ignore for `tests/**`; see
  [per-file-ignores](#ruff-per-file-ignores-and-banned-api-tests-annb011dtz001-datetimeutcnow-ban).
- **B404 / B603 / B607 (`subprocess`)** — the suite shells out deliberately, to
  drive the tools it is testing.

Measured: `uv run bandit -r tests` reports **1913 B101 findings**, three B311,
and five subprocess findings. Scanning tests would produce a gate that is
always red, which is a gate nobody reads.

### What bandit finds in `src/` when it looks

A recursive scan with the configuration bypassed reports four kinds of finding
across the package:

- **B101** ×2 in `src/redstring/llm/adapters/fake.py` — internal invariants in
  a test double that happens to live under `src/`.
- **B311** ×1 in `src/redstring/llm/retry.py` — `random` for retry jitter,
  which is not a cryptographic use.
- **B608** ×5 in `src/redstring/vector/adapters/pgvector.py` — SQL built by
  string composition, flagged on shape rather than on a proven injection path.

All are Low severity. None is currently reported by the gate, for the reason
below.

### `exclude_dirs` is substring matching, and `"build"` matches `redstring`

**As configured, the bandit hook scans nothing.** This is not a claim about
scope; it is measurable:

```
$ uv run bandit -c pyproject.toml -r src
Test results:
        No issues identified.
Code scanned:
        Total lines of code: 0

$ uv run bandit -r src            # same tool, no -c
>> Issue: [B101:assert_used] ...
>> Issue: [B311:blacklist] ...
>> Issue: [B608:hardcoded_sql_expressions] ...
        Total lines of code: 9766
```

The cause is in `bandit.core.manager._is_file_included`, which applies
`exclude_dirs` as a glob match **and** as a plain substring test:

```python
if not _matches_glob_list(path, excluded_path_strings) and not any(
    x in path for x in excluded_path_strings
):
```

`"build"` is a substring of `redstring`. Every path under
`src/redstring/` therefore contains an entry of `exclude_dirs` and is
excluded, whether the path is given relative or absolute, in the main checkout
or in a worktree. Removing the single entry `"build"` from `exclude_dirs`
restores the scan; removing `".venv"` or `"dist"` changes nothing.

Two things this is worth reading as:

- **It is exactly the failure shape [ADR 0014](../adr/0014-exemption-lists-are-empty-and-must-stay-falsifiable.md)
  is about**, arriving from a new direction. There the hazard was an exemption
  list whose entries no longer matched a real file and passed silently; here it
  is an exemption entry that matches *far more* than it names, and also passes
  silently. Both are invisible because a clean run and an inert run print the
  same thing. Bandit gives one extra tell — `Total lines of code: 0` — but `-q`
  is not what hides it, and the hook only prints findings anyway.
- **A passing check you have never seen fail is not yet evidence.** Bandit has
  been green since it was added; proving it can fail costs one command
  (introduce a `subprocess.run(..., shell=True)` under `src/` and watch the
  hook complain) and would have caught this the day the key was written.

Until the entry is fixed, treat a green bandit hook as no information. The
diagnostic invocation that does mean something is `uv run bandit -r src`
without `-c` — which is, in the general case, the inverse of the rule
everywhere else on this page, and only correct because the configured
invocation is currently the broken one.

## import-linter contract

`[tool.importlinter]` in `pyproject.toml` declares one contract, and the
`lint-imports` hook runs it with `pass_filenames: false` — an import contract
is a property of the whole package, and a violation is a *pair* of modules of
which only one is likely staged.

```toml
[tool.importlinter]
root_packages = ["redstring"]
include_external_packages = false

[[tool.importlinter.contracts]]
name = "Layered architecture"
type = "layers"
containers = ["redstring"]
exhaustive = true
```

A run reports what it looked at, which is the number to sanity-check before
believing a green result:

```
Analyzed 81 files, 220 dependencies.
Layered architecture KEPT
Contracts: 1 kept, 0 broken.
```

### `root_packages` and `include_external_packages`

`root_packages = ["redstring"]` is the graph import-linter builds: the
package, and nothing else. `include_external_packages = false` means
third-party imports are not even nodes in that graph.

That second key is a statement of what this tool can and cannot do for you.
**`lint-imports` sees first-party imports only**, so no contract expressible
here can catch `import langchain_openai` appearing in
`src/redstring/extraction/`. The layer rules keep `extraction` off
`redstring.llm.adapters`; they say nothing about the package that adapter
wraps. `tests/unit/llm/test_port_does_not_leak.py` is what covers the external
half — it parses every module under `src/` and fails on a third-party leak
outside the adapter package.

The general rule: **any dependency the architecture deliberately confines to
one module needs a second check of that kind.** The contract alone will not do
it, and the gap is silent rather than loud.

### `containers` and layer names

`containers = ["redstring"]` makes every layer name *relative to the
container*, which is why the entries are bare (`domain`, not
`redstring.domain`). One container, one package.

A layer may be a subpackage or a plain module — `composition` is
`src/redstring/composition.py`, a single file, and sits on the top layer on
its own.

### `exhaustive = true`

Every child of the container must appear on some layer. A new top-level module
or package under `redstring` is a **contract failure** until it is placed
deliberately:

```
$ mkdir src/redstring/throwaway && touch src/redstring/throwaway/__init__.py
$ uv run lint-imports
Layered architecture BROKEN
- redstring.throwaway
(Since this contract is marked as 'exhaustive', every child of every container
 must be defined in the layers.)
```

That transcript is the point of recording this section. `exhaustive` has
caught zero real violations, and a check that has never been seen to fail is
indistinguishable from an option that is inert — the same reasoning
[ADR 0014](../adr/0014-exemption-lists-are-empty-and-must-stay-falsifiable.md)
applies to exemption lists, and the same failure mode the
[bandit section](#bandit-configuration) documents as a live bug. Slice 9 ran
the experiment above by adding a throwaway package, watching the contract
break, and removing it; the command is cheap enough to repeat whenever the
option's value is in doubt.

There is no `exhaustive_ignore`. The twelve layer entries below name every
child of `redstring` exactly once, so the set is closed and adding to it is a
visible decision in review.

### The layer order

Highest to lowest, as declared:

```
composition
extraction : consolidation : temporal : graph : vector : llm   (siblings)
projections
aggregates
events
ports
domain
```

The rules a `layers` contract enforces are two:

- **A lower layer may not import a higher one.** `domain` may not import
  `ports`; `ports` may not import `events`; nothing below `projections` may
  import a projection.
- **Siblings on the same line may not import each other.** The `:` separator
  is what makes `extraction`, `consolidation`, `temporal`, `graph`, `vector`
  and `llm` mutually independent, not merely peers.

A higher layer importing a lower one is always allowed, which is why the order
is the dependency direction and not a diagram of "importance".

`pyproject.toml` carries the reasoning for each placement inline, and the
comments are load-bearing rather than decorative — several of them record why
a *plausible* alternative placement is wrong:

- **`composition` is the top layer and holds one module.** `extraction` may
  not import `projections` — that separation is what stops a store reference
  growing back into the pipeline — and yet something has to hold both, or the
  library ships two halves and a diagram. `build_graph` is that something. A
  second module wanting in here should have to say what it composes.
- **`llm` is a sibling of `extraction`, not beneath it.** Siblings may not
  import each other, so extraction can reach only `ports.llm_provider`.
  Putting `llm` on a lower layer would let extraction import the LangChain
  adapter directly and undo the port.
- **`consolidation` is a sibling for the same structural reason.** It needs
  nothing from extraction — the tie-break both use moved down to
  `domain.preference` when consolidation became its third caller — and placing
  it *above* extraction would let it reach `mapping.py`, which is how a second
  entity-id scheme gets born.
- **`temporal` likewise.** It reads entities through `ports.graph_store` and
  computes over `domain.interval`. Above `extraction` it could reach
  `mapping.py`, and the temptation there is specific: inferred edges would
  acquire a path into `DocumentExtracted`, which is exactly the persistence
  decision `temporal/inference.py` argues against.
- **`projections` read the ports and the event schema and write nothing
  back.** Nothing below them may import a projection, and they may not import
  an adapter.
- **`aggregates` are the write model**, importing `events` and `domain`.
- **`ports` sit directly above `domain` and below every adapter** — an adapter
  such as `graph.adapters` may import its port, never the reverse.

### There is no `services` layer

It was the top layer until slice 9 deleted it, along with `models`, `db` and
`schemas`. The write model is `aggregates` + `events`, the read model is
`projections`, and persistence is the two ports; there is no ORM and no
session for a layer to be built around. `cache`, `config` and `context` left
the line in slice 10 with their modules — a settings object, a module-level
Redis singleton and a re-export shim, none with a caller.

Adding any of these names back needs an argument, and `exhaustive = true` is
what forces the argument to happen: a re-introduced package cannot sit
unplaced.

### Keeping the two copies in step

The layer list appears in three places — `pyproject.toml` (authoritative, with
the inline reasoning), `CLAUDE.md`, and this page. `pyproject.toml` is right
when they disagree. A stale layer diagram in binding instructions is worse
than no diagram: it sends the next author to a package that does not exist.

Note also that the hook's filter is `files: ^(src/|pyproject\.toml$)`, so
editing the contract itself re-runs it against the existing code — changing
the *rules* is checked, not only changing the code.

## pytest configuration

`[tool.pytest.ini_options]` in `pyproject.toml` is the whole pytest
configuration — there is no `pytest.ini`, no `setup.cfg`, and `tests/conftest.py`
adds reporting rather than settings.

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = "test_*.py"
python_classes = "Test*"
python_functions = "test_*"
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
```

`addopts` and `markers` sit in the same table and are covered in
[the next section](#pytest-addopts--m-not-accuracy-and-not-integration) and
[Markers](#markers-unit-integration-accuracy-slow).

### `testpaths = ["tests"]`

Where pytest starts collecting when no path argument is given — so
`uv run pytest` and `uv run pytest tests` are the same run. `src/` is never
collected: there are no doctests in the gate and no test modules living beside
the code.

It is a *default*, not a restriction. A path on the command line overrides it
entirely, which is what makes `uv run pytest -m accuracy tests/accuracy/`
work, and what lets the mutation runners point at a subtree.

### `python_files`, `python_classes`, `python_functions`

All three are pytest's defaults, stated explicitly. Stating them is not
decoration here — two deliberate structures in this suite depend on them, and
both would break silently under a looser pattern.

**`python_files = "test_*.py"` is what keeps the shared compliance suites from
being collected.** `tests/compliance/` holds `graph_store.py`,
`vector_store.py`, `cache.py` and `strategies.py`; the first three define
abstract suite classes (`GraphStoreCompliance` and friends) whose test methods
are inherited by a concrete subclass per adapter. The files do not match
`test_*.py`, so pytest never collects the abstract base directly — it runs
only through the subclasses, which supply a real store via `new_store()`. Were
the pattern widened to include `*_store.py`, the base classes would be
collected with no adapter and every one of them would error.

The same mechanism holds for the non-test helpers that live inside test
packages — `tests/unit/projections/log_builder.py`,
`tests/unit/consolidation/oracle.py`. The oracle in particular is the
*independent* expectation described in
[`.claude/rules/testing.md`](https://github.com/tyevans/redstring/blob/main/.claude/rules/testing.md); it is imported by
tests, never collected as one.

**`python_classes = "Test*"` is load-bearing in the same way, one level down.**
`tests/unit/graph/test_memory_store.py` subclasses the compliance suite twice
in a file that *is* collected:

```python
class TestMemoryStore(GraphStoreCompliance):
    async def new_store(self) -> GraphStore:
        return InMemoryGraphStore()


class _DisposeRecorder(GraphStoreCompliance):
    """Not collected -- the name does not match `python_classes = "Test*"`."""
```

`_DisposeRecorder` exists so a test can assert the suite calls `dispose` for
every store it hands out. It inherits every compliance test, and it defines
`__init__`. Under a wider `python_classes` it would be collected — re-running
the entire suite for no added coverage, and hitting pytest's
"cannot collect test class with `__init__` constructor" warning on the way.
The leading underscore is the mechanism, and it only works because the pattern
is anchored.

So: **a change to any of these three patterns is a change to what the suite
runs, not a formatting preference.** They are written out so that widening one
is a visible edit rather than an inherited default nobody looked at.

### `asyncio_mode = "auto"`

`pytest-asyncio` is pinned to `==1.3.0` in `[dev]` — the only pinned tool in
the project, because its mode and loop-scope semantics have moved between
releases and the settings below are written against this one.

In `auto` mode every `async def` test is collected and run as an asyncio test
with no decorator. That matters because most of this library is async: the two
store ports, the LLM provider port, the projections and the pipeline all have
`async` methods, so nearly every test in the suite is a coroutine. Requiring
`@pytest.mark.asyncio` on each would be several hundred decorators whose
absence fails in the most confusing way available — pytest collects the
coroutine, never awaits it, warns, and **passes**.

`auto` also applies to the inherited compliance methods, which is what lets an
abstract suite define `async def test_...` in `tests/compliance/` and have it
run correctly from a subclass in a different package.

Fourteen explicit `@pytest.mark.asyncio` marks remain — a `pytestmark` in
`tests/unit/temporal/test_query.py` and thirteen method-level marks in
`tests/unit/llm/test_retry.py`. Under `auto` they are redundant rather than
wrong; the mode makes the mark a no-op, not an error.

### `asyncio_default_fixture_loop_scope = "function"`

Sets the default event-loop scope for **async fixtures**. `function` gives
each test its own loop, and each async fixture is torn down with the test that
used it.

This is `pytest-asyncio`'s own recommended value, and leaving it unset emits a
deprecation warning about the unset default rather than choosing quietly. It
is also the value that matches the rest of this suite's isolation posture:

- `pytest-randomly` shuffles test order, so anything surviving between tests is
  an order-dependent bug waiting to surface at a different seed.
- The ratchet runs under `pytest-xdist` (`-n auto`), where tests are
  distributed across worker processes and a shared loop would be shared only
  within a worker — making any dependence on it non-reproducible.
- A wider scope (`session`, `module`) shares one loop across tests, and with it
  anything the loop holds: connection pools, pending tasks, cancelled-but-not-
  awaited coroutines. A test that leaves a task pending would then fail a
  *later* test.

The related trap is not covered by this setting and is worth naming next to
it: **`hypothesis` runs every generated example against a single
function-scoped fixture.** The fixture is created once for the whole `@given`,
so example 7 sees whatever examples 1–6 left behind — `function` scope is
per-*test*, not per-example. This produced an intermittent `MissingEntityError`
in about one run in three, and suppressing the health check that reports it is
what hid the cause. Build the rig inside the test instead; see
[`.claude/rules/testing.md`](https://github.com/tyevans/redstring/blob/main/.claude/rules/testing.md).

There is no `asyncio_default_test_loop_scope` key, so test-level scope is
`pytest-asyncio`'s own default (`function`) as well.

## pytest addopts: `-m "not accuracy and not integration"`

One `addopts` entry, and it is the line that decides what "the suite" means
everywhere else on this page:

```toml
addopts = ["-m", "not accuracy and not integration"]
```

That is the whole of it — no `-q`, no `--strict-markers`, no coverage flags.
Every `uv run pytest` in this repo, and the ratchet's own invocation, runs with
this marker expression applied.

### What it excludes, and why

Two marked suites, both of which need something the commit gate cannot assume:

| Marker | Needs | Where it lives |
| --- | --- | --- |
| `integration` | the backends in `docker-compose.test.yml`, or a live LLM endpoint | `tests/integration/` |
| `accuracy` | a live LLM, and a judgement about extraction *quality* | `tests/accuracy/` |

Excluding them is what keeps `git commit` infra-free and fast. A gate that
fails because Docker is not running is a gate people learn to bypass, and
`--no-verify` skips the entire pipeline rather than the one hook that
complained — see [Pre-commit configuration](#pre-commit-configuration).

Note the shape of the expression: it deselects by **marker**, not by path.
`tests/integration/` is a convention, not the mechanism, and a
`@pytest.mark.integration` on a test anywhere else is deselected just the same.
The current effect is measurable —

```
$ uv run pytest --collect-only -q
1761/1958 tests collected (197 deselected)
```

— so roughly a tenth of the suite does not run on commit.

`tests/accuracy/` currently holds only `__init__.py`, so `-m accuracy` collects
**zero tests**. The marker is declared and excluded for a suite that has not
been written; that absence is a known gap tracked as `BACKLOG.md` B12, not a
licence to assume extraction quality is measured. Nothing in this repo can tell
you whether a change made extraction better or worse — only whether it stayed
correct.

### A `-m` on the command line replaces this one

`addopts` is prepended to the argument list, and pytest's last `-m` wins. So
naming a marker explicitly *overrides* the exclusion rather than intersecting
with it:

```
uv run pytest -m integration                 # runs the 197, excludes nothing else
uv run pytest -m accuracy tests/accuracy/    # needs a live LLM
```

This is why the excluded suites are reachable at all, and it is also the trap:
`uv run pytest -m slow` does **not** mean "slow, but still not integration" —
it means every `slow` test including integration ones. To narrow rather than
replace, write the whole expression: `-m "slow and not integration"`.

Combining an explicit `-m` with a path is belt and braces (the path restricts
collection, the marker restricts selection), which is the form the inline
comment in `pyproject.toml` and `tests/conftest.py` both use.

### Deselection is silent, so `conftest.py` makes it loud

pytest reports a deselection as a bare `197 deselected` with no indication of
*what* was removed or how to run it. `tests/conftest.py` adds a terminal
summary that says both:

```
-------------------------- not run in this invocation --------------------------
   197 'integration' tests -- uv run pytest -m integration    # needs docker-compose.test.yml
```

It is implemented with `pytest_deselected` (counting items by marker) and
`pytest_terminal_summary` (printing the count and the command). Nothing is
skipped at collection time — the file adds reporting, not configuration.

The docstring records why it exists, and it is the strongest argument on this
page for treating a green default run as partial evidence: slice 4 landed a
Neo4j `GraphStore` whose tests are **all** `integration`-marked, and a
cosmic-ray mutant left in its source passed the full default suite because not
one line of that adapter ever ran. The summary cannot make the gate cover that
code — only a combined coverage run can, `BACKLOG.md` B10a — but it stops the
omission being silent.

Two consequences worth carrying:

- **Coverage is measured over the default run**, so the ratchet's number
  describes the code the deselected suites do not exclusively own. See
  [The coverage ratchet contract](#the-coverage-ratchet-contract-scriptscoverage_ratchetpy).
- **Mutation testing inherits the same blind spot.** `cosmic-ray.toml`'s
  `test-command` is `uv run pytest -x -q --no-header -p no:randomly tests/unit`
  and mutmut's `runner` is the same command without a path, so an adapter whose
  only tests are integration-marked has no killing tests at all — every mutant
  in it survives, or, if the environment is incomplete, every mutant appears to
  die. See
  [Verifying a mutation run before trusting it](#verifying-a-mutation-run-before-trusting-it).

The procedures for actually running the excluded suites — bringing the backends
up, pointing the LLM tests at an endpoint — are in
[How to run the integration and mutation suites](../how-to/run-integration-and-mutation-suites.md)
and summarised in [Running the excluded suites deliberately](#running-the-excluded-suites-deliberately).

## Markers: `unit`, `integration`, `accuracy`, `slow`

Four markers are declared in `[tool.pytest.ini_options]`, immediately below
`addopts`:

```toml
markers = [
    "unit: unit tests",
    "integration: integration tests",
    "accuracy: extraction accuracy tests",
    "slow: tests that take a long time to run",
]
```

Declaring a marker does two things and only two: it registers the name so
`--strict-markers` would accept it, and it supplies the description
`uv run pytest --markers` prints. It does **not** apply the marker to
anything. What each one actually selects is decided by where
`@pytest.mark.<name>` appears in `tests/`, and the four differ enormously on
that measure.

| Marker | Applied to | Effect on the gate |
| --- | --- | --- |
| `integration` | 197 tests across 5 files | deselected by `addopts` |
| `accuracy` | **nothing** | deselected by `addopts`; selects zero tests |
| `unit` | 7 tests in 1 file | none — `unit` is not in the marker expression |
| `slow` | **nothing** | none |

### `integration` — the only marker doing work

The one marker that changes what runs. It is applied in five places, four of
them as a module-level `pytestmark` covering the whole file:

- `tests/integration/graph/test_neo4j_store.py`
- `tests/integration/vector/test_pgvector_store.py`
- `tests/integration/llm/test_live_endpoint.py`
- `tests/integration/llm/test_live_pipeline.py`
- `tests/integration/test_wheel_contents.py` — a single
  function-level `@pytest.mark.integration`, not a file-wide one

The first two need the backends in `docker-compose.test.yml`; the two `llm`
files need a live OpenAI-compatible endpoint; the wheel test builds and
installs a wheel, which needs neither backend but does need time and a network
-capable build.

`tests/integration/` is a **convention, not the mechanism.** The deselection is
by marker, so an `@pytest.mark.integration` on a test under `tests/unit/`
would be excluded identically, and an unmarked test placed in
`tests/integration/` would run on every commit. The wheel test is the reminder
that the two do not have to coincide — it lives there by subject matter and
carries its own mark.

The size of what this removes is measurable:

```
$ uv run pytest --collect-only -q -m integration
197/1958 tests collected (1761 deselected)
```

Roughly a tenth of the suite, including every test of the Neo4j and pgvector
adapters. That is the blind spot `tests/conftest.py`'s terminal summary exists
to announce; see
[addopts](#pytest-addopts--m-not-accuracy-and-not-integration).

### `accuracy` — declared, excluded, and empty

`grep -rn "mark.accuracy" tests/` returns nothing. `tests/accuracy/` contains
`__init__.py` and no test module, so `-m accuracy` collects **zero tests**.

The marker is declared and deselected for a suite that has not been written.
Both halves of that are deliberate — the exclusion is in place so the suite can
land without changing the gate — but the consequence is worth stating plainly
rather than inferring from an empty directory: **nothing in this repository
measures extraction quality.** The gates tell you a change stayed correct, not
whether it made extraction better or worse. The gap is tracked as `BACKLOG.md`
B12.

An empty marker is also the exact shape
[ADR 0014](../adr/0014-exemption-lists-are-empty-and-must-stay-falsifiable.md)
warns about, arriving from a third direction: `uv run pytest -m accuracy` exits
green, and a green run over zero tests is indistinguishable from a green run
over a suite. Read the collected count, not the exit code.

### `unit` — applied seven times, and selects nothing useful

`@pytest.mark.unit` appears on seven tests, all in
`tests/unit/test_jellyfish_import.py`. Every other test in `tests/unit/` — the
overwhelming majority of the suite — is unmarked.

It is therefore **not** the complement of `integration`. `-m unit` runs seven
tests, not 1761. The way to run the default suite is to name no marker at all
and let `addopts` apply, which is what "the gate" means everywhere on this
page:

```
uv run pytest                      # 1761 tests: everything not integration/accuracy
uv run pytest -m unit              # 7 tests, all in one file
```

The mark on those seven is not doing selection work; it is documentation on a
file whose subject (a third-party import's behaviour) makes its category
non-obvious. Treat `unit` as effectively unused, and do not reach for it when
you mean "the fast suite".

### `slow` — declared and never applied

`grep -rn "mark.slow" src/ tests/ scripts/` returns nothing. No test carries
it, `addopts` does not mention it, and no hook or script references it. `-m
slow` collects zero tests.

It is a reserved name: the vocabulary exists for the day a test is slow enough
to want deselecting, without a `pyproject.toml` edit at that moment. Until then
it selects nothing, and the same caution as `accuracy` applies — a green run
over an empty selection looks exactly like a green run.

Note the interaction with `addopts` if it is ever used. A command-line `-m`
**replaces** the configured expression rather than intersecting with it, so
`uv run pytest -m "not slow"` would silently re-enable the integration suite.
The narrowing form has to be written out in full:

```
uv run pytest -m "not accuracy and not integration and not slow"
```

### There is no `--strict-markers`

`addopts` is `["-m", "not accuracy and not integration"]` and nothing else, so
an unregistered marker is a `PytestUnknownMarkWarning`, not an error. A typo'd
`@pytest.mark.integraton` therefore **warns and runs on every commit** — the
test is not deselected, because the expression does not match a marker that
does not exist.

That is the failure this list of four declarations does not protect against,
and it is worth knowing which way it fails: a mistyped exclusion marker makes a
test run when it should not, so the symptom is a commit gate that suddenly
needs Docker, not a test that quietly stops running. Loud, but only if you read
the warning summary.

## Running the excluded suites deliberately

`addopts` removes two markers from every default run, so the tests they carry
have to be asked for by name. This section states what each invocation
selects, what it needs present, and which environment variables it reads. The
step-by-step procedures — bringing the containers up, waiting on healthchecks,
tearing down, reading a skip — are in
[How to run the integration and mutation suites](../how-to/run-integration-and-mutation-suites.md).

| Suite | Command | Needs |
| --- | --- | --- |
| integration, all of it | `uv run pytest -m integration` | Docker for the store tests, an endpoint for the `llm` ones |
| Neo4j `GraphStore` | `uv run pytest -m integration tests/integration/graph` | `docker compose -f docker-compose.test.yml up -d neo4j` |
| pgvector `VectorStore` | `uv run pytest -m integration tests/integration/vector` | `… up -d postgres` |
| live LLM | `uv run pytest -m integration tests/integration/llm` | an OpenAI-compatible endpoint |
| wheel packaging | `uv run pytest -m integration tests/integration/test_wheel_contents.py` | `uv`, a build backend, seconds |
| accuracy | `uv run pytest -m accuracy tests/accuracy/` | a live LLM — and selects **zero tests** today |

### `-m` replaces the configured expression, it does not intersect

`addopts` is prepended and pytest's last `-m` wins, so `-m integration`
deselects nothing else — it selects the 197 integration tests and drops the
rest. A path argument narrows collection; the marker still has to be there,
because an unmarked path would be filtered by `addopts` again. That is why
every form above carries both.

The narrowing case has to be written out in full. `-m "not slow"` would
silently re-enable the integration suite; `-m "not accuracy and not
integration and not slow"` is the intended meaning. See
[addopts](#pytest-addopts--m-not-accuracy-and-not-integration).

### The store suites skip rather than fail when the backend is absent

Both adapter modules probe before running, and both probes prove the server
can *serve* rather than merely accept a connection:

- `tests/integration/graph/test_neo4j_store.py` runs `RETURN 1` and requires
  the answer to be `1`. A TCP connect succeeds against a Neo4j still
  recovering its store files, and against one with wrong credentials.
- `tests/integration/vector/test_pgvector_store.py` creates the `vector`
  extension and round-trips one vector through a temporary table. The image
  ships pgvector's files; a database that has never run `CREATE EXTENSION`
  still cannot store a vector.
- `tests/integration/llm/test_live_endpoint.py` asks for a real completion
  with `PROBE_MAX_TOKENS = 2000` and requires non-empty content. A model
  *listing* is not enough — the deployment is `llama-swap`, which lists every
  model it is configured for whether or not the weights load. The budget is
  generous because a reasoning model spends most of ~150 completion tokens on
  chain of thought, and a stingy probe would skip a healthy server.

All three probes exist because of one incident: the accuracy suite probed
Ollama's model list, the model was listed and would not load, and eight tests
**failed instead of skipping** (`BACKLOG.md` B12). A skip is only honest if
the probe checks the capability the tests actually use.

So a green `-m integration` run over a stopped Docker is not evidence. Read
the collected-and-skipped counts, the same way an empty marker selection has
to be read by count rather than by exit code.

### Connection settings are environment variables with working defaults

Every default matches `docker-compose.test.yml`, so nothing needs setting for
the standard local run.

| Variable | Default | Read in |
| --- | --- | --- |
| `KG_TEST_NEO4J_URI` | `bolt://localhost:7688` | `tests/integration/graph/test_neo4j_store.py` |
| `KG_TEST_NEO4J_USER` | `neo4j` | same |
| `KG_TEST_NEO4J_PASSWORD` | `redstring` | same |
| `KG_TEST_POSTGRES_DSN` | `postgresql://postgres:redstring@localhost:5434/redstring_test` | `tests/integration/vector/test_pgvector_store.py` |
| `KG_LLM_BASE_URL` | `http://192.168.1.14:8080/v1` | `tests/integration/llm/test_live_endpoint.py` |
| `KG_LLM_MODEL` | `qwen3.6-27b-mtp` | same |
| `KG_COMPLIANCE_MAX_EXAMPLES` | `50` | `tests/compliance/graph_store.py`, `vector_store.py` |

The two `KG_LLM_*` defaults point at a host on the author's network. They are
defaults, not a requirement: `langchain-openai` speaks to any
OpenAI-compatible server, and the probe skips cleanly when nothing answers.

`KG_COMPLIANCE_MAX_EXAMPLES` is the knob to reach for when a run is too slow
while iterating — `KG_COMPLIANCE_MAX_EXAMPLES=10 uv run pytest -m integration`
is the form every module docstring uses. It is covered in full in
[the next section](#kg_compliance_max_examples-default-50-read-in-testscompliancegraph_storepy-and-vector_storepy-process-wide-only).

### `-n auto` is safe for the vector suite and not for the whole one

The Neo4j suite wipes the single shared database with `MATCH (n) DETACH DELETE
n` before every test, so under `pytest-xdist` each worker destroys the others'
data mid-test — measured as **36 failures that say nothing about the code**
(`BACKLOG.md` B10f). They present as flakiness: different tests fail per run,
and running serially makes them stop.

The wipe cannot be scoped away. The compliance suite generates its own tenant
ids and `new_store()` never learns them, and
`test_delete_by_tenant_removes_exactly_that_tenant` needs a genuinely empty
database to mean anything. A database per worker needs Neo4j Enterprise;
Community allows one.

The pgvector suite is parallel-safe by a cheaper version of the same trick —
its table is `kg_vectors_test_{PYTEST_XDIST_WORKER}` (`kg_vectors_test_main`
outside xdist), so a worker truncates only its own rows. Postgres permits as
many tables as you like; that is the whole difference.

None of this touches the commit gate, whose own `-n auto` under the ratchet is
fine: `addopts` deselects `integration` before xdist sees a test.

### The wheel test needs no backend

`tests/integration/test_wheel_contents.py` carries a
function-level `@pytest.mark.integration` rather than a file-wide
`pytestmark`, and it is marked for cost rather than for infrastructure: it
builds a wheel, installs it into a throwaway environment, and asks the
installed package for all six bundled domain prompts. In a source checkout the
YAML files are simply on disk, so every other test passes whether or not they
are in the distribution — the failure it guards against is a `KeyError` on
every domain id for every installed user, with the whole suite green.

Run it before a release.

### The accuracy suite does not exist yet

`grep -rn "mark.accuracy" tests/` returns nothing and `tests/accuracy/` holds
only `__init__.py`, so `uv run pytest -m accuracy` collects zero tests and
exits green. Nothing in this repository measures extraction quality; the gates
say a change stayed correct, not whether extraction got better or worse. The
gap is `BACKLOG.md` B12.

### A default run is partial evidence, by construction

Coverage and mutation testing are both measured over the default selection, so
code whose only tests are `integration`-marked is invisible to both. Slice 4
landed a Neo4j `GraphStore` whose tests are all integration-marked, and a
cosmic-ray mutant left in its source passed the full default suite because not
one line of the adapter ever ran. `tests/conftest.py` prints a terminal
summary naming the deselected count and the command that runs it, which stops
the omission being silent; only a combined coverage run would close it
(`BACKLOG.md` B10a). See
[Verifying a mutation run before trusting it](#verifying-a-mutation-run-before-trusting-it).

## `KG_COMPLIANCE_MAX_EXAMPLES`

One environment variable, read in two files, defaulting to `50`:

```python
DEFAULT_MAX_EXAMPLES = int(os.environ.get("KG_COMPLIANCE_MAX_EXAMPLES", "50"))

compliance_settings = settings(
    deadline=None,
    max_examples=DEFAULT_MAX_EXAMPLES,
    suppress_health_check=[HealthCheck.too_slow],
)
```

That block appears verbatim in both `tests/compliance/graph_store.py` and
`tests/compliance/vector_store.py`, and the resulting `compliance_settings`
decorates **21** property tests in the graph suite and **14** in the vector
suite. Nothing else in the repository reads the variable; the other
`max_examples` values in `tests/` (25, 50, 60, 300, 500) are hard-coded per
test and are unaffected by it.

It is not a gate setting. The compliance suites' adapter subclasses for real
backends are `integration`-marked and therefore deselected by `addopts`, so on
a normal `git commit` the only suites this variable governs are the in-memory
ones — where 50 examples cost nothing. It matters when you run
[the excluded suites](#running-the-excluded-suites-deliberately) or a mutation
session.

### What the number buys

The suites are property-based, and a real backend calls `new_store()` **once
per example**. That is where the time goes: at 50, a Neo4j run is roughly 750
database resets. Measured on the graph suite:

| `KG_COMPLIANCE_MAX_EXAMPLES` | Graph suite wall time |
| --- | --- |
| 10 | 25 s |
| 25 | 43 s |
| 50 (default) | 66 s |

```
KG_COMPLIANCE_MAX_EXAMPLES=10 uv run pytest -m integration
```

That form — a prefix on the `pytest` invocation — is the one every module
docstring and the `addopts` comment in `pyproject.toml` use.

`deadline=None` and `suppress_health_check=[HealthCheck.too_slow]` accompany
the setting for the same reason it exists: store construction dominates the
per-example cost, so a slow adapter is a performance finding rather than a
flaky test, and hypothesis should not be the thing that reports it.

### Process-wide only

The variable is read at **module import**, so two constraints follow and
neither is negotiable from a test.

**It must be in the environment of the `pytest` process.** Prefix the command
or export it before starting; setting it inside a test or a fixture is too
late, because `compliance_settings` was constructed when the module was
imported.

**It is a per-*run* knob, not a per-adapter one.** By the time a subclass body
executes, the value is fixed and baked into the shared `settings()` object, so
"turn it down for the slow backend and leave the in-memory adapter at 50" is
not achievable as the suite is written. This is tracked as `BACKLOG.md` B10h,
and it is a deliberate trade rather than an oversight — the graph suite's own
comment records the reasoning:

> An explicit `max_examples` inside a `settings()` decorator outranks every
> hypothesis profile, so a hard-coded value here would also make
> `--hypothesis-profile` inert for every adapter. Reading it from the
> environment keeps the promise that an adapter opts in solely by
> implementing `new_store()`.

Fixing B10h therefore means per-adapter hypothesis *profiles*, or a class-level
hook the shared decorator reads through a callable — **not** a
`settings(max_examples=...)` on the subclass, which would reintroduce exactly
the precedence problem the current design avoids. Slice 4 measured the cost
table above, found it negligible, and correctly declined to build the
machinery.

### The suite asserts it is still honoured

Both compliance suites have a paired test that the environment value actually
reaches the decorator:

```python
def test_max_examples_is_tunable_without_editing_the_suite(self):
    from tests.compliance import graph_store as suite

    assert suite.compliance_settings.max_examples == suite.DEFAULT_MAX_EXAMPLES
```

They live in `tests/unit/graph/test_memory_store.py` and
`tests/unit/vector/test_compliance_coverage.py::TestTheSuiteIsTunable`, and
both run on the commit gate. A hard-coded `max_examples` reintroduced into
either shared suite fails there, rather than silently ignoring your
environment — which is the same instinct as
[ADR 0014](../adr/0014-exemption-lists-are-empty-and-must-stay-falsifiable.md):
a knob nothing checks is indistinguishable from a knob that has stopped
working.

### Lowering it changes which boundaries are tested at all

This is the hazard, and it is the reason the recorded cosmic-ray
`test-command` sets `KG_COMPLIANCE_MAX_EXAMPLES=5`: each mutant otherwise pays
for a full integration run.

A property test is a **sampler**, not a proof about a specific value. Two
mutants in `InMemoryVectorStore.search` — `k < 0` widened to `k <= 0`, and to
`k < 1`, both of which make a legal `k=0` raise — were killed on one
cosmic-ray run and **survived the next, with nothing in the adapter changed
between them.** `k=0` was reached only by a property drawing `k` from `0..12`,
so whether the boundary was covered depended on the sampler and on this
variable.

Two things follow:

- **A lowered value makes a mutation result non-deterministic**, and the
  natural misreading of a survivor that used to die is "something changed in
  the source." Nothing had. See
  [Verifying a mutation run before trusting it](#verifying-a-mutation-run-before-trusting-it).
- **Where a guard names a specific value, pin it as an example.**
  `test_k_zero_returns_nothing_rather_than_raising` in
  `tests/compliance/vector_store.py` exists for precisely this, and its
  docstring says so: "A boundary that matters belongs in an example, not in a
  budget." Its assertions are independent of the budget, so it kills both
  mutants at any value of the variable, including 1. The general rule is in
  [`.claude/rules/testing.md`](https://github.com/tyevans/redstring/blob/main/.claude/rules/testing.md).

Raising it is worthwhile in the opposite situation — hunting a suspected
ordering or filtering bug, where more draws is the only thing that widens the
search. And it is not a substitute for parallelism: `-n auto` over the Neo4j
suite produces 36 failures that say nothing about the code
(`BACKLOG.md` B10f), so a slow run is lowered, not sharded. The step-by-step
procedure is in
[How to run the integration and mutation suites](../how-to/run-integration-and-mutation-suites.md).
