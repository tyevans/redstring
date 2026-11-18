"""Contract tests for the diagnostics methods on the `Redstring` facade.

The facade is a thin layer over the composition functions. These tests check
the three behaviours the facade owns: the missing-chunk-store refusal, tenant
scoping of its diagnostics, and delegation of the static schema renderer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from redstring.composition.client import Redstring
from redstring.composition.schema_audit import format_schema_mermaid
from redstring.domain.blocking import blocking_keys_for
from redstring.domain.entity import Entity
from redstring.domain.ids import EntityId, RelationshipId, SourceId, TenantId
from redstring.domain.normalization import normalize_name
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.relationship import Relationship
from redstring.extraction.domains.loader import get_schema_directory, load_schema_from_file
from redstring.graph.adapters.memory import InMemoryGraphStore
from redstring.llm.adapters.fake import FakeLlmProvider
from redstring.llm.adapters.fake_embedding import FakeEmbeddingProvider

OBSERVED = datetime(2026, 2, 9, 11, 7, tzinfo=UTC)


def _entity(
    name: str,
    tenant_id: TenantId,
    *,
    entity_type: str = "person",
    source_id: str = "doc-1",
) -> Entity:
    entity = Entity(
        id=EntityId(uuid4()),
        tenant_id=tenant_id,
        name=name,
        normalized_name=normalize_name(name),
        entity_type=entity_type,
        provenance=Provenance(
            observed_at=OBSERVED,
            extraction_method=ExtractionMethod.MANUAL,
            confidence=1.0,
            source_id=SourceId(source_id),
        ),
    )
    # The store only groups by blocking keys, so an entity without them is
    # never offered to consolidation as a candidate pair.
    return entity.model_copy(update={"blocking_keys": frozenset(blocking_keys_for(entity))})


async def test_chunk_retrieval_without_a_chunk_store_is_refused_by_name() -> None:
    tenant = TenantId(uuid4())
    client = Redstring(
        tenant_id=tenant,
        provider=FakeLlmProvider(by_substring={}),
        graph_store=InMemoryGraphStore(),
    )

    with pytest.raises(ValueError, match="No chunk store configured for this Redstring client"):
        await client.explain_chunk_retrieval("Ada Lovelace")

    with pytest.raises(ValueError, match="No chunk store configured"):
        await client.explain_passage_retrieval("Ada Lovelace")


async def test_client_analyze_corpus_reports_its_own_tenant_only() -> None:
    tenant = TenantId(uuid4())
    other = TenantId(uuid4())
    client = Redstring.in_memory(
        provider=FakeLlmProvider(by_substring={}),
        embeddings=FakeEmbeddingProvider(dimension=8),
        tenant_id=tenant,
    )
    graph = client.graph_store
    ada = _entity("Ada Lovelace", tenant)
    babbage = _entity("Charles Babbage", tenant)
    await graph.upsert_entities([ada, babbage, _entity("Stranger", other)])
    await graph.upsert_relationship(
        Relationship(
            id=RelationshipId(uuid4()),
            tenant_id=tenant,
            source_entity_id=ada.id,
            target_entity_id=babbage.id,
            relationship_type="WORKED_WITH",
            source_id=SourceId("doc-1"),
            confidence=0.9,
        )
    )

    report = await client.analyze_corpus()

    assert report.tenant_id == tenant
    assert report.entity_count == 2
    assert report.relationship_count == 1
    assert report.unique_source_count == 1
    assert report.entities_without_chunks_count == 2


async def test_client_consolidate_tenant_merges_only_its_own_duplicates() -> None:
    tenant = TenantId(uuid4())
    other = TenantId(uuid4())
    client = Redstring.in_memory(
        provider=FakeLlmProvider(by_substring={}),
        embeddings=FakeEmbeddingProvider(dimension=8),
        tenant_id=tenant,
    )
    graph = client.graph_store
    keep = _entity("Acme Corporation", tenant, entity_type="organization")
    dup = _entity("Acme Corporation", tenant, entity_type="organization")
    foreign_keep = _entity("Acme Corporation", other, entity_type="organization")
    foreign_dup = _entity("Acme Corporation", other, entity_type="organization")
    await graph.upsert_entities([keep, dup, foreign_keep, foreign_dup])

    report = await client.consolidate_tenant()

    assert report.tenant_id == tenant
    assert report.merges_executed == 1
    assert report.entities_absorbed == 1
    assert await graph.find_aliases(foreign_dup.id, other) == []
    assert await graph.find_aliases(foreign_keep.id, other) == []


def test_the_static_schema_renderer_accepts_a_registry_id() -> None:
    rendered = Redstring.format_schema_mermaid("literature_fiction")

    expected = format_schema_mermaid(
        load_schema_from_file("literature_fiction.yaml", schema_dir=get_schema_directory())
    )
    assert rendered == expected
    assert rendered.startswith("flowchart LR\n")
