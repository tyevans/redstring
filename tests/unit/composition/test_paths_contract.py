"""Contract tests for `composition.paths.explain_path`.

Documented contract (module and function docstrings): find the shortest
relationship path within `max_depth` hops, traversing edges in both
directions, breaking ties deterministically by neighbour `EntityId` and then
`RelationshipId`, and attaching the chunks that cite entities on the path when
a `ChunkReader` is supplied.

Every expected value is a literal written from that contract.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from redstring.chunks.adapters.memory import InMemoryChunkStore
from redstring.composition.paths import explain_path
from redstring.domain.chunk import StoredChunk
from redstring.domain.entity import Entity
from redstring.domain.ids import EntityId, RelationshipId, SourceId, TenantId
from redstring.domain.normalization import normalize_name
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.relationship import Relationship
from redstring.graph.adapters.memory import InMemoryGraphStore

TENANT = TenantId(UUID("00000000-0000-4000-8000-0000000000aa"))
PROVENANCE = Provenance(
    observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    extraction_method=ExtractionMethod.MANUAL,
    confidence=1.0,
)


def _eid(n: int) -> EntityId:
    return EntityId(UUID(int=n))


def _rid(n: int) -> RelationshipId:
    return RelationshipId(UUID(int=n))


def _entity(n: int, name: str) -> Entity:
    return Entity(
        id=_eid(n),
        tenant_id=TENANT,
        name=name,
        normalized_name=normalize_name(name),
        entity_type="Person",
        provenance=PROVENANCE,
    )


def _rel(rel_n: int, source_n: int, target_n: int, rel_type: str = "KNOWS") -> Relationship:
    return Relationship(
        id=_rid(rel_n),
        tenant_id=TENANT,
        source_entity_id=_eid(source_n),
        target_entity_id=_eid(target_n),
        relationship_type=rel_type,
        confidence=1.0,
    )


class _RelationshipList:
    """A `RelationshipStore` stand-in that returns edges in the order it was given them."""

    def __init__(self, rels: list[Relationship]) -> None:
        self._rels = rels

    async def get_relationships(
        self, entity_id: EntityId, tenant_id: TenantId, **_: Any
    ) -> list[Relationship]:
        return [r for r in self._rels if entity_id in (r.source_entity_id, r.target_entity_id)]


class _EntityOnlyReader:
    """An `EntityReader` that is not a `RelationshipStore`: it has no edge methods."""

    def __init__(self, entities: list[Entity]) -> None:
        self._by_id = {e.id: e for e in entities}

    async def get_entity(self, entity_id: EntityId, tenant_id: TenantId) -> Entity | None:
        return self._by_id.get(entity_id)

    async def find_entities(
        self,
        tenant_id: TenantId,
        *,
        name: str | None = None,
        limit: int = 50,
        after: EntityId | None = None,
    ) -> list[Entity]:
        return [e for e in self._by_id.values() if name is None or e.normalized_name == name]


async def test_max_depth_below_one_is_refused_naming_the_value() -> None:
    store = InMemoryGraphStore()
    with pytest.raises(ValueError, match="max_depth must be at least 1, got 0"):
        await explain_path(_eid(1), _eid(2), tenant_id=TENANT, graph=store, max_depth=0)


async def test_graph_without_relationship_capability_is_refused_when_no_store_given() -> None:
    reader = _EntityOnlyReader([_entity(1, "Ada"), _entity(2, "Grace")])
    with pytest.raises(
        TypeError,
        match="relationships must be provided when graph does not implement RelationshipStore",
    ):
        await explain_path(_eid(1), _eid(2), tenant_id=TENANT, graph=reader)


async def test_explicit_relationship_store_is_used_when_graph_lacks_edge_methods() -> None:
    reader = _EntityOnlyReader([_entity(1, "Ada"), _entity(3, "Grace")])
    edges = _RelationshipList([_rel(0x10, 1, 3)])

    explanation = await explain_path(
        _eid(1), _eid(3), tenant_id=TENANT, graph=reader, relationships=edges
    )

    assert explanation is not None
    assert [e.id for e in explanation.entities] == [_eid(1), _eid(3)]
    assert [r.id for r in explanation.relationships] == [_rid(0x10)]


async def test_same_source_and_target_returns_one_entity_and_only_its_chunks() -> None:
    store = InMemoryGraphStore()
    ada = _entity(1, "Ada")
    grace = _entity(3, "Grace")
    await store.upsert_entities([ada, grace])

    chunk_store = InMemoryChunkStore(dimension=4)
    ada_chunk = StoredChunk(
        tenant_id=TENANT,
        source_id=SourceId("doc-ada"),
        text="Ada wrote the notes.",
        chunk_index=0,
        start_char=0,
        end_char=20,
        entity_ids=[ada.id],
    )
    grace_chunk = StoredChunk(
        tenant_id=TENANT,
        source_id=SourceId("doc-grace"),
        text="Grace compiled the code.",
        chunk_index=0,
        start_char=0,
        end_char=24,
        entity_ids=[grace.id],
    )
    await chunk_store.upsert_many([ada_chunk, grace_chunk])

    explanation = await explain_path(
        ada.id,
        ada.id,
        tenant_id=TENANT,
        graph=store,
        relationships=store,
        chunks=chunk_store,
    )

    assert explanation is not None
    assert [e.id for e in explanation.entities] == [ada.id]
    assert explanation.relationships == ()
    assert [c.id for c in explanation.chunks] == [ada_chunk.id]


async def test_parallel_edges_to_one_neighbour_resolve_to_the_smaller_relationship_id() -> None:
    # Two edges join Ada and Charles. The larger id is stored first, so an
    # implementation that keeps storage order would return it instead.
    store = InMemoryGraphStore()
    await store.upsert_entities([_entity(1, "Ada"), _entity(2, "Charles"), _entity(3, "Grace")])
    edges = _RelationshipList(
        [
            _rel(0x20, 1, 2, "KNOWS"),
            _rel(0x10, 1, 2, "KNOWS"),
            _rel(0x30, 2, 3, "KNOWS"),
        ]
    )

    explanation = await explain_path(
        _eid(1), _eid(3), tenant_id=TENANT, graph=store, relationships=edges
    )

    assert explanation is not None
    assert [e.id for e in explanation.entities] == [_eid(1), _eid(2), _eid(3)]
    assert [r.id for r in explanation.relationships] == [_rid(0x10), _rid(0x30)]


async def test_dangling_neighbour_is_skipped_and_search_still_reaches_target() -> None:
    # The edge to entity 5 leads nowhere: entity 5 is not in the graph. It sorts
    # before the target (9), so the search meets it first and must move on.
    store = InMemoryGraphStore()
    await store.upsert_entities([_entity(1, "Ada"), _entity(9, "Grace")])
    edges = _RelationshipList(
        [
            _rel(0x50, 1, 5, "KNOWS"),
            _rel(0x60, 1, 9, "KNOWS"),
        ]
    )

    explanation = await explain_path(
        _eid(1), _eid(9), tenant_id=TENANT, graph=store, relationships=edges
    )

    assert explanation is not None
    assert [e.id for e in explanation.entities] == [_eid(1), _eid(9)]
    assert [r.id for r in explanation.relationships] == [_rid(0x60)]
