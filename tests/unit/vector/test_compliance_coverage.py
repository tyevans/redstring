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

from kg_builder.domain.ids import EntityId, TenantId
from kg_builder.domain.vector import VectorMatch, VectorRecord
from kg_builder.ports.vector_store import VectorStore
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


class TestTheSuiteIsTunable:
    def test_max_examples_is_tunable_without_editing_the_suite(self):
        from tests.compliance import vector_store as suite

        assert suite.compliance_settings.max_examples == suite.DEFAULT_MAX_EXAMPLES
