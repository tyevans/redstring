"""The top layer: modules holding collaborators no lower layer may hold together.

`pyproject.toml` states that a module wanting in here has to say what it
composes. There are three:

- `build_graph` composes `LlmProvider` + `GraphStore` (+ optionally
  `EmbeddingProvider` + `VectorStore`). `extraction` may not import
  `projections`, so nothing below can hold both halves.
- `retrieval` composes `EmbeddingProvider` + `VectorStore` + `GraphStore` in
  `Retriever`, and `EmbeddingProvider` + `ChunkStore` in `ChunkRetriever`.
  `vector` and `graph` are siblings that may not import each other and
  neither may import `llm`, so no sibling can hold `Retriever`'s three.
  `ChunkRetriever` joins a second forbidden pair in the same module: `llm`
  and `chunks` are likewise siblings forbidden from importing each other, so
  no sibling can hold both of its two.
- `index_documents` composes `Chunker` + `ChunkStore` -- the same
  `extraction` + `projections` pair `build_graph` names, so it is admitted by
  the argument already recorded rather than by a new one.

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

`index_documents` is the same shape for the same reason, and the same import
order settles it.
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
from redstring.composition.index_documents import IndexReport, index_documents
from redstring.composition.retrieval import ChunkRetriever, Retriever

__all__ = [
    "AUTO",
    "AutoDomain",
    "ChunkRetriever",
    "ConsolidationReport",
    "Consolidator",
    "GraphBuildReport",
    "IndexReport",
    "Retriever",
    "build_graph",
    "index_documents",
]
