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

## Aliases, and why the store has to know about them

A merge does not delete the absorbed entity -- there is no `delete_entity` and
there never will be. What it does is record an `Alias`, and the store keeps it
because **a later write has to be able to consult it**. Without that, a
`DocumentExtracted` folded after an `EntitiesMerged` writes the pre-merge
endpoints back and silently reverts the merge, in strict log order, with every
event delivered once (BACKLOG B34, closed by this pair).

So `resolve_entity_ids` is the read a fold makes before writing an edge, and
`upsert_alias`/`remove_alias` are how the merge and undo folds maintain what it
reads. This is not consolidation logic leaking into the store: it is the store
having somewhere to put a fact that already happened.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redstring.domain.alias import Alias
    from redstring.domain.entity import Entity
    from redstring.domain.ids import EntityId, RelationshipId, TenantId
    from redstring.domain.relationship import Relationship


@runtime_checkable
class EntityReader(Protocol):
    """Reads entities back out.

    The narrowest useful slice of the port, and the one most collaborators
    want. `TemporalQuery` needs exactly one of these methods and nothing else
    in `GraphStore`; typing it against the whole port made a test double an
    eighteen-method exercise, which is why `tests/unit/test_temporal_surface.py`
    once faked a store by *subclassing the in-memory adapter* rather than
    implementing the interface.
    """

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


@runtime_checkable
class EntityWriter(Protocol):
    """Puts entities in, idempotently.

    Separate from reading because projections write and queries read, and
    almost nothing does both. A caller holding only this cannot accidentally
    grow a read path.
    """

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


@runtime_checkable
class AliasStore(Protocol):
    """Records and resolves merges.

    Why the store knows about aliases at all is argued in the module
    docstring: a fold has to consult them *before* writing an edge, or a
    `DocumentExtracted` applied after an `EntitiesMerged` silently reverts the
    merge.
    """

    async def upsert_alias(self, alias: Alias) -> None:
        """Record that `alias.alias_entity_id` was absorbed by its canonical.

        Idempotent, last-write-wins, keyed by `(tenant_id, alias_entity_id)`:
        an entity has at most one canonical parent, which is the same fact
        `ConsolidationLog`'s double-merge rule enforces on the write side.

        Neither endpoint needs to exist. An alias is a statement about ids, and
        requiring the entities would make the merge fold depend on the
        extraction fold having run first -- which is exactly the ordering
        assumption aliases exist to remove.
        """
        ...

    async def remove_alias(self, alias_entity_id: EntityId, tenant_id: TenantId) -> bool:
        """Forget one alias; return whether it existed.

        Idempotent: removing an absent alias returns `False` rather than
        raising, so replaying an undo is not an error.
        """
        ...

    async def find_aliases(self, canonical_entity_id: EntityId, tenant_id: TenantId) -> list[Alias]:
        """Return the aliases absorbed *directly* into `canonical_entity_id`.

        Directly, not transitively: `find_aliases(C)` after `B -> A -> C` gives
        `A` alone. A transitive answer would make "which entities did this
        merge absorb" unanswerable, and that is the question an undo asks.

        Ordered ascending by `alias_entity_id` compared as its canonical
        lowercase hyphenated string, so two adapters agree. An id with no
        aliases yields `[]`.
        """
        ...

    async def resolve_entity_ids(
        self, entity_ids: Sequence[EntityId], tenant_id: TenantId
    ) -> dict[EntityId, EntityId]:
        """Map each id to the entity that now stands for it.

        An id that is not an alias maps to itself, so every requested id
        appears in the result and a caller can look up unconditionally. Ids
        this tenant has never seen map to themselves too -- resolution answers
        "has this been merged away", not "does this exist".

        **Transitive.** Chains do form: `ConsolidationLog` refuses to merge
        *into* an alias, which is what stops cycles, but it does not refuse to
        merge a canonical entity away. So `B -> A` followed by `A -> C` is a
        legal pair of merges and `B` must resolve to `C`.

        **Terminating.** A cycle would need some merge to name an alias as its
        canonical, and the write model refuses exactly that. Adapters must
        still bound the walk rather than trust it: this is a projection over
        adapter-supplied data, and a `while` loop that fails to advance turns a
        corrupt row into a hang, which reads as infrastructure trouble and gets
        retried instead of investigated. Raise `AliasCycleError` naming the id.

        Batch because the caller is a fold resolving both endpoints of every
        edge in a document. As a loop it is two round trips per edge.
        """
        ...


@runtime_checkable
class RelationshipStore(Protocol):
    """The edges, and the ways to walk them.

    Reads and writes together here rather than split like entities, because
    the callers that touch relationships at all touch both: a merge reads the
    edges it is about to redirect and writes the redirections.
    """

    async def upsert_relationship(self, relationship: Relationship) -> None:
        """Insert or replace `relationship`, keyed by `(tenant_id, id)`.

        Raises `MissingEntityError` if either endpoint is absent from that
        tenant. Dangling edges are not permitted.
        """
        ...

    async def upsert_relationships(self, relationships: Sequence[Relationship]) -> None:
        """Upsert many relationships, **atomically**.

        Either every element is written or none is. A `MissingEntityError`
        leaves the store exactly as it was before the call -- in particular
        the elements *before* the offending one are not written.

        This used to say the opposite, and the weaker promise is what made the
        two adapters differ: Neo4j validates every endpoint in one query and so
        always wrote nothing, while the in-memory reference wrote the prefix.
        Nothing failed, because the compliance suite asserted the error and
        never what survived it (BACKLOG B10g). Tightening rather than pinning
        the weak version was the cheaper direction *and* the better contract --
        the adapter that would find atomicity hard already had it, and a
        replaying caller that has to retry the whole batch anyway gains nothing
        from a defined prefix.

        Atomicity is scoped to this call. A failure here does not disturb
        anything a previous call wrote.

        Every element remains individually idempotent, so a caller replaying an
        event log reaches the same final state on retry either way.
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

        Merge deduplicates parallel edges: when redirecting an edge onto the
        canonical entity would reproduce an edge the canonical already has --
        same endpoints, same type -- the merge drops it instead, recording
        `after=None` so undo can recreate it. This is the method that drops it.

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


@runtime_checkable
class TenantPurge(Protocol):
    """Removing everything one tenant owns.

    Alone in its own protocol because it is the one operation with no
    per-entity form and the one an ordinary caller should never reach for.
    Requiring it explicitly makes "this collaborator can wipe a tenant" a
    visible fact about a signature.
    """

    async def delete_by_tenant(self, tenant_id: TenantId) -> int:
        """Delete everything belonging to `tenant_id`; return entities removed.

        Relationships of that tenant go too, but the count is of entities only.
        No other tenant is touched.
        """
        ...


@runtime_checkable
class GraphStore(EntityReader, EntityWriter, AliasStore, RelationshipStore, TenantPurge, Protocol):
    """Storage for entities and the relationships between them.

    The whole port, composed from the five capabilities above. Adapters
    implement this; the compliance suite runs against this; and anything
    that genuinely needs the lot -- `GraphProjection` does, using eight of
    the eighteen -- should say so.

    **Collaborators should not.** Depending on eighteen methods to call one
    is the interface-segregation complaint in its plainest form, and it has
    a measured cost here rather than a stylistic one: three of the four
    first-party consumers use three methods or fewer, and every test double
    for any of them had to satisfy all eighteen or cheat by subclassing a
    real adapter. Narrow the annotation to the capability actually used.

    Splitting it changes nothing for an adapter. `GraphStore` still names
    every method through its bases, `runtime_checkable` still works, and
    `tests/unit/graph/test_compliance_coverage.py` still finds all eighteen,
    because `inspect.getmembers` walks the MRO.
    """
