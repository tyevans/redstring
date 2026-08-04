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

from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from kg_builder.domain.entity import Entity
    from kg_builder.domain.ids import EntityId, RelationshipId, TenantId
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

    async def get_entities(
        self, entity_ids: Sequence[EntityId], tenant_id: TenantId
    ) -> list[Entity]:
        """Return whichever of `entity_ids` exist in this tenant.

        Ids that do not exist are omitted rather than represented by a `None`
        placeholder: the caller is asking which of these exist, and a hole
        would only be filtered out again. Repeated ids yield one entity each.
        Order is unspecified -- key the result by `id` if you need to align it
        with the input.

        This exists so consolidation is not a loop over `get_entity`. It must
        be one round trip.
        """
        ...

    async def find_entities(
        self,
        tenant_id: TenantId,
        *,
        name: str | None = None,
        entity_type: str | None = None,
        limit: int | None = None,
        after: EntityId | None = None,
    ) -> list[Entity]:
        """Return this tenant's entities matching every filter supplied.

        `name` matches `Entity.normalized_name` exactly -- no fuzziness, no
        substring. Filters combine with AND. `limit` caps the page size;
        `None` means no cap. A negative `limit` raises `ValueError`.

        **Total order.** Results are ascending by `Entity.id` compared as its
        canonical lowercase hyphenated string. Because every UUID renders to
        the same fixed-width hex format, that ordering coincides with unsigned
        big-endian ordering of the 128-bit value -- so a backend may index
        either the text or the native UUID and satisfy this identically.

        **Cursor.** `after` resumes strictly *after* that id in the above
        order. It need not still exist, which is what makes a page resumable
        when rows are deleted between calls. Pass the last id of a page to get
        the next one; an empty page means the end.

        The order is part of the contract, not an implementation detail: a
        cursor over an undefined order is not resumable. Note in particular
        that insertion order is *not* promised -- no adapter over a real
        database could honour it.
        """
        ...

    async def find_by_blocking_key(self, key: str, tenant_id: TenantId) -> list[Entity]:
        """Return this tenant's entities whose `blocking_keys` contain `key`.

        The store computes nothing: consolidation derives blocking keys with a
        pure key function and the entity carries them.
        """
        ...

    async def find_by_blocking_keys(
        self, keys: Sequence[str], tenant_id: TenantId
    ) -> dict[str, list[Entity]]:
        """Group this tenant's entities by each of `keys`.

        Every requested key appears in the result, mapping to `[]` when
        nothing carries it, so a caller can iterate the mapping without
        re-checking it against what it asked for. An entity carrying several
        of the keys appears under each.

        Blocking a whole tenant is the shape consolidation actually uses, and
        as a loop over `find_by_blocking_key` it is one query per key.
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

    async def get_relationships(
        self,
        entity_id: EntityId,
        tenant_id: TenantId,
        *,
        direction: Literal["out", "in", "both"] = "both",
        relationship_types: Sequence[str] | None = None,
    ) -> list[Relationship]:
        """Return this tenant's relationships touching `entity_id`.

        `neighbors` answers "which entities is this connected to" and loses the
        edge; this answers "how is it connected", preserving type, confidence
        and properties. Consolidation needs it to redirect a merged entity's
        edges onto the canonical one.

        `direction` selects edges where the entity is the source (`"out"`), the
        target (`"in"`), or either (`"both"`, the default). Any other value
        raises `ValueError`. `relationship_types` restricts by type; `None`
        means no filter and `[]` means no type matches. An unknown
        `entity_id` yields `[]`.
        """
        ...

    async def get_relationships_for(
        self,
        entity_ids: Sequence[EntityId],
        tenant_id: TenantId,
        *,
        direction: Literal["out", "in", "both"] = "both",
        relationship_types: Sequence[str] | None = None,
    ) -> list[Relationship]:
        """Return this tenant's relationships touching any of `entity_ids`.

        The result is a set of edges, not a concatenation of per-entity
        answers: an edge whose endpoints are both in `entity_ids` appears
        once. `direction` and `relationship_types` mean what they do on
        `get_relationships`, with `direction` read relative to each id.

        Merging a group of entities needs every edge of that group; this
        exists so that is one round trip rather than one per member.
        """
        ...

    async def delete_relationship(
        self, relationship_id: RelationshipId, tenant_id: TenantId
    ) -> bool:
        """Delete one relationship; return whether it existed.

        Idempotent: deleting an absent id returns `False` rather than raising,
        so replaying a delete is not an error. Deleting an edge is not a
        cascade -- both endpoints survive.

        Merge redirects an edge by upserting its id with new endpoints, which
        can leave a semantically duplicate edge behind. This is how that
        duplicate is removed. **Whether merge dedupes parallel edges at all is
        slice 7's decision, not this port's** -- the port only provides the
        means.

        There is deliberately no `delete_entity`. An entity merged away
        survives as an alias node rather than disappearing, so single-entity
        deletion has no caller; `delete_by_tenant` covers bulk removal. Add
        one only when something genuinely needs it, not for symmetry.
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

        Hop distance is deliberately **not** returned. Slice 8's temporal work
        may want it; adding it now would widen the contract on speculation,
        and it is additive to introduce later.
        """
        ...

    async def delete_by_tenant(self, tenant_id: TenantId) -> int:
        """Delete everything belonging to `tenant_id`; return entities removed.

        Relationships of that tenant go too, but the count is of entities only.
        No other tenant is touched.
        """
        ...
