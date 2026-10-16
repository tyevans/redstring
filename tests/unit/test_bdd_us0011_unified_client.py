"""Executable BDD Acceptance Criteria for US-0011: Unified Client and Batch Ingestion.

Governed by:
- ADR-0149 (Unified Redstring Client Facade and In-Memory Indexing Architecture)
- ADR-0002 (File Length Limit)
- ADR-0003 (Blackbox Frontdoor Verification)
- ADR-0006 (Executable BDD User Stories)
- PRD-0003
- US-0011
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from redstring import (
    BatchGraphReport,
    FakeEmbeddingProvider,
    FakeLlmProvider,
    GraphBuildReport,
    Redstring,
    RetrievalResult,
    TenantId,
)
from redstring.domain.chunk_retrieval import ChunkRetrievalResult

TWO_PEOPLE = {
    "entities": [
        {"name": "Ada Lovelace", "entity_type": "Person"},
        {"name": "Charles Babbage", "entity_type": "Person"},
    ],
    "relationships": [
        {
            "source_name": "Ada Lovelace",
            "target_name": "Charles Babbage",
            "relationship_type": "COLLABORATED_WITH",
        }
    ],
}


class BoundedConcurrencyTrackingProvider:
    """Tracks active concurrent extractions and records peak concurrency."""

    def __init__(self, answer: dict[str, Any], delay: float = 0.02) -> None:
        self._inner = FakeLlmProvider(by_substring={}, default=answer)
        self.delay = delay
        self.active = 0
        self.max_observed_active = 0
        self.model = "tracking-provider-v1"

    async def extract[S](
        self,
        text: str,
        schema: type[S],
        *,
        system_prompt: str | None = None,
    ) -> S:
        self.active += 1
        self.max_observed_active = max(self.max_observed_active, self.active)
        try:
            if self.delay > 0:
                await asyncio.sleep(self.delay)
            return await self._inner.extract(text, schema, system_prompt=system_prompt)
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_scenario_instantiate_in_memory_client_facade() -> None:
    """Scenario: Instantiate in-memory client facade (US-0011)."""
    # Given an LLM provider and an embedding provider
    provider = FakeLlmProvider(by_substring={}, default=TWO_PEOPLE)
    embeddings = FakeEmbeddingProvider(dimension=32)
    tenant_id = TenantId(uuid4())

    # When I instantiate a Redstring client using in_memory factory
    client = Redstring.in_memory(
        provider=provider,
        embeddings=embeddings,
        tenant_id=tenant_id,
    )

    # Then the client provides coordinated graph, vector, and chunk stores
    assert client.graph_store is not None
    assert client.vector_store is not None
    assert client.chunk_store is not None
    assert client.vector_store.dimension == embeddings.dimension
    assert client.chunk_store.dimension == embeddings.dimension

    # And the client exposes unified build_graph, retrieve_entities, and retrieve_passages methods
    assert callable(client.build_graph)
    assert callable(client.build_graph_batch)
    assert callable(client.retrieve_entities)
    assert callable(client.retrieve_passages)


@pytest.mark.asyncio
async def test_scenario_ingest_plain_text_string_into_graph() -> None:
    """Scenario: Ingest plain text string into graph (US-0011)."""
    # Given a Redstring client configured with an LLM provider
    provider = FakeLlmProvider(by_substring={}, default=TWO_PEOPLE)
    embeddings = FakeEmbeddingProvider(dimension=32)
    tenant_id = TenantId(uuid4())
    client = Redstring.in_memory(provider=provider, embeddings=embeddings, tenant_id=tenant_id)

    # When I call build_graph with a plain text string
    raw_text = "Ada Lovelace collaborated with Charles Babbage on the Analytical Engine."
    report = await client.build_graph(raw_text)

    # Then the text is automatically coerced to a SourceDocument with a content-addressed ID
    assert isinstance(report, GraphBuildReport)
    assert report.event is not None
    assert report.event.source_id.startswith("doc-")

    # And entities and relationships are extracted and stored in the graph
    entities = await client.graph.find_entities(tenant_id)
    assert len(entities) == 2
    relationships = await client.graph.get_relationships(entities[0].id, tenant_id)
    assert len(relationships) == 1


@pytest.mark.asyncio
async def test_scenario_ingest_document_collection_concurrently_with_bounded_semaphore() -> None:
    """Scenario: Ingest document collection concurrently with bounded semaphore (US-0011)."""
    # Given a Redstring client and a collection of 5 documents
    provider = BoundedConcurrencyTrackingProvider(TWO_PEOPLE, delay=0.03)
    embeddings = FakeEmbeddingProvider(dimension=32)
    tenant_id = TenantId(uuid4())
    client = Redstring.in_memory(
        provider=provider,  # type: ignore[arg-type]
        embeddings=embeddings,
        tenant_id=tenant_id,
    )

    documents = [
        f"Document {i}: Ada Lovelace and Charles Babbage in London session {i}." for i in range(5)
    ]

    # When I call build_graph_batch with max_concurrency 2
    report = await client.build_graph_batch(documents, max_concurrency=2)

    # Then all 5 documents are extracted concurrently without exceeding concurrency limits
    assert isinstance(report, BatchGraphReport)
    assert report.total_documents == 5
    assert report.successful_documents == 5
    assert report.failed_documents == 0
    assert provider.max_observed_active <= 2

    # And a BatchGraphReport is returned containing aggregated counts of documents,
    # entities, and chunks
    assert report.entities == 10
    assert report.relationships == 5
    assert len(report.reports) == 5


@pytest.mark.asyncio
async def test_scenario_retrieve_entities_and_passages_through_unified_facade() -> None:
    """Scenario: Retrieve entities and passages through unified facade (US-0011)."""
    # Given a Redstring client with ingested documents
    provider = FakeLlmProvider(by_substring={}, default=TWO_PEOPLE)
    embeddings = FakeEmbeddingProvider(dimension=32)
    tenant_id = TenantId(uuid4())
    client = Redstring.in_memory(provider=provider, embeddings=embeddings, tenant_id=tenant_id)

    doc_text = "Ada Lovelace collaborated with Charles Babbage on the Analytical Engine."
    await client.build_graph(doc_text)

    # When I query retrieve_entities for a known entity name
    entity_result = await client.retrieve_entities("Ada Lovelace", k=5)

    # And I query retrieve_passages for a known text keyword
    passage_result = await client.retrieve_passages("Analytical Engine", k=5)

    # Then ranked entities and ranked chunks are returned with fusion scores
    assert isinstance(entity_result, RetrievalResult)
    assert len(entity_result.matches) > 0
    top_entity = entity_result.matches[0].entity
    assert top_entity.name in {"Ada Lovelace", "Charles Babbage"}

    assert isinstance(passage_result, ChunkRetrievalResult)
    assert len(passage_result.matches) > 0
    top_chunk = passage_result.matches[0].chunk
    assert "Ada Lovelace" in top_chunk.text
