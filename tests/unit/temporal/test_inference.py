"""Temporal relations between entities, computed rather than stored."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from redstring.domain.entity import Entity
from redstring.domain.interval import Bounds, TemporalRelation, relate_bounds
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.temporal import DatePrecision, TemporalExtent, UncertaintyMarker
from redstring.temporal.inference import (
    _CANONICAL,
    INFERRED_RELATIONS,
    InferredRelation,
    infer_relations,
)

#: A fixed observation instant. Never `datetime.now(UTC)`: a fixture that
#: varies per run makes any comparison on `observed_at` non-deterministic.
OBSERVED = datetime(2026, 2, 15, 11, 7, tzinfo=UTC)

TENANT = uuid4()


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)  # type: ignore[arg-type]


def dated(name: str, extent: TemporalExtent | None) -> Entity:
    return Entity(
        id=uuid4(),
        tenant_id=TENANT,
        name=name,
        normalized_name=name.lower(),
        entity_type="Event",
        temporal=extent,
        provenance=Provenance(
            observed_at=OBSERVED,
            extraction_method=ExtractionMethod.PATTERN,
            confidence=0.9,
            source_id="doc-1",
        ),
    )


def year(y: int) -> TemporalExtent:
    return TemporalExtent(start_date=utc(y, 1, 1), precision=DatePrecision.YEAR)


def span(first: int, last: int) -> TemporalExtent:
    return TemporalExtent(
        start_date=utc(first, 1, 1), end_date=utc(last, 1, 1), precision=DatePrecision.YEAR
    )


class TestWhatIsInferred:
    def test_an_earlier_event_precedes_a_later_one(self):
        first, second = dated("first", year(1900)), dated("second", year(1950))
        (relation,) = infer_relations([first, second])
        assert relation.relation is TemporalRelation.BEFORE
        assert relation.source_entity_id == first.id
        assert relation.target_entity_id == second.id

    def test_a_month_is_during_the_year_that_holds_it(self):
        whole = dated("the year", year(2023))
        part = dated(
            "the month", TemporalExtent(start_date=utc(2023, 3, 1), precision=DatePrecision.MONTH)
        )
        (relation,) = infer_relations([whole, part])
        assert relation.relation is TemporalRelation.CONTAINS
        assert relation.source_entity_id == whole.id

    def test_overlapping_spans_overlap(self):
        (relation,) = infer_relations([dated("a", span(1900, 1950)), dated("b", span(1940, 1990))])
        assert relation.relation is TemporalRelation.OVERLAPS

    def test_every_pair_of_dated_entities_yields_exactly_one_relation(self):
        entities = [dated(str(y), year(y)) for y in (1900, 1950, 2000, 2050)]
        assert len(infer_relations(entities)) == 6

    def test_undated_entities_take_no_part(self):
        entities = [dated("a", year(1900)), dated("b", None), dated("c", None)]
        assert len(infer_relations(entities)) == 0

    def test_an_entity_with_only_a_sequence_position_is_not_dated(self):
        """A sequence position orders events; it does not place them in time,
        and no interval comparison applies to it."""
        entities = [dated("a", year(1900)), dated("b", TemporalExtent(sequence_position=1))]
        assert infer_relations(entities) == []

    def test_nothing_relates_to_itself(self):
        entity = dated("a", year(1900))
        assert infer_relations([entity, entity]) == []


class TestDirectionIsCanonical:
    """One edge per pair, always the same way round. Two relations per pair --
    `a BEFORE b` and `b AFTER a` -- would double the output and let a caller
    counting edges get a different answer depending on input order."""

    def test_the_pair_order_of_the_input_does_not_change_the_output(self):
        first, second = dated("first", year(1950)), dated("second", year(1900))
        forwards = infer_relations([first, second])
        backwards = infer_relations([second, first])
        assert forwards == backwards

    def test_the_earlier_entity_is_the_source(self):
        later, earlier = dated("later", year(1950)), dated("earlier", year(1900))
        (relation,) = infer_relations([later, earlier])
        assert relation.source_entity_id == earlier.id
        assert relation.relation is TemporalRelation.BEFORE

    def test_no_inverse_relation_is_ever_emitted(self):
        entities = [dated(str(y), year(y)) for y in (1900, 1950, 2000)]
        relations = infer_relations(entities)
        assert all(r.relation is not TemporalRelation.AFTER for r in relations)
        assert all(r.relation is not TemporalRelation.DURING for r in relations)

    def test_containment_survives_when_both_extents_start_together(self):
        """The pair the sort gets wrong. "2023" and "2023-2025" begin at the
        same instant, so ordering by lower-then-upper bound puts the *shorter*
        one first -- and `relate` from the shorter to the longer is DURING,
        which the default filter drops. The edge vanished entirely.

        Direction must come from the relation, not from the sort order."""
        year = dated(
            "2023", TemporalExtent(start_date=utc(2023, 1, 1), precision=DatePrecision.YEAR)
        )
        span_of_years = dated(
            "2023-2025",
            TemporalExtent(
                start_date=utc(2023, 1, 1),
                end_date=utc(2025, 1, 1),
                precision=DatePrecision.YEAR,
            ),
        )
        for order in ([year, span_of_years], [span_of_years, year]):
            (relation,) = infer_relations(order)
            assert relation.relation is TemporalRelation.CONTAINS
            assert relation.source_entity_id == span_of_years.id
            assert relation.target_entity_id == year.id

    def test_containment_survives_when_both_extents_end_together(self):
        """The mirror case, which the sort happens to get right -- included so
        that a fix which merely reverses the sort cannot pass."""
        longer = dated("longer", span(2020, 2025))
        shorter = dated("shorter", span(2023, 2025))
        for order in ([longer, shorter], [shorter, longer]):
            (relation,) = infer_relations(order)
            assert relation.relation is TemporalRelation.CONTAINS
            assert relation.source_entity_id == longer.id

    def test_a_month_opening_its_year_still_relates_to_that_year(self):
        """Ordinary parser output on both sides: `_parse_partial` produces
        both of these, and January is exactly the month whose lower bound
        coincides with its year's."""
        january = dated(
            "January 2023",
            TemporalExtent(start_date=utc(2023, 1, 1), precision=DatePrecision.MONTH),
        )
        whole_year = dated(
            "2023", TemporalExtent(start_date=utc(2023, 1, 1), precision=DatePrecision.YEAR)
        )
        (relation,) = infer_relations([january, whole_year])
        assert relation.relation is TemporalRelation.CONTAINS
        assert relation.source_entity_id == whole_year.id

    def test_every_related_pair_of_dated_entities_yields_an_edge(self):
        """The invariant the filter default rests on: no pair that `relate`
        calls related may be dropped by canonicalisation. Stated over a set
        containing coincident starts, coincident ends and nesting."""
        entities = [
            dated("2023", TemporalExtent(start_date=utc(2023, 1, 1), precision=DatePrecision.YEAR)),
            dated("2023-2025", span(2023, 2025)),
            dated("2020-2025", span(2020, 2025)),
            dated("2050", year(2050)),
        ]
        expected = len(entities) * (len(entities) - 1) // 2
        assert len(infer_relations(entities)) == expected

    def test_two_entities_with_identical_extents_are_ordered_by_id(self):
        """`EQUALS` has no earlier side, so the tie-break must be something
        total or the output depends on input order."""
        pair = [dated("a", year(1900)), dated("b", year(1900))]
        (relation,) = infer_relations(pair)
        assert relation.source_entity_id == min(e.id for e in pair)
        assert infer_relations(pair) == infer_relations(list(reversed(pair)))


class TestTheInvariantIsStructural:
    """`INFERRED_RELATIONS` promises that `AFTER` and `DURING` never come out.
    These assert it against the canonicalisation itself rather than against
    what the sort happens to feed it.

    Why that matters here specifically: after sorting, `relate_bounds` cannot
    return `AFTER` at all -- it would require an interval whose upper bound is
    below its own lower bound. So deleting the `AFTER` entry from `_CANONICAL`
    passes every behavioural test in this file. That is the same species of
    reasoning that produced the `DURING` defect: an invariant resting on an
    argument about sort order rather than on the code. The entry stays, and
    these tests make it live."""

    def test_the_inverse_map_covers_every_relation_the_default_set_excludes(self):
        excluded = set(TemporalRelation) - set(INFERRED_RELATIONS)
        assert set(_CANONICAL) == excluded

    def test_the_inverse_map_lands_inside_the_default_set(self):
        assert set(_CANONICAL.values()) <= set(INFERRED_RELATIONS)

    @pytest.mark.parametrize(("relation", "inverse"), sorted(_CANONICAL.items()))
    def test_each_inverse_matches_what_relate_bounds_says_about_the_swap(self, relation, inverse):
        """Grounded against `relate_bounds`, so this is not the map asserting
        itself: find a real pair of intervals standing in `relation`, and
        check the reversed pair genuinely stands in `inverse`."""
        pairs = {
            TemporalRelation.AFTER: (
                Bounds(utc(2020, 1, 1), utc(2021, 1, 1)),
                Bounds(utc(2000, 1, 1), utc(2001, 1, 1)),
            ),
            TemporalRelation.DURING: (
                Bounds(utc(2000, 1, 1), utc(2001, 1, 1)),
                Bounds(utc(2000, 1, 1), utc(2010, 1, 1)),
            ),
        }
        first, second = pairs[relation]
        assert relate_bounds(first, second) is relation
        assert relate_bounds(second, first) is inverse

    @given(
        starts=st.lists(st.integers(1500, 2500), min_size=2, max_size=5),
        widths=st.lists(st.integers(0, 40), min_size=2, max_size=5),
    )
    @settings(max_examples=300)
    def test_no_excluded_relation_ever_reaches_the_output(self, starts, widths):
        """Over extents that deliberately collide at their endpoints -- shared
        starts and shared ends are what the widths of 0 and the repeated years
        manufacture."""
        entities = [
            dated(
                f"e{n}",
                TemporalExtent(
                    start_date=utc(start, 1, 1),
                    end_date=utc(start + width, 1, 1) if width else None,
                    precision=DatePrecision.YEAR,
                ),
            )
            for n, (start, width) in enumerate(zip(starts, widths, strict=False))
        ]
        relations = infer_relations(entities, relations=set(TemporalRelation))
        assert not {r.relation for r in relations} & set(_CANONICAL)

    @given(
        starts=st.lists(st.integers(1500, 2500), min_size=2, max_size=5),
        widths=st.lists(st.integers(0, 40), min_size=2, max_size=5),
    )
    @settings(max_examples=300)
    def test_the_default_set_loses_no_pair_that_relate_calls_related(self, starts, widths):
        """The property the defect violated. Asking for everything and asking
        for the default set must return the same number of edges, because the
        default set is exactly what canonicalisation can produce."""
        entities = [
            dated(
                f"e{n}",
                TemporalExtent(
                    start_date=utc(start, 1, 1),
                    end_date=utc(start + width, 1, 1) if width else None,
                    precision=DatePrecision.YEAR,
                ),
            )
            for n, (start, width) in enumerate(zip(starts, widths, strict=False))
        ]
        distinct = len({e.id for e in entities})
        assert len(infer_relations(entities)) == distinct * (distinct - 1) // 2
        assert infer_relations(entities) == infer_relations(
            entities, relations=set(TemporalRelation)
        )


class TestNotARelationship:
    """The inferred edge is deliberately not a `Relationship`. It must be
    impossible to hand to `GraphStore.upsert_relationship` by accident, and a
    caller must not be able to mistake one for something the log recorded."""

    def test_an_inferred_relation_is_not_a_relationship(self):
        from redstring.domain.relationship import Relationship

        (relation,) = infer_relations([dated("a", year(1900)), dated("b", year(1950))])
        assert not isinstance(relation, Relationship)

    def test_it_carries_no_id_that_could_be_persisted(self):
        (relation,) = infer_relations([dated("a", year(1900)), dated("b", year(1950))])
        assert not hasattr(relation, "id")

    def test_it_says_what_it_was_derived_from(self):
        first = dated("first", year(1900))
        second = dated("second", span(1950, 1960))
        (relation,) = infer_relations([first, second])
        assert relation.source_extent == first.temporal
        assert relation.target_extent == second.temporal


class TestOpenBoundsReachInference:
    def test_an_open_bound_survives_the_trip_through_entities(self):
        before = dated(
            "before",
            TemporalExtent(
                start_date=utc(1900, 1, 1),
                precision=DatePrecision.YEAR,
                uncertainty=UncertaintyMarker.BEFORE,
            ),
        )
        later = dated("later", year(1950))
        (relation,) = infer_relations([before, later])
        assert relation.relation is TemporalRelation.BEFORE
        assert relation.source_entity_id == before.id

    def test_an_open_bound_can_contain_a_closed_one(self):
        after = dated(
            "after",
            TemporalExtent(
                start_date=utc(1900, 1, 1),
                precision=DatePrecision.YEAR,
                uncertainty=UncertaintyMarker.AFTER,
            ),
        )
        inside = dated("inside", year(1950))
        (relation,) = infer_relations([after, inside])
        assert relation.relation is TemporalRelation.CONTAINS
        assert relation.source_entity_id == after.id


class TestFiltering:
    def test_only_the_asked_for_relations_come_back(self):
        entities = [dated("a", year(1900)), dated("b", span(1890, 1910)), dated("c", year(2000))]
        only_contains = infer_relations(entities, relations={TemporalRelation.CONTAINS})
        assert only_contains
        assert all(r.relation is TemporalRelation.CONTAINS for r in only_contains)

    def test_asking_for_nothing_gets_nothing(self):
        entities = [dated("a", year(1900)), dated("b", year(2000))]
        assert infer_relations(entities, relations=set()) == []

    def test_a_pair_limit_bounds_a_quadratic_computation(self):
        """Inference is O(n^2) in dated entities, so a tenant-wide call on a
        large graph is a hang rather than a slow answer. The cap fails loudly
        instead."""
        entities = [dated(str(y), year(y)) for y in range(1900, 1910)]
        with pytest.raises(ValueError, match="max_pairs"):
            infer_relations(entities, max_pairs=10)

    def test_the_cap_is_compared_against_the_actual_pair_count(self):
        """Ten entities is forty-five pairs, and the message says so.

        Asserting only that *some* limit was exceeded lets the count itself be
        wrong: cosmic-ray rewrote `n * (n - 1) // 2` several ways, and every
        version still exceeded a cap of ten, so nothing failed. The exact
        number is what distinguishes them."""
        entities = [dated(str(y), year(y)) for y in range(1900, 1910)]
        with pytest.raises(ValueError, match=r"10 dated entities is 45 pairs"):
            infer_relations(entities, max_pairs=10)

    def test_the_cap_is_a_maximum_rather_than_a_strict_bound(self):
        """Exactly at the cap is allowed; one below it is not. Off-by-one on
        the comparison is invisible without both halves."""
        entities = [dated(str(y), year(y)) for y in range(1900, 1910)]
        assert infer_relations(entities, max_pairs=45)
        with pytest.raises(ValueError, match="max_pairs"):
            infer_relations(entities, max_pairs=44)

    def test_a_single_dated_entity_is_no_pairs_and_never_trips_the_cap(self):
        """`n * (n - 1) // 2` must be 0 here, not negative and not 1. A
        formula that comes out negative would pass any cap while being
        nonsense, and the empty case is where that shows."""
        assert infer_relations([dated("alone", year(1900))], max_pairs=0) == []
        assert infer_relations([], max_pairs=0) == []


class TestInferredRelationIsOrderable:
    def test_relations_are_comparable_so_a_result_can_be_sorted_stably(self):
        entities = [dated(str(y), year(y)) for y in (2000, 1900, 1950)]
        relations = infer_relations(entities)
        assert sorted(relations) == relations

    def test_no_pair_appears_twice(self):
        """Checked on the endpoint ids rather than by hashing the relation:
        `TemporalExtent` is a pydantic model and so unhashable, which makes
        `InferredRelation` unhashable too. Comparison is what the sort needs
        and comparison is what it has."""
        entities = [dated(str(y), year(y)) for y in (2000, 1900, 1950)]
        pairs = [(r.source_entity_id, r.target_entity_id) for r in infer_relations(entities)]
        assert len(set(pairs)) == len(pairs)

    def test_the_named_tuple_shape_is_what_a_caller_unpacks(self):
        (relation,) = infer_relations([dated("a", year(1900)), dated("b", year(1950))])
        assert isinstance(relation, InferredRelation)
