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

The store is annotated `VectorWriter` and not `VectorStore`. One of the port's
seven methods is called here; the other six exist for library users, which is
a reason for the *port* to have them and none at all for this fold to depend
on them. `ChunkProjection` is the same shape for the same reason.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from eventsource.application.projections import StoreProjection, handles, replay

from redstring.events.document import EntitiesEmbedded
from redstring.ports.vector_store import VectorWriter

if TYPE_CHECKING:
    from eventsource.application.projections import ReplayReport
    from eventsource.ports import GlobalEventFeed

    from redstring.domain.ids import TenantId


__all__ = ["VectorProjection"]


class VectorProjection(StoreProjection[VectorWriter]):
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

    async def wipe_tenant(self, tenant_id: TenantId) -> None:
        """Wipe all vector read models belonging to `tenant_id`."""
        if hasattr(self._store, "delete_by_tenant"):
            await self._store.delete_by_tenant(tenant_id)
        else:
            raise NotImplementedError(
                f"{type(self._store).__name__} does not implement delete_by_tenant"
            )

    async def rebuild(
        self,
        feed: GlobalEventFeed,
        *,
        tenant_id: TenantId,
        strict: bool = False,
        batch_size: int = 1000,
    ) -> ReplayReport:
        """Wipe `tenant_id`'s read models and replay `feed` scoped to `tenant_id`."""
        await self.wipe_tenant(tenant_id)
        return await replay(
            feed,
            [self],
            tenant_id=tenant_id,
            strict=strict,
            batch_size=batch_size,
        )
