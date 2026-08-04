"""Round trips through a real in-memory event store, with real tenant scoping.

No mocks: `InMemoryEventStore` and `InMemorySnapshotStore` are the library's
own adapters, and `tenant_scope` is the real context manager the write path
uses in production.
"""

from uuid import uuid4

import pytest
from eventsource.adapters.memory import InMemoryEventStore, InMemorySnapshotStore
from eventsource.domain import StreamId
from eventsource.domain.exceptions import (
    OptimisticLockError,
    TenantContextNotSetError,
    TenantMismatchError,
)
from eventsource.domain.tenant_context import tenant_scope
from eventsource.ports.positions import ExpectedVersion

from kg_builder.aggregates.consolidation_log import ConsolidationLog
from kg_builder.aggregates.document import Document
from kg_builder.aggregates.repositories import (
    consolidation_repository,
    document_repository,
)
from kg_builder.events.streams import (
    CONSOLIDATION_CATEGORY,
    DOCUMENT_CATEGORY,
    consolidation_stream,
    document_stream,
)

SOURCE_ID = "doc-1"
MODEL = "ollama/qwen3.6-27b"


@pytest.fixture
def event_store():
    return InMemoryEventStore()


@pytest.fixture
def tenant_id():
    return uuid4()


class TestDocumentRepository:
    async def test_an_extraction_round_trips_through_the_log(self, event_store, tenant_id):
        repo = document_repository(event_store)
        aggregate_id = document_stream(tenant_id=tenant_id, source_id=SOURCE_ID).aggregate_id

        async with tenant_scope(tenant_id):
            document = repo.create_new(aggregate_id)
            document.record_extraction(
                tenant_id=tenant_id, source_id=SOURCE_ID, model_version=MODEL
            )
            await repo.save(document)

            reloaded = await repo.load(aggregate_id)

        assert reloaded.version == 1
        assert reloaded.state.extraction_model_versions == [MODEL]

    async def test_a_retry_after_a_crash_writes_nothing_a_second_time(self, event_store, tenant_id):
        """The idempotency rule where it matters: against a reloaded
        aggregate, which is all a retry ever has."""
        repo = document_repository(event_store)
        aggregate_id = document_stream(tenant_id=tenant_id, source_id=SOURCE_ID).aggregate_id

        async with tenant_scope(tenant_id):
            first = repo.create_new(aggregate_id)
            first.record_extraction(tenant_id=tenant_id, source_id=SOURCE_ID, model_version=MODEL)
            await repo.save(first)

            retry = await repo.load(aggregate_id)
            assert (
                retry.record_extraction(
                    tenant_id=tenant_id, source_id=SOURCE_ID, model_version=MODEL
                )
                is None
            )
            await repo.save(retry)

            assert (await repo.load(aggregate_id)).version == 1

    async def test_saving_outside_a_tenant_scope_raises(self, event_store, tenant_id):
        """`TenantAwareRepository` is the reason this is a failure and not a
        silently untenanted write."""
        repo = document_repository(event_store)
        document = repo.create_new(uuid4())
        document.record_extraction(tenant_id=tenant_id, source_id=SOURCE_ID, model_version=MODEL)
        with pytest.raises(TenantContextNotSetError):
            await repo.save(document)

    async def test_saving_an_event_for_another_tenant_raises(self, event_store, tenant_id):
        repo = document_repository(event_store)
        document = repo.create_new(uuid4())
        document.record_extraction(tenant_id=uuid4(), source_id=SOURCE_ID, model_version=MODEL)
        async with tenant_scope(tenant_id):
            with pytest.raises(TenantMismatchError):
                await repo.save(document)

    async def test_two_extractions_of_one_document_cannot_both_append(self, event_store, tenant_id):
        """Two workers retrying the same document is the case optimistic
        concurrency exists for. Both loaded at version 0, so the second
        `append` must be refused rather than producing two extraction events
        that each believe they were first."""
        repo = document_repository(event_store)
        aggregate_id = document_stream(tenant_id=tenant_id, source_id=SOURCE_ID).aggregate_id

        async with tenant_scope(tenant_id):
            worker_a = repo.create_new(aggregate_id)
            worker_b = repo.create_new(aggregate_id)
            for worker, model in ((worker_a, MODEL), (worker_b, "ollama/qwen4-30b")):
                worker.record_extraction(
                    tenant_id=tenant_id, source_id=SOURCE_ID, model_version=model
                )

            await repo.save(worker_a)
            with pytest.raises(OptimisticLockError):
                await repo.save(worker_b)

    async def test_two_documents_of_one_tenant_do_not_share_a_stream(self, event_store, tenant_id):
        """The concurrency the per-document stream is for. If they shared one,
        this second save would take the same optimistic lock as the first."""
        repo = document_repository(event_store)
        async with tenant_scope(tenant_id):
            for source_id in ("doc-1", "doc-2"):
                stream = document_stream(tenant_id=tenant_id, source_id=source_id)
                document = repo.create_new(stream.aggregate_id)
                document.record_extraction(
                    tenant_id=tenant_id, source_id=source_id, model_version=MODEL
                )
                await repo.save(document)

            for source_id in ("doc-1", "doc-2"):
                stream = document_stream(tenant_id=tenant_id, source_id=source_id)
                assert (await repo.load(stream.aggregate_id)).version == 1


class TestConsolidationRepository:
    async def test_merges_round_trip_and_rehydrate(self, event_store, tenant_id):
        repo = consolidation_repository(event_store, InMemorySnapshotStore())
        aggregate_id = consolidation_stream(tenant_id=tenant_id).aggregate_id
        canonical, absorbed = uuid4(), uuid4()

        async with tenant_scope(tenant_id):
            log = repo.create_new(aggregate_id)
            log.merge(
                tenant_id=tenant_id,
                canonical_entity_id=canonical,
                merged_entity_ids=[absorbed],
            )
            await repo.save(log)

            reloaded = await repo.load(aggregate_id)

        assert reloaded.state.alias_of == {absorbed: canonical}

    async def test_a_snapshot_restores_the_same_state_a_full_replay_would(
        self, event_store, tenant_id
    ):
        """Snapshots are an optimisation, so the only thing that matters is
        that they are invisible. Threshold 2 over 6 merges guarantees several
        are taken; the assertion is against a replay of the same log with no
        snapshot store at all.
        """
        snapshots = InMemorySnapshotStore()
        repo = consolidation_repository(event_store, snapshots, snapshot_every=2)
        aggregate_id = consolidation_stream(tenant_id=tenant_id).aggregate_id
        canonical = uuid4()
        absorbed = [uuid4() for _ in range(6)]

        async with tenant_scope(tenant_id):
            for entity_id in absorbed:
                log = await repo.load_or_create(aggregate_id)
                log.merge(
                    tenant_id=tenant_id,
                    canonical_entity_id=canonical,
                    merged_entity_ids=[entity_id],
                )
                await repo.save(log)

            from_snapshot = await repo.load(aggregate_id)
            from_scratch = await consolidation_repository(
                event_store, InMemorySnapshotStore()
            ).load(aggregate_id)

        assert await snapshots.snapshot_exists(aggregate_id, CONSOLIDATION_CATEGORY)
        assert from_snapshot.state == from_scratch.state
        assert from_snapshot.version == from_scratch.version

    async def test_one_tenants_log_is_not_another_tenants(self, event_store, tenant_id):
        """The consolidation stream id *is* the tenant id, so this is the test
        that the two are not accidentally the same stream."""
        repo = consolidation_repository(event_store, InMemorySnapshotStore())
        other_tenant = uuid4()

        async with tenant_scope(tenant_id):
            log = repo.create_new(consolidation_stream(tenant_id=tenant_id).aggregate_id)
            log.merge(
                tenant_id=tenant_id,
                canonical_entity_id=uuid4(),
                merged_entity_ids=[uuid4()],
            )
            await repo.save(log)

        async with tenant_scope(other_tenant):
            theirs = await repo.load_or_create(
                consolidation_stream(tenant_id=other_tenant).aggregate_id
            )

        assert theirs.version == 0
        assert theirs.state is None or theirs.state.merges == []

    async def test_a_concurrent_merge_on_one_tenant_is_refused(self, event_store, tenant_id):
        """Serialising merges per tenant is the whole reason the consolidation
        stream is the tenant. Two merges touching the same entity, both loaded
        at the same version, must not both land -- the second would violate
        "no double merge" against state it never saw."""
        repo = consolidation_repository(event_store, InMemorySnapshotStore())
        aggregate_id = consolidation_stream(tenant_id=tenant_id).aggregate_id
        contested = uuid4()

        async with tenant_scope(tenant_id):
            first = await repo.load_or_create(aggregate_id)
            second = await repo.load_or_create(aggregate_id)
            first.merge(
                tenant_id=tenant_id,
                canonical_entity_id=uuid4(),
                merged_entity_ids=[contested],
            )
            second.merge(
                tenant_id=tenant_id,
                canonical_entity_id=uuid4(),
                merged_entity_ids=[contested],
            )

            await repo.save(first)
            with pytest.raises(OptimisticLockError):
                await repo.save(second)


class TestStreamsAreKeyedOnBothHalves:
    async def test_a_document_and_a_consolidation_stream_never_collide(
        self, event_store, tenant_id
    ):
        """A stream key is the pair `(aggregate_id, category)`, and this is the
        case where the components collide: a document whose derived id happens
        to equal a tenant id.

        Contrived by construction -- `uuid5` will not produce a given uuid4 --
        so it is forced here, because the consequence is not contrived at all:
        if the category were dropped from the key, one tenant's whole merge
        history and one document's extractions would share a stream, and each
        aggregate would rehydrate over the other's events.
        """
        colliding_id = consolidation_stream(tenant_id=tenant_id).aggregate_id

        async with tenant_scope(tenant_id):
            log = ConsolidationLog(colliding_id)
            log.merge(
                tenant_id=tenant_id,
                canonical_entity_id=uuid4(),
                merged_entity_ids=[uuid4()],
            )
            await event_store.append(
                consolidation_stream(tenant_id=tenant_id),
                log.uncommitted_events,
                ExpectedVersion.no_stream(),
            )

            document = Document(colliding_id)
            document.record_extraction(
                tenant_id=tenant_id, source_id=SOURCE_ID, model_version=MODEL
            )
            # `no_stream` is the assertion: it fails if the document's events
            # landed on a stream the merge already occupied.
            await event_store.append(
                StreamId(aggregate_id=colliding_id, category=DOCUMENT_CATEGORY),
                document.uncommitted_events,
                ExpectedVersion.no_stream(),
            )

        assert await event_store.get_stream_version(consolidation_stream(tenant_id=tenant_id)) == 1
