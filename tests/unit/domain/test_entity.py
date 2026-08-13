"""Tests for redstring.domain.entity.

The rules about `confidence`, `model` and `extraction_method` are no longer
asserted here: those fields moved to `Provenance`, and their tests moved with
them to `test_provenance.py` rather than being restated against a nested
attribute. What stays is what `Entity` itself still decides.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from redstring.domain.entity import Entity
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.temporal import TemporalExtent

#: A fixed observation instant. Never `datetime.now(UTC)`: a fixture that
#: varies per run makes any comparison on `observed_at` non-deterministic.
OBSERVED = datetime(2026, 2, 12, 11, 7, tzinfo=UTC)


def _entity(**overrides):
    fields = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "name": "Ada Lovelace",
        "normalized_name": "ada lovelace",
        "entity_type": "person",
        "provenance": Provenance(
            observed_at=OBSERVED,
            extraction_method=ExtractionMethod.LLM,
            confidence=0.9,
        ),
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


@pytest.mark.parametrize(
    "field",
    ["source_id", "source_text", "extraction_method", "model", "confidence"],
)
def test_the_five_provenance_fields_are_gone_from_entity(field):
    """A clean break, asserted rather than assumed.

    Without this, a forwarding property added later for one caller's
    convenience would restore `entity.confidence` as a second way to reach
    one value -- and every test in the tree would keep passing while the two
    ways silently became a place for them to disagree.
    """
    assert field not in Entity.model_fields
    assert not hasattr(_entity(), field)


def test_an_entity_reaches_its_provenance_through_one_attribute():
    entity = _entity()
    assert entity.provenance.extraction_method is ExtractionMethod.LLM
    assert entity.provenance.confidence == 0.9
    assert entity.provenance.observed_at == OBSERVED


def test_provenance_is_required():
    """Not defaulted. An `Entity` with no record of where it came from is the
    state `Provenance` exists to make unrepresentable."""
    with pytest.raises(ValidationError):
        Entity(
            id=uuid4(),
            tenant_id=uuid4(),
            name="Ada Lovelace",
            normalized_name="ada lovelace",
            entity_type="person",
        )


def test_model_field_documents_the_naming_convention():
    description = Provenance.model_fields["model"].description
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
    assert entity.provenance.source_id is None
    assert entity.provenance.source_text is None


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


def test_round_trip_through_model_dump():
    entity = _entity(
        description="A mathematician",
        provenance=Provenance(
            observed_at=OBSERVED,
            extraction_method=ExtractionMethod.LLM,
            confidence=0.9,
            source_id="doc-1",
            source_text="Ada Lovelace was...",
        ),
        external_ids={"wikidata": "Q7259"},
        properties={"born": 1815},
        temporal=TemporalExtent(start_date=datetime(1815, 12, 10, tzinfo=UTC)),
    )
    reconstructed = Entity.model_validate(entity.model_dump())
    assert reconstructed == entity
    # Asserted separately: `==` on the whole model would also pass if
    # `provenance` came back as a dict on both sides.
    assert isinstance(reconstructed.provenance, Provenance)
    assert reconstructed.provenance.observed_at == OBSERVED
