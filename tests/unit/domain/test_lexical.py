"""Scoring an entity against a free-text query, without a text index."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from redstring.domain.entity import Entity
from redstring.domain.lexical import PROPERTY_WEIGHT, lexical_score
from redstring.domain.normalization import normalize_name
from redstring.domain.provenance import ExtractionMethod, Provenance

#: A fixed observation instant. Never `datetime.now(UTC)`: a fixture that
#: varies per run makes any comparison on `observed_at` non-deterministic.
OBSERVED = datetime(2026, 2, 14, 11, 7, tzinfo=UTC)


def _entity(
    name: str,
    normalized_name: str | None = None,
    properties: dict[str, Any] | None = None,
) -> Entity:
    return Entity(
        id=uuid4(),
        tenant_id=uuid4(),
        name=name,
        normalized_name=normalized_name if normalized_name is not None else normalize_name(name),
        entity_type="person",
        properties=properties or {},
        provenance=Provenance(
            observed_at=OBSERVED,
            extraction_method=ExtractionMethod.MANUAL,
            confidence=1.0,
        ),
    )


def test_an_exact_name_scores_one() -> None:
    assert lexical_score("Ada Lovelace", _entity(name="Ada Lovelace")) == 1.0


def test_casing_and_whitespace_are_not_differences() -> None:
    """`string_similarity` normalizes both sides; this pins that it is reached."""
    assert lexical_score("ada  LOVELACE", _entity(name="Ada Lovelace")) == 1.0


def test_a_runtime_built_query_scores_the_same_as_a_literal() -> None:
    """CPython interns literals, so a literal-only suite cannot see `is` for `==`.

    The query here is built at runtime and is a distinct object from the
    literal beside it. The distinctness is asserted through `id()` rather than
    as `built is not "Ada Lovelace"`: an `is` against a literal raises a
    `SyntaxWarning` and is the kind of line a later ruff release removes. The
    claim is identical -- `" ".join(...)` allocates a fresh object and the
    literal is a code constant, so the two are never the same object.
    """
    built = " ".join(["Ada", "Lovelace"])
    literal = "Ada Lovelace"
    assert id(built) != id(literal)
    assert lexical_score(built, _entity(name="Ada Lovelace")) == 1.0


def test_an_abbreviation_beats_an_unrelated_name() -> None:
    """The case embeddings are worst at, and the reason this channel exists."""
    acme = lexical_score("Acme Corp", _entity(name="ACME Corporation"))
    other = lexical_score("Acme Corp", _entity(name="Zebra Holdings"))
    assert acme > other


def test_a_property_can_match_but_scores_below_the_same_match_on_the_name() -> None:
    """The weighting is the claim -- a name match is stronger evidence."""
    on_name = lexical_score("Ada Lovelace", _entity(name="Ada Lovelace"))
    on_property = lexical_score(
        "Ada Lovelace",
        _entity(name="Zebra Holdings", properties={"also_known_as": "Ada Lovelace"}),
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
    entity = _entity(name="Zebra", properties={"count": 7, "tags": ["ada"], "d": {}})
    without_properties = lexical_score("ada", _entity(name="Zebra"))
    assert lexical_score("ada", entity) == pytest.approx(without_properties)


def test_normalized_name_is_scored_when_it_differs_from_the_name() -> None:
    """The extractor's own work is not discarded.

    The field is whatever the extractor wrote; it can carry a form the name
    does not.
    """
    entity = _entity(name="A. Lovelace", normalized_name="ada lovelace")
    assert lexical_score("Ada Lovelace", entity) == 1.0


def test_the_best_field_wins_rather_than_the_fields_summing() -> None:
    """Summing would let many mediocre fields outrank an exact name match.

    It would also push the result above 1.0.
    """
    entity = _entity(
        name="Ada Lovelace",
        properties={"a": "Ada Lovelace", "b": "Ada Lovelace", "c": "Ada Lovelace"},
    )
    assert lexical_score("Ada Lovelace", entity) == 1.0


def test_the_score_stays_within_zero_and_one() -> None:
    entity = _entity(name="Ada Lovelace", properties={"a": "Ada Lovelace"})
    assert 0.0 <= lexical_score("Ada Lovelace", entity) <= 1.0
