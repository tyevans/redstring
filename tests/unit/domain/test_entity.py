"""Tests for kg_builder.domain.entity."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from kg_builder.domain.entity import Entity, ExtractionMethod
from kg_builder.domain.temporal import TemporalExtent


def _entity(**overrides):
    fields = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "name": "Ada Lovelace",
        "normalized_name": "ada lovelace",
        "entity_type": "person",
        "extraction_method": ExtractionMethod.LLM_CLAUDE,
        "confidence": 0.9,
    }
    fields.update(overrides)
    return Entity(**fields)


def test_extraction_method_members():
    assert ExtractionMethod.SCHEMA_ORG == "schema_org"
    assert ExtractionMethod.OPEN_GRAPH == "open_graph"
    assert ExtractionMethod.LLM_CLAUDE == "llm_claude"
    assert ExtractionMethod.LLM_OLLAMA == "llm_ollama"
    assert ExtractionMethod.LLM_OPENAI == "llm_openai"
    assert ExtractionMethod.PATTERN == "pattern"
    assert ExtractionMethod.SPACY == "spacy"
    assert ExtractionMethod.HYBRID == "hybrid"


def test_minimal_entity_construction():
    entity = _entity()
    assert entity.name == "Ada Lovelace"
    assert entity.external_ids == {}
    assert entity.properties == {}
    assert entity.temporal is None
    assert entity.original_entity_type is None
    assert entity.description is None
    assert entity.source_id is None
    assert entity.source_text is None


def test_entity_type_survives_as_free_string():
    entity = _entity(entity_type="plot_point")
    assert entity.entity_type == "plot_point"


def test_blank_name_is_rejected():
    with pytest.raises(ValidationError):
        _entity(name="   ")


def test_empty_name_is_rejected():
    with pytest.raises(ValidationError):
        _entity(name="")


def test_is_temporal_false_when_none():
    assert _entity(temporal=None).is_temporal is False


def test_is_temporal_false_when_empty_extent():
    assert _entity(temporal=TemporalExtent()).is_temporal is False


def test_is_temporal_true_when_extent_populated():
    extent = TemporalExtent(start_date=datetime(2020, 1, 1, tzinfo=UTC))
    assert _entity(temporal=extent).is_temporal is True


def test_no_is_canonical_field():
    assert "is_canonical" not in Entity.model_fields


def test_no_is_alias_of_field():
    assert "is_alias_of" not in Entity.model_fields


def test_no_synced_at_field():
    assert "synced_at" not in Entity.model_fields


@given(st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
def test_confidence_in_range_accepted(confidence):
    entity = _entity(confidence=confidence)
    assert entity.confidence == confidence


@given(st.floats(allow_nan=False, allow_infinity=False).filter(lambda f: f < 0.0 or f > 1.0))
def test_confidence_out_of_range_rejected(confidence):
    with pytest.raises(ValidationError):
        _entity(confidence=confidence)


def test_round_trip_through_model_dump():
    entity = _entity(
        description="A mathematician",
        source_id="doc-1",
        source_text="Ada Lovelace was...",
        external_ids={"wikidata": "Q7259"},
        properties={"born": 1815},
        temporal=TemporalExtent(start_date=datetime(1815, 12, 10, tzinfo=UTC)),
    )
    reconstructed = Entity.model_validate(entity.model_dump())
    assert reconstructed == entity
