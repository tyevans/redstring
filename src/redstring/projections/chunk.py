"""Folding the event log into a `ChunkStore`.

One event matters: `DocumentChunked`. It carries a document's **whole**
chunking, and `replace_source` applies it as one operation -- writing the
passages the event names and deleting this tenant's other passages of that
source in the same call.

That is why the handler is a single line and why the port has that method at
all. Split into an `upsert_many` and a `delete`, a crash between them would
leave a corpus that is neither the old chunking nor the new one; and a
redelivered event would have a window in which the source held nothing.

Idempotent redelivery falls out of the same shape: the incoming set is the
same set, so the second fold writes the same rows and finds no orphans.
Ordering across events is a genuine last-write-wins per source, which the
log's order settles -- there is nothing here for a redelivered *earlier*
event to overwrite that the later one would not restore on its next delivery.
"""

from __future__ import annotations

from eventsource.application.projections import StoreProjection, handles

from redstring.domain.ids import TenantId
from redstring.events.document import DocumentChunked
from redstring.ports.chunk_store import ChunkWriter


class ChunkProjection(StoreProjection[ChunkWriter]):
    """Maintains a `ChunkStore` from the event log.

    Typed against `ChunkWriter` rather than the whole port: this class calls
    `replace_source` and nothing else, one of the port's nine methods. Any
    `ChunkStore` still satisfies it.
    """

    @handles(DocumentChunked)
    async def _apply_chunking(self, _context: object, event: DocumentChunked) -> None:
        await self._store.replace_source(event.source_id, TenantId(event.tenant_id), event.chunks)

    async def _truncate_read_models(self) -> None:
        """Not supported; see `GraphProjection._truncate_read_models`.

        `ChunkPurge.delete_by_tenant` is the only bulk delete the port has,
        for the same reason: nothing here spans tenants.
        """
        raise NotImplementedError(
            "ChunkStore has no cross-tenant delete by design; wipe with "
            "delete_by_tenant(tenant_id) for each tenant being rebuilt"
        )
