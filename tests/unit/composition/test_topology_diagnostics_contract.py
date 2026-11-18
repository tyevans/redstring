"""Contract tests for the argument checks in topology and diff diagnostics.

Each check runs before any store read, so the tests name the offending value
and assert the refusal. A valid call with `top_k_hubs=0` covers the path that
skips hub selection.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from redstring.composition.topology_diagnostics import analyze_topology, diff_graphs
from redstring.domain.entity import Entity
from redstring.domain.ids import EntityId, TenantId
from redstring.domain.normalization import normalize_name
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.graph.adapters.memory import InMemoryGraphStore

OBSERVED = datetime(2026, 2, 9, 11, 7, tzinfo=UTC)


def _entity(name: str, tenant_id: TenantId) -> Entity:
    return Entity(
        id=EntityId(uuid4()),
        tenant_id=tenant_id,
        name=name,
        normalized_name=normalize_name(name),
        entity_type="person",
        provenance=Provenance(
            observed_at=OBSERVED,
            extraction_method=ExtractionMethod.MANUAL,
            confidence=1.0,
        ),
    )


async def test_negative_top_k_hubs_is_refused_naming_the_value() -> None:
    tenant = TenantId(uuid4())
    graph = InMemoryGraphStore()

    with pytest.raises(ValueError, match="top_k_hubs must be >= 0, got -1"):
        await analyze_topology(tenant, graph=graph, relationships=graph, top_k_hubs=-1)


async def test_zero_top_k_hubs_counts_entities_but_returns_no_hubs() -> None:
    tenant = TenantId(uuid4())
    graph = InMemoryGraphStore()
    await graph.upsert_entities(
        [_entity("Ada Lovelace", tenant), _entity("Charles Babbage", tenant)]
    )

    report = await analyze_topology(tenant, graph=graph, relationships=graph, top_k_hubs=0)

    assert report.entity_count == 2
    assert report.top_hubs == ()


@pytest.mark.parametrize("page_size", [0, -4])
async def test_analyze_topology_refuses_a_page_size_below_one_naming_it(page_size: int) -> None:
    tenant = TenantId(uuid4())
    graph = InMemoryGraphStore()

    with pytest.raises(ValueError, match=f"page_size must be >= 1, got {page_size}"):
        await analyze_topology(tenant, graph=graph, relationships=graph, page_size=page_size)


@pytest.mark.parametrize("page_size", [0, -4])
async def test_diff_graphs_refuses_a_page_size_below_one_naming_it(page_size: int) -> None:
    base = TenantId(uuid4())
    target = TenantId(uuid4())
    graph = InMemoryGraphStore()

    with pytest.raises(ValueError, match=f"page_size must be >= 1, got {page_size}"):
        await diff_graphs(base, target, graph=graph, relationships=graph, page_size=page_size)
