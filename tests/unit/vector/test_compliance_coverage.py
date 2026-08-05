"""A gate on the `VectorStore` compliance suite: every read method is covered.

`CLAUDE.md` says to give every store port the same gate `GraphStore` has, and
this is it. The reason is recorded there at length: four read methods shipped
during slice 3 with complete behavioural tests and no mutation-isolation test,
and each time a mutation run -- not review -- found that returning the live
internal object passed everything. A written rule is what failed those four
times, so the rule is executable here.

The read-method list is **derived from the Protocol by introspection**, never
hand-maintained: a hand-kept list needs updating by the same person who forgot
the test.

Unlike its `GraphStore` counterpart there is no legacy registry, because this
suite is new and every test could simply be named to the convention. Keep it
that way -- add `test_<method>_returns_copies` and
`test_<method>_never_crosses_tenants` alongside the method, and this module
needs no edit at all.
"""

from __future__ import annotations

import inspect
import typing
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from redstring.domain.ids import EntityId, TenantId
from redstring.domain.vector import VectorMatch, VectorRecord
from redstring.ports.vector_store import VectorStore
from tests.compliance import strategies
from tests.compliance.vector_store import VectorStoreCompliance

# The port annotates under `if TYPE_CHECKING`, so resolving its hints at
# runtime needs the names supplied explicitly.
_PORT_NAMESPACE = {
    "VectorRecord": VectorRecord,
    "VectorMatch": VectorMatch,
    "EntityId": EntityId,
    "TenantId": TenantId,
    "Sequence": Sequence,
    "Any": Any,
}

ISOLATION_CONVENTION = "test_{method}_returns_copies"
TENANT_CONVENTION = "test_{method}_never_crosses_tenants"

#: Read methods deliberately exempt, with the reason. Empty today; an entry
#: here is a visible decision, an absent one is the omission this catches.
ISOLATION_EXEMPT: dict[str, str] = {}


def _mentions(annotation: object, targets: set[type]) -> bool:
    """Whether `annotation` contains any of `targets`, however nested."""
    if annotation in targets:
        return True
    return any(_mentions(argument, targets) for argument in typing.get_args(annotation))


def read_methods() -> set[str]:
    """Port methods handing domain objects back to the caller.

    Derived from return annotations rather than names, so `delete() -> bool`
    and the `upsert_*` methods are excluded automatically and a future read
    method is included automatically.
    """
    found = set()
    for name, function in inspect.getmembers(VectorStore, inspect.isfunction):
        if name.startswith("_"):
            continue
        hints = typing.get_type_hints(function, localns=_PORT_NAMESPACE)
        if _mentions(hints.get("return"), {VectorRecord, VectorMatch}):
            found.add(name)
    return found


def _uncovered(convention: str, exempt: dict[str, str]) -> set[str]:
    return {
        method
        for method in read_methods()
        if method not in exempt
        and not hasattr(VectorStoreCompliance, convention.format(method=method))
    }


class TestEveryReadMethodIsCovered:
    def test_the_port_has_read_methods_to_check(self):
        """Guard the guard: a detector that finds nothing passes vacuously."""
        assert read_methods() == {"get", "search"}

    def test_every_read_method_declares_isolation_coverage(self):
        missing = _uncovered(ISOLATION_CONVENTION, ISOLATION_EXEMPT)
        assert not missing, (
            f"read methods with no mutation-isolation test: {sorted(missing)}. "
            f"Add a test that mutates the result and asserts a later read is "
            f"unaffected, named "
            f"{[ISOLATION_CONVENTION.format(method=m) for m in sorted(missing)]} "
            f"-- or, if the method genuinely cannot leak stored state, add it "
            f"to ISOLATION_EXEMPT with the reason."
        )

    def test_every_read_method_declares_tenant_coverage(self):
        missing = _uncovered(TENANT_CONVENTION, {})
        assert not missing, (
            f"read methods with no tenant-isolation test: {sorted(missing)}. "
            f"Add "
            f"{[TENANT_CONVENTION.format(method=m) for m in sorted(missing)]}. "
            f"A cross-tenant leak is a data-confidentiality bug; every read "
            f"path needs its own proof, and a new read is a new place to leak."
        )

    def test_the_exemption_list_does_not_outlive_the_port(self):
        stale = set(ISOLATION_EXEMPT) - read_methods()
        assert not stale, f"ISOLATION_EXEMPT names methods the port no longer has: {sorted(stale)}"

    def test_exemptions_carry_a_reason(self):
        for method, reason in ISOLATION_EXEMPT.items():
            assert reason.strip(), f"{method!r} is exempt with no reason given"


class TestTheMetadataStrategyReachesTheReservedKey:
    """Guard the guard: a strategy that cannot generate the interesting value
    makes every property over it vacuous.

    `metadata_dicts` was built on `property_dicts`, which draws keys from
    `st.text(max_size=6)`. `entity_type` is eleven characters, so **the one
    key the port reads could never be generated** -- and a real divergence
    lived in that blind spot: the in-memory store raised
    `TypeError: unhashable type: 'list'` for a stored
    `{"entity_type": ["person"]}` where pgvector returned `[]`.

    The fix was to draw the key deliberately. This test is what stops the
    blind spot reopening the next time the strategy is refactored, because
    nothing else would fail if it did -- the properties would simply go quiet.
    """

    @staticmethod
    def _sample(count: int = 300) -> list[dict[str, Any]]:
        from hypothesis import HealthCheck, given, settings

        drawn: list[dict[str, Any]] = []

        @settings(max_examples=count, suppress_health_check=list(HealthCheck), deadline=None)
        @given(metadata=strategies.metadata_dicts())
        def collect(metadata: dict[str, Any]) -> None:
            drawn.append(metadata)

        collect()
        return drawn

    def test_the_reserved_key_is_generated(self):
        drawn = self._sample()
        with_key = [m for m in drawn if "entity_type" in m]
        assert with_key, (
            "metadata_dicts never generates an 'entity_type' key, so every "
            "property test over stored metadata is silent about the only key "
            "the port actually reads."
        )

    def test_both_string_and_non_string_values_are_generated(self):
        """A filter is only exercised by values on both sides of it."""
        values = [m["entity_type"] for m in self._sample() if "entity_type" in m]
        assert any(isinstance(value, str) for value in values), "no matchable type name generated"
        assert any(not isinstance(value, str) for value in values), "no non-string generated"
        # The unhashable shapes specifically: these are the ones that made the
        # in-memory adapter raise rather than return nothing.
        assert any(isinstance(value, (list, dict)) for value in values), (
            "no unhashable entity_type generated; the TypeError divergence would slip through again"
        )

    def test_generated_metadata_is_always_storable(self):
        """Whatever it draws must still satisfy `VectorRecord`."""
        for metadata in self._sample(100):
            VectorRecord(entity_id=uuid4(), tenant_id=uuid4(), vector=[1.0], metadata=metadata)


class TestTheSuiteIsTunable:
    def test_max_examples_is_tunable_without_editing_the_suite(self):
        from tests.compliance import vector_store as suite

        assert suite.compliance_settings.max_examples == suite.DEFAULT_MAX_EXAMPLES
