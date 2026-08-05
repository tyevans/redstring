"""Tests for redstring.domain.normalization."""

from hypothesis import given
from hypothesis import strategies as st

from redstring.domain.normalization import normalize_name


def test_casefolds():
    assert normalize_name("FOO") == "foo"


def test_strips_leading_trailing_whitespace():
    assert normalize_name("  foo  ") == "foo"


def test_collapses_internal_whitespace_runs():
    assert normalize_name("foo   bar") == "foo bar"


def test_leaves_hyphens_alone():
    assert normalize_name("foo-bar") == "foo-bar"


def test_leaves_underscores_alone():
    assert normalize_name("foo_bar") == "foo_bar"


@given(st.text())
def test_never_raises(name):
    normalize_name(name)


@given(st.text())
def test_idempotent(name):
    once = normalize_name(name)
    twice = normalize_name(once)
    assert once == twice


@given(st.text())
def test_no_leading_or_trailing_whitespace(name):
    result = normalize_name(name)
    assert result == result.strip()


@given(st.text())
def test_no_double_spaces(name):
    result = normalize_name(name)
    assert "  " not in result
