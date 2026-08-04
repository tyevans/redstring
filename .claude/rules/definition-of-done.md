---
paths:
  - "src/**/*.py"
  - "tests/**/*.py"
  - "docs/**/*.md"
---

# Definition of Done

## Deferred work (applies to ALL work)

Nothing is complete while something you noticed sits only in your head, a TODO
comment, or a chat message. It goes in `BACKLOG.md`, in the same commit that
passes it by, written so someone picking it up cold does not have to
rediscover what you already know. See `CLAUDE.md` — this is the project's
hardest rule and it has no exceptions.

## Architectural decisions (applies to ALL work)

No work is complete if the decisions it made or changed are not documented:

1. **No design spec or plan is complete** until it has been run against the
   existing ADRs in `docs/adrs/`. A spec should say, for each related ADR,
   whether it **stands**, is **amended**, or is **superseded**.
2. If work amends or supersedes an ADR, that is part of the work, not a
   follow-up: write the new ADR (or amend the old one's Consequences), and
   update the old ADR's **Status** with an "Amended by" / "Superseded by"
   pointer. ADR bodies are immutable records — never rewrite a Decision
   retroactively; supersede it.
3. New architecturally significant decisions get their own ADR in
   `docs/adrs/`, numbered after the current highest. For this project that
   means: changes to the layered import contract in `pyproject.toml`,
   extraction strategy selection, the entity/graph data model, consolidation
   and merge semantics, and anything that changes a public contract or a
   persistence format.
4. **ADR bodies carry no counts and no file tables.** Those go in the commit
   message, which is immutable and scoped to a moment. See
   `.claude/rules/recurring-defects.md` §5.
5. **ADR numbers are allocated at merge, not at drafting.** Draft under a
   provisional name; re-check `docs/adrs/` on current `main` before merging.
   The directory is currently empty, so the first is `0001`.

## Recurring defect check (applies to ALL work)

`.claude/rules/recurring-defects.md` lists six defect shapes. No work is
complete without a pass against its quick checklist. The two that gate most
often:

- **A method with sibling implementations changed or added** — the semantics
  belong in one shared, parametrised test body exercised by every
  implementation. A regression test in `test_ollama_extractor.py` does not
  satisfy this: it cannot catch the next backend.
- **New counter, stat, or metric field** — a test asserts it non-zero under
  the condition it counts.

## Quality gates

Do not run ruff, bandit, `lint-imports`, or pytest as separate steps. They are
wired into `pre-commit` and run on `git commit`; running them by hand
duplicates the work. Write the change, then commit; the hook reports what is
wrong and often fixes it in place (re-`git add` and commit again when it
does). See `CLAUDE.md`.

Work is not done until the commit passes the gate — not until it passes
"except for the hook", and not with a check disabled. A rule you ignored is a
deferral: it goes in `BACKLOG.md` with why ignoring it was correct.

## New feature

1. Implementation under `src/kg_builder/`, in the correct layer — the
   `lint-imports` contract in `pyproject.toml` is the authority, and a
   cross-layer import means either the code is in the wrong layer or the
   contract needs an explicit, argued change (which is an ADR).
2. Unit tests in `tests/unit/`, mirroring the package path, covering happy
   path and edge cases.
3. `hypothesis` properties wherever a property is easier to state than a table
   of examples.
4. New dependencies added with `uv add` / `uv add --optional <extra>` — never
   by hand-editing `pyproject.toml`.
5. Commit passes the gate, including the coverage ratchet.

## Bug fix

1. A failing test that reproduces the bug, **proved red against the pre-fix
   source** via `git checkout HEAD~1 -- <paths>` (not `git stash`).
2. The fix.
3. All existing tests pass.
4. **If the bug was a divergence between two implementations of one
   contract**, the regression test lives in the shared/parametrised suite and
   the per-implementation duplicates it subsumes are deleted.
5. **The assertion is written from the documented contract, not from observed
   output.** A test written from what the code currently prints encodes the
   bug as the spec.
6. If a `BACKLOG.md` entry described this bug, it is deleted in the same
   commit.

## Refactor

1. No behaviour change — existing tests pass **without modification**. A
   refactor that requires editing assertions is not a refactor; say so and
   treat it as a behaviour change.
2. No public API changes unless explicitly intended.
3. Commit passes the gate.

## New extraction backend or strategy

1. Implements the same contract as its siblings under
   `src/kg_builder/extraction/`.
2. Optional dependency guarded with `try`/`except ImportError` if it pulls a
   heavy or optional package.
3. **Runs the shared, parametrised behaviour suite** — not only its own
   hand-written tests. A backend with only bespoke tests will diverge
   silently from its siblings; that is defect shape §1, and it is the single
   most expensive shape in this list.
4. Accuracy coverage in `tests/accuracy/` if it changes extraction quality,
   marked `accuracy` so it stays out of the commit gate.
