"""Shared builders for the consolidation suite. Everything here is real."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from redstring.domain.entity import Entity
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.relationship import Relationship

#: Fixed rather than `datetime.now(UTC)`: a fixture that varies per run makes
#: any comparison on `observed_at` non-deterministic, and this suite compares
#: entities for equality.
OBSERVED = datetime(2026, 1, 15, 8, 20, tzinfo=UTC)


def entity(
    tenant_id,
    *,
    name="Ada Lovelace",
    entity_id=None,
    source_id="doc-1",
    confidence=1.0,
    observed_at=OBSERVED,
    **overrides,
) -> Entity:
    """`source_id` defaults to "doc-1" because `DocumentExtracted` requires every
    entity to be attributed to the document it came from -- a `None` there is
    refused by the event, not by `Entity`."""
    fields = {
        "id": entity_id or uuid4(),
        "tenant_id": tenant_id,
        "name": name,
        "normalized_name": name.lower(),
        "entity_type": "person",
        "provenance": Provenance(
            observed_at=observed_at,
            extraction_method=ExtractionMethod.MANUAL,
            confidence=confidence,
            source_id=source_id,
        ),
    }
    fields.update(overrides)
    return Entity(**fields)


def edge(tenant_id, *, source, target, kind="knows", confidence=0.5, **overrides):
    fields = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "source_entity_id": source,
        "target_entity_id": target,
        "relationship_type": kind,
        "confidence": confidence,
    }
    fields.update(overrides)
    return Relationship(**fields)


def keyed(tenant_id, name, **overrides):
    from redstring.domain.blocking import blocking_keys_for

    built = entity(tenant_id, name=name, **overrides)
    return built.model_copy(update={"blocking_keys": blocking_keys_for(built)})


class Rig:
    def __init__(self) -> None:
        from eventsource.adapters.memory import (
            InMemoryCheckpointRepository,
            InMemoryDLQRepository,
            InMemoryEventStore,
            InMemorySnapshotStore,
        )

        from redstring.consolidation.candidates import CandidateFinder
        from redstring.consolidation.service import ConsolidationService
        from redstring.graph.adapters.memory import InMemoryGraphStore
        from redstring.projections import GraphProjection

        self.event_store = InMemoryEventStore()
        self.graph_store = InMemoryGraphStore()
        self.projection = GraphProjection(
            self.graph_store,
            checkpoint_repo=InMemoryCheckpointRepository(),
            dlq_repo=InMemoryDLQRepository(),
        )
        self.service = ConsolidationService(
            event_store=self.event_store,
            snapshot_store=InMemorySnapshotStore(),
            graph_store=self.graph_store,
        )
        self.finder = CandidateFinder(self.graph_store, use_graph_signal=False)

    async def seed(self, *entities):
        """Entities straight into the store.

        The graph is a read model, so writing it directly is what a projection
        would have done -- and it keeps these tests about the policy rather
        than about the extraction aggregate, which
        `test_merge_undo_round_trip.py` already covers end to end.
        """
        await self.graph_store.upsert_entities(list(entities))

    async def events(self):
        return [envelope.event async for envelope in self.event_store.read_all()]

    async def catch_up(self):
        from eventsource.application.projections import replay

        report = await replay(self.event_store, [self.projection])
        assert report.failed == 0
