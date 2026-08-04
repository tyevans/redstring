"""A gate on the compliance suite itself: every read method must be covered.

Four read methods shipped during slice 3 with complete behavioural tests and
no mutation-isolation test, and each time a mutation-testing run -- not
review -- found that returning the live internal object passed everything.
The rule "add the isolation test with the method" was written down twice and
is still only a comment; a comment is precisely what failed those four times.

This module makes it mechanical. The read-method list is **derived from the
`GraphStore` Protocol by introspection**, never hand-maintained: a
hand-maintained list is the same failure mode one level up, since it needs
updating by the same person who forgot the test.

Adding a read method to the port therefore fails this test until it is
covered, and the failure names the method and what is missing.
"""

from __future__ import annotations

import inspect
import typing
from collections.abc import Sequence

from kg_builder.domain.entity import Entity
from kg_builder.domain.ids import EntityId, RelationshipId, TenantId
from kg_builder.domain.relationship import Relationship
from kg_builder.ports.graph_store import GraphStore
from tests.compliance.graph_store import GraphStoreCompliance

# The port annotates under `if TYPE_CHECKING`, so resolving its hints at
# runtime needs the names supplied explicitly.
_PORT_NAMESPACE = {
    "Entity": Entity,
    "Relationship": Relationship,
    "EntityId": EntityId,
    "RelationshipId": RelationshipId,
    "TenantId": TenantId,
    "Sequence": Sequence,
}

#: Which test covers each read method's mutation isolation -- that mutating a
#: returned object does not change what a later read returns.
#:
#: The mapping is hand-written but the *keys are checked against the Protocol*,
#: so a new read method fails below until it appears here. Naming across these
#: tests is historic and inconsistent; a name convention would be tidier but
#: would mean renaming working tests to satisfy a checker, which is the tail
#: wagging the dog.
ISOLATION_COVERAGE = {
    "get_entity": "test_mutating_a_read_result_does_not_change_the_store",
    "get_entities": "test_mutating_a_batch_result_does_not_change_the_store",
    "find_entities": "test_mutating_a_find_result_does_not_change_the_store",
    "find_by_blocking_key": "test_mutating_a_blocking_key_result_does_not_change_the_store",
    "find_by_blocking_keys": "test_mutating_a_grouped_result_does_not_change_the_store",
    "get_relationships": "test_mutating_a_relationship_result_does_not_change_the_store",
    "get_relationships_for": "test_mutating_a_batched_relationship_does_not_change_the_store",
    "neighbors": "test_mutating_a_neighbours_result_does_not_change_the_store",
}

#: Which test proves each read method cannot see another tenant. Several
#: methods are covered by one broad property test that exercises every read
#: under the wrong tenant, so values repeat.
TENANT_COVERAGE = {
    "get_entity": "test_no_read_under_one_tenant_ever_sees_another",
    "get_entities": "test_get_entities_never_crosses_tenants",
    "find_entities": "test_no_read_under_one_tenant_ever_sees_another",
    "find_by_blocking_key": "test_no_read_under_one_tenant_ever_sees_another",
    "find_by_blocking_keys": "test_find_by_blocking_keys_never_crosses_tenants",
    "get_relationships": "test_relationships_are_never_readable_from_another_tenant",
    "get_relationships_for": "test_get_relationships_for_never_crosses_tenants",
    "neighbors": "test_relationships_do_not_cross_tenants",
}

#: Read methods deliberately exempt from isolation coverage, with the reason.
#:
#: Empty today. An entry here is a visible decision; an absent entry is the
#: omission this module exists to catch. Anything added must say why the
#: method cannot hand a caller a mutable view of stored state.
ISOLATION_EXEMPT: dict[str, str] = {}


def _mentions(annotation: object, targets: set[type]) -> bool:
    """Whether `annotation` contains any of `targets`, however nested."""
    if annotation in targets:
        return True
    return any(_mentions(arg, targets) for arg in typing.get_args(annotation))


def read_methods() -> set[str]:
    """Port methods that hand domain objects back to the caller.

    Derived from return annotations rather than names: a method returning an
    `Entity` or `Relationship` -- at any nesting depth, so `list[Entity]` and
    `dict[str, list[Entity]]` both count -- can leak a mutable view of stored
    state. `delete_relationship() -> bool` and the `upsert_*` methods cannot,
    and are excluded automatically.
    """
    found = set()
    for name, function in inspect.getmembers(GraphStore, inspect.isfunction):
        if name.startswith("_"):
            continue
        hints = typing.get_type_hints(function, localns=_PORT_NAMESPACE)
        if _mentions(hints.get("return"), {Entity, Relationship}):
            found.add(name)
    return found


class TestEveryReadMethodIsCovered:
    def test_the_port_has_read_methods_to_check(self):
        """Guard the guard: a detector that finds nothing passes vacuously."""
        assert len(read_methods()) >= 8

    def test_every_read_method_declares_isolation_coverage(self):
        missing = read_methods() - set(ISOLATION_COVERAGE) - set(ISOLATION_EXEMPT)
        assert not missing, (
            f"read methods with no mutation-isolation test: {sorted(missing)}. "
            f"Add a test that mutates the result and asserts a later read is "
            f"unaffected, then register it in ISOLATION_COVERAGE -- or, if the "
            f"method genuinely cannot leak stored state, add it to "
            f"ISOLATION_EXEMPT with the reason."
        )

    def test_every_read_method_declares_tenant_coverage(self):
        missing = read_methods() - set(TENANT_COVERAGE)
        assert not missing, (
            f"read methods with no tenant-isolation test: {sorted(missing)}. "
            f"A cross-tenant leak is a data-confidentiality bug; every read "
            f"path needs its own proof, and a new read is a new place to leak."
        )

    def test_registered_tests_exist_on_the_compliance_class(self):
        """Catch a rename that silently empties the registry."""
        for registry, label in ((ISOLATION_COVERAGE, "isolation"), (TENANT_COVERAGE, "tenant")):
            for method, test_name in registry.items():
                assert hasattr(GraphStoreCompliance, test_name), (
                    f"{label} coverage for {method!r} names {test_name!r}, "
                    f"which does not exist on GraphStoreCompliance"
                )

    def test_the_registries_do_not_outlive_the_port(self):
        """A method removed from the port must not leave a stale entry."""
        known = read_methods()
        for registry, label in (
            (ISOLATION_COVERAGE, "ISOLATION_COVERAGE"),
            (TENANT_COVERAGE, "TENANT_COVERAGE"),
            (ISOLATION_EXEMPT, "ISOLATION_EXEMPT"),
        ):
            stale = set(registry) - known
            assert not stale, f"{label} names methods the port no longer has: {sorted(stale)}"

    def test_exemptions_carry_a_reason(self):
        for method, reason in ISOLATION_EXEMPT.items():
            assert reason.strip(), f"{method!r} is exempt with no reason given"
