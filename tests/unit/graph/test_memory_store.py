"""The in-memory `GraphStore` adapter against the shared compliance suite.

Everything asserted about behaviour lives in `tests.compliance.graph_store`.
This module supplies a store and adds only what is specific to *this*
adapter -- namely that it holds no state outside itself.
"""

import itertools
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from redstring.domain.alias import Alias
from redstring.domain.entity import Entity, ExtractionMethod
from redstring.domain.exceptions import AliasCycleError
from redstring.graph.adapters.memory import InMemoryGraphStore
from redstring.ports.graph_store import GraphStore
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


class TestAliasResolutionIsBounded:
    """The walk terminates on data no legal history can produce.

    A cycle needs some merge to name an entity that is *already* an alias as
    its canonical, and `ConsolidationLog` refuses exactly that -- so this state
    is unreachable through the write model and is built here by writing to the
    store directly.

    Tested anyway, and this is the reason: resolution is a walk over
    adapter-supplied data, and the alternative to bounding it is a hang. A test
    that hangs is worse than one that fails, because in CI it reads as
    infrastructure trouble and gets retried rather than investigated.
    """

    @staticmethod
    def _alias(tenant, *, canonical, alias):
        return Alias(
            id=uuid4(),
            tenant_id=tenant,
            canonical_entity_id=canonical,
            alias_entity_id=alias,
            merged_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

    async def test_a_two_cycle_raises_rather_than_hanging(self):
        store = InMemoryGraphStore()
        tenant, a, b = uuid4(), uuid4(), uuid4()
        await store.upsert_alias(self._alias(tenant, canonical=b, alias=a))
        await store.upsert_alias(self._alias(tenant, canonical=a, alias=b))

        with pytest.raises(AliasCycleError) as raised:
            await store.resolve_entity_ids([a], tenant)

        assert raised.value.entity_id == a
        assert raised.value.tenant_id == tenant

    async def test_a_three_cycle_raises_too(self):
        """Separate from the two-cycle because a self-referential check
        (`canonical == wanted`) would catch that one and miss this one."""
        store = InMemoryGraphStore()
        tenant, a, b, c = uuid4(), uuid4(), uuid4(), uuid4()
        await store.upsert_alias(self._alias(tenant, canonical=b, alias=a))
        await store.upsert_alias(self._alias(tenant, canonical=c, alias=b))
        await store.upsert_alias(self._alias(tenant, canonical=a, alias=c))

        with pytest.raises(AliasCycleError):
            await store.resolve_entity_ids([b], tenant)

    async def test_the_longest_legal_chain_still_resolves(self):
        """The bound must not be off by one.

        A chain using *every* alias in the tenant is the worst legal case, and
        a limit of `len(aliases)` rather than `len(aliases) + 1` would reject
        it -- turning the longest correct history into a spurious error.
        """
        store = InMemoryGraphStore()
        tenant = uuid4()
        chain = [uuid4() for _ in range(6)]
        for alias, canonical in itertools.pairwise(chain):
            await store.upsert_alias(self._alias(tenant, canonical=canonical, alias=alias))

        assert await store.resolve_entity_ids([chain[0]], tenant) == {chain[0]: chain[-1]}


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
