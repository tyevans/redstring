# Consolidation Recall and Throughput Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make token-subset name aliases ("Lord Voldemort"/"Voldemort") reach the adjudication band instead of being silently rejected, then add a corpus-level consolidation pass with bounded concurrency and cross-subject adjudication batching.

**Architecture:** Part A strengthens the existing `name` feature in `domain/similarity.py` with a capped token overlap coefficient — no fourth feature, because `combined_score` renormalizes over present features and a fourth one would move every score in the corpus. Part B splits `ConsolidationService.resolve` into a score/band phase (concurrent, store reads only), an adjudication phase (cross-subject batches, `CallLimiter`-bounded), and an emit phase (serial, per-tenant, re-resolving through aliases before each merge).

**Tech Stack:** Python 3.12+, `uv`, `pytest` + `hypothesis`, `pydantic` v2, `jellyfish` (Jaro-Winkler, soundex), `asyncio`.

**Spec:** `docs/plans/2026-08-14-consolidation-recall-and-throughput.md`

## Global Constraints

- **Run tests yourself.** No hook runs `pytest`. Use `uv run pytest <path> -v -p no:randomly` as the inner loop; run the full `uv run pytest` before the final commit of each part.
- **Do not run `ruff`, `bandit`, or `lint-imports` as separate steps.** They are wired into `pre-commit` and run on `git commit`. If the hook fixes a file in place, `git add` it and commit again.
- **Deferred work goes in `BACKLOG.md` in the same commit that passes it by.** No TODO comments, no notes in commit messages as a substitute.
- **Commit messages:** imperative, sentence case, no trailing period, no `feat:`/`fix:` prefixes. The subject says what changed; the body carries counts, measurements, and what was learned.
- **Layer contract:** `domain` is the bottom layer and may import nothing else in the package. `consolidation` and `extraction` are siblings and may not import each other. `composition` is the top layer of the library proper.
- **Every exported name's signature may mention only exported types** — `redstring.__all__` is gated by three tests. If a new public parameter takes a type, that type must be exported too.
- **New read method on a store port?** It needs `test_<method>_returns_copies` and `test_<method>_never_crosses_tenants` on the compliance class. (No task here adds one; this is the guard if a task drifts.)
- **Regression tests must be proved red against the pre-fix source** with `git checkout HEAD~1 -- <paths>`, never `git stash` (the stash stack is shared across worktrees).
- **Existing constants, verbatim:** `HIGH_SIMILARITY = 0.92`, `LOW_SIMILARITY = 0.75`, `ADJUDICATION_BATCH_SIZE = 10`, `FeatureWeights(name=0.5, embedding=0.3, graph=0.2)`, `PREFIX_LENGTH = 5`.

---

## File Structure

**Part A — recall**

| File | Responsibility |
|---|---|
| `src/redstring/domain/similarity.py` | Modify. Add `name_tokens`, `overlap_coefficient`, `CONTAINMENT_CEILING`; `string_similarity` becomes the max of Jaro-Winkler and the capped overlap. |
| `tests/unit/domain/test_similarity.py` | Modify. Unit + property tests for the new functions and the preserved properties of `string_similarity`. |
| `tests/unit/consolidation/test_banding_corpus.py` | Create. A labelled corpus of name pairs and the band each must land in, plus the `LOW ≤ CEILING < HIGH` invariant. |
| `docs/adr/XXXX-overlap-aware-name-similarity.md` | Create. Numbered at merge time. |

**Part B — throughput**

| File | Responsibility |
|---|---|
| `src/redstring/domain/limiter.py` | Create (moved from `extraction/limiter.py`). `CallLimiter`, unchanged behaviour. |
| `src/redstring/extraction/limiter.py` | Delete. |
| `src/redstring/extraction/pipeline.py` | Modify. Import `CallLimiter` from its new home. |
| `src/redstring/consolidation/service.py` | Modify. Extract `_score_and_band` and `_emit`; add `resolve_many`. |
| `src/redstring/consolidation/policy.py` | Modify. Add `adjudicate_across_subjects`, filling batches from many subjects. |
| `src/redstring/composition/build_graph.py` | Modify. `Consolidator.resolve_many` returning `list[ConsolidationReport]`. |
| `src/redstring/__init__.py` | Modify. Export anything new the public signatures name. |
| `tests/unit/consolidation/test_resolve_many.py` | Create. Phase separation, ordering, staleness, mutual confirmation. |
| `tests/unit/consolidation/test_cross_subject_batching.py` | Create. Position mapping across subject boundaries. |
| `docs/adr/XXXX-the-consolidation-pass-is-decide-then-emit.md` | Create. Numbered at merge time. |

---

# PART A — RECALL

### Task 1: Name tokens and the overlap coefficient

Two pure functions, no behaviour change to anything yet. `string_similarity` is untouched in this task — that is Task 2 — so every existing test must still pass unmodified.

**Files:**
- Modify: `src/redstring/domain/similarity.py`
- Test: `tests/unit/domain/test_similarity.py`

**Interfaces:**
- Consumes: `normalize_name` from `redstring.domain.normalization` (already imported in this module).
- Produces:
  - `name_tokens(name: str) -> frozenset[str]`
  - `overlap_coefficient(left: Collection[Hashable], right: Collection[Hashable]) -> float`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/domain/test_similarity.py`:

```python
from redstring.domain.similarity import name_tokens, overlap_coefficient


def test_name_tokens_splits_the_normalized_name_on_whitespace():
    assert name_tokens("Lord  VOLDEMORT") == frozenset({"lord", "voldemort"})


def test_name_tokens_of_a_single_word_is_one_token():
    assert name_tokens("Voldemort") == frozenset({"voldemort"})


def test_name_tokens_deduplicates_repeated_words():
    """A set, not a list: "New York, New York" is two distinct tokens.

    Stated because the overlap coefficient divides by `min(|A|, |B|)`, so a
    repeated token in a multiset would inflate the denominator and quietly
    lower every score involving a name that repeats a word.
    """
    assert name_tokens("New York New York") == frozenset({"new", "york"})


def test_overlap_coefficient_of_a_subset_is_one():
    assert overlap_coefficient({"voldemort"}, {"lord", "voldemort"}) == 1.0


def test_overlap_coefficient_is_symmetric():
    assert overlap_coefficient({"lord", "voldemort"}, {"voldemort"}) == 1.0


def test_overlap_coefficient_of_disjoint_sets_is_zero():
    assert overlap_coefficient({"tom", "riddle"}, {"voldemort"}) == 0.0


def test_overlap_coefficient_divides_by_the_smaller_set():
    """`2/3`, not `2/4`: the divisor is `min`, which is what makes a subset 1.0."""
    assert overlap_coefficient(
        {"university", "of", "oxford"}, {"university", "of", "cambridge", "college"}
    ) == pytest.approx(2 / 3)


def test_overlap_coefficient_of_an_empty_set_is_zero():
    """Nothing is known about one side, which must not read as perfect agreement.

    The same reasoning as `graph_similarity`'s two-empty-sets case in this
    module's docstring: the mathematically conventional answer for a vacuous
    containment is 1.0, and 1.0 here would drag a merge over a threshold on
    the strength of an unparseable name.
    """
    assert overlap_coefficient(set(), {"voldemort"}) == 0.0
    assert overlap_coefficient({"voldemort"}, set()) == 0.0
    assert overlap_coefficient(set(), set()) == 0.0
```

Ensure `import pytest` is present at the top of the test module (it will already be there; check rather than adding a duplicate).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/domain/test_similarity.py -v -p no:randomly`
Expected: FAIL — `ImportError: cannot import name 'name_tokens' from 'redstring.domain.similarity'`

- [ ] **Step 3: Implement both functions**

In `src/redstring/domain/similarity.py`, after `string_similarity` and before `graph_similarity`:

```python
def name_tokens(name: str) -> frozenset[str]:
    """The distinct words of a normalized name.

    **Not `domain.tokenize.tokenize`, deliberately.** That function exists for
    BM25 and drops stopwords; reusing it would couple which entities merge to
    the lexical retrieval tokenizer, so a change made for ranking reasons
    would silently move merge decisions. The two tokenizers answer different
    questions and are allowed to disagree.

    A `frozenset` rather than a list, because `overlap_coefficient` divides by
    the size of the smaller side: in a multiset, "New York, New York" would
    have three tokens and every overlap involving it would be understated.
    """
    return frozenset(normalize_name(name).split())


def overlap_coefficient(left: Collection[Hashable], right: Collection[Hashable]) -> float:
    """`|A n B| / min(|A|, |B|)`, on `0..1`.

    The overlap coefficient rather than Jaccard, and the difference is the
    entire point: Jaccard of `{voldemort}` against `{lord, voldemort}` is
    `0.5`, because the extra token counts against the match. Here a title or
    epithet added to a name is not evidence against it, so the divisor is the
    smaller set and a subset scores `1.0`.

    That asymmetry of *meaning* does not make the function asymmetric --
    `min` is symmetric, so argument order does not matter.

    **An empty side is `0.0`, not `1.0`.** Every set vacuously contains the
    empty set, so the conventional answer is 1.0; here that would let a name
    that normalizes to nothing score a perfect match against everything. The
    same call this module's docstring makes for `graph_similarity`: no
    evidence is not perfect agreement.
    """
    left_set, right_set = set(left), set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / min(len(left_set), len(right_set))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/domain/test_similarity.py -v -p no:randomly`
Expected: PASS, including every pre-existing test in the file unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/redstring/domain/similarity.py tests/unit/domain/test_similarity.py
git commit -m "Add name tokens and the overlap coefficient as pure functions

Neither is wired into scoring yet. The overlap coefficient divides by
the smaller set rather than the union, so a name qualified by a title
scores 1.0 against the bare name -- Jaccard would score 0.5, counting
the title as evidence against the match."
```

---

### Task 2: `string_similarity` absorbs containment

**Files:**
- Modify: `src/redstring/domain/similarity.py`
- Test: `tests/unit/domain/test_similarity.py`

**Interfaces:**
- Consumes: `name_tokens`, `overlap_coefficient` from Task 1.
- Produces: `CONTAINMENT_CEILING: float = 0.85`; `string_similarity` with unchanged signature `(left: str, right: str) -> float`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/domain/test_similarity.py`:

```python
from redstring.domain.similarity import CONTAINMENT_CEILING, string_similarity


def test_a_name_qualified_by_a_title_scores_at_the_containment_ceiling():
    """Jaro-Winkler scores this 0.770 -- inside the band, but only just.

    The margin is the problem rather than the score: 0.770 clears
    LOW_SIMILARITY by 0.02, so an embedding below 0.717 drags the pair out of
    the band entirely. At the ceiling it survives an embedding down to 0.50.
    """
    assert string_similarity("Lord Voldemort", "Voldemort") == pytest.approx(CONTAINMENT_CEILING)


def test_containment_is_symmetric():
    assert string_similarity("Voldemort", "Lord Voldemort") == pytest.approx(
        string_similarity("Lord Voldemort", "Voldemort")
    )


def test_a_surname_only_mention_also_reaches_the_ceiling():
    assert string_similarity("Ada Lovelace", "Lovelace") == pytest.approx(CONTAINMENT_CEILING)


def test_names_sharing_no_tokens_are_unchanged():
    """`max` must leave Jaro-Winkler alone where containment says nothing.

    Asserted against the literal Jaro-Winkler value rather than against
    `string_similarity` itself -- an expectation written in terms of the
    function under test would hold for any implementation, including one
    where containment had swallowed the other branch entirely.
    """
    import jellyfish

    assert string_similarity("Tom Riddle", "Voldemort") == pytest.approx(
        jellyfish.jaro_winkler_similarity("tom riddle", "voldemort")
    )


def test_jaro_winkler_still_wins_when_it_is_the_higher_signal():
    """A near-typo shares no whole token, so containment is 0.0 and JW carries it."""
    score = string_similarity("Voldemort", "Voldemorte")
    assert score > CONTAINMENT_CEILING


def test_identical_names_still_score_exactly_one():
    """The ceiling is strictly below 1.0, so only Jaro-Winkler can reach it."""
    assert string_similarity("Ada  LOVELACE", "ada lovelace") == 1.0


def test_a_containment_match_can_never_reach_one():
    """Otherwise a subset name could merge without ever being asked about."""
    assert CONTAINMENT_CEILING < 1.0
    assert string_similarity("Lord Voldemort", "Voldemort") < 1.0


def test_partial_token_overlap_contributes_nothing():
    """The precision half. A shared "University of" must not carry the pair.

    Asserted as "containment did not raise the score" rather than as a bound
    on the score itself. Jaro-Winkler already scores this pair 0.899, which is
    *above* the ceiling and has nothing to do with this change; a test written
    as `< CONTAINMENT_CEILING` would fail on pre-existing behaviour and invite
    someone to move a threshold to satisfy a test written after the design.
    """
    import jellyfish

    assert string_similarity("University of Oxford", "University of Cambridge") == pytest.approx(
        jellyfish.jaro_winkler_similarity("university of oxford", "university of cambridge")
    )


def test_containment_can_never_produce_an_unasked_merge():
    """The invariant that makes the term safe, stated over every input at once.

    The containment branch is capped at `CONTAINMENT_CEILING`, so no input
    whatever can reach `HIGH_SIMILARITY` through it. A property of the
    arithmetic rather than of any corpus, so it is asserted as one rather than
    sampled -- see the banding corpus for why the corpus states the weaker,
    per-pair form of this.
    """
    assert CONTAINMENT_CEILING * 1.0 < 0.92
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/domain/test_similarity.py -v -p no:randomly`
Expected: FAIL — `ImportError: cannot import name 'CONTAINMENT_CEILING'`, and once that is added, `test_a_name_qualified_by_a_title_scores_at_the_containment_ceiling` fails with `0.7698... != 0.85`.

- [ ] **Step 3: Implement**

In `src/redstring/domain/similarity.py`, add the constant above `string_similarity`:

```python
#: The most a token-containment match alone may score.
#:
#: Chosen against the two thresholds in `consolidation.policy`, and the
#: relation between the three is the whole safety argument:
#:
#: ```
#: LOW_SIMILARITY (0.75)  <=  CONTAINMENT_CEILING (0.85)  <  HIGH_SIMILARITY (0.92)
#: ```
#:
#: **Strictly below `HIGH`** means containment buys a pair a model call, never
#: a merge. That matters because containment is weak in one direction:
#: `{smith}` is a subset of `{john, smith}` and scores 1.0, and "Smith" is a
#: surname shared by millions. Those pairs must be adjudicated, and this
#: ceiling is what guarantees they are rather than leaving it to whether the
#: other two features happen to be present.
#:
#: **At or above `LOW`** means a containment match always reaches the band,
#: rather than reaching it only when an embedding is available to carry it.
#:
#: The relation cannot be asserted in this module -- `domain` is the bottom
#: layer and cannot import `consolidation` -- so it is pinned from the
#: consolidation side, in `tests/unit/consolidation/test_banding_corpus.py`.
CONTAINMENT_CEILING = 0.85
```

Then replace the body of `string_similarity`:

```python
def string_similarity(left: str, right: str) -> float:
    """How alike two names are, on `0..1`. The better of two signals.

    Both sides are normalized first, so casing and whitespace do not count as
    differences -- `"Ada  LOVELACE"` and `"ada lovelace"` are the same name
    and score `1.0`.

    ## Why this is a maximum of two measures

    Jaro-Winkler alone is prefix-weighted, which penalises hardest the alias
    shape natural-language text produces most: a name qualified by a leading
    title, honorific or epithet. `"Lord Voldemort"` against `"Voldemort"`
    scores `0.437`, `0.519` and `0.578` for `"Dr. Grant"/"Grant"`,
    `"President Bartlet"/"Bartlet"` and
    `"Professor Albus Dumbledore"/"Dumbledore"` -- none of which can reach the
    adjudication band however strong the other features are. The score
    collapses as the qualifier grows relative to the name it qualifies, so a
    one-word epithet like `"Lord Voldemort"/"Voldemort"` survives at `0.770`
    and everything longer does not. Even the survivors clear the floor by
    hundredths, and a pair at `0.770` needs an embedding of `0.717` just to
    stay where it is -- so a mediocre second feature deletes it.

    Token containment answers exactly that case and nothing else: it is
    `0.0` for two names sharing no whole word, so `max` leaves Jaro-Winkler
    in place everywhere it was already right. Capping the containment term at
    `CONTAINMENT_CEILING` is what keeps a subset match from merging unasked;
    see that constant.

    A fourth *feature* was rejected rather than this: `combined_score`
    renormalizes over the features present, so adding one dilutes the other
    three for every existing caller and moves every score in the corpus.
    Strengthening the name feature moves only the pairs the name feature was
    wrong about.

    ## The two properties this has always had, and still has

    Symmetric -- `max` of two symmetric measures, and `overlap_coefficient`
    divides by `min`, so argument order does not matter. (Not free:
    Jaro-Winkler's prefix bonus is applied to whichever string comes first in
    some implementations.)

    Exactly `1.0` iff the normalized names are equal. The containment term is
    capped strictly below `1.0`, so only Jaro-Winkler can reach it.
    """
    normalized_left, normalized_right = normalize_name(left), normalize_name(right)
    jaro_winkler = jellyfish.jaro_winkler_similarity(normalized_left, normalized_right)
    containment = overlap_coefficient(name_tokens(normalized_left), name_tokens(normalized_right))
    return max(jaro_winkler, CONTAINMENT_CEILING * containment)
```

Update the module docstring's first bullet, which currently reads "**name** -- `string_similarity`, Jaro-Winkler over normalized names. Catches typos and inflections, and is fooled by two different people with the same name." Replace with:

```
- **name** -- `string_similarity`, the better of Jaro-Winkler and a capped
  token overlap over normalized names. Jaro-Winkler catches typos and
  inflections; the overlap term catches a name qualified by a title or
  epithet, which Jaro-Winkler's prefix bonus punishes hardest. Both are
  fooled by two different people with the same name, which is what the
  adjudication band is for.
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/domain/test_similarity.py -v -p no:randomly`
Expected: PASS.

Then run the consolidation suite, which consumes this function:

Run: `uv run pytest tests/unit/consolidation/ -v -p no:randomly`
Expected: PASS. **If a test here fails, do not weaken it.** A failure means an existing case asserted a score that this change legitimately moves — record the old and new values in the commit body and say why the new one is right. If a failure looks like a genuine precision regression (a pair that should not merge now does), stop and report rather than adjusting the constant to fit.

- [ ] **Step 5: Commit**

```bash
git add src/redstring/domain/similarity.py tests/unit/domain/test_similarity.py
git commit -m "Score a name against its own qualified forms

Jaro-Winkler is prefix-weighted, so it scores "Lord Voldemort" against
"Voldemort" at 0.770 and "Grant" against "Dr. Grant" at 0.437: the score
collapses as the qualifier grows relative to the name, crossing
LOW_SIMILARITY at about one short title. So the longer forms could not
buy a model call at all, and the shorter ones cleared the floor by 0.02
-- a margin any embedding below 0.717 erases.

string_similarity is now the max of Jaro-Winkler and a token overlap
coefficient capped at CONTAINMENT_CEILING = 0.85, which sits at or above
LOW_SIMILARITY and strictly below HIGH_SIMILARITY: containment buys a
pair an adjudication, never a merge. Pairs sharing no whole token score
0.0 on the new term and are left exactly where they were."
```

---

### Task 3: The labelled banding corpus

The regression gate for every threshold and weight in the system. Deterministic and model-free, because the claim is which band the *policy* assigns — not what a model says about the band.

**Files:**
- Create: `tests/unit/consolidation/test_banding_corpus.py`

**Interfaces:**
- Consumes: `CONTAINMENT_CEILING` (Task 2); `string_similarity`, `combined_score`, `SimilarityFeatures`, `FeatureWeights` from `redstring.domain.similarity`; `decide`, `MergeDecision`, `HIGH_SIMILARITY`, `LOW_SIMILARITY` from `redstring.consolidation.policy`.
- Produces: nothing imported elsewhere.

- [ ] **Step 1: Write the corpus test**

Create `tests/unit/consolidation/test_banding_corpus.py`:

```python
"""A labelled corpus of name pairs and the band each must land in.

## Why this is a unit test and not part of `tests/accuracy/`

`tests/accuracy/` grades *extraction* against a hand-graded document corpus,
needs an inference endpoint, and is deselected by default. The claim here
needs neither: which band a pair lands in is decided by `decide` over a
`combined_score`, both pure functions. So this runs on every commit, which is
what makes it a gate on the thresholds and weights rather than a periodic
measurement.

## Three claims, not two, and the middle one is the interesting one

A corpus of should-merge pairs alone is satisfied by a scorer returning `1.0`
for everything, so the precision half is what makes the recall half mean
anything. But "precision" here splits in two, and collapsing them writes a
test that fails against untouched behaviour:

- **`MUST_NOT_MERGE_UNASKED`** — the claim containment actually bears on.
  `{smith}` is a subset of `{john, smith}`, and so is `{york}` of
  `{new, york}`; the ceiling is what guarantees such a pair buys a model call
  rather than a merge.
- **`MUST_REJECT`** — no model call at all. Restricted to names sharing no
  whole token, because that is the behaviour this change must *preserve*.

Pairs like "John Smith"/"Jane Smith" belong to the first list and not the
second: Jaro-Winkler alone already scores them near 0.88, so they reach the
model today and should. Demanding `REJECT` there would be a test asserting
that the band should not exist.

## Names only, no embedding, no graph

Each pair is scored on the name feature alone -- `SimilarityFeatures(name=...)`
with the other two `None`. That is the *hardest* case for recall, since it is
what a freshly extracted entity with no vector and no edges looks like, and it
is the case the Voldemort defect was found in. A pair that reaches its band on
the name alone reaches it with corroborating features too.
"""

from __future__ import annotations

import pytest

from redstring.consolidation.policy import (
    HIGH_SIMILARITY,
    LOW_SIMILARITY,
    MergeDecision,
    decide,
)
from redstring.domain.similarity import (
    CONTAINMENT_CEILING,
    SimilarityFeatures,
    combined_score,
    string_similarity,
)


def band(left: str, right: str) -> MergeDecision:
    """The decision the pipeline reaches for two names and nothing else."""
    features = SimilarityFeatures(name=string_similarity(left, right))
    return decide(combined_score(features))


# Pairs a human resolves without hesitating. Each must at least reach the
# model; whether it merges outright is not asserted, because that is a
# threshold question and these are recall claims.
#
# Ordered roughly by how badly Jaro-Winkler alone handles them, because that
# ordering is the finding: the score collapses as the qualifier grows relative
# to the name it qualifies. The first four score 0.437, 0.519, 0.578 and 0.585
# today and are unreachable at any embedding; the rest clear the floor only by
# hundredths.
MUST_REACH_THE_MODEL = [
    ("Dr. Grant", "Grant"),
    ("President Bartlet", "Bartlet"),
    ("Professor Albus Dumbledore", "Dumbledore"),
    ("Ada Lovelace", "Countess of Lovelace"),
    ("Ada Lovelace", "Lovelace"),
    ("Lord Voldemort", "Voldemort"),
    ("Voldemort", "Lord Voldemort"),
    ("The Ministry of Magic", "Ministry of Magic"),
    ("Ada Lovelace", "Ada Lovelacce"),
    ("Ada  LOVELACE", "ada lovelace"),
]

# Pairs that must never merge *without being asked*. Each shares at least one
# token with its partner, so each is a case containment could have carried.
#
# The claim is `not MERGE`, not `REJECT`, and the difference is the point.
# Jaro-Winkler alone already scores these between 0.86 and 0.90 -- they reach
# the model today, before this branch, and that is the band working as
# designed: they are exactly the ambiguous middle it exists for. Asserting
# `REJECT` here would fail against untouched behaviour and invite someone to
# move a threshold to satisfy a test written after the design.
MUST_NOT_MERGE_UNASKED = [
    ("John Smith", "Jane Smith"),
    ("University of Oxford", "University of Cambridge"),
    ("New York Times", "New York Yankees"),
    ("Bank of England", "Bank of Japan"),
    ("Lord Voldemort", "Voldemort"),
    ("Ada Lovelace", "Lovelace"),
]

# Pairs that must still cost no model call at all. Restricted to names sharing
# no whole token and scoring low on Jaro-Winkler: this is behaviour the change
# must *preserve*, and it is the half that would catch containment firing where
# it has no business firing.
MUST_REJECT = [
    ("Tom Riddle", "Voldemort"),
    ("Ada Lovelace", "Charles Babbage"),
    ("Ministry of Magic", "Hogwarts"),
]


@pytest.mark.parametrize(("left", "right"), MUST_REACH_THE_MODEL)
def test_the_pair_at_least_reaches_the_adjudication_band(left: str, right: str):
    assert band(left, right) is not MergeDecision.REJECT


@pytest.mark.parametrize(("left", "right"), MUST_NOT_MERGE_UNASKED)
def test_the_pair_never_merges_without_a_model_call(left: str, right: str):
    assert band(left, right) is not MergeDecision.MERGE


@pytest.mark.parametrize(("left", "right"), MUST_REJECT)
def test_the_pair_costs_no_model_call(left: str, right: str):
    assert band(left, right) is MergeDecision.REJECT


def test_the_containment_ceiling_sits_between_the_two_thresholds():
    """The invariant that makes containment safe, pinned where it can be.

    `domain` is the bottom layer and cannot import `consolidation`, so the
    constant and the thresholds it is chosen against live in modules that
    cannot see each other. This test is the only place the three are in scope
    at once, which is why it lives here rather than beside the constant.

    Strictly below `HIGH` is the load-bearing half: at or above it, a
    token-subset match would merge without ever being asked about, and
    "Smith" is a subset of "John Smith".
    """
    assert LOW_SIMILARITY <= CONTAINMENT_CEILING < HIGH_SIMILARITY


def test_a_containment_match_alone_lands_in_the_band_rather_than_merging():
    """The end-to-end statement of the line above, through the real functions."""
    assert band("Lord Voldemort", "Voldemort") is MergeDecision.ADJUDICATE
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/unit/consolidation/test_banding_corpus.py -v -p no:randomly`
Expected: PASS.

If any `MUST_NOT_REACH_THE_MODEL` pair fails, that is a real precision regression from Task 2 — **stop and report it** rather than deleting the pair or moving the ceiling. If any `MUST_REACH_THE_MODEL` pair fails, report the pair and its score; do not lower `LOW_SIMILARITY`.

- [ ] **Step 3: Prove the corpus can fail**

A gate whose happy path is "the list is there" must be broken on purpose before it is believed (CLAUDE.md: "Break a new gate on purpose before believing it").

Temporarily revert `string_similarity` to Jaro-Winkler only:

```bash
git checkout HEAD~1 -- src/redstring/domain/similarity.py
uv run pytest tests/unit/consolidation/test_banding_corpus.py -v -p no:randomly
```

Expected: **exactly four** `MUST_REACH_THE_MODEL` cases FAIL — `"Dr. Grant"`, `"President Bartlet"`, `"Professor Albus Dumbledore"`, and `"Countess of Lovelace"`, which score 0.437, 0.519, 0.578 and 0.585 on Jaro-Winkler alone. The other `MUST_REACH` pairs pass either way: they already clear `LOW_SIMILARITY`, by as little as 0.02, which is the margin this change widens rather than creates. Both `MUST_NOT_MERGE_UNASKED` and `MUST_REJECT` pass unchanged.

If *more* than those four fail, something else moved — stop and report. If *fewer*, the corpus is not pinning what it claims. Record the exact list in the commit body.

Then restore:

```bash
git checkout HEAD -- src/redstring/domain/similarity.py
uv run pytest tests/unit/consolidation/test_banding_corpus.py -v -p no:randomly
```

Expected: PASS.

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, coverage at or above `.coverage-baseline`. If coverage rose, run `uv run python scripts/coverage_ratchet.py` so the new baseline is staged into this commit.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/consolidation/test_banding_corpus.py .coverage-baseline
git commit -m "Gate the thresholds with a labelled banding corpus

Model-free and deterministic, so it runs on every commit rather than
with the accuracy suite: which band a pair lands in is decided by two
pure functions, and no endpoint is involved.

Both directions, because the should-merge half alone is satisfied by a
scorer that returns 1.0 for everything -- and over-merging is exactly
the risk containment carries. Every REJECT pair shares a token with its
partner, so each is a pair the new term could have joined.

Proved able to fail by reverting string_similarity to Jaro-Winkler only
and watching the title and surname cases go red."
```

---

### Task 4: ADR and docs for Part A

**Files:**
- Create: `docs/adr/XXXX-overlap-aware-name-similarity.md` (number allocated in this step)
- Modify: `mkdocs.yml` (add to nav, so `--strict` checks the links)
- Modify: `BACKLOG.md` (the CLAUDE.md ADR-table staleness found during design)

- [ ] **Step 1: Allocate the number**

```bash
git ls-tree --name-only main docs/adr/ | sort | tail -1
```

Take the next integer after whatever that prints. Do **not** trust any number written in the spec or in `CLAUDE.md`. Use it for the filename, the H1, and the nav entry — all three, in this commit. (`recurring-defects.md` §6: renaming the file alone is the failure mode, not the fix.)

- [ ] **Step 2: Write the ADR**

`docs/adr/<N>-overlap-aware-name-similarity.md`. No counts, no file tables — those belong in commit messages (§5). Cover:

- **Decision:** `string_similarity` is the maximum of Jaro-Winkler and a token overlap coefficient capped at `CONTAINMENT_CEILING`, which sits at or above `LOW_SIMILARITY` and strictly below `HIGH_SIMILARITY`.
- **Context:** Jaro-Winkler's prefix bonus penalises a name qualified by a leading title or epithet — the most common alias shape in prose. Such a pair blocks correctly on the entity-type key and is then rejected by scoring, so the adjudication band never sees it. Widening the band instead would require dropping `LOW_SIMILARITY` below 0.53, which sends most of a type-key block to the model and reinstates the quadratic cost the band exists to avoid.
- **Why not a fourth feature:** `combined_score` renormalizes over present features, so a fourth one moves every score in the corpus. Strengthening the name feature moves only the pairs the name feature was wrong about.
- **Why the overlap coefficient rather than Jaccard:** a title added to a name is not evidence against the match, so the divisor is the smaller set.
- **Why not `domain.tokenize.tokenize`:** it drops stopwords for BM25; reusing it would couple merge decisions to the retrieval tokenizer.
- **Consequences:** containment buys a model call, never a merge — stated as the two-sided constant relation, and named as the thing to re-check if either threshold ever moves. Adjudication volume rises for callers with an adjudicator wired, which is the intended trade and is bounded by the corpus's `REJECT` half. `string_similarity` keeps both documented properties (symmetry; `1.0` iff equal).
- **Verdicts on existing ADRs:** `0010` stands (this changes a score, not the preference order). `0015` stands. `0006` stands — no export changes.

- [ ] **Step 3: Add the nav entry and build the docs**

Add the new file to `mkdocs.yml`'s ADR nav section, in number order.

Run: `uv run mkdocs build --strict`
Expected: PASS with no warnings. A broken link fails here — that is the gate that makes ADR citation checkable.

- [ ] **Step 4: File the CLAUDE.md staleness in BACKLOG.md**

Add to `BACKLOG.md`:

```markdown
### B-ADR-TABLE — `CLAUDE.md`'s ADR table stops at 0019

`.claude/rules/definition-of-done.md` carries a table of "the ADRs a spec has
to be run against", listing `0001` through `0019`. The tree is at `0039`.

Twenty ADRs are therefore invisible to the rule that exists to make specs
account for existing decisions, in a file loaded into every session. This is
`recurring-defects.md` §5 happening to the file that documents §5 -- the same
way that section's own module map went stale, and the same way its ADR-count
sentence did.

Not fixed here because the fix is not "append twenty rows": the table's value
is the one-line "settles" summary per ADR, and writing twenty of those
accurately means reading twenty ADRs. Doing it badly is worse than the gap,
because a wrong summary is trusted.

Fix: read `docs/adr/0020` through the current highest, add a row each, and add
a test that the table's row count matches the number of files in `docs/adr/`
excluding `index.md` -- so the next gap fails rather than accumulating. That
test is the actual deliverable; the rows go stale again without it.
```

- [ ] **Step 5: Commit**

```bash
git add docs/adr/ mkdocs.yml BACKLOG.md
git commit -m "Record why the name feature absorbed containment

Numbered against main at merge time, with the H1, the filename and the
nav entry set together -- a renumber that moves only the filename is the
failure mode recurring-defects.md section 6 describes.

Also files B-ADR-TABLE: the ADR table in definition-of-done.md stops at
0019 against a tree at 0039, so twenty decisions are invisible to the
rule that exists to make specs account for them. The deliverable there
is the test that the table matches the directory, not the rows."
```

---

# PART B — THROUGHPUT

### Task 5: Move `CallLimiter` to `domain`

A pure move. No behaviour change, so every existing test must pass **unmodified** — that is the definition of a refactor here.

**Files:**
- Create: `src/redstring/domain/limiter.py`
- Delete: `src/redstring/extraction/limiter.py`
- Modify: `src/redstring/extraction/pipeline.py`, `src/redstring/__init__.py`, and any other importer
- Modify: whichever test module imports it (find it, do not guess)

**Interfaces:**
- Produces: `redstring.domain.limiter.CallLimiter`, identical API — `__init__(limit: int)`, `limit` property, `__aenter__`/`__aexit__`.

- [ ] **Step 1: Find every importer**

```bash
grep -rn "extraction.limiter\|extraction import limiter\|CallLimiter" src/ tests/ docs/ mkdocs.yml
```

Write the list down before touching anything — `docs/` and the `__init__.py` export are easy to miss, and a stale doc path is `recurring-defects.md` §5.

- [ ] **Step 2: Move the file**

```bash
git mv src/redstring/extraction/limiter.py src/redstring/domain/limiter.py
```

Update its module docstring — the current one opens by describing `ExtractionPipeline`'s batch size, which is no longer the only caller. Replace the first paragraph with:

```
"""A ceiling on calls in flight against the inference endpoint.

Not owned by any one pipeline, and that is the point. The operator's
constraint is the backend's queue depth -- a single-GPU llama.cpp server
processes one request at a time and converts ten concurrent requests into ten
timeouts -- and the queue does not care which code path issued a request. So
the ceiling has to be one object every call passes through, shared across
callers that cannot import each other.

It lives in `domain` for exactly that reason: `extraction` and `consolidation`
are siblings in the layer contract and forbidden from importing each other,
and two limiters would be two ceilings, which is no ceiling. Nothing here does
I/O or depends on anything above `domain` -- it is a semaphore with a name and
a refusal, which is the same test every other module in this layer passes.
"""
```

Keep the rest of the docstring and the class body exactly as they are.

- [ ] **Step 3: Update every import from the Step 1 list**

In `src/redstring/extraction/pipeline.py` and anywhere else:

```python
from redstring.domain.limiter import CallLimiter
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -p no:randomly`
Expected: PASS, with **no test file edited except for its import line**. If a test needed a real change, the move was not a pure move — stop and report.

- [ ] **Step 5: Commit**

The `pre-commit` hook runs `lint-imports`, which is the check that matters here: it will fail if anything under `domain` now imports upward.

```bash
git add -A
git commit -m "Move CallLimiter down to domain, where both siblings can reach it

extraction and consolidation are siblings in the layer contract and may
not import each other, so a consolidation pass could not have used the
limiter where it sat. Two limiters would be two ceilings, which is no
ceiling -- ADR 0039's argument is that the bound is on calls in flight
against one backend regardless of which path issued them.

Pure move: no test body changed, only import lines."
```

---

### Task 6: Split `resolve` into score/band and emit

Pure refactor. `resolve`'s behaviour, signature and docstring are unchanged; its body is re-expressed in terms of two new private methods that `resolve_many` will reuse.

**Files:**
- Modify: `src/redstring/consolidation/service.py`
- Test: `tests/unit/consolidation/test_resolve.py` (must pass unmodified)

**Interfaces:**
- Produces, on `ConsolidationService`:
  - `async def _score_and_band(self, subject: Entity, *, finder: CandidateSource, high: float, low: float) -> _Banded | None`
  - `async def _emit(self, banded: _Banded, confirmed: list[tuple[ScoredCandidate, str]]) -> EntitiesMerged | None`
  - `@dataclass(frozen=True, slots=True) class _Banded` with fields `subject: Entity`, `confirmed: list[tuple[ScoredCandidate, str]]`, `undecided: list[ScoredCandidate]`

- [ ] **Step 1: Add the dataclass and the two methods**

In `src/redstring/consolidation/service.py`, add near the top of the module (after imports):

```python
@dataclass(frozen=True, slots=True)
class _Banded:
    """One subject's candidates, split by what the score alone settled.

    Carried as one object rather than three parallel lists because
    `resolve_many` holds a collection of these across an await boundary, and
    lists kept aligned by hand are how a verdict gets recorded against the
    wrong pair.
    """

    #: Already resolved through aliases. Not the subject the caller passed.
    subject: Entity
    #: Candidate and the reason it is being merged, carried together -- a
    #: merge attributed to the wrong reason is an audit trail that lies while
    #: looking complete.
    confirmed: list[tuple[ScoredCandidate, str]]
    #: The band. Empty unless an adjudicator is going to be asked.
    undecided: list[ScoredCandidate]
```

Add `from dataclasses import dataclass` to the imports if absent.

Then add the two methods to `ConsolidationService`, moving the existing logic verbatim:

```python
async def _score_and_band(
    self,
    subject: Entity,
    *,
    finder: CandidateSource,
    high: float,
    low: float,
) -> _Banded | None:
    """Resolve, block, score, band. **No writes and no model call.**

    Split out of `resolve` so `resolve_many` can run this half
    concurrently over many subjects: it touches no aggregate and no
    stream, so nothing here contends on the tenant's optimistic
    concurrency. `None` when there is nothing to decide about.
    """
    subject = await self._resolved_subject(subject)

    # `minimum_score=low` means `decide` below can never answer `REJECT`:
    # the finder has already dropped everything under the low threshold.
    # Worth knowing, because it is why two cosmic-ray mutants rewriting the
    # band comparisons as `>=` and `<=` survived -- they differ from `is`
    # only on `REJECT`, which is not in this list. The filter is here
    # rather than after `decide` so the rejected pairs are never scored
    # into a list only to be dropped from it.
    candidates = await finder.candidates(subject, minimum_score=low)
    if not candidates:
        return None

    banded = [(candidate, decide(candidate.score, high=high, low=low)) for candidate in candidates]
    return _Banded(
        subject=subject,
        confirmed=[
            (c, f"score >= {high}") for c, decision in banded if decision is MergeDecision.MERGE
        ],
        undecided=[c for c, decision in banded if decision is MergeDecision.ADJUDICATE],
    )


async def _emit(
    self, banded: _Banded, confirmed: list[tuple[ScoredCandidate, str]]
) -> EntitiesMerged | None:
    """Append one merge covering everything that came out a yes.

    `confirmed` is passed rather than read off `banded` because
    `resolve_many` adds the adjudicated yeses to the score-confirmed ones
    after the fact, and the caller that assembled that list is the one
    that knows it is complete.
    """
    if not confirmed:
        return None
    subject = banded.subject
    confirmed_ids = [candidate.entity.id for candidate, _ in confirmed]
    merge_reason = "; ".join(reason for _, reason in confirmed)
    try:
        return await self.merge(
            tenant_id=subject.tenant_id,
            canonical_entity_id=subject.id,
            merged_entity_ids=confirmed_ids,
            merge_reason=merge_reason,
        )
    except MergeIntoAliasError:
        # `subject` was already resolved to its canonical in
        # `_score_and_band`, so this is not the stale-subject case -- it
        # is a genuine race: something merged this call's canonical into
        # something else in the window between that resolution and the
        # append inside `merge`. Retried once against the new canonical,
        # because the caller did nothing wrong the first time either. Not
        # retried a second time: two races on one call is not the same
        # event happening twice, it is a sign something is genuinely
        # wrong, and that should surface rather than loop.
        subject = await self._resolved_subject(subject)
        return await self.merge(
            tenant_id=subject.tenant_id,
            canonical_entity_id=subject.id,
            merged_entity_ids=confirmed_ids,
            merge_reason=merge_reason,
        )
```

- [ ] **Step 2: Re-express `resolve` in terms of them**

Replace `resolve`'s body (everything after its docstring) with:

```python
        banded = await self._score_and_band(subject, finder=finder, high=high, low=low)
        if banded is None:
            return None

        confirmed = list(banded.confirmed)
        if banded.undecided and adjudicator is not None:
            verdicts = await adjudicator.adjudicate(banded.subject, banded.undecided)
            confirmed += [
                (candidate, verdict.reason)
                # `verdict is None` is "the model did not answer", which is not
                # a yes. See `policy.Adjudicator.adjudicate`.
                for candidate, verdict in zip(banded.undecided, verdicts, strict=True)
                if verdict is not None and verdict.same
            ]
        return await self._emit(banded, confirmed)
```

Leave the docstring exactly as it is.

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/unit/consolidation/ -v -p no:randomly`
Expected: PASS with **no test file modified**. This is a refactor; an edited assertion means it was not one.

- [ ] **Step 4: Commit**

```bash
git add src/redstring/consolidation/service.py
git commit -m "Split resolve into a score-and-band half and an emit half

No behaviour change and no test touched. The split is what the corpus
pass needs: the first half is pure reads and can run concurrently over
many subjects, the second appends to the tenant stream and cannot."
```

---

### Task 7: Cross-subject adjudication batching

**Files:**
- Modify: `src/redstring/consolidation/policy.py`
- Create: `tests/unit/consolidation/test_cross_subject_batching.py`

**Interfaces:**
- Consumes: `Adjudicator`, `AdjudicationBatch`, `AdjudicationVerdict`, `ADJUDICATION_BATCH_SIZE` (existing).
- Produces, on `Adjudicator`:
  - `async def adjudicate_many(self, work: Sequence[tuple[Entity, Sequence[ScoredCandidate]]]) -> list[list[AdjudicationVerdict | None]]` — one verdict list per input subject, aligned to that subject's candidate sequence.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/consolidation/test_cross_subject_batching.py`:

```python
"""Batches that span subjects, and the position mapping that makes them safe.

`Adjudicator.adjudicate` batches within one subject, so a subject with two
ambiguous candidates spends a whole model call on two pairs.
`adjudicate_many` fills each batch from as many subjects as it takes.

The mapping from a batch position back to `(subject, candidate)` is the whole
risk of this feature -- `AdjudicationBatch` deliberately keeps ids out of the
prompt, so position is the only thing tying an answer to its question, and a
batch that spans subjects makes that mapping non-trivial for the first time.
"""

from __future__ import annotations

import pytest

from redstring.consolidation.policy import (
    ADJUDICATION_BATCH_SIZE,
    AdjudicationBatch,
    AdjudicationVerdict,
    Adjudicator,
)


class RecordingProvider:
    """An `LlmProvider` that answers every pair and records what it was asked.

    Each verdict's `reason` is the pair's *global* index across all calls, so
    a test can assert exactly which question each answer came back for. A
    provider returning uniform verdicts could not distinguish a correct
    mapping from a shifted one.
    """

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self._answered = 0

    async def extract(self, prompt, schema, *, system_prompt=None):  # noqa: ANN001, ARG002
        self.prompts.append(prompt)
        pair_count = prompt.count("Pair ")
        verdicts = []
        for _ in range(pair_count):
            verdicts.append(
                AdjudicationVerdict(same=True, confidence=1.0, reason=f"q{self._answered}")
            )
            self._answered += 1
        return AdjudicationBatch(verdicts=verdicts)
```

Then the tests. Build subjects and candidates with the helpers already used in `tests/unit/consolidation/` — **read `tests/unit/consolidation/conftest.py` and `test_policy.py` first and reuse their factories rather than inventing new ones.** Pin every entity id explicitly; do not build them from `uuid4()`. (CLAUDE.md's tie-break-origin row: a fixture whose ids are random certifies a broken test as strong.)

```python
async def test_one_call_covers_pairs_from_several_subjects():
    """Three subjects with two pairs each is one call, not three."""
    provider = RecordingProvider()
    adjudicator = Adjudicator(provider)
    work = [(subject_a, [c1, c2]), (subject_b, [c3, c4]), (subject_c, [c5, c6])]

    await adjudicator.adjudicate_many(work)

    assert len(provider.prompts) == 1


async def test_each_subject_gets_back_verdicts_for_its_own_candidates():
    """The mapping test. Reasons carry the global question index.

    Subject A asked questions 0 and 1, subject B questions 2 and 3. A
    mapping that reset per subject, or that sliced the flat answer list at
    the wrong offsets, returns a different assignment here -- which is the
    defect this whole module exists to catch.
    """
    provider = RecordingProvider()
    adjudicator = Adjudicator(provider)

    results = await adjudicator.adjudicate_many([(subject_a, [c1, c2]), (subject_b, [c3, c4])])

    assert [v.reason for v in results[0]] == ["q0", "q1"]
    assert [v.reason for v in results[1]] == ["q2", "q3"]


async def test_a_subject_whose_pairs_straddle_a_batch_boundary_still_re_pairs():
    """The case a per-subject batcher never produces.

    One subject's candidates are split across two model calls. Its verdict
    list must still be its own candidates in order, reassembled from two
    responses -- and the offsets differ per call, which is where an
    off-by-one lives.
    """
    provider = RecordingProvider()
    adjudicator = Adjudicator(provider)
    # `ADJUDICATION_BATCH_SIZE` pairs on the first subject fills batch one
    # exactly; the second subject's pairs open batch two.
    first = [candidate(i) for i in range(ADJUDICATION_BATCH_SIZE - 1)]
    second = [candidate(100), candidate(101)]

    results = await adjudicator.adjudicate_many([(subject_a, first), (subject_b, second)])

    assert len(provider.prompts) == 2
    assert len(results[0]) == len(first)
    assert len(results[1]) == len(second)
    assert [v.reason for v in results[1]] == ["q9", "q10"]


async def test_a_short_batch_yields_none_for_every_subject_it_touched():
    """The existing safety property, extended across the boundary.

    `adjudicate` already yields `None` for every pair in a short batch rather
    than for the tail, because alignment is unknown once the count disagrees.
    A batch spanning two subjects must poison **both**, not just the one whose
    pairs happened to come last.
    """
    provider = ShortAnsweringProvider(short_by=1)
    adjudicator = Adjudicator(provider)

    results = await adjudicator.adjudicate_many([(subject_a, [c1]), (subject_b, [c2])])

    assert results == [[None], [None]]


async def test_a_subject_with_no_candidates_gets_an_empty_list_not_a_dropped_slot():
    """Alignment is by position in `work`, so an empty subject must hold its place."""
    provider = RecordingProvider()
    adjudicator = Adjudicator(provider)

    results = await adjudicator.adjudicate_many([(subject_a, []), (subject_b, [c1])])

    assert results[0] == []
    assert [v.reason for v in results[1]] == ["q0"]


async def test_a_provider_error_yields_none_only_for_the_batch_that_failed():
    """One failed call must not poison subjects whose pairs were in other calls."""
    provider = FailOnCallProvider(fail_on=0)
    adjudicator = Adjudicator(provider)
    first = [candidate(i) for i in range(ADJUDICATION_BATCH_SIZE)]
    second = [candidate(100)]

    results = await adjudicator.adjudicate_many([(subject_a, first), (subject_b, second)])

    assert results[0] == [None] * ADJUDICATION_BATCH_SIZE
    assert results[1][0] is not None
```

Write `ShortAnsweringProvider` and `FailOnCallProvider` in the same module — one returns an `AdjudicationBatch` with fewer verdicts than pairs, the other raises `LlmProviderError` on the nth call. `candidate(i)` is a local helper building a `ScoredCandidate` with a **pinned** entity id derived from `i`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/consolidation/test_cross_subject_batching.py -v -p no:randomly`
Expected: FAIL — `AttributeError: 'Adjudicator' object has no attribute 'adjudicate_many'`

- [ ] **Step 3: Implement**

Add to `Adjudicator` in `src/redstring/consolidation/policy.py`:

```python
async def adjudicate_many(
    self, work: Sequence[tuple[Entity, Sequence[ScoredCandidate]]]
) -> list[list[AdjudicationVerdict | None]]:
    """Verdicts for many subjects, in batches that span subject boundaries.

    One list per entry in `work`, in the same order and the same length as
    that entry's candidates -- a subject with no candidates gets `[]` and
    keeps its slot, because the caller re-pairs by position here too.

    ## Why this exists

    `adjudicate` batches within one subject, so a subject with two
    ambiguous candidates spends a whole model call on two pairs. Over a
    corpus pass that is most of the calls: the band is a small fraction of
    a block by design, so per-subject batches are nearly always short.

    ## Batches still may not mix questions from one prompt

    Each batch is rendered and asked exactly as `_one_batch` does, and
    every property that made it safe is unchanged: verdicts re-pair by
    position, ids stay out of the prompt, and a batch whose verdict count
    disagrees yields `None` for **every** pair in it. What is new is that a
    poisoned batch can now span subjects -- so it yields `None` for the
    pairs of *every* subject it touched, which is the same rule applied to
    a wider unit rather than a weaker one.
    """
    flat: list[tuple[Entity, ScoredCandidate]] = [
        (subject, candidate) for subject, candidates in work for candidate in candidates
    ]
    verdicts: list[AdjudicationVerdict | None] = []
    for start in range(0, len(flat), self._batch_size):
        verdicts.extend(await self._one_mixed_batch(flat[start : start + self._batch_size]))

    # Re-slice by each subject's candidate count. `zip(strict=True)` over
    # the counts rather than an index arithmetic pass, so a mismatch
    # between what was asked and what came back raises here rather than
    # silently shifting one subject's answers onto another.
    results: list[list[AdjudicationVerdict | None]] = []
    cursor = 0
    for _, candidates in work:
        results.append(verdicts[cursor : cursor + len(candidates)])
        cursor += len(candidates)
    if cursor != len(verdicts):
        raise AssertionError(f"re-paired {cursor} verdicts against {len(verdicts)} asked for")
    return results


async def _one_mixed_batch(
    self, batch: Sequence[tuple[Entity, ScoredCandidate]]
) -> list[AdjudicationVerdict | None]:
    """`_one_batch`, but each pair carries its own subject."""
    questions = [
        AdjudicationQuestion(
            left=subject.name,
            right=candidate.entity.name,
            entity_type=subject.entity_type,
            left_description=subject.description,
            right_description=candidate.entity.description,
        )
        for subject, candidate in batch
    ]
    try:
        answer = await self._provider.extract(
            _render(questions), AdjudicationBatch, system_prompt=_SYSTEM_PROMPT
        )
    except LlmProviderError:
        return [None] * len(batch)
    try:
        return [verdict for _, verdict in zip(batch, answer.verdicts, strict=True)]
    except ValueError:
        return [None] * len(batch)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/consolidation/ -v -p no:randomly`
Expected: PASS, including the existing `test_policy.py` unmodified.

- [ ] **Step 5: Break the mapping on purpose**

The position mapping is this task's whole risk, and a test suite for it must be shown to fail. Temporarily change `results.append(verdicts[cursor : cursor + len(candidates)])` to `results.append(verdicts[: len(candidates)])` — a plausible wrong implementation that is correct for the first subject.

Run: `uv run pytest tests/unit/consolidation/test_cross_subject_batching.py -v -p no:randomly`
Expected: `test_each_subject_gets_back_verdicts_for_its_own_candidates` and the straddle test FAIL. If they pass, the fixtures are not distinguishing the implementations — fix the fixtures, not the assertion.

Revert the deliberate break and re-run to green.

- [ ] **Step 6: Commit**

```bash
git add src/redstring/consolidation/policy.py tests/unit/consolidation/test_cross_subject_batching.py
git commit -m "Fill adjudication batches across subject boundaries

Per-subject batching leaves nearly every batch short: the band is a
small fraction of a block by design, so a subject rarely has ten
ambiguous pairs. adjudicate_many flattens the work, batches the flat
list, and re-slices by each subject's candidate count.

Position is the only thing tying an answer to its question -- ids stay
out of the prompt on purpose -- so the re-slice is where this breaks.
Proved the tests catch it by slicing from zero for every subject, which
is correct for the first one and wrong for the rest."
```

---

### Task 8: `ConsolidationService.resolve_many`

**Files:**
- Modify: `src/redstring/consolidation/service.py`
- Create: `tests/unit/consolidation/test_resolve_many.py`

**Interfaces:**
- Consumes: `_Banded`, `_score_and_band`, `_emit` (Task 6); `Adjudicator.adjudicate_many` (Task 7); `CallLimiter` from `redstring.domain.limiter` (Task 5).
- Produces:
  - `async def resolve_many(self, subjects: Sequence[Entity], *, finder: CandidateSource, adjudicator: MergeAdjudicator | None = None, concurrency: int = 1, limiter: CallLimiter | None = None, high: float = HIGH_SIMILARITY, low: float = LOW_SIMILARITY) -> list[EntitiesMerged]`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/consolidation/test_resolve_many.py`. Reuse the fixtures and store setup from `tests/unit/consolidation/test_resolve.py` — **read it first**. Pin every entity id.

```python
async def test_every_subject_is_scored_before_any_merge_is_emitted():
    """The phase barrier, asserted directly rather than inferred from results.

    A `CandidateSource` that records when it was called, against a
    `GraphStore` wrapper that records when a write happened: the last score
    must precede the first write. Without this, an implementation that
    interleaved the phases would pass every result assertion below while
    reintroducing exactly the read-your-own-writes coupling the split exists
    to remove.
    """


async def test_two_subjects_confirming_the_same_candidate_merge_it_once():
    """First emit wins; the second finds its candidate is now an alias.

    Not exotic -- two near-duplicates of one entity in the same pass is the
    ordinary case for a corpus with a repeated mention.
    """


async def test_a_subject_merged_away_earlier_in_the_pass_is_skipped():
    """The mutual case: A confirms B and B confirms A.

    A symmetric scorer makes this the *normal* outcome for a genuine
    duplicate pair when both entities are in the subject list, so it is the
    first thing to break, not an edge case. The second decision must be
    dropped, not retried and not raised.
    """


async def test_the_emit_order_is_deterministic():
    """Two runs over one graph agree, whatever order phase 1 completed in.

    Asserted by running the same pass twice against fresh identical stores
    and comparing the sequence of canonical ids -- not by inspecting the sort
    key, which would be a test of the implementation rather than the claim.
    """


async def test_concurrency_one_produces_the_same_merges_as_calling_resolve_in_a_loop():
    """The equivalence that makes the default safe.

    The oracle is a serial loop over `resolve`, built independently of
    `resolve_many` -- not `resolve_many` with a different argument. A
    round-trip whose two sides share the code under test checks determinism,
    not correctness.
    """


async def test_no_more_than_concurrency_scorings_are_in_flight_at_once():
    """The bound, asserted by a finder that records its own high-water mark."""


async def test_no_more_than_concurrency_model_calls_are_in_flight_at_once():
    """The limiter, on the phase that actually talks to the endpoint.

    Phase 1 makes no model calls at all -- `CandidateFinder.candidates` is
    store reads -- so a test that bounded the wrong phase would pass against
    an implementation with no limiter in it.
    """


async def test_a_subject_with_no_candidates_contributes_no_event():
    """`None` from `_score_and_band` must not become an empty merge."""


async def test_an_empty_subject_list_makes_no_calls_and_returns_empty():
```

Fill each body out; do not leave the docstring-only stubs above in the committed file.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/consolidation/test_resolve_many.py -v -p no:randomly`
Expected: FAIL — `AttributeError: 'ConsolidationService' object has no attribute 'resolve_many'`

- [ ] **Step 3: Implement**

Add to `ConsolidationService`:

```python
async def resolve_many(
    self,
    subjects: Sequence[Entity],
    *,
    finder: CandidateSource,
    adjudicator: MergeAdjudicator | None = None,
    concurrency: int = 1,
    limiter: CallLimiter | None = None,
    high: float = HIGH_SIMILARITY,
    low: float = LOW_SIMILARITY,
) -> list[EntitiesMerged]:
    """Consolidate a whole corpus in one pass: decide concurrently, emit serially.

    Returns the events emitted, in emit order. A subject that decided
    nothing contributes nothing, so the result is usually shorter than
    `subjects`.

    ## Three phases, and why the fan-out is not over `resolve`

    `resolve` cannot simply be fired concurrently over a list. Two things
    forbid it, and neither is incidental:

    1. **Each merge changes the graph the next subject reads.** Candidate
       finding resolves through aliases, so a merge emitted for one
       subject changes what the next one blocks against.
    2. **`ConsolidationLog` uses optimistic concurrency on the tenant
       stream.** Two concurrent merges within one tenant collide by
       construction -- the stream is the tenant deliberately.

    So the pass splits where those constraints do:

    - **Phase 1, concurrent:** score and band every subject. Store reads
      only -- **no model calls at all** -- so what is bounded here is the
      adapters' connection pools, and `concurrency` bounds it directly, in
      wavefronts.
    - **Phase 2, a barrier:** adjudicate, in batches that span subjects.
      This is the only phase that talks to the endpoint, so this is where
      `limiter` applies. The barrier is a real cost -- no emit starts
      until every subject is scored -- and it is accepted rather than
      engineered around, because without it a batch can only be filled
      from subjects that happen to have finished, which is the per-subject
      batching this phase exists to replace.
    - **Phase 3, serial:** emit, in a deterministic order.

    ## What phase 3 re-checks, and why skipping is right

    Phase 1 completes before any of phase 3 runs, so the staleness window
    `resolve` already documents between its read and its append is wider
    here. Before each merge the subject and its confirmed candidates are
    re-resolved through `resolve_entity_ids`, and anything that has since
    become an alias is dropped. A subject that has itself been merged away
    is skipped entirely.

    **Skipped, not retried.** Retrying would mean re-scoring against a
    graph that phase 1's other decisions are still changing, which is a
    fixed-point computation wearing a pass's clothes. The duplicates that
    subject found now belong to whatever absorbed it, and the next pass
    will find them there. Saying "one pass is one pass" is cheaper and
    more predictable than converging inside one call.

    Args:
        subjects: The entities to consolidate around. Order does not
            affect the result -- emit order is derived, not taken from
            this list -- but each is resolved through aliases first, so
            passing an already-merged entity is harmless.
        finder: Supplies and scores candidates. One instance, used
            concurrently, so it must be safe to call re-entrantly;
            `CandidateFinder` is.
        adjudicator: Consulted for the band. Without one the band is
            **rejected**, not merged -- the same asymmetry `resolve`
            documents, for the same reason.
        concurrency: How many subjects are scored at once, and how many
            adjudication batches may be in flight. Must be at least 1.
        limiter: The endpoint ceiling. Built from `concurrency` when
            omitted. Pass one shared across callers to bound a backend
            serving more than this pass -- which is the whole reason the
            bound is an object rather than a number.
        high: At or above this score, merge without asking.
        low: Below this score, never merge and never ask.
    """
    if concurrency < 1:
        raise ValueError(f"concurrency must be >= 1, got {concurrency}")
    if not subjects:
        return []
    limiter = limiter if limiter is not None else CallLimiter(concurrency)

    # Phase 1 -- score and band, in wavefronts of `concurrency`.
    banded: list[_Banded] = []
    for batch in _batches(subjects, concurrency):
        results = await asyncio.gather(
            *(self._score_and_band(subject, finder=finder, high=high, low=low) for subject in batch)
        )
        banded.extend(result for result in results if result is not None)

    # Emit order is derived rather than inherited from `subjects`, so two
    # runs over one graph agree regardless of what order phase 1 finished
    # in. The subject id as a string, matching how `CandidateFinder`
    # breaks score ties -- one convention for "an arbitrary but total
    # order over entities", not two.
    banded.sort(key=lambda decision: str(decision.subject.id))

    # Phase 2 -- adjudicate, in batches spanning subjects.
    confirmed_per_subject = [list(decision.confirmed) for decision in banded]
    if adjudicator is not None:
        work = [(decision.subject, decision.undecided) for decision in banded if decision.undecided]
        if work:
            async with limiter:
                verdict_lists = await adjudicator.adjudicate_many(work)
            by_subject = {
                subject.id: verdicts
                for (subject, _), verdicts in zip(work, verdict_lists, strict=True)
            }
            for index, decision in enumerate(banded):
                verdicts = by_subject.get(decision.subject.id)
                if verdicts is None:
                    continue
                confirmed_per_subject[index] += [
                    (candidate, verdict.reason)
                    # `verdict is None` is "the model did not answer",
                    # which is not a yes.
                    for candidate, verdict in zip(decision.undecided, verdicts, strict=True)
                    if verdict is not None and verdict.same
                ]

    # Phase 3 -- emit, serially, re-resolving as we go.
    events: list[EntitiesMerged] = []
    for decision, confirmed in zip(banded, confirmed_per_subject, strict=True):
        if not confirmed:
            continue
        fresh = await self._still_mergeable(decision, confirmed)
        if fresh is None:
            continue
        event = await self._emit(decision, fresh)
        if event is not None:
            events.append(event)
    return events


async def _still_mergeable(
    self, decision: _Banded, confirmed: list[tuple[ScoredCandidate, str]]
) -> list[tuple[ScoredCandidate, str]] | None:
    """`confirmed` minus anything an earlier emit in this pass consumed.

    `None` when the subject itself has been merged away, which drops the
    whole decision -- see `resolve_many` for why that is a skip rather
    than a retry.
    """
    subject = decision.subject
    ids = [subject.id, *(candidate.entity.id for candidate, _ in confirmed)]
    canonical = await self._graph.resolve_entity_ids(ids, subject.tenant_id)
    if canonical[subject.id] != subject.id:
        return None
    return [
        (candidate, reason)
        for candidate, reason in confirmed
        if canonical[candidate.entity.id] == candidate.entity.id
    ]
```

Add the wavefront helper at module level (a local one — `consolidation` may not import `extraction`):

```python
def _batches(items: Sequence[_T], size: int) -> Iterator[Sequence[_T]]:
    """Consecutive slices of at most `size`. The last may be short."""
    for start in range(0, len(items), size):
        yield items[start : start + size]
```

Add `import asyncio` and the `CallLimiter` import.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/consolidation/ -v -p no:randomly`
Expected: PASS.

- [ ] **Step 5: Break the staleness check on purpose**

Delete the `if canonical[subject.id] != subject.id: return None` line and run the suite.

Expected: `test_a_subject_merged_away_earlier_in_the_pass_is_skipped` FAILS (most likely with `MergeIntoAliasError` or `DoubleMergeError`). If it passes, the fixture never actually produces the mutual case — fix the fixture.

Restore and re-run to green.

- [ ] **Step 6: Commit**

```bash
git add src/redstring/consolidation/service.py tests/unit/consolidation/test_resolve_many.py
git commit -m "Consolidate a corpus in one decide-then-emit pass

The fan-out cannot be over resolve: each merge changes the graph the
next subject reads, and ConsolidationLog's optimistic concurrency is on
the tenant stream, so two concurrent merges in one tenant collide by
construction. So the pass splits where those constraints do -- score and
band concurrently (store reads only, no model calls), adjudicate in
cross-subject batches behind the CallLimiter, emit serially.

Phase 3 re-resolves before each merge and skips what an earlier emit
consumed. Skipped rather than retried: retrying re-scores against a
graph the pass is still changing, which is a fixed-point computation
wearing a pass's clothes.

Proved the staleness check is load-bearing by deleting it and watching
the mutual-confirmation case fail."
```

---

### Task 9: `Consolidator.resolve_many` and the public surface

**Files:**
- Modify: `src/redstring/composition/build_graph.py`
- Modify: `src/redstring/__init__.py`
- Test: `tests/unit/composition/` (find the module covering `Consolidator`)

**Interfaces:**
- Consumes: `ConsolidationService.resolve_many` (Task 8).
- Produces: `async def resolve_many(self, subjects: Sequence[Entity], *, finder: CandidateSource | None = None, adjudicator: MergeAdjudicator | None = None, concurrency: int = 1, limiter: CallLimiter | None = None, high: float = HIGH_SIMILARITY, low: float = LOW_SIMILARITY) -> list[ConsolidationReport]`

- [ ] **Step 1: Write the failing test**

In the existing `Consolidator` test module:

```python
async def test_resolve_many_returns_a_report_per_merge_and_folds_each_into_the_store():
    """The composed guarantee: events emitted *and* the graph updated.

    Assert the store, not just the reports -- `Consolidator`'s whole reason to
    exist over `ConsolidationService` is that it runs the projection, and a
    report list is identical whether or not it did.
    """


async def test_resolve_many_with_one_subject_matches_resolve():
    """The composed path agrees with the single-subject one it generalises."""
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/composition/ -v -p no:randomly`
Expected: FAIL — no `resolve_many` on `Consolidator`.

- [ ] **Step 3: Implement**

Add to `Consolidator`, mirroring `resolve`'s delegation shape:

```python
    async def resolve_many(
        self,
        subjects: Sequence[Entity],
        *,
        finder: CandidateSource | None = None,
        adjudicator: MergeAdjudicator | None = None,
        concurrency: int = 1,
        limiter: CallLimiter | None = None,
        high: float = HIGH_SIMILARITY,
        low: float = LOW_SIMILARITY,
    ) -> list[ConsolidationReport]:
        """`resolve` over a whole corpus, in one decide-then-emit pass.

        One report per merge actually emitted, in emit order -- shorter than
        `subjects` whenever a subject decided nothing, which is the common
        case.

        Each report's graph effects are already applied: this folds every
        event through the projection as it goes, exactly as `resolve` does for
        one.

        See `ConsolidationService.resolve_many` for the phase structure and
        for why a subject merged away mid-pass is skipped rather than retried.
        Two knobs are worth knowing before raising them: `concurrency` bounds
        both how many subjects are scored at once and how many adjudication
        batches are in flight, and `limiter` is the endpoint ceiling -- pass a
        shared one to bound a backend serving more than this pass.
        """
        events = await self._service.resolve_many(
            subjects,
            finder=finder if finder is not None else self._default_finder,
            adjudicator=adjudicator,
            concurrency=concurrency,
            limiter=limiter,
            high=high,
            low=low,
        )
        return [await self._project_merge(event) for event in events]
```

- [ ] **Step 4: Check the public surface gate**

`resolve_many`'s signature names `CallLimiter`, `CandidateSource`, `MergeAdjudicator`, `Entity` and `ConsolidationReport`. `Consolidator` is exported, so **every type in the signature must be in `redstring.__all__`** — the first of ADR 0006's three tests walks the MRO and checks exactly this.

Run: `uv run pytest tests/unit/test_public_api.py -v -p no:randomly` (find the real module name if it differs)

If it fails, add the missing names to `__all__` in `src/redstring/__init__.py` **and expect the closure to pull more** — exporting one name obliges the types in its own signature. Follow the failures until green; do not silence the test.

- [ ] **Step 5: Run everything**

Run: `uv run pytest`
Expected: PASS. Run `uv run python scripts/coverage_ratchet.py` if coverage rose, so the baseline lands in this commit.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Give the composed entry point a corpus-level method

Consolidator.resolve_many folds every event through the projection as it
goes, which is the whole difference between it and the service.

The public surface gate did its job: the new signature names CallLimiter
and CandidateSource, so both had to be exported or a caller could not
construct what the parameters ask for."
```

---

### Task 10: ADR and how-to for Part B

**Files:**
- Create: `docs/adr/XXXX-the-consolidation-pass-is-decide-then-emit.md`
- Modify: `docs/adr/0015-consolidation-gets-a-composed-entry-point.md` (Status: amended-by pointer)
- Modify: `docs/adr/0039-bounded-concurrency-over-chunks.md` (Status: amended-by pointer)
- Modify: `mkdocs.yml`
- Create or modify: a how-to covering the new pass

- [ ] **Step 1: Allocate the number**

```bash
git ls-tree --name-only main docs/adr/ | sort | tail -1
```

Next integer after that, and after Task 4's ADR. Filename, H1, and nav entry together.

- [ ] **Step 2: Write the ADR**

Cover:

- **Decision:** a corpus pass is three phases — concurrent score/band, a cross-subject adjudication barrier, serial emit — with staleness re-resolved before each merge and stale subjects skipped.
- **Context:** why the fan-out cannot be over `resolve` — merges mutate the graph later subjects read, and the log's optimistic concurrency is per-tenant-stream by design. Contrast with ADR 0039, where chunks were genuinely independent.
- **Why the barrier is accepted:** a batch that can only draw from finished subjects is the per-subject batching the phase exists to replace.
- **Why `CallLimiter` moved to `domain`:** siblings cannot import each other, and two limiters is two ceilings.
- **Why skip and not retry:** a retry re-scores against a graph the pass is still changing; one pass is one pass, and the next pass finds the duplicates under whatever absorbed them.
- **Consequences:** `concurrency=1` is the default and is equivalent to a serial loop over `resolve`. The staleness window is wider than `resolve`'s and the re-resolution is what makes that safe. BACKLOG B43 is unchanged, not worsened — it concerns a parallel edge created by the extraction fold, not ordering within a pass.
- **What this does not do:** no cross-tenant concurrency, no adaptive tuning, no progress reporting, no transitive closure within one pass.
- **Verdicts:** `0004` stands. `0010` stands and becomes load-bearing. `0015` amended. `0039` amended. `0006` stands.

- [ ] **Step 3: Add the amended-by pointers**

In `docs/adr/0015-...md` and `docs/adr/0039-...md`, update the **Status** line to name this ADR. Do not edit either Decision section — ADR bodies are immutable records.

- [ ] **Step 4: Write the how-to**

Follow the shape of `docs/how-to/tune-ingestion-throughput.md`. Cover: when to run a pass, how `concurrency` interacts with corpus size, that raising it past the subject count does nothing, sharing one `CallLimiter` across callers, and that a pass is not a fixed point so a chain may need a second run.

- [ ] **Step 5: Build the docs**

Run: `uv run mkdocs build --strict`
Expected: PASS, no warnings.

- [ ] **Step 6: Commit**

```bash
git add docs/ mkdocs.yml
git commit -m "Record the decide-then-emit pass and amend the two ADRs it touches

0015 gains a corpus-level method beside the per-subject one; 0039's
CallLimiter becomes a shared primitive, which is what its own argument
predicted -- the ceiling is on calls in flight against one backend, and
the backend does not care which path issued them.

Status pointers only on both; the Decision sections are untouched."
```

---

### Task 11: Final verification and PR

- [ ] **Step 1: Full suite**

Run: `uv run pytest`
Expected: PASS, coverage at or above `.coverage-baseline`.

- [ ] **Step 2: Confirm the hook actually ran**

Run: `uv run pytest tests/unit/test_pre_commit_hook_is_installed.py -v -p no:randomly`
Expected: PASS (or skip, on CI only). An absent hook is indistinguishable from a passing one, and this branch's commits would all have bypassed the gate.

- [ ] **Step 3: Mutation-test the two riskiest modules**

```bash
uv run python scripts/mutation.py mutmut
```

Focus on `domain/similarity.py` and `consolidation/policy.py`. **Classify every survivor; do not report a percentage.** Expect equivalent mutants from `from __future__ import annotations` — `X | None` rewritten with other binary operators is unkillable by construction. A survivor in the batch re-slicing arithmetic or in the `max` in `string_similarity` is a real finding and needs a test.

Record what survived and why in the PR body.

- [ ] **Step 4: Check `BACKLOG.md` is honest**

Everything noticed and not fixed during these ten tasks must have an entry naming the file, what is wrong, and what was learned that made deferring right. `B-ADR-TABLE` from Task 4 should be there. Add anything else.

- [ ] **Step 5: Push and open the PR**

```bash
git push -u origin HEAD
gh pr create --title "Consolidation: overlap-aware name similarity and a corpus-level pass" --body "..."
```

The PR body covers: the forcing measurement (the score's collapse with qualifier length, and the margin arithmetic showing a 0.770 pair needs a 0.717 embedding to survive), what Part A changes and the two-sided constant relation that makes it safe, Part B's three phases and why the fan-out is not over `resolve`, the mutation survivors and their classification, and what was deliberately left out (cross-tenant concurrency, transitive closure, adaptive tuning).

---

## Self-Review

**Spec coverage:** Part A's containment feature → Tasks 1–2; the ceiling invariant → Tasks 2–3; the labelled corpus with both directions → Task 3; A's ADR → Task 4. Part B's `CallLimiter` move → Task 5; phase split → Task 6; cross-subject batching → Task 7; the three-phase pass with staleness re-resolution → Task 8; composed entry point → Task 9; B's ADR, the two amendments, and the how-to → Task 10. The spec's "housekeeping found on the way" → Task 4, Step 4.

**Type consistency:** `_Banded` fields (`subject`, `confirmed`, `undecided`) are used identically in Tasks 6 and 8. `adjudicate_many` returns `list[list[AdjudicationVerdict | None]]` in Task 7 and is consumed as one list per subject in Task 8. `resolve_many` returns `list[EntitiesMerged]` on the service (Task 8) and `list[ConsolidationReport]` on the composed `Consolidator` (Task 9) — different by design, matching how `resolve` already differs between the two classes.

**Known gap, deliberately left to the executor:** Tasks 8 and 9 give test names and docstrings rather than full bodies, because the fixtures must be built from the existing `tests/unit/consolidation/` and `tests/unit/composition/` conftest factories, which the executor should read rather than have guessed at here. Every such test carries the property it must pin and the wrong implementation it must exclude, and Tasks 8 and 7 both end with a deliberate-break step that fails if the fixtures do not distinguish them.
