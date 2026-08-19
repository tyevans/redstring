"""Shared compliance suite for the `GraphStore` port.

**Every `GraphStore` adapter must pass this suite unchanged.** It is the
executable definition of the port; the prose in
`redstring.ports.graph_store` describes what these tests enforce.

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

    from redstring.testing.graph_store import GraphStoreCompliance

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

## If you add a read method to the port, add its isolation test here

Every method that hands back objects a caller can mutate needs a test that
mutates the result and asserts a later read is unaffected -- in the same edit
that adds the method.

This is not a style preference. Four read methods were added during slice 3
with complete behavioural tests and no isolation test, and in all four cases
a mutation-testing run, not review, found that returning the live internal
object passed everything. Behavioural tests cannot catch it: handing back the
stored object is correct on every read and wrong only afterwards.

Search this file for `_mutate` to find the existing ones and copy the shape.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from redstring.domain.alias import Alias
from redstring.domain.entity import Entity
from redstring.domain.exceptions import MissingEntityError
from redstring.domain.ids import EntityId, RelationshipId, TenantId
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.relationship import Relationship
from redstring.domain.temporal import DatePrecision, TemporalExtent, UncertaintyMarker
from redstring.ports.graph_store import GraphStore
from redstring.testing import strategies as gen

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

#: Hypothesis examples per property test.
#:
#: An adapter backed by a real database calls `new_store()` once per example,
#: so this number multiplies into database resets: at 50, a Neo4j run is
#: roughly 750 of them. Tune it **without editing this file**:
#:
#:     KG_COMPLIANCE_MAX_EXAMPLES=10 uv run pytest -m integration
#:
#: An explicit `max_examples` inside a `settings()` decorator outranks every
#: hypothesis profile, so a hard-coded value here would also make
#: `--hypothesis-profile` inert for every adapter. Reading it from the
#: environment keeps the promise that an adapter opts in solely by
#: implementing `new_store()`.
DEFAULT_MAX_EXAMPLES = int(os.environ.get("KG_COMPLIANCE_MAX_EXAMPLES", "50"))

# Store construction dominates the per-example cost for real backends, and a
# slow adapter is a performance finding rather than a flaky test. The deadline
# that would otherwise fire is off suite-wide, in `tests/conftest.py` -- this
# file was one of the places that argued for it.
compliance_settings = settings(
    max_examples=DEFAULT_MAX_EXAMPLES,
    suppress_health_check=[HealthCheck.too_slow],
)


#: The `observed_at` every example-based entity carries.
#:
#: Deliberately awkward, and every part of it is load-bearing. It is **not
#: midnight**, **not UTC**, and **not whole-minute**: a value that is any of
#: those round-trips unchanged through an implementation that truncates to a
#: date, normalises the offset away, or stores seconds only. The offset is the
#: sharp one -- Neo4j's driver returns a `neo4j.time.DateTime` whose conversion
#: back is lossy for offsets Python spells differently, which is why the
#: adapter stores ISO text and why a UTC fixture here would have agreed with
#: the lossy implementation.
EXAMPLE_OBSERVED_AT = datetime(2026, 3, 1, 14, 45, 30, tzinfo=timezone(timedelta(hours=-5)))


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

    async def dispose(self, store: GraphStore) -> None:
        """Release whatever `new_store` acquired. No-op by default.

        An in-memory store is garbage; a Neo4j store holds a driver and
        sessions. Adapters that own a connection must override this, or a run
        leaks one connection per hypothesis example.
        """

    @asynccontextmanager
    async def _store(self) -> AsyncIterator[GraphStore]:
        """A store for the duration of one example, disposed afterwards."""
        store = await self.new_store()
        try:
            yield store
        finally:
            await self.dispose(store)

    @pytest.fixture
    async def store(self) -> AsyncIterator[GraphStore]:
        async with self._store() as store:
            yield store

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
        async with self._store() as store:
            await store.upsert_entity(entity)
            assert await store.get_entity(entity.id, entity.tenant_id) == entity

    @compliance_settings
    @given(entity=gen.entities())
    async def test_upsert_entities_round_trips(self, entity: Entity) -> None:
        async with self._store() as store:
            await store.upsert_entities([entity])
            assert await store.get_entity(entity.id, entity.tenant_id) == entity

    async def test_a_temporal_extent_round_trips_field_for_field(self, store: GraphStore) -> None:
        """Every field of `Entity.temporal`, by example rather than by sampler.

        The round-trip properties above *do* cover `temporal` -- `entities()`
        draws one about half the time -- but only when the sampler happens to,
        and `max_examples` here is environment-tunable and lowered to 5 by
        mutation runs. So an adapter that dropped the field could pass a whole
        run, which is what BACKLOG B48 is about (as B53, folded in): the Neo4j adapter stores it as
        `temporal_json` and is correct by accident of implementation rather
        than by contract.

        Every field is set to a *distinct*, non-default value. An extent
        carrying only `start_date` cannot tell "stored the extent" from
        "stored the start date", and `precision` in particular is the one an
        adapter flattening to a timestamp column would silently lose -- which
        is exactly the field `domain/interval.py` needs to widen a year into a
        range.
        """
        tenant = TenantId(uuid4())
        extent = TemporalExtent(
            start_date=datetime(2023, 3, 1, 12, 30, tzinfo=UTC),
            end_date=datetime(2024, 7, 4, 9, 15, tzinfo=UTC),
            precision=DatePrecision.MONTH,
            uncertainty=UncertaintyMarker.CIRCA,
            original_text="circa March 2023 to July 2024",
            sequence_position=7,
            publication_date=datetime(2025, 1, 2, tzinfo=UTC),
        )
        entity = _example_entity(tenant=tenant, temporal=extent)

        await store.upsert_entity(entity)
        stored = await store.get_entity(entity.id, tenant)

        assert stored is not None
        assert stored.temporal == extent

    async def test_an_entity_with_no_temporal_extent_round_trips_as_none(
        self, store: GraphStore
    ) -> None:
        """The other half, and not a formality: an adapter that materialised an
        empty `TemporalExtent()` where the caller wrote `None` would satisfy
        the test above and change what `Entity.is_temporal` answers."""
        tenant = TenantId(uuid4())
        entity = _example_entity(tenant=tenant)

        await store.upsert_entity(entity)
        stored = await store.get_entity(entity.id, tenant)

        assert stored is not None
        assert stored.temporal is None

    async def test_an_entitys_observed_at_survives_a_round_trip(self, store: GraphStore) -> None:
        """Neo4j stores this as ISO text on purpose -- the driver's
        `neo4j.time.DateTime` round-trip is lossy for offsets, which
        `_alias_row` already documents for `merged_at`. A midnight UTC value
        would round-trip through every wrong implementation too.

        So the assertions are on the parts a truncating or normalising
        implementation would lose, not on the instant: `==` alone is satisfied
        by an adapter that converts `14:45:30-05:00` to `19:45:30+00:00`,
        because those *are* the same moment. `utcoffset` is what tells them
        apart.
        """
        tenant = TenantId(uuid4())
        entity = _example_entity(tenant=tenant)

        await store.upsert_entity(entity)
        stored = await store.get_entity(entity.id, tenant)

        assert stored is not None
        assert stored.provenance.observed_at == EXAMPLE_OBSERVED_AT
        assert stored.provenance.observed_at.utcoffset() == timedelta(hours=-5)
        assert stored.provenance.observed_at.second == 30

    # ------------------------------------------------------------------
    # Property 2 -- idempotency
    # ------------------------------------------------------------------

    @compliance_settings
    @given(entity=gen.entities())
    async def test_upserting_twice_is_indistinguishable_from_once(self, entity: Entity) -> None:
        async with self._store() as once, self._store() as twice:
            await once.upsert_entity(entity)

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
        async with self._store() as store:
            tenant = TenantId(data.draw(st.uuids()))
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
        tenant = TenantId(uuid4())
        a, b, c = (_example_entity(tenant=tenant) for _ in range(3))
        await store.upsert_entities([a, b, c])
        edge_id = RelationshipId(uuid4())

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
        async with self._store() as store:
            tenant = TenantId(data.draw(st.uuids()))
            entity_id = EntityId(data.draw(st.uuids()))
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
        self, tenants: tuple[TenantId, TenantId], data: st.DataObject
    ) -> None:
        tenant_a, tenant_b = tenants
        async with self._store() as store:
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
            same_name = b_entity.normalized_name == a_source.normalized_name
            assert by_name == (b_only if same_name else [])

            by_type = await store.find_entities(tenant_b, entity_type=a_source.entity_type)
            assert by_type == (b_only if b_entity.entity_type == a_source.entity_type else [])

    @compliance_settings
    @given(tenants=gen.distinct_tenant_pairs, data=st.data())
    async def test_relationships_do_not_cross_tenants(
        self, tenants: tuple[TenantId, TenantId], data: st.DataObject
    ) -> None:
        tenant_a, tenant_b = tenants
        async with self._store() as store:
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
        self, tenants: tuple[TenantId, TenantId], data: st.DataObject
    ) -> None:
        doomed, spared = tenants
        async with self._store() as store:
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
    @given(tenant=st.uuids().map(TenantId))
    async def test_delete_by_tenant_on_an_unknown_tenant_removes_nothing(
        self, tenant: TenantId
    ) -> None:
        async with self._store() as store:
            assert await store.delete_by_tenant(tenant) == 0

    # ------------------------------------------------------------------
    # Property 6 -- neighbour depth monotonicity
    # ------------------------------------------------------------------

    @compliance_settings
    @given(data=st.data())
    async def test_neighbours_at_depth_n_are_a_subset_of_depth_n_plus_one(
        self, data: st.DataObject
    ) -> None:
        async with self._store() as store:
            tenant = TenantId(data.draw(st.uuids()))
            chain = await self._connected_graph(store, tenant, data)
            origin = chain[0]

            for depth in range(4):
                shallow = {e.id for e in await store.neighbors(origin.id, tenant, depth=depth)}
                deeper = {e.id for e in await store.neighbors(origin.id, tenant, depth=depth + 1)}
                assert shallow <= deeper

    @compliance_settings
    @given(data=st.data())
    async def test_neighbours_at_depth_zero_is_empty(self, data: st.DataObject) -> None:
        async with self._store() as store:
            tenant = TenantId(data.draw(st.uuids()))
            chain = await self._connected_graph(store, tenant, data)
            assert await store.neighbors(chain[0].id, tenant, depth=0) == []

    # ------------------------------------------------------------------
    # Property 7 -- mutation isolation
    # ------------------------------------------------------------------

    @compliance_settings
    @given(entity=gen.entities())
    async def test_mutating_a_read_result_does_not_change_the_store(self, entity: Entity) -> None:
        async with self._store() as store:
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
        async with self._store() as store:
            pristine = entity.model_copy(deep=True)
            await store.upsert_entity(entity)

            _mutate(entity)

            assert await store.get_entity(pristine.id, pristine.tenant_id) == pristine

    @compliance_settings
    @given(entity=gen.entities())
    async def test_mutating_a_find_result_does_not_change_the_store(self, entity: Entity) -> None:
        async with self._store() as store:
            await store.upsert_entity(entity)
            pristine = entity.model_copy(deep=True)

            for found in await store.find_entities(entity.tenant_id):
                _mutate(found)

            assert await store.get_entity(entity.id, entity.tenant_id) == pristine

    async def test_mutating_a_blocking_key_result_does_not_change_the_store(
        self, store: GraphStore
    ) -> None:
        """Isolation is required of *every* read path, not just the common two."""
        tenant = TenantId(uuid4())
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
        tenant = TenantId(uuid4())
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
        assert await store.get_entity(EntityId(uuid4()), TenantId(uuid4())) is None

    async def test_dangling_source_raises_missing_entity_error(self, store: GraphStore) -> None:
        tenant = TenantId(uuid4())
        target = _example_entity(tenant=tenant)
        await store.upsert_entity(target)
        missing = EntityId(uuid4())

        with pytest.raises(MissingEntityError) as raised:
            await store.upsert_relationship(
                _example_relationship(tenant, source=missing, target=target.id)
            )
        assert raised.value.entity_id == missing
        assert raised.value.tenant_id == tenant

    async def test_dangling_target_raises_missing_entity_error(self, store: GraphStore) -> None:
        tenant = TenantId(uuid4())
        source = _example_entity(tenant=tenant)
        await store.upsert_entity(source)
        missing = EntityId(uuid4())

        with pytest.raises(MissingEntityError) as raised:
            await store.upsert_relationship(
                _example_relationship(tenant, source=source.id, target=missing)
            )
        assert raised.value.entity_id == missing

    async def test_endpoint_in_another_tenant_is_still_dangling(self, store: GraphStore) -> None:
        tenant, other = TenantId(uuid4()), TenantId(uuid4())
        source = _example_entity(tenant=tenant)
        target = _example_entity(tenant=other)
        await store.upsert_entities([source, target])

        with pytest.raises(MissingEntityError):
            await store.upsert_relationship(
                _example_relationship(tenant, source=source.id, target=target.id)
            )

    async def test_a_rejected_relationship_leaves_no_trace(self, store: GraphStore) -> None:
        tenant = TenantId(uuid4())
        source = _example_entity(tenant=tenant)
        await store.upsert_entity(source)

        with pytest.raises(MissingEntityError):
            await store.upsert_relationship(
                _example_relationship(tenant, source=source.id, target=EntityId(uuid4()))
            )
        assert await store.neighbors(source.id, tenant) == []

    async def test_upsert_relationships_rejects_a_dangling_element(self, store: GraphStore) -> None:
        tenant = TenantId(uuid4())
        a, b = _example_entity(tenant=tenant), _example_entity(tenant=tenant)
        await store.upsert_entities([a, b])

        with pytest.raises(MissingEntityError):
            await store.upsert_relationships(
                [
                    _example_relationship(tenant, source=a.id, target=b.id),
                    _example_relationship(tenant, source=a.id, target=EntityId(uuid4())),
                ]
            )

    async def test_a_rejected_batch_writes_nothing_at_all(self, store: GraphStore) -> None:
        """All-or-nothing, including the elements *before* the bad one.

        The port used to permit a partial write and say so, which left the two
        adapters differing on an axis nothing asserted: Neo4j validates every
        endpoint in one query and so wrote nothing, while the in-memory
        reference wrote the prefix. Both passed, because the test above stops
        at "it raised" (BACKLOG B10g).

        Note where the good edge is: **first**. A batch whose only element is
        the bad one cannot tell a partial write from an atomic one, which is
        why the divergence survived a test that looked like it covered this.
        """
        tenant = TenantId(uuid4())
        a, b = _example_entity(tenant=tenant), _example_entity(tenant=tenant)
        await store.upsert_entities([a, b])
        good = _example_relationship(tenant, source=a.id, target=b.id)

        with pytest.raises(MissingEntityError):
            await store.upsert_relationships(
                [good, _example_relationship(tenant, source=a.id, target=EntityId(uuid4()))]
            )

        assert await store.get_relationships(a.id, tenant) == []
        assert await store.neighbors(a.id, tenant) == []

    async def test_a_batch_that_fails_leaves_an_earlier_batch_alone(
        self, store: GraphStore
    ) -> None:
        """Atomicity is about *this* call, not about the store.

        Rolling back further than the failed batch would be a different and
        much worse bug, and an implementation that cleared the tenant's edges
        on failure would pass the test above.
        """
        tenant = TenantId(uuid4())
        a, b = _example_entity(tenant=tenant), _example_entity(tenant=tenant)
        await store.upsert_entities([a, b])
        established = _example_relationship(tenant, source=a.id, target=b.id)
        await store.upsert_relationships([established])

        with pytest.raises(MissingEntityError):
            await store.upsert_relationships(
                [_example_relationship(tenant, source=b.id, target=EntityId(uuid4()))]
            )

        assert [r.id for r in await store.get_relationships(a.id, tenant)] == [established.id]

    async def test_negative_depth_is_rejected(self, store: GraphStore) -> None:
        with pytest.raises(ValueError, match="depth"):
            await store.neighbors(EntityId(uuid4()), TenantId(uuid4()), depth=-1)

    async def test_negative_limit_is_rejected(self, store: GraphStore) -> None:
        with pytest.raises(ValueError, match="limit"):
            await store.find_entities(TenantId(uuid4()), limit=-1)

    async def test_neighbours_of_an_unknown_entity_is_empty(self, store: GraphStore) -> None:
        assert await store.neighbors(EntityId(uuid4()), TenantId(uuid4())) == []

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
        async with self._store() as store:
            tenant = TenantId(data.draw(st.uuids()))
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
        self, tenants: tuple[TenantId, TenantId], data: st.DataObject
    ) -> None:
        tenant_a, tenant_b = tenants
        async with self._store() as store:
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
        async with self._store() as store:
            tenant = TenantId(data.draw(st.uuids()))
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
        async with self._store() as store:
            tenant = TenantId(data.draw(st.uuids()))
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
        tenant = TenantId(uuid4())
        ids = sorted((EntityId(uuid4()) for _ in range(4)), key=str)
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
        tenant = TenantId(uuid4())
        hub, upstream, downstream = (_example_entity(tenant=tenant) for _ in range(3))
        await store.upsert_entities([hub, upstream, downstream])
        incoming = _example_relationship(tenant, source=upstream.id, target=hub.id)
        outgoing = _example_relationship(tenant, source=hub.id, target=downstream.id)
        await store.upsert_relationships([incoming, outgoing])

        outward = "".join(["o", "u", "t"])
        inward = "".join(["i", "n"])
        both = "".join(["bo", "th"])
        interned = "out"
        assert outward == interned
        assert outward is not interned  # equal in value, distinct as an object

        assert await store.get_relationships(hub.id, tenant, direction=outward) == [outgoing]  # type: ignore[arg-type]
        assert await store.get_relationships(hub.id, tenant, direction=inward) == [incoming]  # type: ignore[arg-type]
        assert len(await store.get_relationships(hub.id, tenant, direction=both)) == 2  # type: ignore[arg-type]

    async def test_get_relationships_defaults_to_both_directions(self, store: GraphStore) -> None:
        tenant = TenantId(uuid4())
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
        tenant = TenantId(uuid4())
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
        tenant = TenantId(uuid4())
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
        assert await store.get_relationships(EntityId(uuid4()), TenantId(uuid4())) == []

    async def test_get_relationships_rejects_an_unknown_direction(self, store: GraphStore) -> None:
        with pytest.raises(ValueError, match="direction"):
            await store.get_relationships(uuid4(), uuid4(), direction="sideways")  # type: ignore[arg-type]

    async def test_re_upserting_a_relationship_yields_exactly_one_row(
        self, store: GraphStore
    ) -> None:
        """Direct assertion of what `test_relationship_upsert_replaces_by_id`
        could previously only infer through `neighbors`."""
        tenant = TenantId(uuid4())
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
        tenant = TenantId(uuid4())
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
        tenant = TenantId(uuid4())
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
        tenant = TenantId(uuid4())
        a, b = _example_entity(tenant=tenant), _example_entity(tenant=tenant)
        await store.upsert_entities([a, b])
        await store.upsert_relationship(_example_relationship(tenant, source=a.id, target=b.id))

        assert [e.id for e in await store.neighbors(b.id, tenant)] == [a.id]

    async def test_neighbours_respect_depth(self, store: GraphStore) -> None:
        tenant = TenantId(uuid4())
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
        tenant = TenantId(uuid4())
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
        tenant = TenantId(uuid4())
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
        tenant = TenantId(uuid4())
        ids = sorted((EntityId(uuid4()) for _ in range(4)), key=str)
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
        tenant = TenantId(uuid4())
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
        tenant = TenantId(uuid4())
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
        tenant = TenantId(uuid4())
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
        tenant = TenantId(uuid4())
        entities = [_example_entity(tenant=tenant) for _ in range(3)]
        await store.upsert_entities(entities)

        assert {e.id for e in await store.find_entities(tenant)} == {e.id for e in entities}

    async def test_find_entities_filters_combine_with_and(self, store: GraphStore) -> None:
        tenant = TenantId(uuid4())
        wanted = _example_entity(tenant=tenant, normalized_name="ada", entity_type="person")
        wrong_type = _example_entity(tenant=tenant, normalized_name="ada", entity_type="place")
        wrong_name = _example_entity(tenant=tenant, normalized_name="bob", entity_type="person")
        await store.upsert_entities([wanted, wrong_type, wrong_name])

        found = await store.find_entities(tenant, name="ada", entity_type="person")
        assert [e.id for e in found] == [wanted.id]

    async def test_find_entities_matches_normalized_name_exactly(self, store: GraphStore) -> None:
        tenant = TenantId(uuid4())
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
        tenant = TenantId(uuid4())
        entity = _example_entity(tenant=tenant, entity_type="plot_point")
        await store.upsert_entity(entity)

        equal_but_distinct = "_".join(["plot", "point"])
        assert equal_but_distinct is not entity.entity_type
        assert await store.find_entities(tenant, entity_type=equal_but_distinct) == [entity]

    async def test_find_entities_respects_limit(self, store: GraphStore) -> None:
        tenant = TenantId(uuid4())
        await store.upsert_entities([_example_entity(tenant=tenant) for _ in range(5)])

        assert len(await store.find_entities(tenant, limit=2)) == 2
        assert len(await store.find_entities(tenant, limit=0)) == 0
        assert len(await store.find_entities(tenant, limit=99)) == 5
        assert len(await store.find_entities(tenant, limit=None)) == 5

    async def test_find_entities_on_an_unknown_tenant_is_empty(self, store: GraphStore) -> None:
        assert await store.find_entities(TenantId(uuid4())) == []

    # ------------------------------------------------------------------
    # find_by_blocking_key
    # ------------------------------------------------------------------

    async def test_find_by_blocking_key_groups_candidates(self, store: GraphStore) -> None:
        tenant = TenantId(uuid4())
        one = _example_entity(tenant=tenant, blocking_keys=frozenset({"A430", "person:ad"}))
        two = _example_entity(tenant=tenant, blocking_keys=frozenset({"A430"}))
        other = _example_entity(tenant=tenant, blocking_keys=frozenset({"B123"}))
        keyless = _example_entity(tenant=tenant)
        await store.upsert_entities([one, two, other, keyless])

        assert {e.id for e in await store.find_by_blocking_key("A430", tenant)} == {one.id, two.id}
        assert {e.id for e in await store.find_by_blocking_key("person:ad", tenant)} == {one.id}
        assert await store.find_by_blocking_key("nope", tenant) == []

    async def test_find_by_blocking_key_reflects_the_latest_write(self, store: GraphStore) -> None:
        tenant = TenantId(uuid4())
        entity = _example_entity(tenant=tenant, blocking_keys=frozenset({"old"}))
        await store.upsert_entity(entity)
        await store.upsert_entity(entity.model_copy(update={"blocking_keys": frozenset({"new"})}))

        assert await store.find_by_blocking_key("old", tenant) == []
        assert len(await store.find_by_blocking_key("new", tenant)) == 1

    # ------------------------------------------------------------------
    # Batch reads
    #
    # Consolidation is set-shaped: block a tenant by many keys, fetch many
    # candidates, enumerate a whole group's edges. Each of these exists so
    # that is one query rather than a loop over the singular form -- free
    # in-memory, one round trip versus a thousand against a database.
    #
    # A batch read is also a fresh place for a tenant leak to hide, so each
    # has an isolation property, not just an example.
    # ------------------------------------------------------------------

    @compliance_settings
    @given(tenants=gen.distinct_tenant_pairs, data=st.data())
    async def test_get_entities_never_crosses_tenants(
        self, tenants: tuple[TenantId, TenantId], data: st.DataObject
    ) -> None:
        tenant_a, tenant_b = tenants
        async with self._store() as store:
            a_one, a_two = await self._two_entities(store, tenant_a, data)
            b_entity = data.draw(gen.entities(tenant_id=tenant_b))
            await store.upsert_entity(b_entity)

            wanted = [a_one.id, a_two.id, b_entity.id]
            under_a = await store.get_entities(wanted, tenant_a)
            assert {e.tenant_id for e in under_a} == {tenant_a}
            assert {e.id for e in under_a} == {a_one.id, a_two.id} | (
                {b_entity.id} & {a_one.id, a_two.id}
            )

            under_b = await store.get_entities(wanted, tenant_b)
            assert under_b == [b_entity]

    async def test_get_entities_returns_what_exists(self, store: GraphStore) -> None:
        tenant = TenantId(uuid4())
        one, two = _example_entity(tenant=tenant), _example_entity(tenant=tenant)
        await store.upsert_entities([one, two])
        absent = EntityId(uuid4())

        found = await store.get_entities([one.id, absent, two.id], tenant)

        # Unknown ids are skipped, not represented by a None placeholder: the
        # caller asked which of these exist, and a hole would just be re-filtered.
        assert {e.id for e in found} == {one.id, two.id}
        assert await store.get_entities([], tenant) == []
        assert await store.get_entities([absent], tenant) == []

    async def test_get_entities_deduplicates_repeated_ids(self, store: GraphStore) -> None:
        tenant = TenantId(uuid4())
        entity = _example_entity(tenant=tenant)
        await store.upsert_entity(entity)

        assert await store.get_entities([entity.id, entity.id, entity.id], tenant) == [entity]

    async def test_get_entities_agrees_with_get_entity(self, store: GraphStore) -> None:
        tenant = TenantId(uuid4())
        entities = [_example_entity(tenant=tenant) for _ in range(3)]
        await store.upsert_entities(entities)

        batched = {e.id: e for e in await store.get_entities([e.id for e in entities], tenant)}
        for entity in entities:
            assert batched[entity.id] == await store.get_entity(entity.id, tenant)

    async def test_mutating_a_batch_result_does_not_change_the_store(
        self, store: GraphStore
    ) -> None:
        tenant = TenantId(uuid4())
        entity = _example_entity(tenant=tenant, properties={"nested": {"k": "v"}})
        await store.upsert_entity(entity)
        pristine = entity.model_copy(deep=True)

        for found in await store.get_entities([entity.id], tenant):
            _mutate(found)

        assert await store.get_entity(entity.id, tenant) == pristine

    async def test_find_by_blocking_keys_groups_by_key(self, store: GraphStore) -> None:
        tenant = TenantId(uuid4())
        both = _example_entity(tenant=tenant, blocking_keys=frozenset({"A430", "B123"}))
        only_a = _example_entity(tenant=tenant, blocking_keys=frozenset({"A430"}))
        await store.upsert_entities([both, only_a])

        found = await store.find_by_blocking_keys(["A430", "B123", "absent"], tenant)

        assert {e.id for e in found["A430"]} == {both.id, only_a.id}
        assert {e.id for e in found["B123"]} == {both.id}
        # Every requested key is present, so a caller can iterate the result
        # without re-checking membership against what it asked for.
        assert found["absent"] == []
        assert set(found) == {"A430", "B123", "absent"}

    async def test_find_by_blocking_keys_agrees_with_the_singular_form(
        self, store: GraphStore
    ) -> None:
        tenant = TenantId(uuid4())
        await store.upsert_entities(
            [
                _example_entity(tenant=tenant, blocking_keys=frozenset({"A430"})),
                _example_entity(tenant=tenant, blocking_keys=frozenset({"B123"})),
            ]
        )

        batched = await store.find_by_blocking_keys(["A430", "B123"], tenant)
        for key in ("A430", "B123"):
            assert batched[key] == await store.find_by_blocking_key(key, tenant)

    async def test_find_by_blocking_keys_with_no_keys_is_empty(self, store: GraphStore) -> None:
        assert await store.find_by_blocking_keys([], TenantId(uuid4())) == {}

    async def test_mutating_a_grouped_result_does_not_change_the_store(
        self, store: GraphStore
    ) -> None:
        tenant = TenantId(uuid4())
        entity = _example_entity(
            tenant=tenant, blocking_keys=frozenset({"A430"}), properties={"nested": {"k": "v"}}
        )
        await store.upsert_entity(entity)
        pristine = entity.model_copy(deep=True)

        for group in (await store.find_by_blocking_keys(["A430"], tenant)).values():
            for found in group:
                _mutate(found)

        assert await store.find_by_blocking_keys(["A430"], tenant) == {"A430": [pristine]}

    @compliance_settings
    @given(tenants=gen.distinct_tenant_pairs, data=st.data())
    async def test_find_by_blocking_keys_never_crosses_tenants(
        self, tenants: tuple[TenantId, TenantId], data: st.DataObject
    ) -> None:
        tenant_a, tenant_b = tenants
        async with self._store() as store:
            a_entity = data.draw(gen.entities(tenant_id=tenant_a))
            await store.upsert_entity(a_entity)

            for key in a_entity.blocking_keys or frozenset():
                assert await store.find_by_blocking_keys([key], tenant_b) == {key: []}

    async def test_get_relationships_for_covers_a_whole_group(self, store: GraphStore) -> None:
        tenant = TenantId(uuid4())
        a, b, c, outside = (_example_entity(tenant=tenant) for _ in range(4))
        await store.upsert_entities([a, b, c, outside])
        ab = _example_relationship(tenant, source=a.id, target=b.id)
        bc = _example_relationship(tenant, source=b.id, target=c.id)
        far = _example_relationship(tenant, source=c.id, target=outside.id)
        await store.upsert_relationships([ab, bc, far])

        found = await store.get_relationships_for([a.id, b.id], tenant)

        # ab touches both endpoints and must appear once, not twice: the result
        # is a set of edges, not a concatenation of per-entity answers.
        assert sorted(r.id.hex for r in found) == sorted((ab.id.hex, bc.id.hex))

    async def test_get_relationships_for_honours_direction_and_type(
        self, store: GraphStore
    ) -> None:
        tenant = TenantId(uuid4())
        a, b, c = (_example_entity(tenant=tenant) for _ in range(3))
        await store.upsert_entities([a, b, c])
        out_knows = _example_relationship(tenant, source=a.id, target=c.id, kind="knows")
        out_works = _example_relationship(tenant, source=a.id, target=c.id, kind="works_at")
        incoming = _example_relationship(tenant, source=c.id, target=b.id, kind="knows")
        await store.upsert_relationships([out_knows, out_works, incoming])

        assert await store.get_relationships_for(
            [a.id, b.id], tenant, direction="out", relationship_types=["knows"]
        ) == [out_knows]
        assert await store.get_relationships_for([a.id, b.id], tenant, direction="in") == [incoming]

    async def test_get_relationships_for_agrees_with_the_singular_form(
        self, store: GraphStore
    ) -> None:
        tenant = TenantId(uuid4())
        a, b = _example_entity(tenant=tenant), _example_entity(tenant=tenant)
        await store.upsert_entities([a, b])
        await store.upsert_relationship(_example_relationship(tenant, source=a.id, target=b.id))

        assert await store.get_relationships_for([a.id], tenant) == await store.get_relationships(
            a.id, tenant
        )

    async def test_get_relationships_for_with_no_ids_is_empty(self, store: GraphStore) -> None:
        assert await store.get_relationships_for([], TenantId(uuid4())) == []

    async def test_mutating_a_batched_relationship_does_not_change_the_store(
        self, store: GraphStore
    ) -> None:
        tenant = TenantId(uuid4())
        a, b = _example_entity(tenant=tenant), _example_entity(tenant=tenant)
        await store.upsert_entities([a, b])
        edge = _example_relationship(tenant, source=a.id, target=b.id)
        edge.properties["nested"] = {"k": "v"}
        await store.upsert_relationship(edge)
        pristine = edge.model_copy(deep=True)

        for found in await store.get_relationships_for([a.id], tenant):
            found.relationship_type += "-tampered"
            found.properties["nested"]["k"] = "tampered"

        assert await store.get_relationships_for([a.id], tenant) == [pristine]

    @compliance_settings
    @given(tenants=gen.distinct_tenant_pairs, data=st.data())
    async def test_get_relationships_for_never_crosses_tenants(
        self, tenants: tuple[TenantId, TenantId], data: st.DataObject
    ) -> None:
        tenant_a, tenant_b = tenants
        async with self._store() as store:
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

            ids = [a_source.id, a_target.id]
            assert await store.get_relationships_for(ids, tenant_b) == []
            assert len(await store.get_relationships_for(ids, tenant_a)) == 1

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------

    async def test_find_entities_paginates_in_the_documented_order(self, store: GraphStore) -> None:
        """The port promises ascending canonical-string id order.

        A cursor without a defined total order is not resumable, so the order
        is part of the contract and asserted here rather than left to the
        adapter.
        """
        tenant = TenantId(uuid4())
        entities = [_example_entity(tenant=tenant) for _ in range(7)]
        await store.upsert_entities(entities)
        expected = sorted((e.id for e in entities), key=str)

        assert [e.id for e in await store.find_entities(tenant)] == expected

        page_size = 3
        seen: list[UUID] = []
        cursor: EntityId | None = None
        # Bounded rather than `while True`: a cursor that fails to advance --
        # inclusive instead of exclusive, or ignored entirely -- otherwise
        # loops forever, and a hung test is a far worse failure report than an
        # assertion. The bound is one page per entity, which no correct
        # adapter can reach.
        for _ in range(len(entities) + 1):
            page = await store.find_entities(tenant, limit=page_size, after=cursor)
            if not page:
                break
            assert len(page) <= page_size
            seen.extend(e.id for e in page)
            cursor = page[-1].id
        else:
            pytest.fail("pagination did not terminate: the cursor is not advancing")

        assert seen == expected

    async def test_find_entities_cursor_is_exclusive(self, store: GraphStore) -> None:
        tenant = TenantId(uuid4())
        entities = [_example_entity(tenant=tenant) for _ in range(4)]
        await store.upsert_entities(entities)
        ordered = sorted((e.id for e in entities), key=str)

        after_first = await store.find_entities(tenant, after=ordered[0])
        assert [e.id for e in after_first] == ordered[1:]

        assert await store.find_entities(tenant, after=ordered[-1]) == []

    async def test_find_entities_cursor_need_not_exist(self, store: GraphStore) -> None:
        """Resuming from a since-deleted id must not lose the rest of the page."""
        tenant = TenantId(uuid4())
        entities = [_example_entity(tenant=tenant) for _ in range(3)]
        await store.upsert_entities(entities)
        ordered = sorted((e.id for e in entities), key=str)

        await store.delete_by_tenant(tenant)
        await store.upsert_entities([e for e in entities if e.id != ordered[0]])

        assert [e.id for e in await store.find_entities(tenant, after=ordered[0])] == ordered[1:]

    async def test_find_entities_cursor_combines_with_filters(self, store: GraphStore) -> None:
        tenant = TenantId(uuid4())
        people = [_example_entity(tenant=tenant, entity_type="person") for _ in range(3)]
        await store.upsert_entities([*people, _example_entity(tenant=tenant, entity_type="place")])
        ordered = sorted((e.id for e in people), key=str)

        found = await store.find_entities(tenant, entity_type="person", after=ordered[0])
        assert [e.id for e in found] == ordered[1:]

    # ------------------------------------------------------------------
    # delete_relationship
    # ------------------------------------------------------------------

    async def test_delete_relationship_removes_only_that_edge(self, store: GraphStore) -> None:
        tenant = TenantId(uuid4())
        a, b, c = (_example_entity(tenant=tenant) for _ in range(3))
        await store.upsert_entities([a, b, c])
        doomed = _example_relationship(tenant, source=a.id, target=b.id)
        spared = _example_relationship(tenant, source=a.id, target=c.id)
        await store.upsert_relationships([doomed, spared])

        assert await store.delete_relationship(doomed.id, tenant) is True

        assert await store.get_relationships(a.id, tenant) == [spared]
        assert [e.id for e in await store.neighbors(a.id, tenant)] == [c.id]

    async def test_delete_relationship_leaves_the_endpoints(self, store: GraphStore) -> None:
        """Deleting an edge is not a cascade; both entities survive."""
        tenant = TenantId(uuid4())
        a, b = _example_entity(tenant=tenant), _example_entity(tenant=tenant)
        await store.upsert_entities([a, b])
        edge = _example_relationship(tenant, source=a.id, target=b.id)
        await store.upsert_relationship(edge)

        await store.delete_relationship(edge.id, tenant)

        assert await store.get_entity(a.id, tenant) == a
        assert await store.get_entity(b.id, tenant) == b

    async def test_delete_relationship_reports_whether_it_removed_anything(
        self, store: GraphStore
    ) -> None:
        tenant = TenantId(uuid4())
        a, b = _example_entity(tenant=tenant), _example_entity(tenant=tenant)
        await store.upsert_entities([a, b])
        edge = _example_relationship(tenant, source=a.id, target=b.id)
        await store.upsert_relationship(edge)

        assert await store.delete_relationship(edge.id, tenant) is True
        # Idempotent: replaying a delete is not an error, it just removes nothing.
        assert await store.delete_relationship(edge.id, tenant) is False
        assert await store.delete_relationship(RelationshipId(uuid4()), tenant) is False

    async def test_delete_relationship_is_tenant_scoped(self, store: GraphStore) -> None:
        tenant, other = TenantId(uuid4()), TenantId(uuid4())
        a, b = _example_entity(tenant=tenant), _example_entity(tenant=tenant)
        await store.upsert_entities([a, b])
        edge = _example_relationship(tenant, source=a.id, target=b.id)
        await store.upsert_relationship(edge)

        assert await store.delete_relationship(edge.id, other) is False
        assert await store.get_relationships(a.id, tenant) == [edge]

    # ------------------------------------------------------------------
    # Aliases
    # ------------------------------------------------------------------

    async def test_an_unmerged_id_resolves_to_itself(self, store: GraphStore) -> None:
        """Including an id the tenant has never seen.

        Resolution answers "has this been merged away", not "does this exist" --
        so it must not need the entity, and must not report absence as `None`.
        A caller looks up the mapping unconditionally.
        """
        tenant = TenantId(uuid4())
        known = _example_entity(tenant=tenant)
        await store.upsert_entity(known)
        stranger = EntityId(uuid4())

        assert await store.resolve_entity_ids([known.id, stranger], tenant) == {
            known.id: known.id,
            stranger: stranger,
        }

    async def test_an_alias_resolves_to_its_canonical(self, store: GraphStore) -> None:
        tenant = TenantId(uuid4())
        canonical, absorbed = (_example_entity(tenant=tenant) for _ in range(2))
        await store.upsert_entities([canonical, absorbed])
        await store.upsert_alias(_example_alias(tenant, canonical=canonical.id, alias=absorbed.id))

        assert await store.resolve_entity_ids([absorbed.id], tenant) == {absorbed.id: canonical.id}

    async def test_resolution_follows_a_chain_to_the_end(self, store: GraphStore) -> None:
        """`B -> A` then `A -> C` is a legal pair of merges, so `B` must give `C`.

        A one-hop implementation passes every single-merge test and fails only
        here, which is why the chain is three deep rather than two: at depth
        two, "follow one hop" and "follow to a fixed point" agree for `B` in
        exactly the way `CLAUDE.md` warns about. `d -> c -> b -> a` separates
        them for `d` *and* for `c`, so a fold-once implementation cannot pass
        by accident.
        """
        tenant = TenantId(uuid4())
        a, b, c, d = (_example_entity(tenant=tenant) for _ in range(4))
        await store.upsert_entities([a, b, c, d])
        await store.upsert_alias(_example_alias(tenant, canonical=a.id, alias=b.id))
        await store.upsert_alias(_example_alias(tenant, canonical=b.id, alias=c.id))
        await store.upsert_alias(_example_alias(tenant, canonical=c.id, alias=d.id))

        assert await store.resolve_entity_ids([d.id, c.id, b.id, a.id], tenant) == {
            d.id: a.id,
            c.id: a.id,
            b.id: a.id,
            a.id: a.id,
        }

    async def test_an_alias_is_keyed_by_the_absorbed_entity(self, store: GraphStore) -> None:
        """Last write wins on the alias id: an entity has one canonical parent."""
        tenant = TenantId(uuid4())
        first, second, absorbed = (_example_entity(tenant=tenant) for _ in range(3))
        await store.upsert_entities([first, second, absorbed])
        await store.upsert_alias(_example_alias(tenant, canonical=first.id, alias=absorbed.id))
        await store.upsert_alias(_example_alias(tenant, canonical=second.id, alias=absorbed.id))

        assert await store.resolve_entity_ids([absorbed.id], tenant) == {absorbed.id: second.id}
        assert await store.find_aliases(first.id, tenant) == []

    async def test_removing_an_alias_restores_the_identity(self, store: GraphStore) -> None:
        tenant = TenantId(uuid4())
        canonical, absorbed = (_example_entity(tenant=tenant) for _ in range(2))
        await store.upsert_entities([canonical, absorbed])
        await store.upsert_alias(_example_alias(tenant, canonical=canonical.id, alias=absorbed.id))

        assert await store.remove_alias(absorbed.id, tenant) is True
        assert await store.resolve_entity_ids([absorbed.id], tenant) == {absorbed.id: absorbed.id}
        # Idempotent: replaying an undo must not raise.
        assert await store.remove_alias(absorbed.id, tenant) is False

    async def test_find_aliases_is_direct_and_ordered(self, store: GraphStore) -> None:
        """Direct absorptions only, ascending by alias id.

        `B -> A` and `C -> B` means `find_aliases(A)` is `[B]`, not `[B, C]`:
        an undo asks "what did *this* merge absorb", and a transitive answer
        makes that unanswerable. The two ids are chosen so their canonical
        strings sort one way and their creation order the other, so an
        implementation returning insertion order fails.
        """
        tenant = TenantId(uuid4())
        a = _example_entity(tenant=tenant)
        low = _example_entity(tenant=tenant, id=UUID("00000000-0000-4000-8000-00000000000a"))
        high = _example_entity(tenant=tenant, id=UUID("ffffffff-0000-4000-8000-00000000000f"))
        deeper = _example_entity(tenant=tenant)
        await store.upsert_entities([a, low, high, deeper])
        await store.upsert_alias(_example_alias(tenant, canonical=a.id, alias=high.id))
        await store.upsert_alias(_example_alias(tenant, canonical=a.id, alias=low.id))
        await store.upsert_alias(_example_alias(tenant, canonical=high.id, alias=deeper.id))

        assert [alias.alias_entity_id for alias in await store.find_aliases(a.id, tenant)] == [
            low.id,
            high.id,
        ]

    async def test_find_aliases_returns_copies(self, store: GraphStore) -> None:
        tenant = TenantId(uuid4())
        canonical, absorbed = (_example_entity(tenant=tenant) for _ in range(2))
        alias = _example_alias(tenant, canonical=canonical.id, alias=absorbed.id)
        await store.upsert_entities([canonical, absorbed])
        await store.upsert_alias(alias)
        pristine = alias.model_copy(deep=True)

        for found in await store.find_aliases(canonical.id, tenant):
            assert found.alias_name is not None
            found.alias_name = found.alias_name + "-tampered"
            found.merge_reason = "tampered"

        assert await store.find_aliases(canonical.id, tenant) == [pristine]

    async def test_find_aliases_never_crosses_tenants(self, store: GraphStore) -> None:
        tenant, other = TenantId(uuid4()), TenantId(uuid4())
        canonical, absorbed = (_example_entity(tenant=tenant) for _ in range(2))
        await store.upsert_entities([canonical, absorbed])
        await store.upsert_alias(_example_alias(tenant, canonical=canonical.id, alias=absorbed.id))

        assert await store.find_aliases(canonical.id, other) == []

    async def test_resolve_entity_ids_never_crosses_tenants(self, store: GraphStore) -> None:
        """The dangerous direction: a leak here silently rewrites another
        tenant's edges onto an entity it has never heard of."""
        tenant, other = TenantId(uuid4()), TenantId(uuid4())
        canonical, absorbed = (_example_entity(tenant=tenant) for _ in range(2))
        await store.upsert_entities([canonical, absorbed])
        await store.upsert_alias(_example_alias(tenant, canonical=canonical.id, alias=absorbed.id))

        assert await store.resolve_entity_ids([absorbed.id], other) == {absorbed.id: absorbed.id}
        assert await store.remove_alias(absorbed.id, other) is False
        assert await store.resolve_entity_ids([absorbed.id], tenant) == {absorbed.id: canonical.id}

    async def test_deleting_a_tenant_takes_its_aliases(self, store: GraphStore) -> None:
        """Otherwise a rebuild replays merges over aliases that survived the
        wipe, and `delete_by_tenant` stops being a reset."""
        tenant = TenantId(uuid4())
        canonical, absorbed = (_example_entity(tenant=tenant) for _ in range(2))
        await store.upsert_entities([canonical, absorbed])
        await store.upsert_alias(_example_alias(tenant, canonical=canonical.id, alias=absorbed.id))

        await store.delete_by_tenant(tenant)

        assert await store.resolve_entity_ids([absorbed.id], tenant) == {absorbed.id: absorbed.id}
        assert await store.find_aliases(canonical.id, tenant) == []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _two_entities(
        self, store: GraphStore, tenant: TenantId, data: st.DataObject
    ) -> tuple[Entity, Entity]:
        """Draw and write two distinct entities under `tenant`."""
        ids = data.draw(
            st.tuples(st.uuids().map(EntityId), st.uuids().map(EntityId)).filter(
                lambda p: p[0] != p[1]
            )
        )
        source = data.draw(gen.entities(tenant_id=tenant, entity_id=ids[0]))
        target = data.draw(gen.entities(tenant_id=tenant, entity_id=ids[1]))
        await store.upsert_entities([source, target])
        return source, target

    async def _connected_graph(
        self, store: GraphStore, tenant: TenantId, data: st.DataObject
    ) -> list[Entity]:
        """Write a 5-node chain plus a few extra edges, and return the nodes."""
        ids = data.draw(st.lists(st.uuids().map(EntityId), min_size=5, max_size=5, unique=True))
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


def _example_entity(*, tenant: TenantId, **overrides: Any) -> Entity:  # noqa: ANN401
    """A minimal valid entity for the example-based tests."""
    fields: dict[str, Any] = {
        "id": EntityId(uuid4()),
        "tenant_id": tenant,
        "name": "Ada Lovelace",
        "normalized_name": "ada lovelace",
        "entity_type": "person",
        "provenance": Provenance(
            observed_at=EXAMPLE_OBSERVED_AT,
            extraction_method=ExtractionMethod.MANUAL,
            confidence=1.0,
        ),
    }
    fields.update(overrides)
    return Entity(**fields)


def _example_alias(tenant: TenantId, *, canonical: EntityId, alias: EntityId) -> Alias:
    """A minimal valid alias. `merged_at` is fixed so equality is stable."""
    return Alias(
        id=uuid4(),
        tenant_id=tenant,
        canonical_entity_id=canonical,
        alias_entity_id=alias,
        alias_name="A. Lovelace",
        alias_normalized_name="a. lovelace",
        merged_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _example_relationship(
    tenant: TenantId,
    *,
    source: EntityId,
    target: EntityId,
    kind: str = "knows",
    edge_id: RelationshipId | None = None,
) -> Relationship:
    return Relationship(
        id=edge_id if edge_id is not None else RelationshipId(uuid4()),
        tenant_id=tenant,
        source_entity_id=source,
        target_entity_id=target,
        relationship_type=kind,
        confidence=1.0,
    )
