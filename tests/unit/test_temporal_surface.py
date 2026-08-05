"""Timeline reads are reachable from `redstring`, and answer real questions.

`TemporalQuery` needed no composition work -- unlike consolidation, it already
took a single `GraphStore` and already had `timeline()` and
`relations_in_interval()`. What it lacked was an export, so the capability the
README advertises ("interval inference and time-sliced queries") could only be
reached by a dotted path the documentation described as liable to move.

These tests are written against `from redstring import ...` deliberately. A
test importing `redstring.temporal.query` would pass whether or not the
capability is on the public surface, which is the thing being fixed -- so it
would be a test of the wrong claim.

What they do *not* re-test is interval semantics. `tests/unit/temporal/` and
`tests/unit/domain/test_interval.py` own that, at 500 hypothesis examples per
property. Repeating it here would be a second copy of a tested claim; these
assert that a caller who only ever types `redstring.` can get a timeline out
and that the ordering contract survives the trip.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from redstring import (
    Bounds,
    DatePrecision,
    Entity,
    ExtractionMethod,
    InMemoryGraphStore,
    TemporalExtent,
    TemporalQuery,
    TemporalRelation,
    infer_relations,
)
from tests.compliance.strategies import aware_datetimes, distinct_tenant_pairs, entities


def dated(tenant_id, name, year, *, end_year=None, precision=DatePrecision.YEAR):
    """An entity that happened in `year`, or spanned `year..end_year`."""
    return Entity(
        id=uuid4(),
        tenant_id=tenant_id,
        source_id="doc-1",
        name=name,
        normalized_name=name.lower(),
        entity_type="event",
        extraction_method=ExtractionMethod.MANUAL,
        confidence=1.0,
        temporal=TemporalExtent(
            start_date=datetime(year, 1, 1, tzinfo=UTC),
            end_date=datetime(end_year, 12, 31, tzinfo=UTC) if end_year else None,
            precision=precision,
        ),
    )


def undated(tenant_id, name):
    return Entity(
        id=uuid4(),
        tenant_id=tenant_id,
        source_id="doc-1",
        name=name,
        normalized_name=name.lower(),
        entity_type="event",
        extraction_method=ExtractionMethod.MANUAL,
        confidence=1.0,
    )


@pytest.fixture
def tenant_id():
    return uuid4()


@pytest.fixture
def store():
    return InMemoryGraphStore()


class TestTheCapabilityIsReachable:
    async def test_a_timeline_comes_back_in_time_order(self, store, tenant_id):
        """The headline question, asked only through the public surface."""
        later = dated(tenant_id, "Analytical Engine", 1837)
        earlier = dated(tenant_id, "Difference Engine", 1822)
        await store.upsert_entities([later, earlier])

        found = await TemporalQuery(store).timeline(tenant_id)

        assert [e.name for e in found] == ["Difference Engine", "Analytical Engine"]

    async def test_undated_entities_are_absent_rather_than_first_or_last(self, store, tenant_id):
        """An entity with no extent is not "at the beginning of time"; it is
        not on a timeline at all. Sorting it to either end would put it in an
        answer about *when*, which is the one thing it cannot support."""
        await store.upsert_entities(
            [dated(tenant_id, "Difference Engine", 1822), undated(tenant_id, "Ada Lovelace")]
        )

        found = await TemporalQuery(store).timeline(tenant_id)

        assert [e.name for e in found] == ["Difference Engine"]

    async def test_an_interval_restricts_the_timeline(self, store, tenant_id):
        await store.upsert_entities(
            [
                dated(tenant_id, "Difference Engine", 1822),
                dated(tenant_id, "Analytical Engine", 1837),
            ]
        )

        window = Bounds(datetime(1830, 1, 1, tzinfo=UTC), datetime(1840, 1, 1, tzinfo=UTC))
        found = await TemporalQuery(store).timeline(tenant_id, interval=window)

        assert [e.name for e in found] == ["Analytical Engine"]

    async def test_relations_between_dated_entities_are_computed(self, store, tenant_id):
        """The inference half. `BEFORE` between two disjoint years is the
        relation a caller most often wants and the one `INFERRED_RELATIONS`
        leads with."""
        await store.upsert_entities(
            [
                dated(tenant_id, "Difference Engine", 1822),
                dated(tenant_id, "Analytical Engine", 1837),
            ]
        )

        relations = await TemporalQuery(store).relations_in_interval(tenant_id)

        assert [r.relation for r in relations] == [TemporalRelation.BEFORE]

    async def test_infer_relations_works_on_entities_the_caller_already_has(self, tenant_id):
        """The function, not the query object: a caller who already holds
        entities should not have to put them in a store to relate them."""
        relations = infer_relations(
            [
                dated(tenant_id, "Analytical Engine", 1837),
                dated(tenant_id, "Difference Engine", 1822),
            ]
        )

        assert [r.relation for r in relations] == [TemporalRelation.BEFORE]


class TestTenantIsolation:
    async def test_a_timeline_never_crosses_tenants(self, store, tenant_id):
        """Every read in this library is tenant-scoped, and a timeline is a
        read. Asserted here rather than assumed because `TemporalQuery` pages
        the whole tenant in Python -- the filtering is this module's, not the
        store's, which is exactly where a scope could be dropped."""
        other = uuid4()
        await store.upsert_entities([dated(tenant_id, "Ours", 1822), dated(other, "Theirs", 1837)])

        found = await TemporalQuery(store).timeline(tenant_id)

        assert [e.name for e in found] == ["Ours"]

    async def test_relations_never_cross_tenants(self, store, tenant_id):
        """The sharper case: two entities that *would* relate if the scope
        leaked. A test with one tenant's entity alone cannot see this."""
        other = uuid4()
        await store.upsert_entities([dated(tenant_id, "Ours", 1822), dated(other, "Theirs", 1837)])

        assert await TemporalQuery(store).relations_in_interval(tenant_id) == []


class TestProperties:
    """Two claims that examples cannot make, over generated entities.

    `tests/compliance/strategies.py` already draws entities with optional
    `TemporalExtent`s, so these get undated entities, open-ended extents and
    coincident bounds for free -- the shapes a hand-written fixture forgets.
    """

    @given(
        tenant_id=st.uuids(),
        drawn=st.lists(st.tuples(entities(), aware_datetimes), min_size=2, max_size=8),
    )
    @settings(max_examples=200)
    async def test_the_order_is_total_when_extents_coincide(self, tenant_id, drawn):
        """Entities sharing an extent come back in a deterministic order.

        `_chronologically` ends with the entity id precisely because two
        entities routinely carry the same extent -- three things that happened
        in 1066 -- and without that component their relative order would
        depend on what the adapter handed back, which the port does not
        promise to keep stable.

        **The obvious version of this property is vacuous, and it was written
        first.** Inserting the same entities in a rotated order and comparing
        two timelines passes *with the id tie-break deleted*, because
        `InMemoryGraphStore` already returns entities ascending by id -- so
        insertion order never reaches the sort, and the input cannot
        distinguish the two implementations. That is CLAUDE.md's failure shape
        exactly, caught only by breaking `_chronologically` on purpose and
        watching the property stay green.

        This version reverses what the store returns, so the sort is the only
        thing that can produce the order, and pins the answer to ascending id
        rather than to "whatever both runs agreed on".
        """
        shared = TemporalExtent(start_date=drawn[0][1])
        owned = [
            entity.model_copy(update={"tenant_id": tenant_id, "id": uuid4(), "temporal": shared})
            for entity, _ in drawn
        ]

        store = _ReversingGraphStore()
        await store.upsert_entities(owned)

        found = await TemporalQuery(store).timeline(tenant_id)

        assert [e.id for e in found] == sorted(e.id for e in owned)

    @given(
        pair=distinct_tenant_pairs,
        drawn=st.lists(st.tuples(entities(), aware_datetimes), min_size=1, max_size=6),
    )
    @settings(max_examples=200)
    async def test_a_timeline_holds_nothing_belonging_to_another_tenant(self, pair, drawn):
        """Tenant isolation over *any* pair of distinct tenants, not two
        hand-picked UUIDs.

        `TemporalQuery` pages the whole tenant and filters in Python, so the
        scope is enforced by this module rather than by the store -- which is
        exactly the arrangement where a dropped `tenant_id` would go
        unnoticed. Both tenants get the *same* entities so the two sides
        cannot differ by accident of what was generated.
        """
        mine, theirs = pair
        # A `start_date` is drawn per entity rather than reusing
        # `temporal_extents()`, and the difference is the whole reason the
        # vacuity guard below is worth having. `entities()` makes `temporal`
        # optional, so a drawn set can be entirely undated -- and
        # `temporal_extents()` is not enough either, because *both* its date
        # fields are independently optional, so it happily draws an extent
        # carrying only a `sequence_position`. `bounds()` returns `None` for
        # that, and `timeline` documents such entities as never matching.
        #
        # Two rounds of an empty result taught that; the guard is what turned
        # a silently-passing property into a failing one.
        store = InMemoryGraphStore()
        await store.upsert_entities(
            [
                e.model_copy(
                    update={
                        "tenant_id": owner,
                        "id": uuid4(),
                        "temporal": TemporalExtent(start_date=start),
                    }
                )
                for owner in (mine, theirs)
                for e, start in drawn
            ]
        )

        found = await TemporalQuery(store).timeline(mine)

        assert found, "the fixture put dated entities in, so this must not be vacuous"
        assert {e.tenant_id for e in found} == {mine}


class _ReversingGraphStore(InMemoryGraphStore):
    """An adapter that returns entities in the opposite order to the default.

    Every `GraphStore` in this repository hands back entities ascending by id,
    which makes any property about *ordering* untestable through them: the
    answer is already sorted before `timeline` sees it, so deleting the sort's
    tie-break changes nothing observable.

    The port promises no such order. This adapter exercises that permission --
    it is a legal `GraphStore` behaving in a way no shipped one does, which is
    the only way to tell "timeline sorts" apart from "the store happened to".
    """

    async def find_entities(self, *args, **kwargs):
        found = await super().find_entities(*args, **kwargs)
        return list(reversed(found))
