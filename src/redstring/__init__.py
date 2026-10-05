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

`docs/examples/build_a_graph.py` is runnable and verified by
`tests/unit/test_end_to_end_example.py`.

## What is supported

Everything named in `__all__` below is the supported public surface. Dotted paths
are internal and may change without notice, with four exceptions for deployed adapters:

    redstring.graph.adapters.neo4j.Neo4jGraphStore
    redstring.vector.adapters.pgvector.PgVectorStore
    redstring.llm.adapters.langchain.LangChainLlmProvider
    redstring.llm.adapters.langchain_embedding.LangChainEmbeddingProvider

These four import paths are stable (ADR 0147) and tested by
`tests/unit/test_deployed_adapter_paths_are_stable.py`. They are not exported here
to avoid mandatory runtime dependencies on optional adapter packages (`neo4j`, `pgvector`, `llm`).

The public surface is closed and self-contained (ADR 0106):
- **Composition & Retrieval:** `build_graph`, `index_documents`, `summarize_themes`,
  `Retriever`, `ChunkRetriever`, and report types.
- **Ports & Protocols:** `GraphStore`, `VectorStore`, `ChunkStore`, `Cache`,
  `LlmProvider`, `EmbeddingProvider`, `Chunker`, and capability protocols (`AsyncClosable`).
- **Domain & Events:** `SourceDocument`, `Entity`, `Relationship`, `StoredChunk`,
  `DocumentExtracted`, `EntitiesEmbedded`, `DocumentChunked`, `EntitiesMerged`.
- **Adapters & Providers:** In-memory stores (`InMemoryGraphStore`, `InMemoryVectorStore`,
  `InMemoryChunkStore`) and test doubles (`FakeLlmProvider`, `FakeEmbeddingProvider`).
- **Domain Schemas & Extraction:** `DomainSchema`, `load_schema_from_file`,
  `load_schema_from_string`, `domain_system_prompt`, `ExtractionPipeline`.
- **Exceptions:** `RedstringError` and all public domain and provider error subtypes.

The write model is event-sourced: extraction emits events folded into stores by projections.
For replay, use `from eventsource import replay`.
"""

from redstring.aggregates.document import Document
from redstring.chunks.adapters.memory import InMemoryChunkStore
from redstring.composition import (
    AUTO,
    AutoDomain,
    ChunkRetriever,
    ConsolidationReport,
    Consolidator,
    GraphBuildReport,
    IndexReport,
    Retriever,
    Theme,
    ThemeReport,
    build_graph,
    index_documents,
    summarize_themes,
)
from redstring.consolidation.candidates import CandidateFinder, ScoredCandidate
from redstring.consolidation.policy import AdjudicationVerdict, Adjudicator
from redstring.consolidation.protocols import CandidateSource, ConsolidationGraph, MergeAdjudicator
from redstring.domain.alias import Alias
from redstring.domain.bm25 import CorpusStats
from redstring.domain.chunk import ChunkId, StoredChunk
from redstring.domain.chunk_ranking import (
    LexicalCandidate,
    LexicalCandidates,
    RankedChunk,
    rank_chunks,
)
from redstring.domain.chunk_retrieval import (
    ChunkRetrievalResult,
    ScoredChunk,
    SemanticCandidate,
)
from redstring.domain.consolidation import (
    MergeableFields,
    PropertyResolution,
    RelationshipRedirection,
)
from redstring.domain.entity import Entity
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
    TaskPrefixMismatchError,
    UnknownDomainError,
    UnknownMergeError,
    UnstructuredCompletionError,
    VectorProvenanceMismatchError,
)
from redstring.domain.ids import EntityId, RelationshipId, SourceId, TenantId
from redstring.domain.interval import Bounds, TemporalRelation
from redstring.domain.limiter import CallLimiter
from redstring.domain.merge_strategy import PropertyMergePolicy, PropertyMergeStrategy
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.relationship import Relationship
from redstring.domain.retrieval import RetrievalMode, RetrievalResult, ScoredEntity
from redstring.domain.similarity import FeatureWeights, SimilarityFeatures
from redstring.domain.source import SourceDocument
from redstring.domain.temporal import DatePrecision, TemporalExtent, UncertaintyMarker
from redstring.domain.tokenize import tokenize
from redstring.domain.vector import VectorMatch, VectorProvenance, VectorRecord
from redstring.events.document import DocumentChunked, DocumentExtracted, EntitiesEmbedded
from redstring.events.merge import EntitiesMerged, MergeUndone
from redstring.events.streams import document_stream
from redstring.extraction.carryover import DEFAULT_CARRYOVER_ENTITIES
from redstring.extraction.chunkers import BoundaryPreferenceChunker, SlidingWindowChunker
from redstring.extraction.chunking import Chunk, ChunkingResult
from redstring.extraction.domains.loader import load_schema_from_file, load_schema_from_string
from redstring.extraction.domains.models import (
    ConfidenceThresholds,
    DomainSchema,
    DomainSummary,
    EntityTypeSchema,
    PropertySchema,
    RelationshipTypeSchema,
)
from redstring.extraction.domains.registry import list_available_domains
from redstring.extraction.errors import ChunkerError, ChunkingError, ChunkSizeError
from redstring.extraction.pipeline import (
    DEFAULT_SYSTEM_PROMPT,
    ExtractionPipeline,
    PartialExtractionError,
    PipelineResult,
)
from redstring.extraction.prompt_generator import domain_system_prompt
from redstring.extraction.protocols import Chunker
from redstring.extraction.schema import ExtractedEntity, ExtractedRelationship, Extraction
from redstring.graph.adapters.memory import InMemoryGraphStore
from redstring.llm.adapters.fake import EMPTY, FakeLlmProvider, Response
from redstring.llm.adapters.fake_embedding import FakeEmbeddingProvider
from redstring.ports.cache import Cache, HitWindow, KeyValueCache
from redstring.ports.chunk_store import (
    ChunkPurge,
    ChunkReader,
    ChunkStore,
    ChunkWriter,
    LexicalCandidateSource,
    SemanticCandidateSource,
)
from redstring.ports.embedding_provider import EmbeddingProvider
from redstring.ports.graph_store import (
    AliasStore,
    EntityReader,
    EntityWriter,
    GraphStore,
    RelationshipStore,
    TenantPurge,
)
from redstring.ports.lifecycle import AsyncClosable
from redstring.ports.llm_provider import LlmProvider
from redstring.ports.vector_store import VectorPurge, VectorReader, VectorStore, VectorWriter
from redstring.projections import ChunkProjection, GraphProjection, VectorProjection
from redstring.temporal.inference import InferredRelation, infer_relations
from redstring.temporal.query import CursorStalledError, TemporalQuery
from redstring.vector.adapters.memory import InMemoryVectorStore

__version__ = "0.12.0"

__all__ = [
    "AUTO",
    "DEFAULT_CARRYOVER_ENTITIES",
    "DEFAULT_SYSTEM_PROMPT",
    "EMPTY",
    "AdjudicationVerdict",
    "Adjudicator",
    "Alias",
    "AliasCycleError",
    "AliasStore",
    "AsyncClosable",
    "AutoDomain",
    "BoundaryPreferenceChunker",
    "Bounds",
    "Cache",
    "CallLimiter",
    "CandidateFinder",
    "CandidateSource",
    "Chunk",
    "ChunkId",
    "ChunkProjection",
    "ChunkPurge",
    "ChunkReader",
    "ChunkRetrievalResult",
    "ChunkRetriever",
    "ChunkSizeError",
    "ChunkStore",
    "ChunkWriter",
    "Chunker",
    "ChunkerError",
    "ChunkingError",
    "ChunkingResult",
    "ConfidenceThresholds",
    "ConsolidationGraph",
    "ConsolidationInvariantError",
    "ConsolidationReport",
    "Consolidator",
    "CorpusStats",
    "CursorStalledError",
    "DatePrecision",
    "DimensionMismatchError",
    "Document",
    "DocumentChunked",
    "DocumentExtracted",
    "DomainSchema",
    "DomainSummary",
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
    "ExtractedEntity",
    "ExtractedRelationship",
    "Extraction",
    "ExtractionMethod",
    "ExtractionPipeline",
    "FakeEmbeddingProvider",
    "FakeLlmProvider",
    "FeatureWeights",
    "GraphBuildReport",
    "GraphProjection",
    "GraphStore",
    "HitWindow",
    "InMemoryChunkStore",
    "InMemoryGraphStore",
    "InMemoryVectorStore",
    "IndexReport",
    "InferredRelation",
    "KeyValueCache",
    "LexicalCandidate",
    "LexicalCandidateSource",
    "LexicalCandidates",
    "LlmProvider",
    "LlmProviderError",
    "MalformedCompletionError",
    "MergeAdjudicator",
    "MergeIntoAliasError",
    "MergeUndone",
    "MergeableFields",
    "MissingEntityError",
    "PartialExtractionError",
    "PipelineResult",
    "PropertyMergePolicy",
    "PropertyMergeStrategy",
    "PropertyResolution",
    "PropertySchema",
    "Provenance",
    "RankedChunk",
    "RedstringError",
    "RefusedCompletionError",
    "Relationship",
    "RelationshipId",
    "RelationshipRedirection",
    "RelationshipStore",
    "RelationshipTypeSchema",
    "Response",
    "RetrievalMode",
    "RetrievalResult",
    "Retriever",
    "ScoredCandidate",
    "ScoredChunk",
    "ScoredEntity",
    "SemanticCandidate",
    "SemanticCandidateSource",
    "SimilarityFeatures",
    "SlidingWindowChunker",
    "SourceDocument",
    "SourceId",
    "StoredChunk",
    "TaskPrefixMismatchError",
    "TemporalExtent",
    "TemporalQuery",
    "TemporalRelation",
    "TenantId",
    "TenantPurge",
    "Theme",
    "ThemeReport",
    "UncertaintyMarker",
    "UnknownDomainError",
    "UnknownMergeError",
    "UnstructuredCompletionError",
    "VectorMatch",
    "VectorProjection",
    "VectorProvenance",
    "VectorProvenanceMismatchError",
    "VectorPurge",
    "VectorReader",
    "VectorRecord",
    "VectorStore",
    "VectorWriter",
    "__version__",
    "build_graph",
    "document_stream",
    "domain_system_prompt",
    "index_documents",
    "infer_relations",
    "list_available_domains",
    "load_schema_from_file",
    "load_schema_from_string",
    "rank_chunks",
    "summarize_themes",
    "tokenize",
]
