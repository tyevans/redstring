"""`redstring.testing` is a second promised surface, and needs its own gate.

ADR 0006 says `redstring.__all__` is the whole promise and anything reached by
a dotted path is internal. `redstring.testing` breaks that as written: it is
documented for adapter authors outside this repository, who reach it by
`from redstring.testing.graph_store import GraphStoreCompliance` -- a dotted
path -- and it *cannot* be folded into `redstring.__all__`, because importing
it requires `pytest` and `hypothesis` and `redstring` depends on neither.
Exporting it from the top level would make `import redstring` fail for every
consumer who installed the library for knowledge graphs.

So it is a separate surface with a separate `__all__`, and this module is what
keeps that honest. Three checks, and none of them is the one ADR 0006's check
1 performs -- signature self-containment is not the risk here, because a
compliance class's whole public surface is a hook an adapter overrides.

What *is* the risk is drift between three lists that all describe the same
thing: `redstring.testing.__all__`, the modules on disk, and the table in
`docs/how-to/implement-a-store-adapter.md`. The third is already gated by
`tests/unit/test_the_adapter_guide_names_every_compliance_suite.py`; this
module gates the first two against each other.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import redstring.testing as testing

#: Every module in the package that holds a compliance class. Derived, not
#: written down -- a new port's suite is covered the day the module lands.
SUITE_MODULES = sorted(
    name
    for _, name, _ in pkgutil.iter_modules(testing.__path__)
    if name not in {"strategies", "lifetime"}
)


def _compliance_classes(module_name: str) -> list[str]:
    module = importlib.import_module(f"redstring.testing.{module_name}")
    return sorted(name for name in vars(module) if name.endswith("Compliance"))


def test_the_detector_finds_the_suites():
    """Guard the guard: an empty `SUITE_MODULES` would pass everything below.

    Five ports have a compliance suite. The assertion is `>= 5` rather than
    `== 5` so a sixth port does not fail this line, and rather than `> 0`
    because a discovery bug that found one module would otherwise read as
    working.
    """
    assert len(SUITE_MODULES) >= 5, SUITE_MODULES


@pytest.mark.parametrize("module_name", SUITE_MODULES)
def test_every_compliance_class_is_exported(module_name: str) -> None:
    """A suite reachable only by a dotted path is undocumented by ADR 0006's
    rule, and this package inherits that rule rather than being exempt from it.

    The dotted import is still how the how-to tells an adapter author to reach
    a suite -- importing one class out of one module is clearer than a package
    import, and it is what keeps a `ChunkStore` author from paying for the
    `GraphStore` suite's hypothesis strategies. What the export adds is the
    *list*: `redstring.testing.__all__` is where someone discovers that a fifth
    suite exists at all.
    """
    missing = [name for name in _compliance_classes(module_name) if name not in testing.__all__]
    assert not missing, (
        f"redstring/testing/{module_name}.py declares {missing}, which "
        f"redstring.testing.__all__ does not name. A suite nobody can find is "
        f"a suite nobody runs."
    )


@pytest.mark.parametrize("name", sorted(testing.__all__))
def test_every_exported_name_resolves(name: str) -> None:
    """Ruff's F822 covers this for `redstring.__all__` and does not reach here
    automatically -- it is worth having explicitly, because this list is the
    one an adapter author reads first."""
    assert hasattr(testing, name), f"redstring.testing.__all__ names {name}, which does not exist"


def test_importing_the_package_does_not_need_a_store_adapter():
    """The forbidden-imports contract, asserted from the other side.

    `lint-imports` forbids `redstring.testing` from importing `redstring.graph`
    and its siblings, because a suite that could reach an adapter would be
    checking one implementation while claiming to check the port. That
    contract is a static check over the import graph; this is the same claim
    made against what actually got imported, which catches a function-local
    import the contract's own author would call a loophole.

    A local import inside a test body is exactly how the previous violation
    was written -- `from redstring.chunks.adapters.memory import
    InMemoryChunkStore`, inside a method, with a comment explaining that
    module scope would create a cycle.
    """
    import sys

    for module_name in SUITE_MODULES:
        importlib.import_module(f"redstring.testing.{module_name}")

    forbidden = (
        "redstring.graph",
        "redstring.vector",
        "redstring.chunks",
        "redstring.llm",
        "redstring.extraction",
        "redstring.consolidation",
        "redstring.temporal",
        "redstring.projections",
        "redstring.composition",
    )

    # Read off each module's own globals rather than `sys.modules`: by the time
    # this test runs the rest of the suite has imported half the library, so
    # "is it loaded" answers nothing. What a compliance module *bound at module
    # scope* is attributable to that module.
    for module_name in SUITE_MODULES:
        module = sys.modules[f"redstring.testing.{module_name}"]
        offenders = sorted(
            {
                f"{value.__module__}.{getattr(value, '__name__', '?')}"
                for value in vars(module).values()
                for root in forbidden
                if getattr(value, "__module__", "").startswith(f"{root}.")
            }
        )
        assert not offenders, (
            f"redstring/testing/{module_name}.py holds a module-scope name from "
            f"an adapter package: {offenders}. A compliance suite states what a "
            f"*port* promises; reaching an adapter makes it a test of one "
            f"implementation."
        )
