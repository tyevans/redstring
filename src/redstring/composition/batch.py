"""Concurrent batch document ingestion."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple

from redstring.composition.build_graph import (
    AutoDomain,
    GraphBuildReport,
    build_graph,
)
from redstring.composition.coercion import coerce_document
from redstring.domain.exceptions import DimensionMismatchError
from redstring.domain.ids import SourceId
from redstring.extraction.carryover import DEFAULT_CARRYOVER_ENTITIES

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime

    from eventsource.ports.store import AggregateStore

    from redstring.domain.ids import TenantId
    from redstring.domain.source import SourceDocument
    from redstring.extraction.domains.models import DomainSchema
    from redstring.extraction.protocols import Chunker
    from redstring.ports.chunk_store import ChunkStore
    from redstring.ports.embedding_provider import EmbeddingProvider
    from redstring.ports.graph_store import GraphStore
    from redstring.ports.llm_provider import LlmProvider
    from redstring.ports.vector_store import VectorStore

__all__ = [
    "BatchDocumentError",
    "BatchGraphReport",
    "build_graph_batch",
]


class BatchDocumentError(NamedTuple):
    """An error that occurred during document processing in a batch ingestion run."""

    source_id: SourceId
    error: Exception


@dataclass(frozen=True, slots=True)
class BatchGraphReport:
    """Aggregated outcome of a `build_graph_batch` run."""

    total_documents: int
    successful_documents: int
    failed_documents: int
    entities: int
    relationships: int
    total_chunks: int
    failed_chunks: int
    chunks_written: int
    embedded: int
    reports: tuple[GraphBuildReport, ...] = ()
    errors: tuple[BatchDocumentError, ...] = ()


async def build_graph_batch(
    documents: Sequence[SourceDocument | str | dict[str, Any]],
    *,
    provider: LlmProvider,
    store: GraphStore,
    tenant_id: TenantId,
    max_concurrency: int = 4,
    continue_on_error: bool = True,
    on_document_complete: Callable[[SourceDocument, GraphBuildReport], None] | None = None,
    on_document_error: Callable[[Any, Exception], None] | None = None,
    domain: str | DomainSchema | AutoDomain | None = None,
    chunker: Chunker | None = None,
    skip_failed_chunks: bool = False,
    allow_partial: bool = False,
    carryover_entities: int = DEFAULT_CARRYOVER_ENTITIES,
    gleanings: int = 0,
    constrain_to_domain: bool = False,
    embedding_provider: EmbeddingProvider | None = None,
    vector_store: VectorStore | None = None,
    chunks: ChunkStore | None = None,
    event_store: AggregateStore | None = None,
    observed_at: datetime | None = None,
    concurrency: int = 1,
) -> BatchGraphReport:
    """Ingest a collection of documents concurrently with bounded concurrency.

    Args:
        documents: Documents, strings, or dictionaries to ingest.
        provider: LLM provider for extraction.
        store: Graph store for extracted entities and relations.
        tenant_id: Applied to every entity, relationship and store call.
        max_concurrency: Maximum number of documents extracted concurrently. Must be >= 1.
        continue_on_error: If True, document failures are recorded in `report.errors`
            while remaining documents continue processing. If False, the first
            exception is raised immediately.
        on_document_complete: Optional callback invoked after each successfully ingested document.
        on_document_error: Optional callback invoked whenever a document ingestion fails.
        domain: Domain schema, name, or AUTO.
        chunker: Document chunker.
        skip_failed_chunks: Skip failed chunks within extraction pipeline.
        allow_partial: Allow partial extraction results.
        carryover_entities: Entity carryover count between chunks.
        gleanings: Number of gleaning extraction passes.
        constrain_to_domain: Constrain extraction to schema domain.
        embedding_provider: Embedding provider for vector store.
        vector_store: Vector store for entity embeddings.
        chunks: Chunk store for document passages.
        event_store: Aggregate event store.
        observed_at: Observation timestamp stamped onto entities.
        concurrency: Chunk-level concurrency within each document.

    Returns:
        A BatchGraphReport aggregating outcomes and statistics across all documents.
    """
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1")

    if (
        embedding_provider is not None
        and vector_store is not None
        and embedding_provider.dimension != vector_store.dimension
    ):
        raise DimensionMismatchError(
            expected=vector_store.dimension, actual=embedding_provider.dimension
        )
    if (
        embedding_provider is not None
        and chunks is not None
        and embedding_provider.dimension != chunks.dimension
    ):
        raise DimensionMismatchError(expected=chunks.dimension, actual=embedding_provider.dimension)

    if not documents:
        return BatchGraphReport(
            total_documents=0,
            successful_documents=0,
            failed_documents=0,
            entities=0,
            relationships=0,
            total_chunks=0,
            failed_chunks=0,
            chunks_written=0,
            embedded=0,
            reports=(),
            errors=(),
        )

    semaphore = asyncio.Semaphore(max_concurrency)

    async def _process_item(
        index: int,
        item: SourceDocument | str | dict[str, Any],
    ) -> tuple[int, GraphBuildReport | None, BatchDocumentError | None]:
        async with semaphore:
            try:
                doc = coerce_document(item)
            except Exception as exc:
                err = BatchDocumentError(source_id=SourceId(f"item-{index}"), error=exc)
                if not continue_on_error:
                    raise
                if on_document_error is not None:
                    on_document_error(item, exc)
                return index, None, err

            try:
                report = await build_graph(
                    doc,
                    provider=provider,
                    store=store,
                    tenant_id=tenant_id,
                    domain=domain,
                    chunker=chunker,
                    skip_failed_chunks=skip_failed_chunks,
                    allow_partial=allow_partial,
                    carryover_entities=carryover_entities,
                    gleanings=gleanings,
                    constrain_to_domain=constrain_to_domain,
                    embedding_provider=embedding_provider,
                    vector_store=vector_store,
                    chunks=chunks,
                    event_store=event_store,
                    observed_at=observed_at,
                    concurrency=concurrency,
                )
                if on_document_complete is not None:
                    on_document_complete(doc, report)
                return index, report, None
            except Exception as exc:
                err = BatchDocumentError(source_id=doc.id, error=exc)
                if not continue_on_error:
                    raise
                if on_document_error is not None:
                    on_document_error(doc, exc)
                return index, None, err

    results = await asyncio.gather(*[_process_item(i, item) for i, item in enumerate(documents)])
    results.sort(key=lambda r: r[0])

    reports: list[GraphBuildReport] = []
    errors: list[BatchDocumentError] = []

    for _, rep, err in results:
        if rep is not None:
            reports.append(rep)
        if err is not None:
            errors.append(err)

    return BatchGraphReport(
        total_documents=len(documents),
        successful_documents=len(reports),
        failed_documents=len(errors),
        entities=sum(r.entities for r in reports),
        relationships=sum(r.relationships for r in reports),
        total_chunks=sum(r.total_chunks for r in reports),
        failed_chunks=sum(r.failed_chunks for r in reports),
        chunks_written=sum(r.chunks_written for r in reports),
        embedded=sum(r.embedded for r in reports),
        reports=tuple(reports),
        errors=tuple(errors),
    )
