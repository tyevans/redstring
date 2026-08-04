"""Tests for kg_builder.domain.consolidation."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from kg_builder.domain.consolidation import RelationshipRedirection
from kg_builder.domain.relationship import Relationship


def _relationship(**overrides):
    fields = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "source_entity_id": uuid4(),
        "target_entity_id": uuid4(),
        "relationship_type": "works_for",
        "confidence": 0.9,
    }
    fields.update(overrides)
    return Relationship(**fields)


def test_a_redirection_carries_the_edge_before_and_after():
    before = _relationship()
    after = before.model_copy(update={"source_entity_id": uuid4()})
    redirection = RelationshipRedirection(before=before, after=after)
    assert redirection.before == before
    assert redirection.after == after


def test_a_dropped_edge_has_no_after():
    before = _relationship()
    assert RelationshipRedirection(before=before, after=None).after is None


def test_after_must_keep_the_same_relationship_id():
    before = _relationship()
    other = _relationship(tenant_id=before.tenant_id)
    with pytest.raises(ValidationError, match="same relationship"):
        RelationshipRedirection(before=before, after=other)


def test_after_must_keep_the_same_tenant():
    before = _relationship()
    after = before.model_copy(update={"tenant_id": uuid4()})
    with pytest.raises(ValidationError, match="same tenant"):
        RelationshipRedirection(before=before, after=after)
