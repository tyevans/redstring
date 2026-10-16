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
from typing import TYPE_CHECKING, Literal, Self

from redstring.domain.exceptions import AliasCycleError, MissingEntityError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from redstring.domain.alias import Alias
    from redstring.domain.entity import Entity
    from redstring.domain.ids import EntityId, RelationshipId, TenantId
    from redstring.domain.relationship import Relationship


class InMemoryGraphStore:
    """A `GraphStore` backed by plain dictionaries."""

    def __init__(self) -> None:
        self._entities: dict[TenantId, dict[EntityId, Entity]] = {}
        self._relationships: dict[TenantId, dict[RelationshipId, Relationship]] = {}
        # Keyed by the *alias* id, not the canonical one: an entity has at most
        # one canonical parent, so that key is unique, while a canonical entity
        # has many aliases. `find_aliases` pays a scan for it, which is the
        # right way round -- resolution runs per edge endpoint on every
        # extraction fold, and `find_aliases` runs once per undo.
        self._aliases: dict[TenantId, dict[EntityId, Alias]] = {}
        self._blocking_index: dict[TenantId, dict[str, set[EntityId]]] = {}
        self._outgoing: dict[TenantId, dict[EntityId, set[RelationshipId]]] = {}
        self._incoming: dict[TenantId, dict[EntityId, set[RelationshipId]]] = {}
        self._adjacency: dict[TenantId, dict[EntityId, set[tuple[EntityId, str]]]] = {}

    # ------------------------------------------------------------------
    # Entities
    # ------------------------------------------------------------------

    async def upsert_entity(self, entity: Entity) -> None:
        tenant = self._entities.setdefault(entity.tenant_id, {})
        old_entity = tenant.get(entity.id)
        if old_entity is not None and old_entity.blocking_keys:
            tenant_idx = self._blocking_index.get(entity.tenant_id, {})
            for key in old_entity.blocking_keys:
                if key in tenant_idx:
                    tenant_idx[key].discard(entity.id)
                    if not tenant_idx[key]:
                        del tenant_idx[key]

        tenant[entity.id] = entity.model_copy(deep=True)
        if entity.blocking_keys:
            tenant_idx = self._blocking_index.setdefault(entity.tenant_id, {})
            for key in entity.blocking_keys:
                tenant_idx.setdefault(key, set()).add(entity.id)

    async def upsert_entities(self, entities: Sequence[Entity]) -> None:
        for entity in entities:
            await self.upsert_entity(entity)

    async def get_entity(self, entity_id: EntityId, tenant_id: TenantId) -> Entity | None:
        entity = self._entities.get(tenant_id, {}).get(entity_id)
        return None if entity is None else entity.model_copy(deep=True)

    async def get_entities(
        self, entity_ids: Sequence[EntityId], tenant_id: TenantId
    ) -> list[Entity]:
        known = self._entities.get(tenant_id, {})
        # dict.fromkeys deduplicates while keeping the caller's order, which
        # makes a repeated id yield one entity without an extra pass.
        return [
            known[entity_id].model_copy(deep=True)
            for entity_id in dict.fromkeys(entity_ids)
            if entity_id in known
        ]

    async def find_entities(
        self,
        tenant_id: TenantId,
        *,
        name: str | None = None,
        entity_type: str | None = None,
        limit: int | None = None,
        after: EntityId | None = None,
    ) -> list[Entity]:
        if limit is not None and limit < 0:
            raise ValueError("limit must not be negative")

        found = sorted(
            (
                entity
                for entity in self._entities.get(tenant_id, {}).values()
                if (name is None or entity.normalized_name == name)
                and (entity_type is None or entity.entity_type == entity_type)
                # Strictly after, and by the same key the sort uses, so the
                # cursor stays valid when the id it names has been deleted.
                and (after is None or str(entity.id) > str(after))
            ),
            key=lambda entity: str(entity.id),
        )
        page = found if limit is None else found[:limit]
        return [entity.model_copy(deep=True) for entity in page]

    async def find_by_blocking_key(self, key: str, tenant_id: TenantId) -> list[Entity]:
        tenant_idx = self._blocking_index.get(tenant_id, {})
        matching_ids = tenant_idx.get(key, ())
        entities = self._entities.get(tenant_id, {})
        return [entities[eid].model_copy(deep=True) for eid in matching_ids if eid in entities]

    async def find_by_blocking_keys(
        self, keys: Sequence[str], tenant_id: TenantId
    ) -> dict[str, list[Entity]]:
        grouped: dict[str, list[Entity]] = {key: [] for key in keys}
        tenant_idx = self._blocking_index.get(tenant_id, {})
        entities = self._entities.get(tenant_id, {})
        for key in keys:
            matching_ids = tenant_idx.get(key, ())
            if matching_ids:
                grouped[key] = [
                    entities[eid].model_copy(deep=True) for eid in matching_ids if eid in entities
                ]
        return grouped

    # ------------------------------------------------------------------
    # Aliases
    # ------------------------------------------------------------------

    async def upsert_alias(self, alias: Alias) -> None:
        tenant = self._aliases.setdefault(alias.tenant_id, {})
        tenant[alias.alias_entity_id] = alias.model_copy(deep=True)

    async def remove_alias(self, alias_entity_id: EntityId, tenant_id: TenantId) -> bool:
        return self._aliases.get(tenant_id, {}).pop(alias_entity_id, None) is not None

    async def find_aliases(self, canonical_entity_id: EntityId, tenant_id: TenantId) -> list[Alias]:
        return sorted(
            (
                alias.model_copy(deep=True)
                for alias in self._aliases.get(tenant_id, {}).values()
                if alias.canonical_entity_id == canonical_entity_id
            ),
            key=lambda alias: str(alias.alias_entity_id),
        )

    async def resolve_entity_ids(
        self, entity_ids: Sequence[EntityId], tenant_id: TenantId
    ) -> dict[EntityId, EntityId]:
        aliases = self._aliases.get(tenant_id, {})
        # Bounded by the number of aliases: a walk longer than that has
        # revisited a node, so it is a cycle. Cheaper and tighter than a
        # visited set, and it cannot be fooled by a chain that merely happens
        # to be long. `+ 1` so a chain using every alias still resolves.
        limit = len(aliases) + 1
        resolved: dict[EntityId, EntityId] = {}
        for entity_id in entity_ids:
            current = entity_id
            for _ in range(limit):
                alias = aliases.get(current)
                if alias is None:
                    break
                current = alias.canonical_entity_id
            else:
                raise AliasCycleError(entity_id=entity_id, tenant_id=tenant_id)
            resolved[entity_id] = current
        return resolved

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    def _index_relationship(self, relationship: Relationship) -> None:
        t_id = relationship.tenant_id
        r_id = relationship.id
        src = relationship.source_entity_id
        tgt = relationship.target_entity_id
        rtype = relationship.relationship_type

        self._outgoing.setdefault(t_id, {}).setdefault(src, set()).add(r_id)
        self._incoming.setdefault(t_id, {}).setdefault(tgt, set()).add(r_id)
        self._adjacency.setdefault(t_id, {}).setdefault(src, set()).add((tgt, rtype))
        self._adjacency.setdefault(t_id, {}).setdefault(tgt, set()).add((src, rtype))

    def _unindex_relationship(self, relationship: Relationship) -> None:
        t_id = relationship.tenant_id
        r_id = relationship.id
        src = relationship.source_entity_id
        tgt = relationship.target_entity_id
        rtype = relationship.relationship_type

        if t_id in self._outgoing and src in self._outgoing[t_id]:
            self._outgoing[t_id][src].discard(r_id)
        if t_id in self._incoming and tgt in self._incoming[t_id]:
            self._incoming[t_id][tgt].discard(r_id)
        if t_id in self._adjacency:
            if src in self._adjacency[t_id]:
                self._adjacency[t_id][src].discard((tgt, rtype))
            if tgt in self._adjacency[t_id]:
                self._adjacency[t_id][tgt].discard((src, rtype))

    async def upsert_relationship(self, relationship: Relationship) -> None:
        self._require_endpoints(relationship)
        tenant = self._relationships.setdefault(relationship.tenant_id, {})
        old_rel = tenant.get(relationship.id)
        if old_rel is not None:
            self._unindex_relationship(old_rel)
        tenant[relationship.id] = relationship.model_copy(deep=True)
        self._index_relationship(relationship)

    async def upsert_relationships(self, relationships: Sequence[Relationship]) -> None:
        """All-or-nothing: every endpoint is checked before anything is written."""
        for relationship in relationships:
            self._require_endpoints(relationship)
        for relationship in relationships:
            tenant = self._relationships.setdefault(relationship.tenant_id, {})
            old_rel = tenant.get(relationship.id)
            if old_rel is not None:
                self._unindex_relationship(old_rel)
            tenant[relationship.id] = relationship.model_copy(deep=True)
            self._index_relationship(relationship)

    def _require_endpoints(self, relationship: Relationship) -> None:
        """Raise `MissingEntityError` unless both endpoints exist for the tenant."""
        known = self._entities.get(relationship.tenant_id, {})
        for endpoint in (relationship.source_entity_id, relationship.target_entity_id):
            if endpoint not in known:
                raise MissingEntityError(entity_id=endpoint, tenant_id=relationship.tenant_id)

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
        rels = self._relationships.get(tenant_id, {})
        rel_ids: set[RelationshipId] = set()

        if direction in ("out", "both"):
            rel_ids.update(self._outgoing.get(tenant_id, {}).get(entity_id, ()))
        if direction in ("in", "both"):
            rel_ids.update(self._incoming.get(tenant_id, {}).get(entity_id, ()))

        return [
            rels[rid].model_copy(deep=True)
            for rid in rel_ids
            if rid in rels and (allowed is None or rels[rid].relationship_type in allowed)
        ]

    async def get_relationships_for(
        self,
        entity_ids: Sequence[EntityId],
        tenant_id: TenantId,
        *,
        direction: Literal["out", "in", "both"] = "both",
        relationship_types: Sequence[str] | None = None,
    ) -> list[Relationship]:
        if direction not in ("out", "in", "both"):
            raise ValueError(f"direction must be 'out', 'in' or 'both', not {direction!r}")

        allowed = None if relationship_types is None else set(relationship_types)
        rels = self._relationships.get(tenant_id, {})
        out_map = self._outgoing.get(tenant_id, {})
        in_map = self._incoming.get(tenant_id, {})
        rel_ids: set[RelationshipId] = set()

        for eid in entity_ids:
            if direction in ("out", "both") and eid in out_map:
                rel_ids.update(out_map[eid])
            if direction in ("in", "both") and eid in in_map:
                rel_ids.update(in_map[eid])

        return [
            rels[rid].model_copy(deep=True)
            for rid in rel_ids
            if rid in rels and (allowed is None or rels[rid].relationship_type in allowed)
        ]

    async def delete_relationship(
        self, relationship_id: RelationshipId, tenant_id: TenantId
    ) -> bool:
        tenant = self._relationships.get(tenant_id, {})
        old_rel = tenant.pop(relationship_id, None)
        if old_rel is not None:
            self._unindex_relationship(old_rel)
            return True
        return False

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
        adj_map = self._adjacency.get(tenant_id, {})

        seen = {entity_id}
        order: list[EntityId] = []
        frontier: deque[tuple[EntityId, int]] = deque([(entity_id, 0)])
        while frontier:
            current, hops = frontier.popleft()
            if hops == depth:
                continue
            neighbors_set = adj_map.get(current, set())
            candidate_neighbors = sorted(
                {target for target, rtype in neighbors_set if allowed is None or rtype in allowed},
                key=str,
            )
            for neighbour in candidate_neighbors:
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
        self._aliases.pop(tenant_id, None)
        self._blocking_index.pop(tenant_id, None)
        self._outgoing.pop(tenant_id, None)
        self._incoming.pop(tenant_id, None)
        self._adjacency.pop(tenant_id, None)
        return removed

    # ------------------------------------------------------------------
    # Lifetime
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Nothing to release: this adapter holds dictionaries the interpreter
        already owns.

        A no-op rather than an omission. `GraphStore` declares the release
        half through `AsyncClosable` so a caller can write one lifetime
        discipline against the port whichever adapter is behind it; an adapter
        that owns no driver, pool or client satisfies "release what you hold"
        by doing nothing, and saying so here is more honest than making the
        caller find out by reading the class.
        """

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()
