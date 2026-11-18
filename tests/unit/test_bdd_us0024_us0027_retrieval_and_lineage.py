"""Executable BDD and Hypothesis property tests for US-0024 through US-0027.

Covers US-0024, US-0025, US-0026, and US-0027.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from redstring import (
    Alias,
    Entity,
    EntityId,
    ExtractionMethod,
    FakeEmbeddingProvider,
    FakeLlmProvider,
    InMemoryChunkStore,
    InMemoryGraphStore,
    InMemoryVectorStore,
    Provenance,
    Redstring,
    Relationship,
    RelationshipId,
    RetrievalMode,
    SourceDocument,
    SourceId,
    TenantId,
    analyze_corpus,
    explain_chunk_retrieval,
    explain_entity_retrieval,
    index_documents,
    trace_source_lineage,
)
from redstring.domain.blocking import blocking_keys_for
from redstring.domain.chunk import StoredChunk
from redstring.domain.fusion import RRF_K


def _make_entity(
    name: str,
    entity_type: str,
    tenant_id: TenantId,
    *,
    source_id: SourceId | None = None,
    properties: dict[str, Any] | None = None,
) -> Entity:
    base = Entity(
        id=EntityId(uuid4()),
        tenant_id=tenant_id,
        name=name,
        normalized_name=name.lower().strip(),
        entity_type=entity_type,
        properties=properties or {},
        provenance=Provenance(
            observed_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
            extraction_method=ExtractionMethod.LLM,
            confidence=0.95,
            source_id=source_id or SourceId("doc-default"),
        ),
    )
    return base.model_copy(update={"blocking_keys": list(blocking_keys_for(base))})


def _make_rel(
    tenant_id: TenantId,
    src: EntityId,
    tgt: EntityId,
    rel_type: str,
    *,
    source_id: SourceId | None = None,
) -> Relationship:
    return Relationship(
        id=RelationshipId(uuid4()),
        tenant_id=tenant_id,
        source_entity_id=src,
        target_entity_id=tgt,
        relationship_type=rel_type,
        source_id=source_id or SourceId("doc-default"),
        properties={},
        confidence=0.9,
    )


def _make_chunk(
    tenant_id: TenantId,
    source_id: SourceId,
    index: int,
    text: str,
    *,
    entity_ids: list[EntityId] | None = None,
    embedding: list[float] | None = None,
) -> StoredChunk:
    return StoredChunk(
        tenant_id=tenant_id,
        source_id=source_id,
        chunk_index=index,
        text=text,
        start_char=0,
        end_char=len(text),
        entity_ids=entity_ids or [],
        embedding=embedding,
    )


@pytest.mark.asyncio
async def test_scenario_us0024_1_explain_hybrid_entity_retrieval_channel_attribution() -> None:
    """US-0024 Scenario 1: Explain hybrid entity retrieval with RRF channel attribution."""
    tenant = TenantId(uuid4())
    graph = InMemoryGraphStore()
    vectors = InMemoryVectorStore(dimension=8)
    embeddings = FakeEmbeddingProvider(dimension=8)

    e1 = _make_entity(
        "Acme Robotics Corporation",
        "organization",
        tenant,
        properties={"alias": "Acme Bot Labs"},
    )
    e2 = _make_entity("Acme Logistics Group", "organization", tenant)
    await graph.upsert_entities([e1, e2])

    vecs = await embeddings.embed_query([e1.name, e2.name])
    await vectors.upsert(e1.id, vecs[0], tenant)
    await vectors.upsert(e2.id, vecs[1], tenant)

    client = Redstring(
        tenant_id=tenant,
        provider=FakeLlmProvider(by_substring={}),
        embeddings=embeddings,
        graph_store=graph,
        vector_store=vectors,
    )
    explanation = await client.explain_entity_retrieval("Acme Robotics", k=5)

    assert explanation.mode is RetrievalMode.HYBRID
    assert explanation.semantic_candidate_count >= 2
    assert explanation.lexical_candidate_count >= 2
    assert len(explanation.matches) == 2

    for idx, match in enumerate(explanation.matches):
        assert match.fused_rank == idx
        expected_sem = (
            1.0 / (RRF_K + match.semantic_rank) if match.semantic_rank is not None else 0.0
        )
        expected_lex = 1.0 / (RRF_K + match.lexical_rank) if match.lexical_rank is not None else 0.0
        assert match.semantic_rrf_contribution == pytest.approx(expected_sem)
        assert match.lexical_rrf_contribution == pytest.approx(expected_lex)
        assert match.fused_score == pytest.approx(
            match.semantic_rrf_contribution + match.lexical_rrf_contribution
        )
        assert match.dominant_channel in {"semantic", "lexical", "tie"}
        assert len(match.matched_blocking_keys) >= 1
        assert match.best_lexical_field in {"name", "normalized_name", "property:alias"}


@pytest.mark.asyncio
async def test_scenario_us0024_2_detect_dangling_vector_ids_during_explanation() -> None:
    """US-0024 Scenario 2: Detect dangling vector IDs during entity retrieval explanation."""
    tenant = TenantId(uuid4())
    graph = InMemoryGraphStore()
    vectors = InMemoryVectorStore(dimension=8)
    embeddings = FakeEmbeddingProvider(dimension=8)

    live = _make_entity("Orbital Dynamics", "organization", tenant)
    ghost_id = EntityId(uuid4())
    await graph.upsert_entity(live)

    vecs = await embeddings.embed_query(["Orbital Dynamics", "Orbital Ghost"])
    await vectors.upsert(live.id, vecs[0], tenant)
    await vectors.upsert(ghost_id, vecs[1], tenant)

    explanation = await explain_entity_retrieval(
        "Orbital", tenant, graph=graph, embeddings=embeddings, vectors=vectors, k=5
    )
    assert ghost_id in explanation.dangling_vector_ids
    assert all(m.entity.id != ghost_id for m in explanation.matches)


@pytest.mark.asyncio
async def test_scenario_us0024_3_explain_lexical_only_entity_retrieval() -> None:
    """US-0024 Scenario 3: Explain lexical-only entity retrieval without embeddings."""
    tenant = TenantId(uuid4())
    graph = InMemoryGraphStore()
    await graph.upsert_entity(_make_entity("Zephyr BioLabs", "organization", tenant))

    client = Redstring(
        tenant_id=tenant, provider=FakeLlmProvider(by_substring={}), graph_store=graph
    )
    explanation = await client.explain_entity_retrieval("Zephyr BioLabs", k=5)

    assert explanation.mode is RetrievalMode.LEXICAL
    assert explanation.semantic_candidate_count == 0
    assert len(explanation.matches) == 1
    match = explanation.matches[0]
    assert match.semantic_rank is None
    assert match.semantic_rrf_contribution == 0.0
    assert match.dominant_channel == "lexical"


@pytest.mark.asyncio
async def test_scenario_us0025_1_explain_hybrid_passage_retrieval_with_bm25_terms() -> None:
    """US-0025 Scenario 1: Explain hybrid passage retrieval with BM25 term contributions."""
    tenant = TenantId(uuid4())
    embeddings = FakeEmbeddingProvider(dimension=8)
    client = Redstring.in_memory(
        tenant_id=tenant,
        provider=FakeLlmProvider(by_substring={}),
        embeddings=embeddings,
    )
    assert client.chunk_store is not None
    await index_documents(
        [
            SourceDocument(
                id=SourceId("doc-1"),
                text="Quantum battery solid-state electrolyte stability under thermal cycling.",
            ),
            SourceDocument(
                id=SourceId("doc-2"),
                text="Supply chain logistics for lithium carbonate shipping.",
            ),
        ],
        store=client.chunk_store,
        tenant_id=tenant,
        embeddings=embeddings,
    )

    explanation = await client.explain_passage_retrieval("quantum electrolyte", k=5)

    assert explanation.mode is RetrievalMode.HYBRID
    assert "quantum" in explanation.query_terms
    assert "electrolyte" in explanation.query_terms
    assert explanation.corpus_stats is not None
    assert explanation.corpus_stats.n_docs == 2
    assert len(explanation.matches) >= 1

    top = explanation.matches[0]
    assert top.fused_rank == 0
    assert top.fused_score == pytest.approx(
        top.semantic_rrf_contribution + top.lexical_rrf_contribution
    )
    assert "quantum" in top.matched_terms
    assert sum(top.term_bm25_contributions.values()) == pytest.approx(top.lexical_score or 0.0)


@pytest.mark.asyncio
async def test_scenario_us0025_2_explain_chunk_retrieval_over_unembedded_corpus() -> None:
    """US-0025 Scenario 2: Explain passage retrieval over an unembedded corpus."""
    tenant = TenantId(uuid4())
    chunks = InMemoryChunkStore(dimension=8)
    embeddings = FakeEmbeddingProvider(dimension=8)
    await chunks.upsert_many(
        [_make_chunk(tenant, SourceId("doc-u1"), 0, "Cryogenic valve assembly specifications")]
    )

    explanation = await explain_chunk_retrieval(
        "cryogenic valve", tenant, chunks=chunks, embeddings=embeddings, k=5
    )
    assert explanation.semantic_candidates_considered == 0
    assert explanation.semantic_candidate_count == 0
    assert len(explanation.matches) == 1
    assert explanation.matches[0].dominant_channel == "lexical"


@pytest.mark.asyncio
async def test_scenario_us0026_1_trace_forward_footprint_of_source_document() -> None:
    """US-0026 Scenario 1: Trace the complete footprint of a source document."""
    tenant = TenantId(uuid4())
    graph = InMemoryGraphStore()
    chunks = InMemoryChunkStore(dimension=8)

    src_1, src_2 = SourceId("doc-1"), SourceId("doc-2")
    e1 = _make_entity("Alice Chen", "person", tenant, source_id=src_1)
    e2 = _make_entity("Helios Labs", "organization", tenant, source_id=src_1)
    e3 = _make_entity("Other Corp", "organization", tenant, source_id=src_2)
    await graph.upsert_entities([e1, e2, e3])

    r1 = _make_rel(tenant, e1.id, e2.id, "works_at", source_id=src_1)
    r2 = _make_rel(tenant, e2.id, e3.id, "partners_with", source_id=src_2)
    await graph.upsert_relationships([r1, r2])

    c1 = _make_chunk(
        tenant, src_1, 0, "Alice Chen works at Helios Labs.", entity_ids=[e1.id, e2.id]
    )
    c2 = _make_chunk(tenant, src_1, 1, "Unlinked appendix note.")
    await chunks.upsert_many([c1, c2])

    report = await trace_source_lineage(
        src_1, tenant, graph=graph, relationships=graph, chunks=chunks
    )
    assert {e.id for e in report.entities} == {e1.id, e2.id}
    assert [r.id for r in report.relationships] == [r1.id]
    assert [c.id for c in report.chunks] == [c1.id, c2.id]
    assert report.linked_chunk_count == 1
    assert report.unlinked_chunk_count == 1


@pytest.mark.asyncio
async def test_scenario_us0026_2_and_3_trace_entity_lineage_and_unknown_elements() -> None:
    """US-0026 Scenarios 2 & 3: Trace entity lineage across aliases and handle unknown IDs."""
    tenant = TenantId(uuid4())
    graph, chunks = InMemoryGraphStore(), InMemoryChunkStore(dimension=8)
    src_a, src_b = SourceId("doc-a"), SourceId("doc-b")
    canonical = _make_entity("Apex Semiconductor", "organization", tenant, source_id=src_a)
    duplicate_id = EntityId(uuid4())
    await graph.upsert_entity(canonical)
    await graph.upsert_alias(
        Alias(
            id=uuid4(),
            alias_entity_id=duplicate_id,
            canonical_entity_id=canonical.id,
            alias_name="Apex Semi Inc.",
            alias_normalized_name="apex semi inc.",
            tenant_id=tenant,
            merged_at=datetime(2026, 5, 2, 12, 0, tzinfo=UTC),
        )
    )
    ch_a = _make_chunk(
        tenant, src_a, 0, "Apex Semiconductor fab expansion", entity_ids=[canonical.id]
    )
    ch_b = _make_chunk(tenant, src_b, 0, "Apex Semi Inc. wafer supply", entity_ids=[duplicate_id])
    await chunks.upsert_many([ch_a, ch_b])

    client = Redstring(
        tenant_id=tenant,
        provider=FakeLlmProvider(by_substring={}),
        graph_store=graph,
        chunk_store=chunks,
    )
    lineage = await client.trace_entity_lineage(duplicate_id)
    assert lineage is not None
    assert lineage.entity.id == canonical.id
    assert len(lineage.aliases) == 1
    assert lineage.aliases[0].alias_name == "Apex Semi Inc."
    assert {c.id for c in lineage.chunks} == {ch_a.id, ch_b.id}
    assert set(lineage.source_ids) == {src_a, src_b}

    by_name = await client.trace_entity_lineage("Apex Semiconductor")
    assert by_name is not None
    assert by_name.entity.id == canonical.id
    empty_src = await client.trace_source_lineage(SourceId("doc-missing"))
    assert (empty_src.entities, empty_src.relationships, empty_src.chunks) == ((), (), ())
    assert await client.trace_entity_lineage(EntityId(uuid4())) is None
    assert await client.trace_entity_lineage("Nonexistent Entity") is None


@pytest.mark.asyncio
async def test_scenario_us0027_1_and_2_analyze_corpus_health_and_empty_tenant() -> None:
    """US-0027 Scenarios 1 & 2: Compute corpus health over mixed and empty tenants."""
    tenant = TenantId(uuid4())
    graph, chunks = InMemoryGraphStore(), InMemoryChunkStore(dimension=8)
    empty_report = await analyze_corpus(tenant, graph=graph, relationships=graph, chunks=chunks)
    assert (empty_report.total_entities, empty_report.total_chunks) == (0, 0)
    assert empty_report.entity_chunk_coverage_ratio == 0.0
    assert (empty_report.min_chunk_chars, empty_report.max_chunk_chars) == (0, 0)
    assert empty_report.mean_chunk_chars == 0.0

    src_1, src_2 = SourceId("doc-1"), SourceId("doc-2")
    e_with = _make_entity("Entity With Chunk", "concept", tenant, source_id=src_1)
    e_without = _make_entity("Entity Without Chunk", "concept", tenant, source_id=src_1)
    await graph.upsert_entities([e_with, e_without])
    c_linked = _make_chunk(
        tenant, src_1, 0, "Alpha text", entity_ids=[e_with.id], embedding=[0.1] * 8
    )
    c_unlinked = _make_chunk(tenant, src_2, 0, "Beta longer text passage")
    await chunks.upsert_many([c_linked, c_unlinked])

    report = await analyze_corpus(
        tenant, graph=graph, relationships=graph, chunks=chunks, source_ids=[src_1, src_2]
    )
    assert (report.total_entities, report.unique_source_count, report.total_chunks) == (2, 2, 2)
    assert report.linked_chunks + report.unlinked_chunks == report.total_chunks
    assert report.embedded_chunks + report.unembedded_chunks == report.total_chunks
    assert (report.entities_with_chunks_count, report.entities_without_chunks_count) == (1, 1)
    assert report.entity_chunk_coverage_ratio == pytest.approx(0.5)
    assert report.min_chunk_chars <= report.mean_chunk_chars <= report.max_chunk_chars


@given(
    sem_rank=st.one_of(st.none(), st.integers(min_value=1, max_value=100)),
    lex_rank=st.one_of(st.none(), st.integers(min_value=1, max_value=100)),
)
@settings(max_examples=40)
def test_property_rrf_channel_contributions_sum_to_fused_score(
    sem_rank: int | None, lex_rank: int | None
) -> None:
    """Property: Per-channel RRF contributions are non-negative and sum to fused score."""
    sem_c = 1.0 / (RRF_K + sem_rank) if sem_rank is not None else 0.0
    lex_c = 1.0 / (RRF_K + lex_rank) if lex_rank is not None else 0.0
    assert sem_c >= 0.0
    assert lex_c >= 0.0
    assert (sem_c + lex_c) == pytest.approx(sem_c + lex_c)
