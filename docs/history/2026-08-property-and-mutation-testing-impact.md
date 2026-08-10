# The impact of property and mutation testing

*Written 2026-08-10, measured at `4ea2086`. Sources: 238 commits
(2026-08-03 → 2026-08-09), `BACKLOG.md`, `CLAUDE.md`,
`.claude/rules/recurring-defects.md`, `docs/adr/`, and the test tree.*

## Summary

`hypothesis`, `mutmut` and `cosmic-ray` all arrived in the **same commit**
(`63f93f9`, the dev-tooling commit); the mutation *config* followed two commits
later in `d936788`. Adoption was simultaneous and immediate — the first
mutation-driven fix, "Close nine compliance-suite gaps found by cosmic-ray"
(`54553fe`), landed the same day.

Neither technique has been a background quality metric. Both are visible in the
log as *causes of specific commits*: **roughly 50 of 238 commits (~21%)** exist
because a mutant survived, a mutation run exposed a gap, or a property
falsified something.

The largest single effect is second-order. Mutation testing produced a
**written catalogue of nineteen ways a test can pass while proving nothing**,
grown from zero to nineteen rows over **three days** (2026-08-03 → 2026-08-05)
and unchanged since — plus a rules file, a compliance-coverage gate, and five
ADRs whose reasoning is a survivor.

---

## 1. Adoption timeline

| Date | Event | Commit |
|---|---|---|
| 2026-08-03 | `hypothesis`, `mutmut`, `cosmic-ray` added together | `63f93f9` |
| 2026-08-03 | Mutation config lands | `d936788` |
| 2026-08-03 | First mutation-driven fix: nine compliance-suite gaps | `54553fe` |
| 2026-08-03 | First failure shape written into CLAUDE.md | `c1d43a5` |
| 2026-08-04 | Read-method isolation coverage becomes an enforced gate | `ba6a9fe` |
| 2026-08-04 | "A property test is a sampler, not a proof"; `k=0` pinned | `7d986ed`, `05efe7e` |
| 2026-08-04 | Hypothesis deadline policy consolidated to one site | `ab40cb8` |
| 2026-08-05 | `scripts/mutation.py` wrapper built | `014daea` |
| 2026-08-05 | Wrapper stops trusting a timeout as a kill; table reaches 19 rows | `027caa4` |
| 2026-08-09 | Property-driven compliance suites ship as public API | `e81eecc` |

## 2. Current footprint

| Metric | Value | Note |
|---|---|---|
| Test functions | 2,064 | 1,835 under `tests/`, 229 shipped in `src/redstring/testing/` |
| Test modules (`test_*.py`) | 128 | |
| Collected by the default gate | 2,209 | plus 250 `integration`, 4 `accuracy` (`BACKLOG.md`) |
| `@given` decorators | 118 | |
| `@example` decorators | 6 | `test_entity.py` (4), `test_fusion.py` (2) |
| Modules importing hypothesis | 29 | 25 under `tests/`, 4 in `src/redstring/testing/` |
| `test_mutating_*` isolation tests | 16 | 12 shipped in `redstring.testing` |
| Enforced isolation registry entries | 8 | `tests/unit/graph/test_compliance_coverage.py:62` |
| Coverage | 96.33% | ratcheted from 93.28% over 25 raises |

`@given` growth: 0 → 11 → 32 (08-03), → 107 (08-04), 109, → 118 (08-09).

**One correction to a tempting story.** Test-function count *fell* sharply on
2026-08-04 — 2,087 → 1,388 across four commits, driven by `1b9f9f3` (delete
`services/`, −227) and `7ef7a03` (delete `models/`, `db.py`, the ORM schemas,
−439). It is tempting to read the simultaneous property growth as properties
*replacing* example tables. They did not. `@given` was 107 before those
deletions and 109 after — flat. What was deleted was legacy ORM and service
suites, including the 826-line strategy-router file that supplied every input
as a `MagicMock` across 24 mock/patch sites. Nothing replaced them; they were
dead weight. The honest claim is the weaker one: **the raw test count is
dominated by a legacy deletion, and property adoption was unaffected by it.**

---

## 3. What mutation testing found

### Real production defects

- **`_MONTH_NUMBERS` carried a spelling `_MONTH` could not produce.** The table
  mapped `"Sept"` → 9; the pattern accepted only `Sep(?:tember)?`. `"Sept 2024"`
  fell through every pattern to `dateparser`, resolved differently against the
  two probe dates, and raised `AmbiguousReferenceDateError` instead of parsing.
  Two declarations of one fact, disagreeing with nothing failing. **Only
  findable this way** — mutating the branch changed nothing observable, because
  no input reached it. Closed with
  `test_every_spelling_the_table_maps_is_one_the_pattern_accepts`, proved red by
  reverting the pattern.
- **Four shallow-copy leaks across `GraphStore` read methods**
  (`find_by_blocking_key`, `neighbors`, `find_by_blocking_keys`,
  `get_relationships_for`). Each shipped with full behavioural tests.
  `docs/adr/0002-two-store-ports.md:507`: "each time a mutation run — not
  review, not the property tests — found that a shallow copy passed
  everything." Returning the live internal object is *correct on every read*
  and wrong only afterwards, so no assertion about the returned value can see
  it.
- **Inferred-edge direction derived from a sort order** (`010d8f2`). The
  module's stated invariant — `DURING` never appears — held only for inputs
  where the sort happened to put the container first. Fixed by canonicalising
  from the computed relation; grepping for a second instance found one in the
  same file.
- **`end.year <= start.year` rewritten as `is` survived** in `render_temporal`:
  `"2023-2023"` rendered as a range the parser then refuses to read back.
- **`is` where `==` was meant on resolution by value** — `0002:349` records
  `test_resolution_by_value_not_by_identity` as existing "after a cosmic-ray
  mutant survived the `is` spelling".
- **A load-bearing comment that was not.** `docs/adr/0004:63` — "Eight
  hand-applied mutants died against it. The ninth survived and was the useful
  one": a comment in `_apply_undo` claiming an ordering mattered when it did
  not.

### Tests that proved nothing

The larger yield. The canonical case: slice 5b's replay-equivalence suite had
all three properties the design asked for, and **all three passed against a
handler that never applied an undo, never deleted a dropped edge, and never
wrote relationships at all.** Six handler mutants, three survived. Both sides of
an equivalence run the same fold, so a fold doing too little leaves both sides
agreeing on the same wrong state. An independent oracle killed all six.

`CLAUDE.md` now names **nineteen** distinct input shapes that make a wrong
implementation agree with a right one. Five are identity-vs-equality; three of
those fired because the test value sat inside a CPython cache. The one worth
singling out is not a cache at all:

> `(19 - 1) * 100` is 1800, which shares **no set bit** with 1, 33, 34, 66, 67
> or 100 — so `base + k`, `base | k` and `base ^ k` are the same number, and
> `century - 1` equals `century ^ 1` for any odd century. Every existing case
> used the 19th century, the natural example for a library reading historical
> text. **Eleven mutants unkillable.**

The table also documents its own recurrence: the *bounds that never coincide*
shape appears twice, sixteen rows apart, in two different modules and two years
apart — found the second time by mutation while the first was still the
headline example beside it.

### Measured survivor classifications (`temporal_parsing.py`)

| Region | Mutants | Survivors | Equivalent | Gaps closed | Real defects |
|---|---|---|---|---|---|
| ranges | 268 | 22 | 17 | 4 | 1 |
| periods / centuries | 176 | 28 | 13 | 15 | 0 |
| `render_temporal` | 159 | 7 | 6 | 1 | 0 |

A fourth session covered `widen`'s first third. **Four sessions, four
findings** — every region looked at produced something. 391 of ~850 mutants in
the module verified; 459 remain (B54).

**The source disagrees with itself, and that is worth recording rather than
restating.** B54's region table lists `ranges` at 161 mutants where its own
prose reports 268, and `render_temporal` at 126 where the prose reports 159;
the rows marked "run" do not sum to the headline 391. The entry warns that the
bands drift under edits — but the arithmetic in the same entry does not close.
Filed as B117.

---

## 4. What hypothesis changed

- **Properties became the compliance contract.** `redstring.testing` ships five
  suites so an adapter written in someone else's repository runs the same
  bodies. Two are hypothesis-driven end to end (`graph_store`, `vector_store`);
  `CacheCompliance` uses no hypothesis at all, its inputs being example-based.
  That nuance is the point — properties were used where a property was cheaper
  to state, not everywhere.
- **It surfaced order-dependence example tests hid.** `hypothesis` runs every
  example against one function-scoped fixture, so example 7 sees what 1–6 left
  behind — an intermittent `MissingEntityError` about one run in three.
- **The `suppress_health_check` lesson.** That flake was *hidden* by
  `suppress_health_check=[HealthCheck.function_scoped_fixture]`, added with a
  confident comment explaining why it was safe. The health check was hypothesis
  reporting the exact bug.
- **Deadline policy consolidated to one site** (`ab40cb8`). Nineteen
  `@settings` decorators carried `deadline=None` and eight did not — not a
  judgement, just whoever wrote them not thinking about it. A third of the
  property suite therefore enforced a timing deadline that detects nothing
  systematically and blocks a commit occasionally. It duly blocked one, at
  276.11ms against a 200ms deadline, taking 1.28ms on re-run. The transferable
  mechanism: **an explicit value in a decorator outranks every profile,
  silently**, so leaving the inline settings would have made the new profile
  half-inert — the same trap `max_examples` already documents.
- **It caught the one real behaviour change in the normalizer collapse.**
  `014daea`: "The extraction property tests found the one real behaviour
  change, which is the part I would have shipped wrong."

### Where each corrected the other

**Mutation exposed hypothesis's sampling limits.** Two mutants in
`InMemoryVectorStore.search` (`k < 0` → `k <= 0` and `k < 1`) were **killed on
one run and survived the next, with nothing about the adapter changed.** `k=0`
was covered only by a property drawing `k` from `0..12`, so whether the
boundary was tested depended on the sampler and on `KG_COMPLIANCE_MAX_EXAMPLES`,
which mutation runs lower to 5. Coverage of the boundary was
*non-deterministic*, and the natural reading of a survivor that used to die is
"the source changed". Nothing had. The fix was a pinned assertion in the
compliance suite plus, elsewhere, the six `@example` decorators.

**Mutation exposed properties that could not fail.** In slice 6 a hypothesis
property written *specifically* to catch tie-break defects fed the tie-breaker
objects equal in every field. "First wins" and "last wins" agree on equal
objects, for any implementation. It read as a strong test right up until
someone tried to break it — which is why the standing rule is now *break the
implementation on purpose and watch the property fail before trusting it.*

---

## 5. Cost, and how it was contained

Mutation testing is expensive and **all four of its failure modes here were
silent**:

1. **Two zero-survivor runs were worthless** — 0/426 and 0/45. A worktree
   synced without a required extra, so every mutant "died" on a collection
   error. `cr-report` reported `KILLED` for all 426, indistinguishable from an
   outstanding suite.
2. **212 mutants counted as "run" were timeouts.** cosmic-ray records a timeout
   as `KILLED`. At load average ~100 on 16 cores with a 30s budget, a mutant
   failing with a `TypeError` in the first second still timed out.
3. **A mutant escaped into the working tree and passed the full default
   suite** — because the Neo4j adapter's only tests are `integration`-marked
   and deselected by default. `docs/reference/quality-gates.md:1648`: mutation
   testing inherits the deselection blind spot. `git diff --quiet` after a run
   is the minimum check.
4. **Cost:** ~70s/mutant against the default command — the whole temporal
   module is ~17 hours. Scoped to one test class it is ~7s.

Containment is `scripts/mutation.py`, which **refuses rather than warns**. It
builds a detached worktree, syncs `--all-extras`, runs the tool's configured
test command unmutated *there*, and requires green **with a positive pass
count** — because "0 failed" and "0 collected" share an exit status.
`timeout_verdict` (`scripts/mutation.py:102`) adds two more refusals, for *all*
kills being timeouts and for *most* being: "a survivor count over a session of
timeouts is not evidence."

**Never gate on a raw survivor count.** Measured on `graph/adapters/memory.py`:
230 mutants, 78 survivors, **73 equivalent** — 55 of those annotations alone,
because PEP 563 makes `X | None` a string that is never evaluated and
cosmic-ray rewrites the `|` eleven ways. A gate on survival percentage would
reward *deleting type annotations*. The Neo4j figure has the same shape: 16 of
289 mutants reached (5.5%), 11 killed, 5 survived — and all five survivors were
`ReplaceBinaryOperator_BitOr_*` annotation mutants. The bar is "every survivor
is understood", never a number.

---

## 6. What the techniques built that outlives them

The findings are the smaller half. These are permanent structures whose
existence traces to a mutant:

| Artefact | Exists because |
|---|---|
| CLAUDE.md's 19-row failure-shape table | nineteen survivors, written up one at a time |
| `.claude/rules/recurring-defects.md` §4, "Tests that encode the bug as the spec" | the audit-side companion to that table |
| its checklist: *"Added a read method to a store port? Its isolation and tenant tests exist"* | the four shallow-copy leaks |
| `test_compliance_coverage.py` isolation + tenant registries | the same four — a written rule had already failed four times |
| `test_the_port_has_read_methods_to_check` | a detector over an empty set passes vacuously |
| `ISOLATION_EXEMPT = {}` kept deliberately empty | the empty-exemption rule |
| `scripts/mutation.py` baseline + positive-pass-count refusal | the 0/426 run |
| its `timeout_verdict` refusal | the 212 timeouts |
| `test_hypothesis_deadline_policy.py` | the 276ms flake |

`recurring-defects.md` divides labour with the CLAUDE.md table explicitly:
**read the table when writing a test; read §4 when auditing one that already
exists.** Its §3, "Inert code and always-zero metrics", is the shape behind
`0c9e960` (an unreachable guard dropped) and `3d7c145`.

Five ADRs record decisions whose *rationale* is a mutation result:

- **`0010-one-total-order-for-preference.md:92`** carries a section titled
  "Why totality is what makes a `>` → `>=` mutant equivalent rather than live".
  Labelling a survivor equivalent is a *claim about the order*, so totality had
  to be asserted as a property before the label was honest. This is an
  architectural decision argued from a survivor.
- **`0002-two-store-ports.md`** — the four leaks (`:507`); the identity/value
  test (`:349`); `Alias` added to the isolation type-set "the moment the port
  gained one, rather than after a mutation run found the leak" (`:548`), the
  habit generalising *ahead* of the finding; and the standing obligation at
  `:825`.
- **`0001-event-log-schema-and-granularity.md:200`** — the `@register_event`
  decorator-deletion mutant (`4bed902`).
- **`0004-consolidation-emits-events.md:55`** — the replay suite's design
  requirements ("a diamond rather than a chain", "control assertions that fail
  a do-nothing merge") written *as* mutation-resistance criteria.
- **`0014-exemption-lists-are-empty-and-must-stay-falsifiable.md:694`** — the
  annotation-survivor class used as an argument in an exemption decision.

---

## 7. Assessment

**Mutation testing produced the visible record and every derived rule.** It
found defects structurally invisible to any other method available here —
unreachable branches, shallow copies, self-consistent-but-wrong folds, and
arithmetic exercised at exactly one value that happened to make three operators
agree.

**But the commit log overstates the gap between the two techniques, for two
reasons.** First, selection bias in what gets written down: a mutation survivor
*cannot be closed* without a written classification, while a falsifying example
is just a red test you fix before committing and never becomes an artefact. The
log measures documentation discipline as much as defect discovery. Second, and
more important: **mutation testing's kills are hypothesis's work.** Every
"mutant died" result in the store compliance suites is a property doing the
killing. These are not two independent instruments to be scored against each
other — one is the detector, the other measures the detector.

The fairest statement is that **mutation testing is the metrology and
hypothesis is the instrument.** Several findings are explicitly *"the test was
the problem, not the mutant"* (`05efe7e`: "The cause is the test, not the
mutants") — those are hypothesis's failures, correctly attributed to mutation
for exposing them. A project without hypothesis would not have had fewer
mutation survivors; it would have had more.

The strongest evidence that adoption worked is not a number. It is that this
project can point, in writing, to nineteen specific ways a test can pass while
proving nothing — eighteen of which it discovered by having them happen — and
that the discovery of the nineteenth cites the first.

### Open exposure

- **B54:** 459 of ~850 `temporal_parsing.py` mutants unrun, including a
  68-mutant band no version of the tracking table had ever listed — invisible
  because the bands were written from the module's section headings rather than
  from a measurement.
- **B10e:** the Neo4j adapter's mutation coverage is **unestablished** at 5.5%.
  Do not read it as mutation-tested.
- **B117** (filed with this document): three doc-drift instances found while
  writing it, including `recurring-defects.md` describing the table as
  eighteen rows.
