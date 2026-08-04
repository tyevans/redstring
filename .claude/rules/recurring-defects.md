---
paths:
  - "src/**/*.py"
  - "tests/**/*.py"
  - "docs/**/*.md"
---

# Recurring Defect Shapes

**Provenance:** imported from `eventsource-py`, where these six shapes were
derived from an audit of ~130 `fix:` and correction commits. The *shapes* are
general and worth carrying; the *evidence* was that project's history, and is
summarised here abstractly rather than by SHA. kg-builder has not yet been
audited against its own history — as instances turn up here, record them under
"Local instances" at the bottom, with the commit. An entry with a local
instance is load-bearing; one without is a prior.

Check for these when writing, reviewing, or diagnosing code — they are cheap
to prevent and expensive to find.

**The through-line:** five of the six are the same root cause wearing
different clothes — *one fact stored in more than one place, with no mechanism
that fails when the copies disagree.* Two implementations of one interface,
three declaration sites, a doc versus the tree, a test's assertion versus the
contract.

---

## 1. Silent divergence between implementations of one interface

Two implementations of the same protocol behave differently. Nothing fails,
because each one's tests assert its own behaviour.

The observed shapes: an empty-collection filter that matched *zero* rows in
one backend and *everything* in another; a time cutoff truncated to midnight
in one implementation and a rolling instant in the others; an in-memory
update that rebuilt its record from scratch and silently dropped a field the
SQL path preserved through its UPSERT column list.

**Where this lives in kg-builder:** anywhere more than one class implements
one behaviour contract — the extractor implementations under
`src/kg_builder/extraction/` (Ollama vs. OpenAI vs. any future backend), the
branches selected by `strategy_router.py`, and any place `db.py` or `cache.py`
has both a real and an in-memory/no-op path.

**Rule:** the semantics of a shared contract are pinned by one shared test
body parametrised over every implementation, never by a per-implementation
test. Adding a method to a shared interface is not done until every
implementation runs a shared case for it. When you fix a divergence, the
regression test goes in the shared/parametrised suite — if it lives only in
`test_ollama_extractor.py`, it cannot catch the next backend. Deleting the
per-implementation duplicates it subsumes is part of the fix.

**Reviewing:** for any changed method, open the sibling implementations and
compare. Empty collections, zero/`None` defaults, date and time truncation,
and "not set" versus "set to nothing" are where they diverge.

## 2. Redundant declaration sites with undocumented precedence

The same fact declared in N places; one silently wins; the losers look
authoritative and are not.

The observed shapes: a type name with three sources (constructor parameter,
class attribute, field default) where the constructor param won invisibly and
the vast majority of call sites were pure ceremony restating the class
attribute — the only sites exercising the override were the tests written for
it; and a hand-declared name on hundreds of sites, tens of which had silently
drifted from the derived value.

**Rule:** a fact has exactly one declaration site. Before adding a parameter,
field, or class attribute, ask whether the value is already derivable from
something the caller must supply anyway — if it is, derive it. Do not add an
override "for flexibility": every instance above was introduced that way, and
the override was used only by the test written to exercise it.

If a second site is genuinely required, the precedence order is documented at
the declaration and pinned by a test that sets both to conflicting values.

## 3. Inert code and always-zero metrics

Branches never taken and counters never incremented pass every test that does
not assert on them.

The worst observed instance: a runner read an attribute that **nothing in the
tree ever set**. A whole class of checkpointing silently never happened, a
duplicate-suppression branch was permanently unreachable, and its two counters
were pinned at zero. It shipped and passed CI.

**This is the class a shared conformance suite will not catch.** Guard it
directly:

- When you add a counter or stat field, add a test asserting it **non-zero**
  under the condition it counts. "Asserted zero in the happy path" is not
  coverage. In kg-builder this bites hardest on extraction stats, retry and
  circuit-breaker counters, and consolidation/merge tallies — all of which are
  read by humans deciding whether a pipeline run was healthy.
- When you read an attribute set elsewhere in the tree (`getattr`, duck-typed
  reads, anything the type checker cannot follow), grep for the write site
  before you rely on it. If nothing writes it, you have found this bug.
- Prefer deleting an unreachable branch over preserving it.

## 4. Tests that encode the bug as the spec

A test written from observed output rather than from the contract, which then
locks the defect in place and makes the real fix look like a regression.

In the observed instance the test asserting the buggy value had been added in
the *immediately preceding* commit; the fix had to rename and invert it.

**Rule:** write the assertion from the documented contract before running the
code. When adding a regression test, prove it red first — with
`git checkout HEAD~1 -- <paths>`, not `git stash`.

This one is sharpened by the coverage ratchet: pressure to keep the number
from falling is pressure to write *a* test, and a test transcribed from
current output satisfies the ratchet while asserting nothing.

## 5. Docs and plans rotting the moment a sweep names specifics

There is a whole sub-genre of commits fixing the previous sweep — stale
counts, a survivor table that needed two passes, a module map that no longer
matched the tree, a "second stale claim" the first sweep missed.

**Rule:** ADR and plan bodies do not contain counts of things or tables of
files. "13 exceptions moved", "83 of 86 call sites" — that belongs in the
commit message, which is immutable and correctly scoped to a moment in time.
An ADR states the decision, the forces, and the consequences; those stay
true. A number decays the next time anyone touches the tree.

Applies directly to `docs/plans/ring-migration.md` and anything like it: a
migration plan that enumerates module counts or per-file status will be wrong
within a slice or two. Track status in the tree (or in `BACKLOG.md`), not in
prose.

When a sweep *does* have to touch prose, grep for the symbol across `docs/`,
`README.md`, docstrings, and `CLAUDE.md` — not a curated list of files. The
repeated failure is a sweep that fixes the pages it thought of.

## 6. ADR number collisions

Observed three times, always the same cause: parallel branches each taking the
next free number from `main`.

**Rule:** this is structural to parallel work, not carelessness. Draft the ADR
under a provisional name (branch name or date suffix), and allocate the number
at merge time after checking `docs/adrs/` on current `main`. Before merging
any branch that adds an ADR, re-check the number.

`docs/adrs/` is currently empty, so the first ADR is `0001`.

---

## Quick checklist

- [ ] Changed a method with sibling implementations? Compared the siblings; case lives in the shared/parametrised suite.
- [ ] Added a parameter or attribute? It is not derivable from something already supplied.
- [ ] Added a counter or stat? A test asserts it non-zero.
- [ ] Read an attribute the type checker cannot follow? Grepped for the write site.
- [ ] Wrote a regression test? Proved red against `HEAD~1` first.
- [ ] Touched an ADR or plan? No counts, no file tables; ADR number re-checked against `main`.

---

## Local instances

None recorded yet. When one of these shapes shows up in kg-builder, add it
here with the commit and one line on how it hid — that is what turns an
imported prior into this project's own evidence.
