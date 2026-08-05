"""redstring: build a knowledge graph from documents you already have.

```python
from redstring import FakeLlmProvider, InMemoryGraphStore, SourceDocument, build_graph

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
through a dotted path (`redstring.extraction.mapping`, say) is internal and
may change without notice, including in a patch release.

The surface is **closed**, which is a stronger claim than "documented" and is
the one that took a review to get right. Every type named in an exported
signature is either exported too, or belongs to another package and is
recorded with its import path; every `RedstringError` is either exported or
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
- **Ports.** `GraphStore`, `VectorStore`, `LlmProvider`, `EmbeddingProvider`,
  `Chunker`. Implement
  one to plug in a backend of your own; the compliance suite in
  `tests/compliance` is what says whether you got it right.
- **Adapters.** `InMemoryGraphStore` and `InMemoryVectorStore` are complete
  implementations, not test doubles -- suitable for a single-process job.
  `Neo4jGraphStore` and `PgVectorStore` need their extras
  (`redstring[neo4j]`, and `asyncpg`, which is a core dependency).
- **Providers.** `FakeLlmProvider` answers from a script or by substring
  (`Response`, `EMPTY`) and validates like the real thing;
  `LangChainLlmProvider` (`redstring[llm]`) speaks to any OpenAI-compatible
  server. `FakeEmbeddingProvider` does the same job for `EmbeddingProvider`,
  hashing text into deterministic unit vectors so the vector half of the
  library is exercisable with no model;
  `redstring.llm.adapters.langchain_embedding.LangChainEmbeddingProvider`
  (`redstring[llm]`) is the live one. Both LangChain adapters are reached by
  path rather than exported, so `import redstring` does not pull LangChain in.
- **Domain-aware prompting.** `domain_system_prompt` takes a bundled domain id
  or a `DomainSchema` of your own -- `load_schema_from_file` and
  `load_schema_from_string` build one, out of `EntityTypeSchema`,
  `RelationshipTypeSchema`, `PropertySchema` and `ConfidenceThresholds`.
- **Pieces, for callers who want the steps rather than the whole.**
  `ExtractionPipeline` (`PipelineResult`, `DEFAULT_SYSTEM_PROMPT`),
  `Chunk`/`ChunkingResult`, `GraphProjection`, `VectorProjection`, `project`,
  `ReplayReport`, and `Document` with `document_stream` to address it.
  `project(strict=True)` raises `ReplayFailedError` rather than counting a
  rejection, for the caller who would rather have no rebuild than a partial
  one.
- **Errors.** `RedstringError` and everything under it that a caller can
  reach: `LlmProviderError` and its three shapes, `MissingEntityError`,
  `AliasCycleError`, `DimensionMismatchError`, `PartialExtractionError`,
  `ReplayFailedError`, and the chunking three.

`project`'s signature is the one place the surface deliberately names another
package's types: `GlobalEventFeed`, `EventSubscriber` and `Position` all come
from `eventsource` (a core dependency, so they are importable). Re-exporting
them under our own name would be worse than depending on them openly.

## What is deliberately not here

- **Temporal inference.** `redstring.temporal` is real and tested and has no
  composed entry point yet, so exporting its classes would publish an API
  whose shape is still being decided by callers it does not have. Import it
  by path and expect movement.

  Consolidation used to be listed here too. `Consolidator` is the composed
  entry point it was waiting for (ADR 0015), and it is exported -- together
  with the closure that came with it: `CandidateFinder`, `Adjudicator`, the
  two merge events, the four value types those name, and the four
  consolidation errors.
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
`redstring.projections` folds that event into a store. `build_graph` does
both in one call for a caller with no event store; a caller who has one
appends `report.event` and drives `project` over the feed instead. The
separation is not decoration -- it is why a store can be rebuilt, and
`tests/unit/projections/test_replay_equivalence.py` is what proves it can.
"""

from redstring.aggregates.document import Document
from redstring.composition import (
    AUTO,
    AutoDomain,
    ConsolidationReport,
    Consolidator,
    GraphBuildReport,
    build_graph,
)
from redstring.consolidation.candidates import CandidateFinder, ScoredCandidate
from redstring.consolidation.policy import AdjudicationVerdict, Adjudicator
from redstring.domain.alias import Alias
from redstring.domain.consolidation import RelationshipRedirection
from redstring.domain.entity import Entity, ExtractionMethod
from redstring.domain.exceptions import (
    AliasCycleError,
    ConsolidationInvariantError,
    DimensionMismatchError,
    DoubleMergeError,
    EmbeddingProviderError,
    EmptyCompletionError,
    LlmProviderError,
    MalformedCompletionError,
    MergeIntoAliasError,
    MissingEntityError,
    RedstringError,
    RefusedCompletionError,
    ReplayFailedError,
    UnknownDomainError,
    UnknownMergeError,
)
from redstring.domain.ids import EntityId, RelationshipId, SourceId, TenantId
from redstring.domain.interval import Bounds, TemporalRelation
from redstring.domain.relationship import Relationship
from redstring.domain.similarity import FeatureWeights, SimilarityFeatures
from redstring.domain.source import SourceDocument
from redstring.domain.temporal import DatePrecision, TemporalExtent, UncertaintyMarker
from redstring.domain.vector import VectorMatch, VectorRecord
from redstring.events.document import DocumentExtracted, EntitiesEmbedded
from redstring.events.merge import EntitiesMerged, MergeUndone
from redstring.events.streams import document_stream
from redstring.extraction.chunking import Chunk, ChunkingResult
from redstring.extraction.domains.loader import load_schema_from_file, load_schema_from_string
from redstring.extraction.domains.models import (
    ConfidenceThresholds,
    DomainSchema,
    EntityTypeSchema,
    PropertySchema,
    RelationshipTypeSchema,
)
from redstring.extraction.errors import ChunkerError, ChunkingError, ChunkSizeError
from redstring.extraction.pipeline import (
    DEFAULT_SYSTEM_PROMPT,
    ExtractionPipeline,
    PartialExtractionError,
    PipelineResult,
)
from redstring.extraction.prompt_generator import domain_system_prompt
from redstring.extraction.protocols import Chunker
from redstring.graph.adapters.memory import InMemoryGraphStore
from redstring.llm.adapters.fake import EMPTY, FakeLlmProvider, Response
from redstring.llm.adapters.fake_embedding import FakeEmbeddingProvider
from redstring.ports.embedding_provider import EmbeddingProvider
from redstring.ports.graph_store import (
    AliasStore,
    EntityReader,
    EntityWriter,
    GraphStore,
    RelationshipStore,
    TenantPurge,
)
from redstring.ports.llm_provider import LlmProvider
from redstring.ports.vector_store import VectorStore
from redstring.projections import GraphProjection, ReplayReport, VectorProjection, project
from redstring.temporal.inference import InferredRelation, infer_relations
from redstring.temporal.query import CursorStalledError, TemporalQuery
from redstring.vector.adapters.memory import InMemoryVectorStore

__version__ = "0.2.0"

__all__ = [
    "AUTO",
    "DEFAULT_SYSTEM_PROMPT",
    "EMPTY",
    "AdjudicationVerdict",
    "Adjudicator",
    "Alias",
    "AliasCycleError",
    "AliasStore",
    "AutoDomain",
    "Bounds",
    "CandidateFinder",
    "Chunk",
    "ChunkSizeError",
    "Chunker",
    "ChunkerError",
    "ChunkingError",
    "ChunkingResult",
    "ConfidenceThresholds",
    "ConsolidationInvariantError",
    "ConsolidationReport",
    "Consolidator",
    "CursorStalledError",
    "DatePrecision",
    "DimensionMismatchError",
    "Document",
    "DocumentExtracted",
    "DomainSchema",
    "DoubleMergeError",
    "EmbeddingProvider",
    "EmbeddingProviderError",
    "EmptyCompletionError",
    "EntitiesEmbedded",
    "EntitiesMerged",
    "Entity",
    "EntityId",
    "EntityReader",
    "EntityTypeSchema",
    "EntityWriter",
    "ExtractionMethod",
    "ExtractionPipeline",
    "FakeEmbeddingProvider",
    "FakeLlmProvider",
    "FeatureWeights",
    "GraphBuildReport",
    "GraphProjection",
    "GraphStore",
    "InMemoryGraphStore",
    "InMemoryVectorStore",
    "InferredRelation",
    "LlmProvider",
    "LlmProviderError",
    "MalformedCompletionError",
    "MergeIntoAliasError",
    "MergeUndone",
    "MissingEntityError",
    "PartialExtractionError",
    "PipelineResult",
    "PropertySchema",
    "RedstringError",
    "RefusedCompletionError",
    "Relationship",
    "RelationshipId",
    "RelationshipRedirection",
    "RelationshipStore",
    "RelationshipTypeSchema",
    "ReplayFailedError",
    "ReplayReport",
    "Response",
    "ScoredCandidate",
    "SimilarityFeatures",
    "SourceDocument",
    "SourceId",
    "TemporalExtent",
    "TemporalQuery",
    "TemporalRelation",
    "TenantId",
    "TenantPurge",
    "UncertaintyMarker",
    "UnknownDomainError",
    "UnknownMergeError",
    "VectorMatch",
    "VectorProjection",
    "VectorRecord",
    "VectorStore",
    "__version__",
    "build_graph",
    "document_stream",
    "domain_system_prompt",
    "infer_relations",
    "load_schema_from_file",
    "load_schema_from_string",
    "project",
]
