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
summarised here abstractly rather than by SHA. redstring **has** now been
audited against its own history: the ring-migration campaign
(`docs/plans/ring-migration.md`) supplied the first instances for shapes 1, 3,
4 and 5, and later slices have added more; all are recorded with their SHAs
under "Local instances" at the bottom. Read that
section before treating any shape here as a foreign import — those four are
load-bearing local evidence. Shapes 2 and 6 remain priors, and stay priors
until someone records an instance; keep adding to "Local instances" as they
turn up.

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

**Where this lives in redstring:** the four ports, each with two
implementations that must be interchangeable —

| Port | Implementations |
|---|---|
| `ports/graph_store.py` | `graph/adapters/memory.py`, `graph/adapters/neo4j.py` |
| `ports/vector_store.py` | `vector/adapters/memory.py`, `vector/adapters/pgvector.py` |
| `ports/cache.py` | `llm/cache/memory.py`, `llm/cache/redis.py` |
| `ports/llm_provider.py` | `llm/adapters/fake.py`, `llm/adapters/langchain.py` |
| `ports/embedding_provider.py` | `llm/adapters/fake_embedding.py` |

Four of the five have a shared suite; `llm_provider` does not, so it is the
one where this shape is currently free to happen. See
`docs/adr/0002-two-store-ports.md` for why the store ports are two rather than
one, and `docs/how-to/implement-a-store-adapter.md` for the procedure a third
implementation follows.

**Rule:** the semantics of a shared contract are pinned by one shared test
body parametrised over every implementation, never by a per-implementation
test. Adding a method to a shared interface is not done until every
implementation runs a shared case for it. Here that body is
`tests/compliance/graph_store.py`, `tests/compliance/vector_store.py` and
`tests/compliance/cache.py` (with generators in
`tests/compliance/strategies.py`) — none of them named `test_*.py`, so none
collected directly. An adapter opts in by subclassing and supplying one hook:
an `async def new_store()` returning a fresh empty store for the two store
suites (the class turns that into the `store` fixture itself, plus an optional
`dispose` for adapters holding a connection), or a `cache` fixture for the
cache suite. `tests/unit/graph/test_memory_store.py` is the model — its whole
body is `class TestMemoryStore(GraphStoreCompliance)` with a three-line
`new_store`, and the only extra tests in the file are ones true of *that*
adapter and no other (it holds no state outside itself, and the compliance
harness's own `dispose` contract). `tests/unit/vector/test_memory_store.py`
and `tests/unit/llm/test_memory_cache.py` are the same shape.
When you fix a divergence, the regression test goes
in the shared suite; if it lands in `test_memory_store.py` it cannot catch the
next adapter. Deleting the per-implementation duplicates it subsumes is part
of the fix.

**Naming the divergences a shared suite permits.** Not every difference
between adapters is a defect, and a suite that pretends otherwise is worse
than none — it either fails against a legitimate backend or gets an opt-out
flag that silently disables the check it was written for.
`tests/compliance/vector_store.py` states the `VectorStore` contract in **two
tiers** for exactly this reason: an approximate index (ivfflat, hnsw, a
managed ANN service) may legitimately omit a true neighbour, so tier 1 asserts
exact membership, ordering and scores on tens of vectors — where every
sensible backend scans sequentially and *is* exact, so `k`, filter-before-`k`,
tie-break order and self-similarity all live here — and tier 2 asserts only
recall on a larger dataset (200 vectors), that the single true nearest
neighbour appears somewhere in the returned top-k: not its rank, not the rest
of the list. Every test belongs to exactly one tier, and the two tier banners
in the file say which. Both tiers still bind everyone — the weaker one is not
an escape hatch, and *an adapter that cannot pass tier 1 on ten vectors is not
a `VectorStore`*.

There is deliberately **no `is_approximate` capability flag**. A flag that
lets an adapter opt out of correctness tests is how adapters quietly stop
being interchangeable: it gets set once, for a good reason, and from then on
the suite is silent about the thing it was written to check. So when a
divergence is real, encode it as a *weaker shared claim every adapter still
makes*, never as an exemption for one of them. Deciding this before the
divergent adapter exists is the whole point — writing the weaker tier
afterwards means tuning it until the new adapter passes, which is not a test.
The file says so in the tier-2 banner, and means it: **tier 2 currently passes
trivially and its comment admits it.** Every adapter in this tree is exact
(in-memory scans brute-force; pgvector has no ANN index on purpose — see
`docs/adr/0012-no-ann-index-in-a-multi-tenant-vector-store.md` and BACKLOG
B10k), so nothing there has ever run against a store that *can* miss a
neighbour, and its passing is evidence about the tests rather than about
recall. One query over one corpus is not a recall claim; a real one needs many
queries, a stated recall@k target, and a failure message reporting the
measured rate. Strengthen it *before* the first approximate adapter lands.
A permitted-divergence tier is a placeholder for evidence, and saying so in
the file is what stops the next reader mistaking it for evidence.

The same reasoning has a second, unrelated instance in the same suite, which
is what makes it a shape rather than a one-off: pgvector's `vector` is float4,
so the suite generates float32-representable components and compares *scores*
with a tolerance — while still asserting exact equality of the *stored
vector*, because float32-representable values survive a float32 column
unchanged. That is the same move again: weaken the shared claim precisely as
far as the backend genuinely forces, and no further.

**Mechanise the coverage of the shared suite.** A shared body only pins what
it contains, and the gap it reliably develops is per-method.
`tests/unit/graph/test_compliance_coverage.py` and
`tests/unit/vector/test_compliance_coverage.py` close it: each derives the
read-method list from its Protocol by introspection and fails when a method
lacks `test_{method}_returns_copies` and `test_{method}_never_crosses_tenants`
on the compliance class. **Every store port gets this gate**, because the
written rule failed four times first — four read methods shipped in slice 3
with full behavioural tests and no mutation-isolation test, and a mutation run
rather than review found each time that returning the live internal object
passed everything. Four occurrences is one missing habit, not four mistakes,
and a habit is not fixed by writing the rule down a third time.

Five properties make it a gate rather than a checklist, and each is worth
copying to the next port:

- **Derived from *return annotations*, not names.** `read_methods()` walks
  the Protocol and keeps any method whose return type mentions a domain type
  at any nesting depth, so `list[Entity]` and `dict[str, list[Entity]]` both
  count while `delete_relationship() -> bool` and the `upsert_*` methods drop
  out automatically. A future read method is included the day it is added.
  The one thing introspection cannot infer is a *new domain type* on the
  port — that set is written down (`{Entity, Relationship, Alias}` for
  `GraphStore`, `{VectorRecord, VectorMatch}` for `VectorStore`), and `Alias`
  was added to it when the port gained one rather than after a mutation run
  found the leak.
- **The list is introspected rather than hand-kept**, deliberately: a
  hand-kept list needs updating by the same person who forgot the test.
- **The guard guards itself.** Both modules assert the detector finds
  something (`len(read_methods()) >= 8`; `read_methods() == {"get", "search"}`)
  — a coverage checker over an empty set passes vacuously and is
  indistinguishable from a working one. Same reasoning as
  `exhaustive = true` in §3.
- **The registries are checked in both directions.** `GraphStore` carries a
  legacy registry mapping eight differently-named tests, written before the
  convention; it is closed to additions. It is safe only because two further
  tests assert every registered name still exists on the compliance class
  (catching a rename that silently empties it) and that no entry names a
  method the port no longer has. `VectorStore` has no legacy registry and
  should stay that way — write the two conventional names and that module
  needs no edit at all.
- **The exemption list is empty and must carry a reason.** Both
  `ISOLATION_EXEMPT` dicts are `{}`, a test rejects a blank reason, and
  another rejects an entry for a method the port has dropped. That is
  `docs/adr/0014-exemption-lists-are-empty-and-must-stay-falsifiable.md`
  applied per-port: an entry is a visible decision, an absent entry is the
  omission the module exists to catch.

The vector module extends the idea one step further, to the *generators* the
shared suite draws from, and the instance is the argument for doing so:
`metadata_dicts` was built on `property_dicts`, whose keys came from
`st.text(max_size=6)` — so `entity_type`, eleven characters and the only key
the port reads, **could never be generated**. A real divergence lived in that
blind spot (in-memory raised `TypeError: unhashable type: 'list'` on a stored
`{"entity_type": ["person"]}` where pgvector returned `[]`). Three tests now
assert the strategy reaches the reserved key, on both sides of the filter, and
with unhashable values specifically. A strategy that cannot draw the
interesting value does not fail — the properties over it simply go quiet,
which is the §3 shape wearing test-fixture clothes.

`ports/cache.py` has a compliance suite but no such gate; it is the remaining
place a new read can ship uncovered. See
`docs/how-to/implement-a-store-adapter.md` for the adapter-side procedure and
`.claude/rules/testing.md` for why mutation-isolation is a separate test from
any behavioural one — returning the live internal object is *correct* on every
read and wrong only afterwards, so no assertion about the returned value can
see it.

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
  coverage. In redstring this bites hardest on the resilience counters in
  `llm/circuit_breaker.py`, `llm/retry.py` and `llm/rate_limiter.py`, and on
  the merge tallies under `consolidation/` and in `extraction/merging.py` —
  all of which are read by humans deciding whether a pipeline run was healthy.
  The resilience three are the harder half, because their counters do not live
  in the process: failure counts, probe slots and window hits are all `Cache`
  keys, so a counter that is never incremented looks exactly like a cache miss
  and every caller keeps working. `merge_extractions` sums four of these
  (`dropped_entities`, `unresolved_relationships`, `self_loops`,
  `undatable_relative`) across chunks; a fold that drops one still returns the
  right entities and edges, which is precisely why no behavioural assertion
  sees it. Test each with input that makes it non-zero *and* differ from its
  siblings — four counters all summed to the same number cannot tell you which
  line was wired to which field.
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
current output satisfies the ratchet while asserting nothing. Coverage counts
lines executed, never claims checked, so this failure is invisible to the one
gate most likely to have prompted it. `scripts/coverage_ratchet.py` and
`.coverage-baseline` are doing a different job; treat a rising number as
evidence about reach, not about correctness.

**The near neighbour, and where it is written down.** The shape above is a
test that asserts the *wrong value*. Its more common sibling is a test that
asserts a right value under an input that cannot tell the right
implementation from a wrong one — same outcome, no observably bad assertion,
and it survives review because it reads correctly. That catalogue is not
repeated here: **CLAUDE.md carries the sixteen-row table of failure shapes**
this project has actually hit (interned string literals hiding `is` for `==`,
a chain graph hiding first-found for shortest-path, ids from `uuid4()` hiding
a composite key compared on one component, an expectation written in terms of
the constant under test), together with the rules derived from it — force a
collision when a key is a tuple, pin boundary values as `@example` alongside a
property, give a round-trip an oracle that is not the code under test, and
break the implementation on purpose before trusting a property. Read that
table when writing the test; read this section when auditing one that already
exists. `.claude/rules/testing.md` states the same rules as conventions for
where such tests live and how they are run, and
`.claude/rules/definition-of-done.md` is where "proved red first" is a
completion condition rather than advice.

The two shapes meet in `010d8f2`, the ring-migration campaign's only Critical
(see "Local instances" below): the module's stated invariant held because of
an argument about sort order, and every test agreed with it because no test
used two intervals sharing a lower bound. The assertion was not transcribed
from a bug — the *input* was chosen so the bug could not appear. When you fix
one of these, grep for the second instance before closing; both times this
project looked, it was there.

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

Note the exact path: the plan is `docs/plans/ring-migration.md`, not
`docs/ring-migration.md`. A rule about stale references is the last place a
wrong path should survive, and this one did for several slices.

When a sweep *does* have to touch prose, grep for the symbol across `docs/`,
`README.md`, docstrings, and `CLAUDE.md` — not a curated list of files. The
repeated failure is a sweep that fixes the pages it thought of.

**This file is its own exhibit.** §1 above previously anchored the
divergence shape to `extraction/ollama_extractor.py`,
`openai_extractor.py`, `llm_extractor.py`, `factory.py`,
`strategy_router.py`, `db.py` and `cache.py`. **Every one of those modules
has been deleted.** The section still read as authoritative — a plausible
module map, in a binding rules file, naming nothing that exists — and it
went stale exactly the way this section predicts: not by anyone editing it
wrongly, but by the tree moving underneath prose that named specifics. The
rule was written here before the campaign started, and the campaign broke
the page carrying it.

Two things follow. First, **naming specific modules is not the same mistake
as naming counts**: §1 is useless without anchors, so the fix is to re-anchor
on each sweep, not to write vaguely. Prefer anchors that are structurally
stable — a *port* outlives the adapters behind it, which is why §1 now hangs
off `ports/` and the compliance suites rather than off whichever extractor
happened to exist. Second, **a deletion sweep's grep has to cover
`.claude/` too.** `docs/` and `CLAUDE.md` are the obvious targets; rules
files are read by every future session and are the least likely to be
opened during a refactor.

## 6. ADR number collisions

Observed three times, always the same cause: parallel branches each taking the
next free number from `main`.

**Rule:** this is structural to parallel work, not carelessness. Draft the ADR
under a provisional name (branch name or date suffix), and allocate the number
at merge time after checking the ADR directory on current `main`. Before
merging any branch that adds an ADR, re-check the number.

The directory is **`docs/adr/`**, singular — not `docs/adrs/`. A new ADR is
numbered against the highest one there, which you find by asking rather than
by reading a number out of this file — the count written here went stale
within three ADRs of being written, which is §5 catching the section that
warns about §5:

```
git ls-tree --name-only main docs/adr/ | sort | tail -1
```

Run that at merge time, not at draft time — the answer changes while a branch
is open, which is the entire failure mode.

**This tree ran the whole experiment, and the second half is the lesson.**
Eight files were once numbered `0007-*` — eight parallel slices each correctly
taking "the next free number from `main`", which was `0007` for all of them.
Nothing failed. The filenames were distinct, so no file overwrote another and
no tool complained; the collision was invisible right up until someone tried
to cite "ADR 7" and found eight documents.

Then it was **half fixed**, which turned out to be worse than not fixing it.
Seven files were renamed to `0008`–`0014` and nothing else was touched:

- Seven H1 titles still read `# ADR 0007:`, so the rendered documents claimed
  a number none of them had, and seven of them claimed the *same* one.
- Every inbound `](../adr/0007-<slug>.md)` link — 43 of them, across the
  how-tos, the reference pages and the ADRs themselves — pointed at a path
  that no longer existed. All were already broken. Nothing said so.
- Both of these files, which are loaded into every session, still described
  `0001` through `0006` as the whole set and the `0007` eight-way as live.

None of that is exotic; it is §5 with an ADR number in place of a module map,
and it survived because **a renumber has no failing test**. The fix that
matters is not the sweep, it is the gate: `mkdocs build --strict` fails on a
link to a missing page, so the docs site is what makes the next half-finished
renumber impossible to land. Add the citation to the site's nav and the
number becomes checkable rather than merely conventional.

So: **renumbering means the filename, the H1, and every inbound citation, in
one commit.** Renaming the file alone is the failure mode, not the fix.

Cross-references by number decay for the same reason. Prefer linking the full
path — `docs/adr/0002-two-store-ports.md`, as §1 does — over "see ADR 2"; the
path is checkable by grep, by `mkdocs --strict`, and survives a renumber that
a bare number does not.

---

## Quick checklist

- [ ] Changed a method with sibling implementations? Compared the siblings; case lives in the shared/parametrised suite.
- [ ] Added a parameter or attribute? It is not derivable from something already supplied.
- [ ] Added a counter or stat? A test asserts it non-zero.
- [ ] Read an attribute the type checker cannot follow? Grepped for the write site.
- [ ] Added a read method to a store port? Its isolation and tenant tests exist under the conventional names — `test_{method}_returns_copies` and `test_{method}_never_crosses_tenants` on the compliance class, so `test_compliance_coverage.py` sees them.
- [ ] Wrote a regression test? Proved red against `HEAD~1` first.
- [ ] Touched an ADR or plan? No counts, no file tables; ADR number re-checked against `main`.

---

## Local instances

redstring's own evidence. (a) through (f) come from the
ring-migration campaign (`docs/plans/ring-migration.md`) and the rest from the
slices after it. Each entry names the shape it instances, the commit, and —
the part worth having — *how it stayed invisible*. Several arrived here from
`BACKLOG.md`, which is where a closed entry's lesson would otherwise sit in a
file about open work until someone deleted it. Shapes 2 and 6
have no entry yet and remain imported priors; add to this list rather than
rewriting a shape when one turns up.

**(a) §1 and §3 — a router that routed on a deleted model, tested entirely
through `MagicMock` (`3502900`).** `extraction/strategy_router.py` took a
`ScrapingJob` in every entry point — `route(job, content)` read
`job.extraction_strategy`, `job.content_domain` and `job.id`, and its
`JobUpdateCallback` wrote the classification back onto the job row — but the
model had been deleted a slice earlier. 583 source lines whose whole API was
shaped around a type that no longer existed, and the suite said nothing:
every job in the 826-line test file was a bare `MagicMock`, across 24
mock/patch sites. A `MagicMock` answers any attribute, so the tests were
equally green before and after the model went. This is the §1 shape with the
second implementation being *the absent one* — nothing pinned the router
against the real type — and the §3 shape at the same time, since a mock
satisfies a branch without ever executing the condition that reaches it.
Deleting it moved coverage 81.70 → 81.56, the movement §4 warns not to read as
being about tests. Read `MagicMock`-only fixtures as an unverified interface,
not as a tested one.

**(b) §1 — the cache compliance suite's docstring names the divergence it
exists to catch, and says it was learned twice.** `tests/compliance/cache.py`
opens with it: "an in-memory reference that is *more forgiving* than the real
backend lets a caller pass its tests on behaviour production does not have."
The concrete instance is in the suite itself —
`test_a_value_comes_back_as_str_not_bytes`, whose docstring records that a
Redis client left at its defaults returns `bytes`, so a caller comparing
against a string literal matches in every `MemoryCache` test and never matches
in production. The port says `str`; only a shared body asserted against both
adapters says so where it counts. The awkward cases are deliberately in the
shared suite for the same reason — `increment` on a key that `set` wrote,
`ttl_seconds` on a non-first increment, a hit window with two events at the
same instant. **"Learned twice" is the entry.** A shape that recurs after
being fixed is not bad luck; it is the sign that the fix was a per-adapter
test rather than a shared one.

**(c) §3 — `exhaustive = true` on the import contract, after it had caught
zero violations (`db9805d`).** A check that has never fired is
indistinguishable from a check that cannot fire, which is §3 applied to a
gate rather than a counter. Turning the option on was not cosmetic: import-linter
rejects `exhaustive` outright for a contract with no `containers`, so it
required adding `containers = ["redstring"]` and making every layer name
relative. It was then *proven to bite* the only way that means anything —
adding a throwaway top-level package, watching the contract fail, and removing
it. A new top-level package is now a contract failure until someone places it
deliberately. **Never leave a gate in the "passed, never seen fail" state;
break it on purpose once.**

**(d) §3 and §1 — both strictness exemption lists emptied (`6058746`,
`1a73c33`), after `6a473ff` recorded why the obvious measurement was
worthless.** The order matters. A review measured what the `events/**`
exemption was hiding with
`ruff check --select ANN,TC src/redstring/events/` and got "All checks
passed!" — a result that was **unconditional**, because `per-file-ignores`
applies on top of `--select` and the ignore was exactly `["ANN", "TC"]`. The
command could not have reported a finding whatever the code said; it is §4's
"what other implementation would also pass this?" wearing a lint invocation.
Deleting the entry surfaced **ten**. Nine of those turned out to be a
*misconfiguration* rather than debt —
`runtime-evaluated-base-classes` matches the base class as written in the file
rather than through the MRO, and every event declares `TenantDomainEvent`, not
`pydantic.BaseModel`. Applying ruff's own suggested fix left the module
importable and broke 23 tests with `PydanticUserError: not fully defined`,
because pydantic resolves field annotations at schema-build time: **an import
smoke test passes; only using the model catches it.** So an exemption can
absorb a misconfiguration indefinitely, which is the strongest argument for
removing exemptions early. The lasting rule — *delete the entry and run the
configured gate, because the command measuring an exemption must not be
subject to it* — is in CLAUDE.md and
`docs/adr/0014-exemption-lists-are-empty-and-must-stay-falsifiable.md`; mypy's
version of the trap is naming files on the command line, which bypasses
`exclude` and answers a different question than the configured run.

**(e) §5 — the layer diagram in binding instructions, and eight source-less
`__pycache__` trees (`059c8e6`; `6daee21`, `d121b7d`).** `059c8e6` fixed a
CLAUDE.md diagram still showing the pre-slice-9 layering after `services`,
`models`, `db` and `schemas` were deleted and `consolidation` and `temporal`
joined the sibling band — in the one file loaded into *every* session, so an
author placing a new module by it would have been told to put it under a layer
that does not exist. Found in review of the slice that made it stale, which is
the only reliable time to find it. The `__pycache__` half is §5 at the
filesystem level: `git rm -r` removes the tracked sources and the ignored
bytecode tree survives as a source-less directory under a package path, with
`git status` clean. Slices 6 and 7 left four between them (`6daee21`); slice 9
left eight more — `models`, `schemas`, `services`, `services/extraction` and
their four mirrored test directories — removed in `d121b7d`. Harmless in
itself, since Python 3 will not import from a `__pycache__` without its
source, but **the removal produces no diff**, so both commits exist only
because the note landed in `BACKLOG.md` (B37) alongside. A deletion sweep's
grep has to cover `.claude/`, `CLAUDE.md` and the untracked tree, not just
`docs/`.

**(f) §4 and §1 — an inferred edge's direction derived from a sort
(`010d8f2`), and the habit that found its twin (`aa0e2eb`).** The campaign's
only Critical. `temporal/inference.py` claimed `AFTER` and `DURING` never
appear in its output, and `INFERRED_RELATIONS` omitted them accordingly — but
that claim was an argument about `order_key`, not a property of the code.
`order_key` sorts by lower bound then upper bound ascending, so two extents
sharing a lower bound put the *shorter* first, `relate` from shorter to longer
is `DURING`, and the default filter discarded it: the pair produced **no edge
at all**. Not exotic input — "2023" and "2023-2025" both come straight out of
the parser, a month and the year it opens, an event and the era beginning with
it. The suite missed it in §4's own terms: the direction tests used disjoint
years (1900/1950/2000) and the single containment test put March 2023 inside
2023, whose lower bounds *differ*, so the container sorted first and the right
answer came out — inputs on which the wrong canonicalisation and the right one
agree. **The lesson is structural rather than about inputs: an invariant that
holds by an argument about sort order is inferred, not enforced.**
Canonicalising from the computed relation makes it true by construction. And
the habit worth copying is in `aa0e2eb`: fixing it, the same reasoning turned
up a *second* time in the same file, in a map entry no test could reach whose
justification was also an argument about the sort. Grep for the second
instance before closing — both times this project has looked, it was there.

**(g) §1 and §3 — a comment that correctly described what the code had to do,
directly above code that did not do it (`31093f9`).** `RedisCache` recorded a
hit with `pipe.zadd(window, {f"{at!r}:{id(self):x}": at})`, under this comment:
"the member must be unique or two hits at the same instant collapse into one,
which under-counts exactly when a burst is what the caller is trying to
detect." The comment is right. `id(self)` is the *cache object's* address —
constant for its whole life — so it distinguished two `RedisCache` instances
and never two hits, which was the only thing it was there for; across
processes an address can collide between two callers sharing one Redis. So
`count_hits` under-reported precisely in the case the comment names.
`MemoryCache` cannot exhibit it at all, because it appends to a list. **This
is the §1 shape demonstrated by the one adapter that had been excused from
the shared suite written about it**, and the §3 shape at the same time — a
uniqueness guard that never once made anything unique. Fixed with
`uuid4().hex`, proved by restoring `id(self)` and watching the test fail. Two
lessons: an adapter outside its compliance suite is where this shape lives,
and *a comment stating an invariant is not evidence the line beneath it holds
one* — read the expression, not the intent.

**(h) §3 — a `Typing :: Typed` classifier that was a false claim, and nothing
in the repo could have noticed (`9a99170`).** PEP 561 says a type checker
ignores a dependency's annotations entirely unless the installed package
carries a `py.typed` marker. Without it, `mypy --strict` over this whole
package bought downstream callers *nothing* — `redstring` resolved as `Any`
for all of them — while the repo's own gate was as green as it would ever be.
It could not have been caught from inside: every test imports from `src/`,
where annotations are read directly, so the difference is invisible except in
an installed wheel. The fix was the marker plus
`tests/integration/test_wheel_contents.py` asserting it survives packaging,
proved red by removing it. **When a claim is about the artifact, only the
artifact can falsify it.**

**(i) §3 — a projection, an event and an aggregate command with no caller,
for six slices (`f87ba86`).** `VectorProjection` and
`Document.record_embeddings` were both written when `EntitiesEmbedded` was
designed, and nothing ever emitted one. The port they served looked complete
from the inside — tests, types, a compliance suite — because everything
existed except the call. Building `EmbeddingProvider` turned out to be mostly
*calling* code that was already there. **Code that is fully tested and never
invoked passes every gate this repository has**, so the question to ask of a
new component is not "is it covered" but "what in the tree reaches it".
