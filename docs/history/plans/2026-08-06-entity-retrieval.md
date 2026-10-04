# Entity Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `Retriever` that turns a query string into ranked `Entity` results, fusing a semantic channel (`EmbeddingProvider` + `VectorStore`) with a lexical channel (blocking-key candidates from `GraphStore`, scored by Jaro-Winkler).

**Architecture:** Pure scoring and fusion live in `domain/` (lowest layer, no I/O). Orchestration lives in a new `composition/retrieval.py`, because only the top layer may hold all three collaborators — `vector` and `graph` are siblings that may not import each other, and neither may import `llm`. `composition.py` becomes a package to accommodate the second module.

**Tech Stack:** Python 3.12+, pydantic v2, `jellyfish` (already a dependency, via `domain/similarity.py`), pytest, hypothesis, `uv`.

Spec: `docs/superpowers/specs/2026-08-06-entity-retrieval-design.md`.

## Global Constraints

- **Do not bump the version.** `pyproject.toml` stays at `0.3.0`.
- **Never edit `pyproject.toml` dependency tables by hand** — use `uv add`. No new dependencies are expected in this plan.
- **Do not run ruff, bandit, `lint-imports`, or pytest as separate pre-commit steps.** They are wired into `pre-commit` and run on `git commit`. Write the change, then commit; re-`git add` and re-commit when a hook fixes something in place. Running the named test in a step is fine and expected — that is the TDD cycle, not the gate.
- **Anything noticed and not fixed lands in `BACKLOG.md` in the same commit that passes it by.** Not a TODO comment, not the PR body.
- **Commit messages:** imperative, sentence case, no trailing period, no `feat:`/`fix:` prefix. Body says what it cost and what was learned. End with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- **`from __future__ import annotations` at the top of every new module**, matching every other module in the package.
- **Every new public name must be added to `redstring.__all__`** (Task 8) or the public-surface gate fails.
- **RRF constant is `60`**, a module constant, not a parameter.
- **The name "BM25" appears nowhere in `src/`.**
- Prove every regression/property test red before trusting it. Where a step says "run to verify it fails", that is a required step, not a formality.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/redstring/domain/blocking.py` (modify) | Gains name-level key functions; entity-level ones delegate to them |
| `src/redstring/domain/retrieval.py` (create) | `RetrievalMode`, `ScoredEntity`, `RetrievalResult` |
| `src/redstring/domain/fusion.py` (create) | `reciprocal_rank_fusion` — pure, over id sequences |
| `src/redstring/domain/lexical.py` (create) | `lexical_score(query, entity)` — pure, no I/O |
| `src/redstring/composition/__init__.py` (create) | Re-exports `build_graph` and `Retriever` so no import path changes |
| `src/redstring/composition/build_graph.py` (create, from move) | Existing `composition.py`, moved verbatim |
| `src/redstring/composition/retrieval.py` (create) | `Retriever` — orchestration only |
| `src/redstring/__init__.py` (modify) | Four new exports |
| `docs/adr/00NN-*.md` × 2 (create) | Amend ADR 0007; record the not-BM25 decision |
| `docs/how-to/retrieve-entities.md` (create) | How-to |
| `tests/unit/domain/test_*.py` | Unit tests per domain module |
| `tests/unit/composition/test_retrieval.py` | Retriever tests |

---

### Task 1: Name-level blocking keys

Blocking key functions currently take an `Entity`. A query is a bare string, and building a throwaway `Entity` to derive its keys would require inventing a tenant, a type and a confidence. Extract the name-level core and have the entity-level functions delegate — one declaration site, per recurring-defect §2.

**Files:**
- Modify: `src/redstring/domain/blocking.py`
- Test: `tests/unit/domain/test_blocking.py` (exists — add to it)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `prefix_key_for_name(name: str) -> str`, `soundex_key_for_name(name: str) -> str | None`, `query_blocking_keys(query: str) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/domain/test_blocking.py`:

```python
def test_prefix_key_for_name_agrees_with_the_entity_form() -> None:
    """The two spellings are one function; a copy is how they drift."""
    entity = make_entity(name="Ada Lovelace")
    assert prefix_key_for_name("Ada Lovelace") == prefix_key(entity)


def test_soundex_key_for_name_agrees_with_the_entity_form() -> None:
    entity = make_entity(name="Ada Lovelace")
    assert soundex_key_for_name("Ada Lovelace") == soundex_key(entity)


def test_soundex_key_for_name_is_none_when_nothing_can_be_coded() -> None:
    """Same empty case as the entity form: digits code to nothing."""
    assert soundex_key_for_name("12345") is None


def test_query_blocking_keys_carries_prefix_and_soundex() -> None:
    keys = query_blocking_keys("Ada Lovelace")
    assert prefix_key_for_name("Ada Lovelace") in keys
    assert soundex_key_for_name("Ada Lovelace") in keys


def test_query_blocking_keys_never_carries_a_type_key() -> None:
    """A type key blocks an entire type -- often the whole tenant.

    The type key exists so an entity is never unblockable. As a *query* key it
    is the opposite: it matches every entity of that type, so candidate
    generation would degrade into a full scan the moment a query happened to
    share a type. `entity_types` is applied as a filter, not as a key.
    """
    keys = query_blocking_keys("Ada Lovelace")
    assert not any(key.startswith("t:") for key in keys)


def test_query_blocking_keys_drops_an_absent_soundex() -> None:
    """A query that codes to nothing yields the prefix key alone, not a None."""
    assert query_blocking_keys("12345") == [prefix_key_for_name("12345")]


def test_query_blocking_keys_has_no_duplicates() -> None:
    """`find_by_blocking_keys` keys its result by key; a repeat is a wasted query."""
    keys = query_blocking_keys("Ada Lovelace")
    assert len(keys) == len(set(keys))
```

Import the new names alongside the existing imports at the top of the file. If the module has no `make_entity` helper, use whatever factory the existing tests in that file use; if they construct `Entity(...)` inline, do the same.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/domain/test_blocking.py -x -q`
Expected: FAIL, `ImportError: cannot import name 'prefix_key_for_name'`.

- [ ] **Step 3: Implement**

In `src/redstring/domain/blocking.py`, add the name-level functions above the existing `prefix_key`, and rewrite the two entity-level ones to delegate. Move each existing docstring body onto the name-level function (that is where the reasoning now lives) and leave the entity form a one-liner pointing at it.

```python
def prefix_key_for_name(name: str) -> str:
    """The first `PREFIX_LENGTH` characters of the normalized name.

    Normalized here rather than trusting `Entity.normalized_name`: that field
    is whatever the extractor put there, and a key function that produced a
    different answer for the same name depending on which extractor ran would
    make blocking silently miss matches across sources.

    Total, with no empty-name branch. `Entity` rejects a name whose `strip()`
    is falsy and `normalize_name` strips with the same function, so a valid
    entity cannot normalize to nothing. A *query* can be blank, and the
    `Retriever` rejects that before reaching here.
    """
    return _PREFIX + normalize_name(name)[:PREFIX_LENGTH]


def soundex_key_for_name(name: str) -> str | None:
    """A phonetic code for the name, or `None` when there is nothing to code.

    The name is NFKD-normalized and reduced to its ASCII letters first.
    `jellyfish.soundex` accepts anything and codes digits, spaces and CJK into
    keys that collide far too widely -- see the module docstring for the four
    measured cases, and for why the normalization has to come *before* the
    filter rather than instead of it.

    The **whole** name is coded, not the first token. Coding "Ada Lovelace" as
    if it were "Ada" would block it with every Adam and Adams in the tenant,
    which is the block-too-large failure this file's docstring warns about.
    """
    # NFKD first, so an accented letter becomes base + combining mark and the
    # base survives the ASCII filter. Filtering without it drops the letter.
    decomposed = unicodedata.normalize("NFKD", normalize_name(name))
    letters = "".join(
        character for character in decomposed if character.isascii() and character.isalpha()
    )
    if not letters:
        return None
    return _SOUNDEX + jellyfish.soundex(letters)


def prefix_key(entity: Entity) -> str:
    """`prefix_key_for_name` of the entity's name. See that function."""
    return prefix_key_for_name(entity.name)


def soundex_key(entity: Entity) -> str | None:
    """`soundex_key_for_name` of the entity's name. See that function."""
    return soundex_key_for_name(entity.name)


def query_blocking_keys(query: str) -> list[str]:
    """The keys a free-text query should look for candidates under.

    **Prefix and soundex only, never the type key.** The type key exists so no
    entity is unblockable; as a query key it matches every entity of that
    type, which turns candidate generation into a full scan. `entity_types`
    filters the candidates instead.

    A list rather than a `frozenset` because `find_by_blocking_keys` takes a
    `Sequence` and keys its result by what it was asked for; ordering is
    stable so a failing test names the same key twice.
    """
    keys = [prefix_key_for_name(query), soundex_key_for_name(query)]
    return list(dict.fromkeys(key for key in keys if key is not None))
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/domain/test_blocking.py -q`
Expected: PASS, including every pre-existing test in the file unchanged. If an existing test needed editing, stop — that is a behaviour change, not the refactor this task describes.

- [ ] **Step 5: Commit**

```bash
git add src/redstring/domain/blocking.py tests/unit/domain/test_blocking.py
git commit
```

Message: `Extract the name-level blocking keys so a query can derive its own`

---

### Task 2: Retrieval domain types

**Files:**
- Create: `src/redstring/domain/retrieval.py`
- Test: `tests/unit/domain/test_retrieval.py`

**Interfaces:**
- Consumes: `Entity` from `domain.entity`.
- Produces: `RetrievalMode` (`SEMANTIC`/`LEXICAL`/`HYBRID`), `ScoredEntity(entity, score, semantic, lexical)`, `RetrievalResult(query, matches)`.

- [ ] **Step 1: Write the failing test**

`tests/unit/domain/test_retrieval.py`:

```python
"""The retrieval result types."""

from __future__ import annotations

import pytest

from redstring.domain.retrieval import RetrievalMode, RetrievalResult, ScoredEntity

from tests.unit.domain.factories import make_entity  # or the file's local factory


def test_a_channel_that_did_not_rank_is_none_not_zero() -> None:
    """`None` means "not ranked here"; `0.0` means "ranked, and scored zero".

    They are different facts and a caller acts on them differently -- one says
    the lexical channel was off, the other says the name did not match. A type
    that collapsed them would make `semantic is None` unaskable.
    """
    unranked = ScoredEntity(entity=make_entity(), score=0.5, semantic=None, lexical=0.9)
    ranked_zero = ScoredEntity(entity=make_entity(), score=0.5, semantic=0.0, lexical=0.9)
    assert unranked.semantic is None
    assert ranked_zero.semantic == 0.0
    assert unranked.semantic != ranked_zero.semantic


def test_component_scores_default_to_none() -> None:
    """Constructed directly, not through a factory -- the defaults are public.

    Every test building this type through a helper that passes every field
    leaves the declared defaults unexecuted while the signature invites direct
    construction.
    """
    scored = ScoredEntity(entity=make_entity(), score=0.5)
    assert scored.semantic is None
    assert scored.lexical is None


@pytest.mark.parametrize("bad", [-0.01, 1.01])
def test_a_component_score_outside_zero_to_one_is_rejected(bad: float) -> None:
    """Both components are on stated 0..1 scales, so the bound is enforceable.

    `score` is not: RRF is ordinal and unbounded, which is the whole reason it
    carries no `le`/`ge`.
    """
    with pytest.raises(ValueError):
        ScoredEntity(entity=make_entity(), score=0.5, semantic=bad)


def test_the_fused_score_is_not_bounded_to_one() -> None:
    """Two channels at rank 0 sum to 2/60, but nothing in the type caps it.

    Pinning a 0..1 bound here would be the `VectorMatch` scale leaking onto a
    number that is not on it.
    """
    assert ScoredEntity(entity=make_entity(), score=7.5).score == 7.5


def test_a_result_keeps_the_query_it_answered() -> None:
    result = RetrievalResult(query="ada", matches=[])
    assert result.query == "ada"
    assert result.matches == []


def test_the_modes_are_their_own_strings() -> None:
    assert RetrievalMode.HYBRID == "hybrid"
    assert RetrievalMode.SEMANTIC == "semantic"
    assert RetrievalMode.LEXICAL == "lexical"
```

Use whatever entity factory the neighbouring `tests/unit/domain/` modules already use; do not add a new one.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/domain/test_retrieval.py -x -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'redstring.domain.retrieval'`.

- [ ] **Step 3: Implement**

`src/redstring/domain/retrieval.py`:

```python
"""What a retrieval asks for and what it answers with.

## `score` is not on `VectorMatch`'s scale, and that is the point

`VectorMatch.score` is cosine mapped onto `0..1`, and `domain/vector.py`
explains at length why pinning that scale in one place matters: "score" is
ambiguous across vector databases, and an adapter that inverted the sense
would return plausible nonsense rather than an error.

`ScoredEntity.score` is a **reciprocal-rank-fusion** score. It is *ordinal*:
comparable within one result set, meaningless across queries, and never
interpretable as a similarity. It carries no `0..1` bound because it has
none -- two channels agreeing at rank 0 give `2/60`, and nothing caps the sum
as channels are added. Reusing the bare name `score` for a differently-scaled
number is exactly the trap `domain/vector.py` warns about, so the scale is
stated here, where the type is defined, rather than left to a how-to.

## `None` and `0.0` are different facts

`semantic` and `lexical` are `None` when that channel did not rank the entity
at all, and a float when it did. "The lexical channel was off" and "the name
did not match" are different things and a caller acts on them differently.
Both are retained after fusion rather than discarded: without them nobody can
distinguish an entity that matched strongly on both channels from one that
matched on its name alone, and that distinction is the entire reason for
being hybrid.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from redstring.domain.entity import Entity


class RetrievalMode(StrEnum):
    """Which channels a retrieval runs."""

    SEMANTIC = "semantic"
    LEXICAL = "lexical"
    HYBRID = "hybrid"


class ScoredEntity(BaseModel):
    """One entity a retrieval returned, with the scores that put it there."""

    entity: Entity
    #: Fused, ordinal, unbounded. See the module docstring.
    score: float
    #: `VectorMatch` scale (cosine mapped onto 0..1), or `None` if the
    #: semantic channel did not rank this entity.
    semantic: float | None = Field(default=None, ge=0.0, le=1.0)
    #: Jaro-Winkler on 0..1, or `None` if the lexical channel did not rank it.
    lexical: float | None = Field(default=None, ge=0.0, le=1.0)


class RetrievalResult(BaseModel):
    """The answer to one query, best first."""

    query: str
    matches: list[ScoredEntity] = []
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/domain/test_retrieval.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/redstring/domain/retrieval.py tests/unit/domain/test_retrieval.py
git commit
```

Message: `Add the retrieval result types, and say what their score is not`

---

### Task 3: Reciprocal rank fusion

**Files:**
- Create: `src/redstring/domain/fusion.py`
- Test: `tests/unit/domain/test_fusion.py`

**Interfaces:**
- Consumes: `EntityId` from `domain.ids` (it is `UUID`).
- Produces: `RRF_K: int = 60`, `reciprocal_rank_fusion(rankings: Sequence[Sequence[EntityId]]) -> list[tuple[EntityId, float]]` — fused best-first, ties broken by ascending canonical lowercase hyphenated id string.

- [ ] **Step 1: Write the failing test**

`tests/unit/domain/test_fusion.py`:

```python
"""Reciprocal rank fusion."""

from __future__ import annotations

from uuid import UUID

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from redstring.domain.fusion import RRF_K, reciprocal_rank_fusion

# Fixed ids, ordered so the *string* order is knowable at a glance. The
# tie-break is on the canonical lowercase hyphenated form, so a test using
# random uuid4s would pass or fail depending on how they happened to sort --
# the exact shape CLAUDE.md's table records for a `<=` that meant `==`.
A = UUID("00000000-0000-4000-8000-00000000000a")
B = UUID("00000000-0000-4000-8000-00000000000b")
C = UUID("00000000-0000-4000-8000-00000000000c")


def test_an_entity_in_both_rankings_at_the_same_rank_beats_one_in_either_alone() -> None:
    """The collision case: without it, "sum" and "max" are the same function.

    A and B are both rank 0 -- A in both rankings, B in one. Under `max` they
    tie at `1/60` and the tie-break decides, which A wins anyway on id order.
    So the test asserts the *score*, not the order: only summing gives A twice
    B's score, and only a scored assertion can see the difference.
    """
    fused = dict(reciprocal_rank_fusion([[A], [A, B]]))
    assert fused[A] == pytest.approx(2 / (RRF_K + 1))
    assert fused[B] == pytest.approx(1 / (RRF_K + 2))


def test_rank_is_one_based_so_the_first_element_is_not_a_division_by_k() -> None:
    """`1/(k+rank)` with a 0-based rank makes the top of each list `1/60`.

    Stated as a literal rather than as an expression in RRF_K: writing the
    expectation in terms of the constant under test makes it true for any
    value of that constant, zero included.
    """
    fused = dict(reciprocal_rank_fusion([[A, B]]))
    assert fused[A] == pytest.approx(1 / 61)
    assert fused[B] == pytest.approx(1 / 62)


def test_ties_break_by_ascending_id_string() -> None:
    """Two entities at the same rank in different rankings score identically."""
    fused = reciprocal_rank_fusion([[B], [A]])
    assert [entity_id for entity_id, _ in fused] == [A, B]


def test_an_empty_ranking_contributes_nothing_and_does_not_shift_ranks() -> None:
    """An off channel is an empty list, not a missing argument.

    If an empty ranking shifted the others' ranks, turning a channel off would
    silently rescore the channel that stayed on.
    """
    with_empty = reciprocal_rank_fusion([[A, B], []])
    without = reciprocal_rank_fusion([[A, B]])
    assert with_empty == without


def test_no_rankings_at_all_is_empty() -> None:
    assert reciprocal_rank_fusion([]) == []


def test_a_repeated_id_within_one_ranking_counts_once_at_its_best_rank() -> None:
    """A malformed channel must not be able to inflate its own contribution."""
    fused = dict(reciprocal_rank_fusion([[A, B, A]]))
    assert fused[A] == pytest.approx(1 / 61)


@given(st.lists(st.sampled_from([A, B, C]), unique=True, max_size=3))
@example([])
@example([A])
def test_every_id_present_appears_exactly_once_in_the_output(ids: list[UUID]) -> None:
    """Boundary sizes pinned as examples: a sampler decides how often it draws
    0 and 1, and mutation runs lower the example count to 5.
    """
    fused = reciprocal_rank_fusion([ids])
    assert sorted(entity_id for entity_id, _ in fused) == sorted(set(ids))


@given(st.permutations([A, B, C]))
def test_the_order_is_total_so_no_two_results_are_interchangeable(
    ranking: list[UUID],
) -> None:
    """A `>` widened to `>=` is only "equivalent" if the order is total.

    Asserting the totality is what makes that label honest rather than
    assumed.
    """
    fused = reciprocal_rank_fusion([ranking])
    keys = [(-score, str(entity_id)) for entity_id, score in fused]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/domain/test_fusion.py -x -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'redstring.domain.fusion'`.

- [ ] **Step 3: Implement**

`src/redstring/domain/fusion.py`:

```python
"""Combining two rankings into one, by rank and never by score.

## Why rank and not score

The semantic channel scores with cosine mapped onto `0..1`; the lexical
channel scores with Jaro-Winkler on `0..1`. The shared range is a coincidence
of both being normalized -- the two numbers have **no common unit**, and a
weighted sum of them silently invents an exchange rate that will be wrong for
some corpus and unfalsifiable for all of them. Reciprocal rank fusion uses
only the position, which is the one thing both channels genuinely produce.

The cost is real and worth stating: RRF discards magnitude, so a semantic
match at `0.99` and one at `0.51` contribute equally if both are ranked
first. That is why `ScoredEntity` retains the component scores -- the caller
can see what fusion threw away.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redstring.domain.ids import EntityId

#: The `k` of `1/(k + rank)`, from Cormack, Clarke and Buettcher (SIGIR 2009),
#: "Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning
#: Methods", where 60 was found to work across runs without tuning.
#:
#: Deliberately a constant and not a parameter. Exposing it would invite
#: tuning against a benchmark this library does not have, and a value tuned on
#: one caller's corpus is not a better default -- it is the same arbitrary
#: number with a misleading provenance.
RRF_K = 60


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[EntityId]],
) -> list[tuple[EntityId, float]]:
    """Fuse `rankings` into one, best first.

    Each ranking is best-first and may be empty; an empty one contributes
    nothing and does not shift any other ranking's positions, so turning a
    channel off cannot rescore the channel that stayed on.

    An id repeated within one ranking counts **once, at its best position** --
    a channel that emitted a duplicate must not be able to inflate its own
    contribution.

    Ties break by ascending `EntityId` compared as its canonical lowercase
    hyphenated string, the same rule `VectorStore.search` uses. The result is
    therefore a total order, so truncating to `k` through a tie cannot depend
    on dict ordering or on which channel ran first.
    """
    scores: defaultdict[EntityId, float] = defaultdict(float)
    for ranking in rankings:
        seen: set[EntityId] = set()
        for position, entity_id in enumerate(ranking):
            if entity_id in seen:
                continue
            seen.add(entity_id)
            scores[entity_id] += 1.0 / (RRF_K + position + 1)
    return sorted(scores.items(), key=lambda item: (-item[1], str(item[0])))
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/domain/test_fusion.py -q`
Expected: PASS.

- [ ] **Step 5: Break it on purpose and watch the properties fail**

A property that stays green under a deliberate defect is worse than no property. Temporarily change `-item[1]` to `item[1]` (inverting the order) and run the module. Expected: `test_the_order_is_total_so_no_two_results_are_interchangeable` FAILS. Then change `1.0 / (RRF_K + position + 1)` to `1.0 / (RRF_K + position)` and confirm `test_rank_is_one_based...` FAILS. Revert both. If either stays green, the test is wrong — fix the test before continuing.

- [ ] **Step 6: Commit**

```bash
git add src/redstring/domain/fusion.py tests/unit/domain/test_fusion.py
git commit
```

Message: `Fuse rankings by reciprocal rank, because the two scores share no unit`

---

### Task 4: The lexical scorer

**Files:**
- Create: `src/redstring/domain/lexical.py`
- Test: `tests/unit/domain/test_lexical.py`

**Interfaces:**
- Consumes: `Entity`; `string_similarity` from `domain.similarity`.
- Produces: `PROPERTY_WEIGHT: float = 0.6`, `lexical_score(query: str, entity: Entity) -> float` on `0..1`.

- [ ] **Step 1: Write the failing test**

`tests/unit/domain/test_lexical.py`:

```python
"""Scoring an entity against a free-text query, without a text index."""

from __future__ import annotations

import pytest

from redstring.domain.lexical import PROPERTY_WEIGHT, lexical_score

# Use the same entity factory the neighbouring domain tests use.


def test_an_exact_name_scores_one() -> None:
    assert lexical_score("Ada Lovelace", make_entity(name="Ada Lovelace")) == 1.0


def test_casing_and_whitespace_are_not_differences() -> None:
    """`string_similarity` normalizes both sides; this pins that it is reached."""
    assert lexical_score("ada  LOVELACE", make_entity(name="Ada Lovelace")) == 1.0


def test_a_runtime_built_query_scores_the_same_as_a_literal() -> None:
    """CPython interns literals, so a literal-only suite cannot see `is` for `==`.

    The query here is built at runtime and is a distinct object from any
    literal in the module.
    """
    built = " ".join(["Ada", "Lovelace"])
    assert built is not "Ada Lovelace"  # noqa: F632 - the distinctness is the point
    assert lexical_score(built, make_entity(name="Ada Lovelace")) == 1.0


def test_an_abbreviation_beats_an_unrelated_name() -> None:
    """The case embeddings are worst at, and the reason this channel exists."""
    acme = lexical_score("Acme Corp", make_entity(name="ACME Corporation"))
    other = lexical_score("Acme Corp", make_entity(name="Zebra Holdings"))
    assert acme > other


def test_a_property_can_match_but_scores_below_the_same_match_on_the_name() -> None:
    """The weighting is the claim -- a name match is stronger evidence."""
    on_name = lexical_score("Ada Lovelace", make_entity(name="Ada Lovelace"))
    on_property = lexical_score(
        "Ada Lovelace",
        make_entity(name="Zebra Holdings", properties={"also_known_as": "Ada Lovelace"}),
    )
    assert on_property == pytest.approx(PROPERTY_WEIGHT)
    assert on_property < on_name
    assert on_property > 0.0


def test_a_non_string_property_is_skipped_rather_than_coerced() -> None:
    """`properties` is free-form JSON: ints, lists and dicts all appear.

    Coercing them to `str` would invent matches against "7" and "['a']" that
    no caller asked for -- the same reading `ports/vector_store.py` applies to
    a non-string `entity_type`.
    """
    entity = make_entity(name="Zebra", properties={"count": 7, "tags": ["ada"], "d": {}})
    assert lexical_score("ada", entity) == pytest.approx(
        lexical_score("ada", make_entity(name="Zebra"))
    )


def test_normalized_name_is_scored_when_it_differs_from_the_name() -> None:
    """The field is whatever the extractor wrote; it can carry a form the
    name does not, and ignoring it would discard the extractor's own work.
    """
    entity = make_entity(name="A. Lovelace", normalized_name="ada lovelace")
    assert lexical_score("Ada Lovelace", entity) == 1.0


def test_the_best_field_wins_rather_than_the_fields_summing() -> None:
    """Summing would let an entity with many mediocre fields outrank an exact
    name match, and would push the result above 1.0.
    """
    entity = make_entity(
        name="Ada Lovelace",
        properties={"a": "Ada Lovelace", "b": "Ada Lovelace", "c": "Ada Lovelace"},
    )
    assert lexical_score("Ada Lovelace", entity) == 1.0


def test_the_score_stays_within_zero_and_one() -> None:
    entity = make_entity(name="Ada Lovelace", properties={"a": "Ada Lovelace"})
    assert 0.0 <= lexical_score("Ada Lovelace", entity) <= 1.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/domain/test_lexical.py -x -q`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/redstring/domain/lexical.py`:

```python
"""Scoring an entity against a free-text query, with no text index.

This is **not BM25**, and nothing here should be renamed towards it. BM25
weights a term by corpus statistics -- document frequency and average document
length -- and neither quantity means anything over a corpus of entity names,
where every "document" is a handful of words. A field-weighted string
similarity does the job people actually want from a lexical channel here:
catching `ACME Corporation` against `Acme Corp`, which is exactly where cosine
is weakest. Real BM25 needs stored text, which this library does not keep.

The score is a **maximum over fields, not a sum.** Summing would let an entity
with many mediocre fields outrank an exact name match, and would leave the
result unbounded above -- so it could not be compared against the semantic
channel's `0..1` even informally, and could not be reported on `ScoredEntity`
under a stated scale.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from redstring.domain.similarity import string_similarity

if TYPE_CHECKING:
    from redstring.domain.entity import Entity

#: What a match on a property value is worth relative to a match on the name.
#:
#: A name is what an entity *is*; a property is something recorded about it,
#: and a query matching one is weaker evidence. The exact figure is a
#: judgement rather than a measurement -- there is no graded retrieval corpus
#: in this repo to fit it against, and inventing one from the accuracy suite's
#: five documents would dress a guess as a result.
PROPERTY_WEIGHT = 0.6


def lexical_score(query: str, entity: Entity) -> float:
    """How well `entity` matches `query` lexically, on `0..1`.

    The best of: the name, the extractor's `normalized_name`, and each string
    value in `properties` at `PROPERTY_WEIGHT`. Casing and whitespace are not
    differences -- `string_similarity` normalizes both sides, and this reuses
    it rather than growing a second normalization, because two normalizations
    that agree today are how two subsystems disagree in six months.

    Non-string property values are skipped rather than coerced. `properties`
    is free-form JSON, and `str(7)` would invent a match against the query
    `"7"` that no caller asked for -- the same reading `ports/vector_store.py`
    gives a non-string `entity_type`.
    """
    best = max(
        string_similarity(query, entity.name),
        string_similarity(query, entity.normalized_name),
    )
    for value in entity.properties.values():
        if isinstance(value, str):
            best = max(best, PROPERTY_WEIGHT * string_similarity(query, value))
    return best
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/domain/test_lexical.py -q`
Expected: PASS.

- [ ] **Step 5: Break it on purpose**

Change `max(best, PROPERTY_WEIGHT * ...)` to `max(best, string_similarity(...))` (dropping the weight) and confirm `test_a_property_can_match_but_scores_below_the_same_match_on_the_name` FAILS. Change `isinstance(value, str)` to `value is not None` and confirm `test_a_non_string_property_is_skipped_rather_than_coerced` FAILS (it will raise inside `string_similarity`). Revert both.

- [ ] **Step 6: Commit**

```bash
git add src/redstring/domain/lexical.py tests/unit/domain/test_lexical.py
git commit
```

Message: `Score entities lexically by field, and say why it is not BM25`

---

### Task 5: Make `composition` a package

Pure move. No behaviour change, so **every existing test must pass without modification** — if an assertion needs editing, stop and say so.

**Files:**
- Delete: `src/redstring/composition.py`
- Create: `src/redstring/composition/__init__.py`, `src/redstring/composition/build_graph.py`
- Modify: `pyproject.toml` (import-linter layer comment only — the layer name `composition` is unchanged)

**Interfaces:**
- Produces: `redstring.composition.build_graph`, `redstring.composition.GraphBuildReport`, `redstring.composition.AUTO`, `redstring.composition.AutoDomain` — every name previously importable from `redstring.composition`, unchanged.

- [ ] **Step 1: Record the existing surface, so the move can be proved faithful**

```bash
uv run python -c "import redstring.composition as c; print(sorted(n for n in vars(c) if not n.startswith('_')))"
```

Save that output — Step 5 compares against it.

- [ ] **Step 2: Move the file**

```bash
git mv src/redstring/composition.py src/redstring/composition/build_graph.py
```

(`git mv` onto a path whose parent does not exist fails; `mkdir -p src/redstring/composition` first, then `git mv`.)

- [ ] **Step 3: Write the package `__init__`**

`src/redstring/composition/__init__.py`:

```python
"""The top layer: modules that hold collaborators no lower layer may hold together.

`pyproject.toml` states that a module wanting in here has to say what it
composes. There are two:

- `build_graph` composes `LlmProvider` + `GraphStore` (+ optionally
  `EmbeddingProvider` + `VectorStore`). `extraction` may not import
  `projections`, so nothing below can hold both halves.
- `retrieval` composes `EmbeddingProvider` + `VectorStore` + `GraphStore`.
  `vector` and `graph` are siblings that may not import each other and
  neither may import `llm`, so no sibling can hold all three.

This was one module until retrieval arrived; see the ADR amending 0007.
"""

from __future__ import annotations

from redstring.composition.build_graph import AUTO, AutoDomain, GraphBuildReport, build_graph

__all__ = ["AUTO", "AutoDomain", "GraphBuildReport", "build_graph"]
```

Adjust the imported names to exactly match what Step 1 printed. If Step 1 listed a public name this `__all__` omits, an existing import path breaks — add it.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q -x`
Expected: PASS, with **no test file edited**. Any failure here is a faithfulness problem in the move, not a test to update.

- [ ] **Step 5: Prove the surface is unchanged**

Re-run the Step 1 command and diff against the saved output. Expected: identical.

- [ ] **Step 6: Update the layer prose**

In `pyproject.toml`, the import-linter comment says `composition` "is the top layer and holds one module" and that "a second module wanting in here should have to say what it composes." Rewrite it to name both modules and what each composes (the `__init__` docstring above is the wording). In `CLAUDE.md`, update the matching paragraph under "Architecture contract" the same way — a stale layer diagram in binding instructions sends the next author to a package that does not exist.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit
```

Message: `Make composition a package, since retrieval belongs beside build_graph`

Body must state that the move is verbatim, that no test changed, and what the second module composes.

---

### Task 6: The `Retriever`

**Files:**
- Create: `src/redstring/composition/retrieval.py`
- Modify: `src/redstring/composition/__init__.py`
- Test: `tests/unit/composition/test_retrieval.py`

**Interfaces:**
- Consumes: `query_blocking_keys` (Task 1), `RetrievalMode`/`ScoredEntity`/`RetrievalResult` (Task 2), `reciprocal_rank_fusion` (Task 3), `lexical_score` (Task 4); ports `EmbeddingProvider`, `VectorStore`, `GraphStore`; `DimensionMismatchError` from `domain.exceptions`.
- Produces: `Retriever(embeddings=..., vectors=..., graph=...)` with `async retrieve(query, tenant_id, *, k=10, entity_types=None, mode=RetrievalMode.HYBRID) -> RetrievalResult`.

Port signatures this task depends on, verbatim:

```python
EmbeddingProvider.embed(texts: Sequence[str]) -> list[list[float]]        # order is the contract
EmbeddingProvider.dimension -> int
VectorStore.dimension -> int
VectorStore.search(vector, tenant_id, *, k=10, entity_types=None, min_score=None) -> list[VectorMatch]
GraphStore.find_by_blocking_keys(keys: Sequence[str], tenant_id) -> dict[str, list[Entity]]
GraphStore.get_entities(entity_ids: Sequence[EntityId], tenant_id) -> list[Entity]   # order unspecified
```

`get_entities` returns entities in **unspecified order** and omits ids it does not have — key the result by `id` rather than zipping.

- [ ] **Step 1: Write the failing tests**

`tests/unit/composition/test_retrieval.py`. Build stores with the real in-memory adapters (`InMemoryGraphStore`, `InMemoryVectorStore`) and `FakeEmbeddingProvider` — not `MagicMock`. A `MagicMock` answers any attribute, which is how a router in this repo shipped 583 lines routing on a deleted model with a green suite.

```python
"""The composed retrieval surface."""

from __future__ import annotations

import pytest

from redstring import (
    DimensionMismatchError,
    FakeEmbeddingProvider,
    InMemoryGraphStore,
    InMemoryVectorStore,
    RetrievalMode,
    Retriever,
)


@pytest.mark.asyncio
async def test_an_exact_name_is_retrieved() -> None: ...


@pytest.mark.asyncio
async def test_two_tenants_holding_the_same_entity_id_never_cross() -> None:
    """The composite-key case, forced rather than hoped for.

    Ids come from `uuid4()` everywhere else in this repo and never collide, so
    a `(tenant_id, id)` key compared on `id` alone survives every natural
    test. CLAUDE.md records this firing anyway, in a fix round that cited the
    rule. Both tenants get the *same* `EntityId` with different names; each
    retrieve must see only its own.
    """


@pytest.mark.asyncio
async def test_a_vector_match_whose_entity_the_graph_lacks_is_skipped() -> None:
    """The two stores are independent projections and lag independently.

    Write the vector and not the entity. The result must omit it, must not
    raise, and must not backfill to `k` from further down -- backfilling would
    hide a projection that had fallen badly behind.
    """


@pytest.mark.asyncio
async def test_a_skipped_dangling_match_is_not_backfilled() -> None:
    """Distinct from the test above: this one asserts the *count*.

    Three vectors, one dangling, `k=3` -> two results. A version that topped
    up to `k` passes the skip test and fails this one.
    """


@pytest.mark.asyncio
async def test_the_lexical_channel_scores_a_candidate_after_a_zero_scoring_one() -> None:
    """A bad row followed by a good one.

    On a one-element remainder `break` and `continue` are the same function.
    The blocking key must return a poor candidate *before* a strong one, so a
    loop that stops at the first weak match drops the answer.
    """


@pytest.mark.asyncio
async def test_entity_types_filters_the_lexical_channel_before_k_is_applied() -> None:
    """Filter-before-k, the defect `ports/vector_store.py` calls out by name.

    `k=1` with one non-matching candidate ranked above one matching candidate.
    Truncating first then filtering returns nothing while a match exists.
    """


@pytest.mark.asyncio
async def test_a_result_reports_both_component_scores_when_both_channels_ranked() -> None: ...


@pytest.mark.asyncio
async def test_a_semantic_only_mode_leaves_lexical_none() -> None:
    """`None` is the claim that the channel did not rank it -- see the type."""


@pytest.mark.asyncio
async def test_a_lexical_only_mode_makes_no_embedding_call() -> None:
    """Wrap the provider in a counting subclass; assert the count is zero.

    A mode that embedded anyway would be correct in its output and would cost
    a paid round trip per query -- invisible to every assertion about results.
    """


@pytest.mark.asyncio
async def test_entities_are_compared_by_equality_not_identity() -> None:
    """Both shipped adapters hand back the object they were given, so `is`
    where `==` was meant passes against both.

    Use a `GraphStore` wrapper whose reads return
    `Entity.model_validate(entity.model_dump())` -- equal, distinct objects,
    which is a permitted adapter behaviour no port forbids.
    """


@pytest.mark.asyncio
async def test_mutating_a_result_cannot_change_what_a_later_retrieve_returns() -> None: ...


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
async def test_a_blank_query_raises(blank: str) -> None: ...


@pytest.mark.asyncio
async def test_k_zero_returns_nothing_and_a_negative_k_raises() -> None:
    """Both pinned as literals -- `VectorStore.search` says the same, and a
    property sampling `k` from a range makes boundary coverage depend on the
    sampler and on the lowered example count under mutation.
    """


@pytest.mark.asyncio
async def test_empty_entity_types_matches_nothing() -> None:
    """`[]` means nothing matches; `None` means no filter. Same as the port."""


@pytest.mark.asyncio
async def test_a_provider_and_store_of_different_dimensions_are_refused() -> None:
    """At construction, before any text is embedded -- `build_graph`'s rule."""
    with pytest.raises(DimensionMismatchError):
        Retriever(
            embeddings=FakeEmbeddingProvider(dimension=8),
            vectors=InMemoryVectorStore(dimension=16),
            graph=InMemoryGraphStore(),
        )


@pytest.mark.asyncio
async def test_more_results_than_k_are_truncated() -> None: ...
```

Fill in each elided body. Check the exact constructor signatures of `FakeEmbeddingProvider`, `InMemoryVectorStore` and `InMemoryGraphStore` in the source before writing them; do not guess. Use a realistic dimension where one is free — a check written with `is not` passes at 8 and rejects everything real at 768.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/composition/test_retrieval.py -x -q`
Expected: FAIL, `ImportError: cannot import name 'Retriever'`.

- [ ] **Step 3: Implement**

`src/redstring/composition/retrieval.py`. The algorithm:

1. Reject a blank query (`not query.strip()`) with `ValueError`; reject `k < 0` with `ValueError`; return an empty `RetrievalResult` for `k == 0`.
2. Semantic channel, when `mode` is `SEMANTIC` or `HYBRID`: `[vector] = await self._embeddings.embed([query])`, then `await self._vectors.search(vector, tenant_id, k=k, entity_types=entity_types)`. Keep `match.score` per id and the id order as the ranking.
3. Lexical channel, when `mode` is `LEXICAL` or `HYBRID`: `query_blocking_keys(query)`, `find_by_blocking_keys`, flatten the groups and dedupe by `entity.id` (an entity carrying several keys appears under each). **Filter by `entity_types` before truncating** — compare `entity.entity_type` against the sequence, with `[]` matching nothing and `None` meaning no filter. Score each with `lexical_score`, sort by `(-score, str(entity.id))`, truncate to `k`.
4. Fuse the two id rankings with `reciprocal_rank_fusion`, truncate to `k`.
5. Resolve entities: the lexical channel already holds them; fetch the rest with one `get_entities` call and key the result by `id`. Skip any id neither source has, without backfilling.
6. Build `ScoredEntity` per surviving id, carrying `semantic`/`lexical` only where that channel ranked it.

Write the module docstring to carry the blocking-recall limitation verbatim:

> **Lexical recall is bounded by blocking.** A query that shares no blocking key with an entity cannot be retrieved lexically, however high its string similarity would have been. There is no text index in this library, so candidates come from the same prefix and soundex keys consolidation uses. This is the honest cost of storing no text, and it is the second reason this channel is not called BM25.

and the dangling-match policy:

> A vector match whose entity the graph store does not have is **skipped, and the result is not backfilled to `k`**. The two stores are independent projections of one log and lag independently, so this is ordinary, not exceptional — raising would make retrieval fail during replay, and topping up would turn a badly lagging projection into silence.

Store the collaborators privately (`self._embeddings`, `self._vectors`, `self._graph`) — they are not part of the promise. Type every signature; `mypy --strict` covers this package.

- [ ] **Step 4: Export from the package**

Add `Retriever` to `src/redstring/composition/__init__.py`'s imports and `__all__`.

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/unit/composition/test_retrieval.py -q`
Expected: PASS.

- [ ] **Step 6: Break it on purpose**

Three deliberate defects, each of which must turn a test red. If any stays green, that test is not testing what it claims:

1. Move the `entity_types` filter to *after* the truncate-to-`k` → `test_entity_types_filters_the_lexical_channel_before_k_is_applied` fails.
2. Replace the lexical loop's `continue` with `break` → `test_the_lexical_channel_scores_a_candidate_after_a_zero_scoring_one` fails.
3. Backfill the result to `k` after skipping dangling matches → `test_a_skipped_dangling_match_is_not_backfilled` fails.

Revert all three.

- [ ] **Step 7: Commit**

```bash
git add src/redstring/composition/retrieval.py src/redstring/composition/__init__.py tests/unit/composition/test_retrieval.py
git commit
```

Message: `Compose a hybrid retriever over the embedding and graph stores`

---

### Task 7: Public surface

**Files:**
- Modify: `src/redstring/__init__.py`
- Test: the three existing gate tests run unchanged.

**Interfaces:**
- Produces: `Retriever`, `RetrievalMode`, `RetrievalResult`, `ScoredEntity` importable from `redstring`.

- [ ] **Step 1: Add the exports**

Add the four imports in the existing alphabetical positions and the four names to `__all__`, also alphabetically. Update the capability prose near the top of the module docstring — it currently enumerates what the library offers and does not mention retrieval.

- [ ] **Step 2: Run the gates**

Run: `uv run pytest tests/unit/test_public_api.py -q` (use the real filename — find it with `grep -rl "__all__" tests/unit | head`).
Expected: PASS.

The signature gate requires every type named in an exported signature to itself be exported. `Retriever.__init__` names `EmbeddingProvider`, `VectorStore`, `GraphStore`; `retrieve` names `TenantId`, `RetrievalMode`, `RetrievalResult`. All are exported after this task. **The gate walks the MRO**, so a base class's `__init__` counts too.

- [ ] **Step 3: Extend the end-to-end example**

Find the example the third gate test runs (it asserts the example imports nothing but `redstring`). Add a retrieval step to it: build a graph, then retrieve against it, importing only from `redstring`.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit
```

Message: `Export the retrieval surface, and retrieve in the end-to-end example`

---

### Task 8: ADRs, how-to, backlog

Per `definition-of-done.md`, work is incomplete while a decision it changed is undocumented, and **ADR numbers are allocated at merge against `main`, not at drafting**.

**Files:**
- Create: two ADRs in `docs/adr/`
- Create: `docs/how-to/retrieve-entities.md`
- Modify: `docs/adr/0007-composition-is-the-only-top-layer.md` (Status line only), `docs/adr/index.md`, `mkdocs.yml` nav, `BACKLOG.md`

- [ ] **Step 1: Allocate the numbers**

```bash
git ls-tree --name-only main docs/adr/ | sort | tail -1
```

Number both new ADRs against that, not against anything written in this plan.

- [ ] **Step 2: Write the amending ADR**

Title: `composition holds a second module, and retrieval is what it composes`. Content: ADR 0007's first decision said the layer holds exactly one module. That stands as reasoning and is amended in fact. State what retrieval composes and why no sibling can hold all three (`vector` and `graph` may not import each other; neither may import `llm`). **No counts, no file tables** — those go in the commit message. Add an "Amended by" pointer to 0007's **Status** line; do not touch its Decision.

- [ ] **Step 3: Write the not-BM25 ADR**

Title: `the lexical channel is not BM25, and its recall is bounded by blocking`. Content: why corpus statistics are undefined over entity names; why RRF rather than a weighted score blend; that candidate generation reuses blocking keys and what recall that costs; that real BM25 waits for a chunk store. Record the two rejected alternatives — weighted score fusion, and a `bm25` name on a non-BM25 scorer — with why each was rejected.

- [ ] **Step 4: Write the how-to**

`docs/how-to/retrieve-entities.md`, following the shape of the existing how-tos. A runnable example using only `redstring` imports; the three modes; what the component scores mean; and the blocking-recall limitation stated plainly, because a caller who does not know it will read a missing result as a bug.

- [ ] **Step 5: Wire the docs up**

Add both ADRs to `docs/adr/index.md` and the how-to plus both ADRs to the `mkdocs.yml` nav. Then:

Run: `uv run mkdocs build --strict`
Expected: PASS. This is the gate that catches a citation pointing at a page that does not exist — a half-finished docs change has no failing test otherwise.

- [ ] **Step 6: File the backlog entries**

At minimum, in `BACKLOG.md`, written so someone picking each up cold does not have to rediscover it:

- `PROPERTY_WEIGHT = 0.6` is a judgement, not a measurement — there is no graded retrieval corpus in the repo to fit it against, and the accuracy suite's five documents are too few to be one. Name the file and the constant, and say what evidence would settle it.
- No retrieval accuracy suite exists. `tests/accuracy/` measures extraction; nothing measures whether hybrid beats semantic alone on this corpus, so the claim that fusion helps is currently an argument rather than a result.
- The lexical channel does not consult aliases. `GraphStore.find_aliases` and `resolve_entity_ids` exist, and a query matching an alias name retrieves nothing today. Say why it was deferred (it needs a decision about whether an alias hit returns the alias or its canonical, which is `domain.preference`'s territory).
- Anything else noticed during Tasks 1–7 and not fixed.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit
```

Message: `Record what retrieval composes, and why its lexical half is not BM25`

---

### Task 9: Ship it

- [ ] **Step 1: Confirm the version did not move**

```bash
grep '^version' pyproject.toml
```
Expected: `version = "0.3.0"`. If it moved, revert that hunk.

- [ ] **Step 2: Run the full gate as the hook runs it**

Run: `uv run python scripts/coverage_ratchet.py`
Expected: green, with a **positive pass count** and coverage at or above `.coverage-baseline`. A run reporting "0 collected" has the same exit status as a passing one — read the count, not the exit code.

If coverage fell, do not paper over it with tests. Under 0.1% is arithmetic: lower the baseline in the same commit and say why in the message.

- [ ] **Step 3: Confirm the architecture contract still holds**

The `composition` package is new structure on an `exhaustive = true` contract. `lint-imports` runs in the hook, so a clean commit is the evidence; if it failed, the fix is placing the module deliberately, not editing the contract.

- [ ] **Step 4: Branch, push, open the PR**

```bash
git switch -c retrieval-surface   # if not already on a branch
git push -u origin HEAD
gh pr create --title "Add a hybrid entity retrieval surface" --body ...
```

The PR body states: what shipped, that the version deliberately did not move, that the lexical channel is not BM25 and why, the blocking-recall limitation, the coverage movement with its reason, and that part B (chunk store, true BM25) is next. End with the Claude Code footer.

---

## Self-Review

**Spec coverage.** Composition-as-package → Task 5. Domain types → Task 2. RRF → Task 3. Lexical channel → Task 4, with candidate generation split into Task 1 because it needs a `blocking.py` refactor that stands alone. API and error policy → Task 6. Named failure-shape tests → Task 6 steps 1 and 6, plus Task 3 step 5 and Task 4 step 5. Public surface → Task 7. ADRs, how-to, backlog → Task 8. Version-unchanged and PR → Task 9. No spec section is unimplemented.

**One thing the spec did not settle, decided here:** the spec said the lexical channel derives blocking keys from the query but not *which* keys. Task 1 excludes the type key and says why — a type key matches every entity of its type, so including it turns candidate generation into a full scan. `entity_types` filters instead. This is a strengthening, not a departure.

**Type consistency.** `query_blocking_keys -> list[str]` feeds `find_by_blocking_keys(keys: Sequence[str], ...)`. `reciprocal_rank_fusion(rankings: Sequence[Sequence[EntityId]]) -> list[tuple[EntityId, float]]` is consumed by Task 6 as `(entity_id, score)` pairs. `lexical_score(query, entity) -> float` on `0..1` matches `ScoredEntity.lexical`'s `ge=0.0, le=1.0`. `EntityId` is `UUID`, so `str(entity_id)` is the canonical lowercase hyphenated form the tie-break needs.
