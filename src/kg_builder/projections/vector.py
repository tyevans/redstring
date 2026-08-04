"""Folding the event log into a `VectorStore`.

One event matters: `EntitiesEmbedded`. `upsert_many` is idempotent and
last-write-wins per `(tenant_id, entity_id)`, so a redelivered event leaves
the same rows.

Unlike the graph fold, this one **is** order-independent across events in the
sense that matters: two `EntitiesEmbedded` for disjoint entity sets commute,
and two for the same entity are a genuine last-write-wins, which the log's
order settles. There is no merge-equivalent here to be overwritten by a
redelivered earlier event -- consolidation does not touch embeddings.

A `VectorStore` is built for one embedding model. An event carrying vectors of
the wrong length raises `DimensionMismatchError`, which is a poison event and
goes to the DLQ: it means the store and the emitter disagree about which model
is in play, and quietly accepting it would produce plausible nonsense rather
than an error.
"""

from __future__ import annotations

from eventsource.application.projections import handles

from kg_builder.events.document import EntitiesEmbedded
from kg_builder.ports.vector_store import VectorStore
from kg_builder.projections.base import StoreProjection


class VectorProjection(StoreProjection[VectorStore]):
    """Maintains a `VectorStore` from the event log."""

    @handles(EntitiesEmbedded)
    async def _apply_embeddings(self, _context: object, event: EntitiesEmbedded) -> None:
        await self._store.upsert_many(event.embeddings)

    async def _truncate_read_models(self) -> None:
        """Not supported; see `GraphProjection._truncate_read_models`.

        `VectorStore.delete_by_tenant` is the only bulk delete the port has,
        for the same reason: nothing here spans tenants.
        """
        raise NotImplementedError(
            "VectorStore has no cross-tenant delete by design; wipe with "
            "delete_by_tenant(tenant_id) for each tenant being rebuilt"
        )
