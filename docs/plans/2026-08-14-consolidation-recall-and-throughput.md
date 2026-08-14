# Consolidation: recall first, then throughput

Two separable pieces of work, in that order. **A** fixes a scoring gap that
keeps obvious duplicates away from the model entirely. **B** adds bounded
concurrency and cross-subject batching to a consolidation pass that currently
has no corpus-level driver at all.

A leads because B's only effect is to run the decision procedure more often,
and there is no value in running a leaky one faster.

## The forcing observation

Downstream graphs accumulate "Lord Voldemort" and "Voldemort" as separate
entities. Tracing that pair through the existing pipeline:

- **Blocking finds it.** The two share no prefix key (`p:lord ` vs `p:volde`)
  and no soundex key (the whole name is coded, so the title changes it), but
  they share the entity-type key, which exists precisely so nothing is
  unblockable. The pair reaches `CandidateFinder.candidates`.
- **Scoring rejects it.** `string_similarity` is Jaro-Winkler, which is
  prefix-weighted:

  ```
  string_similarity("Lord Voldemort", "Voldemort") = 0.531
  ```

  Under the default `FeatureWeights(name=0.5, embedding=0.3, graph=0.2)`, the
  pair reaches `LOW_SIMILARITY = 0.75` — the floor to be *asked about* — only
  if

  ```
  0.3·embedding + 0.2·graph ≥ 0.485      (maximum attainable: 0.500)
  ```

  which needs both remaining features above ~0.97. In practice the pair scores
  `REJECT` and no model call is ever made.

The failure is structural rather than a tuning miss. Jaro-Winkler's prefix
bonus penalises hardest exactly the alias shape natural-language text produces
most: **a name qualified by a leading title, honorific, or epithet.** "Lord
Voldemort"/"Voldemort", "Dr. Grant"/"Grant", "President Bartlet"/"Bartlet" all
fail the same way, and each is a pair a human resolves without hesitating.

Widening the band would not fix it — `LOW_SIMILARITY` would have to fall below
0.53, which sends most of a type-key block to the model and reintroduces the
quadratic cost the band exists to avoid.

## A. Overlap-aware name similarity

### Decision

`string_similarity` becomes the **maximum** of Jaro-Winkler and a capped token
overlap coefficient:

```
string_similarity(a, b) = max(
    jaro_winkler(norm(a), norm(b)),
    CONTAINMENT_CEILING · overlap_coefficient(tokens(a), tokens(b)),
)

overlap_coefficient(A, B) = |A ∩ B| / min(|A|, |B|)      (0.0 if either is empty)
```

A fourth feature was considered and rejected: `combined_score` renormalizes
over present features, so adding one dilutes the other three for every
existing caller and moves every score in the corpus. Strengthening the name
feature moves only the pairs the name feature was wrong about.

### The ceiling is the whole safety argument

`CONTAINMENT_CEILING = 0.85`, and the constant is chosen against the two
thresholds it sits between:

```
LOW_SIMILARITY (0.75)  ≤  CONTAINMENT_CEILING (0.85)  <  HIGH_SIMILARITY (0.92)
```

- **Below `HIGH`** means a token-subset match can *never* merge on its own. It
  buys the pair a model call, not a merge. This matters because containment is
  a weak signal in one specific direction: `{smith} ⊂ {john, smith}` scores 1.0
  and "Smith" is a surname shared by millions. Those pairs must be adjudicated,
  and the ceiling is what guarantees they are.
- **At or above `LOW`** means it always reaches the band rather than sometimes
  landing short of it depending on the other features.

That two-sided relation is the invariant to pin with a test. It is also a
cross-layer one — the constant lives in `domain.similarity` and the thresholds
in `consolidation.policy`, and `domain` cannot import `consolidation` — so the
test lives on the consolidation side, which may import downward.

### Why name tokens are not `domain.tokenize.tokenize`

`tokenize` exists for BM25 and drops stopwords. Reusing it would couple merge
decisions to the lexical retrieval tokenizer, so a change made for ranking
reasons would silently move which entities merge. Name tokens are
`normalize_name(x).split()` — defined in `similarity.py`, owned by the thing
that uses them.

### What this changes for the Voldemort pair

`overlap_coefficient({lord, voldemort}, {voldemort}) = 1/1 = 1.0`, so the name
feature becomes `0.85` rather than `0.531`. With no embedding and no graph
neighbours the combined score is `0.85` — `ADJUDICATE`. The model is asked, the
verdict lands on `EntitiesMerged.merge_reason`, and the merge is auditable and
undoable. That is the outcome the band was designed to produce.

Pairs sharing no tokens are untouched: `overlap_coefficient` is `0.0` for
"Tom Riddle"/"Voldemort", and `max` leaves Jaro-Winkler in place.

`string_similarity`'s two documented properties survive. It is still symmetric
(overlap uses `min` of the two sizes, so argument order does not matter), and
still exactly `1.0` iff the normalized names are equal (the overlap term is
capped at `0.85`, so only Jaro-Winkler can reach `1.0`).

### Measuring it

There is no consolidation accuracy harness — `tests/accuracy/` grades
extraction only. Rather than extend that suite, this adds a **labelled banding
corpus** as a unit test: pairs of names with the band each should land in.

This is deterministic and needs no model or endpoint, because the claim under
test is which band the *policy* assigns, not what a model says about the band.
That makes it a regression gate on the weights and thresholds that runs on
every commit, which the accuracy suite deliberately is not.

The corpus must carry both directions or it measures nothing:

- pairs that must reach at least `ADJUDICATE` (title/epithet aliases,
  surname-only mentions, initialisms)
- pairs that must stay `REJECT` (different people sharing a surname, sibling
  institutions like "University of Oxford"/"University of Cambridge", the
  same-token-different-referent cases containment is most likely to break)

The second group is the one that makes the first meaningful. A corpus of only
should-merge pairs is satisfied by scoring everything `1.0`.

### Risk this accepts

**Adjudication volume rises**, because pairs that used to be rejected without a
call now get one. That is the intended trade — those calls are the ones that
find the duplicates being missed today — but it is a real cost increase for
callers with an adjudicator wired, and it lands in the same release as B, which
makes the pass run more often. The banding corpus is what bounds it: the
`REJECT` half is a direct check that containment has not turned a type-key
block into a stream of model calls.

## B. A corpus-level consolidation pass

### The gap

`ConsolidationService.resolve` and `Consolidator.resolve` both handle exactly
one subject. Nothing in the library loops over a corpus, so every caller writes
that loop, and every caller writes it serially.

### Why this is not extraction's concurrency problem

ADR 0039 could fan out over chunks because chunks are independent. Subjects are
not:

1. **Each merge changes the graph the next subject reads.** Candidate finding
   resolves through aliases; a merge emitted for subject *i* changes what
   subject *j* blocks against.
2. **`ConsolidationLog` uses optimistic concurrency on the tenant stream.** Two
   concurrent merges within one tenant collide by construction — the stream is
   the tenant deliberately, and that is not a decision to revisit here.

So the fan-out cannot be over `resolve`. It has to split `resolve` into its
read-and-decide half (pure reads plus model calls, safely concurrent) and its
emit half (serialized per tenant).

### Decision

A two-phase pass:

**Phase 1 — decide, concurrently.** For each subject, block, score, and band.
Bounded by a `CallLimiter`, so model calls respect the same endpoint ceiling
extraction does. Produces a decision per subject and touches no store.

**Phase 2 — emit, serially.** Walk the decisions in a deterministic order and
emit each. Before emitting, re-resolve the subject and its absorbed entities
through `resolve_entity_ids`: an earlier emit in this same pass may have made
one of them an alias. A subject that has been merged away is skipped, not
retried — it will be reconsidered on the next pass, and its duplicates now
belong to the entity that absorbed it.

The staleness window `ConsolidationService` already documents between its read
and its append gets wider here, since phase 1 completes before any of phase 2
runs. The re-resolution in phase 2 is what makes that safe, and BACKLOG B43's
known gap is unchanged by it, not worsened — that entry is about a parallel
edge created by the extraction fold, not about ordering within a pass.

**Cross-subject adjudication batching.** Today `Adjudicator` batches within one
subject, so a subject with two ambiguous candidates spends a whole model call
on two pairs. Phase 1 collects ambiguous pairs from every subject and fills
batches of `ADJUDICATION_BATCH_SIZE` across subject boundaries.

The existing safety property must survive the change: verdicts re-pair **by
position**, a short batch yields `None` for every pair in it rather than for
the tail, and ids stay out of the prompt. Coalescing across subjects means the
mapping from batch position back to `(subject, candidate)` is now
non-trivial — that mapping is where this feature will break, and it is what to
test hardest.

### The `CallLimiter` layering question

`CallLimiter` lives in `extraction/limiter.py`. `consolidation` is a *sibling*
of `extraction` in the import contract, forbidden from importing it, so the
batch pass cannot use it where it sits.

It moves to `domain/limiter.py`. It is a pure asyncio primitive with no I/O and
no dependency on anything above `domain`, which is the same test every other
`domain` module passes; `domain` already holds algorithms (`bm25`, `fusion`,
`similarity`) rather than only value types, so this is not a new kind of
occupant. The public name `CallLimiter` is unchanged, so `__all__` and every
caller are unaffected — this is an internal move, and ADR 0006's gate does not
see it.

The alternative — a second limiter type for consolidation — is rejected outright:
the whole argument of ADR 0039 is that the ceiling is on **calls in flight
against one backend**, regardless of which code path issued them. Two limiters
is two ceilings, which is no ceiling.

### Deliberately not in scope

- **No concurrency across tenants.** The stream is per-tenant and so is the
  pass. A caller running several tenants concurrently is a composition question
  and shares one `CallLimiter`, exactly as ADR 0039 leaves multi-document.
- **No adaptive tuning.** `concurrency` is a number the caller sets.
- **No progress reporting** mid-pass.
- **No transitive closure.** One pass resolves each subject against the graph
  as phase 1 saw it. Chains that only appear after a merge are the next pass's
  work, and saying so is cheaper than making one pass fixed-point.

## ADRs

Run against the existing set:

| ADR | Verdict |
|---|---|
| `0004` consolidation emits events | **Stands.** Phase 2 emits; a projection writes. The split makes the separation more load-bearing, not less. |
| `0010` one total order for preference | **Stands**, and becomes load-bearing the way 0039 made it for extraction: phase 1's completion order varies, so the decisions must fold order-independently. |
| `0015` consolidation gets a composed entry point | **Amended.** The composed entry point gains a corpus-level method beside the per-subject one. |
| `0039` bounded concurrency over chunks | **Amended.** Its `CallLimiter` becomes a shared primitive rather than an extraction-owned one; its ceiling argument is extended to a second caller, which is what that argument predicted. |
| `0006` public surface is gated | **Stands.** `CallLimiter` is already exported; the move does not change the surface. A new public method on `Consolidator` pulls its closure and the gate will say so. |

New ADRs, numbered against `main` **at merge time**, not now:

1. **Overlap-aware name similarity** — why the name feature absorbed
   containment rather than becoming a fourth feature, and why the ceiling sits
   strictly between the two thresholds.
2. **The consolidation pass is decide-then-emit** — why fan-out is over the
   read half only, and why a stale subject is skipped rather than retried.

## Housekeeping found on the way

`CLAUDE.md`'s ADR table stops at `0019` while the tree is at `0039` — a stale
table of specifics in a file loaded into every session, which is
`recurring-defects.md` §5 happening to the file that documents §5. It goes in
`BACKLOG.md` if not fixed in this branch.
