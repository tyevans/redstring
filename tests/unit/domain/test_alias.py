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


def test_self_merge_is_rejected():
    entity_id = uuid4()
    with pytest.raises(ValidationError):
        _alias(canonical_entity_id=entity_id, alias_entity_id=entity_id)


def test_naive_merged_at_is_rejected():
    with pytest.raises(ValidationError):
        _alias(merged_at=datetime(2024, 1, 1))


def test_round_trip_through_model_dump():
    alias = _alias(merge_reason="exact name match")
    reconstructed = Alias.model_validate(alias.model_dump())
    assert reconstructed == alias


def test_displaced_values_are_not_an_alias_field():
    """Undo is a compensating event, so an alias carries no displaced payload.

    `Alias.displaced` was a `dict[str, Any]` added when undo was a storage
    problem. `MergeUndone` now carries typed `RelationshipRedirection`s, so a
    caller passing `displaced` is working from the superseded design and is
    told so rather than having the value silently dropped -- which is what
    pydantic does by default, and what would make the stale call site look
    like it still worked.
    """
    with pytest.raises(ValidationError):
        _alias(displaced={"name": "Ada Lovelace"})
