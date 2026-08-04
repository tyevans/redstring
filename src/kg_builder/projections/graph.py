"""Folding the event log into a `GraphStore`.

## What each event does to the store

- `DocumentExtracted` -- upsert the entities, then the relationships. In that
  order, and in one handler, because `upsert_relationship` refuses an edge
  whose endpoint is absent. This is the reason the event is coarse.
- `EntitiesMerged` -- apply each `RelationshipRedirection`: upsert `after`
  over the id it shares with `before`, or delete the edge when the merge
  dropped it. Nothing else happens: the entities that were absorbed stay in
  the store, because `GraphStore` deliberately has no `delete_entity` and an
  entity merged away survives as an alias.
- `MergeUndone` -- upsert every restored relationship. That both moves a
  redirected edge back and recreates a dropped one, because the restoration
  carries whole `Relationship`s.

## Idempotency, and the ordering it does assume

Every write here is an `upsert` or an idempotent `delete`, so applying an
event twice leaves the same state as applying it once -- slice 3 made that a
property of the port precisely so a projection would not need a second dedupe
layer, and there is none here.

Handlers are **not** order-independent across events, and the reason is
`GraphStore`'s shape rather than an oversight. A `DocumentExtracted` applied
*after* an `EntitiesMerged` that redirected one of its edges writes that
edge's original endpoints back, undoing the merge. The store has nowhere to
record that the merge happened -- there is no alias node, no canonical
pointer, nothing a later write could consult -- so the fold cannot detect it.

**This is not only a redelivery hazard.** It happens in strict log order,
every event delivered once, whenever a document is re-extracted under a new
model version after a merge touched its entities -- which
`Document.record_extraction` exists to allow. So the assumption is not "the
bus preserves order" but *"no `DocumentExtracted` ever follows a merge that
touched its entities"*, which no delivery mechanism can supply. See BACKLOG
B34, and `tests/unit/projections/test_known_gaps.py`, which pins the wrong
answer on purpose so the day it changes, someone reads it.

Redelivery is the milder half of the same defect, and there the fold is safe:
a checkpointed feed redelivers a contiguous suffix in order, so the last
occurrence of each event is still in log order and the final state is the
log's. An at-most-once bus that could deliver e1, e2, e1 would break that too.

## A missing endpoint is a poison event

`upsert_relationship` raises `MissingEntityError` for an edge pointing at an
entity this tenant does not have -- which happens when a document references
an entity from a document that has not been projected yet. That event goes to
the DLQ and the projection carries on; it is not retried into a wedge, and it
is not silently dropped either.
"""

from __future__ import annotations

from eventsource.application.projections import handles

from kg_builder.events.document import DocumentExtracted
from kg_builder.events.merge import EntitiesMerged, MergeUndone
from kg_builder.ports.graph_store import GraphStore
from kg_builder.projections.base import StoreProjection


class GraphProjection(StoreProjection[GraphStore]):
    """Maintains a `GraphStore` from the event log. See the module docstring."""

    @handles(DocumentExtracted)
    async def _apply_extraction(self, _context: object, event: DocumentExtracted) -> None:
        await self._store.upsert_entities(event.entities)
        await self._store.upsert_relationships(event.relationships)

    @handles(EntitiesMerged)
    async def _apply_merge(self, _context: object, event: EntitiesMerged) -> None:
        for redirection in event.redirections:
            if redirection.after is None:
                await self._store.delete_relationship(
                    redirection.before.id, redirection.before.tenant_id
                )
            else:
                await self._store.upsert_relationship(redirection.after)

    @handles(MergeUndone)
    async def _apply_undo(self, _context: object, event: MergeUndone) -> None:
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
