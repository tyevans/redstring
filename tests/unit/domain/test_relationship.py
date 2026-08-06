"""Tests for redstring.domain.relationship."""

from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from redstring.domain.relationship import Relationship


def _relationship(**overrides):
    fields = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "source_entity_id": uuid4(),
        "target_entity_id": uuid4(),
        "relationship_type": "knows",
        "confidence": 0.8,
    }
    fields.update(overrides)
    return Relationship(**fields)


def test_minimal_construction():
    rel = _relationship()
    assert rel.relationship_type == "knows"
    assert rel.properties == {}


def test_provenance_is_absent_until_it_is_given():
    """The default is executed here rather than through a factory that fills
    every field: `_relationship` deliberately omits `source_id`, so this is
    the model's own default and not one restated in a helper."""
    assert _relationship().source_id is None
    assert _relationship(source_id="doc-1").source_id == "doc-1"


@pytest.mark.parametrize("bad", ["doc\x00-1", "doc\ud800-1"])
def test_provenance_cannot_carry_text_a_store_will_refuse(bad):
    """`source_id` is free-form text reaching the event log, so it is subject
    to the same rejection as every other free-form field.
    `test_text_rejection_covers_every_field.py` proves the *list* is complete;
    this proves this field's entry in it works."""
    with pytest.raises(ValidationError):
        _relationship(source_id=bad)


def test_self_loop_is_rejected():
    entity_id = uuid4()
    with pytest.raises(ValidationError):
        _relationship(source_entity_id=entity_id, target_entity_id=entity_id)


@given(st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
def test_confidence_in_range_accepted(confidence):
    rel = _relationship(confidence=confidence)
    assert rel.confidence == confidence


@given(st.floats(allow_nan=False, allow_infinity=False).filter(lambda f: f < 0.0 or f > 1.0))
def test_confidence_out_of_range_rejected(confidence):
    with pytest.raises(ValidationError):
        _relationship(confidence=confidence)


def test_round_trip_through_model_dump():
    rel = _relationship(properties={"since": 2020})
    reconstructed = Relationship.model_validate(rel.model_dump())
    assert reconstructed == rel
