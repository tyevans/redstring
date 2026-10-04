"""Candidate grouping structures for consolidation passes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redstring.consolidation.candidates import ScoredCandidate
    from redstring.domain.entity import Entity


__all__ = ["BandedCandidates"]


@dataclass(frozen=True, slots=True)
class BandedCandidates:
    """One subject's candidates, split by what the score alone settled.

    Carried as one object rather than three parallel lists because
    `resolve_many` holds a collection of these across an await boundary, and
    lists kept aligned by hand are how a verdict gets recorded against the
    wrong pair.
    """

    #: Already resolved through aliases. Not the subject the caller passed.
    subject: Entity
    #: Candidate and the reason it is being merged, carried together -- a
    #: merge attributed to the wrong reason is an audit trail that lies while
    #: looking complete.
    confirmed: list[tuple[ScoredCandidate, str]]
    #: The band. Empty unless an adjudicator is going to be asked.
    undecided: list[ScoredCandidate]
