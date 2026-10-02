"""Tests for optimistic concurrency retry and replanning in ConsolidationService."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from eventsource import OptimisticLockError
from eventsource.adapters.memory import InMemoryEventStore, InMemorySnapshotStore
from eventsource.domain.tenant_context import tenant_scope

from redstring.aggregates.repositories import consolidation_repository
from redstring.consolidation.service import ConsolidationService
from redstring.events.merge import EntitiesMerged
from redstring.events.streams import consolidation_stream
from redstring.graph.adapters.memory import InMemoryGraphStore

from .conftest import Rig, edge, keyed


class TestConcurrentMergeReplan:
    async def test_concurrent_merges_on_same_tenant_stream_retry_and_both_succeed(self):
        """Two concurrent merges on the same tenant target the same ConsolidationLog aggregate.

        One attempt will encounter an OptimisticLockError, retry, reload the updated
        log, and succeed. Both events are persisted without caller exception.
        """
        rig, tenant_id = Rig(), uuid4()
        canonical_1 = keyed(tenant_id, "Ada Lovelace")
        absorbed_1 = keyed(tenant_id, "A. Lovelace")
        canonical_2 = keyed(tenant_id, "Charles Babbage")
        absorbed_2 = keyed(tenant_id, "C. Babbage")
        await rig.seed(canonical_1, absorbed_1, canonical_2, absorbed_2)

        task_1 = rig.service.merge(
            tenant_id=tenant_id,
            canonical_entity_id=canonical_1.id,
            merged_entity_ids=[absorbed_1.id],
            merge_reason="concurrent 1",
        )
        task_2 = rig.service.merge(
            tenant_id=tenant_id,
            canonical_entity_id=canonical_2.id,
            merged_entity_ids=[absorbed_2.id],
            merge_reason="concurrent 2",
        )

        event_1, event_2 = await asyncio.gather(task_1, task_2)

        assert isinstance(event_1, EntitiesMerged)
        assert isinstance(event_2, EntitiesMerged)
        events = await rig.events()
        assert len([e for e in events if isinstance(e, EntitiesMerged)]) == 2

    async def test_retry_re_reads_updated_graph_topology(self):
        """When an initial attempt conflicts, the retry must re-read relationships from the graph

        and include late edges in the planned redirections.
        """
        rig, tenant_id = Rig(), uuid4()
        canonical = keyed(tenant_id, "Ada Lovelace")
        absorbed = keyed(tenant_id, "A. Lovelace")
        outsider = keyed(tenant_id, "Analytical Engine")
        await rig.seed(canonical, absorbed, outsider)

        # We stage a concurrent append directly into the aggregate stream
        # so that rig.service.merge's first save attempt conflicts.
        conflict_happened = False
        original_save = rig.service._log.save

        async def save_with_concurrent_edge_injection(log):
            nonlocal conflict_happened
            if not conflict_happened:
                conflict_happened = True
                # Add an edge to the graph store that the initial read missed
                late_edge = edge(tenant_id, source=absorbed.id, target=outsider.id, kind="invented")
                await rig.graph_store.upsert_relationships([late_edge])

                # Commit a dummy merge event to advance the stream version
                async with tenant_scope(tenant_id):
                    concurrent_log = await rig.service._log.load_or_create(
                        consolidation_stream(tenant_id=tenant_id).aggregate_id
                    )
                    other_canonical = keyed(tenant_id, "Other Canonical")
                    other_absorbed = keyed(tenant_id, "Other Absorbed")
                    await rig.seed(other_canonical, other_absorbed)
                    concurrent_log.merge(
                        tenant_id=tenant_id,
                        canonical_entity_id=other_canonical.id,
                        merged_entity_ids=[other_absorbed.id],
                        merge_reason="interfering merge",
                        redirections=[],
                        resolution=None,
                    )
                    await original_save(concurrent_log)

            return await original_save(log)

        rig.service._log.save = save_with_concurrent_edge_injection

        merged = await rig.service.merge(
            tenant_id=tenant_id,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
            max_retries=3,
        )

        assert conflict_happened
        # The re-read during retry saw the late_edge and planned a redirection for it!
        assert len(merged.redirections) == 1
        assert merged.redirections[0].before.relationship_type == "invented"

    async def test_exhausted_retries_raises_optimistic_lock_error(self):
        """If conflicts persist past max_retries, OptimisticLockError is propagated."""
        event_store = InMemoryEventStore()
        graph_store = InMemoryGraphStore()
        repo = consolidation_repository(event_store, InMemorySnapshotStore())

        service = ConsolidationService(
            event_store=event_store,
            snapshot_store=InMemorySnapshotStore(),
            graph_store=graph_store,
        )

        tenant_id = uuid4()
        canonical = keyed(tenant_id, "Ada Lovelace")
        absorbed = keyed(tenant_id, "A. Lovelace")
        await graph_store.upsert_entities([canonical, absorbed])

        # Always advance stream version before save to force repeated conflict
        async def always_conflicting_save(log):
            async with tenant_scope(tenant_id):
                other = await repo.load_or_create(
                    consolidation_stream(tenant_id=tenant_id).aggregate_id
                )
                other.merge(
                    tenant_id=tenant_id,
                    canonical_entity_id=uuid4(),
                    merged_entity_ids=[uuid4()],
                    merge_reason="conflict",
                    redirections=[],
                    resolution=None,
                )
                await repo.save(other)
            return await repo.save(log)

        service._log.save = always_conflicting_save

        with pytest.raises(OptimisticLockError):
            await service.merge(
                tenant_id=tenant_id,
                canonical_entity_id=canonical.id,
                merged_entity_ids=[absorbed.id],
                max_retries=2,
            )
