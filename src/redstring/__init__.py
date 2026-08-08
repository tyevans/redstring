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
  `index_documents` and `IndexReport` are the other write path: it splits
  documents into a `ChunkStore` and asks no model anything, so a corpus is
  affordable for every document a caller holds and extraction can be paid for
  over whichever subset is worth it.
- **Ranking passages.** `tokenize` decides what counts as a term.
  `rank_chunks` scores a store's `LexicalCandidates` with BM25 and returns
  `RankedChunk`s, best first -- the scorer is pure so two `ChunkStore`
  adapters, asked for the same candidates and statistics, rank identically.
  `LexicalCandidate` and `CorpusStats` are what an adapter hands back;
  `ChunkStore.lexical_candidates` is where a caller asks for them.
- **Retrieval.** `Retriever` turns a query string into ranked entities,
  fusing a semantic channel over `VectorStore` with a lexical one over
  `GraphStore`'s blocking keys. `RetrievalMode` picks the channels,
  `RetrievalResult` and `ScoredEntity` are what comes back. Note the
  scale: `ScoredEntity.score` is a fused *rank* score, ordinal and
  unbounded, and is not on `VectorMatch`'s 0..1.
- **What you put in.** `SourceDocument`.
- **What comes out.** `Entity`, `Relationship`, `Alias`, `ExtractionMethod`,
  `TemporalExtent` (with `DatePrecision` and `UncertaintyMarker`),
  `VectorRecord`, `VectorMatch`, `StoredChunk`, and the `DocumentExtracted`,
  `EntitiesEmbedded` and `DocumentChunked` events carrying them. `EntityId`, `RelationshipId`,
  `TenantId` and `SourceId` are the id vocabulary -- the first three are
  `UUID`, the last is `str`. `ChunkId` joins them for a stored passage, and
  is a `str`: a chunk is identified by the digest of its source and its text.
- **Ports.** `GraphStore`, `VectorStore`, `ChunkStore`, `Cache`,
  `LlmProvider`, `EmbeddingProvider`, `Chunker`. Implement one to plug in a
  backend of your own; the compliance suite in `tests/compliance` is what
  says whether you got it right.

  Three of them are **composed from capability protocols, and those are
  exported too**: `GraphStore` from `EntityReader`, `EntityWriter`,
  `AliasStore`, `RelationshipStore` and `TenantPurge`; `ChunkStore` from
  `ChunkWriter`, `ChunkReader`, `LexicalCandidateSource` and `ChunkPurge`;
  `Cache` from `KeyValueCache` and `HitWindow`. Implement the composed port;
  *depend* on the narrowest capability you actually call. The split is what
  lets `ChunkProjection` need one method rather than nine, and what lets a
  caller supply BM25 recall from an index that is not a chunk store at all.
- **Adapters.** `InMemoryGraphStore`, `InMemoryVectorStore` and
  `InMemoryChunkStore` are complete
  implementations, not test doubles -- suitable for a single-process job.
  `Neo4jGraphStore` and `PgVectorStore` need their extras
  (`redstring[neo4j]` and `redstring[pgvector]`).
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
  or a `DomainSchema` of your own. `list_available_domains` is how you find out
  which ids are bundled, returning a `DomainSummary` each -- without it the
  supported way to discover them is to pass a wrong one and read
  `UnknownDomainError`. `load_schema_from_file` and
  `load_schema_from_string` build one, out of `EntityTypeSchema`,
  `RelationshipTypeSchema`, `PropertySchema` and `ConfidenceThresholds`.
- **Pieces, for callers who want the steps rather than the whole.**
  `ExtractionPipeline` (`PipelineResult`, `DEFAULT_SYSTEM_PROMPT`),
  `Chunk`/`ChunkingResult`, `GraphProjection`, `VectorProjection`,
  `ChunkProjection`, and
  `Document` with `document_stream` to address it.
- **Errors.** `RedstringError` and everything under it that a caller can
  reach: `LlmProviderError` and its three shapes, `MissingEntityError`,
  `AliasCycleError`, `DimensionMismatchError`, `PartialExtractionError`,
  and the chunking three.

**The rebuild driver is `eventsource.replay`, not ours.** `project`/`replay`,
`ReplayReport`, `ReplayFailure` and `ReplayFailedError` were exported here
while `eventsource-py` had no rebuild driver. They were reported upstream and
landed in 0.12.0, so they are gone from this surface rather than re-exported
from it -- a caller writes `from eventsource import replay`, which is the same
choice this module makes everywhere else: depending on another package openly
beats republishing its names under ours. `redstring.projections` says what the
upstream version does that this one did not.

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

  `Consolidator.resolve` is typed against `CandidateSource` and
  `MergeAdjudicator` rather than those two classes. Both are single-method
  protocols, and they are what make the docstring's "supply one to change the
  weights or the blocking" a real offer: substituting your own search index
  for the blocking, or a human review queue for the model, no longer means
  subclassing a class whose constructor demands collaborators you do not
  have. `CandidateFinder` and `Adjudicator` remain the defaults.
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
appends `report.event` and drives `eventsource.replay` over the feed instead. The
separation is not decoration -- it is why a store can be rebuilt, and
`tests/unit/projections/test_replay_equivalence.py` is what proves it can.
"""

from redstring.aggregates.document import Document
from redstring.chunks.adapters.memory import InMemoryChunkStore
from redstring.composition import (
    AUTO,
    AutoDomain,
    ConsolidationReport,
    Consolidator,
    GraphBuildReport,
    IndexReport,
    Retriever,
    build_graph,
    index_documents,
)
from redstring.consolidation.candidates import CandidateFinder, ScoredCandidate
from redstring.consolidation.policy import AdjudicationVerdict, Adjudicator
from redstring.consolidation.protocols import CandidateSource, MergeAdjudicator
from redstring.domain.alias import Alias
from redstring.domain.bm25 import CorpusStats
from redstring.domain.chunk import ChunkId, StoredChunk
from redstring.domain.chunk_ranking import (
    LexicalCandidate,
    LexicalCandidates,
    RankedChunk,
    rank_chunks,
)
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
    UnknownDomainError,
    UnknownMergeError,
)
from redstring.domain.ids import EntityId, RelationshipId, SourceId, TenantId
from redstring.domain.interval import Bounds, TemporalRelation
from redstring.domain.relationship import Relationship
from redstring.domain.retrieval import RetrievalMode, RetrievalResult, ScoredEntity
from redstring.domain.similarity import FeatureWeights, SimilarityFeatures
from redstring.domain.source import SourceDocument
from redstring.domain.temporal import DatePrecision, TemporalExtent, UncertaintyMarker
from redstring.domain.tokenize import tokenize
from redstring.domain.vector import VectorMatch, VectorRecord
from redstring.events.document import DocumentChunked, DocumentExtracted, EntitiesEmbedded
from redstring.events.merge import EntitiesMerged, MergeUndone
from redstring.events.streams import document_stream
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
from redstring.ports.llm_provider import LlmProvider
from redstring.ports.vector_store import VectorStore
from redstring.projections import ChunkProjection, GraphProjection, VectorProjection
from redstring.temporal.inference import InferredRelation, infer_relations
from redstring.temporal.query import CursorStalledError, TemporalQuery
from redstring.vector.adapters.memory import InMemoryVectorStore

__version__ = "0.3.0"

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
    "Cache",
    "CandidateFinder",
    "CandidateSource",
    "Chunk",
    "ChunkId",
    "ChunkProjection",
    "ChunkPurge",
    "ChunkReader",
    "ChunkSizeError",
    "ChunkStore",
    "ChunkWriter",
    "Chunker",
    "ChunkerError",
    "ChunkingError",
    "ChunkingResult",
    "ConfidenceThresholds",
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
    "MissingEntityError",
    "PartialExtractionError",
    "PipelineResult",
    "PropertySchema",
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
    "ScoredEntity",
    "SimilarityFeatures",
    "SourceDocument",
    "SourceId",
    "StoredChunk",
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
    "index_documents",
    "infer_relations",
    "list_available_domains",
    "load_schema_from_file",
    "load_schema_from_string",
    "rank_chunks",
    "tokenize",
]
