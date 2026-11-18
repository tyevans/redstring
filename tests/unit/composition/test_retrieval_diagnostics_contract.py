"""Contract tests for `explain_entity_retrieval` and `explain_chunk_retrieval`.

Each test states the behaviour it checks in its name, and the expected values
are written as literals. Every store is a real in-memory adapter.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from redstring.chunks.adapters.memory import InMemoryChunkStore
from redstring.composition.retrieval_diagnostics import (
    explain_chunk_retrieval,
    explain_entity_retrieval,
)
from redstring.domain.blocking import blocking_keys_for, query_blocking_keys
from redstring.domain.chunk import StoredChunk
from redstring.domain.entity import Entity
from redstring.domain.fusion import RRF_K
from redstring.domain.ids import EntityId, SourceId, TenantId
from redstring.domain.normalization import normalize_name
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.retrieval import RetrievalMode
from redstring.domain.tokenize import tokenize
from redstring.graph.adapters.memory import InMemoryGraphStore
from redstring.llm.adapters.fake_embedding import FakeEmbeddingProvider
from redstring.vector.adapters.memory import InMemoryVectorStore

OBSERVED = datetime(2026, 2, 9, 11, 7, tzinfo=UTC)

#: `nomic-embed-text` width. Realistic on purpose; see the retrieval tests.
DIMENSION = 768


def _entity(
    name: str,
    tenant_id: TenantId,
    *,
    entity_type: str = "person",
    properties: dict[str, object] | None = None,
    normalized_name: str | None = None,
    blocking_keys: frozenset[str] | None = None,
) -> Entity:
    entity = Entity(
        id=EntityId(uuid4()),
        tenant_id=tenant_id,
        name=name,
        normalized_name=normalized_name if normalized_name is not None else normalize_name(name),
        entity_type=entity_type,
        properties=properties or {},
        provenance=Provenance(
            observed_at=OBSERVED,
            extraction_method=ExtractionMethod.MANUAL,
            confidence=1.0,
        ),
    )
    keys = blocking_keys if blocking_keys is not None else frozenset(blocking_keys_for(entity))
    return entity.model_copy(update={"blocking_keys": keys})


def _chunk(
    tenant_id: TenantId,
    *,
    text: str,
    source_id: str = "doc-1",
    chunk_index: int = 0,
    entity_ids: list[EntityId] | None = None,
) -> StoredChunk:
    return StoredChunk(
        tenant_id=tenant_id,
        source_id=source_id,
        text=text,
        chunk_index=chunk_index,
        start_char=0,
        end_char=len(text),
        entity_ids=entity_ids or [],
    )


# ---------------------------------------------------------------------------
# Refusals shared by both entry points
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "k", "overfetch", "message"),
    [
        ("   ", 5, 3, "query must not be blank"),
        ("Ada", -3, 3, "k must not be negative, got -3"),
        ("Ada", 5, 0, "overfetch must be at least 1, got 0"),
    ],
)
async def test_entity_retrieval_refuses_bad_parameters_and_names_them(
    query: str, k: int, overfetch: int, message: str
) -> None:
    tenant = TenantId(uuid4())
    with pytest.raises(ValueError, match=message):
        await explain_entity_retrieval(
            query, tenant, graph=InMemoryGraphStore(), k=k, overfetch=overfetch
        )


@pytest.mark.parametrize(
    ("query", "k", "overfetch", "message"),
    [
        ("   ", 5, 3, "query must not be blank"),
        ("Ada", -1, 3, "k must not be negative, got -1"),
        ("Ada", 5, 0, "overfetch must be at least 1, got 0"),
    ],
)
async def test_chunk_retrieval_refuses_bad_parameters_and_names_them(
    query: str, k: int, overfetch: int, message: str
) -> None:
    tenant = TenantId(uuid4())
    chunks = InMemoryChunkStore(dimension=DIMENSION)
    with pytest.raises(ValueError, match=message):
        await explain_chunk_retrieval(query, tenant, chunks=chunks, k=k, overfetch=overfetch)


async def test_semantic_entity_retrieval_without_embeddings_names_the_mode_and_the_gap() -> None:
    tenant = TenantId(uuid4())
    with pytest.raises(
        ValueError, match=r"cannot serve mode 'semantic' without embeddings and vectors"
    ):
        await explain_entity_retrieval(
            "Ada", tenant, graph=InMemoryGraphStore(), mode=RetrievalMode.SEMANTIC
        )


async def test_semantic_chunk_retrieval_without_embeddings_names_the_mode_and_the_gap() -> None:
    tenant = TenantId(uuid4())
    chunks = InMemoryChunkStore(dimension=DIMENSION)
    with pytest.raises(ValueError, match=r"cannot serve mode 'semantic' without embeddings"):
        await explain_chunk_retrieval("Ada", tenant, chunks=chunks, mode=RetrievalMode.SEMANTIC)


# ---------------------------------------------------------------------------
# Entity retrieval
# ---------------------------------------------------------------------------


async def test_k_zero_returns_no_matches_even_when_an_entity_matches_and_still_reports_keys() -> (
    None
):
    tenant = TenantId(uuid4())
    graph = InMemoryGraphStore()
    await graph.upsert_entities([_entity("Ada Lovelace", tenant)])

    result = await explain_entity_retrieval("Ada Lovelace", tenant, graph=graph, k=0)

    assert result.matches == ()
    assert result.semantic_candidate_count == 0
    assert result.lexical_candidate_count == 0
    assert result.dangling_vector_ids == ()
    assert result.query_blocking_keys == tuple(sorted(query_blocking_keys("Ada Lovelace")))
    assert result.query_blocking_keys, "the query keys are computed even when k is zero"


async def test_entity_types_filter_keeps_only_named_types_in_the_lexical_channel() -> None:
    tenant = TenantId(uuid4())
    graph = InMemoryGraphStore()
    person = _entity("Ada Lovelace", tenant, entity_type="person")
    org = _entity("Ada Lovelace Analytical Engines", tenant, entity_type="organization")
    await graph.upsert_entities([person, org])

    unfiltered = await explain_entity_retrieval(
        "Ada Lovelace", tenant, graph=graph, mode=RetrievalMode.LEXICAL
    )
    assert {m.entity.id for m in unfiltered.matches} == {person.id, org.id}

    filtered = await explain_entity_retrieval(
        "Ada Lovelace",
        tenant,
        graph=graph,
        mode=RetrievalMode.LEXICAL,
        entity_types=["organization"],
    )
    assert [m.entity.id for m in filtered.matches] == [org.id]
    assert filtered.lexical_candidate_count == 1


async def test_a_semantic_only_hit_is_resolved_from_the_graph_and_has_no_lexical_field() -> None:
    tenant = TenantId(uuid4())
    graph = InMemoryGraphStore()
    embeddings = FakeEmbeddingProvider(dimension=DIMENSION)
    vectors = InMemoryVectorStore(dimension=DIMENSION)
    # Shares no blocking key with the query, so the lexical channel cannot
    # reach it. Only the vector channel can, and it is the only route to it.
    unrelated = _entity(
        "Zebra Farm",
        tenant,
        entity_type="location",
        blocking_keys=frozenset({"no-overlap-key"}),
    )
    await graph.upsert_entities([unrelated])
    [query_vector] = await embeddings.embed_query(["Ada Lovelace"])
    await vectors.upsert(unrelated.id, query_vector, tenant)

    result = await explain_entity_retrieval(
        "Ada Lovelace",
        tenant,
        graph=graph,
        embeddings=embeddings,
        vectors=vectors,
        mode=RetrievalMode.HYBRID,
    )

    [match] = result.matches
    assert match.entity.id == unrelated.id
    assert match.semantic_rank == 1
    assert match.semantic_rrf_contribution == pytest.approx(1.0 / (RRF_K + 1))
    assert match.lexical_rank is None
    assert match.lexical_rrf_contribution == 0.0
    assert match.best_lexical_field is None
    assert match.dominant_channel == "semantic"
    assert result.lexical_candidate_count == 0
    assert result.dangling_vector_ids == ()


async def test_a_vector_hit_with_no_graph_entity_is_reported_as_dangling() -> None:
    tenant = TenantId(uuid4())
    graph = InMemoryGraphStore()
    embeddings = FakeEmbeddingProvider(dimension=DIMENSION)
    vectors = InMemoryVectorStore(dimension=DIMENSION)
    ghost = EntityId(uuid4())
    [query_vector] = await embeddings.embed_query(["Ada Lovelace"])
    await vectors.upsert(ghost, query_vector, tenant)

    result = await explain_entity_retrieval(
        "Ada Lovelace",
        tenant,
        graph=graph,
        embeddings=embeddings,
        vectors=vectors,
        mode=RetrievalMode.SEMANTIC,
    )

    assert result.matches == ()
    assert result.semantic_candidate_count == 1
    assert result.dangling_vector_ids == (ghost,)


async def test_best_lexical_field_names_a_property_that_beats_the_name() -> None:
    tenant = TenantId(uuid4())
    graph = InMemoryGraphStore()
    entity = _entity(
        "Apple Orchard",
        tenant,
        properties={"alias": "Zebra"},
        blocking_keys=frozenset(query_blocking_keys("Zebra")),
    )
    await graph.upsert_entities([entity])

    result = await explain_entity_retrieval(
        "Zebra", tenant, graph=graph, mode=RetrievalMode.LEXICAL
    )

    [match] = result.matches
    assert match.best_lexical_field == "property:alias"


async def test_best_lexical_field_names_normalized_name_when_it_matches_better_than_name() -> None:
    tenant = TenantId(uuid4())
    graph = InMemoryGraphStore()
    # A caller-stored normalized form that differs from the display name.
    # Consistent entities cannot reach this branch: both sides of the
    # comparison normalise identically inside `string_similarity`.
    entity = _entity(
        "Ada Lovelace",
        tenant,
        normalized_name="lovelace",
        blocking_keys=frozenset(query_blocking_keys("lovelace")),
    )
    await graph.upsert_entities([entity])

    result = await explain_entity_retrieval(
        "lovelace", tenant, graph=graph, mode=RetrievalMode.LEXICAL
    )

    [match] = result.matches
    assert match.best_lexical_field == "normalized_name"


# ---------------------------------------------------------------------------
# Chunk retrieval
# ---------------------------------------------------------------------------


async def test_chunk_k_zero_returns_no_stats_and_no_matches_but_tokenises_the_query() -> None:
    tenant = TenantId(uuid4())
    chunks = InMemoryChunkStore(dimension=DIMENSION)
    await chunks.upsert_many([_chunk(tenant, text="Ada Lovelace wrote notes on the engine")])

    result = await explain_chunk_retrieval("Ada Lovelace", tenant, chunks=chunks, k=0)

    assert result.matches == ()
    assert result.corpus_stats is None
    assert result.semantic_candidate_count == 0
    assert result.lexical_candidate_count == 0
    assert result.query_terms == tuple(tokenize("Ada Lovelace"))
    assert "lovelace" in result.query_terms


async def test_source_id_is_not_needed_for_a_chunk_match_to_be_explained() -> None:
    tenant = TenantId(uuid4())
    chunks = InMemoryChunkStore(dimension=DIMENSION)
    chunk = _chunk(tenant, text="Ada Lovelace wrote notes", source_id="doc-7")
    await chunks.upsert_many([chunk])

    result = await explain_chunk_retrieval("Ada Lovelace", tenant, chunks=chunks)

    [match] = result.matches
    assert match.chunk.source_id == SourceId("doc-7")
    assert match.lexical_rank == 1
    assert match.semantic_rank is None
    assert match.dominant_channel == "lexical"
