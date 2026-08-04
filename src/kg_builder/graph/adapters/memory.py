"""In-memory `GraphStore`: the reference adapter.

This is a real implementation, not a stub -- it enforces every contract the
port states, including `MissingEntityError` on dangling edges. An adapter more
permissive than its port is useless as a reference, because tests written
against it would pass here and fail on Neo4j.

Two design notes:

- **Copy on write and on read.** Entities and relationships are pydantic
  models holding mutable containers. Handing out a reference lets a caller
  mutate stored state by accident; keeping the caller's object lets a caller
  mutate it afterwards. Both directions are closed with a deep copy.
- **Adjacency is derived, not stored.** Traversal scans the tenant's
  relationships rather than maintaining an index. A reference implementation
  is judged on being obviously correct, and a second copy of the edge set is
  a second thing to keep consistent.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Literal

from kg_builder.domain.exceptions import MissingEntityError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from kg_builder.domain.entity import Entity
    from kg_builder.domain.ids import EntityId, RelationshipId, TenantId
    from kg_builder.domain.relationship import Relationship


class InMemoryGraphStore:
    """A `GraphStore` backed by plain dictionaries."""

    def __init__(self) -> None:
        self._entities: dict[TenantId, dict[EntityId, Entity]] = {}
        self._relationships: dict[TenantId, dict[RelationshipId, Relationship]] = {}

    # ------------------------------------------------------------------
    # Entities
    # ------------------------------------------------------------------

    async def upsert_entity(self, entity: Entity) -> None:
        tenant = self._entities.setdefault(entity.tenant_id, {})
        tenant[entity.id] = entity.model_copy(deep=True)

    async def upsert_entities(self, entities: Sequence[Entity]) -> None:
        for entity in entities:
            await self.upsert_entity(entity)

    async def get_entity(self, entity_id: EntityId, tenant_id: TenantId) -> Entity | None:
        entity = self._entities.get(tenant_id, {}).get(entity_id)
        return None if entity is None else entity.model_copy(deep=True)

    async def find_entities(
        self,
        tenant_id: TenantId,
        *,
        name: str | None = None,
        entity_type: str | None = None,
        limit: int | None = None,
    ) -> list[Entity]:
        if limit is not None and limit < 0:
            raise ValueError("limit must not be negative")

        found = [
            entity.model_copy(deep=True)
            for entity in self._entities.get(tenant_id, {}).values()
            if (name is None or entity.normalized_name == name)
            and (entity_type is None or entity.entity_type == entity_type)
        ]
        return found if limit is None else found[:limit]

    async def find_by_blocking_key(self, key: str, tenant_id: TenantId) -> list[Entity]:
        return [
            entity.model_copy(deep=True)
            for entity in self._entities.get(tenant_id, {}).values()
            if entity.blocking_keys is not None and key in entity.blocking_keys
        ]

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    async def upsert_relationship(self, relationship: Relationship) -> None:
        known = self._entities.get(relationship.tenant_id, {})
        for endpoint in (relationship.source_entity_id, relationship.target_entity_id):
            if endpoint not in known:
                raise MissingEntityError(entity_id=endpoint, tenant_id=relationship.tenant_id)

        tenant = self._relationships.setdefault(relationship.tenant_id, {})
        tenant[relationship.id] = relationship.model_copy(deep=True)

    async def upsert_relationships(self, relationships: Sequence[Relationship]) -> None:
        for relationship in relationships:
            await self.upsert_relationship(relationship)

    async def get_relationships(
        self,
        entity_id: EntityId,
        tenant_id: TenantId,
        *,
        direction: Literal["out", "in", "both"] = "both",
        relationship_types: Sequence[str] | None = None,
    ) -> list[Relationship]:
        if direction not in ("out", "in", "both"):
            raise ValueError(f"direction must be 'out', 'in' or 'both', not {direction!r}")

        allowed = None if relationship_types is None else set(relationship_types)
        return [
            relationship.model_copy(deep=True)
            for relationship in self._relationships.get(tenant_id, {}).values()
            if self._touches(relationship, entity_id, direction)
            and (allowed is None or relationship.relationship_type in allowed)
        ]

    @staticmethod
    def _touches(
        relationship: Relationship, entity_id: EntityId, direction: Literal["out", "in", "both"]
    ) -> bool:
        if direction == "out":
            return relationship.source_entity_id == entity_id
        if direction == "in":
            return relationship.target_entity_id == entity_id
        return entity_id in (relationship.source_entity_id, relationship.target_entity_id)

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------

    async def neighbors(
        self,
        entity_id: EntityId,
        tenant_id: TenantId,
        *,
        depth: int = 1,
        relationship_types: Sequence[str] | None = None,
    ) -> list[Entity]:
        if depth < 0:
            raise ValueError("depth must not be negative")

        entities = self._entities.get(tenant_id, {})
        if entity_id not in entities:
            return []

        allowed = None if relationship_types is None else set(relationship_types)
        adjacency: dict[EntityId, set[EntityId]] = {}
        for relationship in self._relationships.get(tenant_id, {}).values():
            if allowed is not None and relationship.relationship_type not in allowed:
                continue
            source, target = relationship.source_entity_id, relationship.target_entity_id
            adjacency.setdefault(source, set()).add(target)
            adjacency.setdefault(target, set()).add(source)

        # Breadth-first, `seen` seeded with the origin: that is what terminates
        # cycles and what keeps the origin out of its own neighbour set.
        seen = {entity_id}
        order: list[EntityId] = []
        frontier: deque[tuple[EntityId, int]] = deque([(entity_id, 0)])
        while frontier:
            current, hops = frontier.popleft()
            if hops == depth:
                continue
            for neighbour in sorted(adjacency.get(current, set()), key=str):
                if neighbour in seen or neighbour not in entities:
                    continue
                seen.add(neighbour)
                order.append(neighbour)
                frontier.append((neighbour, hops + 1))

        return [entities[found].model_copy(deep=True) for found in order]

    # ------------------------------------------------------------------
    # Tenant lifecycle
    # ------------------------------------------------------------------

    async def delete_by_tenant(self, tenant_id: TenantId) -> int:
        removed = len(self._entities.pop(tenant_id, {}))
        self._relationships.pop(tenant_id, None)
        return removed
