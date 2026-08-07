"""The top layer: modules holding collaborators no lower layer may hold together.

`pyproject.toml` states that a module wanting in here has to say what it
composes. There are two:

- `build_graph` composes `LlmProvider` + `GraphStore` (+ optionally
  `EmbeddingProvider` + `VectorStore`). `extraction` may not import
  `projections`, so nothing below can hold both halves.
- `retrieval` composes `EmbeddingProvider` + `VectorStore` + `GraphStore`.
  `vector` and `graph` are siblings that may not import each other and
  neither may import `llm`, so no sibling can hold all three.

This was one module until retrieval arrived; see the ADR amending 0007.

Everything `redstring.composition` exported as a module is re-exported here, so
no import path changed in the move. That includes `Consolidator` and
`ConsolidationReport`, which `redstring/__init__.py` imports from this name.

**`build_graph` is both a submodule and the function in it, and the function
wins.** That is deliberate and it is what keeps `from redstring.composition
import build_graph` meaning what it meant before the move. The order is what
makes it deterministic: the `from` below loads the submodule (which binds
`build_graph` on this package to the *module*), then rebinds the name to the
function, and a later `import redstring.composition.build_graph` finds the
module already in `sys.modules` and does not re-bind. Renaming the submodule to
un-shadow it would break the public import path this package exists to
preserve.
"""

from __future__ import annotations

from redstring.composition.build_graph import (
    AUTO,
    AutoDomain,
    ConsolidationReport,
    Consolidator,
    GraphBuildReport,
    build_graph,
)

__all__ = [
    "AUTO",
    "AutoDomain",
    "ConsolidationReport",
    "Consolidator",
    "GraphBuildReport",
    "build_graph",
]
