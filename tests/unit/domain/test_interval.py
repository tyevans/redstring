"""Interval logic over possibly-open, possibly-imprecise bounds."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from kg_builder.domain.interval import (
    INSTANT,
    Bounds,
    TemporalRelation,
    bounds,
    relate,
    relate_bounds,
)
from kg_builder.domain.temporal import DatePrecision, TemporalExtent, UncertaintyMarker


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)  # type: ignore[arg-type]


def year(y: int) -> TemporalExtent:
    return TemporalExtent(start_date=utc(y, 1, 1), precision=DatePrecision.YEAR)


def month(y: int, m: int) -> TemporalExtent:
    return TemporalExtent(start_date=utc(y, m, 1), precision=DatePrecision.MONTH)


def day(y: int, m: int, d: int) -> TemporalExtent:
    return TemporalExtent(start_date=utc(y, m, d), precision=DatePrecision.DAY)


class TestBounds:
    """Precision widens a bound. This is where "2023" becomes all of 2023."""

    def test_a_bare_year_spans_its_whole_year(self):
        assert bounds(year(2023)) == Bounds(utc(2023, 1, 1), utc(2024, 1, 1))

    def test_a_bare_month_spans_its_whole_month(self):
        assert bounds(month(2023, 12)) == Bounds(utc(2023, 12, 1), utc(2024, 1, 1))

    def test_a_stated_range_is_widened_at_its_far_end_only(self):
        """ "1914-1918" ends when 1918 ends, not when it starts."""
        extent = TemporalExtent(
            start_date=utc(1914, 1, 1), end_date=utc(1918, 1, 1), precision=DatePrecision.YEAR
        )
        assert bounds(extent) == Bounds(utc(1914, 1, 1), utc(1919, 1, 1))

    def test_before_is_open_below(self):
        extent = TemporalExtent(
            start_date=utc(1900, 1, 1),
            precision=DatePrecision.YEAR,
            uncertainty=UncertaintyMarker.BEFORE,
        )
        assert bounds(extent) == Bounds(None, utc(1900, 1, 1))

    def test_after_is_open_above_and_starts_when_the_unit_ends(self):
        """ "after 1900" does not include 1900 itself."""
        extent = TemporalExtent(
            start_date=utc(1900, 1, 1),
            precision=DatePrecision.YEAR,
            uncertainty=UncertaintyMarker.AFTER,
        )
        assert bounds(extent) == Bounds(utc(1901, 1, 1), None)

    def test_an_extent_with_no_dates_has_no_bounds(self):
        assert bounds(TemporalExtent()) is None
        assert bounds(TemporalExtent(sequence_position=2)) is None

    def test_an_unstated_precision_is_read_as_an_instant_not_as_a_day(self):
        """Defaulting a missing precision to DAY would invent a claim the
        extent never made, and would make an exact timestamp swallow a whole
        day's worth of other events."""
        extent = TemporalExtent(start_date=utc(2023, 5, 4, 12, 30))
        # Spelled out rather than written as `start + INSTANT`. Phrasing the
        # expectation in terms of the constant under test makes the assertion
        # true for *any* value of it -- cosmic-ray set `INSTANT` to zero and
        # this test still passed, while the interval it describes became empty.
        assert bounds(extent) == Bounds(utc(2023, 5, 4, 12, 30), utc(2023, 5, 4, 12, 30, 0, 1))
        assert INSTANT.total_seconds() > 0


class TestUncertaintyOtherThanTheOpenOnes:
    """`bounds` branches on `uncertainty`, and only BEFORE and AFTER change
    anything. The other four must fall through to the ordinary closed
    interval -- which no test checked until a mutation run pointed it out.

    `UncertaintyMarker` is a `str` Enum, so `is` mutated to `>=` compares the
    *strings*: "circa" >= "before" is true, and a circa-dated extent silently
    became open-ended in one direction. Every test at the time either left
    `uncertainty` at `None`, skipping the branch entirely, or set exactly the
    marker being tested."""

    @pytest.mark.parametrize(
        "marker",
        [
            UncertaintyMarker.EXACT,
            UncertaintyMarker.CIRCA,
            UncertaintyMarker.APPROXIMATE,
            UncertaintyMarker.INFERRED,
        ],
    )
    def test_a_marker_that_is_not_an_open_bound_leaves_the_interval_closed(self, marker):
        extent = TemporalExtent(
            start_date=utc(2023, 1, 1), precision=DatePrecision.YEAR, uncertainty=marker
        )
        assert bounds(extent) == Bounds(utc(2023, 1, 1), utc(2024, 1, 1))

    def test_an_open_marker_wins_over_a_stated_range(self):
        """A marker and an `end_date` together are a contradiction: the marker
        says open in one direction, the range says closed at both. The marker
        wins and the far endpoint is dropped, rather than the two being
        reconciled into a plausible interval that nothing asserted.

        `parse_temporal` cannot build one of these -- the range strategies run
        before uncertainty is folded in -- so this pins the behaviour for
        hand-built extents."""
        contradictory = TemporalExtent(
            start_date=utc(1900, 1, 1),
            end_date=utc(1950, 1, 1),
            precision=DatePrecision.YEAR,
            uncertainty=UncertaintyMarker.BEFORE,
        )
        assert bounds(contradictory) == Bounds(None, utc(1900, 1, 1))

        onwards = contradictory.model_copy(update={"uncertainty": UncertaintyMarker.AFTER})
        assert bounds(onwards) == Bounds(utc(1901, 1, 1), None)

    def test_a_circa_extent_relates_like_an_exact_one(self):
        circa = TemporalExtent(
            start_date=utc(2023, 1, 1),
            precision=DatePrecision.YEAR,
            uncertainty=UncertaintyMarker.CIRCA,
        )
        assert relate(circa, year(2023)) is TemporalRelation.EQUALS
        assert relate(circa, year(2050)) is TemporalRelation.BEFORE


class TestSharedEndpoints:
    """Two intervals agreeing at one end and differing at the other. Allen
    calls these `starts` and `finishes`; here they collapse into DURING and
    CONTAINS, which is only true if the bound comparisons are inclusive.

    Found by mutation: weakening either `<=` to `<` turns both of these into
    OVERLAPS, and the whole suite passed -- every closed-interval example
    happened to differ at both ends."""

    def test_a_shorter_interval_starting_at_the_same_instant_is_during(self):
        shorter = TemporalExtent(
            start_date=utc(2000, 1, 1), end_date=utc(2005, 1, 1), precision=DatePrecision.YEAR
        )
        longer = TemporalExtent(
            start_date=utc(2000, 1, 1), end_date=utc(2010, 1, 1), precision=DatePrecision.YEAR
        )
        assert relate(shorter, longer) is TemporalRelation.DURING
        assert relate(longer, shorter) is TemporalRelation.CONTAINS

    def test_a_shorter_interval_ending_at_the_same_instant_is_during(self):
        shorter = TemporalExtent(
            start_date=utc(2005, 1, 1), end_date=utc(2010, 1, 1), precision=DatePrecision.YEAR
        )
        longer = TemporalExtent(
            start_date=utc(2000, 1, 1), end_date=utc(2010, 1, 1), precision=DatePrecision.YEAR
        )
        assert relate(shorter, longer) is TemporalRelation.DURING
        assert relate(longer, shorter) is TemporalRelation.CONTAINS

    def test_a_month_starting_its_year_is_during_that_year(self):
        assert relate(month(2023, 1), year(2023)) is TemporalRelation.DURING

    def test_a_month_ending_its_year_is_during_that_year(self):
        assert relate(month(2023, 12), year(2023)) is TemporalRelation.DURING

    def test_open_bounds_sharing_their_finite_end_nest_rather_than_overlap(self):
        wider = TemporalExtent(
            start_date=utc(2000, 1, 1),
            precision=DatePrecision.YEAR,
            uncertainty=UncertaintyMarker.AFTER,
        )
        narrower = TemporalExtent(
            start_date=utc(2001, 1, 1), end_date=utc(2010, 1, 1), precision=DatePrecision.YEAR
        )
        assert relate(narrower, wider) is TemporalRelation.DURING


class TestPrecisionIsNotExtent:
    """The failure-shape table's row, verbatim: a test using only
    day-precision dates cannot tell an implementation that compares
    intervals from one that compares precision."""

    def test_a_year_contains_a_month_inside_it(self):
        assert relate(year(2023), month(2023, 3)) is TemporalRelation.CONTAINS

    def test_a_year_is_not_before_a_month_inside_it(self):
        assert relate(year(2023), month(2023, 3)) is not TemporalRelation.BEFORE

    def test_a_month_is_during_the_year_that_holds_it(self):
        assert relate(month(2023, 3), year(2023)) is TemporalRelation.DURING

    def test_a_coarse_and_a_fine_extent_of_equal_span_are_equal(self):
        """A year at YEAR precision and the same year stated as an explicit
        range are the same interval. An implementation keyed on precision
        calls these different."""
        stated = TemporalExtent(
            start_date=utc(2023, 1, 1), end_date=utc(2023, 12, 1), precision=DatePrecision.MONTH
        )
        assert relate(year(2023), stated) is TemporalRelation.EQUALS

    def test_a_year_precedes_a_month_in_a_later_year(self):
        assert relate(year(2022), month(2023, 3)) is TemporalRelation.BEFORE

    def test_december_of_one_year_precedes_the_next_year(self):
        """A widening that overflows the month field rather than carrying into
        the year gets this backwards."""
        assert relate(month(2022, 12), year(2023)) is TemporalRelation.BEFORE


class TestOpenBounds:
    """`None` is minus infinity in one position and plus infinity in the
    other. An implementation that treats it as one thing everywhere -- or as
    "now" -- passes every closed-interval test."""

    def setup_method(self):
        self.before_1900 = TemporalExtent(
            start_date=utc(1900, 1, 1),
            precision=DatePrecision.YEAR,
            uncertainty=UncertaintyMarker.BEFORE,
        )
        self.after_2000 = TemporalExtent(
            start_date=utc(2000, 1, 1),
            precision=DatePrecision.YEAR,
            uncertainty=UncertaintyMarker.AFTER,
        )

    def test_open_below_precedes_open_above_when_they_do_not_meet(self):
        assert relate(self.before_1900, self.after_2000) is TemporalRelation.BEFORE
        assert relate(self.after_2000, self.before_1900) is TemporalRelation.AFTER

    def test_open_below_contains_everything_earlier(self):
        assert relate(self.before_1900, year(1850)) is TemporalRelation.CONTAINS
        assert relate(year(1850), self.before_1900) is TemporalRelation.DURING

    def test_open_above_contains_everything_later(self):
        assert relate(self.after_2000, year(2050)) is TemporalRelation.CONTAINS

    def test_open_below_does_not_contain_something_later(self):
        assert relate(self.before_1900, year(1950)) is TemporalRelation.BEFORE

    def test_open_above_does_not_contain_something_earlier(self):
        assert relate(self.after_2000, year(1950)) is TemporalRelation.AFTER

    def test_an_open_bound_is_not_the_present_day(self):
        """The tempting reading of "after 2000" is "2000 until now". Under it,
        an event in 2200 would fall outside, and this assertion would also
        start failing on its own in the year 2200 rather than at review."""
        assert relate(self.after_2000, year(2200)) is TemporalRelation.CONTAINS

    def test_open_on_both_sides_contains_everything(self):
        everything = Bounds(None, None)
        assert relate_bounds(everything, bounds(year(1850))) is TemporalRelation.CONTAINS
        assert relate_bounds(bounds(year(1850)), everything) is TemporalRelation.DURING
        assert relate_bounds(everything, everything) is TemporalRelation.EQUALS

    def test_two_intervals_open_on_the_same_side_are_ordered_by_the_closed_one(self):
        after_1900 = TemporalExtent(
            start_date=utc(1900, 1, 1),
            precision=DatePrecision.YEAR,
            uncertainty=UncertaintyMarker.AFTER,
        )
        assert relate(after_1900, self.after_2000) is TemporalRelation.CONTAINS
        assert relate(self.after_2000, after_1900) is TemporalRelation.DURING


class TestClosedIntervals:
    def test_adjacent_intervals_do_not_overlap(self):
        """Half-open bounds: 2022 ends at the instant 2023 begins."""
        assert relate(year(2022), year(2023)) is TemporalRelation.BEFORE

    def test_partial_overlap(self):
        first = TemporalExtent(
            start_date=utc(2020, 1, 1), end_date=utc(2023, 1, 1), precision=DatePrecision.YEAR
        )
        second = TemporalExtent(
            start_date=utc(2022, 1, 1), end_date=utc(2025, 1, 1), precision=DatePrecision.YEAR
        )
        assert relate(first, second) is TemporalRelation.OVERLAPS
        assert relate(second, first) is TemporalRelation.OVERLAPS

    def test_identical_days_are_equal(self):
        assert relate(day(2023, 5, 4), day(2023, 5, 4)) is TemporalRelation.EQUALS

    def test_consecutive_days_are_ordered(self):
        assert relate(day(2023, 5, 4), day(2023, 5, 5)) is TemporalRelation.BEFORE
        assert relate(day(2023, 5, 5), day(2023, 5, 4)) is TemporalRelation.AFTER

    def test_an_undated_extent_relates_to_nothing(self):
        assert relate(TemporalExtent(), year(2023)) is None
        assert relate(year(2023), TemporalExtent()) is None
        assert relate(TemporalExtent(), TemporalExtent()) is None


# --- Properties -------------------------------------------------------------

#: Bounded away from `datetime.min`/`max` so that widening cannot overflow, and
#: away from two-digit years for the same reason the parser's strategy is.
_moments = st.datetimes(
    min_value=datetime(1000, 1, 1),
    max_value=datetime(2900, 1, 1),
).map(lambda d: d.replace(tzinfo=UTC))


@st.composite
def extents(draw: st.DrawFn) -> TemporalExtent:
    start = draw(_moments)
    span = draw(st.integers(min_value=0, max_value=3000))
    end = draw(st.one_of(st.none(), st.just(start + timedelta(days=span))))
    return TemporalExtent(
        start_date=start,
        end_date=end,
        precision=draw(st.sampled_from([*DatePrecision, None])),
        uncertainty=draw(st.sampled_from([*UncertaintyMarker, None])),
    )


_INVERSE = {
    TemporalRelation.BEFORE: TemporalRelation.AFTER,
    TemporalRelation.AFTER: TemporalRelation.BEFORE,
    TemporalRelation.DURING: TemporalRelation.CONTAINS,
    TemporalRelation.CONTAINS: TemporalRelation.DURING,
    TemporalRelation.OVERLAPS: TemporalRelation.OVERLAPS,
    TemporalRelation.EQUALS: TemporalRelation.EQUALS,
}


class TestProperties:
    @given(a=extents(), b=extents())
    @settings(max_examples=500)
    def test_every_pair_of_dated_extents_has_exactly_one_relation(self, a, b):
        found = relate(a, b)
        assert found is not None
        assert found in TemporalRelation

    @given(a=extents(), b=extents())
    @settings(max_examples=500)
    def test_reversing_the_arguments_inverts_the_relation(self, a, b):
        """`relate(a, b)` and `relate(b, a)` must be each other's mirror. An
        implementation that returns whichever branch it happened to reach
        first -- the shape the legacy inference had -- fails here."""
        assert relate(b, a) is _INVERSE[relate(a, b)]

    @given(a=extents())
    @settings(max_examples=200)
    def test_an_extent_equals_itself(self, a):
        assert relate(a, a) is TemporalRelation.EQUALS

    @given(a=extents(), b=extents())
    @settings(max_examples=500)
    def test_bounds_are_ordered_and_never_empty(self, a, b):
        """A half-open interval whose upper bound is not strictly above its
        lower is empty, and every relation over an empty interval is
        nonsense."""
        for extent in (a, b):
            found = bounds(extent)
            assert found is not None
            if found.lower is not None and found.upper is not None:
                assert found.upper > found.lower

    @given(a=extents(), b=extents())
    @settings(max_examples=500)
    def test_before_and_after_are_the_only_disjoint_relations(self, a, b):
        first, second = bounds(a), bounds(b)
        assert first is not None
        assert second is not None
        disjoint = relate(a, b) in (TemporalRelation.BEFORE, TemporalRelation.AFTER)
        meets = _intersects(first, second)
        assert disjoint is not meets

    @given(a=extents(), b=extents(), c=extents())
    @settings(max_examples=300)
    def test_before_is_transitive(self, a, b, c):
        if relate(a, b) is TemporalRelation.BEFORE and relate(b, c) is TemporalRelation.BEFORE:
            assert relate(a, c) is TemporalRelation.BEFORE


def _intersects(first: Bounds, second: Bounds) -> bool:
    """Written independently of the implementation, on purpose: a helper that
    called the same private predicate would agree with a bug."""
    lower = max(
        (b for b in (first.lower, second.lower) if b is not None),
        default=None,
    )
    upper = min(
        (b for b in (first.upper, second.upper) if b is not None),
        default=None,
    )
    if lower is None or upper is None:
        return True
    return lower < upper


class TestRelateBoundsDirectly:
    """`relate_bounds` is reachable on its own so the both-open interval --
    which no `TemporalExtent` can produce -- is still covered."""

    @pytest.mark.parametrize(
        ("first", "second", "expected"),
        [
            (Bounds(None, None), Bounds(None, None), TemporalRelation.EQUALS),
            (Bounds(None, utc(2000, 1, 1)), Bounds(None, None), TemporalRelation.DURING),
            (Bounds(utc(2000, 1, 1), None), Bounds(None, None), TemporalRelation.DURING),
            (
                Bounds(None, None),
                Bounds(utc(2000, 1, 1), utc(2001, 1, 1)),
                TemporalRelation.CONTAINS,
            ),
            (
                Bounds(None, utc(2000, 1, 1)),
                Bounds(utc(1999, 1, 1), None),
                TemporalRelation.OVERLAPS,
            ),
        ],
    )
    def test_infinities_compose(self, first, second, expected):
        assert relate_bounds(first, second) is expected
