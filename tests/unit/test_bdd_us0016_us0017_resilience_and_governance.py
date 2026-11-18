"""Executable BDD Acceptance Criteria for US-0016 and US-0017: Resilience & Governance.

Governed by:
- ADR-0126 (Chunk Store and Cache Are Capabilities Too)
- ADR-0128 (A Capability Declares Its Own Release)
- ADR-0135 (Provenance Is a Value Object)
- ADR-0136 (A Merge Resolves the Canonical Entity's Fields)
- ADR-0143 (A Query Is Embedded Differently From a Document)
- ADR-0002 (File Length Limit)
- ADR-0003 (Blackbox Frontdoor Verification)
- ADR-0006 (Executable BDD User Stories)
- PRD-0006
- US-0016, US-0017
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from eventsource.adapters.memory import InMemoryEventStore

from redstring import (
    Consolidator,
    Entity,
    ExtractionMethod,
    GraphProjection,
    InMemoryChunkStore,
    InMemoryGraphStore,
    InMemoryVectorStore,
    MergeUndone,
    Provenance,
    Relationship,
    SourceDocument,
    SourceId,
    TenantId,
    VectorProvenanceMismatchError,
    index_documents,
)
from redstring.llm.cache.memory import MemoryCache
from redstring.llm.circuit_breaker import CircuitBreaker, CircuitState
from redstring.llm.rate_limiter import RateLimiter, RateLimitExceeded
from redstring.llm.retry import LlmRetryPolicy, RetryExhausted, with_retry
from redstring.projections.rebuild import rebuild_tenant


def _make_entity(
    tenant_id: TenantId,
    name: str,
    entity_type: str = "Person",
    source_id: str = "doc-gov",
) -> Entity:
    return Entity(
        id=uuid4(),
        tenant_id=tenant_id,
        name=name,
        normalized_name=name.lower(),
        entity_type=entity_type,
        provenance=Provenance(
            observed_at=datetime(2026, 1, 1, tzinfo=UTC),
            extraction_method=ExtractionMethod.LLM,
            confidence=0.95,
            source_id=SourceId(source_id),
            model_version="model-v1",
        ),
    )


@pytest.mark.asyncio
async def test_scenario_enforce_per_tenant_sliding_window_rate_limits() -> None:
    """Scenario: Enforce per-tenant sliding-window rate limits (US-0016)."""
    tenant_a = uuid4()
    tenant_b = uuid4()
    limiter = RateLimiter(rpm=2, window_seconds=60.0)

    await limiter.acquire(tenant_a)
    await limiter.acquire(tenant_a)

    with pytest.raises(RateLimitExceeded) as exc_info:
        await limiter.acquire(tenant_a)
    assert exc_info.value.retry_after > 0.0

    # Tenant B is completely unaffected
    await limiter.acquire(tenant_b)


@pytest.mark.asyncio
async def test_scenario_trip_circuit_breaker_and_recover_through_half_open() -> None:
    """Scenario: Trip circuit breaker on consecutive failures and recover (US-0016)."""
    breaker = CircuitBreaker(
        failure_threshold=2,
        recovery_timeout=0.05,
        half_open_max_calls=1,
    )
    assert await breaker.state() == CircuitState.CLOSED
    assert await breaker.allow_request() is True

    await breaker.record_failure()
    await breaker.record_failure()
    assert await breaker.state() == CircuitState.OPEN
    assert await breaker.allow_request() is False

    # Wait for recovery_timeout to elapse and probe via HALF_OPEN
    await asyncio.sleep(0.08)
    assert await breaker.allow_request() is True
    assert await breaker.state() == CircuitState.HALF_OPEN

    await breaker.record_success()
    assert await breaker.state() == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_scenario_retry_transient_transport_failures_and_chain_cause() -> None:
    """Scenario: Retry transient transport failures with exponential backoff (US-0016)."""
    attempts = 0
    fast_policy = LlmRetryPolicy(max_retries=2, initial_delay=0.005, max_delay=0.02, jitter=0.0)

    @with_retry(retryable_exceptions=(ConnectionError,), policy=fast_policy)
    async def flaky_call() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("Temporary reset")
        return "recovered"

    assert await flaky_call() == "recovered"
    assert attempts == 3

    @with_retry(retryable_exceptions=(ConnectionError,), policy=fast_policy)
    async def always_failing_call() -> str:
        raise ConnectionError("Permanent network outage")

    with pytest.raises(RetryExhausted) as exc_info:
        await always_failing_call()
    assert isinstance(exc_info.value.__cause__, ConnectionError)


@pytest.mark.asyncio
async def test_scenario_preserve_caller_owned_cache_lifecycle() -> None:
    """Scenario: Preserve caller-owned cache lifecycle when shared (US-0016)."""
    shared_cache = MemoryCache()
    tenant_id = uuid4()

    async with RateLimiter(rpm=5, cache=shared_cache) as limiter:
        await limiter.acquire(tenant_id)

    # Exiting RateLimiter block must not close caller-owned shared_cache
    breaker = CircuitBreaker(failure_threshold=3, cache=shared_cache)
    assert await breaker.allow_request() is True
    await breaker.record_success()
    assert await breaker.state() == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_scenario_undo_entity_merge_and_restore_absorbed_entities() -> None:
    """Scenario: Undo an entity merge and restore absorbed entities and relationships (US-0017)."""
    tenant_id = TenantId(uuid4())
    graph_store = InMemoryGraphStore()

    canonical = _make_entity(tenant_id, "Ada Lovelace")
    duplicate = _make_entity(tenant_id, "Augusta Ada King")
    babbage = _make_entity(tenant_id, "Charles Babbage")

    for e in (canonical, duplicate, babbage):
        await graph_store.upsert_entity(e)

    rel = Relationship(
        id=uuid4(),
        tenant_id=tenant_id,
        source_entity_id=duplicate.id,
        target_entity_id=babbage.id,
        relationship_type="CORRESPONDED_WITH",
        source_id=SourceId("doc-gov"),
        confidence=0.95,
    )
    await graph_store.upsert_relationship(rel)

    consolidator = Consolidator(graph_store)
    merge_report = await consolidator.merge(
        tenant_id=tenant_id,
        canonical_entity_id=canonical.id,
        merged_entity_ids=[duplicate.id],
        merge_reason="Confirmed historical alias",
    )
    assert merge_report.affected_entity_ids == (duplicate.id,)

    # Undo the merge using only its event_id
    undo_report = await consolidator.undo(
        tenant_id=tenant_id,
        merge_event_id=merge_report.event.event_id,
    )
    assert isinstance(undo_report.event, MergeUndone)
    assert undo_report.affected_entity_ids == (duplicate.id,)

    restored = await graph_store.get_entity(duplicate.id, tenant_id)
    assert restored is not None
    assert restored.name == "Augusta Ada King"
    restored_rels = await graph_store.get_relationships(duplicate.id, tenant_id)
    assert len(restored_rels) == 1
    assert restored_rels[0].target_entity_id == babbage.id


@pytest.mark.asyncio
async def test_scenario_purge_single_tenant_across_stores_without_cross_tenant_impact() -> None:
    """Scenario: Purge a single tenant across graph, vector, and chunk stores (US-0017)."""
    tenant_a = TenantId(uuid4())
    tenant_b = TenantId(uuid4())

    graph_store = InMemoryGraphStore()
    vector_store = InMemoryVectorStore(dimension=4)
    chunk_store = InMemoryChunkStore(dimension=4)

    entity_a = _make_entity(tenant_a, "Tenant A Entity")
    entity_b = _make_entity(tenant_b, "Tenant B Entity")
    await graph_store.upsert_entity(entity_a)
    await graph_store.upsert_entity(entity_b)

    await vector_store.upsert(entity_a.id, (1.0, 0.0, 0.0, 0.0), tenant_a)
    await vector_store.upsert(entity_b.id, (0.0, 1.0, 0.0, 0.0), tenant_b)

    await index_documents(
        [SourceDocument(id=SourceId("doc-a"), text="Tenant A chunk")],
        store=chunk_store,
        tenant_id=tenant_a,
    )
    await index_documents(
        [SourceDocument(id=SourceId("doc-b"), text="Tenant B chunk")],
        store=chunk_store,
        tenant_id=tenant_b,
    )

    # Purge Tenant A across all three stores via delete_by_tenant
    await graph_store.delete_by_tenant(tenant_a)
    await vector_store.delete_by_tenant(tenant_a)
    await chunk_store.delete_by_tenant(tenant_a)

    assert await graph_store.find_entities(tenant_a) == []
    assert await vector_store.search((1.0, 0.0, 0.0, 0.0), tenant_a, k=5) == []
    assert await chunk_store.get_by_source(SourceId("doc-a"), tenant_a) == []

    # Tenant B retains 100% of its data
    assert len(await graph_store.find_entities(tenant_b)) == 1
    assert len(await vector_store.search((0.0, 1.0, 0.0, 0.0), tenant_b, k=5)) == 1
    assert len(await chunk_store.get_by_source(SourceId("doc-b"), tenant_b)) == 1


@pytest.mark.asyncio
async def test_scenario_rebuild_single_tenant_across_projections_from_feed() -> None:
    """Scenario: Rebuild a single tenant across read projections from the event feed (US-0017)."""
    from redstring import FakeLlmProvider, build_graph

    tenant_a = TenantId(uuid4())
    tenant_b = TenantId(uuid4())
    event_store = InMemoryEventStore()
    graph_store = InMemoryGraphStore()

    provider = FakeLlmProvider(
        by_substring={},
        default={
            "entities": [{"name": "Rebuilt Entity A", "entity_type": "Person"}],
            "relationships": [],
        },
    )
    await build_graph(
        SourceDocument(id=SourceId("doc-rebuild-a"), text="Rebuilt Entity A text."),
        provider=provider,
        store=graph_store,
        tenant_id=tenant_a,
        event_store=event_store,
    )

    entity_b = _make_entity(tenant_b, "Untouched Entity B", source_id="doc-rebuild-b")
    await graph_store.upsert_entity(entity_b)

    # Wipe tenant_a in graph_store and rebuild from event_store feed
    await graph_store.delete_by_tenant(tenant_a)
    assert await graph_store.find_entities(tenant_a) == []

    await rebuild_tenant(
        event_store,
        GraphProjection(graph_store),
        tenant_id=tenant_a,
    )

    rebuilt_a = await graph_store.find_entities(tenant_a)
    assert len(rebuilt_a) == 1
    assert rebuilt_a[0].name == "Rebuilt Entity A"

    remaining_b = await graph_store.find_entities(tenant_b)
    assert len(remaining_b) == 1
    assert remaining_b[0].name == "Untouched Entity B"


@pytest.mark.asyncio
async def test_scenario_reject_vector_embeddings_with_mismatched_provenance() -> None:
    """Scenario: Reject vector embeddings with mismatched provenance (US-0017)."""
    vector_store = InMemoryVectorStore(
        dimension=4, model="embed-v1", document_prefix="search_doc: "
    )

    with pytest.raises(VectorProvenanceMismatchError):
        vector_store.ensure_schema(model="embed-v2", document_prefix="search_doc: ")
