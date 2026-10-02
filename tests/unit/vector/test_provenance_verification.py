"""Provenance verification tests for vector stores (TASK-0007, BACKLOG B156).

Guards against silently corrupting similarity search across heterogeneous runs
when document_prefix or model identity changes.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

import pytest

from redstring import (
    DimensionMismatchError,
    FakeEmbeddingProvider,
    FakeLlmProvider,
    InMemoryGraphStore,
    InMemoryVectorStore,
    Retriever,
    SourceDocument,
    TaskPrefixMismatchError,
    VectorProvenance,
    VectorProvenanceMismatchError,
    build_graph,
)
from redstring.domain.ids import SourceId, TenantId
from redstring.llm.adapters.langchain_embedding import LangChainEmbeddingProvider
from redstring.vector.adapters.pgvector import PgVectorStore
from redstring.vector.provenance import (
    ensure_pgvector_provenance,
    provenance_table_ddl,
    verify_vector_provenance,
)


class TestVerifyVectorProvenance:
    def test_matching_provenance_passes(self) -> None:
        verify_vector_provenance(
            expected_dimension=1536,
            actual_dimension=1536,
            expected_model="text-embedding-3-small",
            actual_model="text-embedding-3-small",
            expected_document_prefix="search_document: ",
            actual_document_prefix="search_document: ",
        )

    def test_dimension_mismatch_raises(self) -> None:
        with pytest.raises(DimensionMismatchError) as exc_info:
            verify_vector_provenance(expected_dimension=1536, actual_dimension=768)
        assert exc_info.value.expected == 1536
        assert exc_info.value.actual == 768

    def test_model_mismatch_raises(self) -> None:
        with pytest.raises(VectorProvenanceMismatchError, match="changing embedding model"):
            verify_vector_provenance(
                expected_dimension=768,
                actual_dimension=768,
                expected_model="model-a",
                actual_model="model-b",
            )

    def test_prefix_mismatch_raises(self) -> None:
        with pytest.raises(TaskPrefixMismatchError) as exc_info:
            verify_vector_provenance(
                expected_dimension=768,
                actual_dimension=768,
                expected_document_prefix="passage: ",
                actual_document_prefix="search_document: ",
            )
        assert exc_info.value.expected == "passage: "
        assert exc_info.value.actual == "search_document: "


class TestInMemoryVectorStoreProvenance:
    def test_initial_provenance_recording(self) -> None:
        store = InMemoryVectorStore(
            dimension=128,
            model="test-embedder",
            document_prefix="passage: ",
        )
        assert store.model == "test-embedder"
        assert store.document_prefix == "passage: "
        assert store.provenance == VectorProvenance(
            dimension=128,
            model="test-embedder",
            document_prefix="passage: ",
        )

    def test_ensure_schema_rejects_prefix_mismatch(self) -> None:
        store = InMemoryVectorStore(
            dimension=128,
            model="test-embedder",
            document_prefix="passage: ",
        )
        with pytest.raises(TaskPrefixMismatchError):
            store.ensure_schema(model="test-embedder", document_prefix="query: ")

    def test_ensure_schema_rejects_model_mismatch(self) -> None:
        store = InMemoryVectorStore(dimension=128, model="model-a")
        with pytest.raises(VectorProvenanceMismatchError):
            store.ensure_schema(model="model-b")

    def test_ensure_schema_populates_unconfigured_attributes(self) -> None:
        store = InMemoryVectorStore(dimension=128)
        assert store.model is None

        configured = InMemoryVectorStore(dimension=128)
        configured.ensure_schema(model="model-c", document_prefix="doc: ")
        assert configured.model == "model-c"
        assert configured.document_prefix == "doc: "


class FakeAsyncpgConnection:
    def __init__(self, row: dict[str, Any] | None = None, typmod: int | None = None) -> None:
        self.row = row
        self.typmod = typmod
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append((query, args))
        return "OK"

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        return self.row

    async def fetchval(self, query: str, *args: Any) -> Any:
        return self.typmod


class FakeAsyncpgPool:
    def __init__(self, connection: FakeAsyncpgConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[FakeAsyncpgConnection]:
        yield self.connection


class TestPgVectorProvenanceWiring:
    def test_provenance_table_ddl_structure(self) -> None:
        ddl = provenance_table_ddl("my_vectors")
        assert "CREATE TABLE IF NOT EXISTS my_vectors_provenance" in ddl
        assert "dimension integer NOT NULL" in ddl
        assert "document_prefix text NOT NULL" in ddl

    async def test_ensure_pgvector_provenance_inserts_when_empty(self) -> None:
        conn = FakeAsyncpgConnection(row=None)
        provenance = await ensure_pgvector_provenance(
            conn,
            "test_table",
            dimension=384,
            model="bge-small-en-v1.5",
            document_prefix="represent this document: ",
        )
        assert provenance is not None
        assert provenance.dimension == 384
        assert provenance.model == "bge-small-en-v1.5"
        assert provenance.document_prefix == "represent this document: "
        assert any("INSERT INTO test_table_provenance" in q for q, _ in conn.executed)

    async def test_ensure_pgvector_provenance_verifies_existing_row(self) -> None:
        existing = {
            "dimension": 384,
            "model": "bge-small-en-v1.5",
            "document_prefix": "represent this document: ",
        }
        conn = FakeAsyncpgConnection(row=existing)
        provenance = await ensure_pgvector_provenance(
            conn,
            "test_table",
            dimension=384,
            model="bge-small-en-v1.5",
            document_prefix="represent this document: ",
        )
        assert provenance is not None
        assert provenance.document_prefix == "represent this document: "

    async def test_ensure_pgvector_provenance_raises_on_prefix_disagreement(self) -> None:
        existing = {
            "dimension": 384,
            "model": "bge-small-en-v1.5",
            "document_prefix": "prefix-one",
        }
        conn = FakeAsyncpgConnection(row=existing)
        with pytest.raises(TaskPrefixMismatchError) as exc_info:
            await ensure_pgvector_provenance(
                conn,
                "test_table",
                dimension=384,
                model="bge-small-en-v1.5",
                document_prefix="prefix-two",
            )
        assert exc_info.value.expected == "prefix-one"
        assert exc_info.value.actual == "prefix-two"

    async def test_pgvector_store_ensure_schema_verifies_provenance_frontdoor(self) -> None:
        existing = {
            "dimension": 256,
            "model": "bge-small-en-v1.5",
            "document_prefix": "prefix-one",
        }
        conn = FakeAsyncpgConnection(row=existing, typmod=256)
        pool = FakeAsyncpgPool(conn)
        store = PgVectorStore(
            pool,
            dimension=256,
            model="bge-small-en-v1.5",
            document_prefix="prefix-two",
            table="my_table",
        )
        with pytest.raises(TaskPrefixMismatchError):
            await store.ensure_schema()


class TestWiringChecksInComposition:
    async def test_build_graph_rejects_prefix_mismatch(self) -> None:
        provider = FakeEmbeddingProvider(dimension=64, document_prefix="passage: ")
        store = InMemoryVectorStore(dimension=64, document_prefix="document: ")
        graph = InMemoryGraphStore()
        llm = FakeLlmProvider(by_substring={})

        with pytest.raises(TaskPrefixMismatchError) as exc_info:
            await build_graph(
                SourceDocument(id=SourceId("doc-1"), text="some text"),
                provider=llm,
                store=graph,
                tenant_id=TenantId(uuid4()),
                embedding_provider=provider,
                vector_store=store,
            )
        assert exc_info.value.expected == "document: "
        assert exc_info.value.actual == "passage: "

    async def test_build_graph_rejects_model_mismatch(self) -> None:
        provider = FakeEmbeddingProvider(dimension=64, model="fake-v1")
        store = InMemoryVectorStore(dimension=64, model="fake-v2")
        graph = InMemoryGraphStore()
        llm = FakeLlmProvider(by_substring={})

        with pytest.raises(VectorProvenanceMismatchError):
            await build_graph(
                SourceDocument(id=SourceId("doc-1"), text="some text"),
                provider=llm,
                store=graph,
                tenant_id=TenantId(uuid4()),
                embedding_provider=provider,
                vector_store=store,
            )

    async def test_build_graph_accepts_compatible_wiring(self) -> None:
        provider = FakeEmbeddingProvider(dimension=64, model="fake-v1", document_prefix="passage: ")
        store = InMemoryVectorStore(dimension=64, model="fake-v1", document_prefix="passage: ")
        graph = InMemoryGraphStore()
        llm = FakeLlmProvider(by_substring={})

        report = await build_graph(
            SourceDocument(id=SourceId("doc-1"), text="some text"),
            provider=llm,
            store=graph,
            tenant_id=TenantId(uuid4()),
            embedding_provider=provider,
            vector_store=store,
        )
        assert report.entities == 0

    def test_retriever_rejects_prefix_mismatch(self) -> None:
        class DummyEntityReader:
            async def get_entity(self, *args: Any, **kwargs: Any) -> Any:
                return None

            async def find_by_blocking_keys(self, *args: Any, **kwargs: Any) -> Any:
                return {}

            async def get_entities(self, *args: Any, **kwargs: Any) -> Any:
                return []

            async def close(self) -> None:
                pass

        provider = FakeEmbeddingProvider(dimension=64, document_prefix="query_prefix: ")
        store = InMemoryVectorStore(dimension=64, document_prefix="doc_prefix: ")

        with pytest.raises(TaskPrefixMismatchError):
            Retriever(
                embeddings=provider,
                vectors=store,
                graph=DummyEntityReader(),  # type: ignore[arg-type]
            )


class TestProviderPrefixAccessors:
    def test_fake_embedding_provider_exposes_prefixes(self) -> None:
        provider = FakeEmbeddingProvider(
            dimension=32,
            document_prefix="doc: ",
            query_prefix="query: ",
        )
        assert provider.document_prefix == "doc: "
        assert provider.query_prefix == "query: "

    def test_langchain_embedding_provider_exposes_prefixes(self) -> None:
        class DummyEmbeddings:
            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                return [[0.1] * 16 for _ in texts]

            def embed_query(self, text: str) -> list[float]:
                return [0.1] * 16

        provider = LangChainEmbeddingProvider(
            DummyEmbeddings(),  # type: ignore[arg-type]
            dimension=16,
            model="langchain-dummy",
            document_prefix="prefix_doc: ",
            query_prefix="prefix_query: ",
        )
        assert provider.document_prefix == "prefix_doc: "
        assert provider.query_prefix == "prefix_query: "
