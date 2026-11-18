"""`explain_path` refuses over-deep searches and compares the target before fetching.

Ids are fixed rather than drawn. `explain_path` sorts neighbours by the string
form of their id, so the order in which a neighbour and the target are visited
is a fact of the ids. A test that drew random ids would see either order on
different runs, and could pass for an accidental reason on one of them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from redstring.composition.paths import explain_path
from redstring.domain.entity import Entity
from redstring.domain.ids import EntityId, RelationshipId, TenantId
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.relationship import Relationship
from redstring.graph.adapters.memory import InMemoryGraphStore
from redstring.ports.graph_store import MAX_TRAVERSAL_DEPTH

#: The source. Sorts below both neighbours, and is never a candidate.
SOURCE = EntityId(UUID("00000000-0000-0000-0000-000000000001"))
#: Sorts before `LATE`: visited first, so the old code fetched it before the target.
EARLY = EntityId(UUID("00000000-0000-0000-0000-00000000000a"))
#: Sorts after `EARLY`.
LATE = EntityId(UUID("00000000-0000-0000-0000-00000000000b"))


class _CountingStore(InMemoryGraphStore):
    """An in-memory store that records every `get_entity` call by id."""

    def __init__(self) -> None:
        super().__init__()
        self.fetched: list[EntityId] = []

    async def get_entity(self, entity_id: EntityId, tenant_id: TenantId) -> Entity | None:
        self.fetched.append(entity_id)
        return await super().get_entity(entity_id, tenant_id)


def _entity(entity_id: EntityId, tenant: TenantId, name: str) -> Entity:
    return Entity(
        id=entity_id,
        tenant_id=tenant,
        name=name,
        normalized_name=name.lower(),
        entity_type="person",
        provenance=Provenance(
            observed_at=datetime(2026, 3, 1, tzinfo=UTC),
            extraction_method=ExtractionMethod.MANUAL,
            confidence=1.0,
        ),
    )


def _edge(tenant: TenantId, source: EntityId, target: EntityId) -> Relationship:
    return Relationship(
        id=RelationshipId(uuid4()),
        tenant_id=tenant,
        source_entity_id=source,
        target_entity_id=target,
        relationship_type="knows",
        confidence=1.0,
    )


async def _star(tenant: TenantId) -> _CountingStore:
    """`SOURCE` with two neighbours, `EARLY` and `LATE`, each one edge away."""
    store = _CountingStore()
    names = {SOURCE: "Source", EARLY: "Early", LATE: "Late"}
    await store.upsert_entities([_entity(i, tenant, names[i]) for i in names])
    await store.upsert_relationships([_edge(tenant, SOURCE, EARLY), _edge(tenant, SOURCE, LATE)])
    return store


class TestTargetIsComparedBeforeItIsFetched:
    """The target is fetched once, by endpoint resolution, and never again in the loop.

    The two orders matter because they differ in what the old code did. With
    the neighbour sorting first, the old code fetched the non-target neighbour
    and then the target a second time. With the target sorting first, it
    fetched the target a second time and returned.
    """

    async def test_target_sorting_after_a_neighbour_is_fetched_once(self) -> None:
        tenant = TenantId(uuid4())
        store = await _star(tenant)

        explanation = await explain_path(SOURCE, LATE, tenant_id=tenant, graph=store)

        assert explanation is not None
        assert [e.id for e in explanation.entities] == [SOURCE, LATE]
        assert store.fetched.count(LATE) == 1
        # The neighbour before the target is not the answer, so it must be fetched.
        assert EARLY in store.fetched

    async def test_target_sorting_before_a_neighbour_is_fetched_once(self) -> None:
        tenant = TenantId(uuid4())
        store = await _star(tenant)

        explanation = await explain_path(SOURCE, EARLY, tenant_id=tenant, graph=store)

        assert explanation is not None
        assert [e.id for e in explanation.entities] == [SOURCE, EARLY]
        assert store.fetched.count(EARLY) == 1


class TestMaxDepthHasTheSameCapAsNeighbors:
    async def test_the_maximum_depth_is_accepted(self) -> None:
        tenant = TenantId(uuid4())
        store = await _star(tenant)

        explanation = await explain_path(
            SOURCE, LATE, tenant_id=tenant, graph=store, max_depth=MAX_TRAVERSAL_DEPTH
        )

        assert explanation is not None

    async def test_one_past_the_maximum_depth_is_rejected(self) -> None:
        tenant = TenantId(uuid4())
        store = await _star(tenant)

        with pytest.raises(ValueError, match=rf"max_depth must not exceed {MAX_TRAVERSAL_DEPTH}"):
            await explain_path(
                SOURCE, LATE, tenant_id=tenant, graph=store, max_depth=MAX_TRAVERSAL_DEPTH + 1
            )
