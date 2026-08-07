"""The `docs/` example is executed here, and that is the point of it.

Slice 10's deliverable is that a caller can build a `SourceDocument`, extract
it with an `LlmProvider`, project it into a `GraphStore` and query the result
-- in about a screen. `docs/examples/build_a_graph.py` is that screen, and
this file is what keeps it true. A README snippet nothing runs is a snippet
that rots, and this project has just spent a slice deleting statements about
the world that had quietly become false.

Two things are asserted, and the second is the less obvious one:

1. The example runs and the graph it built answers the questions it asks.
2. **Every import in the example is from `redstring` itself.** That is what
   makes it evidence about the *public API* rather than about the library's
   internals. Without it the example could reach into
   `redstring.graph.adapters.memory` and still pass, and the top-level
   surface could be empty while this test stayed green.

Loaded from its path rather than imported as a package, because `docs/` is
documentation and making it importable would mean shipping it in the wheel.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "examples" / "build_a_graph.py"

#: Modules the example may import besides `redstring`. The standard library
#: is not the subject here -- `asyncio` and `uuid` say nothing about whether
#: this library's public API is usable.
ALLOWED_NON_KG_ROOTS = frozenset({"asyncio", "uuid", "__future__"})


@pytest.fixture(scope="module")
def example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("redstring_docs_example", EXAMPLE)
    assert spec is not None, f"cannot load {EXAMPLE}"
    assert spec.loader is not None, f"no loader for {EXAMPLE}"
    module = importlib.util.module_from_spec(spec)
    # Registered before execution so that `from __future__ import annotations`
    # and any dataclass in the example resolve their own module by name.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestTheExampleIsAboutThePublicApi:
    def test_it_imports_redstring_and_nothing_deeper(self) -> None:
        tree = ast.parse(EXAMPLE.read_text(encoding="utf-8"), filename=str(EXAMPLE))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                imported.append(node.module or "")
            elif isinstance(node, ast.Import):
                imported += [alias.name for alias in node.names]

        assert imported, "the example imports nothing at all; did it move?"
        offenders = [
            module
            for module in imported
            if module.split(".")[0] not in ALLOWED_NON_KG_ROOTS and module != "redstring"
        ]
        assert not offenders, (
            f"the example reaches past the public API into {offenders}. Either export "
            f"what it needs from `redstring/__init__.py` or the example is not "
            f"evidence that the public API is usable."
        )

    def test_it_fits_in_a_screen(self) -> None:
        # The brief's own success criterion, made checkable. Counted from the
        # first import so the module docstring and the canned answer -- which
        # a real caller replaces with a real provider -- do not pay for
        # themselves twice.
        source = EXAMPLE.read_text(encoding="utf-8")
        body = source[source.index("import asyncio") :]
        assert len(body.splitlines()) < 80, "the composition is too big to read at once"


class TestTheExampleRuns:
    async def test_it_builds_the_graph_it_describes(self, example: ModuleType) -> None:
        people, babbage_neighbours, _ = await example.main()

        assert people == ["Ada Lovelace", "Charles Babbage"]
        # Both of Babbage's edges are traversed, and in both directions: Ada
        # points *at* him and he points at the Engine. A `neighbors` that
        # followed only outbound edges would return the Engine alone, and a
        # single-name assertion could not tell the two apart.
        assert babbage_neighbours == ["Ada Lovelace", "Analytical Engine"]

    async def test_the_entity_type_filter_actually_filtered(self, example: ModuleType) -> None:
        # "Analytical Engine" is a Machine, so its absence from `people` is
        # the filter working rather than an accident of the fixture: the
        # example extracts three entities and queries back two.
        people, _, _ = await example.main()

        assert "Analytical Engine" not in people

    async def test_the_misspelled_query_retrieves_the_name_it_meant(
        self, example: ModuleType
    ) -> None:
        # "Charles Babage" is not any stored name, so this is the lexical
        # channel doing the work the example claims it does: the query shares
        # a blocking key with "Charles Babbage" and scores highest against it.
        # Asserting *first* rather than merely present is the point -- a
        # retriever that returned the three entities in any order would pass a
        # membership check while ranking nothing.
        _, _, retrieved = await example.main()

        assert retrieved[0] == "Charles Babbage"
        assert len(retrieved) == 3
