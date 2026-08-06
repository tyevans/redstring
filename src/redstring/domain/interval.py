"""How two `TemporalExtent`s stand to each other in time.

Pure. Two extents in, one relation out; no store, no clock, no entity.

## An extent is not an interval until precision has been applied

`TemporalExtent` records what the text said: "2023" is
`start_date=2023-01-01, end_date=None, precision=YEAR`. The *interval* it
denotes is all of 2023, and `bounds` is the single place that conversion
happens. Every comparison below runs on `Bounds`, never on the raw fields.

This is the project's failure-shape table, applied before it could bite. A
suite written entirely with day-precision dates cannot tell an implementation
that compares intervals from one that compares `precision` values, or one that
compares `start_date` alone -- on day-precision input all three agree.
"2023" and "March 2023" is the input where they disagree: they overlap, and
"2023" is emphatically not *before* "March 2023".

## Half-open, `[lower, upper)`

So that adjacent intervals do not overlap: 2022 ends at the instant 2023
begins, and exactly one of them contains that instant. The alternative -- an
inclusive upper bound at the last representable instant of the unit -- makes
correctness depend on the resolution of whatever stored the value, and
`datetime`'s microseconds, Neo4j's nanoseconds and Postgres's microseconds do
not agree on what "the last instant of 2022" is.

## Precision changes an interval; uncertainty mostly does not

`BEFORE` and `AFTER` are the only two markers that alter the bounds, and they
do it by opening one. `CIRCA` and `APPROXIMATE` are claims about confidence
rather than about extent, so they leave the interval exactly where `EXACT`
would -- see `bounds` for why widening them would mean inventing a margin.

## `None` is two different infinities

In `Bounds.lower` it means "unbounded below"; in `Bounds.upper`, "unbounded
above". They arise from `UncertaintyMarker.BEFORE` and `AFTER`, which are
genuine open bounds -- "before 1900" has no earliest moment.

The comparison helpers are therefore *position-specific*: `_lower_le` and
`_upper_le` differ only in what they do with `None`, and that difference is
the whole of their content. An implementation with a single `_le` that reads
`None` as "missing" -- or, worse, as "now" -- passes every closed-interval
test in the suite, which is why `TestOpenBounds` exists and why one of its
cases would otherwise start failing spontaneously in the year 2200.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Final, NamedTuple

from redstring.domain.temporal_parsing import widen

if TYPE_CHECKING:
    from redstring.domain.temporal import TemporalExtent


#: The width given to a moment whose extent states no precision. Not a day:
#: defaulting to a day would invent a claim the extent never made, and let one
#: exact timestamp swallow every other event that day. One microsecond is
#: `datetime`'s resolution, so this is "an instant" in the only units available.
#:
#: Module level rather than a `Bounds` attribute: an annotated name in a
#: `NamedTuple` body becomes a *field*, so `Bounds.INSTANT` would be a
#: descriptor and every `Bounds(lower, upper)` call site a `TypeError` waiting
#: for the branch that reads it.
INSTANT: Final = timedelta(microseconds=1)


class Bounds(NamedTuple):
    """A half-open interval `[lower, upper)`; `None` is infinity outwards."""

    lower: datetime | None
    upper: datetime | None


class TemporalRelation(StrEnum):
    """How the first extent stands to the second.

    Deliberately coarser than Allen's thirteen relations. `meets`,
    `starts`, `finishes` and their inverses are distinctions this data cannot
    support: they turn on exact endpoint equality, and an endpoint that came
    from widening a year is an artefact of the precision rule rather than
    something the text asserted.
    """

    BEFORE = "before"
    AFTER = "after"
    DURING = "during"
    CONTAINS = "contains"
    OVERLAPS = "overlaps"
    EQUALS = "equals"


def bounds(extent: TemporalExtent) -> Bounds | None:
    """The interval `extent` denotes, or `None` if it denotes none.

    `None` for an extent carrying no dates -- one holding only a
    `sequence_position`, say. That is not an error: sequence position orders
    events that have no dates at all, and no interval comparison applies to it.

    ## Only two markers change the interval

    `BEFORE` and `AFTER` open a bound. `EXACT`, `CIRCA`, `APPROXIMATE` and
    `INFERRED` all fall through to the ordinary closed interval, and that is a
    decision rather than an omission: "circa 1850" is a claim about *how
    confidently* 1850 is known, not about which years it might have been.
    Widening it by some margin would mean inventing the margin -- a decade? a
    century? -- and then every comparison would rest on a number nobody chose
    deliberately. The uncertainty is preserved on the extent for a caller that
    wants to weight it; the interval stays what the text said.

    ## An open marker discards the far endpoint

    "before 1900" and "after 1900" name one instant, so an extent carrying both
    a marker and an `end_date` has said something contradictory -- the marker
    says the range is open in one direction while the range says it is closed
    at both. The marker wins and `end_date` is dropped.

    Deliberate, and the alternative was considered: reading "before" as
    `(-inf, end_date)` would honour both, but it silently converts a
    contradiction into a plausible interval, and the extent that produced it is
    almost certainly a parser bug rather than a caller's intent.
    `parse_temporal` cannot construct one -- the range strategies run before
    uncertainty is folded in and never combine the two -- so this is a guard
    against hand-built extents, and it fails towards the smaller claim.
    """
    start, end = extent.start_date, extent.end_date
    if start is None and end is None:
        return None

    if extent.uncertainty is not None:
        from redstring.domain.temporal import UncertaintyMarker

        anchor = start if start is not None else end
        if extent.uncertainty is UncertaintyMarker.BEFORE:
            # Open below, and stopping where the named unit *begins*: "before
            # 1900" excludes 1900 rather than running to the end of it.
            return Bounds(None, anchor)
        if extent.uncertainty is UncertaintyMarker.AFTER:
            # Symmetrically, "after 1900" begins once 1900 is over.
            return Bounds(_widen(anchor, extent), None)

    lower = start
    upper = _widen(end if end is not None else start, extent)
    return Bounds(lower, upper)


def _widen(moment: datetime | None, extent: TemporalExtent) -> datetime | None:
    """The first instant after the unit `moment` was stated to."""
    if moment is None:
        return None
    if extent.precision is None:
        return moment + INSTANT
    return widen(moment, extent.precision)


def relate(first: TemporalExtent, second: TemporalExtent) -> TemporalRelation | None:
    """How `first` stands to `second`, or `None` if either states no date."""
    a, b = bounds(first), bounds(second)
    if a is None or b is None:
        return None
    return relate_bounds(a, b)


def relate_bounds(first: Bounds, second: Bounds) -> TemporalRelation:
    """How two intervals stand to each other. Total: always one answer.

    Public, and not merely an implementation detail of `relate`, because the
    interval open at *both* ends is not reachable from any `TemporalExtent` --
    an extent with neither date has no bounds at all -- and it is exactly the
    case where a `None`-handling mistake hides.
    """
    if first.lower == second.lower and first.upper == second.upper:
        return TemporalRelation.EQUALS
    if _disjoint(first.upper, second.lower):
        return TemporalRelation.BEFORE
    if _disjoint(second.upper, first.lower):
        return TemporalRelation.AFTER
    if _lower_le(second.lower, first.lower) and _upper_le(first.upper, second.upper):
        return TemporalRelation.DURING
    if _lower_le(first.lower, second.lower) and _upper_le(second.upper, first.upper):
        return TemporalRelation.CONTAINS
    return TemporalRelation.OVERLAPS


def _disjoint(upper: datetime | None, lower: datetime | None) -> bool:
    """Does an interval ending at `upper` finish at or before one starting at
    `lower`? An unbounded end never does, and neither does an unbounded start.
    """
    return upper is not None and lower is not None and upper <= lower


def _lower_le(x: datetime | None, y: datetime | None) -> bool:
    """`x <= y` for two *lower* bounds, where `None` is minus infinity."""
    if x is None:
        return True
    if y is None:
        return False
    return x <= y


def _upper_le(x: datetime | None, y: datetime | None) -> bool:
    """`x <= y` for two *upper* bounds, where `None` is plus infinity."""
    if x is None:
        return y is None
    if y is None:
        return True
    return x <= y
