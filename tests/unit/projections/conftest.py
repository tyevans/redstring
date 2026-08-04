"""Shared machinery for the projection suite: building logs, dumping stores.

Everything here is real -- `InMemoryEventStore`, `InMemoryGraphStore`,
`InMemoryVectorStore`, `InMemoryCheckpointRepository`, `InMemoryDLQRepository`
-- and nothing is mocked. A mocked store cannot fail a replay-equivalence
test, which would make the test worthless.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from eventsource.adapters.memory import (
    InMemoryCheckpointRepository,
    InMemoryDLQRepository,
    InMemoryEventStore,
)
from eventsource.application.projections.retry import ExponentialBackoffRetryPolicy
from eventsource.application.subscriptions.retry import RetryConfig

from kg_builder.graph.adapters.memory import InMemoryGraphStore
from kg_builder.projections import GraphProjection, VectorProjection
from kg_builder.vector.adapters.memory import InMemoryVectorStore

#: Vector length for every embedding in this suite.
DIMENSION = 4

#: Retries with no backoff. The default policy sleeps two seconds before its
#: first retry, so a suite that exercises the DLQ path would spend most of its
#: time asleep. Zero retries also makes "went to the DLQ" mean "failed once",
#: which is what the tests assert.
NO_RETRIES = ExponentialBackoffRetryPolicy(config=RetryConfig(max_retries=0))


@dataclass
class Rig:
    """An event log, two empty stores, and the projections between them."""

    event_store: InMemoryEventStore
    graph_store: InMemoryGraphStore
    vector_store: InMemoryVectorStore
    dlq: InMemoryDLQRepository
    projections: list

    async def dump(self, tenant_ids):
        return await dump_stores(self.graph_store, self.vector_store, tenant_ids)

    async def shape(self, tenant_ids):
        return await dump_shape(self.graph_store, self.vector_store, tenant_ids)


def fresh_rig() -> Rig:
    """A rig with nothing in it.

    A function rather than a fixture because the hypothesis tests need one
    **per example**, and a function-scoped fixture is created once for the
    whole `@given` -- so every example after the first would run against a log
    and stores the previous examples left behind. That is not hypothetical:
    it made this suite fail intermittently, with a merge event from an earlier
    example landing in a later example's log, and `suppress_health_check=
    [HealthCheck.function_scoped_fixture]` was what hid it.
    """
    graph_store = InMemoryGraphStore()
    vector_store = InMemoryVectorStore(dimension=DIMENSION)
    dlq = InMemoryDLQRepository()
    return Rig(
        event_store=InMemoryEventStore(),
        graph_store=graph_store,
        vector_store=vector_store,
        dlq=dlq,
        projections=[
            GraphProjection(
                graph_store,
                checkpoint_repo=InMemoryCheckpointRepository(),
                dlq_repo=dlq,
                retry_policy=NO_RETRIES,
            ),
            VectorProjection(
                vector_store,
                checkpoint_repo=InMemoryCheckpointRepository(),
                dlq_repo=dlq,
                retry_policy=NO_RETRIES,
            ),
        ],
    )


@pytest.fixture
def rig():
    return fresh_rig()


async def dump_stores(graph_store, vector_store, tenant_ids):
    """Everything the two stores hold for `tenant_ids`, in a comparable form.

    Read entirely through the ports -- `find_entities`, `get_relationships_for`
    and `VectorStore.get` -- rather than by reaching into either adapter's
    internals. A dump that read private state would pass on a store that
    diverged from what its own port reports, which is the divergence a replay
    test exists to catch.

    Sorted and turned into plain data because `find_entities` promises a total
    order but `get_relationships_for` promises none, and comparing two states
    must not depend on which order an adapter happened to return.
    """
    state = {}
    for tenant_id in sorted(tenant_ids, key=str):
        entities = await _all_entities(graph_store, tenant_id)
        relationships = await graph_store.get_relationships_for([e.id for e in entities], tenant_id)
        vectors = []
        for entity in entities:
            record = await vector_store.get(entity.id, tenant_id)
            if record is not None:
                vectors.append(record.model_dump(mode="json"))
        state[str(tenant_id)] = {
            "entities": sorted((e.model_dump(mode="json") for e in entities), key=str),
            "relationships": sorted((r.model_dump(mode="json") for r in relationships), key=str),
            "vectors": sorted(vectors, key=str),
        }
    return state


async def _all_entities(graph_store, tenant_id, *, page=50, max_pages=1000):
    """Every entity of a tenant, paged through the cursor the port defines.

    Bounded: the loop's exit depends on the adapter returning a short page, and
    a cursor that failed to advance would hang rather than fail.
    """
    entities = []
    after = None
    for _ in range(max_pages):
        found = await graph_store.find_entities(tenant_id, limit=page, after=after)
        entities.extend(found)
        if len(found) < page:
            return entities
        after = found[-1].id
    raise AssertionError(
        f"paging {tenant_id} did not terminate in {max_pages} pages; the "
        f"`after` cursor is probably not advancing"
    )


async def dump_shape(graph_store, vector_store, tenant_ids):
    """The stores reduced to what an oracle can predict.

    `dump_stores` compares a projection against *itself* after a replay, so it
    keeps every field. This one is compared against `BuiltLog.expected_shape`,
    which knows which entities exist, where each edge points, and what each
    vector is -- and deliberately not the rest, because an oracle that
    restated every field of every payload would just be a second copy of the
    fold.
    """
    shape = {}
    for tenant_id in sorted(tenant_ids, key=str):
        entities = await _all_entities(graph_store, tenant_id)
        relationships = await graph_store.get_relationships_for([e.id for e in entities], tenant_id)
        vectors = {}
        for entity in entities:
            record = await vector_store.get(entity.id, tenant_id)
            if record is not None:
                vectors[str(entity.id)] = record.vector
        shape[str(tenant_id)] = {
            "entity_ids": sorted(str(e.id) for e in entities),
            "edges": {
                str(r.id): [str(r.source_entity_id), str(r.target_entity_id)] for r in relationships
            },
            "vectors": vectors,
        }
    return shape
