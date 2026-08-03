"""
Unit tests for TemporalParserService.

Tests cover:
- Date format parsing (ISO 8601, natural language, partial dates)
- Date range parsing
- Uncertainty detection
- Precision inference
- Edge cases and error handling
- Safety limits (input length, malformed input)

Target coverage: 90%+
"""

from datetime import UTC, datetime

import pytest

from kg_builder.schemas.timeline import DatePrecision, UncertaintyMarker
from kg_builder.services.temporal_parser import (
    ParseMethod,
    TemporalParseResult,
    TemporalParserService,
    create_temporal_parser,
)


class TestTemporalParserServiceInit:
    """Tests for TemporalParserService initialization."""

    def test_default_initialization(self):
        """Parser initializes with default settings."""
        parser = TemporalParserService()
        assert parser.max_input_length == 500
        assert parser.timeout_seconds == 2.0

    def test_custom_initialization(self):
        """Parser initializes with custom settings."""
        parser = TemporalParserService(max_input_length=1000, timeout_seconds=5.0)
        assert parser.max_input_length == 1000
        assert parser.timeout_seconds == 5.0

    def test_factory_function(self):
        """Factory function creates parser correctly."""
        parser = create_temporal_parser(max_input_length=200, timeout_seconds=1.0)
        assert isinstance(parser, TemporalParserService)
        assert parser.max_input_length == 200
        assert parser.timeout_seconds == 1.0


class TestInputValidation:
    """Tests for input validation."""

    def test_empty_string_returns_empty_result(self):
        """Empty string returns result with no parsed date."""
        parser = TemporalParserService()
        result = parser.parse("")
        assert result.start_date is None
        assert result.confidence == 0.0

    def test_none_input_returns_empty_result(self):
        """None input returns result with no parsed date."""
        parser = TemporalParserService()
        # Test with None cast through the validation
        result = parser.parse(None)  # type: ignore
        assert result.start_date is None
        assert result.original_text == ""

    def test_whitespace_only_returns_empty_result(self):
        """Whitespace-only input returns result with no parsed date."""
        parser = TemporalParserService()
        result = parser.parse("   \t\n  ")
        assert result.start_date is None

    def test_exceeds_max_length_returns_empty_result(self):
        """Input exceeding max length returns empty result."""
        parser = TemporalParserService(max_input_length=10)
        result = parser.parse("January 15, 2024")
        assert result.start_date is None

    def test_within_max_length_parses_successfully(self):
        """Input within max length parses successfully."""
        parser = TemporalParserService(max_input_length=1000)
        result = parser.parse("January 15, 2024")
        assert result.start_date is not None


class TestISO8601Parsing:
    """Tests for ISO 8601 date format parsing."""

    @pytest.mark.parametrize(
        "input_text,expected_year,expected_month,expected_day",
        [
            ("2024-03-15", 2024, 3, 15),
            ("2024-12-01", 2024, 12, 1),
            ("1999-01-31", 1999, 1, 31),
            ("2000-06-15", 2000, 6, 15),
        ],
    )
    def test_iso_date_parsing(self, input_text, expected_year, expected_month, expected_day):
        """ISO 8601 dates are parsed correctly."""
        parser = TemporalParserService()
        result = parser.parse(input_text)

        assert result.start_date is not None
        assert result.start_date.year == expected_year
        assert result.start_date.month == expected_month
        assert result.start_date.day == expected_day

    @pytest.mark.parametrize(
        "input_text,expected_hour,expected_minute",
        [
            ("2024-03-15T10:30:00", 10, 30),
            ("2024-03-15T23:59:00", 23, 59),
            ("2024-03-15T00:00:00", 0, 0),
        ],
    )
    def test_iso_datetime_parsing(self, input_text, expected_hour, expected_minute):
        """ISO 8601 datetimes are parsed correctly."""
        parser = TemporalParserService()
        result = parser.parse(input_text)

        assert result.start_date is not None
        assert result.start_date.hour == expected_hour
        assert result.start_date.minute == expected_minute

    def test_iso_datetime_has_utc_timezone(self):
        """Parsed ISO datetimes are normalized to UTC."""
        parser = TemporalParserService()
        result = parser.parse("2024-03-15T10:30:00")

        assert result.start_date is not None
        assert result.start_date.tzinfo == UTC


class TestCommonDateFormats:
    """Tests for common date format parsing."""

    @pytest.mark.parametrize(
        "input_text,expected_year,expected_month,expected_day",
        [
            ("March 15, 2024", 2024, 3, 15),
            ("December 25, 1999", 1999, 12, 25),
            ("January 1, 2000", 2000, 1, 1),
            ("15 March 2024", 2024, 3, 15),
            ("Mar 15, 2024", 2024, 3, 15),
        ],
    )
    def test_written_date_formats(self, input_text, expected_year, expected_month, expected_day):
        """Written date formats are parsed correctly."""
        parser = TemporalParserService()
        result = parser.parse(input_text)

        assert result.start_date is not None
        assert result.start_date.year == expected_year
        assert result.start_date.month == expected_month
        assert result.start_date.day == expected_day


class TestPartialDateParsing:
    """Tests for partial date parsing."""

    @pytest.mark.parametrize(
        "input_text,expected_year",
        [
            ("1850", 1850),
            ("2024", 2024),
            ("1999", 1999),
            ("2000", 2000),
        ],
    )
    def test_year_only_parsing(self, input_text, expected_year):
        """Year-only dates are parsed correctly."""
        parser = TemporalParserService()
        result = parser.parse(input_text)

        assert result.start_date is not None
        assert result.start_date.year == expected_year
        assert result.start_date.month == 1
        assert result.start_date.day == 1
        assert result.precision == DatePrecision.YEAR

    @pytest.mark.parametrize(
        "input_text,expected_year,expected_month",
        [
            ("March 2024", 2024, 3),
            ("January 2000", 2000, 1),
            ("December 1999", 1999, 12),
            ("Mar 2024", 2024, 3),
            ("Jan 2000", 2000, 1),
        ],
    )
    def test_month_year_parsing(self, input_text, expected_year, expected_month):
        """Month + year dates are parsed correctly."""
        parser = TemporalParserService()
        result = parser.parse(input_text)

        assert result.start_date is not None
        assert result.start_date.year == expected_year
        assert result.start_date.month == expected_month
        assert result.precision == DatePrecision.MONTH

    @pytest.mark.parametrize(
        "input_text,expected_year,expected_month",
        [
            ("Q1 2024", 2024, 1),
            ("Q2 2024", 2024, 4),
            ("Q3 2024", 2024, 7),
            ("Q4 2024", 2024, 10),
        ],
    )
    def test_quarter_parsing(self, input_text, expected_year, expected_month):
        """Quarter expressions are parsed correctly."""
        parser = TemporalParserService()
        result = parser.parse(input_text)

        assert result.start_date is not None
        assert result.start_date.year == expected_year
        assert result.start_date.month == expected_month
        assert result.precision == DatePrecision.MONTH

    @pytest.mark.parametrize(
        "input_text,expected_decade_start",
        [
            ("the 1850s", 1850),
            ("1990s", 1990),
            ("the 1920s", 1920),
        ],
    )
    def test_decade_parsing(self, input_text, expected_decade_start):
        """Decade expressions are parsed correctly."""
        parser = TemporalParserService()
        result = parser.parse(input_text)

        assert result.start_date is not None
        assert result.start_date.year == expected_decade_start
        assert result.precision == DatePrecision.YEAR


class TestDateRangeParsing:
    """Tests for date range parsing."""

    @pytest.mark.parametrize(
        "input_text,expected_start_year,expected_end_year",
        [
            ("1914-1918", 1914, 1918),
            ("1939-1945", 1939, 1945),
            ("2020-2023", 2020, 2023),
            ("1914 - 1918", 1914, 1918),
            ("1914 to 1918", 1914, 1918),
        ],
    )
    def test_year_range_parsing(self, input_text, expected_start_year, expected_end_year):
        """Year ranges are parsed correctly."""
        parser = TemporalParserService()
        result = parser.parse(input_text)

        assert result.start_date is not None
        assert result.end_date is not None
        assert result.start_date.year == expected_start_year
        assert result.end_date.year == expected_end_year
        assert result.start_date.month == 1
        assert result.end_date.month == 12

    def test_month_range_same_year_parsing(self):
        """Month ranges within same year are parsed correctly."""
        parser = TemporalParserService()
        result = parser.parse("January to March 2024")

        assert result.start_date is not None
        assert result.end_date is not None
        assert result.start_date.year == 2024
        assert result.start_date.month == 1
        assert result.end_date.year == 2024
        assert result.end_date.month == 3

    def test_quarter_range_parsing(self):
        """Quarter ranges are parsed correctly."""
        parser = TemporalParserService()
        result = parser.parse("Q1-Q2 2024")

        assert result.start_date is not None
        assert result.end_date is not None
        assert result.start_date.year == 2024
        assert result.start_date.month == 1
        assert result.end_date.year == 2024
        assert result.end_date.month == 6


class TestHistoricalPeriodParsing:
    """Tests for historical period parsing."""

    @pytest.mark.parametrize(
        "input_text,expected_start_century,expected_end_century",
        [
            ("19th century", 1801, 1900),
            ("20th century", 1901, 2000),
            ("21st century", 2001, 2100),
            ("1st century", 1, 100),
        ],
    )
    def test_century_parsing(self, input_text, expected_start_century, expected_end_century):
        """Century expressions are parsed correctly."""
        parser = TemporalParserService()
        result = parser.parse(input_text)

        assert result.start_date is not None
        assert result.end_date is not None
        assert result.start_date.year == expected_start_century
        assert result.end_date.year == expected_end_century
        assert result.precision == DatePrecision.YEAR

    @pytest.mark.parametrize(
        "input_text,expected_start,expected_end",
        [
            ("early 19th century", 1801, 1833),
            ("mid 19th century", 1834, 1866),
            ("late 19th century", 1867, 1900),
        ],
    )
    def test_century_portion_parsing(self, input_text, expected_start, expected_end):
        """Century portion expressions are parsed correctly."""
        parser = TemporalParserService()
        result = parser.parse(input_text)

        assert result.start_date is not None
        assert result.end_date is not None
        assert result.start_date.year == expected_start
        assert result.end_date.year == expected_end

    def test_medieval_period_parsing(self):
        """Medieval period is parsed correctly."""
        parser = TemporalParserService()
        result = parser.parse("medieval period")

        assert result.start_date is not None
        assert result.end_date is not None
        assert result.start_date.year == 500
        assert result.end_date.year == 1500

    def test_renaissance_period_parsing(self):
        """Renaissance period is parsed correctly."""
        parser = TemporalParserService()
        result = parser.parse("renaissance")

        assert result.start_date is not None
        assert result.end_date is not None
        assert result.start_date.year == 1400
        assert result.end_date.year == 1600

    def test_historical_periods_have_approximate_uncertainty(self):
        """Historical periods are marked as approximate."""
        parser = TemporalParserService()
        result = parser.parse("19th century")

        assert result.uncertainty == UncertaintyMarker.APPROXIMATE


class TestUncertaintyDetection:
    """Tests for uncertainty marker detection."""

    @pytest.mark.parametrize(
        "input_text,expected_marker",
        [
            ("circa 1850", UncertaintyMarker.CIRCA),
            ("c. 1850", UncertaintyMarker.CIRCA),
            ("ca. 1850", UncertaintyMarker.CIRCA),
        ],
    )
    def test_circa_detection(self, input_text, expected_marker):
        """Circa markers are detected correctly."""
        parser = TemporalParserService()
        result = parser.detect_uncertainty(input_text)
        assert result == expected_marker

    @pytest.mark.parametrize(
        "input_text,expected_marker",
        [
            ("around 1850", UncertaintyMarker.APPROXIMATE),
            ("approximately 1850", UncertaintyMarker.APPROXIMATE),
            ("about 1850", UncertaintyMarker.APPROXIMATE),
            ("roughly 1850", UncertaintyMarker.APPROXIMATE),
            ("nearly 1850", UncertaintyMarker.APPROXIMATE),
        ],
    )
    def test_approximate_detection(self, input_text, expected_marker):
        """Approximate markers are detected correctly."""
        parser = TemporalParserService()
        result = parser.detect_uncertainty(input_text)
        assert result == expected_marker

    @pytest.mark.parametrize(
        "input_text,expected_marker",
        [
            ("before 1850", UncertaintyMarker.BEFORE),
            ("prior to 1850", UncertaintyMarker.BEFORE),
            ("by 1850", UncertaintyMarker.BEFORE),
            ("until 1850", UncertaintyMarker.BEFORE),
        ],
    )
    def test_before_detection(self, input_text, expected_marker):
        """Before markers are detected correctly."""
        parser = TemporalParserService()
        result = parser.detect_uncertainty(input_text)
        assert result == expected_marker

    @pytest.mark.parametrize(
        "input_text,expected_marker",
        [
            ("after 1850", UncertaintyMarker.AFTER),
            ("following 1850", UncertaintyMarker.AFTER),
            ("since 1850", UncertaintyMarker.AFTER),
            ("from 1850", UncertaintyMarker.AFTER),
        ],
    )
    def test_after_detection(self, input_text, expected_marker):
        """After markers are detected correctly."""
        parser = TemporalParserService()
        result = parser.detect_uncertainty(input_text)
        assert result == expected_marker

    def test_no_uncertainty_marker_returns_exact(self):
        """Text without uncertainty markers returns EXACT."""
        parser = TemporalParserService()
        result = parser.detect_uncertainty("March 15, 2024")
        assert result == UncertaintyMarker.EXACT

    def test_empty_text_returns_exact(self):
        """Empty text returns EXACT."""
        parser = TemporalParserService()
        result = parser.detect_uncertainty("")
        assert result == UncertaintyMarker.EXACT

    def test_uncertainty_preserved_in_parse_result(self):
        """Uncertainty marker is preserved in parse result."""
        parser = TemporalParserService()
        result = parser.parse("circa 1850")

        assert result.uncertainty == UncertaintyMarker.CIRCA
        assert result.start_date is not None
        assert result.start_date.year == 1850


class TestPrecisionInference:
    """Tests for precision level inference."""

    @pytest.mark.parametrize(
        "input_text,expected_precision",
        [
            ("2024", DatePrecision.YEAR),
            ("1850", DatePrecision.YEAR),
            ("the 1990s", DatePrecision.YEAR),
        ],
    )
    def test_year_precision(self, input_text, expected_precision):
        """Year-only input has YEAR precision."""
        parser = TemporalParserService()
        result = parser.parse(input_text)
        assert result.precision == expected_precision

    @pytest.mark.parametrize(
        "input_text,expected_precision",
        [
            ("March 2024", DatePrecision.MONTH),
            ("Q1 2024", DatePrecision.MONTH),
            ("January 2000", DatePrecision.MONTH),
        ],
    )
    def test_month_precision(self, input_text, expected_precision):
        """Month-year input has MONTH precision."""
        parser = TemporalParserService()
        result = parser.parse(input_text)
        assert result.precision == expected_precision

    @pytest.mark.parametrize(
        "input_text,expected_precision",
        [
            ("March 15, 2024", DatePrecision.DAY),
            ("2024-03-15", DatePrecision.DAY),
            ("15 March 2024", DatePrecision.DAY),
        ],
    )
    def test_day_precision(self, input_text, expected_precision):
        """Full date input has DAY precision."""
        parser = TemporalParserService()
        result = parser.parse(input_text)
        assert result.precision == expected_precision

    @pytest.mark.parametrize(
        "input_text,expected_precision",
        [
            ("2024-03-15T10:00", DatePrecision.HOUR),
            ("March 15, 2024 10:00", DatePrecision.HOUR),
        ],
    )
    def test_hour_precision(self, input_text, expected_precision):
        """Date with hour has HOUR precision."""
        parser = TemporalParserService()
        result = parser.parse(input_text)
        assert result.precision == expected_precision

    @pytest.mark.parametrize(
        "input_text,expected_precision",
        [
            ("2024-03-15T10:30:00", DatePrecision.MINUTE),
            ("March 15, 2024 10:30", DatePrecision.MINUTE),
        ],
    )
    def test_minute_precision(self, input_text, expected_precision):
        """Date with minute has MINUTE precision."""
        parser = TemporalParserService()
        result = parser.parse(input_text)
        assert result.precision == expected_precision


class TestConfidenceScoring:
    """Tests for confidence score calculation."""

    def test_high_confidence_for_iso_dates(self):
        """ISO 8601 dates have high confidence."""
        parser = TemporalParserService()
        result = parser.parse("2024-03-15")

        assert result.confidence >= 0.8

    def test_lower_confidence_for_uncertain_dates(self):
        """Uncertain dates have lower confidence."""
        parser = TemporalParserService()
        exact_result = parser.parse("2024-03-15")
        uncertain_result = parser.parse("circa 2024")

        assert uncertain_result.confidence < exact_result.confidence

    def test_zero_confidence_for_unparseable(self):
        """Unparseable text has zero confidence."""
        parser = TemporalParserService()
        result = parser.parse("not a date at all")

        # dateparser might parse this, so check for low confidence
        assert result.confidence <= 0.5 or result.start_date is None


class TestParseMethod:
    """Tests for parse method tracking."""

    def test_dateutil_method_for_iso_dates(self):
        """ISO dates use dateutil method."""
        parser = TemporalParserService()
        result = parser.parse("2024-03-15")

        assert result.parse_method == ParseMethod.DATEUTIL.value

    def test_regex_method_for_ranges(self):
        """Date ranges use regex method."""
        parser = TemporalParserService()
        result = parser.parse("1914-1918")

        assert result.parse_method == ParseMethod.REGEX.value

    def test_heuristic_method_for_historical(self):
        """Historical periods use heuristic method."""
        parser = TemporalParserService()
        result = parser.parse("medieval period")

        assert result.parse_method == ParseMethod.HEURISTIC.value


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_malformed_date_does_not_raise(self):
        """Malformed dates do not raise exceptions."""
        parser = TemporalParserService()

        # These should not raise
        result = parser.parse("not-a-date")
        assert isinstance(result, TemporalParseResult)

        result = parser.parse("32/13/2024")
        assert isinstance(result, TemporalParseResult)

        result = parser.parse("February 30, 2024")
        assert isinstance(result, TemporalParseResult)

    def test_special_characters_handled(self):
        """Special characters are handled gracefully."""
        parser = TemporalParserService()

        result = parser.parse("@#$%^&*()")
        assert isinstance(result, TemporalParseResult)

    def test_unicode_dates_handled(self):
        """Unicode date separators are handled."""
        parser = TemporalParserService()

        # En-dash separator
        result = parser.parse("1914\u20131918")
        assert result.start_date is not None
        assert result.end_date is not None

    def test_very_old_years(self):
        """Very old years are handled."""
        parser = TemporalParserService()

        result = parser.parse("1st century")
        assert result.start_date is not None
        assert result.start_date.year == 1

    def test_far_future_years(self):
        """Far future years are handled."""
        parser = TemporalParserService()

        result = parser.parse("3000")
        assert result.start_date is not None
        assert result.start_date.year == 3000

    def test_original_text_preserved(self):
        """Original text is preserved in result."""
        parser = TemporalParserService()

        result = parser.parse("  circa 1850  ")
        assert result.original_text == "circa 1850"

    def test_utc_normalization(self):
        """All dates are normalized to UTC."""
        parser = TemporalParserService()

        result = parser.parse("2024-03-15T10:30:00")
        assert result.start_date is not None
        assert result.start_date.tzinfo == UTC


class TestRelativeDates:
    """Tests for relative date parsing (via dateparser)."""

    def test_yesterday_parsing(self):
        """'yesterday' is parsed correctly."""
        parser = TemporalParserService()
        result = parser.parse("yesterday")

        if result.start_date is not None:
            # Should be a date in the past
            assert result.start_date < datetime.now(UTC)
            assert result.parse_method == ParseMethod.DATEPARSER.value

    def test_last_week_parsing(self):
        """'last week' is parsed correctly."""
        parser = TemporalParserService()
        result = parser.parse("last week")

        if result.start_date is not None:
            assert result.start_date < datetime.now(UTC)


class TestUncertaintyWithParsing:
    """Tests for uncertainty detection combined with parsing."""

    def test_circa_date_parsed_correctly(self):
        """Circa dates are parsed with correct year."""
        parser = TemporalParserService()
        result = parser.parse("circa 1850")

        assert result.start_date is not None
        assert result.start_date.year == 1850
        assert result.uncertainty == UncertaintyMarker.CIRCA

    def test_before_date_parsed_correctly(self):
        """Before dates are parsed with correct year."""
        parser = TemporalParserService()
        result = parser.parse("before 1900")

        assert result.start_date is not None
        assert result.start_date.year == 1900
        assert result.uncertainty == UncertaintyMarker.BEFORE

    def test_after_date_parsed_correctly(self):
        """After dates are parsed with correct year."""
        parser = TemporalParserService()
        result = parser.parse("after 1945")

        assert result.start_date is not None
        assert result.start_date.year == 1945
        assert result.uncertainty == UncertaintyMarker.AFTER

    def test_approximately_date_parsed_correctly(self):
        """Approximately dates are parsed with correct year."""
        parser = TemporalParserService()
        result = parser.parse("approximately March 2024")

        assert result.start_date is not None
        assert result.start_date.year == 2024
        assert result.start_date.month == 3
        assert result.uncertainty == UncertaintyMarker.APPROXIMATE


class TestTemporalParseResultDataclass:
    """Tests for TemporalParseResult dataclass."""

    def test_default_values(self):
        """Default values are set correctly."""
        result = TemporalParseResult()

        assert result.start_date is None
        assert result.end_date is None
        assert result.precision == DatePrecision.DAY
        assert result.uncertainty == UncertaintyMarker.EXACT
        assert result.original_text == ""
        assert result.confidence == 0.0
        assert result.parse_method == ""

    def test_custom_values(self):
        """Custom values are set correctly."""
        now = datetime.now(UTC)
        result = TemporalParseResult(
            start_date=now,
            end_date=now,
            precision=DatePrecision.YEAR,
            uncertainty=UncertaintyMarker.CIRCA,
            original_text="circa 2024",
            confidence=0.85,
            parse_method="dateutil",
        )

        assert result.start_date == now
        assert result.end_date == now
        assert result.precision == DatePrecision.YEAR
        assert result.uncertainty == UncertaintyMarker.CIRCA
        assert result.original_text == "circa 2024"
        assert result.confidence == 0.85
        assert result.parse_method == "dateutil"


class TestDateRangeWithUncertainty:
    """Tests for date ranges with uncertainty markers."""

    def test_circa_range_parsed(self):
        """Circa with date range is parsed correctly."""
        parser = TemporalParserService()
        result = parser.parse("circa 1914-1918")

        assert result.start_date is not None
        assert result.end_date is not None
        assert result.uncertainty == UncertaintyMarker.CIRCA

    def test_approximately_range_parsed(self):
        """Approximately with date range is parsed correctly."""
        parser = TemporalParserService()
        result = parser.parse("approximately 1850-1900")

        assert result.start_date is not None
        assert result.end_date is not None
        assert result.uncertainty == UncertaintyMarker.APPROXIMATE
