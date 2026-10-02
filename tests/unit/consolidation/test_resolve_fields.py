"""Tests for property merging and field resolution during entity merge."""

from __future__ import annotations

from uuid import uuid4

import pytest
from eventsource.adapters.memory import InMemorySnapshotStore

from redstring.consolidation.service import ConsolidationService
from redstring.domain.exceptions import MissingEntityError
from redstring.domain.ids import EntityId
from redstring.domain.merge_strategy import PropertyMergePolicy, PropertyMergeStrategy

from .conftest import Rig, keyed


class TestMergeDecidesFields:
    async def test_the_emitted_event_carries_a_resolution(self):
        """`plan_properties` is called and attaches resolution to event."""
        rig, tenant = Rig(), uuid4()
        canonical = keyed(tenant, "Ada Lovelace", properties={"role": "mathematician"})
        absorbed = keyed(tenant, "Ada Lovelace", properties={"role": "analyst"})
        await rig.seed(canonical, absorbed)

        event = await rig.service.merge(
            tenant_id=tenant,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
        )

        assert event.resolution is not None
        assert event.resolution.entity_id == canonical.id
        assert event.resolution.before.properties == canonical.properties

    async def test_the_services_policy_decides(self):
        """A non-default policy configured on the service reaches `plan_properties`."""
        rig, tenant = Rig(), uuid4()
        canonical = keyed(tenant, "Ada Lovelace", properties={"role": "mathematician"})
        absorbed = keyed(tenant, "Ada Lovelace", properties={"role": "analyst"})
        await rig.seed(canonical, absorbed)
        service = ConsolidationService(
            event_store=rig.event_store,
            snapshot_store=InMemorySnapshotStore(),
            graph_store=rig.graph_store,
            merge_policy=PropertyMergePolicy(default=PropertyMergeStrategy.PREFER_MERGED),
        )

        event = await service.merge(
            tenant_id=tenant,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
        )

        assert event.resolution.after.properties == absorbed.properties

    async def test_a_per_call_policy_overrides_the_services(self):
        rig, tenant = Rig(), uuid4()
        canonical = keyed(tenant, "Ada Lovelace", properties={"role": "mathematician"})
        absorbed = keyed(tenant, "Ada Lovelace", properties={"role": "analyst"})
        await rig.seed(canonical, absorbed)

        event = await rig.service.merge(
            tenant_id=tenant,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
            policy=PropertyMergePolicy(default=PropertyMergeStrategy.PREFER_MERGED),
        )

        assert event.resolution.after.properties == absorbed.properties

    async def test_a_canonical_entity_with_no_row_is_refused(self):
        """Log and graph disagreeing raises MissingEntityError."""
        rig, tenant = Rig(), uuid4()
        absorbed = keyed(tenant, "Ada Lovelace")
        await rig.seed(absorbed)

        with pytest.raises(MissingEntityError):
            await rig.service.merge(
                tenant_id=tenant,
                canonical_entity_id=EntityId(uuid4()),
                merged_entity_ids=[absorbed.id],
            )

    async def test_an_absorbed_entity_with_no_row_is_tolerated(self):
        """Absorbed entity missing from graph read model is tolerated."""
        rig, tenant = Rig(), uuid4()
        canonical = keyed(tenant, "Ada Lovelace")
        await rig.seed(canonical)

        event = await rig.service.merge(
            tenant_id=tenant,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[EntityId(uuid4())],
        )

        assert event.resolution is not None
