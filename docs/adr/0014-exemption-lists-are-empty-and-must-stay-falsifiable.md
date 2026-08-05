# ADR 0007: Exemption lists are empty, and an emptied one is deleted rather than kept

Two lists in `pyproject.toml` once weakened the quality gates for named paths:
ruff's `[tool.ruff.lint.per-file-ignores]` and mypy's `exclude`. Both are now
empty of legacy entries — and they ended in *different* states. ruff keeps the
key, carrying only the one deliberate `tests/**` entry; mypy carries no
`exclude` key at all, so `--strict` covers every module under
`src/redstring`.

**Why this is an ADR:** the asymmetry looks like an oversight and is not, and
the reasoning behind it generalises past these two lists. A per-file exemption
that names a deleted path passes silently in both tools, so a
shrinking-exemption ratchet can stop shrinking with nobody told. Worse, the
obvious way to measure what an exemption hides — running the linter restricted
to the exempted path — is *subject to the exemption*, and returns "All checks
passed!" unconditionally. And when the last entry was finally deleted for real,
what it had been absorbing turned out not to be strictness debt but a
misconfiguration whose suggested fix imports cleanly and breaks at model use.

This ADR records three decisions: that the legacy entries are gone, that an
emptied *exclusion* is deleted while an emptied *ignore list* may be kept, and
that the only honest measurement of an exemption is to delete it and run the
configured gate.

Related: [quality gates reference](../reference/quality-gates.md) for how the
gates run, [ADR 0001](0001-event-log-schema-and-granularity.md) for the event
classes that the ruff false positives landed on, and
[`.claude/rules/recurring-defects.md`](../../.claude/rules/recurring-defects.md)
for the wider "a passing check you have never seen fail is not yet evidence"
rule this is an instance of.

## Status

Accepted, slice 10 of the ring migration. Superseded by nothing; the
configuration it describes is the one in `pyproject.toml` today.

The state this ADR records is checkable in two places rather than asserted:

- `[tool.ruff.lint.per-file-ignores]` contains a single `"tests/**"` key, and
  every code under it is annotated with the reason it is there (`B011`, `ANN`
  for test functions, `DTZ001` for the naive-datetime strategies that exist to
  prove rejection). No entry names a path under `src/`. The block of comments
  that follows the key is the removal record: slice 9 took `models`,
  `services`, `schemas`, `graph/client.py` and `graph/queries.py`, then
  `events/**`; slice 10 took the last one,
  `src/redstring/extraction/**` = `["ANN", "TC", "RET", "ERA"]`, together with
  its test-side twin.
- `[tool.mypy]` has `files = ["src/redstring"]`, `strict = true`, and **no
  `exclude` key**. The one narrowing that remains is an
  `ignore_missing_imports` override for `asyncpg.*`, which is about a
  third-party package shipping no `py.typed` rather than about this codebase's
  own debt.

Two things this ADR does *not* claim to have settled. There is no automated
staleness guard over `per-file-ignores` itself — the mechanism that caught a
stale entry in slice 9 was a test asserting that another exemption list named
real files, and no equivalent covers the ruff key. Re-adding a `src/` entry is
therefore still caught by review rather than by a gate, which is precisely why
the key was left in place with its comment history intact instead of being
trimmed to nothing. And the `runtime-evaluated-base-classes` entry naming
`eventsource.domain.tenant_events.TenantDomainEvent` is a workaround for how
ruff resolves base classes, not a fix; if ruff ever matches through the MRO,
that entry becomes redundant and should be re-measured the way this ADR
prescribes rather than deleted on the assumption that it is inert.

## Context: two exemption lists, one silent failure mode

The library did not start with strict gates over every module. It started as an
extraction from an application, and both mypy and ruff were introduced over a
codebase that could not pass them. The usual answer was taken in both tools:
name the packages that were not ready, and let the gate run at full strength
everywhere else.

- ruff got `[tool.ruff.lint.per-file-ignores]` entries for `models`,
  `services`, `schemas`, `graph/client.py`, `graph/queries.py`, `events/**`,
  and finally `src/redstring/extraction/**` = `["ANN", "TC", "RET", "ERA"]`.
- mypy got `exclude = ["^src/redstring/extraction/"]`, sitting under
  `files = ["src/redstring"]` and `strict = true`.

Both are *ratchets by intent*: each entry is supposed to shrink and then die
with the debt it covers. The intent is not what makes a ratchet work, though.
A ratchet works when the mechanism notices that it has stopped moving, and
neither of these lists has that property on its own.

### The failure mode: an entry that matches nothing still passes

ruff accepts a `per-file-ignores` key whose glob matches no file. mypy accepts
an `exclude` regex that matches no path. Neither warns. So the moment a legacy
package is deleted — the very moment the exemption has served its purpose and
should be gone — the configuration reaches the state that is *indistinguishable
from a healthy one*: the gate is green, the list is short, nothing is being
suppressed, and nobody is told that the entry is now dead weight. The list has
stopped shrinking and there is no signal.

That is bad in itself, and worse in what it enables. A dead entry is a hole
with a name in it, and the name reads like a justification. When a new module
lands under a path a dead glob still covers, the exemption silently takes it,
and the gate that was supposed to be at full strength over the new code is not.

Slice 9 ran the experiment by accident and got both outcomes in a single
commit, which is why this is an ADR rather than a comment:

- Deleting `models/extracted_entity.py` **failed the gate**. The exemption
  covering it lived in a *test*, not in configuration, and that test asserted
  that every entry still names a real file. The same shape is visible today in
  `tests/unit/graph/test_neo4j_adapter_is_wired.py`, where `LEGACY_CYPHER` is
  guarded by `test_the_exemption_list_has_no_stale_entries` ("An exemption must
  die with the module it covers") and by
  `test_every_exempted_module_actually_contains_cypher` ("An exemption for a
  module with no Cypher is a hole, not a waiver").
- The ruff and mypy lists in `pyproject.toml` carried three deleted paths for
  one and two commits respectively, and **nothing said a word**.

Same hazard, same commit, opposite outcomes. The only variable was whether
someone had written the staleness check as a test. Configuration files do not
have that habit available to them, and this project has no staleness guard over
`per-file-ignores` — which is the reason the eventual answer was to empty the
lists rather than to police them.

### The measurement is subject to the exemption it measures

Emptying a list requires knowing what it hides, and the obvious way to find out
is unconditionally wrong. Running the linter restricted to the exempted path
still applies `per-file-ignores` on top of `--select`, so a command scoped to a
path whose ignore list is exactly the codes being selected cannot report a
finding whatever the code says. `All checks passed!` there is not a
measurement; it is a tautology. The same applies to naming files explicitly on
the mypy command line, which bypasses `exclude` and therefore answers a
different question than the configured run does. Both are worked through in
[The measurement rule](#the-measurement-rule-delete-the-entry-and-run-the-configured-gate)
below.

This is the "a passing check you have never seen fail is not yet evidence" rule
from [`.claude/rules/recurring-defects.md`](../../.claude/rules/recurring-defects.md),
applied to a lint invocation instead of a test. Both spellings of *prove it can
fail* are the same instruction, and here the vacuous result and the vacuously
green ratchet reinforce each other: the list stops shrinking, and the command
you would reach for to check whether it should shrink reports that there is
nothing to do.

### What the last entry turned out to be hiding

Deleting `src/redstring/extraction/**` for real surfaced ten ruff findings
across eight files (its test-side twin hid a further seventeen `PTH123` and one
`PT018`), and deleting mypy's `exclude` surfaced fourteen errors across five
files — bare `dict`/`list` generics, a missing `types-PyYAML`, and `merging.py`
reaching `preference` through `mapping`'s namespace instead of importing it
from `domain.preference` where it is defined.

The mypy findings were ordinary debt. Most of the ruff findings were not: nine
of the ten came from a *misconfiguration* of
`flake8-type-checking`'s `runtime-evaluated-base-classes`, not from strictness.
Ruff matches a base class as written in the file rather than through the MRO,
and every event class here declares `TenantDomainEvent` — a pydantic model, but
not spelled `pydantic.BaseModel` — so `TC001`/`TC002`/`TC003` fired on field
annotations that pydantic must resolve at runtime. The suggested fix compiles
and imports cleanly and breaks at model *use*; see
[what the extraction exemption was hiding](#what-the-extraction-exemption-was-hiding-a-misconfiguration-not-debt),
and [ADR 0001](0001-event-log-schema-and-granularity.md) for the event classes
involved.

That is the sharpest argument in this ADR's favour. An exemption cannot tell
debt from a misconfiguration — it absorbs both, indefinitely, and the longer it
sits the more confidently it reads as a record of known technical debt. The
`events/**` entry had an `ANN` half that exempted nothing at all and a `TC` half
that was covering for a settings bug. Removing exemptions promptly is worth
more than any amount of accumulated strictness debt would suggest, because what
you find under one may not be debt.

For how the gates themselves run — pre-commit, ruff, mypy, bandit,
`lint-imports`, the coverage ratchet — see the
[quality gates reference](../reference/quality-gates.md).

## Decision

**No exemption in `pyproject.toml` may name a path under `src/`.** Every legacy
entry in both lists is deleted, and each one was deleted in the commit that
deleted or repaired the package it covered — not in a later cleanup pass. The
last two went in slice 10: ruff's
`"src/redstring/extraction/**" = ["ANN", "TC", "RET", "ERA"]` and mypy's
`exclude = ["^src/redstring/extraction/"]`, both removed by fixing
`extraction/` rather than by deleting it.

Three rules follow, and they are the decision proper:

1. **An exemption is measured with the entry deleted, never through it.** A
   linter invocation scoped to an exempted path is subject to that path's
   exemption and cannot report a finding. Delete the entry, run the configured
   gate, read the result. That is the only number that means anything.
2. **An emptied *exclusion* is deleted; an emptied *ignore list* may be kept.**
   The two lists end in different states on purpose, because an exclusion over
   an empty set excludes nothing and any staleness guard written over it would
   pass vacuously.
3. **An exemption removed is not assumed to have been hiding debt.** What comes
   out is classified before it is fixed. Nine of the ten ruff findings under
   `extraction/` were a misconfiguration of
   `flake8-type-checking`'s `runtime-evaluated-base-classes`, and applying
   ruff's own suggested fix would have shipped a runtime break.

What remains in each list is narrow and reasoned inline in `pyproject.toml`;
the two sections below say what each one is and why it is not a legacy entry
wearing a different hat.

### ruff `per-file-ignores` keeps only its non-legacy `tests/**` entry

The key survives, with a single `"tests/**"` glob under it, carrying `B011`,
`ANN`, and `DTZ001`. Those codes are there for two *kinds* of reason, and the
distinction is what makes them non-legacy:

- `ANN` and `B011` are about test code being different code. Return
  annotations on test functions produce findings in bulk and signal in none;
  `B011` is about `assert False` being a legitimate thing for a test to write.
  The exemption is a statement about what tests are, not a debt marker with a
  clock on it.
- `DTZ001` is load-bearing. `hypothesis`'s `st.datetimes(min_value=...,
  max_value=...)` bounds **must** be naive, and strategies that generate naive
  datetimes exist precisely to prove the domain rejects them. Enforcing DTZ001
  in `tests/` would make it impossible to test the invariant it protects.
  `DTZ003` (`utcnow`) stays enforced everywhere, including tests, and is
  reinforced by a `flake8-tidy-imports` ban on the attribute — DTZ003 catches
  the *call* but not a bare reference such as
  `default_factory=datetime.utcnow`, which is how it reached this codebase.

Neither kind shrinks over time, so neither is a ratchet, so neither has the
silent-staleness problem: a `tests/**` glob stops matching only if the test
suite is gone. The key is kept rather than emptied for a second reason as well.
The comment block below it is the removal record — which entry died in which
slice, and what deleting it surfaced — and keeping the key keeps that history
attached to the thing it is about. Re-adding a `src/` entry then reads as an
edit to a list whose every neighbour explains why it is not one.

The `runtime-evaluated-base-classes` setting naming
`eventsource.domain.tenant_events.TenantDomainEvent` is *not* an exemption and
is not covered by this decision. It corrects ruff's model of the codebase — it
tells ruff a truth about the base class that ruff cannot derive — rather than
suppressing a finding ruff is right about. It is still a workaround for how
ruff resolves bases, and if ruff ever matches through the MRO it should be
re-measured by deletion, not assumed inert.

### mypy carries no `exclude` key at all

`[tool.mypy]` has `files = ["src/redstring"]`, `strict = true`,
`warn_unreachable`, `warn_return_any`, `disallow_untyped_defs`, and the
`pydantic.mypy` plugin — and **no `exclude`**. Strict mode therefore covers
every module in the package with no per-path escape hatch of any kind. The
plugin is part of what that coverage is worth: `init_typed` checks constructor
*calls* against field types, without which `Entity(confidence="high")`
type-checks clean and fails only at runtime, which is most of the point of
typing a domain model whose job is enforcing invariants.

There was an `exclude`, and it named `extraction/`. Slice 10 emptied it by
fixing that package's findings rather than by deleting the package — the
distinction matters, because emptying a list by deleting what it covered
proves nothing about the gate.

**The key was then deleted rather than left as `exclude = []`.** An empty
exclusion changes no behaviour, so the choice looks cosmetic; it is not, for
two reasons:

- It presents as a live ratchet that has merely reached zero. A list with a
  key still in it invites the next author to *add an entry*, which is a small
  edit to something already there. With no key, adding an exclusion means
  adding a key — visible in a diff, and hard to do without a sentence in the
  commit message saying why.
- Any staleness guard written over it would pass vacuously. A guard asserting
  that every exclusion still matches a real path is trivially true over an
  empty list, so the mechanism this ADR exists to demand would itself become a
  check that has never been seen fail — the same failure, one level up. See
  [`.claude/rules/recurring-defects.md`](../../.claude/rules/recurring-defects.md).

This is where the asymmetry with ruff comes from, and it is a distinction
between *kinds of list* rather than between the two tools. ruff's
`per-file-ignores` still admits entries that are not debt — the `tests/**`
codes are permanent statements about what test code is — so the key has live
content and keeping it costs nothing. mypy's `exclude` admits nothing but
debt: there is no path under `src/` that should permanently escape strict
mode. A list whose only legitimate content is temporary, once empty, has no
reason to exist. The general form is worked through in
[the next section](#why-the-two-lists-end-differently-exclusion-over-an-empty-set-is-vacuous).

The one narrowing that remains is an `[[tool.mypy.overrides]]` block setting
`ignore_missing_imports` for `asyncpg.*`, and it is deliberately not covered
by this decision. asyncpg ships no `py.typed`, so mypy cannot see its types at
all; the alternative is a stub package tracking asyncpg's releases for the
sake of one adapter. The override is scoped to a third-party module namespace
rather than to a path under `src/`, `redstring.vector.adapters.pgvector` is
the only module that imports asyncpg, and that adapter's own signatures are
fully annotated — the untyped surface stops at the driver.

It is also exempt from the measurement rule, and for a reason worth stating
rather than assuming: rule 1 exists because deleting an exemption *surfaces
something you did not know*. Deleting this override surfaces the same missing
stubs every time, determined by the dependency's packaging rather than by this
codebase. That makes it a fact about asyncpg, not a ratchet, and it has no
clock on it. The check that would matter is a different one — whether asyncpg
has started shipping `py.typed` — and that is a dependency-upgrade question,
not a gate question.

## Why the two lists end differently: exclusion over an empty set is vacuous

The asymmetry is not about ruff versus mypy. It is about what a list *does*
when it is empty, and there are two answers.

An **exclusion** subtracts from a set the gate would otherwise cover. Empty, it
subtracts nothing: `exclude = []` and no `exclude` key produce byte-identical
behaviour from mypy. The key is pure residue. And residue is not neutral —
it does two things, both bad:

- It reads as a live ratchet that happens to be at zero, so the next author
  with an awkward module adds an entry. That is a one-line edit to something
  already present, which is the cheapest possible way to reopen a hole. With no
  key at all, the same author has to *introduce* `exclude`, which is a new key
  in a diff and effectively impossible to land without a sentence justifying
  it.
- Any staleness guard written over it passes vacuously. "Every excluded path
  still exists" is trivially true of an empty list, so the mechanism this ADR
  demands would itself become a check nobody has ever seen fail — the exact
  failure being guarded against, reproduced one level up in the guard. See
  [`.claude/rules/recurring-defects.md`](../../.claude/rules/recurring-defects.md).

A **seam** is different. It is the single place a rule admits exceptions, and
its emptiness is the rule's strongest possible state rather than its absence.
`per-file-ignores` is that for ruff: it still holds live, permanent content
(`tests/**` with `B011`, `ANN`, `DTZ001` — statements about what test code is,
not debt with a clock on it), and the comment block beneath it is the removal
record of every legacy entry and what deleting each one surfaced. Keeping the
key keeps that history attached to the configuration it explains.

The clearest illustration is not in `pyproject.toml` at all — it is the pair of
in-test exemption lists that are empty and *deliberately kept*:

- `LEGACY_CYPHER` in `tests/unit/graph/test_neo4j_adapter_is_wired.py` is
  `frozenset()`, and its comment says why it survived its last entry: "it is
  the seam the rule is enforced at: with it empty,
  `test_no_module_outside_the_adapter_contains_cypher` admits no exceptions at
  all, and adding one means adding a name here, which is a visible decision in
  review rather than a query that quietly appeared in a service."
- `LEGACY_PGVECTOR` in `tests/unit/vector/test_pgvector_adapter_is_wired.py`
  is empty for the identical reason, and its comment records that
  `test_the_exemption_list_has_no_stale_entries` is what caught its last entry
  outliving `models/extracted_entity.py` in slice 9.

So an empty list is kept in one place and deleted in another, in the same
codebase, on the same reasoning. The distinguishing question is: **does the
list have a non-vacuous check attached to it, and does emptiness make that
check stronger?** For `LEGACY_PGVECTOR` the answer is yes twice over. Empty, it
makes `test_no_module_outside_the_adapter_speaks_pgvector` absolute; and the
list's own vacuity risk is covered by a separate test —
`test_the_detector_would_notice`, whose docstring is "Guard the guard: a marker
list that matches nothing passes vacuously, so prove it fires on the adapter
itself" — which asserts the *detector* still finds pgvector syntax in the
adapter. That test is meaningful whether or not the exemption list has entries,
so the enforcement never becomes vacuous even though the exemption did.

mypy's `exclude` has no such structure available. There is no path under `src/`
that should permanently escape strict mode, so the list's only legitimate
content is temporary; there is no detector alongside it that emptiness
strengthens; and its enforcement — `files = ["src/redstring"]` with
`strict = true` — is complete precisely when the key is absent. A list whose
every possible entry is debt, once empty, has nothing left to be. Deleting it
*is* the enforcement.

The general rule, then, is not "empty lists get deleted" and not "empty lists
get kept":

> Keep an emptied list when it is the seam a rule admits exceptions at and
> something else non-vacuously proves the rule still bites. Delete an emptied
> list when it only ever subtracted from a gate, because an exclusion over an
> empty set excludes nothing and any guard over it passes vacuously.

Both halves are the same instruction as the measurement rule in the
[next section](#the-measurement-rule-delete-the-entry-and-run-the-configured-gate)
and as the gates described in the
[quality gates reference](../reference/quality-gates.md): a configuration state
is only worth what the failure you have seen it produce. The seam is kept
because someone adding to it fails review; the exclusion is deleted because
nothing would have failed had it stayed.

## The measurement rule: delete the entry and run the configured gate

Before you can delete an exemption you have to know what it is holding back,
and every convenient way of asking that question is wrong in the same way: it
asks the *tool* about a path the tool has been configured to treat specially,
so the answer is decided by the configuration rather than by the code. The rule
that follows is one line:

> **Delete the entry, run the configured gate, read the result.** A scoped
> invocation over an exempted path is subject to that path's exemption and
> cannot report a finding; a green result from it is not a measurement.

This is the same instruction as *prove it can fail*, applied to a lint
invocation instead of a test — see
[`.claude/rules/recurring-defects.md`](../../.claude/rules/recurring-defects.md).
The failure it guards against is not that the measurement is imprecise. It is
that the measurement is **unconditional**: it returns the same output for code
that is clean and code that is riddled, so it carries no information at all,
and it reads as reassurance.

The rule has a second half that matters as much. Deleting the entry has to be
the *only* change: emptying a list by deleting the package it covered proves
nothing about the gate, because nothing was ever run at full strength. Slice
10's two deletions both cleared their entries by fixing `extraction/`, which
is why the numbers below are worth quoting.

Both `pyproject.toml` comments record the measurement rather than the
conclusion. The ruff block says the entry "was hiding ten findings across eight
files, two of which were in `prompt_generator.generate_json_schema` and left
with that function"; the mypy block says slice 10 "emptied it by fixing the
last fourteen findings in `extraction/` rather than by deleting the package."
A comment saying the entry was "no longer needed" would be the same sentence
whether anyone had looked or not.

### Why `ruff check --select ANN,TC <path>` could not have reported a finding

The obvious way to find out what an exemption hides is to select exactly the
codes it suppresses and point ruff at exactly the path it covers:

```
$ uv run ruff check --select ANN,TC src/redstring/events/
All checks passed!
```

That result was **unconditional**. `per-file-ignores` is applied *on top of*
`--select`: ruff resolves the selected rule set, then subtracts the per-file
ignores for each file it checks. The entry for that path was exactly
`["ANN", "TC"]`, so the effective rule set for every file under it was the
empty set. The command could not have printed anything else, whatever the code
said — a clean run and a thousand findings are indistinguishable through it.

The tell is available without deleting anything, and it is worth knowing
because it costs one command: **run the same selection against a path the
exemption does not cover.** If `--select ANN,TC` reports findings under
`src/redstring/graph/` and nothing under the exempted path, the difference is
the exemption talking, not the code. That is a positive control for a lint
invocation — the same move as breaking an implementation on purpose to watch a
property fail, and the only way a green scoped run becomes evidence rather than
a tautology.

Deleting the entry and running the configured gate surfaced ten findings across
eight files. The same shape held for the last entry:
`"src/redstring/extraction/**" = ["ANN", "TC", "RET", "ERA"]` measured through
itself reported nothing, and measured by deletion reported ten findings, plus
seventeen `PTH123` and one `PT018` from its test-side twin.

Two adjacent traps make the scoped form worse than merely uninformative here:

- **`--force-exclude` is in the configured invocation and not in yours.** The
  `ruff-check` hook runs `uv run ruff check --fix --force-exclude`, because
  `pre-commit` passes explicit filenames and ruff's default is to check a file
  named on the command line even when configuration excludes it. So the
  command that governs commits and the command you typed differ in flag as
  well as in scope — see the
  [quality gates reference](../reference/quality-gates.md) for the exact hook
  invocations.
- **`--select` narrows to what you already suspect.** Selecting `ANN,TC`
  cannot report the `RET` and `ERA` findings that were also under the
  extraction entry, so even a correctly-unexempted scoped run answers a
  narrower question than the gate does. The configured gate selects the
  project's full rule set and ignores only `UP042`.

### Why naming files on the mypy command line answers a different question

mypy's version of the same mistake is more tempting, because there is no
`--select` to get wrong and the command looks like the real one:

```
$ uv run mypy src/redstring/extraction/
```

Naming files or directories on the command line **bypasses `exclude`**.
`exclude` filters mypy's own *discovery* of what to check under `files`; an
explicit path argument is not discovery, so the filter never applies to it.
The result is the opposite failure to ruff's — not vacuously green, but
*differently scoped*, and wrong in both directions at once:

- It type-checks the excluded package, which the configured run does not. A
  finding it reports is real, but it is not a finding the gate would have made,
  so it cannot tell you whether the gate is at full strength.
- It skips everything outside the argument, so errors that only exist as a
  *relationship* between the argued package and the rest of `src/` never
  arise.

Neither number answers the question "what happens when the exclusion goes."
And unlike the ruff case there is no green-versus-red tell: the scoped run
produces plausible output either way, which is what makes it the more
tempting of the two mistakes.

The configured run takes **no path argument at all**. `[tool.mypy]` carries
`files = ["src/redstring"]` with `strict = true`, and the pre-commit hook is
declared `pass_filenames: false` with `entry: uv run mypy` — deliberately, so
that the hook and a bare `uv run mypy` are the same invocation over the same
discovered set. That single line is what makes the gate reproducible by hand;
a hook that passed filenames would have made every local run of mypy a
differently-scoped run, and this section's mistake the default.

Deleting `exclude = ["^src/redstring/extraction/"]` and running that
configured invocation surfaced fourteen errors across five files: bare
`dict`/`list` generics, a missing `types-PyYAML` (now a `dev` dependency
alongside `types-dateparser` and `types-python-dateutil`), and `merging.py`
reaching `preference` through `mapping`'s namespace instead of importing it
from `domain.preference` where it is defined.

That last one is the argument for whole-package analysis in miniature. It is
not a fact about `merging.py`; it is a fact about the relationship between
`merging.py`, `mapping.py` and `domain.preference`, and no invocation scoped
to one of them was ever going to say it. It is also the finding that mattered
most, because a re-export reached through a sibling's namespace is how the
[architecture contract](../reference/quality-gates.md) gets quietly bypassed —
`lint-imports` sees the import of `mapping`, which is legal, and not the
dependency on `preference` hiding behind it.

Two further reasons the configured run is the only one worth reading here,
both of which a scoped invocation silently drops:

- **The `pydantic.mypy` plugin is configuration, not a flag.** With
  `init_typed = true` and `init_forbid_extra = true`, mypy checks constructor
  *calls* against field types — `Entity(confidence="high")` is an error rather
  than a runtime surprise. A scoped run still loads the plugin (it comes from
  `pyproject.toml`), but a scoped run over a package whose callers are outside
  the argument sees very few of the calls it exists to check.
- **`ignore_missing_imports` for `asyncpg.*` is an override on a module
  namespace, not a path**, so it applies identically either way. It is the one
  narrowing in the mypy block, and it is not a ratchet: deleting it surfaces
  the same missing stubs every time, because asyncpg ships no `py.typed`.
  Nothing is learned by measuring it, which is exactly the property that
  distinguishes it from an exemption.

The environment is part of the configured gate too, and this is where a
measurement goes wrong without any command-line mistake at all. A missing extra
turns real findings into import errors, and `uv add`/`uv remove` re-sync in a
way that can narrow the installed extras back to `dev`. Measure in an
environment synced with `--all-extras`, and treat a suspiciously clean result
as evidence about the environment before it is evidence about the code — the
same instinct
[ADR 0001](0001-event-log-schema-and-granularity.md)'s event modules needed
when their exemption came off, and the one the
[quality gates reference](../reference/quality-gates.md) records for mutation
runs.

## What the extraction exemption was hiding: a misconfiguration, not debt

The measurement rule tells you *how* to find out what an exemption holds back.
This section is about what was actually found, because the answer is the
strongest argument in the ADR and it is not the answer anyone expected.

An exemption is written as a debt marker. Its name says "this package is not
ready yet," and every month it survives makes that reading more confident. But
an exemption does not know what it is suppressing. It subtracts a set of rule
codes from a set of paths, and whatever falls in that intersection is absorbed
— real debt, a rule that does not fit this codebase, and a *misconfiguration of
the linter itself*, indistinguishably.

The last two removals produced one of each, which is why they are worth
recording together:

- `src/redstring/extraction/**` = `["ANN", "TC", "RET", "ERA"]` was hiding ten
  findings across eight files. These were ordinary debt: missing annotations,
  returns ruff wanted restructured, commented-out code. Two of them lived in
  `prompt_generator.generate_json_schema` and left with that function when it
  was deleted, which is recorded in `pyproject.toml` beside the removal. The
  test-side twin hid a further seventeen `PTH123` and one `PT018`. mypy's
  `exclude` over the same package hid fourteen errors across five files. All
  of it was debt, all of it was fixable, and none of it was surprising.
- `events/**` = `["ANN", "TC"]` was different in both halves, and it is the one
  this section is named for. Its `ANN` half **exempted nothing at all** — the
  events package was already fully annotated, so that half of the entry had
  been inert for as long as it had existed. Its `TC` half was not covering
  debt either. It was covering a misconfiguration of ruff's
  `flake8-type-checking` settings, and had been quietly absorbing the symptom
  in place of anyone noticing the cause.

Both halves fail the same way an exemption always fails: they are silent about
which kind of thing they hold. An entry that exempts nothing and an entry that
exempts a settings bug look identical in a diff, and both look identical to an
entry covering genuine debt.

### `runtime-evaluated-base-classes` matches the base as written, not through the MRO

`flake8-type-checking`'s TC001/TC002/TC003 move an import used only in
annotations into an `if TYPE_CHECKING:` block. That is right for most code and
wrong for pydantic, which resolves field annotations at runtime, when it builds
a model's schema. Ruff knows this, and provides the escape hatch:
`runtime-evaluated-base-classes` names base classes whose subclasses' imports
must stay real.

The setting was configured, with `"pydantic.BaseModel"` in it — and it had no
effect on this package, because **ruff matches the base class as it is written
in the file, not through the MRO.** No event here is spelled as a
`pydantic.BaseModel` subclass. Each declares `TenantDomainEvent`, which comes
from a *different distribution* (`eventsource`):

```python
from uuid import UUID

from eventsource import register_event
from eventsource.domain.tenant_events import TenantDomainEvent


@register_event
class MergeUndone(TenantDomainEvent):
    merge_event_id: UUID
```

`TenantDomainEvent` *is* a pydantic model, and every field annotation in that
class is therefore runtime-evaluated. Ruff cannot know that: establishing it
means following an import out of the project, into an installed package, and
walking that class's own bases — whole-program analysis ruff's per-file lint
does not do. So the configured entry matched no class in `redstring.events`,
TC001/TC002/TC003 fired on field annotations across the package, and the `TC`
half of the `events/**` exemption swallowed the lot for as long as it existed.

Two things about this are worth holding on to, because neither is obvious from
the finding itself.

**The rule was not wrong everywhere in the package, which is what made the
exemption look reasonable.** `events/__init__.py` imports the same class under
a genuine `if TYPE_CHECKING:` guard, and that is correct — there it annotates a
module-level constant, `KG_EVENT_TYPES: tuple[type[TenantDomainEvent], ...]`,
which nothing resolves at runtime. The distinction TC00x has to make is *field
annotation versus any other annotation*, and it is exactly the distinction
`runtime-evaluated-base-classes` exists to let it make. A blanket `TC`
exemption over the package erases both the false positives and the correct
guard, so the file that was already right and the files that were wrongly
flagged became indistinguishable.

**The fix is a configuration correction, not an exemption.** Name the real base
alongside the nominal one:

```toml
runtime-evaluated-base-classes = [
    "pydantic.BaseModel",
    "eventsource.domain.tenant_events.TenantDomainEvent",
]
```

The path must be the one the class is *defined* at, spelled in full. That entry
tells ruff a true thing about the codebase it had no way to derive, and TC00x
then runs at full strength over the events package — flagging what should be
guarded, leaving field annotations alone. Contrast an exemption, which
suppresses findings ruff is right about. This is why the entry is not covered
by [rule 1](#decision) and why it survives in a `pyproject.toml` whose legacy
exemptions are all gone.

It remains a workaround for how ruff resolves bases, not a fix for it. If ruff
ever matches through the MRO the entry becomes redundant — and the way to
establish that is [the measurement rule](#the-measurement-rule-delete-the-entry-and-run-the-configured-gate):
delete it, run the configured gate, see whether anything fires. Assuming it has
gone inert is the same mistake as assuming an exemption has.

Almost all of what came out from under the events entry was this
misconfiguration rather than debt. The next section is about why that mattered
more than the count: the fix ruff suggests for these false positives is
mechanical, and applying it ships a runtime break.

### The false positive whose suggested fix imports cleanly and breaks at model use

A false positive that is merely noisy costs a reviewer's attention. This one
costs more, because ruff's TC00x findings arrive **with a fix attached**, the
fix is mechanical, and every cheap way of checking that it worked says it
worked.

The edit ruff offers is to move an annotation-only import under a guard:

```python
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID


@register_event
class MergeUndone(TenantDomainEvent):
    merge_event_id: UUID
```

Applied to `events/merge.py`, that leaves `import redstring.events.merge`
**succeeding** — and then fails the events tests with

```
PydanticUserError: `MergeUndone` is not fully defined; you should define
`UUID`, then call `MergeUndone.model_rebuild()`.
```

`merge_event_id: UUID` is a *field*, and pydantic resolves field annotations
when it builds the model's schema, which happens on first use rather than at
import.

**`from __future__ import annotations` is what makes this survive the import,
not what makes it safe.** Every module in this package carries it, so all
annotations are strings and nothing evaluates them as the class body runs.
That is the property this project relies on elsewhere — it is why a large
share of cosmic-ray's survivors are mutations to annotation text that no test
can kill. Pydantic is the one consumer that *does* evaluate them, later, by
looking the names up in the module's namespace. Move the import into a
`TYPE_CHECKING` block and the name is not there when pydantic looks. The
deferral that normally makes annotations inert is precisely what postpones the
failure past the moment anyone is watching.

Two consequences follow for how work like this is verified.

**An import smoke test passes; only using the model catches it.** Any check
that stops at "does the module import?" is blind to this entire class of
defect — and a module-level import is exactly what someone reaches for when
sanity-checking a mechanical refactor spread across a package. The events
classes are the ones described in
[ADR 0001](0001-event-log-schema-and-granularity.md), and their field
annotations are load-bearing for schema construction rather than decoration,
so the failure is not cosmetic: an event that cannot build its schema cannot
be written to or replayed from the log.

**Nothing weaker than the configured suite would have caught it, which is the
same rule as the rest of this ADR.** A green import, a green
`ruff check`, and a clean diff all agree the fix is fine; the suite is where
the disagreement shows up. That is *prove it can fail* applied to a refactor
instead of to a lint invocation or a test — see
[the measurement rule](#the-measurement-rule-delete-the-entry-and-run-the-configured-gate),
the deliberate-defect habit in
[`.claude/rules/recurring-defects.md`](../../.claude/rules/recurring-defects.md),
and the [quality gates reference](../reference/quality-gates.md) for what the
configured run covers.

There is a diagnostic worth taking from this too. **When a lint rule fires
uniformly across a package — same rule family, same shape, same base class —
suspect the configuration before the code.** Debt is uneven; it accumulates
where the pressure was. A finding that appears in every file of a package and
nowhere else is a statement about how the tool models that package, and here
it was: ruff could not see through `TenantDomainEvent` to `pydantic.BaseModel`.

Which is the argument this ADR is built on. For as long as
`events/**` = `["ANN", "TC"]` sat in `pyproject.toml`, that uniformity was
invisible — along with the fact that the entry's `ANN` half was suppressing
nothing whatsoever. The entry read as an honest record of a package with work
outstanding, and it would have gone on reading that way indefinitely. An
exemption does not distinguish debt from a misconfiguration, and it removes
the pressure to find out which one it is holding. That is a better reason to
remove exemptions promptly than any argument about accumulated strictness
debt: what is under one may not be debt at all, and while it is there nobody
can tell.

## Consequences

**Strict mode now covers every module under `src/redstring`, with no path
escape hatch in either tool.** That is the headline consequence and it is
checkable rather than asserted: `[tool.mypy]` has `files = ["src/redstring"]`,
`strict = true` and no `exclude` key, and `[tool.ruff.lint.per-file-ignores]`
holds one `"tests/**"` glob whose codes are permanent statements about test
code. Anything landing under `src/` from here meets the full gate on its first
commit, which is a different situation from the one this ADR started in: there
is no longer a list a new module can quietly fall into.

**Adding an exemption back is now an argument, not an edit.** This is the
practical effect of deleting mypy's key rather than emptying it. Re-introducing
an exclusion means introducing `exclude` — a new key, visible in the diff,
effectively impossible to land without a sentence in the commit message. Adding
a ruff entry under `src/` means adding a line to a list whose every neighbour is
a comment explaining why the legacy entries are gone and what removing each one
surfaced. Neither is forbidden. Both are conspicuous, which is what the ADR was
after, because neither is caught by a gate.

**And that gap is real.** There is no automated staleness guard over
`per-file-ignores`, and there cannot usefully be one over an absent mypy key.
The mechanism that caught a stale entry in slice 9 was a *test* — the shape
still visible in `tests/unit/graph/test_neo4j_adapter_is_wired.py` and
`tests/unit/vector/test_pgvector_adapter_is_wired.py`, where an exemption list
is guarded by a test that its entries name real files and, in the pgvector
case, by a second test proving the detector itself still fires. Configuration
files have no such habit available. So the consequence is honest rather than
comfortable: the `pyproject.toml` state is protected by emptiness and by
review, and if a `src/` entry ever returns, only a reader will notice. That is
an acceptable position *while the lists are empty* and stops being one the
moment they are not — which is the strongest reason to keep them empty.

**The measurement rule costs something every time it is applied, and that cost
is deliberate.** Finding out what an exemption hides requires deleting it and
running the configured gate — no scoped invocation, no `--select` narrowed to
the codes you suspect, no path argument to mypy. That is slower than the
one-line command, and the one-line command is what people reach for. The rule
buys the difference between an answer and a tautology, and the events entry is
the evidence for the price being right: measured through itself it reported
nothing, and it had been reporting nothing for as long as it existed.

**Removing an exemption is no longer assumed to be a debt-repayment exercise.**
What came out from under `events/**` was mostly a misconfiguration of ruff's
`runtime-evaluated-base-classes`, plus an `ANN` half that had been suppressing
nothing at all. The habits that follow are cheap and now standing: when a rule
fires uniformly across a package, suspect the settings before the code; and
never apply a linter's suggested fix in bulk on the strength of a clean import,
because pydantic's field annotations resolve at schema-build time and the
resulting break is invisible until a model is used. Both are recorded in
`.claude/rules/recurring-defects.md`.

**One structural improvement fell out of the mypy measurement**, and it is the
reason whole-package analysis is worth the stricter setting: `merging.py` was
reaching `preference` through `mapping`'s namespace rather than importing it
from `domain.preference`. `lint-imports` cannot see that — it sees a legal
import of `mapping` — so the architecture contract was being bypassed in a way
only a type-checker run over the whole package could state. Expect the same
class of finding whenever a scoped check is replaced by a configured one.

**Live costs accepted.** Two narrowings remain and are not covered by the
decision. The `ignore_missing_imports` override for `asyncpg.*` is a fact about
a dependency shipping no `py.typed`, not a ratchet — deleting it surfaces the
same missing stubs every time, so the measurement rule has nothing to tell us
about it; the question that would matter is whether asyncpg has started
shipping types, which is an upgrade question. And the
`eventsource.domain.tenant_events.TenantDomainEvent` entry in
`runtime-evaluated-base-classes` is a correction to ruff's model of the
codebase rather than a suppression, but it *is* a workaround for how ruff
resolves base classes. If ruff ever matches through the MRO it becomes
redundant — and the way to establish that is to delete it and run the
configured gate, not to assume it has gone inert.

**The gates are what enforce all of this, so keep the two documents in step.**
[The quality gates reference](../reference/quality-gates.md) records the exact
hook invocations — including `--force-exclude` on `ruff check` and
`pass_filenames: false` on mypy, both of which exist so the configured gate and
a bare local run are the same command. A change to either that made a local
invocation differently-scoped would quietly reintroduce this ADR's mistake as
the default.

## Related decisions and rules

This ADR is one instance of a habit that shows up across the project in
several spellings. The documents below are the other spellings, and each is
listed with what it adds rather than as a bare pointer — the point of the
cross-links is that none of them subsumes another.

**[Quality gates reference](../reference/quality-gates.md) — what the
configured gate actually is.** This ADR keeps saying "run the configured
gate"; that page is where the configured gate is written down, hook by hook.
Two of its sections are load-bearing for the measurement rule rather than
merely adjacent: `ruff check --fix --force-exclude` exists because pre-commit
passes filenames and ruff would otherwise check a file its configuration
excludes, and mypy is declared `pass_filenames: false` so that the hook and a
bare `uv run mypy` are the same invocation over the same discovered set. Its
`per-file-ignores`, `runtime-evaluated-base-classes`, and "There is no
`exclude`" sections describe the end state this ADR reasons toward, and they
are the copy that must move when the configuration does. The reference tells
you what the gate is; this ADR tells you why two of its lists are shaped the
way they are. Keep them in step: a divergence here is a stale explanation of a
live configuration, which is worse than no explanation.

**[ADR 0001](0001-event-log-schema-and-granularity.md) — the classes the
misconfiguration landed on.** The events whose field annotations TC00x wanted
to move into a `TYPE_CHECKING` block are the ones ADR 0001 designs: coarse
events carrying an explicit `event_version`, loaded and saved through
`TenantAwareRepository`. That is what makes the false-positive fix a
correctness problem rather than a style one — an event that cannot build its
pydantic schema cannot be appended to the log or replayed from it, and ADR
0001's cross-check between the filesystem and the event registry is the kind
of thing that runs *at model use*, not at import. The two documents meet at
exactly that point: ADR 0001 explains why those annotations are load-bearing,
this one explains why an exemption hid a change that would have broken them.

**[`.claude/rules/recurring-defects.md`](../../.claude/rules/recurring-defects.md)
— the general shape, and the instances that are not about lint config.** This
ADR is a specific case of §3, *inert code and always-zero metrics*: an entry
that matches nothing, a `--select` that cannot report a finding, a guard over
an empty list, all indistinguishable from health. The rules file carries the
project-wide statement of it — *a passing check you have never seen fail is
not yet evidence* — together with the version of the events-package story that
belongs in a rules file rather than an ADR. It also carries §6, ADR number
collisions, which is why this document is one of several numbered 0007.

Three sibling mechanisms are worth knowing about because they are the same
idea implemented where a configuration file cannot reach:

- **The in-test exemption lists.** `LEGACY_CYPHER` in
  `tests/unit/graph/test_neo4j_adapter_is_wired.py` and `LEGACY_PGVECTOR` in
  `tests/unit/vector/test_pgvector_adapter_is_wired.py` are both empty and
  both deliberately kept, each guarded by a test asserting its entries name
  real files — and, on the pgvector side, by a second test that proves the
  *detector* still fires against the adapter itself, so the enforcement stays
  non-vacuous even though the exemption is empty. That structure is what
  `pyproject.toml` has no way to express, and it is the reason the mypy key
  was deleted while the ruff key was kept.
- **The per-port isolation exemptions.** Both `ISOLATION_EXEMPT` dicts are
  `{}`, a test rejects a blank reason, and another rejects an entry naming a
  method the port has dropped. Same rule, applied per store port: an entry is
  a visible decision, an absent entry is the omission the module exists to
  catch.
- **[ADR 0006](0006-the-public-surface-is-gated.md) — gated, not curated.**
  The public surface is held by three tests rather than by a list someone
  maintains, for the reason this ADR arrives at from the other direction: a
  list nobody is forced to update stops being true silently. Read together,
  the pair says that the answer to "how do we keep this list honest" is
  usually either *delete the list* or *write the test that fails when it
  lies* — and that choosing neither is the failure mode.

The one rule that governs this document itself is
[`.claude/rules/definition-of-done.md`](../../.claude/rules/definition-of-done.md):
an ADR is part of the work, not a follow-up, and its body is an immutable
record. If a future slice re-admits an exemption under `src/`, that is not an
edit to the Decision above — it supersedes it, with a new ADR saying what
measurement justified the entry and what will make it die.
