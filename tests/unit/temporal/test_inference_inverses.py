"""Structural invariant tests for canonical temporal relation inverses.

Verifies that CANONICAL_INVERSES properly covers excluded relations (AFTER, DURING)
and that temporal inference never produces non-canonical relation directions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from redstring.domain.entity import Entity
from redstring.domain.interval import Bounds, TemporalRelation, relate_bounds
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.temporal import DatePrecision, TemporalExtent
from redstring.temporal.inference import (
    CANONICAL_INVERSES,
    INFERRED_RELATIONS,
    infer_relations,
)

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


class TestTheInvariantIsStructural:
    """`INFERRED_RELATIONS` promises that `AFTER` and `DURING` never come out.
    These assert it against the canonicalisation itself rather than against
    what the sort happens to feed it.

    Why that matters here specifically: after sorting, `relate_bounds` cannot
    return `AFTER` at all -- it would require an interval whose upper bound is
    below its own lower bound. So deleting the `AFTER` entry from `CANONICAL_INVERSES`
    passes every behavioural test in this file. That is the same species of
    reasoning that produced the `DURING` defect: an invariant resting on an
    argument about sort order rather than on the code. The entry stays, and
    these tests make it live."""

    def test_the_inverse_map_covers_every_relation_the_default_set_excludes(self) -> None:
        excluded = set(TemporalRelation) - set(INFERRED_RELATIONS)
        assert set(CANONICAL_INVERSES) == excluded

    def test_the_inverse_map_lands_inside_the_default_set(self) -> None:
        assert set(CANONICAL_INVERSES.values()) <= set(INFERRED_RELATIONS)

    @pytest.mark.parametrize(("relation", "inverse"), sorted(CANONICAL_INVERSES.items()))
    def test_each_inverse_matches_what_relate_bounds_says_about_the_swap(
        self, relation: TemporalRelation, inverse: TemporalRelation
    ) -> None:
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
    def test_no_excluded_relation_ever_reaches_the_output(
        self, starts: list[int], widths: list[int]
    ) -> None:
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
        assert not {r.relation for r in relations} & set(CANONICAL_INVERSES)

    @given(
        starts=st.lists(st.integers(1500, 2500), min_size=2, max_size=5),
        widths=st.lists(st.integers(0, 40), min_size=2, max_size=5),
    )
    @settings(max_examples=300)
    def test_the_default_set_loses_no_pair_that_relate_calls_related(
        self, starts: list[int], widths: list[int]
    ) -> None:
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
