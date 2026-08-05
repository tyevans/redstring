"""Tests for redstring.domain.entity."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from pydantic import ValidationError

from redstring.domain.entity import Entity, ExtractionMethod
from redstring.domain.temporal import TemporalExtent


def _entity(**overrides):
    fields = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "name": "Ada Lovelace",
        "normalized_name": "ada lovelace",
        "entity_type": "person",
        "extraction_method": ExtractionMethod.LLM,
        "confidence": 0.9,
    }
    fields.update(overrides)
    return Entity(**fields)


def test_extraction_method_members():
    assert ExtractionMethod.LLM == "llm"
    assert ExtractionMethod.PATTERN == "pattern"
    assert ExtractionMethod.SCHEMA_ORG == "schema_org"
    assert ExtractionMethod.OPEN_GRAPH == "open_graph"
    assert ExtractionMethod.HYBRID == "hybrid"
    assert ExtractionMethod.MANUAL == "manual"


def test_extraction_method_names_no_vendors():
    """Vendor identity is adapter detail; the domain records only *how*."""
    assert {m.value for m in ExtractionMethod} == {
        "llm",
        "pattern",
        "schema_org",
        "open_graph",
        "hybrid",
        "manual",
    }


def test_model_defaults_to_none():
    assert _entity().model is None


def test_model_carries_llm_provenance():
    assert _entity(model="qwen3.6-27b-mtp").model == "qwen3.6-27b-mtp"


@pytest.mark.parametrize(
    "method",
    [ExtractionMethod.LLM, ExtractionMethod.HYBRID],
)
def test_model_is_allowed_for_methods_that_may_invoke_a_model(method):
    assert _entity(extraction_method=method, model="qwen3.6-27b-mtp").model is not None


@pytest.mark.parametrize(
    "method",
    [
        ExtractionMethod.PATTERN,
        ExtractionMethod.SCHEMA_ORG,
        ExtractionMethod.OPEN_GRAPH,
        ExtractionMethod.MANUAL,
    ],
)
def test_model_is_rejected_for_methods_that_cannot_invoke_one(method):
    with pytest.raises(ValidationError, match="model"):
        _entity(extraction_method=method, model="qwen3.6-27b-mtp")


@pytest.mark.parametrize("method", list(ExtractionMethod))
def test_model_may_always_be_omitted(method):
    """An LLM extraction that did not record its model is still valid."""
    assert _entity(extraction_method=method).model is None


def test_model_field_documents_the_naming_convention():
    description = Entity.model_fields["model"].description
    assert description is not None
    assert "provider" in description.lower()


def test_blocking_keys_defaults_to_none():
    assert _entity().blocking_keys is None


def test_blocking_keys_is_a_frozenset():
    entity = _entity(blocking_keys={"person:ada", "A430"})
    assert entity.blocking_keys == frozenset({"person:ada", "A430"})
    assert isinstance(entity.blocking_keys, frozenset)


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


#: Values just outside the bound, pinned rather than left to the sampler.
#:
#: `st.floats().filter(...)` reaches the far extremes readily and the
#: immediate neighbourhood of 1.0 rarely, so a mutant widening the bound to
#: `<= 2.0` survived the property test entirely. A property test is a sampler,
#: not a proof about a value.
JUST_OUTSIDE_CONFIDENCE = [-1e-9, 1.0 + 1e-9, 1.5, 2.0]


@given(st.floats(allow_nan=False, allow_infinity=False).filter(lambda f: f < 0.0 or f > 1.0))
@example(confidence=-1e-9)
@example(confidence=1.0 + 1e-9)
@example(confidence=1.5)
@example(confidence=2.0)
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
