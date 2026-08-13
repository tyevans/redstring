"""Timeline reads over a real `GraphStore`. No mocks."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from redstring.domain.entity import Entity
from redstring.domain.interval import Bounds, TemporalRelation
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.temporal import DatePrecision, TemporalExtent, UncertaintyMarker
from redstring.graph.adapters.memory import InMemoryGraphStore
from redstring.temporal.query import CursorStalledError, TemporalQuery

#: A fixed observation instant. Never `datetime.now(UTC)`: a fixture that
#: varies per run makes any comparison on `observed_at` non-deterministic.
OBSERVED = datetime(2026, 2, 18, 11, 7, tzinfo=UTC)

pytestmark = pytest.mark.asyncio

TENANT = uuid4()
OTHER_TENANT = uuid4()


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)  # type: ignore[arg-type]


def year(y: int) -> TemporalExtent:
    return TemporalExtent(start_date=utc(y, 1, 1), precision=DatePrecision.YEAR)


def entity(
    name: str,
    extent: TemporalExtent | None = None,
    *,
    tenant=TENANT,
    entity_type: str = "Event",
) -> Entity:
    return Entity(
        id=uuid4(),
        tenant_id=tenant,
        name=name,
        normalized_name=name.lower(),
        entity_type=entity_type,
        temporal=extent,
        provenance=Provenance(
            observed_at=OBSERVED,
            extraction_method=ExtractionMethod.PATTERN,
            confidence=0.9,
            source_id="doc-1",
        ),
    )


async def stored(*entities: Entity) -> TemporalQuery:
    store = InMemoryGraphStore()
    await store.upsert_entities(entities)
    return TemporalQuery(store)


class TestEntitiesInInterval:
    async def test_an_entity_inside_the_window_is_returned(self):
        inside = entity("inside", year(1950))
        query = await stored(inside, entity("outside", year(2050)))
        found = await query.entities_in_interval(TENANT, Bounds(utc(1900, 1, 1), utc(2000, 1, 1)))
        assert [e.name for e in found] == ["inside"]

    async def test_an_entity_straddling_the_window_edge_is_returned(self):
        query = await stored(
            entity(
                "straddling",
                TemporalExtent(
                    start_date=utc(1990, 1, 1),
                    end_date=utc(2010, 1, 1),
                    precision=DatePrecision.YEAR,
                ),
            )
        )
        found = await query.entities_in_interval(TENANT, Bounds(utc(1900, 1, 1), utc(2000, 1, 1)))
        assert [e.name for e in found] == ["straddling"]

    async def test_an_adjacent_entity_is_not_in_the_window(self):
        """Half-open bounds. 1999 ends at the instant 2000 begins."""
        query = await stored(entity("adjacent", year(1999)))
        window = Bounds(utc(2000, 1, 1), utc(2001, 1, 1))
        assert await query.entities_in_interval(TENANT, window) == []

    async def test_undated_entities_never_match(self):
        query = await stored(
            entity("undated"), entity("sequenced", TemporalExtent(sequence_position=3))
        )
        found = await query.entities_in_interval(TENANT, Bounds(None, None))
        assert found == []

    async def test_a_precision_widened_extent_is_found_by_a_window_inside_its_year(self):
        """The row the two-column range query misses: "2023" has a null
        `end_date` and denotes all of 2023 anyway."""
        query = await stored(entity("the year", year(2023)))
        found = await query.entities_in_interval(TENANT, Bounds(utc(2023, 7, 1), utc(2023, 8, 1)))
        assert [e.name for e in found] == ["the year"]

    async def test_an_open_bound_is_found_by_a_window_it_reaches(self):
        query = await stored(
            entity(
                "after 1900",
                TemporalExtent(
                    start_date=utc(1900, 1, 1),
                    precision=DatePrecision.YEAR,
                    uncertainty=UncertaintyMarker.AFTER,
                ),
            )
        )
        later = Bounds(utc(3000, 1, 1), utc(3001, 1, 1))
        earlier = Bounds(utc(1800, 1, 1), utc(1801, 1, 1))
        assert await query.entities_in_interval(TENANT, later)
        assert not await query.entities_in_interval(TENANT, earlier)

    async def test_the_relation_filter_reads_entity_to_interval(self):
        window = Bounds(utc(2000, 1, 1), utc(2001, 1, 1))
        query = await stored(
            entity(
                "inside",
                TemporalExtent(start_date=utc(2000, 6, 1), precision=DatePrecision.DAY),
            ),
            entity(
                "spanning",
                TemporalExtent(
                    start_date=utc(1900, 1, 1),
                    end_date=utc(2100, 1, 1),
                    precision=DatePrecision.YEAR,
                ),
            ),
        )
        inside = await query.entities_in_interval(
            TENANT, window, relations={TemporalRelation.DURING}
        )
        spanning = await query.entities_in_interval(
            TENANT, window, relations={TemporalRelation.CONTAINS}
        )
        assert [e.name for e in inside] == ["inside"]
        assert [e.name for e in spanning] == ["spanning"]

    async def test_the_entity_type_filter_is_applied(self):
        query = await stored(
            entity("an event", year(1950)),
            entity("a person", year(1950), entity_type="Person"),
        )
        found = await query.entities_in_interval(TENANT, Bounds(None, None), entity_type="Person")
        assert [e.name for e in found] == ["a person"]


class TestTenantIsolation:
    async def test_another_tenants_entities_are_never_returned(self):
        query = await stored(
            entity("ours", year(1950)), entity("theirs", year(1950), tenant=OTHER_TENANT)
        )
        for found in (
            await query.entities_in_interval(TENANT, Bounds(None, None)),
            await query.timeline(TENANT),
        ):
            assert [e.name for e in found] == ["ours"]

    async def test_relations_are_never_inferred_across_tenants(self):
        """Two entities that would relate, in different tenants. An inferred
        edge between them would be a cross-tenant read wearing a computation
        as a disguise."""
        query = await stored(
            entity("ours", year(1900)), entity("theirs", year(1950), tenant=OTHER_TENANT)
        )
        assert await query.relations_in_interval(TENANT) == []


class TestTimeline:
    async def test_entities_come_back_in_time_order(self):
        query = await stored(
            entity("third", year(2000)), entity("first", year(1900)), entity("second", year(1950))
        )
        assert [e.name for e in await query.timeline(TENANT)] == ["first", "second", "third"]

    async def test_an_open_lower_bound_sorts_before_every_dated_entity(self):
        """ "before 1900" has no start, so it cannot be ordered by one. An
        implementation substituting `datetime.min` gets this right and gets
        year-1 dates wrong; one substituting "today" gets it backwards."""
        query = await stored(
            entity(
                "ancient",
                TemporalExtent(start_date=utc(1, 1, 1), precision=DatePrecision.YEAR),
            ),
            entity(
                "unbounded",
                TemporalExtent(
                    start_date=utc(1900, 1, 1),
                    precision=DatePrecision.YEAR,
                    uncertainty=UncertaintyMarker.BEFORE,
                ),
            ),
        )
        assert [e.name for e in await query.timeline(TENANT)] == ["unbounded", "ancient"]

    async def test_an_open_upper_bound_sorts_last_among_equal_starts(self):
        query = await stored(
            entity("bounded", year(1900)),
            entity(
                "onwards",
                TemporalExtent(
                    start_date=utc(1899, 1, 1),
                    precision=DatePrecision.YEAR,
                    uncertainty=UncertaintyMarker.AFTER,
                ),
            ),
        )
        assert [e.name for e in await query.timeline(TENANT)] == ["bounded", "onwards"]

    async def test_entities_sharing_an_extent_are_ordered_stably_by_id(self):
        """Three things in 1066. Without the id tie-break their order is
        whatever the store handed back, which the port does not promise to
        keep stable across adapters."""
        same = [entity(f"e{n}", year(1066)) for n in range(3)]
        query = await stored(*same)
        first = await query.timeline(TENANT)
        assert [e.id for e in first] == sorted(e.id for e in same)
        assert await query.timeline(TENANT) == first

    async def test_undated_entities_are_absent_from_the_timeline(self):
        query = await stored(entity("dated", year(1900)), entity("undated"))
        assert [e.name for e in await query.timeline(TENANT)] == ["dated"]

    async def test_a_window_narrows_the_timeline(self):
        query = await stored(entity("in", year(1950)), entity("out", year(2050)))
        found = await query.timeline(TENANT, interval=Bounds(utc(1900, 1, 1), utc(2000, 1, 1)))
        assert [e.name for e in found] == ["in"]

    async def test_an_empty_tenant_has_an_empty_timeline(self):
        assert await (await stored()).timeline(TENANT) == []


class TestRelationsInInterval:
    async def test_relations_are_inferred_over_what_the_window_holds(self):
        query = await stored(entity("a", year(1900)), entity("b", year(1950)))
        (relation,) = await query.relations_in_interval(TENANT)
        assert relation.relation is TemporalRelation.BEFORE
        assert (relation.source_name, relation.target_name) == ("a", "b")

    async def test_an_entity_outside_the_window_takes_no_part(self):
        query = await stored(
            entity("a", year(1900)), entity("b", year(1950)), entity("far", year(2500))
        )
        relations = await query.relations_in_interval(
            TENANT, Bounds(utc(1800, 1, 1), utc(2000, 1, 1))
        )
        assert all("far" not in (r.source_name, r.target_name) for r in relations)

    async def test_nothing_is_written_back_to_the_store(self):
        """Computed on read. If this ever starts failing, an inferred edge has
        acquired a write path and can now disagree with its own inputs."""
        store = InMemoryGraphStore()
        await store.upsert_entities([entity("a", year(1900)), entity("b", year(1950))])
        before = await store.get_relationships_for(
            [e.id for e in await store.find_entities(TENANT)], TENANT
        )
        await TemporalQuery(store).relations_in_interval(TENANT)
        after = await store.get_relationships_for(
            [e.id for e in await store.find_entities(TENANT)], TENANT
        )
        assert before == after == []


class TestPaging:
    async def test_a_tenant_larger_than_one_page_is_fully_scanned(self):
        entities = [entity(f"e{n}", year(1900 + n)) for n in range(25)]
        store = InMemoryGraphStore()
        await store.upsert_entities(entities)
        query = TemporalQuery(store, page_size=4)
        assert len(await query.timeline(TENANT)) == 25

    async def test_a_tenant_exactly_filling_a_page_terminates(self):
        """The off-by-one: a final page of exactly `page_size` is followed by
        an empty one, and an implementation exiting only on a *short* page
        must still handle that."""
        entities = [entity(f"e{n}", year(1900 + n)) for n in range(8)]
        store = InMemoryGraphStore()
        await store.upsert_entities(entities)
        assert len(await TemporalQuery(store, page_size=4).timeline(TENANT)) == 8

    async def test_a_cursor_that_does_not_advance_fails_rather_than_hangs(self):
        """A hang in CI reads as infrastructure trouble and gets retried
        rather than investigated, so the bound is what makes this a bug
        report. The stalled store is a real object, not a mock: it is a
        `GraphStore` whose `find_entities` ignores `after`, which is a
        plausible adapter defect rather than an invented one."""

        class IgnoresTheCursor(InMemoryGraphStore):
            async def find_entities(self, tenant_id, **kwargs):
                kwargs.pop("after", None)
                return await super().find_entities(tenant_id, **kwargs)

        store = IgnoresTheCursor()
        await store.upsert_entities([entity(f"e{n}", year(1900 + n)) for n in range(4)])
        with pytest.raises(CursorStalledError, match="not advancing"):
            await TemporalQuery(store, page_size=2).timeline(TENANT)

    async def test_a_page_size_that_would_never_advance_is_refused(self):
        with pytest.raises(ValueError, match="page_size"):
            TemporalQuery(InMemoryGraphStore(), page_size=0)


class TestMutationIsolation:
    async def test_mutating_a_returned_entity_does_not_change_the_store(self):
        """Every read method gets this. A shallow copy passes every
        behavioural test and is wrong only afterwards, which is why no
        assertion about the returned value can see it."""
        store = InMemoryGraphStore()
        await store.upsert_entities([entity("original", year(1900))])
        query = TemporalQuery(store)

        (found,) = await query.timeline(TENANT)
        found.properties["injected"] = True
        if found.temporal is not None:
            found.temporal.original_text = "tampered"

        (again,) = await query.timeline(TENANT)
        assert "injected" not in again.properties
        assert again.temporal is not None
        assert again.temporal.original_text != "tampered"
