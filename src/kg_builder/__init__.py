"""kg-builder: build a knowledge graph from documents you already have.

```python
from kg_builder import FakeLlmProvider, InMemoryGraphStore, SourceDocument, build_graph

store = InMemoryGraphStore()
report = await build_graph(
    SourceDocument(id="notes", text="Ada Lovelace worked with Charles Babbage."),
    provider=FakeLlmProvider(by_substring={"Ada": {...}}),
    store=store,
    tenant_id=tenant_id,
)
entities = await store.find_entities(tenant_id, entity_type="Person")
```

`docs/examples/build_a_graph.py` is that, complete and runnable, and
`tests/unit/test_end_to_end_example.py` runs it on every commit -- including
an assertion that it imports nothing but this module, so the surface below
cannot quietly stop being sufficient.

## What is supported

**Everything named in `__all__` here, and nothing else.** Anything reached
through a dotted path (`kg_builder.extraction.mapping`, say) is internal and
may change without notice, including in a patch release.

- **Composition.** `build_graph`, `GraphBuildReport`, `AUTO`.
- **What you put in.** `SourceDocument`.
- **What comes out.** `Entity`, `Relationship`, `Alias`, `ExtractionMethod`,
  and the `DocumentExtracted` event carrying them.
- **Ports.** `GraphStore`, `VectorStore`, `LlmProvider`. Implement one to
  plug in a backend of your own; the compliance suite in `tests/compliance`
  is what says whether you got it right.
- **Adapters.** `InMemoryGraphStore` and `InMemoryVectorStore` are complete
  implementations, not test doubles -- suitable for a single-process job.
  `Neo4jGraphStore` and `PgVectorStore` need their extras
  (`kg-builder[neo4j]`, and `asyncpg`, which is a core dependency).
- **Providers.** `FakeLlmProvider` answers from a script and validates like
  the real thing; `LangChainLlmProvider` (`kg-builder[llm]`) speaks to any
  OpenAI-compatible server.
- **Pieces, for callers who want the steps rather than the whole.**
  `ExtractionPipeline`, `GraphProjection`, `VectorProjection`, `project`,
  `Document`, `domain_system_prompt`.
- **Errors.** `KgBuilderError` and the things that go wrong under it.

## What is deliberately not here

- **Consolidation and temporal inference.** Both are real
  (`kg_builder.consolidation`, `kg_builder.temporal`) and both are tested,
  but neither has a composed entry point yet -- exporting the classes would
  publish an API whose shape is still being decided by the callers it does
  not have. Import them by path and expect movement.
- **No scraping, no HTML preprocessing.** A caller supplies a
  `SourceDocument`. Fetching content is a different job with different
  failure modes, and it was removed rather than left unfinished (slice 1).
- **No settings object and no environment reads.** Every component takes its
  configuration through its constructor. `tests/unit/test_library_reads_no_environment.py`
  is what keeps that true.
- **Encryption.** There was an `EncryptionService`; it had no caller, no port
  to sit behind, and an encrypted `normalized_name` cannot be indexed or
  blocked on -- which breaks consolidation. Deleted in slice 10; BACKLOG B58
  records what a real answer would need.

## Where the write model is

Extraction emits `DocumentExtracted` on a `Document` aggregate and stops.
`kg_builder.projections` folds that event into a store. `build_graph` does
both in one call for a caller with no event store; a caller who has one
appends `report.event` and drives `project` over the feed instead. The
separation is not decoration -- it is why a store can be rebuilt, and
`tests/unit/projections/test_replay_equivalence.py` is what proves it can.
"""

from kg_builder.aggregates.document import Document
from kg_builder.composition import AUTO, GraphBuildReport, build_graph
from kg_builder.domain.alias import Alias
from kg_builder.domain.entity import Entity, ExtractionMethod
from kg_builder.domain.exceptions import (
    EmptyCompletionError,
    KgBuilderError,
    LlmProviderError,
    MalformedCompletionError,
)
from kg_builder.domain.relationship import Relationship
from kg_builder.domain.source import SourceDocument
from kg_builder.events.document import DocumentExtracted
from kg_builder.extraction.pipeline import (
    DEFAULT_SYSTEM_PROMPT,
    ExtractionPipeline,
    PartialExtractionError,
    PipelineResult,
)
from kg_builder.extraction.prompt_generator import domain_system_prompt
from kg_builder.graph.adapters.memory import InMemoryGraphStore
from kg_builder.llm.adapters.fake import FakeLlmProvider
from kg_builder.ports.graph_store import GraphStore
from kg_builder.ports.llm_provider import LlmProvider
from kg_builder.ports.vector_store import VectorStore
from kg_builder.projections import GraphProjection, ReplayReport, VectorProjection, project
from kg_builder.vector.adapters.memory import InMemoryVectorStore

__version__ = "0.1.0"

__all__ = [
    "AUTO",
    "DEFAULT_SYSTEM_PROMPT",
    "Alias",
    "Document",
    "DocumentExtracted",
    "EmptyCompletionError",
    "Entity",
    "ExtractionMethod",
    "ExtractionPipeline",
    "FakeLlmProvider",
    "GraphBuildReport",
    "GraphProjection",
    "GraphStore",
    "InMemoryGraphStore",
    "InMemoryVectorStore",
    "KgBuilderError",
    "LlmProvider",
    "LlmProviderError",
    "MalformedCompletionError",
    "PartialExtractionError",
    "PipelineResult",
    "Relationship",
    "ReplayReport",
    "SourceDocument",
    "VectorProjection",
    "VectorStore",
    "__version__",
    "build_graph",
    "domain_system_prompt",
    "project",
]
