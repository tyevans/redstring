"""Properties every event in the log schema must have.

These are driven off `KG_EVENT_TYPES` by introspection rather than listed by
hand, so a new event class is held to them the moment it joins the tuple --
the same shape as `tests/unit/graph/test_compliance_coverage.py`, and for the
same reason: a rule written only in prose is the one that failed four times
in slice 3.
"""

import importlib
import pkgutil
from uuid import uuid4

import pytest
from eventsource.domain.event_registry import default_registry, get_event_class

from redstring.events import KG_EVENT_TYPES
from redstring.events.streams import CONSOLIDATION_CATEGORY, DOCUMENT_CATEGORY

#: The schema version of every event, by wire name, written out by hand.
#:
#: A version is a wire fact -- it is what tells an upcaster which shape a
#: stored payload has -- so it is pinned the way
#: `tests/unit/test_enum_values_are_a_wire_format.py` pins the other wire
#: facts: typed out, never derived. An expectation written as
#: `{t.__name__: t.model_fields["event_version"].default for t in ...}` is true
#: for every possible number including the wrong one, which is CLAUDE.md's row
#: about an expectation stated in terms of the thing under test.
#:
#: This replaced a blanket `default == 1`, which stopped being sayable when
#: `DocumentExtracted` went to 2 (see
#: `docs/adr/0035-provenance-is-a-value-object.md` and 0001's Consequences).
#: The half of that
#: assertion worth keeping -- an event must not merely *inherit* its version --
#: is the `__annotations__` check below, and it is what the blanket number was
#: never doing.
EXPECTED_EVENT_VERSIONS: dict[str, int] = {
    "DocumentChunked": 1,
    "DocumentExtracted": 2,
    "EntitiesEmbedded": 1,
    "EntitiesMerged": 2,
    "MergeUndone": 1,
}


def test_the_schema_is_not_empty():
    """A registry-driven suite over an empty registry passes vacuously."""
    assert len(KG_EVENT_TYPES) >= 4


def test_every_event_has_a_pinned_expected_version():
    """The table is a hand-kept list, so it needs the usual staleness guard.

    An entry for an event that no longer exists, or an event with no entry,
    both make the parametrised check below quietly stop describing the schema.
    """
    assert EXPECTED_EVENT_VERSIONS.keys() == {t.__name__ for t in KG_EVENT_TYPES}


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


@pytest.mark.parametrize("event_type", KG_EVENT_TYPES, ids=lambda t: t.__name__)
def test_every_event_is_at_the_version_written_down_for_it(event_type):
    """A version bump is a migration, and must be a visible edit to a table.

    The `__annotations__` check says somebody wrote *a* number; this says which
    one, so a payload shape changing without the version moving -- or the
    version moving without anyone deciding to -- fails here.
    """
    assert (
        event_type.model_fields["event_version"].default
        == EXPECTED_EVENT_VERSIONS[event_type.__name__]
    )


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


@pytest.mark.parametrize("event_type", KG_EVENT_TYPES, ids=lambda t: t.__name__)
def test_every_event_resolves_from_the_registry_by_its_wire_name(event_type):
    """`@register_event` is what turns a stored event back into its class.

    Unregistered, an event round-trips through JSON as a dict and nothing
    fails until a persistent store tries to rehydrate one -- long after the
    events were written. The in-memory store keeps object identity, so no
    other test in this suite can see the difference, which is exactly why a
    cosmic-ray mutant deleting the decorator survived until this existed.

    It also guards the reverse: slice 5b had to *un*-register the legacy
    consolidation events because they were holding the wire names this schema
    needs, and the registry refuses duplicates. If they ever come back, this
    is what says so.
    """
    assert get_event_class(event_type.event_type_name()) is event_type


def _import_every_module_of_the_events_package():
    """Import every module in `redstring.events`, and say which they were.

    Without this the registry check has the same hole it is closing. A new
    event module that nothing imports registers nothing, so the registry would
    not know about it and the comparison would pass -- the omission would be
    invisible in exactly the way the hand-maintained tuple is. Walking the
    package makes the *filesystem* the source of truth, which is the only
    thing here that cannot be forgotten.
    """
    package = importlib.import_module("redstring.events")
    for module in pkgutil.iter_modules(package.__path__):
        importlib.import_module(f"redstring.events.{module.name}")


def _registered_kg_event_classes():
    """Every registered event class that belongs to `redstring.events`.

    Derived from the library's own registry rather than from `KG_EVENT_TYPES`,
    which is the point: the tuple is hand-maintained, and every other gate in
    this module reads from it.

    There is no exclusion list any more. `LEGACY_EVENT_MODULES` and its two
    guard tests were deleted in slice 9 with the last legacy module
    (`events/scraping.py`, B33): an exclusion list over an empty set excludes
    nothing, and a guard that iterates it passes vacuously. Every module in
    the package is now live schema, so the walk is the whole check.
    """
    _import_every_module_of_the_events_package()
    return {
        cls
        for cls in default_registry.list_classes()
        if cls.__module__.startswith("redstring.events")
    }


def test_the_tuple_lists_exactly_the_registered_events():
    """`KG_EVENT_TYPES` is hand-maintained, and everything else keys off it.

    `test_schema.py` and `test_replay_coverage.py` both derive their cases
    from that tuple, so an event class that is written, registered, and simply
    not added to it gets no schema check, no replay case and no handler check
    -- and nothing goes red. The docstring saying "adding an event means
    adding it here" is exactly the prose rule CLAUDE.md says fails.

    This is the one gate that cannot key off the tuple, so it reads the
    registry after importing the whole package.
    """
    registered = _registered_kg_event_classes()
    missing = registered - set(KG_EVENT_TYPES)
    extra = set(KG_EVENT_TYPES) - registered

    assert not missing, (
        f"registered but absent from KG_EVENT_TYPES, so nothing checks them: "
        f"{sorted(c.__name__ for c in missing)}"
    )
    assert not extra, (
        f"in KG_EVENT_TYPES but not registered, so a stored one cannot be "
        f"deserialised: {sorted(c.__name__ for c in extra)}"
    )
