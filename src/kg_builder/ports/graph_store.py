"""The `GraphStore` port: entity and relationship storage, in domain terms.

A `GraphStore` is a **projection**, not the write model. The event log is the
authority; stores are derived, disposable, and rebuildable by replay. Two
consequences shape this interface:

- **Every write is idempotent.** Projection handlers replay. Applying the same
  event twice must leave the store in the same state as applying it once.
- **Nothing here is Cypher-shaped.** Backends must include SQL and plain
  dictionaries as well as Neo4j.

Adapters are required to be **read-your-writes**: once an `upsert_*` call has
returned, the effect is visible to the next read on the same store. Lag, when
these stores are fed by projections, exists between the event log and the
store -- never inside the store.

Every method is tenant-scoped. There is no cross-tenant read, ever.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from kg_builder.domain.entity import Entity
    from kg_builder.domain.ids import EntityId, TenantId
    from kg_builder.domain.relationship import Relationship


@runtime_checkable
class GraphStore(Protocol):
    """Storage for entities and the relationships between them."""

    async def upsert_entity(self, entity: Entity) -> None:
        """Insert or replace `entity`, keyed by `(entity.tenant_id, entity.id)`.

        Idempotent, last-write-wins: upserting the same id twice leaves exactly
        one entity holding the later value.
        """
        ...

    async def upsert_entities(self, entities: Sequence[Entity]) -> None:
        """Upsert many entities. Equivalent to `upsert_entity` per element.

        Entities in the sequence may belong to different tenants; each is keyed
        by its own `tenant_id`.
        """
        ...

    async def get_entity(self, entity_id: EntityId, tenant_id: TenantId) -> Entity | None:
        """Return the entity, or `None` if this tenant has no such id.

        An unknown id is not an error.
        """
        ...

    async def find_entities(
        self,
        tenant_id: TenantId,
        *,
        name: str | None = None,
        entity_type: str | None = None,
        limit: int | None = None,
    ) -> list[Entity]:
        """Return this tenant's entities matching every filter supplied.

        `name` matches `Entity.normalized_name` exactly -- no fuzziness, no
        substring. Filters combine with AND. `limit` caps the result size;
        `None` means no cap. A negative `limit` raises `ValueError`.
        """
        ...

    async def find_by_blocking_key(self, key: str, tenant_id: TenantId) -> list[Entity]:
        """Return this tenant's entities whose `blocking_keys` contain `key`.

        The store computes nothing: consolidation derives blocking keys with a
        pure key function and the entity carries them.
        """
        ...

    async def upsert_relationship(self, relationship: Relationship) -> None:
        """Insert or replace `relationship`, keyed by `(tenant_id, id)`.

        Raises `MissingEntityError` if either endpoint is absent from that
        tenant. Dangling edges are not permitted.
        """
        ...

    async def upsert_relationships(self, relationships: Sequence[Relationship]) -> None:
        """Upsert many relationships. Equivalent to `upsert_relationship` each.

        Not atomic: a `MissingEntityError` part-way through leaves earlier
        elements written. Callers replaying an event log get the same final
        state on retry because every element is individually idempotent.
        """
        ...

    async def neighbors(
        self,
        entity_id: EntityId,
        tenant_id: TenantId,
        *,
        depth: int = 1,
        relationship_types: Sequence[str] | None = None,
    ) -> list[Entity]:
        """Return entities reachable from `entity_id` within `depth` hops.

        Traversal follows edges in **both** directions. `relationship_types`,
        when given, restricts traversal to edges of those types. Cycles
        terminate. The origin entity is never in the result, and neither is
        any entity of another tenant. An unknown `entity_id` yields `[]`.
        `depth=0` yields `[]`; a negative `depth` raises `ValueError`.
        """
        ...

    async def delete_by_tenant(self, tenant_id: TenantId) -> int:
        """Delete everything belonging to `tenant_id`; return entities removed.

        Relationships of that tenant go too, but the count is of entities only.
        No other tenant is touched.
        """
        ...
