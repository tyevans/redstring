"""Tests for kg_builder.domain.consolidation."""

from uuid import UUID, uuid4

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


class TestComparisonsAreByValueNotIdentity:
    """Both checks compare ids, and the tests above build `after` with
    `model_copy`, which shares the id object with `before`.

    A `!=` meaning `is not` would then pass every test above and *reject* the
    real case: a redirection reconstructed from a stored event, whose two ids
    are equal and not identical.
    """

    def test_an_after_whose_id_arrived_as_a_string_is_the_same_edge(self):
        before = _relationship()
        after = before.model_copy(
            update={
                "id": UUID(str(before.id)),
                "source_entity_id": uuid4(),
            }
        )
        assert after.id is not before.id
        RelationshipRedirection(before=before, after=after)

    def test_an_after_whose_tenant_arrived_as_a_string_is_the_same_tenant(self):
        before = _relationship()
        after = before.model_copy(update={"tenant_id": UUID(str(before.tenant_id))})
        assert after.tenant_id is not before.tenant_id
        RelationshipRedirection(before=before, after=after)
