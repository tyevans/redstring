"""Shared compliance suite for the `GraphStore` port.

**Every `GraphStore` adapter must pass this suite unchanged.** It is the
executable definition of the port; the prose in
`kg_builder.ports.graph_store` describes what these tests enforce.

## Consistency contract

Adapters are required to be **read-your-writes**: once an `upsert_*` call has
returned, its effect is visible to the next read issued on the same store.
There is no "eventually" inside a store.

When these stores are later fed by projection handlers, lag exists *between
the event log and the store* -- an event appended a moment ago may not yet be
projected. That is the projection's lag, not the store's, and slice 5b should
not read any weaker guarantee into this port.

## How an adapter opts in

Subclass and supply `new_store`::

    from tests.compliance.graph_store import GraphStoreCompliance

    class TestMemoryStore(GraphStoreCompliance):
        async def new_store(self) -> GraphStore:
            return InMemoryGraphStore()

`new_store` must return an **empty** store, and each call must return one
isolated from every other. The property tests call it once per generated
example, because hypothesis reuses the surrounding fixture across examples
and a shared store would let state from example *n* decide example *n+1*.

A `store` fixture is provided by this class in terms of `new_store` for the
example-based tests, so adapters supply exactly one thing.

An adapter that needs real infrastructure (Neo4j) implements `new_store` by
wiping and handing back its test database.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from kg_builder.domain.entity import Entity, ExtractionMethod
from kg_builder.domain.exceptions import MissingEntityError
from kg_builder.domain.relationship import Relationship
from kg_builder.ports.graph_store import GraphStore
from tests.compliance import strategies as gen

# Store construction dominates the per-example cost for real backends, so the
# deadline is off; a slow adapter is a performance finding, not a flaky test.
compliance_settings = settings(
    deadline=None,
    max_examples=50,
    suppress_health_check=[HealthCheck.too_slow],
)


def _mutate(entity: Entity) -> None:
    """Mutate `entity` in place, reaching into nested containers.

    A shallow copy survives a top-level mutation and fails here, which is the
    point: nested reachability is where naive in-memory adapters leak.
    """
    entity.name = entity.name + "-tampered"
    entity.properties["__tampered__"] = True
    entity.external_ids["__tampered__"] = "yes"
    for value in entity.properties.values():
        if isinstance(value, dict):
            value["__nested_tamper__"] = True
        elif isinstance(value, list):
            value.append("__nested_tamper__")


class GraphStoreCompliance:
    """Tests every `GraphStore` implementation must pass."""

    async def new_store(self) -> GraphStore:
        """Return a fresh, empty store. Adapters must override this."""
        raise NotImplementedError

    @pytest.fixture
    async def store(self) -> GraphStore:
        return await self.new_store()

    # ------------------------------------------------------------------
    # The port itself
    # ------------------------------------------------------------------

    async def test_satisfies_the_graph_store_protocol(self, store: GraphStore) -> None:
        assert isinstance(store, GraphStore)

    # ------------------------------------------------------------------
    # Property 1 -- round-trip
    # ------------------------------------------------------------------

    @compliance_settings
    @given(entity=gen.entities())
    async def test_upsert_then_get_round_trips(self, entity: Entity) -> None:
        store = await self.new_store()
        await store.upsert_entity(entity)
        assert await store.get_entity(entity.id, entity.tenant_id) == entity

    @compliance_settings
    @given(entity=gen.entities())
    async def test_upsert_entities_round_trips(self, entity: Entity) -> None:
        store = await self.new_store()
        await store.upsert_entities([entity])
        assert await store.get_entity(entity.id, entity.tenant_id) == entity

    # ------------------------------------------------------------------
    # Property 2 -- idempotency
    # ------------------------------------------------------------------

    @compliance_settings
    @given(entity=gen.entities())
    async def test_upserting_twice_is_indistinguishable_from_once(self, entity: Entity) -> None:
        once = await self.new_store()
        await once.upsert_entity(entity)

        twice = await self.new_store()
        await twice.upsert_entity(entity)
        await twice.upsert_entity(entity)

        assert await twice.find_entities(entity.tenant_id) == await once.find_entities(
            entity.tenant_id
        )
        assert len(await twice.find_entities(entity.tenant_id)) == 1

    @compliance_settings
    @given(data=st.data())
    async def test_upserting_a_relationship_twice_leaves_one_edge(
        self, data: st.DataObject
    ) -> None:
        store = await self.new_store()
        tenant = data.draw(st.uuids())
        source, target = await self._two_entities(store, tenant, data)
        relationship = data.draw(
            gen.relationships(
                tenant_id=tenant, source_entity_id=source.id, target_entity_id=target.id
            )
        )

        await store.upsert_relationship(relationship)
        after_once = await store.neighbors(source.id, tenant)
        await store.upsert_relationship(relationship)

        assert await store.neighbors(source.id, tenant) == after_once
        assert after_once == [target]

    async def test_relationship_upsert_replaces_by_id(self, store: GraphStore) -> None:
        """A re-upserted relationship id replaces the edge; it does not add one.

        `neighbors` deduplicates by entity, so through traversal alone a store
        that appends duplicate edges looks identical to a correct one until the
        second write differs from the first. Changing both endpoint and type
        makes the duplicate observable in the traversal path specifically --
        `test_re_upserting_a_relationship_yields_exactly_one_row` asserts the
        same contract directly via `get_relationships`, and both are kept
        because they exercise different code.
        """
        tenant = uuid4()
        a, b, c = (_example_entity(tenant=tenant) for _ in range(3))
        await store.upsert_entities([a, b, c])
        edge_id = uuid4()

        await store.upsert_relationship(
            _example_relationship(tenant, source=a.id, target=b.id, kind="knows", edge_id=edge_id)
        )
        await store.upsert_relationship(
            _example_relationship(
                tenant, source=a.id, target=c.id, kind="works_at", edge_id=edge_id
            )
        )

        assert [e.id for e in await store.neighbors(a.id, tenant, depth=5)] == [c.id]
        assert await store.neighbors(a.id, tenant, relationship_types=["knows"]) == []

    # ------------------------------------------------------------------
    # Property 3 -- last write wins
    # ------------------------------------------------------------------

    @compliance_settings
    @given(data=st.data())
    async def test_last_write_wins(self, data: st.DataObject) -> None:
        store = await self.new_store()
        tenant = data.draw(st.uuids())
        entity_id = data.draw(st.uuids())
        first = data.draw(gen.entities(tenant_id=tenant, entity_id=entity_id))
        second = data.draw(gen.entities(tenant_id=tenant, entity_id=entity_id))

        await store.upsert_entity(first)
        await store.upsert_entity(second)

        assert await store.get_entity(entity_id, tenant) == second
        assert len(await store.find_entities(tenant)) == 1

    # ------------------------------------------------------------------
    # Property 4 -- tenant isolation (the one that matters most)
    # ------------------------------------------------------------------

    @compliance_settings
    @given(tenants=gen.distinct_tenant_pairs, data=st.data())
    async def test_no_read_under_one_tenant_ever_sees_another(
        self, tenants: tuple[UUID, UUID], data: st.DataObject
    ) -> None:
        tenant_a, tenant_b = tenants
        store = await self.new_store()

        a_source, a_target = await self._two_entities(store, tenant_a, data)
        await store.upsert_relationship(
            data.draw(
                gen.relationships(
                    tenant_id=tenant_a,
                    source_entity_id=a_source.id,
                    target_entity_id=a_target.id,
                )
            )
        )
        # Tenant B is populated too: an isolation test against an empty tenant
        # would pass on a store that simply lost the writes.
        b_entity = data.draw(gen.entities(tenant_id=tenant_b))
        await store.upsert_entity(b_entity)

        # Every read under B is asserted against the exact expected answer,
        # not merely "does not contain A". Generated ids may collide across
        # tenants, and the collision is the interesting case: the store must
        # answer with B's value, never A's.
        b_only = [b_entity]
        for entity in (a_source, a_target):
            expected = b_entity if entity.id == b_entity.id else None
            assert await store.get_entity(entity.id, tenant_b) == expected
            assert await store.neighbors(entity.id, tenant_b) == []

        assert await store.find_entities(tenant_b) == b_only

        for key in (a_source.blocking_keys or frozenset()) | (
            a_target.blocking_keys or frozenset()
        ):
            found = await store.find_by_blocking_key(key, tenant_b)
            assert found == (b_only if key in (b_entity.blocking_keys or frozenset()) else [])

        by_name = await store.find_entities(tenant_b, name=a_source.normalized_name)
        assert by_name == (b_only if b_entity.normalized_name == a_source.normalized_name else [])

        by_type = await store.find_entities(tenant_b, entity_type=a_source.entity_type)
        assert by_type == (b_only if b_entity.entity_type == a_source.entity_type else [])

    @compliance_settings
    @given(tenants=gen.distinct_tenant_pairs, data=st.data())
    async def test_relationships_do_not_cross_tenants(
        self, tenants: tuple[UUID, UUID], data: st.DataObject
    ) -> None:
        tenant_a, tenant_b = tenants
        store = await self.new_store()
        a_source, a_target = await self._two_entities(store, tenant_a, data)
        # The same ids under tenant B, unrelated by any edge.
        b_source = data.draw(gen.entities(tenant_id=tenant_b, entity_id=a_source.id))
        b_target = data.draw(gen.entities(tenant_id=tenant_b, entity_id=a_target.id))
        await store.upsert_entities([b_source, b_target])
        await store.upsert_relationship(
            data.draw(
                gen.relationships(
                    tenant_id=tenant_a,
                    source_entity_id=a_source.id,
                    target_entity_id=a_target.id,
                )
            )
        )

        assert await store.neighbors(a_source.id, tenant_a) == [a_target]
        assert await store.neighbors(b_source.id, tenant_b) == []

    # ------------------------------------------------------------------
    # Property 5 -- delete_by_tenant is exact
    # ------------------------------------------------------------------

    @compliance_settings
    @given(tenants=gen.distinct_tenant_pairs, data=st.data())
    async def test_delete_by_tenant_removes_exactly_that_tenant(
        self, tenants: tuple[UUID, UUID], data: st.DataObject
    ) -> None:
        doomed, spared = tenants
        store = await self.new_store()

        doomed_source, doomed_target = await self._two_entities(store, doomed, data)
        await store.upsert_relationship(
            data.draw(
                gen.relationships(
                    tenant_id=doomed,
                    source_entity_id=doomed_source.id,
                    target_entity_id=doomed_target.id,
                )
            )
        )
        spared_source, spared_target = await self._two_entities(store, spared, data)
        await store.upsert_relationship(
            data.draw(
                gen.relationships(
                    tenant_id=spared,
                    source_entity_id=spared_source.id,
                    target_entity_id=spared_target.id,
                )
            )
        )

        before = await store.find_entities(doomed)
        spared_before = await store.find_entities(spared)

        removed = await store.delete_by_tenant(doomed)

        assert removed == len(before)
        assert await store.find_entities(doomed) == []
        assert await store.get_entity(doomed_source.id, doomed) is None
        assert await store.neighbors(doomed_source.id, doomed) == []

        # Re-adding the entities must not resurrect the deleted edges. Without
        # this step a store that drops entities but keeps relationships passes
        # the traversal path, because `neighbors` on a missing entity returns
        # [] either way. `test_delete_by_tenant_leaves_no_orphan_relationships`
        # asserts the same thing directly.
        await store.upsert_entities([doomed_source, doomed_target])
        assert await store.neighbors(doomed_source.id, doomed, depth=5) == []
        assert sorted(await store.find_entities(spared), key=lambda e: str(e.id)) == sorted(
            spared_before, key=lambda e: str(e.id)
        )
        assert await store.neighbors(spared_source.id, spared) == [spared_target]

    @compliance_settings
    @given(tenant=st.uuids())
    async def test_delete_by_tenant_on_an_unknown_tenant_removes_nothing(
        self, tenant: UUID
    ) -> None:
        store = await self.new_store()
        assert await store.delete_by_tenant(tenant) == 0

    # ------------------------------------------------------------------
    # Property 6 -- neighbour depth monotonicity
    # ------------------------------------------------------------------

    @compliance_settings
    @given(data=st.data())
    async def test_neighbours_at_depth_n_are_a_subset_of_depth_n_plus_one(
        self, data: st.DataObject
    ) -> None:
        store = await self.new_store()
        tenant = data.draw(st.uuids())
        chain = await self._connected_graph(store, tenant, data)
        origin = chain[0]

        for depth in range(4):
            shallow = {e.id for e in await store.neighbors(origin.id, tenant, depth=depth)}
            deeper = {e.id for e in await store.neighbors(origin.id, tenant, depth=depth + 1)}
            assert shallow <= deeper

    @compliance_settings
    @given(data=st.data())
    async def test_neighbours_at_depth_zero_is_empty(self, data: st.DataObject) -> None:
        store = await self.new_store()
        tenant = data.draw(st.uuids())
        chain = await self._connected_graph(store, tenant, data)
        assert await store.neighbors(chain[0].id, tenant, depth=0) == []

    # ------------------------------------------------------------------
    # Property 7 -- mutation isolation
    # ------------------------------------------------------------------

    @compliance_settings
    @given(entity=gen.entities())
    async def test_mutating_a_read_result_does_not_change_the_store(self, entity: Entity) -> None:
        store = await self.new_store()
        await store.upsert_entity(entity)
        pristine = entity.model_copy(deep=True)

        first = await store.get_entity(entity.id, entity.tenant_id)
        assert first is not None
        _mutate(first)

        assert await store.get_entity(entity.id, entity.tenant_id) == pristine
        assert await store.find_entities(entity.tenant_id) == [pristine]

    @compliance_settings
    @given(entity=gen.entities())
    async def test_mutating_the_argument_after_a_write_does_not_change_the_store(
        self, entity: Entity
    ) -> None:
        store = await self.new_store()
        pristine = entity.model_copy(deep=True)
        await store.upsert_entity(entity)

        _mutate(entity)

        assert await store.get_entity(pristine.id, pristine.tenant_id) == pristine

    @compliance_settings
    @given(entity=gen.entities())
    async def test_mutating_a_find_result_does_not_change_the_store(self, entity: Entity) -> None:
        store = await self.new_store()
        await store.upsert_entity(entity)
        pristine = entity.model_copy(deep=True)

        for found in await store.find_entities(entity.tenant_id):
            _mutate(found)

        assert await store.get_entity(entity.id, entity.tenant_id) == pristine

    async def test_mutating_a_blocking_key_result_does_not_change_the_store(
        self, store: GraphStore
    ) -> None:
        """Isolation is required of *every* read path, not just the common two."""
        tenant = uuid4()
        entity = _example_entity(
            tenant=tenant, blocking_keys=frozenset({"A430"}), properties={"nested": {"k": "v"}}
        )
        await store.upsert_entity(entity)
        pristine = entity.model_copy(deep=True)

        for found in await store.find_by_blocking_key("A430", tenant):
            _mutate(found)

        assert await store.find_by_blocking_key("A430", tenant) == [pristine]
        assert await store.get_entity(entity.id, tenant) == pristine

    async def test_mutating_a_neighbours_result_does_not_change_the_store(
        self, store: GraphStore
    ) -> None:
        tenant = uuid4()
        origin = _example_entity(tenant=tenant)
        neighbour = _example_entity(tenant=tenant, properties={"nested": {"k": "v"}})
        await store.upsert_entities([origin, neighbour])
        await store.upsert_relationship(
            _example_relationship(tenant, source=origin.id, target=neighbour.id)
        )
        pristine = neighbour.model_copy(deep=True)

        for found in await store.neighbors(origin.id, tenant):
            _mutate(found)

        assert await store.neighbors(origin.id, tenant) == [pristine]
        assert await store.get_entity(neighbour.id, tenant) == pristine

    # ------------------------------------------------------------------
    # Error semantics
    # ------------------------------------------------------------------

    async def test_get_entity_returns_none_for_an_unknown_id(self, store: GraphStore) -> None:
        assert await store.get_entity(uuid4(), uuid4()) is None

    async def test_dangling_source_raises_missing_entity_error(self, store: GraphStore) -> None:
        tenant = uuid4()
        target = _example_entity(tenant=tenant)
        await store.upsert_entity(target)
        missing = uuid4()

        with pytest.raises(MissingEntityError) as raised:
            await store.upsert_relationship(
                _example_relationship(tenant, source=missing, target=target.id)
            )
        assert raised.value.entity_id == missing
        assert raised.value.tenant_id == tenant

    async def test_dangling_target_raises_missing_entity_error(self, store: GraphStore) -> None:
        tenant = uuid4()
        source = _example_entity(tenant=tenant)
        await store.upsert_entity(source)
        missing = uuid4()

        with pytest.raises(MissingEntityError) as raised:
            await store.upsert_relationship(
                _example_relationship(tenant, source=source.id, target=missing)
            )
        assert raised.value.entity_id == missing

    async def test_endpoint_in_another_tenant_is_still_dangling(self, store: GraphStore) -> None:
        tenant, other = uuid4(), uuid4()
        source = _example_entity(tenant=tenant)
        target = _example_entity(tenant=other)
        await store.upsert_entities([source, target])

        with pytest.raises(MissingEntityError):
            await store.upsert_relationship(
                _example_relationship(tenant, source=source.id, target=target.id)
            )

    async def test_a_rejected_relationship_leaves_no_trace(self, store: GraphStore) -> None:
        tenant = uuid4()
        source = _example_entity(tenant=tenant)
        await store.upsert_entity(source)

        with pytest.raises(MissingEntityError):
            await store.upsert_relationship(
                _example_relationship(tenant, source=source.id, target=uuid4())
            )
        assert await store.neighbors(source.id, tenant) == []

    async def test_upsert_relationships_rejects_a_dangling_element(self, store: GraphStore) -> None:
        tenant = uuid4()
        a, b = _example_entity(tenant=tenant), _example_entity(tenant=tenant)
        await store.upsert_entities([a, b])

        with pytest.raises(MissingEntityError):
            await store.upsert_relationships(
                [
                    _example_relationship(tenant, source=a.id, target=b.id),
                    _example_relationship(tenant, source=a.id, target=uuid4()),
                ]
            )

    async def test_negative_depth_is_rejected(self, store: GraphStore) -> None:
        with pytest.raises(ValueError, match="depth"):
            await store.neighbors(uuid4(), uuid4(), depth=-1)

    async def test_negative_limit_is_rejected(self, store: GraphStore) -> None:
        with pytest.raises(ValueError, match="limit"):
            await store.find_entities(uuid4(), limit=-1)

    async def test_neighbours_of_an_unknown_entity_is_empty(self, store: GraphStore) -> None:
        assert await store.neighbors(uuid4(), uuid4()) == []

    # ------------------------------------------------------------------
    # get_relationships
    #
    # The edge read path. Before it existed, relationship state was only
    # observable through `neighbors`, which returns entities -- so a stored
    # relationship's type, confidence and properties were unverifiable, and
    # two real defects (orphaned edges after a tenant delete, duplicate rows
    # on re-upsert) could only be caught by indirect inference.
    # ------------------------------------------------------------------

    @compliance_settings
    @given(data=st.data())
    async def test_relationship_round_trips(self, data: st.DataObject) -> None:
        store = await self.new_store()
        tenant = data.draw(st.uuids())
        source, target = await self._two_entities(store, tenant, data)
        relationship = data.draw(
            gen.relationships(
                tenant_id=tenant, source_entity_id=source.id, target_entity_id=target.id
            )
        )

        await store.upsert_relationship(relationship)

        assert await store.get_relationships(source.id, tenant) == [relationship]
        assert await store.get_relationships(target.id, tenant) == [relationship]

    @compliance_settings
    @given(tenants=gen.distinct_tenant_pairs, data=st.data())
    async def test_relationships_are_never_readable_from_another_tenant(
        self, tenants: tuple[UUID, UUID], data: st.DataObject
    ) -> None:
        tenant_a, tenant_b = tenants
        store = await self.new_store()
        a_source, a_target = await self._two_entities(store, tenant_a, data)
        await store.upsert_relationship(
            data.draw(
                gen.relationships(
                    tenant_id=tenant_a,
                    source_entity_id=a_source.id,
                    target_entity_id=a_target.id,
                )
            )
        )
        # Same entity ids under tenant B, with no edge between them.
        await store.upsert_entities(
            [
                data.draw(gen.entities(tenant_id=tenant_b, entity_id=a_source.id)),
                data.draw(gen.entities(tenant_id=tenant_b, entity_id=a_target.id)),
            ]
        )

        for entity_id in (a_source.id, a_target.id):
            assert await store.get_relationships(entity_id, tenant_b) == []
            assert len(await store.get_relationships(entity_id, tenant_a)) == 1

    @compliance_settings
    @given(data=st.data())
    async def test_mutating_a_relationship_result_does_not_change_the_store(
        self, data: st.DataObject
    ) -> None:
        """The gap that closed BACKLOG B32.

        With no edge read path, an adapter storing a shallow copy of a
        relationship was unobservable through the port -- a surviving mutant
        that no test could kill.
        """
        store = await self.new_store()
        tenant = data.draw(st.uuids())
        source, target = await self._two_entities(store, tenant, data)
        relationship = data.draw(
            gen.relationships(
                tenant_id=tenant, source_entity_id=source.id, target_entity_id=target.id
            )
        )
        await store.upsert_relationship(relationship)
        pristine = relationship.model_copy(deep=True)

        for found in await store.get_relationships(source.id, tenant):
            found.relationship_type += "-tampered"
            found.properties["__tampered__"] = True
            for value in found.properties.values():
                if isinstance(value, dict):
                    value["__nested_tamper__"] = True
                elif isinstance(value, list):
                    value.append("__nested_tamper__")

        assert await store.get_relationships(source.id, tenant) == [pristine]

    @compliance_settings
    @given(data=st.data())
    async def test_mutating_the_relationship_argument_after_a_write_is_ignored(
        self, data: st.DataObject
    ) -> None:
        store = await self.new_store()
        tenant = data.draw(st.uuids())
        source, target = await self._two_entities(store, tenant, data)
        relationship = data.draw(
            gen.relationships(
                tenant_id=tenant, source_entity_id=source.id, target_entity_id=target.id
            )
        )
        pristine = relationship.model_copy(deep=True)
        await store.upsert_relationship(relationship)

        relationship.relationship_type += "-tampered"
        relationship.properties["__tampered__"] = True

        assert await store.get_relationships(source.id, tenant) == [pristine]

    async def test_get_relationships_filters_by_direction(self, store: GraphStore) -> None:
        """Direction must be decided by identity of the endpoint, not by
        ordering of it.

        The ids are pinned so that one neighbour sorts *below* the hub and one
        sorts *above* it. An adapter comparing endpoints with `<=` or `>=`
        instead of `==` then over-matches in a way that random ids would
        expose only sometimes -- a test that passes by luck of the draw is
        worse than one that fails.
        """
        tenant = uuid4()
        ids = sorted((uuid4() for _ in range(4)), key=str)
        low, hub, high, sink = (_example_entity(tenant=tenant, id=i) for i in ids)
        await store.upsert_entities([low, hub, high, sink])
        from_below = _example_relationship(tenant, source=low.id, target=hub.id)
        from_above = _example_relationship(tenant, source=high.id, target=hub.id)
        outgoing = _example_relationship(tenant, source=hub.id, target=sink.id)
        await store.upsert_relationships([from_below, from_above, outgoing])

        assert await store.get_relationships(hub.id, tenant, direction="out") == [outgoing]
        assert {r.id for r in await store.get_relationships(hub.id, tenant, direction="in")} == {
            from_below.id,
            from_above.id,
        }
        assert {r.id for r in await store.get_relationships(hub.id, tenant, direction="both")} == {
            from_below.id,
            from_above.id,
            outgoing.id,
        }

    async def test_get_relationships_compares_direction_by_value(self, store: GraphStore) -> None:
        """`direction` is matched by value, not by object identity.

        The example tests pass string literals, which CPython interns, so an
        adapter using `is` passes them while failing for a caller that built
        the string at runtime.

        The hub has one edge in *each* direction, so falling through to
        "both" -- which is what an unrecognised value degrades to -- gives a
        different answer from every valid direction. Without that, "out" and
        "both" coincide and the fall-through hides.
        """
        tenant = uuid4()
        hub, upstream, downstream = (_example_entity(tenant=tenant) for _ in range(3))
        await store.upsert_entities([hub, upstream, downstream])
        incoming = _example_relationship(tenant, source=upstream.id, target=hub.id)
        outgoing = _example_relationship(tenant, source=hub.id, target=downstream.id)
        await store.upsert_relationships([incoming, outgoing])

        outward = "".join(["o", "u", "t"])
        inward = "".join(["i", "n"])
        both = "".join(["bo", "th"])
        assert id(outward) != id("out")  # equal in value, distinct as an object

        assert await store.get_relationships(hub.id, tenant, direction=outward) == [outgoing]  # type: ignore[arg-type]
        assert await store.get_relationships(hub.id, tenant, direction=inward) == [incoming]  # type: ignore[arg-type]
        assert len(await store.get_relationships(hub.id, tenant, direction=both)) == 2  # type: ignore[arg-type]

    async def test_get_relationships_defaults_to_both_directions(self, store: GraphStore) -> None:
        tenant = uuid4()
        hub, upstream, downstream = (_example_entity(tenant=tenant) for _ in range(3))
        await store.upsert_entities([hub, upstream, downstream])
        await store.upsert_relationships(
            [
                _example_relationship(tenant, source=upstream.id, target=hub.id),
                _example_relationship(tenant, source=hub.id, target=downstream.id),
            ]
        )

        assert len(await store.get_relationships(hub.id, tenant)) == 2

    async def test_get_relationships_filters_by_type(self, store: GraphStore) -> None:
        tenant = uuid4()
        a, b, c = (_example_entity(tenant=tenant) for _ in range(3))
        await store.upsert_entities([a, b, c])
        # The excluded edge is written first, so an adapter that stops at the
        # first non-matching edge rather than skipping it fails here.
        excluded = _example_relationship(tenant, source=a.id, target=b.id, kind="works_at")
        wanted = _example_relationship(tenant, source=a.id, target=c.id, kind="knows")
        await store.upsert_relationships([excluded, wanted])

        assert await store.get_relationships(a.id, tenant, relationship_types=["knows"]) == [wanted]
        assert await store.get_relationships(a.id, tenant, relationship_types=[]) == []
        assert len(await store.get_relationships(a.id, tenant, relationship_types=None)) == 2

    async def test_get_relationships_combines_direction_and_type(self, store: GraphStore) -> None:
        tenant = uuid4()
        hub, other = _example_entity(tenant=tenant), _example_entity(tenant=tenant)
        await store.upsert_entities([hub, other])
        await store.upsert_relationships(
            [
                _example_relationship(tenant, source=hub.id, target=other.id, kind="knows"),
                _example_relationship(tenant, source=other.id, target=hub.id, kind="knows"),
                _example_relationship(tenant, source=hub.id, target=other.id, kind="works_at"),
            ]
        )

        found = await store.get_relationships(
            hub.id, tenant, direction="out", relationship_types=["knows"]
        )
        assert [r.relationship_type for r in found] == ["knows"]
        assert [r.source_entity_id for r in found] == [hub.id]

    async def test_get_relationships_of_an_unknown_entity_is_empty(self, store: GraphStore) -> None:
        assert await store.get_relationships(uuid4(), uuid4()) == []

    async def test_get_relationships_rejects_an_unknown_direction(self, store: GraphStore) -> None:
        with pytest.raises(ValueError, match="direction"):
            await store.get_relationships(uuid4(), uuid4(), direction="sideways")  # type: ignore[arg-type]

    async def test_re_upserting_a_relationship_yields_exactly_one_row(
        self, store: GraphStore
    ) -> None:
        """Direct assertion of what `test_relationship_upsert_replaces_by_id`
        could previously only infer through `neighbors`."""
        tenant = uuid4()
        a, b = _example_entity(tenant=tenant), _example_entity(tenant=tenant)
        await store.upsert_entities([a, b])
        edge = _example_relationship(tenant, source=a.id, target=b.id)

        await store.upsert_relationship(edge)
        await store.upsert_relationship(edge)
        await store.upsert_relationships([edge, edge])

        assert await store.get_relationships(a.id, tenant) == [edge]

    async def test_delete_by_tenant_leaves_no_orphan_relationships(self, store: GraphStore) -> None:
        """Direct assertion of the second defect found by injection.

        Previously this could only be inferred by re-adding the entities and
        re-checking `neighbors`.
        """
        tenant = uuid4()
        a, b = _example_entity(tenant=tenant), _example_entity(tenant=tenant)
        await store.upsert_entities([a, b])
        await store.upsert_relationship(_example_relationship(tenant, source=a.id, target=b.id))

        await store.delete_by_tenant(tenant)

        assert await store.get_relationships(a.id, tenant) == []
        await store.upsert_entities([a, b])
        assert await store.get_relationships(a.id, tenant) == []

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------

    async def test_neighbours_terminate_on_a_cycle(self, store: GraphStore) -> None:
        tenant = uuid4()
        a, b, c = (_example_entity(tenant=tenant) for _ in range(3))
        await store.upsert_entities([a, b, c])
        # A -> B -> C -> A. `Relationship` forbids self-loops, so the shortest
        # cycle a store can be asked to survive is this three-node one.
        await store.upsert_relationships(
            [
                _example_relationship(tenant, source=a.id, target=b.id),
                _example_relationship(tenant, source=b.id, target=c.id),
                _example_relationship(tenant, source=c.id, target=a.id),
            ]
        )

        reached = {e.id for e in await store.neighbors(a.id, tenant, depth=99)}
        assert reached == {b.id, c.id}

    async def test_neighbours_traverse_both_directions(self, store: GraphStore) -> None:
        tenant = uuid4()
        a, b = _example_entity(tenant=tenant), _example_entity(tenant=tenant)
        await store.upsert_entities([a, b])
        await store.upsert_relationship(_example_relationship(tenant, source=a.id, target=b.id))

        assert [e.id for e in await store.neighbors(b.id, tenant)] == [a.id]

    async def test_neighbours_respect_depth(self, store: GraphStore) -> None:
        tenant = uuid4()
        a, b, c = (_example_entity(tenant=tenant) for _ in range(3))
        await store.upsert_entities([a, b, c])
        await store.upsert_relationships(
            [
                _example_relationship(tenant, source=a.id, target=b.id),
                _example_relationship(tenant, source=b.id, target=c.id),
            ]
        )

        assert {e.id for e in await store.neighbors(a.id, tenant, depth=1)} == {b.id}
        assert {e.id for e in await store.neighbors(a.id, tenant, depth=2)} == {b.id, c.id}

    async def test_neighbours_traverse_a_long_chain(self, store: GraphStore) -> None:
        """Reach every hop of a 5-node chain, one depth at a time.

        Two-hop tests are not enough: an adapter that computes the next hop
        count with any operator that happens to agree with `+ 1` on {0, 1}
        passes them and diverges at hop three.
        """
        tenant = uuid4()
        chain = [_example_entity(tenant=tenant) for _ in range(5)]
        await store.upsert_entities(chain)
        await store.upsert_relationships(
            [
                _example_relationship(tenant, source=chain[i].id, target=chain[i + 1].id)
                for i in range(4)
            ]
        )

        for depth in range(5):
            reached = {e.id for e in await store.neighbors(chain[0].id, tenant, depth=depth)}
            assert reached == {node.id for node in chain[1 : depth + 1]}

    async def test_neighbours_default_depth_is_one_hop(self, store: GraphStore) -> None:
        """The default must be pinned, not merely "some small number"."""
        tenant = uuid4()
        a, b, c = (_example_entity(tenant=tenant) for _ in range(3))
        await store.upsert_entities([a, b, c])
        await store.upsert_relationships(
            [
                _example_relationship(tenant, source=a.id, target=b.id),
                _example_relationship(tenant, source=b.id, target=c.id),
            ]
        )

        assert [e.id for e in await store.neighbors(a.id, tenant)] == [b.id]

    async def test_neighbours_keep_scanning_past_an_already_visited_node(
        self, store: GraphStore
    ) -> None:
        """Meeting a visited node skips that node, not the rest of the scan.

        The ids are sorted so the origin is the lowest, which puts the
        already-visited node first for any adapter that scans in id order --
        the arrangement that catches an early exit instead of a skip.
        """
        tenant = uuid4()
        ids = sorted((uuid4() for _ in range(4)), key=str)
        hub_root, hub, leaf_one, leaf_two = (_example_entity(tenant=tenant, id=i) for i in ids)
        await store.upsert_entities([hub_root, hub, leaf_one, leaf_two])
        await store.upsert_relationships(
            [
                _example_relationship(tenant, source=hub_root.id, target=hub.id),
                _example_relationship(tenant, source=hub.id, target=leaf_one.id),
                _example_relationship(tenant, source=hub.id, target=leaf_two.id),
            ]
        )

        reached = {e.id for e in await store.neighbors(hub_root.id, tenant, depth=2)}
        assert reached == {hub.id, leaf_one.id, leaf_two.id}

    async def test_neighbours_keep_scanning_past_a_filtered_out_edge(
        self, store: GraphStore
    ) -> None:
        """A non-matching edge is skipped, not treated as the end of the scan."""
        tenant = uuid4()
        a, b, c = (_example_entity(tenant=tenant) for _ in range(3))
        await store.upsert_entities([a, b, c])
        # The excluded edge is written first, so an adapter that stops at the
        # first non-matching edge never sees the matching one.
        await store.upsert_relationships(
            [
                _example_relationship(tenant, source=a.id, target=b.id, kind="works_at"),
                _example_relationship(tenant, source=a.id, target=c.id, kind="knows"),
            ]
        )

        found = await store.neighbors(a.id, tenant, relationship_types=["knows"])
        assert [e.id for e in found] == [c.id]

    async def test_neighbours_filter_by_relationship_type(self, store: GraphStore) -> None:
        tenant = uuid4()
        a, b, c = (_example_entity(tenant=tenant) for _ in range(3))
        await store.upsert_entities([a, b, c])
        await store.upsert_relationships(
            [
                _example_relationship(tenant, source=a.id, target=b.id, kind="knows"),
                _example_relationship(tenant, source=a.id, target=c.id, kind="works_at"),
            ]
        )

        assert {
            e.id for e in await store.neighbors(a.id, tenant, relationship_types=["knows"])
        } == {b.id}
        assert await store.neighbors(a.id, tenant, relationship_types=[]) == []

    async def test_neighbours_exclude_the_origin(self, store: GraphStore) -> None:
        tenant = uuid4()
        a, b = _example_entity(tenant=tenant), _example_entity(tenant=tenant)
        await store.upsert_entities([a, b])
        await store.upsert_relationships(
            [
                _example_relationship(tenant, source=a.id, target=b.id),
                _example_relationship(tenant, source=b.id, target=a.id),
            ]
        )

        assert [e.id for e in await store.neighbors(a.id, tenant, depth=5)] == [b.id]

    # ------------------------------------------------------------------
    # find_entities
    # ------------------------------------------------------------------

    async def test_find_entities_returns_everything_for_the_tenant(self, store: GraphStore) -> None:
        tenant = uuid4()
        entities = [_example_entity(tenant=tenant) for _ in range(3)]
        await store.upsert_entities(entities)

        assert {e.id for e in await store.find_entities(tenant)} == {e.id for e in entities}

    async def test_find_entities_filters_combine_with_and(self, store: GraphStore) -> None:
        tenant = uuid4()
        wanted = _example_entity(tenant=tenant, normalized_name="ada", entity_type="person")
        wrong_type = _example_entity(tenant=tenant, normalized_name="ada", entity_type="place")
        wrong_name = _example_entity(tenant=tenant, normalized_name="bob", entity_type="person")
        await store.upsert_entities([wanted, wrong_type, wrong_name])

        found = await store.find_entities(tenant, name="ada", entity_type="person")
        assert [e.id for e in found] == [wanted.id]

    async def test_find_entities_matches_normalized_name_exactly(self, store: GraphStore) -> None:
        tenant = uuid4()
        entity = _example_entity(tenant=tenant, name="Ada Lovelace", normalized_name="ada lovelace")
        await store.upsert_entities([entity])

        # Built at runtime so it is equal to the stored name but not the same
        # object. Filters must compare by value; an adapter using `is` passes
        # against a literal purely because CPython interns it.
        equal_but_distinct = " ".join(["ada", "lovelace"])
        assert equal_but_distinct is not entity.normalized_name
        assert await store.find_entities(tenant, name=equal_but_distinct) == [entity]

        assert await store.find_entities(tenant, name="ada lovelace") == [entity]
        assert await store.find_entities(tenant, name="ada") == []
        assert await store.find_entities(tenant, name="Ada Lovelace") == []

    async def test_find_entities_matches_entity_type_by_value(self, store: GraphStore) -> None:
        tenant = uuid4()
        entity = _example_entity(tenant=tenant, entity_type="plot_point")
        await store.upsert_entity(entity)

        equal_but_distinct = "_".join(["plot", "point"])
        assert equal_but_distinct is not entity.entity_type
        assert await store.find_entities(tenant, entity_type=equal_but_distinct) == [entity]

    async def test_find_entities_respects_limit(self, store: GraphStore) -> None:
        tenant = uuid4()
        await store.upsert_entities([_example_entity(tenant=tenant) for _ in range(5)])

        assert len(await store.find_entities(tenant, limit=2)) == 2
        assert len(await store.find_entities(tenant, limit=0)) == 0
        assert len(await store.find_entities(tenant, limit=99)) == 5
        assert len(await store.find_entities(tenant, limit=None)) == 5

    async def test_find_entities_on_an_unknown_tenant_is_empty(self, store: GraphStore) -> None:
        assert await store.find_entities(uuid4()) == []

    # ------------------------------------------------------------------
    # find_by_blocking_key
    # ------------------------------------------------------------------

    async def test_find_by_blocking_key_groups_candidates(self, store: GraphStore) -> None:
        tenant = uuid4()
        one = _example_entity(tenant=tenant, blocking_keys=frozenset({"A430", "person:ad"}))
        two = _example_entity(tenant=tenant, blocking_keys=frozenset({"A430"}))
        other = _example_entity(tenant=tenant, blocking_keys=frozenset({"B123"}))
        keyless = _example_entity(tenant=tenant)
        await store.upsert_entities([one, two, other, keyless])

        assert {e.id for e in await store.find_by_blocking_key("A430", tenant)} == {one.id, two.id}
        assert {e.id for e in await store.find_by_blocking_key("person:ad", tenant)} == {one.id}
        assert await store.find_by_blocking_key("nope", tenant) == []

    async def test_find_by_blocking_key_reflects_the_latest_write(self, store: GraphStore) -> None:
        tenant = uuid4()
        entity = _example_entity(tenant=tenant, blocking_keys=frozenset({"old"}))
        await store.upsert_entity(entity)
        await store.upsert_entity(entity.model_copy(update={"blocking_keys": frozenset({"new"})}))

        assert await store.find_by_blocking_key("old", tenant) == []
        assert len(await store.find_by_blocking_key("new", tenant)) == 1

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _two_entities(
        self, store: GraphStore, tenant: UUID, data: st.DataObject
    ) -> tuple[Entity, Entity]:
        """Draw and write two distinct entities under `tenant`."""
        ids = data.draw(st.tuples(st.uuids(), st.uuids()).filter(lambda p: p[0] != p[1]))
        source = data.draw(gen.entities(tenant_id=tenant, entity_id=ids[0]))
        target = data.draw(gen.entities(tenant_id=tenant, entity_id=ids[1]))
        await store.upsert_entities([source, target])
        return source, target

    async def _connected_graph(
        self, store: GraphStore, tenant: UUID, data: st.DataObject
    ) -> list[Entity]:
        """Write a 5-node chain plus a few extra edges, and return the nodes."""
        ids = data.draw(st.lists(st.uuids(), min_size=5, max_size=5, unique=True))
        nodes = [data.draw(gen.entities(tenant_id=tenant, entity_id=i)) for i in ids]
        await store.upsert_entities(nodes)

        edges = [(i, i + 1) for i in range(len(nodes) - 1)]
        extra = data.draw(
            st.lists(
                st.tuples(st.integers(0, 4), st.integers(0, 4)).filter(lambda p: p[0] != p[1]),
                max_size=4,
            )
        )
        await store.upsert_relationships(
            [
                data.draw(
                    gen.relationships(
                        tenant_id=tenant,
                        source_entity_id=nodes[i].id,
                        target_entity_id=nodes[j].id,
                    )
                )
                for i, j in [*edges, *extra]
            ]
        )
        return nodes


def _example_entity(*, tenant: UUID, **overrides: Any) -> Entity:
    """A minimal valid entity for the example-based tests."""
    fields: dict[str, Any] = {
        "id": uuid4(),
        "tenant_id": tenant,
        "name": "Ada Lovelace",
        "normalized_name": "ada lovelace",
        "entity_type": "person",
        "extraction_method": ExtractionMethod.MANUAL,
        "confidence": 1.0,
    }
    fields.update(overrides)
    return Entity(**fields)


def _example_relationship(
    tenant: UUID,
    *,
    source: UUID,
    target: UUID,
    kind: str = "knows",
    edge_id: UUID | None = None,
) -> Relationship:
    return Relationship(
        id=edge_id if edge_id is not None else uuid4(),
        tenant_id=tenant,
        source_entity_id=source,
        target_entity_id=target,
        relationship_type=kind,
        confidence=1.0,
    )
