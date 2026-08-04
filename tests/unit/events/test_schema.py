"""Properties every event in the log schema must have.

These are driven off `KG_EVENT_TYPES` by introspection rather than listed by
hand, so a new event class is held to them the moment it joins the tuple --
the same shape as `tests/unit/graph/test_compliance_coverage.py`, and for the
same reason: a rule written only in prose is the one that failed four times
in slice 3.
"""

from uuid import uuid4

import pytest

from kg_builder.events import KG_EVENT_TYPES
from kg_builder.events.streams import CONSOLIDATION_CATEGORY, DOCUMENT_CATEGORY


def test_the_schema_is_not_empty():
    """A registry-driven suite over an empty registry passes vacuously."""
    assert len(KG_EVENT_TYPES) >= 4


@pytest.mark.parametrize("event_type", KG_EVENT_TYPES, ids=lambda t: t.__name__)
def test_every_event_declares_its_schema_version_explicitly(event_type):
    """`event_version` must be declared on the class, not merely inherited.

    `DomainEvent` defaults it to 1, so an event that never mentions it looks
    versioned and is not: nobody chose the number, and nobody will think to
    bump it. Checking `__annotations__` rather than the resolved field default
    is the difference between "the value is 1" and "somebody wrote 1".
    """
    assert "event_version" in event_type.__annotations__, (
        f"{event_type.__name__} inherits event_version instead of declaring it"
    )
    assert event_type.model_fields["event_version"].default == 1


@pytest.mark.parametrize("event_type", KG_EVENT_TYPES, ids=lambda t: t.__name__)
def test_no_event_declares_its_event_type_by_hand(event_type):
    """`DomainEvent` derives `event_type` from the class name.

    Declaring it is either noise, or -- worse -- a silent decoupling in which
    the wire name and the class name drift apart and only the log knows.
    """
    assert "event_type" not in event_type.__annotations__
    assert event_type.event_type_name() == event_type.__name__


@pytest.mark.parametrize("event_type", KG_EVENT_TYPES, ids=lambda t: t.__name__)
def test_every_event_belongs_to_one_of_the_two_stream_categories(event_type):
    """There are two aggregates, so there are two categories.

    A third would be a stream nothing owns: no aggregate to enforce its
    invariants and no repository to manage its version.
    """
    assert event_type.model_fields["aggregate_type"].default in {
        DOCUMENT_CATEGORY,
        CONSOLIDATION_CATEGORY,
    }


@pytest.mark.parametrize("event_type", KG_EVENT_TYPES, ids=lambda t: t.__name__)
def test_every_event_requires_a_tenant(event_type):
    """Tenant isolation is the property this project treats as inviolable, and
    an event with an optional tenant is one `None` away from a projection
    writing rows nobody owns."""
    assert event_type.model_fields["tenant_id"].is_required()


@pytest.mark.parametrize("event_type", KG_EVENT_TYPES, ids=lambda t: t.__name__)
def test_every_event_rejects_fields_it_does_not_declare(event_type):
    """A stored event is forever, so a typo'd field must not be silently
    dropped on the way in -- it would be indistinguishable, afterwards, from
    the emitter never having set it."""
    with pytest.raises(ValueError, match=r"[Ee]xtra"):
        event_type.model_validate(
            {
                "aggregate_id": uuid4(),
                "tenant_id": uuid4(),
                "not_a_field_of_any_event": 1,
            }
        )
