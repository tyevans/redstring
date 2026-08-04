"""The in-memory `GraphStore` adapter against the shared compliance suite.

Everything asserted about behaviour lives in `tests.compliance.graph_store`.
This module supplies a store and adds only what is specific to *this*
adapter -- namely that it holds no state outside itself.
"""

from uuid import uuid4

from kg_builder.domain.entity import Entity, ExtractionMethod
from kg_builder.graph.adapters.memory import InMemoryGraphStore
from kg_builder.ports.graph_store import GraphStore
from tests.compliance.graph_store import GraphStoreCompliance


class TestMemoryStore(GraphStoreCompliance):
    async def new_store(self) -> GraphStore:
        return InMemoryGraphStore()


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
