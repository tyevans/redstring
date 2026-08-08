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

        A negative IDF means a document is *penalised* for containing a
        query term, which reverses the ranking of two documents that differ
        only in containing it. The usual patch is a `max(0, ...)` floor --
        this is the assertion that says the floor is not needed rather than
        missing.
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
        be any large number -- or the saturation term dropped entirely --
        and every other ranking assertion still passes.
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
