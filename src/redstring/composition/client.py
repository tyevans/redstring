"""The Redstring unified client facade."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from redstring.chunks.adapters.memory import InMemoryChunkStore
from redstring.composition.batch import (
    BatchDocumentError,
    BatchGraphReport,
    build_graph_batch,
    build_graph_stream,
)
from redstring.composition.build_graph import (
    ConsolidationReport,
    Consolidator,
    GraphBuildReport,
    build_graph,
)
from redstring.composition.client_diagnostics import _RedstringDiagnosticsMixin
from redstring.composition.coercion import coerce_document
from redstring.composition.retrieval import ChunkRetriever, Retriever
from redstring.consolidation.policy import HIGH_SIMILARITY, LOW_SIMILARITY
from redstring.domain.entity import Entity
from redstring.domain.ids import EntityId, TenantId
from redstring.domain.normalization import normalize_name
from redstring.graph.adapters.memory import InMemoryGraphStore
from redstring.ports.lifecycle import AsyncClosable
from redstring.vector.adapters.memory import InMemoryVectorStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from redstring.consolidation.protocols import CandidateSource, MergeAdjudicator
    from redstring.domain.chunk_retrieval import ChunkRetrievalResult
    from redstring.domain.retrieval import RetrievalMode, RetrievalResult
    from redstring.domain.source import SourceDocument
    from redstring.ports.chunk_store import ChunkStore
    from redstring.ports.embedding_provider import EmbeddingProvider
    from redstring.ports.graph_store import GraphStore
    from redstring.ports.llm_provider import LlmProvider
    from redstring.ports.vector_store import VectorStore

__all__ = ["Redstring"]


class Redstring(_RedstringDiagnosticsMixin):
    """Unified client facade coordinating graph, vector, and chunk storage,

    entity extraction, concurrent batch ingestion, hybrid retrieval, and consolidation.
    """

    def __init__(
        self,
        *,
        graph_store: GraphStore,
        provider: LlmProvider,
        vector_store: VectorStore | None = None,
        chunk_store: ChunkStore | None = None,
        embeddings: EmbeddingProvider | None = None,
        tenant_id: TenantId | None = None,
    ) -> None:
        self._graph_store = graph_store
        self._provider = provider
        self._vector_store = vector_store
        self._chunk_store = chunk_store
        self._embeddings = embeddings
        self._tenant_id = tenant_id if tenant_id is not None else TenantId(uuid4())
        self._consolidator = Consolidator(self._graph_store, vector_store=self._vector_store)

        if self._embeddings is not None and self._vector_store is not None:
            self._retriever = Retriever(
                embeddings=self._embeddings,
                vectors=self._vector_store,
                graph=self._graph_store,
            )
        else:
            self._retriever = Retriever.lexical_only(graph=self._graph_store)

        if self._chunk_store is not None:
            if self._embeddings is not None:
                self._chunk_retriever: ChunkRetriever | None = ChunkRetriever(
                    embeddings=self._embeddings,
                    chunks=self._chunk_store,
                )
            else:
                self._chunk_retriever = ChunkRetriever.lexical_only(chunks=self._chunk_store)
        else:
            self._chunk_retriever = None

    @classmethod
    def in_memory(
        cls,
        *,
        provider: LlmProvider,
        embeddings: EmbeddingProvider | None = None,
        tenant_id: TenantId | None = None,
        dimension: int | None = None,
    ) -> Redstring:
        """Create a fully-wired, in-memory Redstring client with coordinated stores."""
        dim = dimension or (embeddings.dimension if embeddings is not None else 1536)
        graph_store = InMemoryGraphStore()
        vector_store = (
            InMemoryVectorStore(dimension=dim)
            if embeddings is not None or dimension is not None
            else None
        )
        chunk_store = (
            InMemoryChunkStore(dimension=dim)
            if embeddings is not None or dimension is not None
            else None
        )
        return cls(
            graph_store=graph_store,
            provider=provider,
            vector_store=vector_store,
            chunk_store=chunk_store,
            embeddings=embeddings,
            tenant_id=tenant_id,
        )

    @classmethod
    def connect(
        cls,
        *,
        graph_store: GraphStore,
        provider: LlmProvider,
        vector_store: VectorStore | None = None,
        chunk_store: ChunkStore | None = None,
        embeddings: EmbeddingProvider | None = None,
        tenant_id: TenantId | None = None,
    ) -> Redstring:
        """Connect to existing stores."""
        return cls(
            graph_store=graph_store,
            provider=provider,
            vector_store=vector_store,
            chunk_store=chunk_store,
            embeddings=embeddings,
            tenant_id=tenant_id,
        )

    def for_tenant(self, tenant_id: TenantId) -> Redstring:
        """Return a client instance bound to `tenant_id`."""
        return Redstring(
            graph_store=self._graph_store,
            provider=self._provider,
            vector_store=self._vector_store,
            chunk_store=self._chunk_store,
            embeddings=self._embeddings,
            tenant_id=tenant_id,
        )

    @property
    def tenant_id(self) -> TenantId:
        return self._tenant_id

    @property
    def graph_store(self) -> GraphStore:
        return self._graph_store

    @property
    def graph(self) -> GraphStore:
        return self._graph_store

    @property
    def vector_store(self) -> VectorStore | None:
        return self._vector_store

    @property
    def vectors(self) -> VectorStore | None:
        return self._vector_store

    @property
    def chunk_store(self) -> ChunkStore | None:
        return self._chunk_store

    @property
    def chunks(self) -> ChunkStore | None:
        return self._chunk_store

    @property
    def provider(self) -> LlmProvider:
        return self._provider

    @property
    def embeddings(self) -> EmbeddingProvider | None:
        return self._embeddings

    @property
    def consolidator(self) -> Consolidator:
        return self._consolidator

    def _active_tenant(self, tenant_id: TenantId | None) -> TenantId:
        return tenant_id if tenant_id is not None else self._tenant_id

    async def build_graph(
        self,
        doc_or_text: SourceDocument | str | dict[str, Any],
        *,
        tenant_id: TenantId | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> GraphBuildReport:
        """Extract a document or text and write into the graph and vector stores."""
        return await build_graph(
            coerce_document(doc_or_text),
            provider=self._provider,
            store=self._graph_store,
            tenant_id=self._active_tenant(tenant_id),
            vector_store=self._vector_store,
            embedding_provider=self._embeddings,
            chunks=self._chunk_store,
            **kwargs,
        )

    async def build_graph_batch(
        self,
        docs_or_texts: Sequence[SourceDocument | str | dict[str, Any]],
        *,
        max_concurrency: int = 4,
        continue_on_error: bool = True,
        tenant_id: TenantId | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> BatchGraphReport:
        """Ingest a collection of documents concurrently with bounded concurrency."""
        return await build_graph_batch(
            docs_or_texts,
            provider=self._provider,
            store=self._graph_store,
            tenant_id=self._active_tenant(tenant_id),
            vector_store=self._vector_store,
            embedding_provider=self._embeddings,
            chunks=self._chunk_store,
            max_concurrency=max_concurrency,
            continue_on_error=continue_on_error,
            **kwargs,
        )

    def build_graph_stream(
        self,
        docs_or_texts: Sequence[SourceDocument | str | dict[str, Any]],
        *,
        max_concurrency: int = 4,
        continue_on_error: bool = True,
        tenant_id: TenantId | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> AsyncIterator[GraphBuildReport | BatchDocumentError]:
        """Stream `GraphBuildReport` results incrementally as each document completes."""
        return build_graph_stream(
            docs_or_texts,
            provider=self._provider,
            store=self._graph_store,
            tenant_id=self._active_tenant(tenant_id),
            vector_store=self._vector_store,
            embedding_provider=self._embeddings,
            chunks=self._chunk_store,
            max_concurrency=max_concurrency,
            continue_on_error=continue_on_error,
            **kwargs,
        )

    async def retrieve_entities(
        self,
        query: str,
        *,
        k: int = 5,
        entity_types: Sequence[str] | None = None,
        mode: RetrievalMode | None = None,
        tenant_id: TenantId | None = None,
    ) -> RetrievalResult:
        """Retrieve ranked entities matching the query."""
        return await self._retriever.retrieve(
            query,
            self._active_tenant(tenant_id),
            k=k,
            entity_types=entity_types,
            mode=mode,
        )

    async def retrieve_passages(
        self,
        query: str,
        *,
        k: int = 5,
        mode: RetrievalMode | None = None,
        tenant_id: TenantId | None = None,
    ) -> ChunkRetrievalResult:
        """Retrieve ranked passages matching the query from the chunk store."""
        if self._chunk_retriever is None:
            raise ValueError("No chunk store configured for this Redstring client")
        return await self._chunk_retriever.retrieve_chunks(
            query, self._active_tenant(tenant_id), k=k, mode=mode
        )

    async def neighbors(
        self,
        entity_id_or_name: EntityId | str,
        *,
        depth: int = 1,
        relationship_types: Sequence[str] | None = None,
        tenant_id: TenantId | None = None,
    ) -> list[Entity]:
        """Return entities reachable from entity_id or name within depth hops."""
        active_tenant = self._active_tenant(tenant_id)
        if isinstance(entity_id_or_name, str):
            matches = await self._graph_store.find_entities(
                active_tenant, name=normalize_name(entity_id_or_name), limit=1
            )
            if not matches:
                return []
            target_id = matches[0].id
        else:
            target_id = entity_id_or_name

        return await self._graph_store.neighbors(
            target_id, active_tenant, depth=depth, relationship_types=relationship_types
        )

    async def consolidate(
        self,
        subject: Entity | EntityId,
        *,
        finder: CandidateSource | None = None,
        adjudicator: MergeAdjudicator | None = None,
        high: float = HIGH_SIMILARITY,
        low: float = LOW_SIMILARITY,
        tenant_id: TenantId | None = None,
    ) -> ConsolidationReport | None:
        """Resolve duplicate candidate entities for subject."""
        if not isinstance(subject, Entity):
            entity = await self._graph_store.get_entity(subject, self._active_tenant(tenant_id))
            if entity is None:
                return None
            subject_entity = entity
        else:
            subject_entity = subject

        return await self._consolidator.resolve(
            subject_entity, finder=finder, adjudicator=adjudicator, high=high, low=low
        )

    async def close(self) -> None:
        """Close any open stores and providers implementing AsyncClosable."""
        resources = (
            self._graph_store,
            self._vector_store,
            self._chunk_store,
            self._embeddings,
            self._provider,
        )
        for resource in resources:
            if isinstance(resource, AsyncClosable):
                await resource.close()

    async def __aenter__(self) -> Redstring:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        await self.close()
