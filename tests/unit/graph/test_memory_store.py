"""The in-memory `GraphStore` adapter against the shared compliance suite.

Everything asserted about behaviour lives in `tests.compliance.graph_store`.
This module supplies a store and adds only what is specific to *this*
adapter -- namely that it holds no state outside itself.
"""

from uuid import uuid4

import pytest

from kg_builder.domain.entity import Entity, ExtractionMethod
from kg_builder.graph.adapters.memory import InMemoryGraphStore
from kg_builder.ports.graph_store import GraphStore
from tests.compliance.graph_store import GraphStoreCompliance


class TestMemoryStore(GraphStoreCompliance):
    async def new_store(self) -> GraphStore:
        return InMemoryGraphStore()


class _DisposeRecorder(GraphStoreCompliance):
    """Not collected -- the name does not match `python_classes = "Test*"`.

    Subclassing here is only to reach `_store`; collecting it would re-run the
    whole compliance suite a second time for no added coverage.
    """

    def __init__(self) -> None:
        self.disposed: list[GraphStore] = []

    async def new_store(self) -> GraphStore:
        return InMemoryGraphStore()

    async def dispose(self, store: GraphStore) -> None:
        self.disposed.append(store)


class TestComplianceHarness:
    """`dispose` must run for every store the suite hands out.

    Slice 4 holds a Neo4j driver per store and the suite builds one per
    hypothesis example, so a missed `dispose` leaks a connection per example.
    That the hook fires is a property of the suite, not of any adapter.
    """

    async def test_dispose_runs_when_the_body_completes(self):
        harness = _DisposeRecorder()
        async with harness._store() as store:
            assert harness.disposed == []
        assert harness.disposed == [store]

    async def test_dispose_runs_even_when_the_body_raises(self):
        harness = _DisposeRecorder()
        with pytest.raises(RuntimeError, match="boom"):
            async with harness._store() as store:
                raise RuntimeError("boom")
        assert harness.disposed == [store]

    async def test_dispose_defaults_to_a_no_op(self):
        """An in-memory adapter supplies only `new_store` and still works."""
        plain = TestMemoryStore()
        async with plain._store() as store:
            assert isinstance(store, InMemoryGraphStore)

    def test_max_examples_is_tunable_without_editing_the_suite(self):
        from tests.compliance import graph_store as suite

        assert suite.compliance_settings.max_examples == suite.DEFAULT_MAX_EXAMPLES


class TestMemoryStoreSpecifics:
    async def test_two_stores_share_nothing(self):
        one, two = InMemoryGraphStore(), InMemoryGraphStore()
        tenant = uuid4()
        entity = Entity(
            id=uuid4(),
            tenant_id=tenant,
            name="Ada",
            normalized_name="ada",
            entity_type="person",
            extraction_method=ExtractionMethod.MANUAL,
            confidence=1.0,
        )
        await one.upsert_entity(entity)

        assert await two.get_entity(entity.id, tenant) is None
        assert await two.find_entities(tenant) == []
