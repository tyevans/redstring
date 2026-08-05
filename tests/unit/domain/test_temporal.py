"""Tests for redstring.domain.temporal."""

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from redstring.domain.temporal import DatePrecision, TemporalExtent, UncertaintyMarker

aware_datetimes = st.datetimes(
    min_value=datetime(1, 1, 1),
    max_value=datetime(9999, 12, 30),
).map(lambda dt: dt.replace(tzinfo=UTC))

naive_datetimes = st.datetimes(
    min_value=datetime(1, 1, 1),
    max_value=datetime(9999, 12, 30),
)


def test_date_precision_members():
    assert DatePrecision.YEAR == "year"
    assert DatePrecision.MONTH == "month"
    assert DatePrecision.DAY == "day"
    assert DatePrecision.HOUR == "hour"
    assert DatePrecision.MINUTE == "minute"


def test_uncertainty_marker_members():
    assert UncertaintyMarker.EXACT == "exact"
    assert UncertaintyMarker.APPROXIMATE == "approximate"
    assert UncertaintyMarker.CIRCA == "circa"
    assert UncertaintyMarker.BEFORE == "before"
    assert UncertaintyMarker.AFTER == "after"
    assert UncertaintyMarker.INFERRED == "inferred"


def test_empty_extent_is_empty():
    assert TemporalExtent().is_empty is True


def test_extent_with_start_date_is_not_empty():
    extent = TemporalExtent(start_date=datetime(2020, 1, 1, tzinfo=UTC))
    assert extent.is_empty is False


def test_extent_without_both_dates_has_no_range():
    extent = TemporalExtent(start_date=datetime(2020, 1, 1, tzinfo=UTC))
    assert extent.has_range is False


def test_extent_with_both_dates_has_range():
    extent = TemporalExtent(
        start_date=datetime(2020, 1, 1, tzinfo=UTC),
        end_date=datetime(2020, 1, 2, tzinfo=UTC),
    )
    assert extent.has_range is True


def test_end_before_start_is_rejected():
    with pytest.raises(ValidationError):
        TemporalExtent(
            start_date=datetime(2020, 1, 2, tzinfo=UTC),
            end_date=datetime(2020, 1, 1, tzinfo=UTC),
        )


def test_end_equal_start_is_accepted():
    same = datetime(2020, 1, 1, tzinfo=UTC)
    extent = TemporalExtent(start_date=same, end_date=same)
    assert extent.has_range is True


def test_naive_start_date_is_rejected():
    with pytest.raises(ValidationError):
        TemporalExtent(start_date=datetime(2020, 1, 1))


def test_naive_end_date_is_rejected():
    with pytest.raises(ValidationError):
        TemporalExtent(end_date=datetime(2020, 1, 1))


def test_naive_publication_date_is_rejected():
    with pytest.raises(ValidationError):
        TemporalExtent(publication_date=datetime(2020, 1, 1))


def test_negative_sequence_position_is_rejected():
    with pytest.raises(ValidationError):
        TemporalExtent(sequence_position=-1)


def test_zero_sequence_position_is_accepted():
    assert TemporalExtent(sequence_position=0).sequence_position == 0


@given(aware_datetimes, aware_datetimes)
def test_date_ordering_property(a, b):
    """For any two aware datetimes, construction succeeds iff end >= start."""
    start, end = (a, b)
    if end >= start:
        extent = TemporalExtent(start_date=start, end_date=end)
        assert extent.end_date >= extent.start_date
    else:
        with pytest.raises(ValidationError):
            TemporalExtent(start_date=start, end_date=end)


@given(naive_datetimes)
def test_any_naive_datetime_is_rejected(dt):
    with pytest.raises(ValidationError):
        TemporalExtent(start_date=dt)


# Drawn as a sorted pair rather than start + timedelta: an offset of up to
# 100000 days added to a start near year 9999 raises OverflowError inside the
# strategy, which is a bug in the test rather than a property of the model.
# Sorting keeps full-range coverage, extremes included, and cannot overflow.
ordered_aware_pairs = st.lists(aware_datetimes, min_size=2, max_size=2).map(sorted)


@given(ordered_aware_pairs)
def test_round_trip_through_model_dump(bounds):
    start, end = bounds
    extent = TemporalExtent(
        start_date=start,
        end_date=end,
        precision=DatePrecision.DAY,
        uncertainty=UncertaintyMarker.APPROXIMATE,
        original_text="sometime",
        sequence_position=3,
        publication_date=start,
    )
    reconstructed = TemporalExtent.model_validate(extent.model_dump())
    assert reconstructed == extent
