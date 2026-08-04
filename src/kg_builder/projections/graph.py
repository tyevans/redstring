"""Folding the event log into a `GraphStore`.

## What each event does to the store

- `DocumentExtracted` -- upsert the entities, then the relationships, with
  every edge endpoint **resolved through the alias table first**. In that
  order, and in one handler, because `upsert_relationship` refuses an edge
  whose endpoint is absent. This is the reason the event is coarse.
- `EntitiesMerged` -- record an `Alias` per absorbed entity, then apply each
  `RelationshipRedirection`: upsert `after` over the id it shares with
  `before`, or delete the edge when the merge dropped it. The absorbed
  entities stay in the store, because `GraphStore` deliberately has no
  `delete_entity` and an entity merged away survives as an alias.
- `MergeUndone` -- remove those aliases, then upsert every restored
  relationship. That both moves a redirected edge back and recreates a dropped
  one, because the restoration carries whole `Relationship`s.

## Why the extraction fold resolves, and what it fixes (B34)

The fold used to write a document's edges by id with the endpoints extraction
found. If a merge had already moved one of those edges onto a canonical
entity, the upsert wrote the *original* endpoints back and the merge was
undone in the read model, with nothing to notice.

**That was not a redelivery hazard.** It happened in strict log order, every
event delivered exactly once, whenever a document was re-extracted under a new
model version after a merge touched its entities -- which
`Document.record_extraction` exists to allow. The assumption the fold was
making was not "the bus preserves order" but *"no `DocumentExtracted` ever
follows a merge that touched its entities"*, which is a property of what the
write side emits and which no delivery mechanism can supply.

The cause was `GraphStore`'s shape rather than the fold's: the store had
nowhere to record that a merge had happened, so the handler could not tell
"this edge belongs to an entity that has since been absorbed" from "this edge
is new". `upsert_alias`/`resolve_entity_ids` are that somewhere, and this
handler is the reader. BACKLOG B34, closed.

Resolution is transitive, because chains form: `ConsolidationLog` refuses to
merge *into* an alias, but it does not refuse to merge a canonical entity
away, so `B -> A` then `A -> C` is legal and an edge on `B` belongs on `C`.

**An edge whose endpoints resolve to the same entity is deleted, not written.**
`Relationship` rejects a self-loop outright, so there is nothing to upsert; and
the merge that caused the collapse already deleted that edge, recording
`after=None`. Deleting is what makes re-extraction agree with the merge rather
than raise.

## Idempotency, and the ordering it no longer assumes

Every write here is an `upsert` or an idempotent `delete`, so applying an event
twice leaves the same state as applying it once -- slice 3 made that a property
of the port precisely so a projection would not need a second dedupe layer, and
there is none here.

Redelivery was always the milder half of B34, and there the fold was already
safe: a checkpointed feed redelivers a contiguous suffix in order, so the last
occurrence of each event is still in log order and the final state is the
log's. An at-most-once bus that could deliver e1, e2, e1 would break that too.

## Alias ids are derived, not generated

`_alias_id` hashes the tenant and the absorbed entity into a `uuid5`. A `uuid4`
would make a replay produce different alias rows for the same log, which is
precisely what the replay-equivalence tests exist to forbid.

The **merge event id is deliberately not in the hash**, even though it is to
hand. The row is keyed by `(tenant_id, alias_entity_id)` in every adapter --
an entity has at most one canonical parent -- so that pair is the row's
identity, and hashing anything else in would let one logical row carry two
different ids depending on which merge last wrote it. It would also make two
independently-built logs of the same scenario disagree, which is what
`test_two_halves_project_to_the_same_state_as_one_whole` compares.

## A missing endpoint is a poison event

`upsert_relationship` raises `MissingEntityError` for an edge pointing at an
entity this tenant does not have -- which happens when a document references
an entity from a document that has not been projected yet. That event goes to
the DLQ and the projection carries on; it is not retried into a wedge, and it
is not silently dropped either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import NAMESPACE_OID, uuid5

from eventsource.application.projections import handles

from kg_builder.domain.alias import Alias
from kg_builder.events.document import DocumentExtracted
from kg_builder.events.merge import EntitiesMerged, MergeUndone
from kg_builder.ports.graph_store import GraphStore
from kg_builder.projections.base import StoreProjection

if TYPE_CHECKING:
    from uuid import UUID

    from kg_builder.domain.ids import EntityId, TenantId
    from kg_builder.domain.relationship import Relationship


def _alias_id(tenant_id: TenantId, alias_entity_id: EntityId) -> UUID:
    """A stable id for the alias row a merge writes. See the module docstring."""
    return uuid5(NAMESPACE_OID, f"kg-builder:alias:{tenant_id}:{alias_entity_id}")


class GraphProjection(StoreProjection[GraphStore]):
    """Maintains a `GraphStore` from the event log. See the module docstring."""

    @handles(DocumentExtracted)
    async def _apply_extraction(self, _context: object, event: DocumentExtracted) -> None:
        await self._store.upsert_entities(event.entities)
        await self._store.upsert_relationships(
            await self._resolved(event.relationships, event.tenant_id)
        )

    async def _resolved(
        self, relationships: list[Relationship], tenant_id: TenantId
    ) -> list[Relationship]:
        """`relationships` with both endpoints moved onto their canonicals.

        One resolution call for the whole document, not one per edge: the
        caller is a fold and the port's batch shape exists for exactly this.

        An edge whose endpoints collapse onto one entity is deleted here rather
        than returned, because `Relationship` will not construct a self-loop --
        so there is no value this function could return that says "and this one
        must go".
        """
        if not relationships:
            return []

        endpoints = [
            endpoint
            for relationship in relationships
            for endpoint in (relationship.source_entity_id, relationship.target_entity_id)
        ]
        canonical = await self._store.resolve_entity_ids(endpoints, tenant_id)

        resolved = []
        for relationship in relationships:
            source = canonical[relationship.source_entity_id]
            target = canonical[relationship.target_entity_id]
            if source == target:
                await self._store.delete_relationship(relationship.id, relationship.tenant_id)
                continue
            resolved.append(
                relationship.model_copy(
                    update={"source_entity_id": source, "target_entity_id": target}
                )
            )
        return resolved

    @handles(EntitiesMerged)
    async def _apply_merge(self, _context: object, event: EntitiesMerged) -> None:
        # Aliases first. A redirection's `after` already names the canonical
        # entity, so the order does not matter to this event -- it matters to
        # the next `DocumentExtracted`, and writing them first means a handler
        # that fails part-way leaves the store closer to correct rather than
        # further from it.
        absorbed = await self._store.get_entities(event.merged_entity_ids, event.tenant_id)
        names = {entity.id: entity for entity in absorbed}
        for entity_id in event.merged_entity_ids:
            entity = names.get(entity_id)
            await self._store.upsert_alias(
                Alias(
                    id=_alias_id(event.tenant_id, entity_id),
                    tenant_id=event.tenant_id,
                    canonical_entity_id=event.canonical_entity_id,
                    alias_entity_id=entity_id,
                    alias_name=None if entity is None else entity.name,
                    alias_normalized_name=None if entity is None else entity.normalized_name,
                    merged_at=event.occurred_at,
                    merge_reason=event.merge_reason,
                )
            )

        for redirection in event.redirections:
            if redirection.after is None:
                await self._store.delete_relationship(
                    redirection.before.id, redirection.before.tenant_id
                )
            else:
                await self._store.upsert_relationship(redirection.after)

    @handles(MergeUndone)
    async def _apply_undo(self, _context: object, event: MergeUndone) -> None:
        # The order of these two steps does **not** matter, and that is worth
        # saying because it looks as though it should. `restored_relationships`
        # carry the pre-merge endpoints, so an alias still in place while they
        # are written would seem to contradict them -- but nothing resolves on
        # this path. Only `_apply_extraction` resolves, because only it handles
        # data that predates the merge without knowing about it.
        #
        # Checked rather than assumed: swapping these two statements by hand
        # left all 52 tests in `tests/unit/consolidation` passing. An earlier
        # version of this comment claimed the order was load-bearing; it was
        # not, and a comment asserting a constraint that does not exist is how
        # a later reader comes to believe the fold resolves here too.
        for entity_id in event.unmerged_entity_ids:
            await self._store.remove_alias(entity_id, event.tenant_id)
        await self._store.upsert_relationships(event.restored_relationships)

    async def _truncate_read_models(self) -> None:
        """Not supported, deliberately. Wipe per tenant instead.

        `GraphStore` has no operation that spans tenants -- "there is no
        cross-tenant read, ever", and by the same argument no cross-tenant
        delete. A `reset()` that quietly wiped nothing would be worse than one
        that says so: rebuilding a projection over a store that still held the
        old rows is how a rebuild comes to look successful while carrying
        stale entities nothing will ever remove.
        """
        raise NotImplementedError(
            "GraphStore has no cross-tenant delete by design; wipe with "
            "delete_by_tenant(tenant_id) for each tenant being rebuilt"
        )
