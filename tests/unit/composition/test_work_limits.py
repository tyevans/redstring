"""Named maxima on the composition entry points.

Every paged entry point takes `page_size`, and every batch entry point takes
`max_concurrency`, `concurrency` and `gleanings`. Each is tested at its maximum
(accepted) and one above it (refused). The refusals happen before any store or
model is touched, so each refusal test also asserts the store was never read.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from redstring import (
    FakeLlmProvider,
    InMemoryGraphStore,
    TenantId,
    build_graph_batch,
)
from redstring.composition.consolidation_sweep import (
    consolidate_tenant,
    preview_consolidation,
)
from redstring.composition.lineage import analyze_corpus, trace_source_lineage
from redstring.composition.themes import MAX_PAGE_SIZE, summarize_themes
from redstring.composition.themes.topology import read_topology
from redstring.domain.ids import SourceId
from redstring.domain.limiter import MAX_CONCURRENCY
from redstring.extraction.gleaning import MAX_GLEANINGS


class _ReadCountingStore(InMemoryGraphStore):
    """Counts reads, so a refusal can be shown to have happened before any."""

    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    async def get_entities(self, *args, **kwargs):
        self.reads += 1
        return await super().get_entities(*args, **kwargs)

    async def find_entities(self, *args, **kwargs):
        self.reads += 1
        return await super().find_entities(*args, **kwargs)


def _provider() -> FakeLlmProvider:
    return FakeLlmProvider(by_substring={}, default={"entities": [], "relationships": []})


PAGED_ENTRY_POINTS = {
    "read_topology": lambda store, tenant, size: read_topology(
        tenant, store, store, page_size=size
    ),
    "trace_source_lineage": lambda store, tenant, size: trace_source_lineage(
        SourceId("doc-1"), tenant, graph=store, relationships=store, page_size=size
    ),
    "analyze_corpus": lambda store, tenant, size: analyze_corpus(
        tenant, graph=store, relationships=store, page_size=size
    ),
    "preview_consolidation": lambda store, tenant, size: preview_consolidation(
        tenant, graph_store=store, page_size=size
    ),
    "consolidate_tenant": lambda store, tenant, size: consolidate_tenant(
        tenant, store=store, page_size=size
    ),
    "summarize_themes": lambda store, tenant, size: summarize_themes(
        tenant, graph=store, relationships=store, provider=_provider(), page_size=size
    ),
}


class TestPageSizeMaximum:
    @pytest.mark.parametrize("entry", sorted(PAGED_ENTRY_POINTS))
    async def test_the_maximum_page_size_is_accepted(self, entry: str) -> None:
        store = InMemoryGraphStore()
        await PAGED_ENTRY_POINTS[entry](store, TenantId(uuid4()), MAX_PAGE_SIZE)

    @pytest.mark.parametrize("entry", sorted(PAGED_ENTRY_POINTS))
    async def test_one_above_the_maximum_page_size_is_refused_before_any_read(
        self, entry: str
    ) -> None:
        store = _ReadCountingStore()
        with pytest.raises(ValueError, match="page_size must be at most"):
            await PAGED_ENTRY_POINTS[entry](store, TenantId(uuid4()), MAX_PAGE_SIZE + 1)
        assert store.reads == 0

    @pytest.mark.parametrize("entry", sorted(PAGED_ENTRY_POINTS))
    async def test_a_page_size_below_one_is_still_refused(self, entry: str) -> None:
        store = _ReadCountingStore()
        with pytest.raises(ValueError, match="page_size must be at least 1"):
            await PAGED_ENTRY_POINTS[entry](store, TenantId(uuid4()), 0)
        assert store.reads == 0


class TestBatchMaxima:
    async def test_the_maximum_max_concurrency_is_accepted(self) -> None:
        report = await build_graph_batch(
            [],
            provider=_provider(),
            store=InMemoryGraphStore(),
            tenant_id=TenantId(uuid4()),
            max_concurrency=MAX_CONCURRENCY,
        )
        assert report.total_documents == 0

    async def test_one_above_the_maximum_max_concurrency_is_refused(self) -> None:
        with pytest.raises(ValueError, match="max_concurrency must be at most"):
            await build_graph_batch(
                [],
                provider=_provider(),
                store=InMemoryGraphStore(),
                tenant_id=TenantId(uuid4()),
                max_concurrency=MAX_CONCURRENCY + 1,
            )

    async def test_the_maximum_concurrency_is_accepted(self) -> None:
        report = await build_graph_batch(
            [],
            provider=_provider(),
            store=InMemoryGraphStore(),
            tenant_id=TenantId(uuid4()),
            concurrency=MAX_CONCURRENCY,
        )
        assert report.total_documents == 0

    async def test_one_above_the_maximum_concurrency_is_refused(self) -> None:
        with pytest.raises(ValueError, match="concurrency must be at most"):
            await build_graph_batch(
                [],
                provider=_provider(),
                store=InMemoryGraphStore(),
                tenant_id=TenantId(uuid4()),
                concurrency=MAX_CONCURRENCY + 1,
            )

    async def test_the_maximum_gleanings_is_accepted(self) -> None:
        report = await build_graph_batch(
            [],
            provider=_provider(),
            store=InMemoryGraphStore(),
            tenant_id=TenantId(uuid4()),
            gleanings=MAX_GLEANINGS,
        )
        assert report.total_documents == 0

    async def test_one_above_the_maximum_gleanings_is_refused(self) -> None:
        provider = _provider()
        with pytest.raises(ValueError, match="gleanings must be <="):
            await build_graph_batch(
                [],
                provider=provider,
                store=InMemoryGraphStore(),
                tenant_id=TenantId(uuid4()),
                gleanings=MAX_GLEANINGS + 1,
            )
