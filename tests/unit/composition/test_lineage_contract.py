"""Contract tests for `trace_entity_lineage` and `analyze_corpus`.

Expected values are literals written from the documented contract. Every
store is a real in-memory adapter.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from redstring.chunks.adapters.memory import InMemoryChunkStore
from redstring.composition.lineage import analyze_corpus, trace_entity_lineage
from redstring.domain.alias import Alias
from redstring.domain.chunk import StoredChunk
from redstring.domain.entity import Entity
from redstring.domain.ids import EntityId, RelationshipId, SourceId, TenantId
from redstring.domain.normalization import normalize_name
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.relationship import Relationship
from redstring.graph.adapters.memory import InMemoryGraphStore

OBSERVED = datetime(2026, 2, 9, 11, 7, tzinfo=UTC)
DIMENSION = 8


def _entity(name: str, tenant_id: TenantId, *, source_id: str | None = None) -> Entity:
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
            source_id=SourceId(source_id) if source_id is not None else None,
        ),
    )


def _relationship(
    tenant_id: TenantId,
    source: Entity,
    target: Entity,
    *,
    source_id: str | None,
) -> Relationship:
    return Relationship(
        id=RelationshipId(uuid4()),
        tenant_id=tenant_id,
        source_entity_id=source.id,
        target_entity_id=target.id,
        relationship_type="KNOWS",
        source_id=SourceId(source_id) if source_id is not None else None,
        confidence=0.9,
    )


def _chunk(
    tenant_id: TenantId,
    *,
    text: str,
    chunk_index: int,
    source_id: str = "doc-1",
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


def _alias(canonical: Entity, absorbed: Entity, tenant_id: TenantId) -> Alias:
    return Alias(
        id=uuid4(),
        tenant_id=tenant_id,
        canonical_entity_id=canonical.id,
        alias_entity_id=absorbed.id,
        alias_name=absorbed.name,
        merged_at=OBSERVED,
    )


async def test_a_relationship_source_is_counted_among_the_entity_sources() -> None:
    tenant = TenantId(uuid4())
    graph = InMemoryGraphStore()
    ada = _entity("Ada Lovelace", tenant, source_id="doc-a")
    babbage = _entity("Charles Babbage", tenant)
    edge = _relationship(tenant, ada, babbage, source_id="doc-r")
    await graph.upsert_entities([ada, babbage])
    await graph.upsert_relationship(edge)

    report = await trace_entity_lineage(ada.id, tenant, graph=graph, relationships=graph)

    assert report is not None
    assert report.source_ids == (SourceId("doc-a"), SourceId("doc-r"))
    assert report.relationships == (edge,)


async def test_an_alias_id_resolves_to_the_canonical_entity_when_aliases_are_supplied() -> None:
    tenant = TenantId(uuid4())
    graph = InMemoryGraphStore()
    canonical = _entity("Ada Lovelace", tenant)
    absorbed = _entity("A. Lovelace", tenant)
    alias = _alias(canonical, absorbed, tenant)
    await graph.upsert_entities([canonical, absorbed])
    await graph.upsert_alias(alias)

    report = await trace_entity_lineage(
        absorbed.id, tenant, graph=graph, relationships=graph, aliases=graph
    )

    assert report is not None
    assert report.entity.id == canonical.id
    assert report.absorbed_aliases == (alias,)
    assert report.aliases == report.absorbed_aliases


async def test_without_an_alias_store_the_absorbed_id_is_traced_as_itself() -> None:
    tenant = TenantId(uuid4())
    graph = InMemoryGraphStore()
    canonical = _entity("Ada Lovelace", tenant)
    absorbed = _entity("A. Lovelace", tenant)
    await graph.upsert_entities([canonical, absorbed])
    await graph.upsert_alias(_alias(canonical, absorbed, tenant))

    report = await trace_entity_lineage(absorbed.id, tenant, graph=graph, relationships=graph)

    assert report is not None
    assert report.entity.id == absorbed.id
    assert report.absorbed_aliases == ()


async def test_chunks_of_an_absorbed_alias_join_the_canonical_lineage_once_and_in_order() -> None:
    tenant = TenantId(uuid4())
    graph = InMemoryGraphStore()
    chunks = InMemoryChunkStore(dimension=DIMENSION)
    canonical = _entity("Ada Lovelace", tenant)
    absorbed = _entity("A. Lovelace", tenant)
    await graph.upsert_entities([canonical, absorbed])
    await graph.upsert_alias(_alias(canonical, absorbed, tenant))

    canonical_only = _chunk(
        tenant, text="Ada wrote the first program.", chunk_index=0, entity_ids=[canonical.id]
    )
    alias_only = _chunk(
        tenant, text="A. Lovelace annotated the notes.", chunk_index=1, entity_ids=[absorbed.id]
    )
    both = _chunk(
        tenant,
        text="Ada and A. Lovelace are one person.",
        chunk_index=2,
        entity_ids=[canonical.id, absorbed.id],
    )
    unlinked = _chunk(tenant, text="An unrelated passage.", chunk_index=3)
    await chunks.upsert_many([alias_only, unlinked, both, canonical_only])

    with_aliases = await trace_entity_lineage(
        canonical.id, tenant, graph=graph, relationships=graph, aliases=graph, chunks=chunks
    )
    without_aliases = await trace_entity_lineage(
        canonical.id, tenant, graph=graph, relationships=graph, chunks=chunks
    )

    assert with_aliases is not None
    assert without_aliases is not None
    # Sorted by (source, chunk_index, id). `both` appears once, not twice.
    assert [c.id for c in with_aliases.supporting_chunks] == [
        canonical_only.id,
        alias_only.id,
        both.id,
    ]
    assert with_aliases.source_ids == (SourceId("doc-1"),)
    assert [c.id for c in without_aliases.supporting_chunks] == [canonical_only.id, both.id]


async def test_a_relationship_only_source_is_counted_in_the_corpus() -> None:
    tenant = TenantId(uuid4())
    graph = InMemoryGraphStore()
    ada = _entity("Ada Lovelace", tenant, source_id="doc-a")
    babbage = _entity("Charles Babbage", tenant)
    await graph.upsert_entities([ada, babbage])
    await graph.upsert_relationship(_relationship(tenant, ada, babbage, source_id="doc-r"))

    report = await analyze_corpus(tenant, graph=graph, relationships=graph)

    assert report.entity_count == 2
    assert report.relationship_count == 1
    assert report.unique_source_count == 2


async def test_an_unknown_entity_id_has_no_lineage() -> None:
    tenant = TenantId(uuid4())
    graph = InMemoryGraphStore()

    report = await trace_entity_lineage(
        EntityId(uuid4()), tenant, graph=graph, relationships=graph, aliases=graph
    )

    assert report is None


async def test_a_relationship_with_no_recorded_source_adds_no_source() -> None:
    tenant = TenantId(uuid4())
    graph = InMemoryGraphStore()
    ada = _entity("Ada Lovelace", tenant, source_id="doc-a")
    babbage = _entity("Charles Babbage", tenant)
    await graph.upsert_entities([ada, babbage])
    await graph.upsert_relationship(_relationship(tenant, ada, babbage, source_id=None))

    report = await trace_entity_lineage(ada.id, tenant, graph=graph, relationships=graph)

    assert report is not None
    assert report.source_ids == (SourceId("doc-a"),)
    assert len(report.relationships) == 1


async def test_a_relationship_with_no_recorded_source_is_not_a_corpus_source() -> None:
    tenant = TenantId(uuid4())
    graph = InMemoryGraphStore()
    ada = _entity("Ada Lovelace", tenant, source_id="doc-a")
    babbage = _entity("Charles Babbage", tenant, source_id="doc-b")
    await graph.upsert_entities([ada, babbage])
    await graph.upsert_relationship(_relationship(tenant, ada, babbage, source_id=None))

    report = await analyze_corpus(tenant, graph=graph, relationships=graph)

    assert report.relationship_count == 1
    assert report.unique_source_count == 2
