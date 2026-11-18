"""Executable BDD Acceptance Criteria for US-0014 and US-0015: Temporal Queries & Themes.

Governed by:
- ADR-0142 (Themes Are Recomputed, Never Stored)
- ADR-0002 (File Length Limit)
- ADR-0003 (Blackbox Frontdoor Verification)
- ADR-0006 (Executable BDD User Stories)
- PRD-0005
- US-0014, US-0015
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from redstring import (
    Bounds,
    DatePrecision,
    Entity,
    ExtractionMethod,
    FakeLlmProvider,
    InMemoryChunkStore,
    InMemoryGraphStore,
    Provenance,
    Relationship,
    SourceDocument,
    SourceId,
    TemporalExtent,
    TemporalQuery,
    TemporalRelation,
    TenantId,
    ThemeReport,
    UncertaintyMarker,
    index_documents,
    infer_relations,
    summarize_themes,
)


def _make_dated_entity(
    tenant_id: TenantId,
    name: str,
    temporal: TemporalExtent | None,
    *,
    entity_type: str = "Event",
) -> Entity:
    return Entity(
        id=uuid4(),
        tenant_id=tenant_id,
        name=name,
        normalized_name=name.lower(),
        entity_type=entity_type,
        temporal=temporal,
        provenance=Provenance(
            observed_at=datetime(2026, 1, 1, tzinfo=UTC),
            extraction_method=ExtractionMethod.LLM,
            confidence=0.95,
            source_id=SourceId("doc-temporal"),
            model_version="test-v1",
        ),
    )


@pytest.mark.asyncio
async def test_scenario_reconstruct_chronological_timeline_across_precisions() -> None:
    """Scenario: Reconstruct chronological timeline across mixed date precisions (US-0014)."""
    tenant_id = TenantId(uuid4())
    store = InMemoryGraphStore()

    ancient = _make_dated_entity(
        tenant_id,
        "Pre-1800 Precursor",
        TemporalExtent(
            start_date=datetime(1800, 1, 1, tzinfo=UTC),
            precision=DatePrecision.YEAR,
            uncertainty=UncertaintyMarker.BEFORE,
        ),
    )
    babbage_1837 = _make_dated_entity(
        tenant_id,
        "Analytical Engine Design",
        TemporalExtent(
            start_date=datetime(1837, 1, 1, tzinfo=UTC),
            precision=DatePrecision.YEAR,
        ),
    )
    lovelace_1843 = _make_dated_entity(
        tenant_id,
        "Note G Publication",
        TemporalExtent(
            start_date=datetime(1843, 9, 1, tzinfo=UTC),
            end_date=datetime(1843, 9, 30, tzinfo=UTC),
            precision=DatePrecision.DAY,
        ),
    )
    undated = _make_dated_entity(tenant_id, "Undated Concept", None)

    for entity in (lovelace_1843, undated, babbage_1837, ancient):
        await store.upsert_entity(entity)

    query = TemporalQuery(store)
    timeline = await query.timeline(tenant_id)

    assert [e.name for e in timeline] == [
        "Pre-1800 Precursor",
        "Analytical Engine Design",
        "Note G Publication",
    ]


@pytest.mark.asyncio
async def test_scenario_filter_entities_intersecting_query_interval() -> None:
    """Scenario: Filter entities intersecting a query interval (US-0014)."""
    tenant_a = TenantId(uuid4())
    tenant_b = TenantId(uuid4())
    store = InMemoryGraphStore()

    e_1840_1845 = _make_dated_entity(
        tenant_a,
        "Span 1840-1845",
        TemporalExtent(
            start_date=datetime(1840, 1, 1, tzinfo=UTC),
            end_date=datetime(1845, 12, 31, tzinfo=UTC),
            precision=DatePrecision.YEAR,
        ),
    )
    e_1850 = _make_dated_entity(
        tenant_a,
        "Event 1850",
        TemporalExtent(
            start_date=datetime(1850, 1, 1, tzinfo=UTC),
            precision=DatePrecision.YEAR,
        ),
    )
    e_1900 = _make_dated_entity(
        tenant_a,
        "Event 1900",
        TemporalExtent(
            start_date=datetime(1900, 1, 1, tzinfo=UTC),
            precision=DatePrecision.YEAR,
        ),
    )
    other_tenant_entity = _make_dated_entity(
        tenant_b,
        "Other Tenant 1845",
        TemporalExtent(
            start_date=datetime(1845, 1, 1, tzinfo=UTC),
            precision=DatePrecision.YEAR,
        ),
    )

    for entity in (e_1840_1845, e_1850, e_1900, other_tenant_entity):
        await store.upsert_entity(entity)

    window = Bounds(
        lower=datetime(1842, 1, 1, tzinfo=UTC),
        upper=datetime(1855, 12, 31, tzinfo=UTC),
    )
    matches = await TemporalQuery(store).entities_in_interval(tenant_a, window)
    names = {e.name for e in matches}
    assert names == {"Span 1840-1845", "Event 1850"}


def test_scenario_infer_canonical_allen_interval_relations_on_read() -> None:
    """Scenario: Infer canonical Allen interval relations on read (US-0014)."""
    tenant_id = TenantId(uuid4())
    event_a = _make_dated_entity(
        tenant_id,
        "Event A (1830)",
        TemporalExtent(start_date=datetime(1830, 1, 1, tzinfo=UTC), precision=DatePrecision.YEAR),
    )
    event_b = _make_dated_entity(
        tenant_id,
        "Event B (1843)",
        TemporalExtent(start_date=datetime(1843, 1, 1, tzinfo=UTC), precision=DatePrecision.YEAR),
    )
    era_c = _make_dated_entity(
        tenant_id,
        "Victorian Era (1837-1901)",
        TemporalExtent(
            start_date=datetime(1837, 1, 1, tzinfo=UTC),
            end_date=datetime(1901, 12, 31, tzinfo=UTC),
            precision=DatePrecision.YEAR,
        ),
    )

    inferred = infer_relations([era_c, event_b, event_a])
    relations_by_pair = {(rel.source_name, rel.target_name): rel.relation for rel in inferred}

    assert relations_by_pair[("Event A (1830)", "Event B (1843)")] == TemporalRelation.BEFORE
    assert (
        relations_by_pair[("Victorian Era (1837-1901)", "Event B (1843)")]
        == TemporalRelation.CONTAINS
    )
    # Inverse relations AFTER and DURING are never emitted
    assert all(
        rel.relation not in {TemporalRelation.AFTER, TemporalRelation.DURING} for rel in inferred
    )


def test_scenario_refuse_quadratic_temporal_inference_when_pairs_exceed_ceiling() -> None:
    """Scenario: Refuse quadratic temporal inference when pairs exceed max_pairs (US-0014)."""
    tenant_id = TenantId(uuid4())
    entities = [
        _make_dated_entity(
            tenant_id,
            f"Event {year}",
            TemporalExtent(
                start_date=datetime(year, 1, 1, tzinfo=UTC), precision=DatePrecision.YEAR
            ),
        )
        for year in range(1800, 1810)
    ]

    with pytest.raises(ValueError, match="max_pairs=10"):
        infer_relations(entities, max_pairs=10)


@pytest.mark.asyncio
async def test_scenario_cluster_tenant_graph_and_synthesize_themes() -> None:
    """Scenario: Cluster tenant graph into communities and synthesize themes (US-0015)."""
    tenant_id = TenantId(uuid4())
    graph_store = InMemoryGraphStore()
    chunk_store = InMemoryChunkStore(dimension=16)

    e1 = _make_dated_entity(tenant_id, "Ada Lovelace", None, entity_type="Person")
    e2 = _make_dated_entity(tenant_id, "Charles Babbage", None, entity_type="Person")
    e3 = _make_dated_entity(tenant_id, "Analytical Engine", None, entity_type="Machine")
    singleton_1 = _make_dated_entity(tenant_id, "Isolated Node 1", None, entity_type="Misc")
    singleton_2 = _make_dated_entity(tenant_id, "Isolated Node 2", None, entity_type="Misc")

    for e in (e1, e2, e3, singleton_1, singleton_2):
        await graph_store.upsert_entity(e)

    rel_1 = Relationship(
        id=uuid4(),
        tenant_id=tenant_id,
        source_entity_id=e1.id,
        target_entity_id=e2.id,
        relationship_type="COLLABORATED_WITH",
        source_id=SourceId("doc-themes"),
        confidence=0.95,
    )
    rel_2 = Relationship(
        id=uuid4(),
        tenant_id=tenant_id,
        source_entity_id=e1.id,
        target_entity_id=e3.id,
        relationship_type="PROGRAMMED",
        source_id=SourceId("doc-themes"),
        confidence=0.95,
    )
    await graph_store.upsert_relationships([rel_1, rel_2])

    doc = SourceDocument(
        id=SourceId("doc-themes"),
        text="Ada Lovelace and Charles Babbage developed the Analytical Engine.",
    )
    await index_documents([doc], store=chunk_store, tenant_id=tenant_id)

    theme_answer = {
        "title": "Early Mechanical Computing Pioneers",
        "summary": "Ada Lovelace and Charles Babbage collaborated on the Analytical Engine.",
    }
    provider = FakeLlmProvider(by_substring={}, default=theme_answer)

    report = await summarize_themes(
        tenant_id,
        graph=graph_store,
        relationships=graph_store,
        provider=provider,
        chunks=chunk_store,
        min_size=2,
    )

    assert isinstance(report, ThemeReport)
    assert len(report.themes) == 1
    assert report.themes[0].title == "Early Mechanical Computing Pioneers"
    assert set(report.themes[0].members) == {e1.id, e2.id, e3.id}
    assert report.too_small == 2
    assert report.failed == 0


@pytest.mark.asyncio
async def test_scenario_skip_failed_community_model_calls_gracefully() -> None:
    """Scenario: Skip failed community model calls when skip_failed is enabled (US-0015)."""
    tenant_id = TenantId(uuid4())
    graph_store = InMemoryGraphStore()

    e1 = _make_dated_entity(tenant_id, "Node A", None)
    e2 = _make_dated_entity(tenant_id, "Node B", None)
    await graph_store.upsert_entity(e1)
    await graph_store.upsert_entity(e2)
    await graph_store.upsert_relationship(
        Relationship(
            id=uuid4(),
            tenant_id=tenant_id,
            source_entity_id=e1.id,
            target_entity_id=e2.id,
            relationship_type="LINKED",
            source_id=SourceId("doc-themes"),
            confidence=0.95,
        )
    )

    # Empty completion triggers EmptyCompletionError (a subclass of LlmProviderError)
    from redstring import EMPTY

    failing_provider = FakeLlmProvider(
        by_substring={},
        default=EMPTY,
    )

    report = await summarize_themes(
        tenant_id,
        graph=graph_store,
        relationships=graph_store,
        provider=failing_provider,
        skip_failed=True,
    )

    assert report.themes == ()
    assert report.failed == 1
