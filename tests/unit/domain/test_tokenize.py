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
    assert tokenize("ＡＣＭＥ") == ["acme"]  # noqa: RUF001
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
