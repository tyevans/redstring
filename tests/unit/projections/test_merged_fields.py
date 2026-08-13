"""`GraphProjection` applying a merge's field decision, and undoing it.

Task 5: the field decision Tasks 1-4 built and emitted (`EntitiesMerged.resolution`,
`MergeUndone.restored_fields`) has to actually reach the graph. These tests are
narrower than the round trip in `tests/unit/consolidation/test_merge_undo_round_trip.py`
-- that one goes through the real `ConsolidationService`; these construct the
events by hand so each case (no decision at all, an unknown canonical entity,
applying the same event twice) can be isolated.

Everything here is real -- `InMemoryGraphStore`, a real `GraphProjection` -- and
nothing is mocked, following `tests/unit/projections/conftest.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from eventsource.adapters.memory import InMemoryDLQRepository

from redstring.domain.consolidation import MergeableFields, PropertyResolution
from redstring.domain.entity import Entity
from redstring.domain.exceptions import MissingEntityError
from redstring.domain.ids import EntityId, TenantId
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.events.merge import EntitiesMerged
from redstring.graph.adapters.memory import InMemoryGraphStore
from redstring.projections import GraphProjection

from .conftest import NO_RETRIES

OBSERVED = datetime(2026, 2, 4, 11, 7, tzinfo=UTC)


def _entity(tenant_id: TenantId, *, name: str, **overrides) -> Entity:
    fields = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "name": name,
        "normalized_name": name.lower(),
        "entity_type": "person",
        "properties": {"role": "analyst"},
        "provenance": Provenance(
            observed_at=OBSERVED,
            extraction_method=ExtractionMethod.MANUAL,
            confidence=1.0,
            source_id="doc-1",
        ),
    }
    fields.update(overrides)
    return Entity(**fields)


class Rig:
    """One store, one projection. Real throughout -- see the module docstring."""

    def __init__(self) -> None:
        self.store = InMemoryGraphStore()
        self.dlq = InMemoryDLQRepository()
        self.projection = GraphProjection(self.store, dlq_repo=self.dlq, retry_policy=NO_RETRIES)


def _merged(
    tenant_id: TenantId,
    *,
    canonical_entity_id: EntityId,
    merged_entity_ids: list[EntityId],
    resolution: PropertyResolution | None = None,
) -> EntitiesMerged:
    return EntitiesMerged(
        aggregate_id=uuid4(),
        tenant_id=tenant_id,
        canonical_entity_id=canonical_entity_id,
        merged_entity_ids=merged_entity_ids,
        resolution=resolution,
    )


class TestApplyingAFieldDecision:
    async def test_a_merge_writes_the_resolved_fields_onto_the_canonical_entity(self) -> None:
        rig = Rig()
        tenant_id = TenantId(uuid4())
        canonical = _entity(tenant_id, name="Ada Lovelace", properties={"role": "analyst"})
        absorbed = _entity(tenant_id, name="A. Lovelace")
        await rig.store.upsert_entities([canonical, absorbed])

        await rig.projection.handle(
            _merged(
                tenant_id,
                canonical_entity_id=canonical.id,
                merged_entity_ids=[absorbed.id],
                resolution=PropertyResolution(
                    entity_id=canonical.id,
                    before=MergeableFields(properties={"role": "analyst"}),
                    after=MergeableFields(
                        description="a mathematician",
                        external_ids={"wikidata": "Q7259"},
                        properties={"role": "mathematician"},
                    ),
                ),
            )
        )

        stored = await rig.store.get_entity(canonical.id, tenant_id)
        assert stored is not None
        assert stored.properties == {"role": "mathematician"}
        assert stored.description == "a mathematician"
        assert stored.external_ids == {"wikidata": "Q7259"}

    async def test_it_changes_nothing_else_about_the_entity(self) -> None:
        """The upsert must be a copy with three fields replaced, not a rebuilt
        entity. `name` and `provenance` surviving is what says so."""
        rig = Rig()
        tenant_id = TenantId(uuid4())
        canonical = _entity(tenant_id, name="Ada Lovelace", properties={"role": "analyst"})
        absorbed = _entity(tenant_id, name="A. Lovelace")
        await rig.store.upsert_entities([canonical, absorbed])

        await rig.projection.handle(
            _merged(
                tenant_id,
                canonical_entity_id=canonical.id,
                merged_entity_ids=[absorbed.id],
                resolution=PropertyResolution(
                    entity_id=canonical.id,
                    before=MergeableFields(properties={"role": "analyst"}),
                    after=MergeableFields(properties={"role": "mathematician"}),
                ),
            )
        )

        stored = await rig.store.get_entity(canonical.id, tenant_id)
        assert stored is not None
        assert stored.name == canonical.name
        assert stored.provenance == canonical.provenance

    async def test_a_merge_that_decided_nothing_leaves_the_entity_alone(self) -> None:
        rig = Rig()
        tenant_id = TenantId(uuid4())
        canonical = _entity(tenant_id, name="Ada Lovelace", properties={"role": "analyst"})
        absorbed = _entity(tenant_id, name="A. Lovelace")
        await rig.store.upsert_entities([canonical, absorbed])

        await rig.projection.handle(
            _merged(tenant_id, canonical_entity_id=canonical.id, merged_entity_ids=[absorbed.id])
        )

        stored = await rig.store.get_entity(canonical.id, tenant_id)
        assert stored == canonical

    async def test_it_leaves_the_absorbed_entity_untouched(self) -> None:
        """The premise the one-entity payload rests on. If a merge ever did
        write to an absorbed entity, the undo payload would be incomplete and
        nothing else in the suite would notice."""
        rig = Rig()
        tenant_id = TenantId(uuid4())
        canonical = _entity(tenant_id, name="Ada Lovelace", properties={"role": "analyst"})
        absorbed = _entity(tenant_id, name="A. Lovelace")
        await rig.store.upsert_entities([canonical, absorbed])

        await rig.projection.handle(
            _merged(
                tenant_id,
                canonical_entity_id=canonical.id,
                merged_entity_ids=[absorbed.id],
                resolution=PropertyResolution(
                    entity_id=canonical.id,
                    before=MergeableFields(properties={"role": "analyst"}),
                    after=MergeableFields(properties={"role": "mathematician"}),
                ),
            )
        )

        stored = await rig.store.get_entity(absorbed.id, tenant_id)
        assert stored == absorbed

    async def test_applying_the_same_merge_twice_is_the_same_as_once(self) -> None:
        rig = Rig()
        tenant_id = TenantId(uuid4())
        canonical = _entity(tenant_id, name="Ada Lovelace", properties={"role": "analyst"})
        absorbed = _entity(tenant_id, name="A. Lovelace")
        await rig.store.upsert_entities([canonical, absorbed])

        event = _merged(
            tenant_id,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
            resolution=PropertyResolution(
                entity_id=canonical.id,
                before=MergeableFields(properties={"role": "analyst"}),
                after=MergeableFields(properties={"role": "mathematician"}),
            ),
        )

        await rig.projection.handle(event)
        after_first = await rig.store.get_entity(canonical.id, tenant_id)
        await rig.projection.handle(event)
        after_second = await rig.store.get_entity(canonical.id, tenant_id)

        assert after_second == after_first

    async def test_a_canonical_entity_with_no_row_is_a_poison_event(self) -> None:
        rig = Rig()
        tenant_id = TenantId(uuid4())
        absorbed = _entity(tenant_id, name="A. Lovelace")
        await rig.store.upsert_entities([absorbed])

        missing = EntityId(uuid4())
        with pytest.raises(MissingEntityError):
            await rig.projection.handle(
                _merged(
                    tenant_id,
                    canonical_entity_id=missing,
                    merged_entity_ids=[absorbed.id],
                    resolution=PropertyResolution(
                        entity_id=missing,  # the same unknown id
                        before=MergeableFields(),
                        after=MergeableFields(properties={"role": "x"}),
                    ),
                )
            )
