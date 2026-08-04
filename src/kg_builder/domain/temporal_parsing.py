"""Text to `TemporalExtent`. Pure, and explicitly dated.

No store, no clock, no config object. One function of two arguments.

## The reference date is a parameter because a replay would otherwise diverge

"last year" is not a date; it is a date *and* a vantage point. A parser that
reads the vantage point from `date.today()` answers the same question
differently on different days, and this is an event-sourced system: entities
reach the log inside `DocumentExtracted`, and a re-extraction of the same
document under a new model version is the supported way an entity's temporal
data improves. With a hidden clock, that re-extraction silently produces a
**different graph** from the original run, and every test written on the day
the code was written agrees with every test run on the day it was reviewed.

So `reference_date` is required, and there is nowhere in this module that a
clock can be read. `SourceDocument.published_at` is the natural value for it.

### What happens when the caller has no reference date

`parse_temporal(text, reference_date=None)` does not fall back to today, and it
does not silently return `None` for anything it could not date. It raises
`AmbiguousReferenceDateError` -- but only for text whose meaning *actually*
depends on the vantage point. "14 July 1789" is fine without one.

That distinction is not a keyword list ("last", "ago", "next", ...), which
would be an incomplete enumeration of a natural language and would fail open.
It is measured: parse the text twice against two reference dates decades apart
and compare the results. Identical results mean the text did not consult the
vantage point, which is exactly the property being asserted, for whatever
reason the underlying library had. Different results mean it did.

The same probe covers a hazard that is *not* spelled `today` anywhere in our
source: `dateutil.parser.parse` fills components the text omits from the
current date, so "March 15" acquires this year's year. Because the probe drives
`default=` as well as `RELATIVE_BASE`, that case is caught by the same test
rather than needing to be anticipated.

## Precision carries width; `end_date` carries a stated range

"2023" parses to `start_date=2023-01-01, end_date=None, precision=YEAR`, not to
an explicit 12-month span. The extent of a bare year is recoverable from its
precision, and `domain.interval` is the one place that widening happens -- so
there is one rule rather than one here and another there that can disagree.

`end_date` is set only when the text states a second endpoint: a range
("1914-1918"), or a period whose span is a convention rather than a precision
("19th century").
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Final, NamedTuple

from dateutil import parser as dateutil_parser
from dateutil.relativedelta import relativedelta

from kg_builder.domain.temporal import DatePrecision, TemporalExtent, UncertaintyMarker

#: Longer than any date expression and short enough that a pathological regex
#: cannot be handed a novel. Text above this is not truncated -- a truncated
#: date expression parses to a *wrong* date, which is worse than no date.
MAX_INPUT_LENGTH: Final = 500

#: The two vantage points the ambiguity probe uses when the caller supplied
#: none. Far apart on purpose: any expression that consults a reference date at
#: all resolves differently across 36 years. Both are arbitrary and neither
#: reaches a result -- a text that survives the probe did not look at them.
_PROBE_A: Final = datetime(1999, 6, 15, tzinfo=UTC)
_PROBE_B: Final = datetime(2035, 2, 20, tzinfo=UTC)

_MONTH: Final = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)

_MONTH_NAMES: Final = (
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


class AmbiguousReferenceDateError(ValueError):
    """The text's meaning depends on a reference date and none was given.

    Not raised speculatively: raised only after the text has been shown to
    resolve differently against two vantage points.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__(
            f"{text!r} resolves differently depending on when it was written, so "
            f"parsing it without a reference date would make a re-extraction "
            f"produce a different graph. Pass SourceDocument.published_at, or "
            f"whatever date this text should be read as of."
        )


class _Parsed(NamedTuple):
    """What a strategy found, before uncertainty is folded back in."""

    start: datetime
    end: datetime | None
    precision: DatePrecision


# --- Uncertainty ------------------------------------------------------------

#: Order matters: the first marker whose pattern matches wins, and `CIRCA`
#: precedes `APPROXIMATE` so that "circa" is not also read as vague.
_UNCERTAINTY_PATTERNS: Final[tuple[tuple[UncertaintyMarker, tuple[re.Pattern[str], ...]], ...]] = (
    (
        UncertaintyMarker.CIRCA,
        (
            re.compile(r"\bcirca\b", re.IGNORECASE),
            re.compile(r"\bc\.\s*(?=\d)", re.IGNORECASE),
            re.compile(r"\bca\.\s*(?=\d)", re.IGNORECASE),
        ),
    ),
    (
        UncertaintyMarker.APPROXIMATE,
        (
            re.compile(r"\baround\b", re.IGNORECASE),
            re.compile(r"\bapproximately\b", re.IGNORECASE),
            re.compile(r"\babout\b", re.IGNORECASE),
            re.compile(r"\broughly\b", re.IGNORECASE),
        ),
    ),
    (
        UncertaintyMarker.BEFORE,
        (
            re.compile(r"\bbefore\b", re.IGNORECASE),
            re.compile(r"\bprior\s+to\b", re.IGNORECASE),
            re.compile(r"\buntil\b", re.IGNORECASE),
            re.compile(r"\bno\s+later\s+than\b", re.IGNORECASE),
        ),
    ),
    (
        UncertaintyMarker.AFTER,
        (
            re.compile(r"\bafter\b", re.IGNORECASE),
            re.compile(r"\bsince\b", re.IGNORECASE),
            re.compile(r"\bno\s+earlier\s+than\b", re.IGNORECASE),
        ),
    ),
)

#: Stripped before parsing. Kept separate from the detection patterns above
#: because they differ: detection matches "before", removal takes the trailing
#: space with it too, or `dateutil` sees a leading blank.
#:
#: `\s*` rather than `\s+` so that "circa1850" strips as readily as
#: "circa 1850", and so that text which is *only* a marker strips to nothing --
#: which is what makes `parse_temporal`'s empty-after-stripping guard
#: reachable. With `\s+` that guard was dead code, and "" is precisely the
#: input `dateutil` reads as "today, in every field".
_MARKERS_TO_STRIP: Final = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bcirca\s*",
        r"\bc\.\s*(?=\d)",
        r"\bca\.\s*(?=\d)",
        r"\baround\s*",
        r"\bapproximately\s*",
        r"\babout\s*",
        r"\broughly\s*",
        r"\bbefore\s*",
        r"\bprior\s+to\s*",
        r"\bno\s+later\s+than\s*",
        r"\buntil\s+",
        r"\bafter\s*",
        r"\bsince\s*",
        r"\bno\s+earlier\s+than\s*",
    )
)


def detect_uncertainty(text: str) -> UncertaintyMarker:
    """The first uncertainty marker `text` carries, or `EXACT`."""
    for marker, patterns in _UNCERTAINTY_PATTERNS:
        if any(pattern.search(text) for pattern in patterns):
            return marker
    return UncertaintyMarker.EXACT


def _strip_markers(text: str) -> str:
    for pattern in _MARKERS_TO_STRIP:
        text = pattern.sub("", text)
    return text.strip()


# --- Strategies, each a pure function of (text, base) -----------------------

_YEAR_RANGE = re.compile(r"^(\d{4})\s*(?:[-\u2013\u2014]|to)\s*(\d{4})$", re.IGNORECASE)
_MONTH_RANGE = re.compile(
    rf"^({_MONTH})\s*(?:[-\u2013\u2014]|to)\s*({_MONTH})\s+(\d{{4}})$", re.IGNORECASE
)
_QUARTER_RANGE = re.compile(r"^Q([1-4])\s*[-\u2013\u2014]\s*Q([1-4])\s+(\d{4})$", re.IGNORECASE)


def _parse_range(text: str) -> _Parsed | None:
    """A stated two-endpoint span. Anchored: a range inside a sentence is not
    one, and an unanchored pattern reads "born 1850, died 1910" as a range."""
    match = _YEAR_RANGE.match(text)
    if match:
        start, end = int(match.group(1)), int(match.group(2))
        if end < start:
            return None
        return _Parsed(
            datetime(start, 1, 1, tzinfo=UTC), datetime(end, 1, 1, tzinfo=UTC), DatePrecision.YEAR
        )

    match = _MONTH_RANGE.match(text)
    if match:
        year = int(match.group(3))
        first = _month_number(match.group(1))
        last = _month_number(match.group(2))
        if last < first:
            return None
        return _Parsed(
            datetime(year, first, 1, tzinfo=UTC),
            datetime(year, last, 1, tzinfo=UTC),
            DatePrecision.MONTH,
        )

    match = _QUARTER_RANGE.match(text)
    if match:
        first, last, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if last < first:
            return None
        return _Parsed(
            datetime(year, (first - 1) * 3 + 1, 1, tzinfo=UTC),
            datetime(year, (last - 1) * 3 + 1, 1, tzinfo=UTC),
            DatePrecision.MONTH,
        )
    return None


#: Every spelling `_MONTH` accepts, mapped to its number. Built from
#: `_MONTH_NAMES` rather than written out so the two cannot drift.
_MONTH_NUMBERS: Final = {
    spelling.lower(): index
    for index, name in enumerate(_MONTH_NAMES, start=1)
    for spelling in ({name, name[:3]} if name != "September" else {name, "Sep", "Sept"})
}


def _month_number(name: str) -> int:
    """The month `name` denotes. Total, not partial.

    `_MONTH` is the only way into this function and it matches nothing but
    these spellings, so a `None` return would be a branch no input can reach --
    and an unreachable guard is worse than none: it reads as a handled case and
    is never exercised, so nothing notices when it stops being unreachable.
    A `KeyError` here means `_MONTH` and `_MONTH_NAMES` have drifted apart,
    which is a bug rather than bad input.
    """
    return _MONTH_NUMBERS[name.lower()]


_YEAR_ONLY = re.compile(r"^(\d{4})$")
_MONTH_YEAR = re.compile(rf"^({_MONTH})\s+(\d{{4}})$", re.IGNORECASE)
_QUARTER = re.compile(r"^Q([1-4])\s+(\d{4})$", re.IGNORECASE)
_DECADE = re.compile(r"^(?:the\s+)?(\d{3})0s$", re.IGNORECASE)


def _parse_partial(text: str) -> _Parsed | None:
    """A date the text states to a coarser grain than a day.

    Ahead of `dateutil` on purpose: `dateutil` reads "1850" as the 18th of the
    current month, which is both wrong and day-precise.
    """
    match = _YEAR_ONLY.match(text)
    if match:
        return _Parsed(datetime(int(match.group(1)), 1, 1, tzinfo=UTC), None, DatePrecision.YEAR)

    match = _MONTH_YEAR.match(text)
    if match:
        return _Parsed(
            datetime(int(match.group(2)), _month_number(match.group(1)), 1, tzinfo=UTC),
            None,
            DatePrecision.MONTH,
        )

    match = _QUARTER.match(text)
    if match:
        quarter, year = int(match.group(1)), int(match.group(2))
        return _Parsed(
            datetime(year, (quarter - 1) * 3 + 1, 1, tzinfo=UTC), None, DatePrecision.MONTH
        )

    match = _DECADE.match(text)
    if match:
        decade = int(match.group(1)) * 10
        return _Parsed(
            datetime(decade, 1, 1, tzinfo=UTC),
            datetime(decade + 9, 1, 1, tzinfo=UTC),
            DatePrecision.YEAR,
        )
    return None


_CENTURY_PORTION = re.compile(r"^(early|mid|late)\s+(\d{1,2})(?:st|nd|rd|th)\s+century$", re.I)
_CENTURY = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)\s+century$", re.IGNORECASE)


def _parse_period(text: str) -> _Parsed | None:
    """A named span whose width is a convention, not a precision.

    Named eras ("medieval", "renaissance", "ancient") are deliberately *not*
    here. The old parser dated "medieval period" to 500-1500 CE, which is a
    claim about historiography rather than about the text, and it made every
    passing mention of the word into a dated event.
    """
    match = _CENTURY_PORTION.match(text)
    if match:
        portion, century = match.group(1).lower(), int(match.group(2))
        if century < 1:
            return None
        base = (century - 1) * 100
        first, last = {
            "early": (base + 1, base + 33),
            "mid": (base + 34, base + 66),
            "late": (base + 67, base + 100),
        }[portion]
        return _Parsed(
            datetime(first, 1, 1, tzinfo=UTC),
            datetime(last, 1, 1, tzinfo=UTC),
            DatePrecision.YEAR,
        )

    match = _CENTURY.match(text)
    if match:
        century = int(match.group(1))
        if century < 1:
            return None
        return _Parsed(
            datetime((century - 1) * 100 + 1, 1, 1, tzinfo=UTC),
            datetime(century * 100, 1, 1, tzinfo=UTC),
            DatePrecision.YEAR,
        )
    return None


_WRITES_MINUTES = re.compile(r"\d{1,2}:\d{2}")
_WRITES_HOUR_ONLY = re.compile(r"\b\d{1,2}\s*(?:am|pm|a\.m\.|p\.m\.|o'clock)", re.IGNORECASE)


def _time_precision(text: str) -> DatePrecision:
    """`DAY`, unless the text writes a clock time.

    Read off the *text*, never off the parsed datetime: every datetime has a
    minute field, so inferring from the value alone would call midnight
    minute-precise and make "15 March 2024" finer than the text supports.

    Precision is what the text **writes**, not what its digits happen to be.
    The obvious-looking refinement -- "the minutes are `00`, so it is really
    hour precision" -- is value-sniffing, and it is wrong in the same way for
    the same reason that "March 1" is not month precision. It also cannot be
    applied consistently: `14:00:00` would then be hour-precise while `14:00:01`
    was minute-precise, so the granularity of an event would depend on when it
    happened. `HOUR` is reached from text that writes only an hour.
    """
    if _WRITES_MINUTES.search(text):
        return DatePrecision.MINUTE
    if _WRITES_HOUR_ONLY.search(text):
        return DatePrecision.HOUR
    return DatePrecision.DAY


def _parse_absolute(text: str, base: datetime) -> _Parsed | None:
    """A structured date. `base` fills only components the text omits."""
    try:
        parsed = dateutil_parser.parse(text, fuzzy=False, default=base)
    except (ValueError, OverflowError):
        return None
    # Always aware: `base` is required aware and `dateutil` takes `tzinfo` from
    # the default for any component the text omits, while text stating an
    # offset supplies its own. A `tzinfo is None` branch here would be
    # unreachable, and `TemporalExtent` rejects a naive datetime anyway, so a
    # mistake in this reasoning fails loudly rather than storing a naive value.
    return _Parsed(parsed, None, _time_precision(text))


def _parse_natural(text: str, base: datetime) -> _Parsed | None:
    """Natural language, including relative expressions, resolved from `base`.

    `dateparser` is imported here rather than at module scope, and that is not
    style. Importing it costs a quarter of a second -- it builds language
    detection tables -- and this module is reached from
    `extraction/mapping.py`, so at module scope every importer of extraction
    pays it whether or not any document ever contains a date. It first showed
    up as two hypothesis properties in *unrelated* test files exceeding their
    200ms deadline, which is an obscure way to be told about an import cost.

    Deferred, the cost is paid once, by the first text that gets past the four
    cheaper strategies. B50 has the measurement.
    """
    import dateparser

    try:
        parsed = dateparser.parse(
            text,
            settings={
                "RETURN_AS_TIMEZONE_AWARE": True,
                "TIMEZONE": "UTC",
                "TO_TIMEZONE": "UTC",
                "RELATIVE_BASE": base.replace(tzinfo=None),
                "PREFER_DATES_FROM": "past",
            },
        )
    except Exception:  # pragma: no cover
        # `dateparser` raises a wide and undocumented set out of its language
        # detection -- `re.error` and `IndexError` have both been seen, and it
        # documents neither. Uncovered because no input is known that reaches
        # it; kept because "no input is known" is not "no input exists", and
        # the alternative is one odd phrase in one chunk failing a whole
        # document's extraction.
        return None
    if parsed is None:
        return None
    # Aware by `RETURN_AS_TIMEZONE_AWARE`; see `_parse_absolute` on why there
    # is no fallback branch.
    return _Parsed(parsed, None, _time_precision(text))


def _attempt(text: str, base: datetime) -> _Parsed | None:
    """Every strategy, in the one order that works, as a function of `base`.

    Purity in `base` is what makes the ambiguity probe sound: if this took the
    clock from anywhere else, two probes could agree while a third day
    disagreed.
    """
    for strategy in (_parse_range, _parse_partial, _parse_period):
        found = strategy(text)
        if found is not None:
            return found
    return _parse_absolute(text, base) or _parse_natural(text, base)


# --- The entry points -------------------------------------------------------


def parse_temporal(text: str, *, reference_date: datetime | None) -> TemporalExtent | None:
    """The extent `text` denotes, read as of `reference_date`.

    Args:
        text: A temporal expression. Anything else yields `None`.
        reference_date: The vantage point relative expressions resolve
            against -- `SourceDocument.published_at`, typically. Must be
            timezone-aware. `None` is permitted and means "this text had
            better not need one", which is checked rather than assumed.

    Returns:
        A `TemporalExtent`, or `None` if `text` states no date. `None` is a
        normal outcome: most entity mentions are not dated.

    Raises:
        AmbiguousReferenceDateError: `reference_date` is `None` and the text
            resolves differently depending on when it was written.
        ValueError: `reference_date` is naive.
    """
    if reference_date is not None and reference_date.tzinfo is None:
        raise ValueError("reference_date must be timezone-aware")

    cleaned = text.strip()
    if not cleaned or len(cleaned) > MAX_INPUT_LENGTH:
        return None

    uncertainty = detect_uncertainty(cleaned)
    stripped = _strip_markers(cleaned)
    if not stripped:
        return None

    if reference_date is not None:
        found = _attempt(stripped, reference_date)
    else:
        against_a = _attempt(stripped, _PROBE_A)
        against_b = _attempt(stripped, _PROBE_B)
        if against_a != against_b:
            raise AmbiguousReferenceDateError(cleaned)
        found = against_a

    if found is None:
        return None

    # A period is uncertain by construction: "19th century" is a convention
    # about where a century's edges fall, not a claim the text made.
    if found.end is not None and _parse_period(stripped) is not None:
        uncertainty = (
            UncertaintyMarker.APPROXIMATE if uncertainty is UncertaintyMarker.EXACT else uncertainty
        )

    return TemporalExtent(
        start_date=found.start,
        end_date=found.end,
        precision=found.precision,
        uncertainty=uncertainty,
        original_text=cleaned,
    )


_RENDER_PREFIX: Final = {
    UncertaintyMarker.EXACT: "",
    UncertaintyMarker.CIRCA: "circa ",
    UncertaintyMarker.APPROXIMATE: "around ",
    UncertaintyMarker.BEFORE: "before ",
    UncertaintyMarker.AFTER: "after ",
}


def render_temporal(extent: TemporalExtent) -> str | None:
    """`extent` as text this module parses back to the same extent, or `None`.

    Exists for the round-trip property, which is the only test shape that can
    catch a strategy that parses a form the previous strategy has already
    quietly mangled. `None` for anything it cannot render *faithfully* -- an
    approximate rendering would make the property pass by lowering the bar.
    """
    if extent.start_date is None or extent.precision is None:
        return None
    if extent.sequence_position is not None or extent.publication_date is not None:
        return None
    prefix = _RENDER_PREFIX.get(extent.uncertainty or UncertaintyMarker.EXACT)
    if prefix is None:
        return None

    start, end, precision = extent.start_date, extent.end_date, extent.precision

    if precision is DatePrecision.YEAR:
        if start != datetime(start.year, 1, 1, tzinfo=UTC):
            return None
        if end is None:
            return f"{prefix}{start.year}"
        if end != datetime(end.year, 1, 1, tzinfo=UTC) or end.year <= start.year:
            return None
        return f"{prefix}{start.year}-{end.year}"

    if end is not None:
        # Month and day ranges render only in forms this module's range
        # patterns accept, and those forms cannot express a cross-year span.
        return None

    if precision is DatePrecision.MONTH:
        if start != datetime(start.year, start.month, 1, tzinfo=UTC):
            return None
        return f"{prefix}{_MONTH_NAMES[start.month - 1]} {start.year}"

    if precision is DatePrecision.DAY:
        if start != datetime(start.year, start.month, start.day, tzinfo=UTC):
            return None
        return f"{prefix}{start.day} {_MONTH_NAMES[start.month - 1]} {start.year}"

    return None


def widen(moment: datetime, precision: DatePrecision) -> datetime:
    """The first instant *after* the unit of `precision` containing `moment`.

    Half-open on purpose. The alternative -- the last representable instant
    inside the unit -- forces every comparison to know the resolution of the
    underlying store, and `datetime`'s microseconds, Neo4j's nanoseconds and
    Postgres's microseconds do not agree on what that is.

    Lives here rather than in `domain.interval` because it is the exact inverse
    of what the parsers above do when they floor a partial date, and the two
    have to stay each other's inverse.
    """
    if precision is DatePrecision.YEAR:
        return datetime(moment.year, 1, 1, tzinfo=moment.tzinfo) + relativedelta(years=1)
    if precision is DatePrecision.MONTH:
        return datetime(moment.year, moment.month, 1, tzinfo=moment.tzinfo) + relativedelta(
            months=1
        )
    if precision is DatePrecision.DAY:
        return datetime(
            moment.year, moment.month, moment.day, tzinfo=moment.tzinfo
        ) + relativedelta(days=1)
    if precision is DatePrecision.HOUR:
        return moment.replace(minute=0, second=0, microsecond=0) + relativedelta(hours=1)
    return moment.replace(second=0, microsecond=0) + relativedelta(minutes=1)
