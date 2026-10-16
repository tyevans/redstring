"""Tests for concurrent batch document ingestion."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from redstring import (
    BatchDocumentError,
    FakeEmbeddingProvider,
    FakeLlmProvider,
    InMemoryChunkStore,
    InMemoryGraphStore,
    InMemoryVectorStore,
    SourceDocument,
    SourceId,
    TenantId,
    build_graph_batch,
)
from redstring.domain.exceptions import DimensionMismatchError, LlmProviderError

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


class ConcurrencyTrackingProvider:
    """A real LlmProvider that tracks concurrent invocations and can simulate delay/failure."""

    def __init__(
        self,
        answer: dict[str, Any],
        delay: float = 0.02,
        fail_on_substring: str | None = None,
    ) -> None:
        self._inner = FakeLlmProvider(by_substring={}, default=answer)
        self.delay = delay
        self.fail_on_substring = fail_on_substring
        self.active = 0
        self.max_observed_active = 0
        self.model = "tracking-test-model"

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
            if self.fail_on_substring and self.fail_on_substring in text:
                raise LlmProviderError("Simulated LLM failure", model="tracking-test-model")
            if self.delay > 0:
                await asyncio.sleep(self.delay)
            return await self._inner.extract(text, schema, system_prompt=system_prompt)
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_build_graph_batch_empty() -> None:
    store = InMemoryGraphStore()
    provider = FakeLlmProvider(by_substring={}, default=TWO_PEOPLE)
    report = await build_graph_batch(
        [],
        provider=provider,
        store=store,
        tenant_id=TenantId(uuid4()),
    )
    assert report.total_documents == 0
    assert report.successful_documents == 0
    assert report.failed_documents == 0
    assert report.entities == 0
    assert report.relationships == 0
    assert report.reports == ()
    assert report.errors == ()


@pytest.mark.asyncio
async def test_build_graph_batch_bounded_concurrency() -> None:
    store = InMemoryGraphStore()
    provider = ConcurrencyTrackingProvider(TWO_PEOPLE, delay=0.03)
    tenant = TenantId(uuid4())

    docs = [
        f"Document {i}: Ada Lovelace and Charles Babbage collaborated on computing."
        for i in range(5)
    ]

    completed_docs: list[SourceDocument] = []

    def on_complete(doc: SourceDocument, rep: Any) -> None:
        completed_docs.append(doc)

    report = await build_graph_batch(
        docs,
        provider=provider,  # type: ignore[arg-type]
        store=store,
        tenant_id=tenant,
        max_concurrency=2,
        on_document_complete=on_complete,
    )

    assert report.total_documents == 5
    assert report.successful_documents == 5
    assert report.failed_documents == 0
    assert len(report.reports) == 5
    assert report.entities == 10  # 2 entities per document * 5
    assert report.relationships == 5  # 1 relationship per document * 5
    assert len(completed_docs) == 5
    # Concurrency limit must be strictly respected
    assert provider.max_observed_active <= 2


@pytest.mark.asyncio
async def test_build_graph_batch_continue_on_error() -> None:
    store = InMemoryGraphStore()
    provider = ConcurrencyTrackingProvider(
        TWO_PEOPLE,
        delay=0.01,
        fail_on_substring="POISON_PILL",
    )
    tenant = TenantId(uuid4())

    items = [
        "Normal document 1",
        "POISON_PILL will fail extraction",
        "Normal document 2",
    ]

    failed_items: list[tuple[Any, Exception]] = []

    def on_error(item: Any, exc: Exception) -> None:
        failed_items.append((item, exc))

    report = await build_graph_batch(
        items,
        provider=provider,  # type: ignore[arg-type]
        store=store,
        tenant_id=tenant,
        continue_on_error=True,
        on_document_error=on_error,
    )

    assert report.total_documents == 3
    assert report.successful_documents == 2
    assert report.failed_documents == 1
    assert len(report.errors) == 1
    assert isinstance(report.errors[0], BatchDocumentError)
    assert isinstance(report.errors[0].error, LlmProviderError)
    assert len(failed_items) == 1

    # Also verify continue_on_error without callback handles failure cleanly
    report2 = await build_graph_batch(
        items,
        provider=provider,  # type: ignore[arg-type]
        store=store,
        tenant_id=tenant,
        continue_on_error=True,
    )
    assert report2.failed_documents == 1


@pytest.mark.asyncio
async def test_build_graph_batch_raise_on_error() -> None:
    store = InMemoryGraphStore()
    provider = ConcurrencyTrackingProvider(
        TWO_PEOPLE,
        delay=0.01,
        fail_on_substring="POISON_PILL",
    )
    tenant = TenantId(uuid4())

    items = [
        "Normal document 1",
        "POISON_PILL will fail extraction",
    ]

    with pytest.raises(LlmProviderError, match="Simulated LLM failure"):
        await build_graph_batch(
            items,
            provider=provider,  # type: ignore[arg-type]
            store=store,
            tenant_id=tenant,
            continue_on_error=False,
        )


@pytest.mark.asyncio
async def test_build_graph_batch_invalid_concurrency() -> None:
    store = InMemoryGraphStore()
    provider = FakeLlmProvider(by_substring={}, default=TWO_PEOPLE)
    tenant = TenantId(uuid4())

    with pytest.raises(ValueError, match="max_concurrency must be at least 1"):
        await build_graph_batch(
            ["text"],
            provider=provider,
            store=store,
            tenant_id=tenant,
            max_concurrency=0,
        )


@pytest.mark.asyncio
async def test_build_graph_batch_mixed_inputs() -> None:
    store = InMemoryGraphStore()
    provider = FakeLlmProvider(by_substring={}, default=TWO_PEOPLE)
    tenant = TenantId(uuid4())

    items: list[SourceDocument | str | dict[str, Any]] = [
        SourceDocument(id=SourceId("doc-explicit"), text="Ada Lovelace was an analyst."),
        "Charles Babbage constructed mechanical calculators.",
        {"id": "doc-dict", "text": "The Analytical Engine used punched cards."},
    ]

    report = await build_graph_batch(
        items,
        provider=provider,
        store=store,
        tenant_id=tenant,
        max_concurrency=3,
    )

    assert report.total_documents == 3
    assert report.successful_documents == 3
    assert report.failed_documents == 0
    assert report.entities == 6


@pytest.mark.asyncio
async def test_build_graph_batch_chunk_dimension_mismatch() -> None:
    store = InMemoryGraphStore()
    provider = FakeLlmProvider(by_substring={}, default=TWO_PEOPLE)
    tenant = TenantId(uuid4())
    embeddings = FakeEmbeddingProvider(dimension=16)
    chunks = InMemoryChunkStore(dimension=32)
    vectors = InMemoryVectorStore(dimension=32)

    with pytest.raises(DimensionMismatchError):
        await build_graph_batch(
            ["hello world"],
            provider=provider,
            store=store,
            tenant_id=tenant,
            embedding_provider=embeddings,
            chunks=chunks,
        )

    with pytest.raises(DimensionMismatchError):
        await build_graph_batch(
            ["hello world"],
            provider=provider,
            store=store,
            tenant_id=tenant,
            embedding_provider=embeddings,
            vector_store=vectors,
        )


@pytest.mark.asyncio
async def test_build_graph_batch_coercion_error_continue() -> None:
    store = InMemoryGraphStore()
    provider = FakeLlmProvider(by_substring={}, default=TWO_PEOPLE)
    tenant = TenantId(uuid4())

    captured_errors: list[tuple[Any, Exception]] = []

    # With on_document_error callback
    report = await build_graph_batch(
        [12345],  # type: ignore[list-item]
        provider=provider,
        store=store,
        tenant_id=tenant,
        continue_on_error=True,
        on_document_error=lambda item, exc: captured_errors.append((item, exc)),
    )

    assert report.total_documents == 1
    assert report.failed_documents == 1
    assert len(report.errors) == 1
    assert len(captured_errors) == 1
    assert captured_errors[0][0] == 12345

    # Without callback
    report_no_cb = await build_graph_batch(
        [12345],  # type: ignore[list-item]
        provider=provider,
        store=store,
        tenant_id=tenant,
        continue_on_error=True,
    )
    assert report_no_cb.failed_documents == 1


@pytest.mark.asyncio
async def test_build_graph_batch_coercion_error_raise() -> None:
    store = InMemoryGraphStore()
    provider = FakeLlmProvider(by_substring={}, default=TWO_PEOPLE)
    tenant = TenantId(uuid4())

    with pytest.raises(TypeError):
        await build_graph_batch(
            [12345],  # type: ignore[list-item]
            provider=provider,
            store=store,
            tenant_id=tenant,
            continue_on_error=False,
        )
