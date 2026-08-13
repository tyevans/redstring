"""Tests for redstring.domain.consolidation."""

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from redstring.domain.consolidation import MergeableFields, RelationshipRedirection
from redstring.domain.entity import Entity
from redstring.domain.merge_strategy import MERGEABLE_FIELDS
from redstring.domain.relationship import Relationship


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


#: Two ids that bracket `PIVOT`, one sorting below it and one above.
#:
#: Pinned rather than `uuid4()`, because the checks under test are `!=` and a
#: mutant rewriting one as `<` or `>` passes a random pair about half the time
#: -- depending on how the ids happened to sort. That is the exact shape
#: CLAUDE.md records from slice 3, and a random pair is what put it there.
BELOW = UUID("00000000-0000-4000-8000-000000000001")
PIVOT = UUID("88888888-8888-4888-8888-888888888888")
ABOVE = UUID("ffffffff-ffff-4fff-bfff-ffffffffffff")


@pytest.mark.parametrize("other_id", [BELOW, ABOVE], ids=["sorts-below", "sorts-above"])
def test_after_must_keep_the_same_relationship_id(other_id):
    before = _relationship(id=PIVOT)
    other = _relationship(id=other_id, tenant_id=before.tenant_id)
    with pytest.raises(ValidationError, match="same relationship"):
        RelationshipRedirection(before=before, after=other)


@pytest.mark.parametrize("other_tenant", [BELOW, ABOVE], ids=["sorts-below", "sorts-above"])
def test_after_must_keep_the_same_tenant(other_tenant):
    """Both directions, for the reason `BELOW`/`ABOVE` exist."""
    before = _relationship(tenant_id=PIVOT)
    after = before.model_copy(update={"tenant_id": other_tenant})
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


class TestMergeableFields:
    def test_it_defaults_to_saying_nothing(self):
        fields = MergeableFields()
        assert fields.description is None
        assert fields.external_ids == {}
        assert fields.properties == {}

    def test_it_holds_exactly_the_three_mergeable_fields(self):
        """Pinned against `MERGEABLE_FIELDS`, so adding a target to one and not
        the other fails rather than silently dropping the new field from every
        event payload."""
        assert set(MergeableFields.model_fields) == MERGEABLE_FIELDS

    def test_every_mergeable_field_names_a_real_entity_field(self):
        """`MERGEABLE_FIELDS` and `MergeableFields.model_fields` can agree with
        each other while both being wrong about `Entity`: nothing above pins
        either against the type they are supposed to describe.

        This seam has no runtime guard. `GraphProjection._apply_fields` builds
        the merge payload with `fields.model_dump()` and applies it via
        `entity.model_copy(update=...)`, and `model_copy(update=...)` performs
        **no validation** -- it accepts unknown keys silently, writing them as
        stray attributes rather than raising. So renaming `Entity.external_ids`
        would leave `MergeableFields` and `MERGEABLE_FIELDS` agreeing with each
        other while every merge silently failed to touch the real field and
        instead set an attribute nothing else reads, with no test failing
        anywhere else in the suite.
        """
        assert set(Entity.model_fields) >= MERGEABLE_FIELDS
