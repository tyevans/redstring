"""Shared port-compliance suites, shipped for adapters written elsewhere.

**A contract two implementations satisfy by accident is not a contract**, and
until this package shipped, the only implementations that could run these
suites were the ones in this repository. An adapter written against
`GraphStore` in someone else's tree had the Protocol -- which pins the
signatures and says nothing about the semantics -- and a `docs/how-to` page
telling it to copy cases out of a directory that was not in the wheel.

Every class here is the same body this repository runs against its own
adapters. Nothing is a reduced "public" variant: a weaker suite for outside
adapters would make the port mean two different things, which is the divergence
these suites exist to prevent.

## Using it

```python
from redstring.testing.graph_store import GraphStoreCompliance


class TestMyStore(GraphStoreCompliance):
    async def new_store(self) -> GraphStore:
        return MyGraphStore()
```

Nothing here is collected on its own -- no module matches `test_*.py` and no
class matches `Test*` -- so the suite runs exactly once per adapter that
subclasses it, and never on its own. A "passing" contract with no
implementation behind it is not reachable.

Three things a consuming project must supply, none of which this package can
set on its behalf:

- **`pytest-asyncio` in `auto` mode**, or an equivalent. Every case is a bare
  `async def`, so under the default strict mode they are collected and
  skipped -- which reads as a pass. Set
  `asyncio_mode = "auto"` in your pytest configuration.
- **`new_store()` returning an *empty*, freshly isolated store on every
  call.** The property tests call it once per generated example, because
  hypothesis reuses the surrounding fixture across examples; a shared store
  lets example *n* decide example *n+1*.
- **`dispose()`, if the adapter holds a driver or a pool.** It is a no-op by
  default, and an adapter that needs it and does not override it leaks one
  connection per example.

`KG_COMPLIANCE_MAX_EXAMPLES` (default 50) tunes the hypothesis budget for a
whole run. It is read at import, so it cannot be set per subclass.

## The import guard

`pytest` and `hypothesis` are imported at module scope throughout this package
and are **not** dependencies of `redstring` itself. Installing
`redstring[test]` supplies them. The guard below turns the resulting
`ModuleNotFoundError` into a message naming the extra, which is the same shape
`RedisCache.from_url` and `LangChainLlmProvider` use for their backends -- a
bare `ModuleNotFoundError: No module named 'hypothesis'` from inside a library
import is a puzzle, and naming the install is the whole fix.

Nothing under `src/redstring/` outside this package imports it, so
`import redstring` never reaches a test dependency. That is asserted rather
than intended: `tests/unit/test_dependencies_stay_confined.py` carries a row
for each of the two libraries.
"""

from __future__ import annotations

try:
    import hypothesis  # noqa: F401  -- imported for the side effect of failing
    import pytest  # noqa: F401  -- loudly, with the extra named, right here
except ModuleNotFoundError as exc:  # pragma: no cover - exercised by a subprocess test
    raise ModuleNotFoundError(
        "redstring.testing needs pytest and hypothesis, which redstring itself "
        "does not depend on. Install them with `pip install redstring[test]` "
        "(or `uv add 'redstring[test]'`)."
    ) from exc

from redstring.testing import strategies
from redstring.testing.cache import NOW, CacheCompliance
from redstring.testing.chunk_store import ChunkStoreCompliance
from redstring.testing.embedding_provider import EmbeddingProviderCompliance
from redstring.testing.graph_store import GraphStoreCompliance
from redstring.testing.lifetime import NoOpLifetime
from redstring.testing.vector_store import VectorStoreCompliance

#: The promise of this package, in the same sense `redstring.__all__` is the
#: promise of the library -- see ADR 0006, and the ADR that made this a second
#: gated surface rather than an extension of that one. A name reachable here
#: by a dotted path and absent from this list is internal.
__all__ = [
    "NOW",
    "CacheCompliance",
    "ChunkStoreCompliance",
    "EmbeddingProviderCompliance",
    "GraphStoreCompliance",
    "NoOpLifetime",
    "VectorStoreCompliance",
    "strategies",
]
