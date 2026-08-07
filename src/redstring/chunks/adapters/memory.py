"""In-memory `ChunkStore`: the reference adapter.

A real implementation, not a stub. It enforces every contract the port states
-- provenance validation on `replace_source`, the `(chunk_index, id)` ordering,
the orphan count -- because an adapter more permissive than its port is
useless as a reference: tests written against it would pass here and fail on
Postgres.

**Copy on write and on read**, as in `vector/adapters/memory.py`. Handing out
a reference lets a caller mutate stored state by accident, and keeping the
caller's object lets a caller mutate it afterwards. Both directions are closed
with a deep copy -- `entity_ids` is a list, so a shallow copy would leave it
shared.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redstring.domain.chunk import ChunkId, StoredChunk
    from redstring.domain.ids import SourceId, TenantId


class InMemoryChunkStore:
    """A `ChunkStore` backed by plain dictionaries."""

    def __init__(self) -> None:
        self._chunks: dict[TenantId, dict[ChunkId, StoredChunk]] = {}

    async def upsert_many(self, chunks: Sequence[StoredChunk]) -> None:
        for chunk in chunks:
            tenant = self._chunks.setdefault(chunk.tenant_id, {})
            # The key is the *pair*: `chunk.tenant_id` selects the mapping and
            # `chunk.id` the slot, so two tenants holding the same
            # content-addressed id are two rows. Content addressing makes that
            # collision ordinary rather than astronomically unlikely.
            tenant[chunk.id] = chunk.model_copy(deep=True)

    async def get(self, chunk_id: ChunkId, tenant_id: TenantId) -> StoredChunk | None:
        chunk = self._chunks.get(tenant_id, {}).get(chunk_id)
        return None if chunk is None else chunk.model_copy(deep=True)

    async def get_by_source(self, source_id: SourceId, tenant_id: TenantId) -> list[StoredChunk]:
        found = [
            chunk
            for chunk in self._chunks.get(tenant_id, {}).values()
            if chunk.source_id == source_id
        ]
        # `chunk_index` then `id`: the index is not unique under content
        # addressing, and ordering on it alone would let two adapters disagree.
        found.sort(key=lambda chunk: (chunk.chunk_index, chunk.id))
        return [chunk.model_copy(deep=True) for chunk in found]

    async def replace_source(
        self,
        source_id: SourceId,
        tenant_id: TenantId,
        chunks: Sequence[StoredChunk],
    ) -> int:
        strays = [
            chunk
            for chunk in chunks
            if chunk.source_id != source_id or chunk.tenant_id != tenant_id
        ]
        if strays:
            raise ValueError(
                f"every chunk must carry source_id={source_id!r} and "
                f"tenant_id={tenant_id}; found "
                f"{sorted({(c.source_id, str(c.tenant_id)) for c in strays})}"
            )

        keep = {chunk.id for chunk in chunks}
        tenant = self._chunks.setdefault(tenant_id, {})
        orphans = [
            chunk_id
            for chunk_id, chunk in tenant.items()
            if chunk.source_id == source_id and chunk_id not in keep
        ]
        for chunk_id in orphans:
            del tenant[chunk_id]
        await self.upsert_many(chunks)
        return len(orphans)

    async def delete_by_source(self, source_id: SourceId, tenant_id: TenantId) -> int:
        tenant = self._chunks.get(tenant_id, {})
        doomed = [chunk_id for chunk_id, chunk in tenant.items() if chunk.source_id == source_id]
        for chunk_id in doomed:
            del tenant[chunk_id]
        return len(doomed)

    async def delete_by_tenant(self, tenant_id: TenantId) -> int:
        return len(self._chunks.pop(tenant_id, {}))
