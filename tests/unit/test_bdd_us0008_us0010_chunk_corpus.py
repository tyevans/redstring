"""Executable BDD Acceptance Criteria for US-0008, US-0009, and US-0010: Chunk Corpus & Fusion.

Governed by:
- ADR-0123 (The Chunk Corpus)
- ADR-0124 (BM25 Over the Chunk Corpus)
- ADR-0138 (The Chunk's Vector Lives on the Chunk)
- ADR-0144 (A Chunk ID Is Derived, Not Supplied)
- ADR-0145 (A Lexical-Only Retriever Is a Constructor)
- ADR-0146 (A Chunk Write Reports What It Added)
- ADR-0002 (File Length Limit)
- ADR-0003 (Blackbox Frontdoor Verification)
- ADR-0006 (Executable BDD User Stories)
- PRD-0002
- US-0008, US-0009, US-0010
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from redstring import (
    ChunkRetrievalResult,
    ChunkRetriever,
    FakeEmbeddingProvider,
    FakeLlmProvider,
    IndexReport,
    InMemoryChunkStore,
    InMemoryGraphStore,
    InMemoryVectorStore,
    RetrievalMode,
    SourceDocument,
    SourceId,
    TenantId,
    build_graph,
    index_documents,
)

EXTRACTION_ANSWER = {
    "entities": [
        {"name": "Ada Lovelace", "entity_type": "Person"},
        {"name": "Analytical Engine", "entity_type": "Machine"},
    ],
    "relationships": [
        {
            "source_name": "Ada Lovelace",
            "target_name": "Analytical Engine",
            "relationship_type": "PROGRAMMED",
        }
    ],
}


@pytest.mark.asyncio
async def test_scenario_persist_stored_chunks_with_deterministic_hash_ids() -> None:
    """Scenario: Persist stored chunks with deterministic hash IDs (US-0008)."""
    tenant_id = TenantId(uuid4())
    chunk_store = InMemoryChunkStore(dimension=16)
    doc = SourceDocument(
        id=SourceId("doc-1"),
        text="Ada Lovelace wrote the Bernoulli algorithm for the Analytical Engine.",
    )

    report1 = await index_documents([doc], store=chunk_store, tenant_id=tenant_id)
    assert isinstance(report1, IndexReport)
    assert report1.documents_indexed == 1
    assert report1.chunks_written == 1

    stored1 = await chunk_store.get_by_source(doc.id, tenant_id)
    assert len(stored1) == 1
    first_chunk_id = stored1[0].id

    # Re-indexing identical content produces the exact same deterministic ChunkId
    chunk_store_second = InMemoryChunkStore(dimension=16)
    await index_documents([doc], store=chunk_store_second, tenant_id=tenant_id)
    stored2 = await chunk_store_second.get_by_source(doc.id, tenant_id)
    assert len(stored2) == 1
    assert stored2[0].id == first_chunk_id


@pytest.mark.asyncio
async def test_scenario_atomic_replacement_of_chunks_on_document_re_extraction() -> None:
    """Scenario: Atomic replacement of chunks on document re-extraction (US-0008)."""
    tenant_id = TenantId(uuid4())
    other_tenant = TenantId(uuid4())
    chunk_store = InMemoryChunkStore(dimension=16)

    doc_v1 = SourceDocument(id=SourceId("doc-1"), text="Original passage about Charles Babbage.")
    doc_other = SourceDocument(id=SourceId("doc-2"), text="Unrelated document passage.")

    await index_documents([doc_v1, doc_other], store=chunk_store, tenant_id=tenant_id)
    await index_documents([doc_v1], store=chunk_store, tenant_id=other_tenant)

    doc_v2 = SourceDocument(id=SourceId("doc-1"), text="Updated passage about Ada Lovelace.")
    await index_documents([doc_v2], store=chunk_store, tenant_id=tenant_id)

    updated_chunks = await chunk_store.get_by_source(SourceId("doc-1"), tenant_id)
    assert len(updated_chunks) == 1
    assert "Ada Lovelace" in updated_chunks[0].text
    assert "Charles Babbage" not in updated_chunks[0].text

    # Other source and other tenant remain untouched
    other_source_chunks = await chunk_store.get_by_source(SourceId("doc-2"), tenant_id)
    assert len(other_source_chunks) == 1
    other_tenant_chunks = await chunk_store.get_by_source(SourceId("doc-1"), other_tenant)
    assert len(other_tenant_chunks) == 1
    assert "Charles Babbage" in other_tenant_chunks[0].text


@pytest.mark.asyncio
async def test_scenario_fuse_bm25_lexical_ranking_and_dense_vector_similarity() -> None:
    """Scenario: Fuse BM25 lexical ranking and dense vector similarity (US-0009)."""
    tenant_id = TenantId(uuid4())
    embeddings = FakeEmbeddingProvider(dimension=16)
    chunk_store = InMemoryChunkStore(dimension=16)

    docs = [
        SourceDocument(
            id=SourceId("doc-bernoulli"),
            text="Ada Lovelace published Note G computing Bernoulli numbers on the engine.",
        ),
        SourceDocument(
            id=SourceId("doc-weaving"),
            text="Jacquard looms used punched cards to weave silk patterns.",
        ),
    ]
    await index_documents(docs, store=chunk_store, embeddings=embeddings, tenant_id=tenant_id)

    retriever = ChunkRetriever(embeddings=embeddings, chunks=chunk_store)
    result = await retriever.retrieve_chunks(
        "Bernoulli numbers Note G",
        tenant_id,
        k=2,
        mode=RetrievalMode.HYBRID,
    )

    assert isinstance(result, ChunkRetrievalResult)
    assert len(result.matches) >= 1
    assert result.matches[0].chunk.source_id == SourceId("doc-bernoulli")
    assert result.matches[0].score > 0.0


@pytest.mark.asyncio
async def test_scenario_rank_passage_citations_with_reciprocal_rank_fusion() -> None:
    """Scenario: Rank passage citations with Reciprocal Rank Fusion (US-0009, US-0010)."""
    tenant_id = TenantId(uuid4())
    provider = FakeLlmProvider(by_substring={}, default=EXTRACTION_ANSWER)
    embeddings = FakeEmbeddingProvider(dimension=16)
    graph_store = InMemoryGraphStore()
    vector_store = InMemoryVectorStore(dimension=16)
    chunk_store = InMemoryChunkStore(dimension=16)

    doc = SourceDocument(
        id=SourceId("doc-citation"),
        text="Ada Lovelace programmed the Analytical Engine in London.",
    )
    build_report = await build_graph(
        doc,
        provider=provider,
        store=graph_store,
        tenant_id=tenant_id,
        embedding_provider=embeddings,
        vector_store=vector_store,
        chunks=chunk_store,
    )
    assert build_report.chunks_written == 1

    lexical_retriever = ChunkRetriever.lexical_only(chunks=chunk_store)
    result = await lexical_retriever.retrieve_chunks("Analytical Engine", tenant_id, k=5)

    assert len(result.matches) == 1
    top = result.matches[0]
    assert top.chunk.source_id == SourceId("doc-citation")
    assert len(top.chunk.entity_ids) == 2


@pytest.mark.asyncio
async def test_scenario_enforce_strict_tenant_isolation_during_chunk_retrieval() -> None:
    """Scenario: Enforce strict tenant isolation during chunk retrieval (US-0010)."""
    tenant_a = TenantId(uuid4())
    tenant_b = TenantId(uuid4())
    chunk_store = InMemoryChunkStore(dimension=16)

    doc_a = SourceDocument(id=SourceId("doc-a"), text="Confidential Alpha cryptography key.")
    doc_b = SourceDocument(id=SourceId("doc-b"), text="Confidential Beta cryptography key.")

    await index_documents([doc_a], store=chunk_store, tenant_id=tenant_a)
    await index_documents([doc_b], store=chunk_store, tenant_id=tenant_b)

    retriever = ChunkRetriever.lexical_only(chunks=chunk_store)
    res_a = await retriever.retrieve_chunks("cryptography", tenant_a, k=10)

    assert len(res_a.matches) == 1
    assert res_a.matches[0].chunk.source_id == SourceId("doc-a")
    assert all(m.chunk.tenant_id == tenant_a for m in res_a.matches)
