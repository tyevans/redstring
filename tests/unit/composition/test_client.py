"""Tests for the Redstring unified client facade."""

from __future__ import annotations

from uuid import uuid4

import pytest

from redstring import (
    BatchGraphReport,
    FakeEmbeddingProvider,
    FakeLlmProvider,
    GraphBuildReport,
    Redstring,
    RetrievalResult,
    SourceDocument,
    SourceId,
    TenantId,
)

TWO_PEOPLE = {
    "entities": [
        {"name": "Ada Lovelace", "entity_type": "Person"},
        {"name": "Charles Babbage", "entity_type": "Person"},
    ],
    "relationships": [
        {
            "source_name": "Ada Lovelace",
            "target_name": "Charles Babbage",
            "relationship_type": "WORKED_WITH",
        }
    ],
}


@pytest.mark.asyncio
async def test_client_in_memory_initialization() -> None:
    provider = FakeLlmProvider(by_substring={}, default=TWO_PEOPLE)
    embeddings = FakeEmbeddingProvider(dimension=16)
    tenant = TenantId(uuid4())

    client = Redstring.in_memory(
        provider=provider,
        embeddings=embeddings,
        tenant_id=tenant,
    )

    assert client.tenant_id == tenant
    assert client.graph is not None
    assert client.graph_store is client.graph
    assert client.vectors is not None
    assert client.vector_store is client.vectors
    assert client.chunks is not None
    assert client.chunk_store is client.chunks
    assert client.provider is provider
    assert client.embeddings is embeddings
    assert client.consolidator is not None

    scoped = client.for_tenant(TenantId(uuid4()))
    assert scoped.tenant_id != client.tenant_id
    assert scoped.graph is client.graph
    assert scoped.vectors is client.vectors


@pytest.mark.asyncio
async def test_client_build_graph_with_plain_string_coercion() -> None:
    provider = FakeLlmProvider(by_substring={}, default=TWO_PEOPLE)
    embeddings = FakeEmbeddingProvider(dimension=16)
    tenant = TenantId(uuid4())

    client = Redstring.in_memory(
        provider=provider,
        embeddings=embeddings,
        tenant_id=tenant,
    )

    # Ingest plain text string directly
    report = await client.build_graph(
        "Ada Lovelace worked alongside Charles Babbage on computing engines."
    )
    assert isinstance(report, GraphBuildReport)
    assert report.entities == 2
    assert report.relationships == 1
    assert report.embedded == 2

    # Verify entities are in the graph store
    entities = await client.graph.find_entities(tenant)
    assert len(entities) == 2
    names = {e.name for e in entities}
    assert names == {"Ada Lovelace", "Charles Babbage"}


@pytest.mark.asyncio
async def test_client_build_graph_batch() -> None:
    provider = FakeLlmProvider(by_substring={}, default=TWO_PEOPLE)
    embeddings = FakeEmbeddingProvider(dimension=16)
    tenant = TenantId(uuid4())

    client = Redstring.in_memory(
        provider=provider,
        embeddings=embeddings,
        tenant_id=tenant,
    )

    items = [
        "Ada Lovelace wrote the first computer algorithm.",
        {"id": "doc-babbage", "text": "Charles Babbage invented the Difference Engine."},
        SourceDocument(
            id=SourceId("doc-analytical"),
            text="The Analytical Engine was general purpose.",
        ),
    ]

    report = await client.build_graph_batch(items, max_concurrency=2)
    assert isinstance(report, BatchGraphReport)
    assert report.total_documents == 3
    assert report.successful_documents == 3
    assert report.failed_documents == 0
    assert report.entities == 6
    assert report.relationships == 3


@pytest.mark.asyncio
async def test_client_retrieve_entities_and_passages() -> None:
    provider = FakeLlmProvider(by_substring={}, default=TWO_PEOPLE)
    embeddings = FakeEmbeddingProvider(dimension=16)
    tenant = TenantId(uuid4())

    client = Redstring.in_memory(
        provider=provider,
        embeddings=embeddings,
        tenant_id=tenant,
    )

    await client.build_graph("Ada Lovelace and Charles Babbage in London.")

    entity_results = await client.retrieve_entities("Ada Lovelace", k=5)
    assert isinstance(entity_results, RetrievalResult)
    assert len(entity_results.matches) > 0
    top_entity = entity_results.matches[0].entity
    assert top_entity.name in {"Ada Lovelace", "Charles Babbage"}

    passage_results = await client.retrieve_passages("London computing", k=5)
    assert len(passage_results.matches) > 0


@pytest.mark.asyncio
async def test_client_neighbors_by_name_and_id() -> None:
    provider = FakeLlmProvider(by_substring={}, default=TWO_PEOPLE)
    embeddings = FakeEmbeddingProvider(dimension=16)
    tenant = TenantId(uuid4())

    client = Redstring.in_memory(
        provider=provider,
        embeddings=embeddings,
        tenant_id=tenant,
    )

    await client.build_graph("Ada Lovelace worked with Charles Babbage.")

    # Query neighbors by entity name string
    neighbors_by_name = await client.neighbors("Ada Lovelace", depth=1)
    assert len(neighbors_by_name) == 1
    assert neighbors_by_name[0].name == "Charles Babbage"

    # Query neighbors by EntityId
    ada_entity = (await client.graph.find_entities(tenant, name="ada lovelace"))[0]
    neighbors_by_id = await client.neighbors(ada_entity.id, depth=1)
    assert len(neighbors_by_id) == 1
    assert neighbors_by_id[0].name == "Charles Babbage"

    # Query nonexistent entity returns empty list
    nonexistent = await client.neighbors("Grace Hopper", depth=1)
    assert nonexistent == []


@pytest.mark.asyncio
async def test_client_consolidate_and_context_manager() -> None:
    provider = FakeLlmProvider(by_substring={}, default=TWO_PEOPLE)
    embeddings = FakeEmbeddingProvider(dimension=16)
    tenant = TenantId(uuid4())

    async with Redstring.in_memory(
        provider=provider,
        embeddings=embeddings,
        tenant_id=tenant,
    ) as client:
        await client.build_graph("Ada Lovelace and Charles Babbage.")
        ada = (await client.graph.find_entities(tenant, name="ada lovelace"))[0]
        # Calling consolidate on an entity with no duplicates returns None
        report = await client.consolidate(ada)
        assert report is None
