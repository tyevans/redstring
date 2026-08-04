"""Tests for kg_builder.domain.alias."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from kg_builder.domain.alias import Alias


def _alias(**overrides):
    fields = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "canonical_entity_id": uuid4(),
        "alias_entity_id": uuid4(),
        "alias_name": "Countess of Lovelace",
        "alias_normalized_name": "countess of lovelace",
        "merged_at": datetime(2024, 1, 1, tzinfo=UTC),
    }
    fields.update(overrides)
    return Alias(**fields)


def test_minimal_construction():
    alias = _alias()
    assert alias.merge_reason is None
    assert alias.displaced == {}


def test_self_merge_is_rejected():
    entity_id = uuid4()
    with pytest.raises(ValidationError):
        _alias(canonical_entity_id=entity_id, alias_entity_id=entity_id)


def test_naive_merged_at_is_rejected():
    with pytest.raises(ValidationError):
        _alias(merged_at=datetime(2024, 1, 1))


def test_round_trip_through_model_dump():
    alias = _alias(
        merge_reason="exact name match",
        displaced={"name": "Ada Lovelace", "confidence": 0.7},
    )
    reconstructed = Alias.model_validate(alias.model_dump())
    assert reconstructed == alias
