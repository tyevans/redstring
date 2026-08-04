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

The surface is **closed**, which is a stronger claim than "documented" and is
the one that took a review to get right. Every type named in an exported
signature is either exported too, or belongs to another package and is
recorded with its import path; every `KgBuilderError` is either exported or
recorded as belonging to a capability that is not.
`tests/unit/test_public_surface_is_self_contained.py` is what enforces both,
and the reason it exists is that four names failed the first test of it --
`RefusedCompletionError`, whose own docstring argues a caller *must*
distinguish it from `EmptyCompletionError`, was raised by exported code and
could not be caught without a dotted import.

- **Composition.** `build_graph`, `GraphBuildReport`, `AUTO`, `AutoDomain`.
- **What you put in.** `SourceDocument`.
- **What comes out.** `Entity`, `Relationship`, `Alias`, `ExtractionMethod`,
  `TemporalExtent` (with `DatePrecision` and `UncertaintyMarker`),
  `VectorRecord`, `VectorMatch`, and the `DocumentExtracted` and
  `EntitiesEmbedded` events carrying them. `EntityId`, `RelationshipId`,
  `TenantId` and `SourceId` are the id vocabulary -- the first three are
  `UUID`, the last is `str`.
- **Ports.** `GraphStore`, `VectorStore`, `LlmProvider`, `Chunker`. Implement
  one to plug in a backend of your own; the compliance suite in
  `tests/compliance` is what says whether you got it right.
- **Adapters.** `InMemoryGraphStore` and `InMemoryVectorStore` are complete
  implementations, not test doubles -- suitable for a single-process job.
  `Neo4jGraphStore` and `PgVectorStore` need their extras
  (`kg-builder[neo4j]`, and `asyncpg`, which is a core dependency).
- **Providers.** `FakeLlmProvider` answers from a script or by substring
  (`Response`, `EMPTY`) and validates like the real thing;
  `LangChainLlmProvider` (`kg-builder[llm]`) speaks to any OpenAI-compatible
  server.
- **Domain-aware prompting.** `domain_system_prompt` takes a bundled domain id
  or a `DomainSchema` of your own -- `load_schema_from_file` and
  `load_schema_from_string` build one, out of `EntityTypeSchema`,
  `RelationshipTypeSchema`, `PropertySchema` and `ConfidenceThresholds`.
- **Pieces, for callers who want the steps rather than the whole.**
  `ExtractionPipeline` (`PipelineResult`, `DEFAULT_SYSTEM_PROMPT`),
  `Chunk`/`ChunkingResult`, `GraphProjection`, `VectorProjection`, `project`,
  `ReplayReport`, and `Document` with `document_stream` to address it.
- **Errors.** `KgBuilderError` and everything under it that a caller can
  reach: `LlmProviderError` and its three shapes, `MissingEntityError`,
  `AliasCycleError`, `DimensionMismatchError`, `PartialExtractionError`, and
  the chunking three.

`project`'s signature is the one place the surface deliberately names another
package's types: `GlobalEventFeed`, `EventSubscriber` and `Position` all come
from `eventsource` (a core dependency, so they are importable). Re-exporting
them under our own name would be worse than depending on them openly.

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
from kg_builder.composition import AUTO, AutoDomain, GraphBuildReport, build_graph
from kg_builder.domain.alias import Alias
from kg_builder.domain.entity import Entity, ExtractionMethod
from kg_builder.domain.exceptions import (
    AliasCycleError,
    DimensionMismatchError,
    EmptyCompletionError,
    KgBuilderError,
    LlmProviderError,
    MalformedCompletionError,
    MissingEntityError,
    RefusedCompletionError,
    UnknownDomainError,
)
from kg_builder.domain.ids import EntityId, RelationshipId, SourceId, TenantId
from kg_builder.domain.relationship import Relationship
from kg_builder.domain.source import SourceDocument
from kg_builder.domain.temporal import DatePrecision, TemporalExtent, UncertaintyMarker
from kg_builder.domain.vector import VectorMatch, VectorRecord
from kg_builder.events.document import DocumentExtracted, EntitiesEmbedded
from kg_builder.events.streams import document_stream
from kg_builder.extraction.chunking import Chunk, ChunkingResult
from kg_builder.extraction.domains.loader import load_schema_from_file, load_schema_from_string
from kg_builder.extraction.domains.models import (
    ConfidenceThresholds,
    DomainSchema,
    EntityTypeSchema,
    PropertySchema,
    RelationshipTypeSchema,
)
from kg_builder.extraction.errors import ChunkerError, ChunkingError, ChunkSizeError
from kg_builder.extraction.pipeline import (
    DEFAULT_SYSTEM_PROMPT,
    ExtractionPipeline,
    PartialExtractionError,
    PipelineResult,
)
from kg_builder.extraction.prompt_generator import domain_system_prompt
from kg_builder.extraction.protocols import Chunker
from kg_builder.graph.adapters.memory import InMemoryGraphStore
from kg_builder.llm.adapters.fake import EMPTY, FakeLlmProvider, Response
from kg_builder.ports.graph_store import GraphStore
from kg_builder.ports.llm_provider import LlmProvider
from kg_builder.ports.vector_store import VectorStore
from kg_builder.projections import GraphProjection, ReplayReport, VectorProjection, project
from kg_builder.vector.adapters.memory import InMemoryVectorStore

__version__ = "0.1.0"

__all__ = [
    "AUTO",
    "DEFAULT_SYSTEM_PROMPT",
    "EMPTY",
    "Alias",
    "AliasCycleError",
    "AutoDomain",
    "Chunk",
    "ChunkSizeError",
    "Chunker",
    "ChunkerError",
    "ChunkingError",
    "ChunkingResult",
    "ConfidenceThresholds",
    "DatePrecision",
    "DimensionMismatchError",
    "Document",
    "DocumentExtracted",
    "DomainSchema",
    "EmptyCompletionError",
    "EntitiesEmbedded",
    "Entity",
    "EntityId",
    "EntityTypeSchema",
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
    "MissingEntityError",
    "PartialExtractionError",
    "PipelineResult",
    "PropertySchema",
    "RefusedCompletionError",
    "Relationship",
    "RelationshipId",
    "RelationshipTypeSchema",
    "ReplayReport",
    "Response",
    "SourceDocument",
    "SourceId",
    "TemporalExtent",
    "TenantId",
    "UncertaintyMarker",
    "UnknownDomainError",
    "VectorMatch",
    "VectorProjection",
    "VectorRecord",
    "VectorStore",
    "__version__",
    "build_graph",
    "document_stream",
    "domain_system_prompt",
    "load_schema_from_file",
    "load_schema_from_string",
    "project",
]
