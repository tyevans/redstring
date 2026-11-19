"""redstring: build a knowledge graph from documents you already have.

`docs/examples/build_a_graph.py` is runnable and verified by the unit test suite.

## What is supported

Everything named in `__all__` below is the supported public surface (ADR 0106). Dotted paths
are internal and may change without notice, with four exceptions for deployed adapters (ADR 0147):

    redstring.graph.adapters.neo4j.Neo4jGraphStore
    redstring.vector.adapters.pgvector.PgVectorStore
    redstring.llm.adapters.langchain.LangChainLlmProvider
    redstring.llm.adapters.langchain_embedding.LangChainEmbeddingProvider

They are not exported here to avoid mandatory runtime dependencies on optional extras
(`neo4j`, `pgvector`, `llm`).

- **Composition & Retrieval:** `build_graph`, `index_documents`, `summarize_themes`,
  `Retriever`, `ChunkRetriever`, `Redstring`, and diagnostic/report types.
- **Ports & Protocols:** `GraphStore`, `VectorStore`, `ChunkStore`, `Cache`,
  `LlmProvider`, `EmbeddingProvider`, `Chunker`, and capability protocols (`AsyncClosable`).
- **Domain & Events:** `SourceDocument`, `Entity`, `Relationship`, `StoredChunk`,
  `DocumentExtracted`, `EntitiesEmbedded`, `DocumentChunked`, `EntitiesMerged`.
- **Adapters & Providers:** In-memory stores and test doubles (`FakeLlmProvider`).
- **Domain Schemas & Exceptions:** `DomainSchema`, `ExtractionPipeline`, `RedstringError`.
"""

from redstring.aggregates.document import Document
from redstring.chunks.adapters.memory import InMemoryChunkStore
from redstring.composition import (
    AUTO,
    AutoDomain,
    BatchDocumentError,
    BatchGraphReport,
    ChunkRetrievalExplanation,
    ChunkRetriever,
    ConsolidationPreview,
    ConsolidationReport,
    Consolidator,
    CorpusHealthReport,
    EntityHub,
    EntityLineageReport,
    EntityModification,
    EntityRetrievalExplanation,
    ExplainedChunkMatch,
    ExplainedEntityMatch,
    ExportFormat,
    GraphBuildReport,
    GraphDiffReport,
    GraphImportReport,
    GraphTopologyReport,
    IndexReport,
    PathExplanation,
    PreviewCandidate,
    Redstring,
    RelationshipModification,
    Retriever,
    SchemaConformanceReport,
    SchemaViolation,
    SourceLineageReport,
    TenantConsolidationReport,
    Theme,
    ThemeReport,
    analyze_corpus,
    analyze_topology,
    audit_schema_conformance,
    build_graph,
    build_graph_batch,
    build_graph_stream,
    coerce_document,
    consolidate_tenant,
    diff_graphs,
    explain_chunk_retrieval,
    explain_entity_retrieval,
    explain_path,
    export_graph,
    format_graphml,
    format_json_ld,
    format_mermaid,
    format_schema_mermaid,
    import_graph,
    index_documents,
    parse_graphml,
    parse_json_ld,
    preview_consolidation,
    summarize_themes,
    trace_entity_lineage,
    trace_source_lineage,
)
from redstring.consolidation.candidates import CandidateFinder, ScoredCandidate
from redstring.consolidation.policy import AdjudicationVerdict, Adjudicator, MergeDecision
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
from redstring.domain.chunk_retrieval import ChunkRetrievalResult, ScoredChunk, SemanticCandidate
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

__version__ = "0.14.0"

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
    "BatchDocumentError",
    "BatchGraphReport",
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
    "ChunkRetrievalExplanation",
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
    "ConsolidationPreview",
    "ConsolidationReport",
    "Consolidator",
    "CorpusHealthReport",
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
    "EntityHub",
    "EntityId",
    "EntityLineageReport",
    "EntityModification",
    "EntityReader",
    "EntityRetrievalExplanation",
    "EntityTypeSchema",
    "EntityWriter",
    "ExplainedChunkMatch",
    "ExplainedEntityMatch",
    "ExportFormat",
    "ExtractedEntity",
    "ExtractedRelationship",
    "Extraction",
    "ExtractionMethod",
    "ExtractionPipeline",
    "FakeEmbeddingProvider",
    "FakeLlmProvider",
    "FeatureWeights",
    "GraphBuildReport",
    "GraphDiffReport",
    "GraphImportReport",
    "GraphProjection",
    "GraphStore",
    "GraphTopologyReport",
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
    "MergeDecision",
    "MergeIntoAliasError",
    "MergeUndone",
    "MergeableFields",
    "MissingEntityError",
    "PartialExtractionError",
    "PathExplanation",
    "PipelineResult",
    "PreviewCandidate",
    "PropertyMergePolicy",
    "PropertyMergeStrategy",
    "PropertyResolution",
    "PropertySchema",
    "Provenance",
    "RankedChunk",
    "Redstring",
    "RedstringError",
    "RefusedCompletionError",
    "Relationship",
    "RelationshipId",
    "RelationshipModification",
    "RelationshipRedirection",
    "RelationshipStore",
    "RelationshipTypeSchema",
    "Response",
    "RetrievalMode",
    "RetrievalResult",
    "Retriever",
    "SchemaConformanceReport",
    "SchemaViolation",
    "ScoredCandidate",
    "ScoredChunk",
    "ScoredEntity",
    "SemanticCandidate",
    "SemanticCandidateSource",
    "SimilarityFeatures",
    "SlidingWindowChunker",
    "SourceDocument",
    "SourceId",
    "SourceLineageReport",
    "StoredChunk",
    "TaskPrefixMismatchError",
    "TemporalExtent",
    "TemporalQuery",
    "TemporalRelation",
    "TenantConsolidationReport",
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
    "analyze_corpus",
    "analyze_topology",
    "audit_schema_conformance",
    "build_graph",
    "build_graph_batch",
    "build_graph_stream",
    "coerce_document",
    "consolidate_tenant",
    "diff_graphs",
    "document_stream",
    "domain_system_prompt",
    "explain_chunk_retrieval",
    "explain_entity_retrieval",
    "explain_path",
    "export_graph",
    "format_graphml",
    "format_json_ld",
    "format_mermaid",
    "format_schema_mermaid",
    "import_graph",
    "index_documents",
    "infer_relations",
    "list_available_domains",
    "load_schema_from_file",
    "load_schema_from_string",
    "parse_graphml",
    "parse_json_ld",
    "preview_consolidation",
    "rank_chunks",
    "summarize_themes",
    "tokenize",
    "trace_entity_lineage",
    "trace_source_lineage",
]
