"""Temporal data reaches an entity while it is being built, not afterwards.

`Entity` already carries `temporal` and entities already reach the log inside
`DocumentExtracted`, so there is no second pass and no second event.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from redstring.domain.entity import ExtractionMethod
from redstring.domain.temporal import DatePrecision, UncertaintyMarker
from redstring.extraction.mapping import map_extraction
from redstring.extraction.merging import merge_extractions
from redstring.extraction.schema import ExtractedEntity, Extraction

TENANT = uuid4()
SOURCE = "doc-1"
PUBLISHED = datetime(2020, 6, 15, tzinfo=UTC)


def extraction(*entities: ExtractedEntity) -> Extraction:
    return Extraction(entities=list(entities))


def mapped(*entities: ExtractedEntity, reference_date=PUBLISHED):
    return map_extraction(
        extraction(*entities),
        tenant_id=TENANT,
        source_id=SOURCE,
        model="test-model",
        reference_date=reference_date,
    )


class TestEnrichmentHappensDuringMapping:
    def test_a_temporal_expression_becomes_an_extent_on_the_entity(self):
        result = mapped(
            ExtractedEntity(
                name="Battle of Hastings", entity_type="Event", temporal_expression="1066"
            )
        )
        (entity,) = result.entities
        assert entity.temporal is not None
        assert entity.temporal.start_date == datetime(1066, 1, 1, tzinfo=UTC)
        assert entity.temporal.precision is DatePrecision.YEAR
        assert entity.temporal.original_text == "1066"

    def test_an_entity_with_no_expression_has_no_extent(self):
        result = mapped(ExtractedEntity(name="Ada Lovelace", entity_type="Person"))
        (entity,) = result.entities
        assert entity.temporal is None
        assert entity.is_temporal is False

    def test_an_unparseable_expression_leaves_the_entity_undated_not_dropped(self):
        """A bad temporal phrase is one bad field, not a bad entity."""
        result = mapped(
            ExtractedEntity(
                name="Ada Lovelace", entity_type="Person", temporal_expression="at some point"
            )
        )
        (entity,) = result.entities
        assert entity.name == "Ada Lovelace"
        assert entity.temporal is None
        assert result.dropped_entities == 0

    def test_a_sequence_position_survives_without_a_date(self):
        """Sequence position is how undated events are ordered at all."""
        result = mapped(
            ExtractedEntity(name="the coronation", entity_type="Event", sequence_position=2)
        )
        (entity,) = result.entities
        assert entity.temporal is not None
        assert entity.temporal.sequence_position == 2
        assert entity.temporal.start_date is None

    def test_a_sequence_position_rides_alongside_a_date(self):
        result = mapped(
            ExtractedEntity(
                name="the coronation",
                entity_type="Event",
                temporal_expression="1066",
                sequence_position=2,
            )
        )
        (entity,) = result.entities
        assert entity.temporal.sequence_position == 2
        assert entity.temporal.start_date == datetime(1066, 1, 1, tzinfo=UTC)

    def test_the_uncertainty_the_text_carries_reaches_the_entity(self):
        result = mapped(
            ExtractedEntity(name="a fresco", entity_type="Work", temporal_expression="circa 1500")
        )
        (entity,) = result.entities
        assert entity.temporal.uncertainty is UncertaintyMarker.CIRCA


class TestTheReferenceDateReachesTheParser:
    def test_a_relative_expression_resolves_against_the_supplied_date(self):
        result = mapped(
            ExtractedEntity(name="the merger", entity_type="Event", temporal_expression="last year")
        )
        (entity,) = result.entities
        assert entity.temporal.start_date.year == 2019

    def test_the_same_document_read_from_two_dates_dates_its_entities_differently(self):
        """The replay hazard, stated as a test. If this ever stops failing to
        differ, something is reading a clock."""
        entity = ExtractedEntity(
            name="the merger", entity_type="Event", temporal_expression="last year"
        )
        first = mapped(entity, reference_date=datetime(2020, 6, 15, tzinfo=UTC))
        second = mapped(entity, reference_date=datetime(2031, 6, 15, tzinfo=UTC))
        assert first.entities[0].temporal.start_date != second.entities[0].temporal.start_date

    def test_mapping_the_same_input_twice_gives_the_same_entities(self):
        entity = ExtractedEntity(
            name="the merger", entity_type="Event", temporal_expression="last year"
        )
        assert mapped(entity).entities == mapped(entity).entities

    def test_a_relative_expression_with_no_reference_date_is_counted_not_raised(self):
        """`map_extraction` never raises for something the *model* did. An
        undated document is the caller's gap, and the counter is what makes
        dropping the date a decision rather than a silent loss."""
        result = mapped(
            ExtractedEntity(
                name="the merger", entity_type="Event", temporal_expression="last year"
            ),
            reference_date=None,
        )
        (entity,) = result.entities
        assert entity.temporal is None
        assert result.undatable_relative == 1

    def test_an_absolute_expression_needs_no_reference_date(self):
        result = mapped(
            ExtractedEntity(name="the treaty", entity_type="Event", temporal_expression="1919"),
            reference_date=None,
        )
        assert result.entities[0].temporal.start_date == datetime(1919, 1, 1, tzinfo=UTC)
        assert result.undatable_relative == 0


class TestMergingAcrossChunks:
    def test_the_counter_sums_across_chunks(self):
        relative = ExtractedEntity(
            name="the merger", entity_type="Event", temporal_expression="last year"
        )
        parts = [mapped(relative, reference_date=None) for _ in range(3)]
        assert merge_extractions(parts).undatable_relative == 3

    def test_a_dated_mention_beats_an_undated_one_regardless_of_order(self):
        """Overlapping windows report one entity twice, and only the window
        holding the date phrase can date it. Whichever chunk that is."""
        dated = mapped(
            ExtractedEntity(
                name="Battle of Hastings", entity_type="Event", temporal_expression="1066"
            )
        )
        undated = mapped(ExtractedEntity(name="Battle of Hastings", entity_type="Event"))
        assert merge_extractions([dated, undated]).entities[0].temporal is not None
        assert merge_extractions([undated, dated]).entities[0].temporal is not None

    def test_a_dated_mention_outranks_a_better_described_undated_one(self):
        """The input that distinguishes "prefer the dated mention" from
        "prefer the fuller description". With both mentions undescribed the
        two agree, because an absent extent sorts below a present one in the
        tail anyway -- so a test using undescribed mentions proves nothing
        about where the flag sits in the order."""
        dated = mapped(
            ExtractedEntity(
                name="Battle of Hastings",
                entity_type="Event",
                description="A battle.",
                temporal_expression="1066",
            )
        )
        better_described = mapped(
            ExtractedEntity(
                name="Battle of Hastings",
                entity_type="Event",
                description="A battle fought in the south of England, at some length.",
            )
        )
        for order in ([dated, better_described], [better_described, dated]):
            (entity,) = merge_extractions(order).entities
            assert entity.temporal is not None
            assert entity.description == "A battle."

    def test_two_mentions_disagreeing_only_about_the_date_do_not_depend_on_order(self):
        """`preference` has to be total over `temporal` now that extraction
        populates it. Two mentions tied on every other field and carrying
        different extents would otherwise be decided by arrival order, in a
        durable log."""
        first = mapped(
            ExtractedEntity(name="the siege", entity_type="Event", temporal_expression="1453")
        )
        second = mapped(
            ExtractedEntity(name="the siege", entity_type="Event", temporal_expression="1454")
        )
        forwards = merge_extractions([first, second]).entities[0].temporal.start_date
        backwards = merge_extractions([second, first]).entities[0].temporal.start_date
        assert forwards == backwards


class TestNoSecondEvent:
    def test_enrichment_adds_no_event_type(self):
        """The ADR's granularity decision is permanent: temporal data rides
        inside `DocumentExtracted`. A re-extraction under a new model version
        is how an entity's dates improve."""
        import redstring.events.document as document_events

        assert not [
            name
            for name in dir(document_events)
            if "Temporal" in name or "Dated" in name or "Enriched" in name
        ]


class TestMappingStillRefusesWhatItAlwaysDid:
    def test_a_model_bearing_method_still_needs_a_model(self):
        with pytest.raises(ValueError, match="must record which model"):
            map_extraction(
                extraction(ExtractedEntity(name="a", entity_type="b")),
                tenant_id=TENANT,
                source_id=SOURCE,
                model=None,
                reference_date=PUBLISHED,
                method=ExtractionMethod.LLM,
            )
