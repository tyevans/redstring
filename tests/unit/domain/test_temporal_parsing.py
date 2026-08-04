"""Text to `TemporalExtent`, and the reference date that keeps it replayable."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from kg_builder.domain.temporal import DatePrecision, TemporalExtent, UncertaintyMarker
from kg_builder.domain.temporal_parsing import (
    AmbiguousReferenceDateError,
    parse_temporal,
    render_temporal,
    widen,
)

REF = datetime(2020, 6, 15, tzinfo=UTC)

MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)  # type: ignore[arg-type]


class TestTheReferenceDateIsAParameter:
    """The replay hazard. `date.today()` inside the parser would make a
    re-extraction of the same document produce a different graph."""

    def test_a_relative_expression_resolves_against_the_reference_date(self):
        parsed = parse_temporal("last year", reference_date=utc(2020, 6, 15))
        assert parsed is not None
        assert parsed.start_date is not None
        assert parsed.start_date.year == 2019

    def test_two_reference_dates_give_two_answers_for_one_text(self):
        earlier = parse_temporal("last year", reference_date=utc(2020, 6, 15))
        later = parse_temporal("last year", reference_date=utc(2031, 6, 15))
        assert earlier is not None
        assert later is not None
        assert earlier.start_date != later.start_date

    def test_a_relative_expression_without_a_reference_date_is_refused(self):
        with pytest.raises(AmbiguousReferenceDateError, match="last year"):
            parse_temporal("last year", reference_date=None)

    def test_an_absolute_expression_needs_no_reference_date(self):
        assert parse_temporal("14 July 1789", reference_date=None) == parse_temporal(
            "14 July 1789", reference_date=REF
        )

    def test_a_date_missing_its_year_is_refused_rather_than_given_this_year(self):
        """`dateutil` fills an absent component from today by default, which is
        the same hazard wearing a different hat -- it is not spelled
        `date.today()` anywhere in our source."""
        with pytest.raises(AmbiguousReferenceDateError):
            parse_temporal("March 15", reference_date=None)

    def test_a_reference_date_must_be_timezone_aware(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            parse_temporal("last year", reference_date=datetime(2020, 6, 15))


class TestPrecision:
    """Precision never claims more than the text supports."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("2023", DatePrecision.YEAR),
            ("the 1850s", DatePrecision.YEAR),
            ("19th century", DatePrecision.YEAR),
            ("March 2023", DatePrecision.MONTH),
            ("Q3 2024", DatePrecision.MONTH),
            ("15 March 2024", DatePrecision.DAY),
            ("March 15, 2024", DatePrecision.DAY),
            ("3pm on 15 March 2024", DatePrecision.HOUR),
            ("2024-03-15T14:30:00", DatePrecision.MINUTE),
        ],
    )
    def test_precision_matches_what_the_text_states(self, text, expected):
        parsed = parse_temporal(text, reference_date=REF)
        assert parsed is not None
        assert parsed.precision is expected

    @pytest.mark.parametrize("text", ["2024-03-15T14:00:00", "2024-03-15T14:00"])
    def test_precision_is_what_the_text_writes_not_what_its_digits_are(self, text):
        """A zero minute field is still a written minute field. Reading `:00`
        as hour precision would make an event's granularity depend on the
        moment it happened to fall on."""
        parsed = parse_temporal(text, reference_date=REF)
        assert parsed is not None
        assert parsed.precision is DatePrecision.MINUTE

    @given(year=st.integers(min_value=1000, max_value=2999))
    def test_a_bare_year_never_yields_a_finer_precision(self, year):
        parsed = parse_temporal(str(year), reference_date=REF)
        assert parsed is not None
        assert parsed.precision is DatePrecision.YEAR
        assert parsed.start_date == utc(year, 1, 1)


class TestUncertainty:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("circa 1850", UncertaintyMarker.CIRCA),
            ("c. 1850", UncertaintyMarker.CIRCA),
            ("around 1850", UncertaintyMarker.APPROXIMATE),
            ("approximately 1850", UncertaintyMarker.APPROXIMATE),
            ("before 1850", UncertaintyMarker.BEFORE),
            ("prior to 1850", UncertaintyMarker.BEFORE),
            ("after 1850", UncertaintyMarker.AFTER),
            ("since 1850", UncertaintyMarker.AFTER),
            ("1850", UncertaintyMarker.EXACT),
        ],
    )
    def test_markers_are_detected(self, text, expected):
        parsed = parse_temporal(text, reference_date=REF)
        assert parsed is not None
        assert parsed.uncertainty is expected

    def test_a_marker_does_not_disturb_the_date_under_it(self):
        assert parse_temporal("circa 1850", reference_date=REF).start_date == utc(1850, 1, 1)

    def test_a_historical_period_is_approximate_even_unmarked(self):
        parsed = parse_temporal("19th century", reference_date=REF)
        assert parsed is not None
        assert parsed.uncertainty is UncertaintyMarker.APPROXIMATE


class TestRanges:
    def test_a_year_range(self):
        parsed = parse_temporal("1914-1918", reference_date=REF)
        assert parsed is not None
        assert parsed.start_date == utc(1914, 1, 1)
        assert parsed.end_date == utc(1918, 1, 1)
        assert parsed.precision is DatePrecision.YEAR

    def test_a_month_range_within_one_year(self):
        parsed = parse_temporal("January to March 2024", reference_date=REF)
        assert parsed is not None
        assert parsed.start_date == utc(2024, 1, 1)
        assert parsed.end_date == utc(2024, 3, 1)
        assert parsed.precision is DatePrecision.MONTH

    def test_a_century_is_a_range(self):
        parsed = parse_temporal("19th century", reference_date=REF)
        assert parsed is not None
        assert parsed.start_date == utc(1801, 1, 1)
        assert parsed.end_date == utc(1900, 1, 1)


class TestUnparseable:
    @pytest.mark.parametrize("text", ["", "   ", "the quick brown fox", "x" * 600])
    def test_nothing_is_not_a_date(self, text):
        assert parse_temporal(text, reference_date=REF) is None


# --- Properties -------------------------------------------------------------

#: Kept away from the two-digit years `dateutil` reads as an abbreviation, and
#: away from year 1 where widening a bound underflows `datetime.min`.
years = st.integers(min_value=1000, max_value=2999)


#: Texts that are *meant* to parse. `st.text()` alone would be vacuous here:
#: essentially no random string is a date, so every example would be filtered
#: out by `assume` and the property would assert nothing.
date_texts = st.one_of(
    years.map(str),
    st.tuples(years, st.integers(1, 12)).map(lambda p: f"{MONTH_NAMES[p[1] - 1]} {p[0]}"),
    st.tuples(years, st.integers(1, 12), st.integers(1, 28)).map(
        lambda p: f"{p[2]} {MONTH_NAMES[p[1] - 1]} {p[0]}"
    ),
    st.tuples(years, st.integers(1, 200)).map(lambda p: f"{p[0]}-{min(p[0] + p[1], 2999)}"),
    st.tuples(years, st.integers(1, 4)).map(lambda p: f"Q{p[1]} {p[0]}"),
    st.integers(1, 21).map(lambda c: f"{c}th century"),
    st.integers(100, 299).map(lambda d: f"the {d}0s"),
    st.sampled_from(["last year", "3 days ago", "next month", "yesterday", "in 2 weeks"]),
).flatmap(
    lambda text: st.sampled_from(
        ["", "circa ", "around ", "before ", "after ", "approximately "]
    ).map(lambda prefix: prefix + text)
)


class TestProperties:
    @given(text=date_texts)
    @settings(max_examples=300, deadline=None)
    def test_a_parsed_extent_is_ordered_and_aware(self, text):
        parsed = parse_temporal(text, reference_date=REF)
        assume(parsed is not None)
        assert parsed.start_date is not None
        assert parsed.start_date.tzinfo is not None
        if parsed.end_date is not None:
            assert parsed.end_date.tzinfo is not None
            assert parsed.start_date <= parsed.end_date

    @given(text=st.one_of(date_texts, st.text(max_size=60)))
    @settings(max_examples=300, deadline=None)
    def test_parsing_is_a_function_of_text_and_reference_date_alone(self, text):
        """Nothing process-local, nothing wall-clock. Two calls, one answer."""
        try:
            first = parse_temporal(text, reference_date=REF)
        except AmbiguousReferenceDateError:  # pragma: no cover - a reference date was given
            pytest.fail("a supplied reference date can never be ambiguous")
        assert first == parse_temporal(text, reference_date=REF)

    @given(
        start=years,
        span=st.integers(min_value=1, max_value=200),
        uncertainty=st.sampled_from(
            [
                UncertaintyMarker.EXACT,
                UncertaintyMarker.CIRCA,
                UncertaintyMarker.APPROXIMATE,
                UncertaintyMarker.BEFORE,
                UncertaintyMarker.AFTER,
            ]
        ),
    )
    def test_a_rendered_year_extent_parses_back_to_itself(self, start, span, uncertainty):
        assume(start + span <= 2999)
        extent = TemporalExtent(
            start_date=utc(start, 1, 1),
            precision=DatePrecision.YEAR,
            uncertainty=uncertainty,
        )
        rendered = render_temporal(extent)
        assert rendered is not None, "a year extent must be renderable"
        round_tripped = parse_temporal(rendered, reference_date=REF)
        assert round_tripped is not None
        assert round_tripped.model_dump(exclude={"original_text"}) == extent.model_dump(
            exclude={"original_text"}
        )

    @given(start=years, span=st.integers(min_value=1, max_value=200))
    def test_a_rendered_year_range_parses_back_to_itself(self, start, span):
        assume(start + span <= 2999)
        extent = TemporalExtent(
            start_date=utc(start, 1, 1),
            end_date=utc(start + span, 1, 1),
            precision=DatePrecision.YEAR,
            uncertainty=UncertaintyMarker.EXACT,
        )
        rendered = render_temporal(extent)
        assert rendered is not None, "a year range must be renderable"
        round_tripped = parse_temporal(rendered, reference_date=REF)
        assert round_tripped is not None
        assert round_tripped.model_dump(exclude={"original_text"}) == extent.model_dump(
            exclude={"original_text"}
        )

    @given(year=years, month=st.integers(min_value=1, max_value=12))
    def test_a_rendered_month_extent_parses_back_to_itself(self, year, month):
        extent = TemporalExtent(
            start_date=utc(year, month, 1),
            precision=DatePrecision.MONTH,
            uncertainty=UncertaintyMarker.EXACT,
        )
        rendered = render_temporal(extent)
        assert rendered is not None, "a month extent must be renderable"
        round_tripped = parse_temporal(rendered, reference_date=REF)
        assert round_tripped is not None
        assert round_tripped.model_dump(exclude={"original_text"}) == extent.model_dump(
            exclude={"original_text"}
        )

    @given(
        year=years,
        month=st.integers(min_value=1, max_value=12),
        day=st.integers(min_value=1, max_value=28),
    )
    def test_a_rendered_day_extent_parses_back_to_itself(self, year, month, day):
        extent = TemporalExtent(
            start_date=utc(year, month, day),
            precision=DatePrecision.DAY,
            uncertainty=UncertaintyMarker.EXACT,
        )
        rendered = render_temporal(extent)
        assert rendered is not None, "a day extent must be renderable"
        round_tripped = parse_temporal(rendered, reference_date=REF)
        assert round_tripped is not None
        assert round_tripped.model_dump(exclude={"original_text"}) == extent.model_dump(
            exclude={"original_text"}
        )

    def test_render_declines_what_it_cannot_render_faithfully(self):
        assert render_temporal(TemporalExtent()) is None
        assert render_temporal(TemporalExtent(sequence_position=3)) is None


class TestWiden:
    """`widen` is the inverse of the flooring the partial-date strategies do,
    and `domain.interval` is its only caller. Half-open: the value returned is
    the first instant *after* the unit, never the last instant inside it."""

    @pytest.mark.parametrize(
        ("moment", "precision", "expected"),
        [
            (utc(2023, 7, 9, 14, 35, 12), DatePrecision.YEAR, utc(2024, 1, 1)),
            (utc(2023, 7, 9, 14, 35, 12), DatePrecision.MONTH, utc(2023, 8, 1)),
            (utc(2023, 7, 9, 14, 35, 12), DatePrecision.DAY, utc(2023, 7, 10)),
            (utc(2023, 7, 9, 14, 35, 12), DatePrecision.HOUR, utc(2023, 7, 9, 15)),
            (utc(2023, 7, 9, 14, 35, 12), DatePrecision.MINUTE, utc(2023, 7, 9, 14, 36)),
        ],
    )
    def test_widen_reaches_the_start_of_the_next_unit(self, moment, precision, expected):
        assert widen(moment, precision) == expected

    @pytest.mark.parametrize(
        ("moment", "precision"),
        [
            (utc(2023, 12, 31, 23, 59, 59), DatePrecision.YEAR),
            (utc(2024, 2, 29), DatePrecision.MONTH),
            (utc(2023, 12, 31), DatePrecision.DAY),
        ],
    )
    def test_widen_crosses_a_boundary_rather_than_overflowing_a_field(self, moment, precision):
        """December + one month is January of the next year, not month 13. A
        `replace(month=month + 1)` implementation passes every mid-year case."""
        assert widen(moment, precision) > moment

    def test_widening_is_idempotent_on_a_floored_moment(self):
        assert widen(utc(2023, 1, 1), DatePrecision.YEAR) == utc(2024, 1, 1)
        assert widen(utc(2023, 12, 1), DatePrecision.MONTH) == utc(2024, 1, 1)

    @given(year=years, month=st.integers(1, 12), day=st.integers(1, 28))
    def test_widen_is_strictly_forward_and_keeps_the_zone(self, year, month, day):
        moment = utc(year, month, day, 6, 30)
        for precision in DatePrecision:
            widened = widen(moment, precision)
            assert widened > moment
            assert widened.tzinfo is moment.tzinfo
