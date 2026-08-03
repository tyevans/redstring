"""
Temporal Parser Service for parsing natural language date expressions.

This module provides robust parsing of various date formats including:
- ISO 8601 dates
- Natural language dates ("March 15, 2024")
- Relative dates ("last week", "3 days ago")
- Partial dates ("March 2024", "2024")
- Date ranges ("1914-1918", "January to March 2024")
- Historical periods ("19th century", "the 1850s")

The parser detects uncertainty markers (circa, approximately, before, after)
and infers precision levels (year, month, day, hour, minute).

Example usage:
    >>> parser = TemporalParserService()
    >>> result = parser.parse("circa 1850")
    >>> assert result.uncertainty == UncertaintyMarker.CIRCA
    >>> assert result.precision == DatePrecision.YEAR

See ADR-025 for design decisions.
"""

from __future__ import annotations

import logging
import re
import signal
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import ClassVar

import dateparser
from dateutil import parser as dateutil_parser
from dateutil.relativedelta import relativedelta

from kg_builder.schemas.timeline import DatePrecision, UncertaintyMarker

logger = logging.getLogger(__name__)

# Maximum input length to prevent DoS attacks
MAX_INPUT_LENGTH = 500

# Parsing timeout in seconds
PARSING_TIMEOUT_SECONDS = 2


class ParseMethod(str, Enum):
    """Method used to parse the date."""

    DATEUTIL = "dateutil"
    DATEPARSER = "dateparser"
    REGEX = "regex"
    HEURISTIC = "heuristic"


class TemporalParseError(Exception):
    """Error during temporal parsing."""

    pass


class TemporalParseTimeout(TemporalParseError):
    """Parsing timed out."""

    pass


@dataclass
class TemporalParseResult:
    """Result of parsing a temporal expression.

    Attributes:
        start_date: Parsed start date/time (UTC normalized)
        end_date: Parsed end date/time for ranges (UTC normalized)
        precision: Inferred precision level
        uncertainty: Detected uncertainty marker
        original_text: Original input text
        confidence: Confidence score (0.0 to 1.0)
        parse_method: Method used for parsing
    """

    start_date: datetime | None = None
    end_date: datetime | None = None
    precision: DatePrecision = DatePrecision.DAY
    uncertainty: UncertaintyMarker = UncertaintyMarker.EXACT
    original_text: str = ""
    confidence: float = 0.0
    parse_method: str = ""


class TemporalParserService:
    """Service for parsing natural language date expressions.

    Provides comprehensive date parsing with:
    - Multiple parsing strategies (dateutil, dateparser, regex, heuristic)
    - Uncertainty detection (circa, approximately, before, after)
    - Precision inference (year, month, day, hour, minute)
    - Safety limits (timeout, input length validation)

    Attributes:
        max_input_length: Maximum allowed input length
        timeout_seconds: Parsing timeout in seconds
    """

    # Regex patterns for uncertainty detection (class variable, initialized in __init__)
    UNCERTAINTY_PATTERNS: ClassVar[dict[UncertaintyMarker, list[re.Pattern]]] = {}

    # Regex patterns for date range detection
    RANGE_PATTERNS: ClassVar[list[tuple[re.Pattern, str]]] = []

    # Regex patterns for partial dates
    PARTIAL_DATE_PATTERNS: ClassVar[list[tuple[re.Pattern, str, DatePrecision]]] = []

    # Historical period patterns
    HISTORICAL_PATTERNS: ClassVar[list[tuple[re.Pattern, str]]] = []

    def __init__(
        self,
        max_input_length: int = MAX_INPUT_LENGTH,
        timeout_seconds: float = PARSING_TIMEOUT_SECONDS,
    ):
        """Initialize the temporal parser service.

        Args:
            max_input_length: Maximum allowed input length (default 500)
            timeout_seconds: Parsing timeout in seconds (default 2.0)
        """
        self.max_input_length = max_input_length
        self.timeout_seconds = timeout_seconds
        self._init_uncertainty_patterns()
        self._init_range_patterns()
        self._init_partial_date_patterns()
        self._init_historical_patterns()

    def _init_uncertainty_patterns(self) -> None:
        """Initialize uncertainty detection patterns."""
        self.UNCERTAINTY_PATTERNS = {
            UncertaintyMarker.CIRCA: [
                re.compile(r"\bcirca\b", re.IGNORECASE),
                re.compile(r"\bc\.\s*(?=\d)", re.IGNORECASE),
                re.compile(r"\bca\.\s*(?=\d)", re.IGNORECASE),
            ],
            UncertaintyMarker.APPROXIMATE: [
                re.compile(r"\baround\b", re.IGNORECASE),
                re.compile(r"\bapproximately\b", re.IGNORECASE),
                re.compile(r"\babout\b", re.IGNORECASE),
                re.compile(r"\broughly\b", re.IGNORECASE),
                re.compile(r"\bnearly\b", re.IGNORECASE),
            ],
            UncertaintyMarker.BEFORE: [
                re.compile(r"\bbefore\b", re.IGNORECASE),
                re.compile(r"\bprior\s+to\b", re.IGNORECASE),
                re.compile(r"\bby\b(?=\s+\d)", re.IGNORECASE),
                re.compile(r"\buntil\b", re.IGNORECASE),
                re.compile(r"\bno\s+later\s+than\b", re.IGNORECASE),
            ],
            UncertaintyMarker.AFTER: [
                re.compile(r"\bafter\b", re.IGNORECASE),
                re.compile(r"\bfollowing\b", re.IGNORECASE),
                re.compile(r"\bsince\b", re.IGNORECASE),
                re.compile(r"\bfrom\b(?=\s+\d)", re.IGNORECASE),
                re.compile(r"\bno\s+earlier\s+than\b", re.IGNORECASE),
            ],
        }

    def _init_range_patterns(self) -> None:
        """Initialize date range patterns."""
        self.RANGE_PATTERNS = [
            # Year ranges: "1914-1918", "1914 - 1918", "1914 to 1918"
            (
                re.compile(
                    r"(\d{4})\s*[-\u2013\u2014]\s*(\d{4})",
                    re.IGNORECASE,
                ),
                "year_range",
            ),
            (
                re.compile(
                    r"(\d{4})\s+to\s+(\d{4})",
                    re.IGNORECASE,
                ),
                "year_range",
            ),
            # Month ranges: "January to March 2024", "Jan-Mar 2024"
            (
                re.compile(
                    r"((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
                    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
                    r")\s*[-\u2013\u2014]?\s*(?:to\s+)?"
                    r"((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
                    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
                    r")\s+(\d{4})",
                    re.IGNORECASE,
                ),
                "month_range_same_year",
            ),
            # Full date ranges: "March 15 to March 20, 2024"
            (
                re.compile(
                    r"((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
                    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
                    r"\s+\d{1,2})\s*(?:to|-)\s*"
                    r"((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
                    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
                    r"\s+\d{1,2}),?\s*(\d{4})",
                    re.IGNORECASE,
                ),
                "date_range_same_year",
            ),
            # Quarter ranges: "Q1-Q2 2024"
            (
                re.compile(r"Q([1-4])\s*[-\u2013\u2014]\s*Q([1-4])\s+(\d{4})", re.IGNORECASE),
                "quarter_range",
            ),
        ]

    def _init_partial_date_patterns(self) -> None:
        """Initialize partial date patterns."""
        self.PARTIAL_DATE_PATTERNS = [
            # Quarter: "Q1 2024", "Q3 2024"
            (
                re.compile(r"Q([1-4])\s+(\d{4})", re.IGNORECASE),
                "quarter",
                DatePrecision.MONTH,
            ),
            # Year only: "2024", "1850"
            (
                re.compile(r"^(\d{4})$"),
                "year_only",
                DatePrecision.YEAR,
            ),
            # Month + Year: "March 2024", "Mar 2024" - anchored to avoid matching in "15 March 2024"
            (
                re.compile(
                    r"^((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
                    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
                    r")\s+(\d{4})$",
                    re.IGNORECASE,
                ),
                "month_year",
                DatePrecision.MONTH,
            ),
            # Decade: "the 1850s", "1990s"
            (
                re.compile(r"(?:the\s+)?(\d{3})0s", re.IGNORECASE),
                "decade",
                DatePrecision.YEAR,
            ),
        ]

    def _init_historical_patterns(self) -> None:
        """Initialize historical period patterns."""
        self.HISTORICAL_PATTERNS = [
            # Early/Mid/Late century: "early 19th century" - must come BEFORE century
            (
                re.compile(
                    r"(early|mid|late)\s+(\d{1,2})(?:st|nd|rd|th)\s+century",
                    re.IGNORECASE,
                ),
                "century_portion",
            ),
            # Century: "19th century", "21st century"
            (
                re.compile(r"(\d{1,2})(?:st|nd|rd|th)\s+century", re.IGNORECASE),
                "century",
            ),
            # Era: "medieval period", "renaissance"
            (
                re.compile(r"medieval\s+(?:period|era|times?)?", re.IGNORECASE),
                "medieval",
            ),
            (
                re.compile(r"renaissance(?:\s+period)?", re.IGNORECASE),
                "renaissance",
            ),
            (
                re.compile(r"ancient\s+(?:times?|period|era)?", re.IGNORECASE),
                "ancient",
            ),
        ]

    def _validate_input(self, text: str) -> str | None:
        """Validate and clean input text.

        Args:
            text: Input text to validate

        Returns:
            Cleaned text or None if invalid
        """
        if not text:
            return None

        # Strip whitespace
        text = text.strip()

        if not text:
            return None

        # Check length
        if len(text) > self.max_input_length:
            logger.warning(
                f"Input text exceeds maximum length ({len(text)} > {self.max_input_length})"
            )
            return None

        return text

    def detect_uncertainty(self, text: str) -> UncertaintyMarker:
        """Detect uncertainty markers in text.

        Scans text for indicators of temporal uncertainty such as
        "circa", "approximately", "before", "after", etc.

        Args:
            text: Text to analyze

        Returns:
            Detected UncertaintyMarker or EXACT if none found
        """
        if not text:
            return UncertaintyMarker.EXACT

        # Check patterns in priority order
        for marker, patterns in self.UNCERTAINTY_PATTERNS.items():
            for pattern in patterns:
                if pattern.search(text):
                    logger.debug(f"Detected uncertainty marker: {marker.value} in '{text}'")
                    return marker

        return UncertaintyMarker.EXACT

    def infer_precision(self, dt: datetime | None, text: str) -> DatePrecision:
        """Infer precision level from parsed datetime and original text.

        Determines the appropriate precision based on what components
        were present in the original text.

        Args:
            dt: Parsed datetime (may be None)
            text: Original text for context

        Returns:
            Inferred DatePrecision
        """
        if not text:
            return DatePrecision.DAY

        text_lower = text.lower().strip()

        # Check for time components
        if re.search(r"\d{1,2}:\d{2}:\d{2}", text):
            return DatePrecision.MINUTE
        if re.search(r"\d{1,2}:\d{2}", text):
            # Check if minutes are specified
            time_match = re.search(r"\d{1,2}:(\d{2})", text)
            if time_match and time_match.group(1) != "00":
                return DatePrecision.MINUTE
            return DatePrecision.HOUR

        # Define month pattern for reuse
        month_pattern = (
            r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
            r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        )

        # Check for full dates with day BEFORE partial patterns
        # "15 March 2024", "March 15, 2024", "March 15 2024"
        full_date_patterns = [
            # "15 March 2024" or "15 Mar 2024"
            rf"^\s*\d{{1,2}}\s+{month_pattern}\s+\d{{4}}\s*$",
            # "March 15, 2024" or "Mar 15, 2024"
            rf"^\s*{month_pattern}\s+\d{{1,2}},?\s+\d{{4}}\s*$",
        ]
        for full_pattern in full_date_patterns:
            if re.match(full_pattern, text, re.IGNORECASE):
                return DatePrecision.DAY

        # Check for year-only patterns
        if re.match(r"^\s*\d{4}\s*$", text):
            return DatePrecision.YEAR

        # Check for month+year without day (must be strict match)
        if re.match(rf"^\s*{month_pattern}\s+\d{{4}}\s*$", text, re.IGNORECASE):
            return DatePrecision.MONTH

        # Check for partial date patterns
        for pattern, _pattern_type, precision in self.PARTIAL_DATE_PATTERNS:
            if pattern.search(text_lower):
                return precision

        # Default to day precision for full dates
        return DatePrecision.DAY

    def _strip_uncertainty_markers(self, text: str) -> str:
        """Remove uncertainty markers from text for parsing.

        Args:
            text: Text with potential uncertainty markers

        Returns:
            Text with uncertainty markers removed
        """
        # Remove common uncertainty prefixes
        patterns_to_remove = [
            r"\bcirca\s+",
            r"\bc\.\s*",
            r"\bca\.\s*",
            r"\baround\s+",
            r"\bapproximately\s+",
            r"\babout\s+",
            r"\broughly\s+",
            r"\bnearly\s+",
            r"\bbefore\s+",
            r"\bprior\s+to\s+",
            r"\bby\s+(?=\d)",
            r"\buntil\s+",
            r"\bafter\s+",
            r"\bfollowing\s+",
            r"\bsince\s+",
            r"\bfrom\s+(?=\d)",
        ]

        result = text
        for pattern in patterns_to_remove:
            result = re.sub(pattern, "", result, flags=re.IGNORECASE)

        return result.strip()

    def parse_date_range(self, text: str) -> tuple[datetime, datetime] | None:
        """Parse a date range expression.

        Handles formats like:
        - "1914-1918"
        - "January to March 2024"
        - "Q1-Q2 2024"

        Args:
            text: Text containing a date range

        Returns:
            Tuple of (start_date, end_date) or None if not a range
        """
        if not text:
            return None

        text = self._strip_uncertainty_markers(text)

        for pattern, range_type in self.RANGE_PATTERNS:
            match = pattern.search(text)
            if match:
                try:
                    if range_type == "year_range":
                        start_year = int(match.group(1))
                        end_year = int(match.group(2))
                        return (
                            datetime(start_year, 1, 1, tzinfo=UTC),
                            datetime(end_year, 12, 31, 23, 59, 59, tzinfo=UTC),
                        )

                    elif range_type == "month_range_same_year":
                        start_month = match.group(1)
                        end_month = match.group(2)
                        year = int(match.group(3))

                        start_dt = dateutil_parser.parse(f"{start_month} 1, {year}")
                        # Get last day of end month
                        end_dt = dateutil_parser.parse(f"{end_month} 1, {year}")
                        # Move to last day of month
                        end_dt = end_dt + relativedelta(months=1, days=-1)

                        return (
                            start_dt.replace(tzinfo=UTC),
                            end_dt.replace(
                                hour=23, minute=59, second=59, tzinfo=UTC
                            ),
                        )

                    elif range_type == "date_range_same_year":
                        start_date = match.group(1)
                        end_date = match.group(2)
                        year = match.group(3)

                        start_dt = dateutil_parser.parse(f"{start_date}, {year}")
                        end_dt = dateutil_parser.parse(f"{end_date}, {year}")

                        return (
                            start_dt.replace(tzinfo=UTC),
                            end_dt.replace(
                                hour=23, minute=59, second=59, tzinfo=UTC
                            ),
                        )

                    elif range_type == "quarter_range":
                        start_q = int(match.group(1))
                        end_q = int(match.group(2))
                        year = int(match.group(3))

                        # Quarter to month mapping (1=Jan, 2=Apr, 3=Jul, 4=Oct)
                        start_month = (start_q - 1) * 3 + 1
                        end_month = end_q * 3

                        start_dt = datetime(year, start_month, 1, tzinfo=UTC)
                        # Last day of end quarter
                        end_dt = datetime(year, end_month, 1, tzinfo=UTC)
                        end_dt = end_dt + relativedelta(months=1, days=-1)

                        return (
                            start_dt,
                            end_dt.replace(hour=23, minute=59, second=59),
                        )

                except (ValueError, OverflowError) as e:
                    logger.debug(f"Failed to parse range '{text}': {e}")
                    continue

        return None

    def parse_partial_date(self, text: str) -> datetime | None:
        """Parse a partial date expression.

        Handles formats like:
        - "1850" (year only)
        - "March 2024" (month + year)
        - "Q3 2024" (quarter)

        Args:
            text: Text containing a partial date

        Returns:
            Parsed datetime or None
        """
        if not text:
            return None

        text = self._strip_uncertainty_markers(text).strip()

        for pattern, pattern_type, _ in self.PARTIAL_DATE_PATTERNS:
            match = pattern.search(text)
            if match:
                try:
                    if pattern_type == "year_only":
                        year = int(match.group(1))
                        return datetime(year, 1, 1, tzinfo=UTC)

                    elif pattern_type == "month_year":
                        month_str = match.group(1)
                        year = int(match.group(2))
                        dt = dateutil_parser.parse(f"{month_str} 1, {year}")
                        return dt.replace(tzinfo=UTC)

                    elif pattern_type == "quarter":
                        quarter = int(match.group(1))
                        year = int(match.group(2))
                        month = (quarter - 1) * 3 + 1
                        return datetime(year, month, 1, tzinfo=UTC)

                    elif pattern_type == "decade":
                        decade_start = int(match.group(1)) * 10
                        return datetime(decade_start, 1, 1, tzinfo=UTC)

                except (ValueError, OverflowError) as e:
                    logger.debug(f"Failed to parse partial date '{text}': {e}")
                    continue

        return None

    def _parse_historical_period(self, text: str) -> tuple[datetime, datetime] | None:
        """Parse historical period expressions.

        Handles formats like:
        - "19th century"
        - "early 20th century"
        - "medieval period"

        Args:
            text: Text containing a historical period

        Returns:
            Tuple of (start_date, end_date) or None
        """
        if not text:
            return None

        text_lower = text.lower()

        for pattern, period_type in self.HISTORICAL_PATTERNS:
            match = pattern.search(text_lower)
            if match:
                try:
                    if period_type == "century":
                        century = int(match.group(1))
                        start_year = (century - 1) * 100 + 1
                        end_year = century * 100
                        return (
                            datetime(start_year, 1, 1, tzinfo=UTC),
                            datetime(end_year, 12, 31, 23, 59, 59, tzinfo=UTC),
                        )

                    elif period_type == "century_portion":
                        portion = match.group(1).lower()
                        century = int(match.group(2))
                        base_year = (century - 1) * 100

                        if portion == "early":
                            start_year = base_year + 1
                            end_year = base_year + 33
                        elif portion == "mid":
                            start_year = base_year + 34
                            end_year = base_year + 66
                        else:  # late
                            start_year = base_year + 67
                            end_year = base_year + 100

                        return (
                            datetime(start_year, 1, 1, tzinfo=UTC),
                            datetime(end_year, 12, 31, 23, 59, 59, tzinfo=UTC),
                        )

                    elif period_type == "medieval":
                        # Roughly 500-1500 CE
                        return (
                            datetime(500, 1, 1, tzinfo=UTC),
                            datetime(1500, 12, 31, 23, 59, 59, tzinfo=UTC),
                        )

                    elif period_type == "renaissance":
                        # Roughly 1400-1600 CE
                        return (
                            datetime(1400, 1, 1, tzinfo=UTC),
                            datetime(1600, 12, 31, 23, 59, 59, tzinfo=UTC),
                        )

                    elif period_type == "ancient":
                        # Roughly 3000 BCE - 500 CE (stored as 1-500 for datetime compatibility)
                        # Note: BCE dates not fully supported, use earliest possible datetime
                        return (
                            datetime(1, 1, 1, tzinfo=UTC),
                            datetime(500, 12, 31, 23, 59, 59, tzinfo=UTC),
                        )

                except (ValueError, OverflowError) as e:
                    logger.debug(f"Failed to parse historical period '{text}': {e}")
                    continue

        return None

    def _parse_with_dateutil(self, text: str) -> datetime | None:
        """Parse using python-dateutil.

        Best for structured date formats like ISO 8601.

        Args:
            text: Text to parse

        Returns:
            Parsed datetime or None
        """
        try:
            dt = dateutil_parser.parse(text, fuzzy=False)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except (ValueError, OverflowError):
            return None

    def _parse_with_dateparser(self, text: str) -> datetime | None:
        """Parse using dateparser library.

        Best for natural language dates and relative expressions.

        Args:
            text: Text to parse

        Returns:
            Parsed datetime or None
        """
        try:
            dt = dateparser.parse(
                text,
                settings={
                    "RETURN_AS_TIMEZONE_AWARE": True,
                    "TIMEZONE": "UTC",
                    "PREFER_DATES_FROM": "past",
                },
            )
            return dt
        except Exception:
            return None

    def _calculate_confidence(
        self,
        parse_method: str,
        has_start_date: bool,
        has_end_date: bool,
        uncertainty: UncertaintyMarker,
        precision: DatePrecision,
    ) -> float:
        """Calculate confidence score for parse result.

        Args:
            parse_method: Method used for parsing
            has_start_date: Whether start date was parsed
            has_end_date: Whether end date was parsed
            uncertainty: Detected uncertainty marker
            precision: Inferred precision level

        Returns:
            Confidence score between 0.0 and 1.0
        """
        if not has_start_date:
            return 0.0

        # Base confidence by parse method
        method_confidence = {
            ParseMethod.DATEUTIL.value: 0.95,
            ParseMethod.DATEPARSER.value: 0.85,
            ParseMethod.REGEX.value: 0.80,
            ParseMethod.HEURISTIC.value: 0.60,
        }

        confidence = method_confidence.get(parse_method, 0.5)

        # Reduce confidence for uncertain dates
        uncertainty_penalty = {
            UncertaintyMarker.EXACT: 0.0,
            UncertaintyMarker.APPROXIMATE: 0.1,
            UncertaintyMarker.CIRCA: 0.15,
            UncertaintyMarker.BEFORE: 0.2,
            UncertaintyMarker.AFTER: 0.2,
            UncertaintyMarker.INFERRED: 0.25,
        }

        confidence -= uncertainty_penalty.get(uncertainty, 0.0)

        # Slightly reduce confidence for coarser precision
        precision_penalty = {
            DatePrecision.MINUTE: 0.0,
            DatePrecision.HOUR: 0.02,
            DatePrecision.DAY: 0.05,
            DatePrecision.MONTH: 0.10,
            DatePrecision.YEAR: 0.15,
        }

        confidence -= precision_penalty.get(precision, 0.0)

        return max(0.0, min(1.0, confidence))

    def parse(self, text: str) -> TemporalParseResult:
        """Parse a temporal expression.

        Main entry point for parsing. Tries multiple strategies
        in order of preference:
        1. Exact ISO 8601 dates (dateutil)
        2. Date ranges (regex)
        3. Partial dates (regex)
        4. Historical periods (regex/heuristic)
        5. Natural language dates (dateparser)

        Args:
            text: Text containing temporal expression

        Returns:
            TemporalParseResult with parsed data
        """
        # Validate input
        cleaned_text = self._validate_input(text)
        if cleaned_text is None:
            return TemporalParseResult(
                original_text=text or "",
                confidence=0.0,
                parse_method="",
            )

        # Detect uncertainty first
        uncertainty = self.detect_uncertainty(cleaned_text)

        # Strip uncertainty markers for parsing
        parse_text = self._strip_uncertainty_markers(cleaned_text)

        start_date: datetime | None = None
        end_date: datetime | None = None
        parse_method = ""
        precision = DatePrecision.DAY

        # Try parsing strategies in order

        # 1. Try date ranges first (year ranges, month ranges)
        range_result = self.parse_date_range(parse_text)
        if range_result:
            start_date, end_date = range_result
            parse_method = ParseMethod.REGEX.value
            # Infer precision from the range
            precision = self.infer_precision(start_date, parse_text)
        else:
            # 2. Try partial dates first (year-only, month-year, quarters)
            # This must come before dateutil to avoid "1850" being parsed as Dec 18, 1850
            start_date = self.parse_partial_date(parse_text)
            if start_date:
                parse_method = ParseMethod.REGEX.value
                precision = self.infer_precision(start_date, parse_text)
            else:
                # 3. Try historical periods (must come before dateutil)
                historical_result = self._parse_historical_period(parse_text)
                if historical_result:
                    start_date, end_date = historical_result
                    parse_method = ParseMethod.HEURISTIC.value
                    precision = DatePrecision.YEAR
                    # Historical periods are inherently uncertain
                    if uncertainty == UncertaintyMarker.EXACT:
                        uncertainty = UncertaintyMarker.APPROXIMATE
                else:
                    # 4. Try exact date with dateutil (ISO 8601 and structured formats)
                    start_date = self._parse_with_dateutil(parse_text)
                    if start_date:
                        parse_method = ParseMethod.DATEUTIL.value
                        precision = self.infer_precision(start_date, parse_text)
                    else:
                        # 5. Try dateparser for natural language
                        start_date = self._parse_with_dateparser(parse_text)
                        if start_date:
                            parse_method = ParseMethod.DATEPARSER.value
                            precision = self.infer_precision(start_date, parse_text)

        # Calculate confidence
        confidence = self._calculate_confidence(
            parse_method=parse_method,
            has_start_date=start_date is not None,
            has_end_date=end_date is not None,
            uncertainty=uncertainty,
            precision=precision,
        )

        return TemporalParseResult(
            start_date=start_date,
            end_date=end_date,
            precision=precision,
            uncertainty=uncertainty,
            original_text=cleaned_text,
            confidence=confidence,
            parse_method=parse_method,
        )


# Timeout handler for parsing operations
class TimeoutHandler:
    """Context manager for parsing timeout protection."""

    def __init__(self, seconds: float):
        self.seconds = seconds
        self._old_handler = None

    def _timeout_handler(self, signum, frame):
        raise TemporalParseTimeout(f"Parsing timed out after {self.seconds} seconds")

    def __enter__(self):
        # Only set alarm on Unix systems
        if hasattr(signal, "SIGALRM"):
            self._old_handler = signal.signal(signal.SIGALRM, self._timeout_handler)
            signal.setitimer(signal.ITIMER_REAL, self.seconds)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if hasattr(signal, "SIGALRM"):
            signal.setitimer(signal.ITIMER_REAL, 0)
            if self._old_handler is not None:
                signal.signal(signal.SIGALRM, self._old_handler)
        return False


def create_temporal_parser(
    max_input_length: int = MAX_INPUT_LENGTH,
    timeout_seconds: float = PARSING_TIMEOUT_SECONDS,
) -> TemporalParserService:
    """Create a configured TemporalParserService instance.

    Factory function for creating parser instances.

    Args:
        max_input_length: Maximum allowed input length
        timeout_seconds: Parsing timeout in seconds

    Returns:
        Configured TemporalParserService instance
    """
    return TemporalParserService(
        max_input_length=max_input_length,
        timeout_seconds=timeout_seconds,
    )
