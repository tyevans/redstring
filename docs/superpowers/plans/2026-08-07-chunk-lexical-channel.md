# Chunk Lexical Channel (B2a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** BM25 ranking over the stored chunk corpus, with scoring in `domain/`
and only recall and corpus statistics in the adapters.

**Architecture:** A domain-owned tokenizer produces terms. Adapters answer
"which chunks contain these terms, how often, how long are they, and how many
chunks contain each term". A pure function in `domain/` turns that into a
ranking. Both adapters therefore rank identically, and the compliance suite
asserts it.

**Tech Stack:** Python 3.13, pydantic, asyncpg, pytest, hypothesis. No new
dependency — the tokenizer and the scorer are stdlib.

## Global Constraints

- **Do not bump the version.** Not in `pyproject.toml`, not anywhere.
- **Never hand-edit `pyproject.toml` dependency tables.** Use `uv add` /
  `uv remove`, then re-sync with `--all-extras`. No new dependency is expected
  in this work.
- **Never run ruff, bandit, lint-imports or pytest as separate pre-commit
  steps.** They are wired into `git commit`. Write, then commit; re-`git add`
  and commit again when a hook fixes something in place.
- **Anything noticed and not fixed goes in `BACKLOG.md` in the same commit**
  that passes it by, naming the file and line and what was learned.
- `BM25_K1 = 1.2` and `BM25_B = 0.75` are **module constants, not
  parameters**, for the reason `RRF_K` is not one.
- IDF is `ln(1 + (N - df + 0.5) / (df + 0.5))`. Not the unsmoothed form, and
  never a `max(0, ...)` floor over one that can go negative.
- Candidate truncation order is **matched distinct terms descending, then
  `chunk_id` ascending**. The tie-break is contract, not an implementation
  detail.
- `rank_chunks` orders by **score descending, then `chunk_id` ascending**.
- Term summation runs over **distinct terms in sorted order**, so float
  addition is deterministic and two adapters produce bit-identical scores.
- Every new port method needs its compliance cases, its mutation-isolation
  test and its tenant-isolation test **in the same task**.
- Every read method's returned objects are the caller's; mutating them cannot
  change stored state.
- Coverage may not fall. If it does, edit `.coverage-baseline` in the same
  commit and justify it in the message.

---

### Task 1: The domain tokenizer

**Files:**
- Create: `src/redstring/domain/tokenize.py`
- Test: `tests/unit/domain/test_tokenize.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `tokenize(text: str) -> list[str]`, `STOPWORDS: frozenset[str]`.
  Every later task uses `tokenize` and nothing else to decide what a term is.

- [ ] **Step 1: Write the failing tests**

```python
"""What counts as a term, and why the tokenizer is not the database's."""

from __future__ import annotations

import pytest

from redstring.domain.tokenize import STOPWORDS, tokenize


def test_splits_on_punctuation_and_casefolds() -> None:
    assert tokenize("Acme Corp, Inc.") == ["acme", "corp", "inc"]


def test_drops_stopwords() -> None:
    """`the` and `of` carry no signal and would dominate document frequency."""
    assert tokenize("the founder of the company") == ["founder", "company"]


def test_keeps_repeats_in_order() -> None:
    """The caller counts term frequency; the tokenizer must not deduplicate.

    A tokenizer returning a set would make every term frequency 1, and BM25
    over frequencies that are all 1 is indistinguishable from counting
    distinct matches -- a defect no ranking assertion elsewhere could see.
    """
    assert tokenize("data data science data") == ["data", "data", "science", "data"]


def test_keeps_digits_and_alphanumerics() -> None:
    assert tokenize("model v2 scored 0.85") == ["model", "v2", "scored", "0", "85"]


def test_normalises_compatibility_forms() -> None:
    """NFKC, so a full-width or ligature spelling is the same term.

    Without normalisation these are different terms, and a document using the
    typographic form is unfindable by a query using the ASCII one.
    """
    assert tokenize("ＡＣＭＥ") == ["acme"]
    assert tokenize("ofﬁce") == ["office"]


def test_splits_on_underscore() -> None:
    """`snake_case` is two terms, matching how a reader would search for it."""
    assert tokenize("source_id") == ["source", "id"]


def test_empty_and_punctuation_only_yield_nothing() -> None:
    assert tokenize("") == []
    assert tokenize("--- ... ???") == []


def test_a_query_of_only_stopwords_yields_nothing() -> None:
    """The caller must be able to see 'this query has no terms'."""
    assert tokenize("the and of") == []


def test_stopwords_are_stored_casefolded() -> None:
    """Guard the guard: a stopword with a capital would never match a token.

    Tokens are casefolded before the stopword test, so an entry like `"The"`
    sits in the set doing nothing while `test_drops_stopwords` still passes on
    the entries that happen to be lowercase.
    """
    assert all(word == word.casefold() for word in STOPWORDS)


def test_stopwords_are_not_empty() -> None:
    """A detector that finds nothing passes vacuously."""
    assert len(STOPWORDS) > 10


@pytest.mark.parametrize("word", ["the", "and", "of", "a", "is"])
def test_common_english_function_words_are_stopwords(word: str) -> None:
    assert word in STOPWORDS
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/domain/test_tokenize.py -q`
Expected: FAIL — `ModuleNotFoundError: redstring.domain.tokenize`

- [ ] **Step 3: Implement**

```python
"""What counts as a term, decided here and nowhere else.

## Why this is not the database's tokenizer

Postgres has a perfectly good `english` text search configuration, and using
it would be the obvious implementation. It is rejected because the in-memory
adapter cannot run it: the two stores would then disagree about what a *term*
is, and every ranking they produced would differ no matter how pure the
scorer was. Tokenization is upstream of every number BM25 computes, so it
belongs in the one place both adapters can share.

## No stemming

"running" does not match "run", and that cost is real. A stemmer is a
language model -- English-only, and a dependency -- and two implementations of
"the Porter stemmer" differ at the edges, which is the divergence this module
exists to prevent, reintroduced. Adding one later means adding a single
domain-owned implementation, never a per-adapter one. Filed as a backlog entry
saying exactly that.
"""

from __future__ import annotations

import re
import unicodedata

#: Words dropped before they reach the index. They appear in nearly every
#: passage, so their inverse document frequency is near zero and they cost a
#: candidate scan for no ranking signal.
#:
#: Deliberately small and English-only. A large list starts discarding terms
#: that carry meaning in some corpus ("can" in a manufacturing corpus), and a
#: per-language list is the stemming argument in the module docstring again.
STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "from",
        "has",
        "have",
        "he",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "she",
        "that",
        "the",
        "they",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
    }
)

#: One or more alphanumerics. `\w` would keep the underscore, making
#: `source_id` a single term no reader would search for.
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """The terms of `text`, in order, with repeats kept.

    Repeats are the caller's business: term *frequency* is what separates
    BM25 from counting distinct matches, and a tokenizer returning a set
    would silently make every frequency 1.

    NFKC first, then casefold. The order matters -- normalising after folding
    leaves compatibility forms that fold to something else unmatched.
    """
    normalised = unicodedata.normalize("NFKC", text).casefold()
    return [token for token in _TOKEN.findall(normalised) if token not in STOPWORDS]
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/domain/test_tokenize.py -q`
Expected: PASS

- [ ] **Step 5: Prove one test can fail**

Temporarily change `_TOKEN` to `re.compile(r"\w+")` and confirm
`test_splits_on_underscore` goes red. Revert.

- [ ] **Step 6: Commit**

```bash
git add src/redstring/domain/tokenize.py tests/unit/domain/test_tokenize.py
git commit -m "Decide what a term is, in the domain and not in the database"
```

---

### Task 2: BM25, as a pure function of numbers

**Files:**
- Create: `src/redstring/domain/bm25.py`
- Test: `tests/unit/domain/test_bm25.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `CorpusStats`, `BM25_K1`, `BM25_B`,
  `inverse_document_frequency(term: str, stats: CorpusStats) -> float`,
  `bm25_score(terms: Sequence[str], term_frequencies: Mapping[str, int],
  doc_length: int, stats: CorpusStats) -> float`.

- [ ] **Step 1: Write the failing tests**

```python
"""BM25's arithmetic, at the boundaries that decide whether it is BM25."""

from __future__ import annotations

import math

import pytest

from redstring.domain.bm25 import (
    BM25_B,
    BM25_K1,
    CorpusStats,
    bm25_score,
    inverse_document_frequency,
)


def stats(*, n_docs: int = 10, avg: float = 20.0, **df: int) -> CorpusStats:
    return CorpusStats(n_docs=n_docs, avg_doc_length=avg, doc_frequencies=dict(df))


class TestInverseDocumentFrequency:
    def test_a_rare_term_outweighs_a_common_one(self) -> None:
        corpus = stats(n_docs=100, rare=1, common=90)
        assert inverse_document_frequency("rare", corpus) > inverse_document_frequency(
            "common", corpus
        )

    def test_a_term_in_every_document_is_still_positive(self) -> None:
        """The unsmoothed form goes negative here; the chosen form cannot.

        A negative IDF means a document is *penalised* for containing a query
        term, which reverses the ranking of two documents that differ only in
        containing it. The usual patch is a `max(0, ...)` floor -- this is
        the assertion that says the floor is not needed rather than missing.
        """
        assert inverse_document_frequency("everywhere", stats(n_docs=10, everywhere=10)) > 0.0

    def test_the_exact_value(self) -> None:
        """Written as a literal, not as the formula under test.

        Expressing the expectation in terms of the implementation makes it
        true for any implementation, including a wrong one.
        """
        assert inverse_document_frequency("t", stats(n_docs=10, t=2)) == pytest.approx(
            math.log(1 + (10 - 2 + 0.5) / (2 + 0.5))
        )

    def test_an_unseen_term_uses_zero_document_frequency(self) -> None:
        """Absent from `doc_frequencies` means df 0, the maximum weight."""
        unseen = inverse_document_frequency("ghost", stats(n_docs=10))
        assert unseen == pytest.approx(math.log(1 + (10 - 0 + 0.5) / 0.5))

    def test_an_empty_corpus_gives_no_weight(self) -> None:
        assert inverse_document_frequency("t", stats(n_docs=0)) == 0.0


class TestScore:
    def test_a_document_matching_no_term_scores_zero(self) -> None:
        assert bm25_score(["alpha"], {}, 20, stats(alpha=3)) == 0.0

    def test_no_query_terms_scores_zero(self) -> None:
        assert bm25_score([], {"alpha": 5}, 20, stats(alpha=3)) == 0.0

    def test_more_occurrences_score_higher(self) -> None:
        corpus = stats(alpha=3)
        assert bm25_score(["alpha"], {"alpha": 5}, 20, corpus) > bm25_score(
            ["alpha"], {"alpha": 1}, 20, corpus
        )

    def test_term_frequency_saturates(self) -> None:
        """The k1 saturation is the whole difference from raw counting.

        Ten occurrences must not score ten times one. Without this, `k1` can
        be any large number -- or the saturation term dropped entirely -- and
        every other ranking assertion still passes.
        """
        corpus = stats(alpha=3)
        one = bm25_score(["alpha"], {"alpha": 1}, 20, corpus)
        ten = bm25_score(["alpha"], {"alpha": 10}, 20, corpus)
        assert ten < 10 * one

    def test_a_shorter_document_scores_higher_at_equal_frequency(self) -> None:
        """Length normalisation, which `b = 0` would remove entirely."""
        corpus = stats(avg=20.0, alpha=3)
        assert bm25_score(["alpha"], {"alpha": 2}, 5, corpus) > bm25_score(
            ["alpha"], {"alpha": 2}, 50, corpus
        )

    def test_two_matched_terms_beat_one(self) -> None:
        """A single-term query cannot tell a sum over terms from the first."""
        corpus = stats(alpha=3, beta=3)
        assert bm25_score(["alpha", "beta"], {"alpha": 2, "beta": 2}, 20, corpus) > bm25_score(
            ["alpha", "beta"], {"alpha": 2}, 20, corpus
        )

    def test_a_repeated_query_term_is_counted_once(self) -> None:
        corpus = stats(alpha=3)
        assert bm25_score(["alpha", "alpha"], {"alpha": 2}, 20, corpus) == bm25_score(
            ["alpha"], {"alpha": 2}, 20, corpus
        )

    def test_summation_order_does_not_change_the_result(self) -> None:
        """Float addition is not associative; the sum runs in sorted order.

        Two adapters returning the same terms in different orders must
        produce the *same* score, not merely a close one -- the compliance
        suite compares adapter rankings for equality.
        """
        corpus = stats(alpha=3, beta=7, gamma=1)
        frequencies = {"alpha": 2, "beta": 4, "gamma": 1}
        assert bm25_score(["gamma", "alpha", "beta"], frequencies, 20, corpus) == bm25_score(
            ["alpha", "beta", "gamma"], frequencies, 20, corpus
        )

    def test_an_empty_corpus_scores_zero(self) -> None:
        assert bm25_score(["alpha"], {"alpha": 1}, 0, stats(n_docs=0, avg=0.0)) == 0.0

    def test_a_corpus_of_empty_documents_does_not_divide_by_zero(self) -> None:
        """`avg_doc_length` of 0 means every document is empty.

        The naive expression divides by it. This must return a number.
        """
        result = bm25_score(["alpha"], {"alpha": 1}, 0, stats(n_docs=5, avg=0.0, alpha=1))
        assert result > 0.0


def test_the_constants_are_the_standard_ones() -> None:
    """Pinned so a change is a visible decision, not a silent retune."""
    assert (BM25_K1, BM25_B) == (1.2, 0.75)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/domain/test_bm25.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
"""BM25: a term-weighted ranking, as arithmetic over supplied statistics.

This module knows nothing about chunks, stores or queries. It takes numbers
and returns a number, which is what makes the ranking identical across every
adapter that can supply the numbers.

## The score is unbounded and ordinal

BM25 is not on `0..1` and never was. A score is comparable to another score
**from the same query over the same corpus** and to nothing else: it moves
with corpus size, with document frequencies, and with how many terms the
query has. `domain/retrieval.py` makes the same statement about RRF and for
the same reason -- a number called "score" that a caller assumes is a
similarity is a bug that never raises.

## The IDF form is the one that cannot go negative

`ln(1 + (N - df + 0.5) / (df + 0.5))` is positive for every `0 <= df <= N`.
The unsmoothed Robertson/Sparck-Jones form goes negative once a term is in
more than half the corpus, which *penalises* a document for containing a
query term and reverses the ranking of two documents differing only in it.
The usual repair is a `max(0, ...)` floor, which discards the signal instead
of weighting it. Choosing a form that cannot go negative is better than
clamping one that can.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

#: Term-frequency saturation. Standard, and a module constant rather than a
#: parameter for the reason `RRF_K` is one: exposing it invites tuning against
#: a benchmark this repository does not have, and a value tuned on one
#: caller's corpus is the same arbitrary number with a better story.
BM25_K1 = 1.2

#: Length-normalisation strength. `0.0` disables normalisation entirely and
#: `1.0` applies it fully; `0.75` is the standard middle.
BM25_B = 0.75


class CorpusStats(BaseModel):
    """What a scorer needs to know about the corpus behind a candidate set.

    `doc_frequencies` covers the terms that were asked for. A term absent
    from it is treated as document frequency `0` -- the maximum weight --
    which is the correct reading for a term no document contains.
    """

    #: Chunks in this tenant's corpus. `0` means an empty corpus, and every
    #: score is then `0.0` rather than undefined.
    n_docs: int = Field(ge=0)
    #: Mean chunk length in tokens. `0.0` means every chunk is empty.
    avg_doc_length: float = Field(ge=0.0)
    doc_frequencies: dict[str, int] = Field(default_factory=dict)


def inverse_document_frequency(term: str, stats: CorpusStats) -> float:
    """How much a match on `term` is worth. See the module docstring."""
    if stats.n_docs == 0:
        return 0.0
    df = stats.doc_frequencies.get(term, 0)
    return math.log(1 + (stats.n_docs - df + 0.5) / (df + 0.5))


def bm25_score(
    terms: Sequence[str],
    term_frequencies: Mapping[str, int],
    doc_length: int,
    stats: CorpusStats,
) -> float:
    """Score one document against `terms`.

    Summed over **distinct terms in sorted order**. Distinct, because a query
    repeating a term does not make the document twice as relevant; sorted,
    because float addition is not associative and two adapters supplying the
    same terms in different orders must produce the *same* score -- the
    compliance suite compares their rankings for equality, not approximate
    equality.
    """
    if stats.n_docs == 0:
        return 0.0

    # An all-empty corpus has no length to normalise against. Every document
    # is then average, so the ratio is 0 and normalisation is the identity.
    ratio = 0.0 if stats.avg_doc_length == 0 else doc_length / stats.avg_doc_length

    total = 0.0
    for term in sorted(set(terms)):
        frequency = term_frequencies.get(term, 0)
        if frequency == 0:
            continue
        saturation = frequency * (BM25_K1 + 1)
        normalisation = frequency + BM25_K1 * (1 - BM25_B + BM25_B * ratio)
        total += inverse_document_frequency(term, stats) * saturation / normalisation
    return total
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/domain/test_bm25.py -q`
Expected: PASS

- [ ] **Step 5: Prove the suite bites**

One at a time, plant each defect, confirm the named test goes red, revert:
- `BM25_B = 0.0` → `test_a_shorter_document_scores_higher_at_equal_frequency`
- drop the `saturation`/`normalisation` split for a bare `frequency` →
  `test_term_frequency_saturates`
- `sorted(set(terms))` → `terms` → `test_a_repeated_query_term_is_counted_once`
- unsmoothed IDF `math.log((N - df + 0.5) / (df + 0.5))` →
  `test_a_term_in_every_document_is_still_positive`

Record in the report which test caught which. A defect no test catches is a
finding, not a footnote.

- [ ] **Step 6: Commit**

```bash
git add src/redstring/domain/bm25.py tests/unit/domain/test_bm25.py
git commit -m "Score by BM25, in a form that cannot go negative"
```

---

### Task 3: The candidate types and `rank_chunks`

**Files:**
- Create: `src/redstring/domain/chunk_ranking.py`
- Test: `tests/unit/domain/test_chunk_ranking.py`

**Interfaces:**
- Consumes: `CorpusStats` and `bm25_score` from Task 2; `StoredChunk` from
  `redstring.domain.chunk`.
- Produces: `LexicalCandidate`, `LexicalCandidates`, `RankedChunk`,
  `rank_chunks(terms, candidates, k) -> list[RankedChunk]`. Task 4's port
  method returns `LexicalCandidates`; Tasks 6 and 7 build them.

- [ ] **Step 1: Write the failing tests**

```python
"""Turning candidates into a ranking, and what the ordering guarantees."""

from __future__ import annotations

import uuid

import pytest

from redstring.domain.bm25 import CorpusStats
from redstring.domain.chunk import StoredChunk, chunk_id
from redstring.domain.chunk_ranking import (
    LexicalCandidate,
    LexicalCandidates,
    rank_chunks,
)

TENANT = uuid.uuid4()
SOURCE = "doc-1"


def chunk(text: str, index: int = 0) -> StoredChunk:
    return StoredChunk(
        id=chunk_id(SOURCE, text),
        tenant_id=TENANT,
        source_id=SOURCE,
        text=text,
        chunk_index=index,
        start_char=0,
        end_char=len(text),
    )


def candidate(text: str, doc_length: int, **frequencies: int) -> LexicalCandidate:
    return LexicalCandidate(
        chunk=chunk(text), doc_length=doc_length, term_frequencies=dict(frequencies)
    )


def bundle(*candidates: LexicalCandidate, n_docs: int = 10, avg: float = 20.0, **df: int):
    return LexicalCandidates(
        stats=CorpusStats(n_docs=n_docs, avg_doc_length=avg, doc_frequencies=dict(df)),
        candidates=list(candidates),
    )


def test_ranks_by_score_descending() -> None:
    """Two query terms, and the two candidates differ on both counts.

    A single-term query would not distinguish a sum over terms from the
    first term alone, and equal document lengths would not distinguish
    length normalisation from none.
    """
    weak = candidate("weak", doc_length=50, alpha=1)
    strong = candidate("strong", doc_length=10, alpha=4, beta=3)
    ranked = rank_chunks(["alpha", "beta"], bundle(weak, strong, alpha=3, beta=2), k=5)
    assert [result.chunk.text for result in ranked] == ["strong", "weak"]
    assert ranked[0].score > ranked[1].score


def test_ties_break_on_chunk_id_ascending() -> None:
    """Equal scores must not order by arrival, or two adapters disagree.

    The two candidates are given identical statistics, so nothing but the
    tie-break can decide -- and they are passed in the order that makes a
    'preserve input order' implementation produce the wrong answer.
    """
    first = candidate("aaa", doc_length=20, alpha=2)
    second = candidate("bbb", doc_length=20, alpha=2)
    high, low = sorted([first, second], key=lambda c: c.chunk.id, reverse=True)
    ranked = rank_chunks(["alpha"], bundle(high, low, alpha=3), k=5)
    assert [result.chunk.id for result in ranked] == sorted([first.chunk.id, second.chunk.id])


def test_truncates_to_k() -> None:
    """`k` is 2 and there are 4 candidates, so `k` cannot be confused with
    the candidate count or with the length of the input."""
    candidates = [candidate(f"chunk {n}", doc_length=20, alpha=n + 1) for n in range(4)]
    assert len(rank_chunks(["alpha"], bundle(*candidates, alpha=3), k=2)) == 2


def test_a_candidate_matching_nothing_is_dropped_not_ranked_zero() -> None:
    """A zero-scoring candidate is not a result; it is a non-match.

    Returning it fills `k` with passages that do not contain a query term,
    which reads to a caller as the ranker being bad rather than as the
    candidate set being generous.
    """
    ranked = rank_chunks(
        ["alpha"], bundle(candidate("nothing", doc_length=20, beta=9), alpha=3), k=5
    )
    assert ranked == []


def test_no_candidates_yields_nothing() -> None:
    assert rank_chunks(["alpha"], bundle(alpha=3), k=5) == []


def test_no_terms_yields_nothing() -> None:
    assert rank_chunks([], bundle(candidate("x", doc_length=20, alpha=2)), k=5) == []


def test_k_of_zero_is_legal_and_empty() -> None:
    """Pinned as an example: a property drawing k from a range may or may not
    sample the boundary, and whether it does decides the mutation result."""
    assert rank_chunks(["alpha"], bundle(candidate("x", 20, alpha=2), alpha=3), k=0) == []


def test_a_negative_k_is_rejected() -> None:
    with pytest.raises(ValueError, match="k"):
        rank_chunks(["alpha"], bundle(), k=-1)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/domain/test_chunk_ranking.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
"""What a store hands back for ranking, and the ranking itself.

## The candidate carries the whole chunk

An id would mean a second round trip per query to fetch the passages that
ranked, and every field of the chunk is wanted by whoever is going to rank
it. The cost is a wider row over the wire for candidates that will be cut.

## `doc_length` and `term_frequencies` are derived, so they are not on
## `StoredChunk`

Both are functions of `text` through `domain.tokenize`. Storing them on the
domain type would create a second place the truth lives and a way for the two
to disagree; carrying them beside the chunk, only where ranking needs them,
cannot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from redstring.domain.bm25 import CorpusStats, bm25_score
from redstring.domain.chunk import StoredChunk

if TYPE_CHECKING:
    from collections.abc import Sequence


class LexicalCandidate(BaseModel):
    """One chunk a store offers for ranking, with the numbers to rank it."""

    chunk: StoredChunk
    #: Total tokens in the chunk, repeats included.
    doc_length: int = Field(ge=0)
    #: Occurrences of each *requested* term. Terms the chunk does not contain
    #: may be absent or present as `0`; the scorer treats both as no match.
    term_frequencies: dict[str, int] = Field(default_factory=dict)


class LexicalCandidates(BaseModel):
    """A store's answer to "which chunks contain these terms"."""

    stats: CorpusStats
    candidates: list[LexicalCandidate] = Field(default_factory=list)


class RankedChunk(BaseModel):
    """One chunk a ranking returned, with its BM25 score.

    The score is **unbounded above and ordinal**: comparable within one
    result set and meaningless across queries or corpora. See
    `domain/bm25.py`.
    """

    chunk: StoredChunk
    score: float


def rank_chunks(
    terms: Sequence[str],
    candidates: LexicalCandidates,
    k: int,
) -> list[RankedChunk]:
    """The best `k` candidates for `terms`, best first.

    Ordered by score descending, ties broken by `chunk.id` ascending. The
    tie-break is not a nicety: without it two stores offering the same
    candidates in different orders return different results, which is
    precisely the divergence putting the scorer in the domain removes.

    Candidates scoring zero are **dropped rather than returned**. A zero score
    means the chunk contains no requested term, and padding `k` with
    non-matches reads as a bad ranker rather than as a generous candidate set.
    """
    if k < 0:
        raise ValueError(f"k must not be negative, got {k}")

    scored = [
        RankedChunk(
            chunk=candidate.chunk,
            score=bm25_score(
                terms, candidate.term_frequencies, candidate.doc_length, candidates.stats
            ),
        )
        for candidate in candidates.candidates
    ]
    ranked = [result for result in scored if result.score > 0.0]
    ranked.sort(key=lambda result: (-result.score, result.chunk.id))
    return ranked[:k]
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/domain/test_chunk_ranking.py -q`
Expected: PASS

- [ ] **Step 5: Prove the suite bites**

Plant each, confirm the named test goes red, revert:
- drop `, result.chunk.id` from the sort key → `test_ties_break_on_chunk_id_ascending`
  (**if it stays green the test is wrong, not the code** — report it)
- `if k < 0` → `if k < 1` → `test_k_of_zero_is_legal_and_empty`
- keep zero-scoring results → `test_a_candidate_matching_nothing_is_dropped_not_ranked_zero`

- [ ] **Step 6: Commit**

```bash
git add src/redstring/domain/chunk_ranking.py tests/unit/domain/test_chunk_ranking.py
git commit -m "Rank candidates by score then id, and drop the non-matches"
```

---

### Task 4: The two port methods

**Files:**
- Modify: `src/redstring/ports/chunk_store.py`

**Interfaces:**
- Consumes: `LexicalCandidates` from Task 3.
- Produces: `ChunkStore.lexical_candidates` and `ChunkStore.get_by_entity`.
  Tasks 5, 6 and 7 implement and gate them.

This task changes no behaviour — it declares the contract the next three
tasks are judged against. There is no test of its own; Task 5 is its test.

- [ ] **Step 1: Add the methods**

Add to the `ChunkStore` Protocol, with `EntityId` and `LexicalCandidates`
added to the `TYPE_CHECKING` imports:

```python
async def lexical_candidates(
    self,
    terms: Sequence[str],
    tenant_id: TenantId,
    limit: int,
) -> LexicalCandidates:
    """Chunks containing any of `terms`, with the statistics to rank them.

    Takes **terms and not a query string**: tokenization is a domain
    decision (`domain/tokenize.py` says why), and a string argument here
    would hand it back to each adapter and let two stores disagree about
    what a term is.

    `stats.doc_frequencies` covers **exactly** `terms`. A term no chunk
    contains appears with `0` rather than being omitted -- an absent key
    and a zero are different facts, and a scorer that has to guess which
    it received is a scorer with a latent bug.

    `stats.n_docs` and `stats.avg_doc_length` describe the tenant's whole
    corpus, not the candidate set. Statistics computed over the survivors
    of a truncation are statistics of a corpus that does not exist.

    **Which candidates survive `limit` is contract, not discretion.**
    Ordered by the number of distinct requested terms the chunk contains,
    descending, then by `id` ascending; the first `limit` are returned.
    Without the tie-break two adapters cut different chunks from an
    equally-matching pair, which is a divergence in results.

    The cost is bounded recall, and it is real: a chunk matching one rare
    and highly informative term can be cut before a chunk matching two
    common ones, so a passage that would have ranked first can be absent
    entirely. This is the same shape as the blocking-bounded recall of the
    entity lexical channel, and it is stated in the caller's documentation
    for the same reason -- a missing result reads as a bug rather than as
    a declared limit.

    Empty `terms` returns no candidates and zeroed statistics without
    touching the store. `limit` of `0` returns no candidates but **still
    populates the statistics**. A negative `limit` raises `ValueError`.

    The returned chunks are the caller's; mutating them cannot change
    stored state.
    """
    ...


async def get_by_entity(self, entity_id: EntityId, tenant_id: TenantId) -> list[StoredChunk]:
    """This tenant's chunks whose `entity_ids` contain `entity_id`.

    A plain read and deliberately not a filter on the ranked path.
    "Which passages mention this entity" is graph navigation, not
    relevance, and folding it into a search signature makes one method
    answer two questions under one `k` -- so a caller asking for the top
    five passages about a topic that mention Ada gets neither question
    answered well.

    Ordered by `source_id`, then `chunk_index`, then `id` ascending: a
    total order, so two adapters cannot disagree. An unknown entity
    yields `[]`. The returned chunks are the caller's.
    """
    ...
```

Also extend the module docstring's "There is no search method" section: it is
now "There is a candidate method and no ranked one", carrying the reason —
ranking is a domain rule, the adapter owns recall and statistics.

- [ ] **Step 2: Commit**

```bash
git add src/redstring/ports/chunk_store.py
git commit -m "Declare the candidate and entity reads on ChunkStore"
```

---

### Task 5: The compliance suite, the gate, and the reference adapter

**Files:**
- Modify: `src/redstring/testing/chunk_store.py`
- Modify: `tests/unit/chunks/test_compliance_coverage.py`
- Modify: `src/redstring/chunks/adapters/memory.py`

**Interfaces:**
- Consumes: the port from Task 4, `tokenize` (Task 1), the candidate types
  (Task 3).
- Produces: the cases Task 6 must pass, and the reference implementation Task 6 is compared against.

**The contract and its reference implementation land together, in one
commit.** Splitting them would put a deliberately-red commit on the branch —
`--no-verify` to get it past the gate, and a bisect that lands on it reports a
failure that is not the one being looked for. Write the cases first and watch
them fail (Step 2), then implement; commit once at the end.

**The gate needs a real change, not just a new expected value.**
`read_methods()` finds methods whose return annotation *mentions*
`StoredChunk`. `get_by_entity` returns `list[StoredChunk]` and is found
automatically. **`lexical_candidates` returns `LexicalCandidates`, which
contains chunks without mentioning the type in its annotation, so the gate
would silently skip it** — exactly the omission this module exists to catch,
appearing in the module that catches it. Add `LexicalCandidates` to the
target set and to `_PORT_NAMESPACE`, and update the self-guard.

- [ ] **Step 1: Update the gate**

```python
    def test_the_port_has_read_methods_to_check(self) -> None:
        assert read_methods() == {
            "get",
            "get_by_source",
            "get_by_entity",
            "lexical_candidates",
        }
```

with `LexicalCandidates` added to `_PORT_NAMESPACE` and to the target set in
`read_methods()`, and a comment recording why a wrapper type has to be named
explicitly: a return type that *contains* domain objects leaks exactly as one
that *is* a domain object, and the annotation cannot see through it.

- [ ] **Step 2: Run the gate to watch it fail**

Run: `uv run pytest tests/unit/chunks/test_compliance_coverage.py -q`
Expected: FAIL, naming the four missing tests
(`test_lexical_candidates_returns_copies`, `..._never_crosses_tenants`, and
the two for `get_by_entity`). **If it fails naming only two, the target set
edit did not take** — stop and fix that first.

- [ ] **Step 3: Add the compliance cases**

Add to `ChunkStoreCompliance`, in the file's existing style. A corpus helper
first — every ranking case needs document frequencies that *differ across
terms*, so a one-chunk corpus is never enough:

```python
    async def _corpus(self, store, tenant):
        """Four chunks whose term statistics genuinely differ.

        `common` is in every chunk, `rare` in one, so IDF has something to
        distinguish. Lengths differ, so length normalisation has something to
        do. Two chunks match the same number of query terms, so the
        truncation tie-break has something to decide.
        """
```

Cases required:

- `test_lexical_candidates_finds_chunks_containing_a_term`
- `test_lexical_candidates_reports_term_frequencies` — a chunk repeating a
  term reports the count, not `1`
- `test_lexical_candidates_reports_doc_length_in_tokens` — a chunk whose text
  contains stopwords reports the *post-tokenization* length, so a store
  counting words or characters fails
- `test_lexical_candidates_reports_corpus_wide_statistics` — `n_docs` and
  `avg_doc_length` describe the whole corpus, asserted with a `limit` that
  truncates, so a store computing them over the survivors fails
- `test_lexical_candidates_reports_zero_for_an_absent_term` — the key is
  present with value `0`
- `test_lexical_candidates_covers_exactly_the_requested_terms` —
  `doc_frequencies.keys() == set(terms)`
- `test_lexical_candidates_truncates_by_match_count_then_id` — the ordering
  contract; the corpus must contain chunks matching *different* numbers of
  terms **and** a pair matching the same number
- `test_lexical_candidates_with_an_empty_term_list_returns_nothing`
- `test_lexical_candidates_with_a_zero_limit_still_reports_statistics`
- `test_lexical_candidates_rejects_a_negative_limit` — `pytest.raises(ValueError)`
- `test_lexical_candidates_returns_copies`
- `test_lexical_candidates_never_crosses_tenants` — **two tenants holding the
  same content-addressed id**, per this repository's standing rule about
  composite keys; ids built by `chunk_id` collide across tenants naturally,
  so this is the case that catches a key compared on `id` alone
- `test_get_by_entity_finds_chunks_mentioning_the_entity`
- `test_get_by_entity_orders_by_source_then_index_then_id` — chunks from two
  sources, with an index-10 case, so a text-typed index column fails here as
  it does for `get_by_source`
- `test_get_by_entity_ignores_other_entities`
- `test_get_by_entity_of_an_unknown_entity_is_empty`
- `test_get_by_entity_returns_copies`
- `test_get_by_entity_never_crosses_tenants` — same-id collision again

And the property this whole design exists for, as a compliance case so both
adapters run it:

- `test_ranking_is_identical_to_the_reference_adapter` — build the same
  corpus in an `InMemoryChunkStore`, run `lexical_candidates` + `rank_chunks`
  on both, and assert the returned `(id, score)` sequences are **equal**, not
  approximately equal. On the in-memory adapter this compares it with itself
  and is trivially true; that is fine and is not why it exists. It exists so
  that every *other* adapter is held to the reference, and it must use a
  corpus where scores actually differ, or it passes on a store that returns
  everything at score zero.

- [ ] **Step 4: Run — expect failure, since the adapter has no such methods**

Run: `uv run pytest tests/unit/chunks -q`
Expected: FAIL with `AttributeError` on the new methods. Confirm every new
case is represented in the failure list — a case that "passes" here is a case
that asserts nothing.

**Derive terms at query time rather than storing an index.** Tokenization is
deterministic and ids are content-addressed, so a chunk's terms are a pure
function of data already held; a stored index would be a second copy that can
drift. Postgres stores one only because it needs an index to seek on, and a
comment should say so — the two adapters must be seen to compute the *same*
thing by different means, or the identical-ranking property looks accidental.

- [ ] **Step 5: Implement `get_by_entity`**

```python
async def get_by_entity(self, entity_id: EntityId, tenant_id: TenantId) -> list[StoredChunk]:
    found = [
        chunk for chunk in self._chunks.get(tenant_id, {}).values() if entity_id in chunk.entity_ids
    ]
    # A total order: source, then index, then id. Two of the three are
    # not unique on their own, and the port's contract is all three.
    found.sort(key=lambda chunk: (chunk.source_id, chunk.chunk_index, chunk.id))
    return [chunk.model_copy(deep=True) for chunk in found]
```

- [ ] **Step 6: Implement `lexical_candidates`**

Compute in this order, and note why in comments:

1. `if limit < 0: raise ValueError`. Before anything else — a rejected call
   must not have counted a corpus.
2. Tokenize every chunk of the tenant once into `Counter` plus a length.
3. `n_docs` = the tenant's chunk count; `avg_doc_length` = mean length, `0.0`
   for an empty corpus.
4. `doc_frequencies` = for **each requested term**, how many chunks contain
   it — including the zeros. Build from the requested terms, never from the
   corpus's own terms, or absent terms go missing from the mapping.
5. Empty `terms` → return zeroed stats and no candidates without scanning.
6. Candidates = chunks matching at least one requested term, sorted by
   `(-matched_distinct_terms, chunk.id)`, truncated to `limit`, each carrying
   a deep copy of the chunk.

Statistics are computed over the **whole corpus, before truncation**.

- [ ] **Step 7: Run the compliance suite**

Run: `uv run pytest tests/unit/chunks -q`
Expected: PASS

- [ ] **Step 8: Prove the suite bites**

Plant each, confirm a named compliance case goes red, revert:
- sort candidates by `chunk.id` alone → the truncation-order case
- compute `avg_doc_length` over the candidates rather than the corpus → the
  corpus-statistics case
- omit zero-frequency terms from `doc_frequencies` → the absent-term case
- return the stored object rather than a copy → the isolation case
- key the tenant lookup on `chunk.id` alone → the cross-tenant case

**Any that stays green is a finding.** Report it rather than reverting
quietly.

- [ ] **Step 9: Commit**

```bash
git add src/redstring/testing/chunk_store.py tests/unit/chunks/ src/redstring/chunks/adapters/memory.py
git commit -m "Gate the candidate and entity reads, and answer them in the reference adapter"
```

---

### Task 6: The Postgres adapter

**Files:**
- Modify: `src/redstring/chunks/adapters/postgres.py`
- Test: `tests/unit/chunks/test_postgres_schema.py` (extend if present)

**Interfaces:**
- Consumes: everything above.
- Produces: the second adapter the identical-ranking case compares.

**Schema.** A `<table>_terms` table and a `doc_length` column on the chunk
row:

```sql
CREATE TABLE IF NOT EXISTS <table>_terms (
  tenant_id uuid    NOT NULL,
  chunk_id  text    NOT NULL,
  term      text    NOT NULL,
  tf        integer NOT NULL,
  PRIMARY KEY (tenant_id, chunk_id, term),
  FOREIGN KEY (tenant_id, chunk_id) REFERENCES <table> (tenant_id, id) ON DELETE CASCADE
)
CREATE INDEX IF NOT EXISTS <table>_terms_term_idx ON <table>_terms (tenant_id, term)
```

**`ON DELETE CASCADE` is doing real work and must be commented as such.** It
is what makes `replace_source`'s orphan delete, `delete_by_source` and
`delete_by_tenant` maintain the term index without any of them mentioning it
— three delete paths that would each otherwise need a second statement, and
each of which would be one edit away from forgetting it.

**The term index is immutable per id, so writes are `ON CONFLICT DO
NOTHING`.** Ids are content-addressed over `(source_id, text)`, so a given id
always has the same text and therefore always the same terms and the same
`doc_length`. There is no update path, and this is a property of B1's identity
decision rather than a convenience — say so in a comment, because the obvious
"delete the old terms then insert" is both unnecessary and unsafe in one
statement (a row deleted and re-inserted in the same statement is a
same-statement double modification).

`doc_length` on the chunk row is likewise immutable per id and is included in
`_INCOMING` and in the insert column list, but **not** in `_ON_CONFLICT`,
alongside a comment saying why the omission is deliberate — an omitted column
in that list is otherwise this file's own documented defect shape.

- [ ] **Step 1: Extend the schema statements and the write payloads**

Terms are computed in Python with `tokenize` and travel as a second `jsonb`
parameter of `(chunk_id, term, tf)` rows. Both `upsert_many` and
`replace_source` gain a term-insert CTE. `replace_source` stays **one
statement**.

- [ ] **Step 2: Implement `get_by_entity`**

```sql
SELECT <columns> FROM <table>
 WHERE tenant_id = $1 AND $2 = ANY (entity_ids)
 ORDER BY source_id ASC, chunk_index ASC, id ASC
```

Add a supporting index on `(tenant_id)` with a GIN index on `entity_ids`:
`CREATE INDEX ... USING gin (entity_ids)`.

- [ ] **Step 3: Implement `lexical_candidates`**

Three queries in one connection:

```sql
-- corpus statistics
SELECT count(*) AS n_docs, coalesce(avg(doc_length), 0) AS avg_len
  FROM <table> WHERE tenant_id = $1

-- document frequencies, one indexed count per requested term
SELECT term, count(*) AS df FROM <table>_terms
 WHERE tenant_id = $1 AND term = ANY ($2) GROUP BY term

-- candidates
WITH matched AS (
  SELECT chunk_id, count(*) AS matched_terms, jsonb_object_agg(term, tf) AS tfs
    FROM <table>_terms
   WHERE tenant_id = $1 AND term = ANY ($2)
   GROUP BY chunk_id
   ORDER BY matched_terms DESC, chunk_id ASC
   LIMIT $3
)
SELECT c.<columns>, c.doc_length, m.tfs
  FROM matched m JOIN <table> c ON c.tenant_id = $1 AND c.id = m.chunk_id
```

Zero-frequency terms are filled in Python from the requested list — the
`GROUP BY` cannot produce a row for a term no chunk has.

Guard `limit < 0` before any query, and short-circuit empty `terms` to zeroed
statistics without a round trip, matching the in-memory adapter.

- [ ] **Step 4: Run the unit suite**

Run: `uv run pytest tests/unit/chunks -q`
Expected: PASS (the server-free schema tests; the compliance suite runs
against Postgres in Task 7).

- [ ] **Step 5: Commit**

```bash
git add src/redstring/chunks/adapters/postgres.py tests/unit/chunks/
git commit -m "Seek candidates and entity mentions in Postgres, cascading the term index"
```

---

### Task 7: Prove it against a real server

**Files:**
- Modify: `tests/integration/chunks/` (follow the existing module's shape)

**Interfaces:** consumes Task 6.

The compliance suite is only evidence if it runs against the second adapter
and can be seen to fail there.

- [ ] **Step 1: Run the compliance suite against Postgres**

Run: `uv run pytest tests/integration/chunks -q -m integration`
Expected: PASS.

**The integration harness caches its schema in a module-level global and its
DDL is `CREATE TABLE IF NOT EXISTS`.** This task adds a column and a table, so
a stale worker table will silently not have them. Confirm the fixture's
existing `information_schema.columns` comparison covers `doc_length` and the
new table; if it does not, extend it — a schema change the harness cannot see
is a green suite proving nothing.

- [ ] **Step 2: Plant deliberate schema and query defects**

One at a time against the real server, confirming a compliance case goes red:
- `doc_length` typed `text` → the doc-length case
- drop `ON DELETE CASCADE` → a case where a re-chunk leaves stale terms
  behind (**write one if none exists**: replace a source, then assert the
  orphaned passage is not a candidate)
- drop `, chunk_id ASC` from the `matched` ordering → the truncation case.
  If it stays green the covering index is supplying the order — re-run with
  `SET LOCAL enable_indexscan = off; enable_bitmapscan = off` and the plan
  asserted, as the existing `ORDER BY id` test does, so an inert `SET` cannot
  pass silently.
- compute `avg_doc_length` from the `matched` CTE → the corpus-statistics case

- [ ] **Step 3: Record the results and commit**

Report which defect each case caught. Any defect nothing catches is a finding.

```bash
git add tests/integration/chunks/
git commit -m "Run the chunk compliance suite against Postgres, with the term index"
```

---

### Task 8: The public surface

**Files:**
- Modify: `src/redstring/__init__.py`
- Create: `docs/how-to/rank-passages.md`
- Modify: `mkdocs.yml`

**Interfaces:** consumes everything above.

- [ ] **Step 1: Export the closure**

Add to `__all__`: `LexicalCandidate`, `LexicalCandidates`, `RankedChunk`,
`CorpusStats`, `rank_chunks`, `tokenize`. **Follow the closure** — the
signature gate will name anything missing, and that is what it is for. Run
the gate and let it tell you rather than guessing.

- [ ] **Step 2: Write the how-to**

`docs/how-to/rank-passages.md`, importing **nothing but `redstring`** (the
third public-surface gate). It must state the bounded-recall cost of `limit`
in the caller's own documentation, not only in the ADR, for the reason ADR
0022's blocking limit is stated in `docs/how-to/retrieve-entities.md`: a
missing result reads as a bug rather than as a declared limit.

- [ ] **Step 3: Run the gates**

Run: `uv run pytest tests/unit/test_public_api.py -q` (or the module holding
the three surface gates) and `uv run mkdocs build --strict`
Expected: PASS, exit 0.

- [ ] **Step 4: Commit**

```bash
git add src/redstring/__init__.py docs/how-to/rank-passages.md mkdocs.yml
git commit -m "Export the ranking closure and document what limit costs"
```

---

### Task 9: ADR 0024, and the amendment to 0022

**Files:**
- Create: `docs/adr/0024-bm25-over-the-chunk-corpus.md`
- Modify: `docs/adr/0022-the-lexical-channel-is-not-bm25.md` (Status only)
- Modify: `BACKLOG.md`
- Modify: `mkdocs.yml`

- [ ] **Step 1: Write ADR 0024**

Cover, with the reasoning and not only the conclusion:

- **Scoring is in `domain/`, recall and statistics in the adapters.** The
  rejected alternative is `ts_rank_cd`, and the reason is that two adapters
  ranking by different formulas make the compliance suite unable to assert
  they agree — the gate that has caught every divergence in this repository.
  ADR 0012 refused the same trade for the semantic channel.
- **The tokenizer is domain-owned**, or the purity of the scorer buys nothing.
- **No stemming**, with the cost stated.
- **Truncation is a stated total order**, and its cost is bounded recall.
- **`ON DELETE CASCADE` maintains the term index**, and content addressing is
  what makes the index immutable per id.
- 0022's *"the name BM25 appears nowhere under `src/`"* **dies here, and
  loudly**: this is the first time the name is honest in this codebase, being
  a real term-weighted ranker over a real corpus of documents. 0022's decision
  about the *entity* lexical channel is untouched — still a field-weighted
  string similarity, still not to be called BM25, and this ranker does not
  replace the thing that catches `Acme Corp`.
- 0023 **stands**: its "no search method" was an argument about *timing*
  ("every decision a search method would encode is downstream of what a stored
  passage is"), and the corpus now exists, so the condition it named is met
  rather than overridden.

- [ ] **Step 2: Amend 0022's Status**

Add the second amendment pointer. **Body untouched** — an ADR records what was
decided when it was decided.

- [ ] **Step 3: Backlog**

Delete B89's B2a portion; leave what B2b still owes and say B2a landed. Add:

- **Stemming**, with the reasoning from Task 1: one domain-owned
  implementation, never per-adapter, and it changes stored term indexes so it
  needs a re-index path.
- **Maintained corpus counters** if `count(*)`/`avg()` per query becomes the
  cost centre, and the note that they were deliberately not built
  speculatively.
- Anything the implementers deferred.

- [ ] **Step 4: Commit**

```bash
git add docs/adr/ BACKLOG.md mkdocs.yml
git commit -m "Record why the ranker is in the domain and the tokenizer is ours"
```
