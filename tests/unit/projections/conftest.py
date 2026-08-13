"""Shared machinery for the projection suite: building logs, dumping stores.

Everything here is real -- `InMemoryEventStore`, `InMemoryGraphStore`,
`InMemoryVectorStore`, `InMemoryChunkStore`, `InMemoryCheckpointRepository`,
`InMemoryDLQRepository`
-- and nothing is mocked. A mocked store cannot fail a replay-equivalence
test, which would make the test worthless.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest
from eventsource.adapters.memory import (
    InMemoryCheckpointRepository,
    InMemoryDLQRepository,
    InMemoryEventStore,
)
from eventsource.application.projections.retry import ExponentialBackoffRetryPolicy
from eventsource.application.subscriptions.retry import RetryConfig
from eventsource.domain.tenant_context import tenant_scope
from eventsource.ports.positions import ExpectedVersion

from redstring.chunks.adapters.memory import InMemoryChunkStore
from redstring.domain.entity import Entity
from redstring.domain.provenance import ExtractionMethod
from redstring.domain.relationship import Relationship
from redstring.events import DocumentExtracted
from redstring.events.streams import document_stream
from redstring.graph.adapters.memory import InMemoryGraphStore
from redstring.projections import ChunkProjection, GraphProjection, VectorProjection
from redstring.vector.adapters.memory import InMemoryVectorStore

#: Vector length for every embedding in this suite.
DIMENSION = 4

#: Every source id `log_builder` can produce, and the only ones a dump reads.
#:
#: A `ChunkStore` has no "list every source" method -- nothing outside a test
#: wants one -- so the dumps below probe. Declared here rather than in
#: `log_builder` because `log_builder` imports this module and not the other
#: way round, and `build_log` asserts that every source it writes is in this
#: tuple: a document named outside it would otherwise have its chunks silently
#: omitted from both the dump and the comparison, which is a corpus nothing
#: checks rather than a failure.
SOURCE_IDS = tuple(f"doc-{i}" for i in range(3))

#: Retries with no backoff. The default policy sleeps two seconds before its
#: first retry, so a suite that exercises the DLQ path would spend most of its
#: time asleep. Zero retries also makes "went to the DLQ" mean "failed once",
#: which is what the tests assert.
NO_RETRIES = ExponentialBackoffRetryPolicy(config=RetryConfig(max_retries=0))


@dataclass
class Rig:
    """An event log, three empty stores, and the projections between them."""

    event_store: InMemoryEventStore
    graph_store: InMemoryGraphStore
    vector_store: InMemoryVectorStore
    chunk_store: InMemoryChunkStore
    dlq: InMemoryDLQRepository
    projections: list

    async def dump(self, tenant_ids):
        return await dump_stores(self.graph_store, self.vector_store, self.chunk_store, tenant_ids)

    async def shape(self, tenant_ids):
        return await dump_shape(self.graph_store, self.vector_store, self.chunk_store, tenant_ids)


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
    chunk_store = InMemoryChunkStore()
    dlq = InMemoryDLQRepository()
    return Rig(
        event_store=InMemoryEventStore(),
        graph_store=graph_store,
        vector_store=vector_store,
        chunk_store=chunk_store,
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
            ChunkProjection(
                chunk_store,
                checkpoint_repo=InMemoryCheckpointRepository(),
                dlq_repo=dlq,
                retry_policy=NO_RETRIES,
            ),
        ],
    )


@pytest.fixture
def rig():
    return fresh_rig()


async def dump_stores(graph_store, vector_store, chunk_store, tenant_ids):
    """Everything the three stores hold for `tenant_ids`, in a comparable form.

    Read entirely through the ports -- `find_entities`, `get_relationships_for`,
    `VectorStore.get` and `ChunkStore.get_by_source` -- rather than by reaching
    into any adapter's
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
        # Aliases are part of the state a rebuild must reproduce, and the only
        # part with a generated id -- so leaving them out would let a `uuid4`
        # alias id pass every replay-equivalence test in the suite.
        aliases = [
            alias.model_dump(mode="json")
            for entity in entities
            for alias in await graph_store.find_aliases(entity.id, tenant_id)
        ]
        chunks = [
            chunk.model_dump(mode="json")
            for source_id in SOURCE_IDS
            for chunk in await chunk_store.get_by_source(source_id, tenant_id)
        ]
        state[str(tenant_id)] = {
            "entities": sorted((e.model_dump(mode="json") for e in entities), key=str),
            "relationships": sorted((r.model_dump(mode="json") for r in relationships), key=str),
            "aliases": sorted(aliases, key=str),
            "vectors": sorted(vectors, key=str),
            "chunks": sorted(chunks, key=str),
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


async def dump_shape(graph_store, vector_store, chunk_store, tenant_ids):
    """The stores reduced to what an oracle can predict.

    `dump_stores` compares a projection against *itself* after a replay, so it
    keeps every field. This one is compared against `BuiltLog.expected_shape`,
    which knows which entities exist, where each edge points, what each
    vector is, and which chunk ids each source holds in what order -- and
    deliberately not the rest, because an oracle that restated every field of
    every payload would just be a second copy of the fold. Notably, it does
    not pin `StoredChunk.entity_ids` -- see BACKLOG B90.
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
        # Ids in the order the port promises, per source: the oracle knows
        # which passages should survive a re-chunk and in what order, and an
        # id set alone could not tell a dropped orphan from a kept one.
        chunks = {}
        for source_id in SOURCE_IDS:
            found = await chunk_store.get_by_source(source_id, tenant_id)
            if found:
                chunks[source_id] = [chunk.id for chunk in found]
        shape[str(tenant_id)] = {
            "entity_ids": sorted(str(e.id) for e in entities),
            "edges": {
                str(r.id): [str(r.source_entity_id), str(r.target_entity_id)] for r in relationships
            },
            "vectors": vectors,
            "chunks": chunks,
        }
    return shape


# --- The poisoned log ------------------------------------------------------
#
# Shared by `test_poison_events.py` (what the fold does with it) and
# `test_replay_failures.py` (what the report says about it). One definition
# because two would drift, and the tests would then disagree about which
# document is the poison.

POISON_TENANT_ID = uuid4()
POISON_MODEL = "ollama/qwen3.6-27b"


def poison_entity(source_id, name):
    return Entity(
        id=uuid4(),
        tenant_id=POISON_TENANT_ID,
        name=name,
        normalized_name=name.lower(),
        entity_type="thing",
        source_id=source_id,
        extraction_method=ExtractionMethod.PATTERN,
        confidence=0.5,
    )


async def append_document(event_store, source_id, entities, relationships):
    stream = document_stream(tenant_id=POISON_TENANT_ID, source_id=source_id)
    async with tenant_scope(POISON_TENANT_ID):
        await event_store.append(
            stream,
            [
                DocumentExtracted(
                    aggregate_id=stream.aggregate_id,
                    tenant_id=POISON_TENANT_ID,
                    source_id=source_id,
                    model_version=POISON_MODEL,
                    entities=entities,
                    relationships=relationships,
                )
            ],
            ExpectedVersion.no_stream(),
        )


@pytest.fixture
async def poisoned_log(rig):
    """Three documents: good, poison, good. The poison is in the middle so a
    projection that stopped on it would visibly drop the third."""
    first = poison_entity("doc-1", "Ada")
    await append_document(rig.event_store, "doc-1", [first], [])

    dangling = poison_entity("doc-2", "Grace")
    await append_document(
        rig.event_store,
        "doc-2",
        [dangling],
        [
            Relationship(
                id=uuid4(),
                tenant_id=POISON_TENANT_ID,
                source_entity_id=dangling.id,
                target_entity_id=uuid4(),  # never extracted by any document
                relationship_type="knows",
                confidence=0.5,
            )
        ],
    )

    last = poison_entity("doc-3", "Barbara")
    await append_document(rig.event_store, "doc-3", [last], [])
    return rig, [first, dangling, last]
