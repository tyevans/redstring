"""Tenant-scoped projection rebuild and wiping.

Ports forbid cross-tenant operations, so global `reset()` is refused (B35).
`wipe_tenant` and `rebuild(tenant_id)` provide the tenant-scoped equivalent:
clearing and re-hydrating read models exclusively for the requested tenant
while guaranteeing other tenants are untouched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from eventsource.adapters.memory import (
    InMemoryCheckpointRepository,
    InMemoryDLQRepository,
    InMemorySnapshotStore,
)
from eventsource.application.projections import ReplayFailedError, replay

from redstring.projections import rebuild_tenant
from redstring.projections.chunk import ChunkProjection
from redstring.projections.vector import VectorProjection
from redstring.testing.lifetime import NoOpLifetime

from .conftest import fresh_rig
from .log_builder import build_log
from .test_replay_equivalence import TWO_TENANTS, TWO_TENANTS_CHUNKED

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redstring.domain.chunk import StoredChunk
    from redstring.domain.ids import EntityId, SourceId, TenantId
    from redstring.domain.vector import VectorRecord


class DummyWriteOnlyVectorStore(NoOpLifetime):
    """VectorWriter without delete_by_tenant."""

    @property
    def dimension(self) -> int:
        return 4

    async def upsert(
        self,
        entity_id: EntityId,
        vector: Sequence[float],
        tenant_id: TenantId,
        *,
        metadata: object = None,
    ) -> None:
        pass

    async def upsert_many(self, items: Sequence[VectorRecord]) -> None:
        pass


class DummyWriteOnlyChunkStore(NoOpLifetime):
    """ChunkWriter without delete_by_tenant."""

    async def upsert_many(self, chunks: Sequence[StoredChunk]) -> int:
        return len(chunks)

    async def replace_source(
        self,
        source_id: SourceId,
        tenant_id: TenantId,
        chunks: Sequence[StoredChunk],
    ) -> int:
        return 0


class DummySubscriber:
    """EventSubscriber without wipe_tenant."""

    def __init__(self) -> None:
        self.handled = 0

    async def handle(self, event: object) -> None:
        self.handled += 1


class TestWipeTenant:
    async def test_graph_projection_wipe_tenant_removes_only_target(self) -> None:
        rig = fresh_rig()
        built = await build_log(rig.event_store, InMemorySnapshotStore(), TWO_TENANTS)
        t1, t2 = built.tenant_ids
        await replay(rig.event_store, rig.projections)

        assert len(await rig.graph_store.find_entities(t1)) > 0
        assert len(await rig.graph_store.find_entities(t2)) > 0

        await rig.projections[0].wipe_tenant(t1)

        assert len(await rig.graph_store.find_entities(t1)) == 0
        assert len(await rig.graph_store.find_entities(t2)) > 0

    async def test_vector_projection_wipe_tenant_removes_only_target(self) -> None:
        rig = fresh_rig()
        built = await build_log(rig.event_store, InMemorySnapshotStore(), TWO_TENANTS)
        t1, t2 = built.tenant_ids
        await replay(rig.event_store, rig.projections)

        t1_entities = await rig.graph_store.find_entities(t1)
        t2_entities = await rig.graph_store.find_entities(t2)

        assert await rig.vector_store.get(t1_entities[0].id, t1) is not None
        assert await rig.vector_store.get(t2_entities[0].id, t2) is not None

        await rig.projections[1].wipe_tenant(t1)

        assert await rig.vector_store.get(t1_entities[0].id, t1) is None
        assert await rig.vector_store.get(t2_entities[0].id, t2) is not None

    async def test_chunk_projection_wipe_tenant_removes_only_target(self) -> None:
        rig = fresh_rig()
        built = await build_log(rig.event_store, InMemorySnapshotStore(), TWO_TENANTS_CHUNKED)
        t1, t2 = built.tenant_ids
        await replay(rig.event_store, rig.projections)

        assert len(await rig.chunk_store.get_by_source("doc-0", t1)) > 0
        assert len(await rig.chunk_store.get_by_source("doc-0", t2)) > 0

        await rig.projections[2].wipe_tenant(t1)

        assert len(await rig.chunk_store.get_by_source("doc-0", t1)) == 0
        assert len(await rig.chunk_store.get_by_source("doc-0", t2)) > 0

    async def test_wipe_tenant_raises_when_underlying_store_cannot_purge(self) -> None:
        v_proj = VectorProjection(
            DummyWriteOnlyVectorStore(),
            checkpoint_repo=InMemoryCheckpointRepository(),
            dlq_repo=InMemoryDLQRepository(),
        )
        with pytest.raises(NotImplementedError, match="does not implement delete_by_tenant"):
            await v_proj.wipe_tenant(uuid4())

        c_proj = ChunkProjection(
            DummyWriteOnlyChunkStore(),
            checkpoint_repo=InMemoryCheckpointRepository(),
            dlq_repo=InMemoryDLQRepository(),
        )
        with pytest.raises(NotImplementedError, match="does not implement delete_by_tenant"):
            await c_proj.wipe_tenant(uuid4())


class TestProjectionRebuild:
    async def test_graph_projection_rebuild_resets_and_replays_target(self) -> None:
        rig = fresh_rig()
        built = await build_log(rig.event_store, InMemorySnapshotStore(), TWO_TENANTS)
        t1, t2 = built.tenant_ids
        await replay(rig.event_store, rig.projections)

        initial_t1_entities = await rig.graph_store.find_entities(t1)
        initial_t2_entities = await rig.graph_store.find_entities(t2)

        # Corrupt t1 state in the graph store
        await rig.projections[0].wipe_tenant(t1)
        assert len(await rig.graph_store.find_entities(t1)) == 0

        # Rebuild graph projection for t1
        report = await rig.projections[0].rebuild(rig.event_store, tenant_id=t1)
        assert report.applied > 0
        assert report.failed == 0

        rebuilt_t1_entities = await rig.graph_store.find_entities(t1)
        assert [e.id for e in rebuilt_t1_entities] == [e.id for e in initial_t1_entities]

        # t2 remains untouched
        rebuilt_t2_entities = await rig.graph_store.find_entities(t2)
        assert [e.id for e in rebuilt_t2_entities] == [e.id for e in initial_t2_entities]

    async def test_vector_projection_rebuild_resets_and_replays_target(self) -> None:
        rig = fresh_rig()
        built = await build_log(rig.event_store, InMemorySnapshotStore(), TWO_TENANTS)
        t1, t2 = built.tenant_ids
        await replay(rig.event_store, rig.projections)

        t1_entities = await rig.graph_store.find_entities(t1)
        t2_entities = await rig.graph_store.find_entities(t2)

        # Wipe t1 in vector store
        await rig.projections[1].wipe_tenant(t1)
        assert await rig.vector_store.get(t1_entities[0].id, t1) is None

        # Rebuild vector projection for t1
        report = await rig.projections[1].rebuild(rig.event_store, tenant_id=t1)
        assert report.applied > 0

        assert await rig.vector_store.get(t1_entities[0].id, t1) is not None
        assert await rig.vector_store.get(t2_entities[0].id, t2) is not None

    async def test_chunk_projection_rebuild_resets_and_replays_target(self) -> None:
        rig = fresh_rig()
        built = await build_log(rig.event_store, InMemorySnapshotStore(), TWO_TENANTS_CHUNKED)
        t1, t2 = built.tenant_ids
        await replay(rig.event_store, rig.projections)

        initial_t1_chunks = await rig.chunk_store.get_by_source("doc-0", t1)
        initial_t2_chunks = await rig.chunk_store.get_by_source("doc-0", t2)

        # Wipe t1 chunks
        await rig.projections[2].wipe_tenant(t1)
        assert len(await rig.chunk_store.get_by_source("doc-0", t1)) == 0

        # Rebuild chunk projection for t1
        report = await rig.projections[2].rebuild(rig.event_store, tenant_id=t1)
        assert report.applied > 0

        rebuilt_t1_chunks = await rig.chunk_store.get_by_source("doc-0", t1)
        assert [c.id for c in rebuilt_t1_chunks] == [c.id for c in initial_t1_chunks]

        rebuilt_t2_chunks = await rig.chunk_store.get_by_source("doc-0", t2)
        assert [c.id for c in rebuilt_t2_chunks] == [c.id for c in initial_t2_chunks]


class TestCoordinatedTenantRebuild:
    async def test_rebuild_tenant_wipes_and_replays_all_projections(self) -> None:
        rig = fresh_rig()
        built = await build_log(rig.event_store, InMemorySnapshotStore(), TWO_TENANTS_CHUNKED)
        t1, t2 = built.tenant_ids
        await replay(rig.event_store, rig.projections)

        initial_state = await rig.dump([t1, t2])

        # Wipe t1 completely
        for proj in rig.projections:
            await proj.wipe_tenant(t1)

        wiped_state = await rig.dump([t1, t2])
        assert wiped_state[str(t1)]["entities"] == []
        assert wiped_state[str(t1)]["vectors"] == []
        assert wiped_state[str(t1)]["chunks"] == []
        assert wiped_state[str(t2)] == initial_state[str(t2)]

        # Rebuild t1 across all projections
        report = await rebuild_tenant(rig.event_store, rig.projections, tenant_id=t1)
        assert report.applied > 0
        assert report.failed == 0

        restored_state = await rig.dump([t1, t2])
        assert restored_state[str(t1)] == initial_state[str(t1)]
        assert restored_state[str(t2)] == initial_state[str(t2)]

    async def test_rebuild_tenant_strict_mode(self, poisoned_log) -> None:
        rig, _ = poisoned_log
        from .conftest import POISON_TENANT_ID

        with pytest.raises(ReplayFailedError):
            await rebuild_tenant(
                rig.event_store, rig.projections, tenant_id=POISON_TENANT_ID, strict=True
            )

        report = await rebuild_tenant(
            rig.event_store, rig.projections, tenant_id=POISON_TENANT_ID, strict=False
        )
        assert report.failed == 1

    async def test_rebuild_tenant_tolerates_subscribers_without_wipe_tenant(self) -> None:
        rig = fresh_rig()
        built = await build_log(rig.event_store, InMemorySnapshotStore(), TWO_TENANTS)
        t1, _ = built.tenant_ids

        dummy = DummySubscriber()
        projections = [*rig.projections, dummy]

        report = await rebuild_tenant(rig.event_store, projections, tenant_id=t1)
        assert report.applied > 0
        assert dummy.handled > 0
