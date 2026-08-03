"""Unit tests for TimelineExportService."""

import csv
import io
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from kg_builder.schemas.timeline import (
    DatePrecision,
    TemporalRelationship,
    TimelineEvent,
    UncertaintyMarker,
)
from kg_builder.services.timeline_export import (
    FORMULA_INJECTION_CHARS,
    TimelineExportService,
    get_timeline_export_service,
)


@pytest.fixture
def sample_job_id():
    """Generate a sample job ID."""
    return uuid4()


@pytest.fixture
def sample_page_id():
    """Generate a sample page ID."""
    return uuid4()


@pytest.fixture
def sample_event(sample_page_id):
    """Create a sample TimelineEvent."""
    return TimelineEvent(
        id=uuid4(),
        name="Historical Event",
        description="An important historical event",
        entity_type="event",
        start_date=datetime(2023, 6, 15, 12, 0, 0, tzinfo=UTC),
        end_date=datetime(2023, 6, 16, 12, 0, 0, tzinfo=UTC),
        precision=DatePrecision.DAY,
        uncertainty=UncertaintyMarker.EXACT,
        original_text="June 15, 2023",
        sequence_position=1,
        source_page_id=sample_page_id,
        source_url="https://example.com/page1",
        involved_entities=[],
    )


@pytest.fixture
def sample_event_sequence_only(sample_page_id):
    """Create a sample TimelineEvent with only sequence position."""
    return TimelineEvent(
        id=uuid4(),
        name="Narrative Event",
        description="An undated narrative event",
        entity_type="event",
        start_date=None,
        end_date=None,
        precision=None,
        uncertainty=None,
        original_text=None,
        sequence_position=5,
        source_page_id=sample_page_id,
        source_url="https://example.com/page2",
        involved_entities=[],
    )


@pytest.fixture
def sample_event_year_precision(sample_page_id):
    """Create a sample TimelineEvent with year precision."""
    return TimelineEvent(
        id=uuid4(),
        name="Year Event",
        description="An event with year precision",
        entity_type="event",
        start_date=datetime(2020, 1, 1, tzinfo=UTC),
        end_date=datetime(2020, 12, 31, tzinfo=UTC),
        precision=DatePrecision.YEAR,
        uncertainty=UncertaintyMarker.APPROXIMATE,
        original_text="around 2020",
        sequence_position=None,
        source_page_id=sample_page_id,
        source_url="https://example.com/page3",
        involved_entities=[],
    )


@pytest.fixture
def sample_relationship(sample_event):
    """Create a sample TemporalRelationship."""
    return TemporalRelationship(
        id=uuid4(),
        source_event_id=sample_event.id,
        target_event_id=uuid4(),
        source_event_name="Event A",
        target_event_name="Event B",
        relationship_type="precedes",
        confidence=0.95,
        evidence="Event A happened before Event B",
        is_inferred=False,
    )


class TestTimelineExportServiceExportJson:
    """Tests for JSON export."""

    def test_export_json_basic(self, sample_event, sample_job_id):
        """Test basic JSON export."""
        service = TimelineExportService()
        result = service.export_json(
            events=[sample_event],
            job_id=sample_job_id,
            user_id="test-user",
        )

        assert "events" in result
        assert len(result["events"]) == 1
        assert result["events"][0]["name"] == sample_event.name
        assert result["events"][0]["id"] == str(sample_event.id)

    def test_export_json_with_metadata(self, sample_event, sample_job_id):
        """Test JSON export includes metadata."""
        service = TimelineExportService()
        result = service.export_json(
            events=[sample_event],
            include_meta=True,
            job_id=sample_job_id,
            user_id="test-user",
            filters_applied={"entity_types": ["event"]},
        )

        assert "_metadata" in result
        assert result["_metadata"]["format"] == "json"
        assert result["_metadata"]["event_count"] == 1
        assert result["_metadata"]["job_id"] == str(sample_job_id)
        assert result["_metadata"]["exported_by"] == "test-user"
        assert "exported_at" in result["_metadata"]
        assert result["_metadata"]["filters_applied"]["entity_types"] == ["event"]

    def test_export_json_without_metadata(self, sample_event):
        """Test JSON export without metadata."""
        service = TimelineExportService()
        result = service.export_json(
            events=[sample_event],
            include_meta=False,
        )

        assert "_metadata" not in result

    def test_export_json_with_relationships(
        self,
        sample_event,
        sample_relationship,
    ):
        """Test JSON export with relationships."""
        service = TimelineExportService()
        result = service.export_json(
            events=[sample_event],
            relationships=[sample_relationship],
            include_relationships=True,
        )

        assert "relationships" in result
        assert len(result["relationships"]) == 1
        assert result["relationships"][0]["relationship_type"] == "precedes"

    def test_export_json_without_relationships(self, sample_event):
        """Test JSON export without relationships."""
        service = TimelineExportService()
        result = service.export_json(
            events=[sample_event],
            include_relationships=False,
        )

        assert "relationships" not in result

    def test_export_json_event_serialization(self, sample_event):
        """Test event serialization for JSON export."""
        service = TimelineExportService()
        data = service._serialize_event_json(sample_event)

        assert data["id"] == str(sample_event.id)
        assert data["name"] == sample_event.name
        assert data["start_date"] == sample_event.start_date.isoformat()
        assert data["precision"] == "day"
        assert data["uncertainty"] == "exact"

    def test_export_json_event_serialization_no_dates(
        self,
        sample_event_sequence_only,
    ):
        """Test event serialization when no dates."""
        service = TimelineExportService()
        data = service._serialize_event_json(sample_event_sequence_only)

        assert data["start_date"] is None
        assert data["end_date"] is None
        assert data["precision"] is None
        assert data["sequence_position"] == 5


class TestTimelineExportServiceExportCsv:
    """Tests for CSV export."""

    def test_export_csv_basic(self, sample_event, sample_job_id):
        """Test basic CSV export."""
        service = TimelineExportService()
        result = service.export_csv(
            events=[sample_event],
            job_id=sample_job_id,
            user_id="test-user",
        )

        # Check header comments
        assert result.startswith("# Timeline Export")
        assert f"# Job ID: {sample_job_id}" in result

        # Parse CSV and check data
        lines = result.split("\n")
        data_lines = [line for line in lines if not line.startswith("#") and line.strip()]
        reader = csv.reader(io.StringIO("\n".join(data_lines)))
        rows = list(reader)

        # Header row + data row
        assert len(rows) == 2
        assert rows[0][0] == "ID"
        assert rows[1][1] == sample_event.name

    def test_export_csv_contains_warning(self, sample_event):
        """Test CSV export contains security warning."""
        service = TimelineExportService()
        result = service.export_csv(events=[sample_event])

        assert "WARNING" in result
        assert "sanitized" in result

    def test_export_csv_iso_date_format(self, sample_event):
        """Test CSV export with ISO date format."""
        service = TimelineExportService()
        result = service.export_csv(
            events=[sample_event],
            date_format="iso",
        )

        assert sample_event.start_date.isoformat() in result

    def test_export_csv_human_date_format(self, sample_event):
        """Test CSV export with human date format."""
        service = TimelineExportService()
        result = service.export_csv(
            events=[sample_event],
            date_format="human",
        )

        expected = sample_event.start_date.strftime("%Y-%m-%d %H:%M:%S UTC")
        assert expected in result

    def test_export_csv_formula_injection_prevention(self, sample_page_id):
        """Test CSV export prevents formula injection."""
        service = TimelineExportService()

        # Create event with dangerous characters
        dangerous_event = TimelineEvent(
            id=uuid4(),
            name="=SUM(A1:A10)",
            description="+1234567890",
            entity_type="-malicious",
            start_date=datetime.now(UTC),
            end_date=None,
            precision=DatePrecision.DAY,
            uncertainty=None,
            original_text="@test",
            sequence_position=None,
            source_page_id=sample_page_id,
            source_url="https://example.com",
            involved_entities=[],
        )

        result = service.export_csv(events=[dangerous_event])

        # Parse CSV and check sanitization
        lines = result.split("\n")
        data_lines = [line for line in lines if not line.startswith("#") and line.strip()]
        reader = csv.reader(io.StringIO("\n".join(data_lines)))
        rows = list(reader)

        # Data row values should be prefixed
        data_row = rows[1]
        assert data_row[1] == "'=SUM(A1:A10)"  # Name
        assert data_row[2] == "'+1234567890"  # Description
        assert data_row[3] == "'-malicious"  # Entity type

    def test_export_csv_all_formula_chars_sanitized(self):
        """Test all formula injection characters are sanitized."""
        service = TimelineExportService()

        for char in FORMULA_INJECTION_CHARS:
            test_value = f"{char}test"
            sanitized = service._sanitize_csv_cell(test_value)
            assert sanitized.startswith("'"), f"Character {repr(char)} not sanitized"

    def test_export_csv_empty_value_not_sanitized(self):
        """Test empty values are not modified."""
        service = TimelineExportService()
        result = service._sanitize_csv_cell("")
        assert result == ""

    def test_export_csv_normal_value_not_sanitized(self):
        """Test normal values are not modified."""
        service = TimelineExportService()
        result = service._sanitize_csv_cell("Normal text")
        assert result == "Normal text"


class TestTimelineExportServiceExportIcs:
    """Tests for ICS (iCalendar) export."""

    def test_export_ics_basic(self, sample_event, sample_job_id):
        """Test basic ICS export."""
        service = TimelineExportService()
        result = service.export_ics(
            events=[sample_event],
            calendar_name="Test Calendar",
            job_id=sample_job_id,
        )

        assert "BEGIN:VCALENDAR" in result
        assert "END:VCALENDAR" in result
        assert "VERSION:2.0" in result
        assert "PRODID:-//Knowledge Mapper//Timeline Export//EN" in result

    def test_export_ics_custom_calendar_name(self, sample_event):
        """Test ICS export with custom calendar name."""
        service = TimelineExportService()
        result = service.export_ics(
            events=[sample_event],
            calendar_name="My Custom Calendar",
        )

        assert "X-WR-CALNAME:My Custom Calendar" in result

    def test_export_ics_contains_vevent(self, sample_event):
        """Test ICS export contains VEVENT."""
        service = TimelineExportService()
        result = service.export_ics(events=[sample_event])

        assert "BEGIN:VEVENT" in result
        assert "END:VEVENT" in result

    def test_export_ics_event_uid(self, sample_event):
        """Test ICS export event has UID."""
        service = TimelineExportService()
        result = service.export_ics(events=[sample_event])

        assert f"UID:{sample_event.id}@knowledge-mapper" in result

    def test_export_ics_event_summary(self, sample_event):
        """Test ICS export event has summary."""
        service = TimelineExportService()
        result = service.export_ics(events=[sample_event])

        assert f"SUMMARY:{sample_event.name}" in result

    def test_export_ics_event_categories(self, sample_event):
        """Test ICS export event has categories."""
        service = TimelineExportService()
        result = service.export_ics(events=[sample_event])

        assert "CATEGORIES:EVENT" in result

    def test_export_ics_day_precision_uses_date(self, sample_event):
        """Test ICS uses DATE format for day precision."""
        service = TimelineExportService()
        result = service.export_ics(events=[sample_event])

        assert "DTSTART;VALUE=DATE:" in result

    def test_export_ics_hour_precision_uses_datetime(self, sample_page_id):
        """Test ICS uses DATE-TIME format for hour precision."""
        service = TimelineExportService()
        event = TimelineEvent(
            id=uuid4(),
            name="Precise Event",
            description="Event with hour precision",
            entity_type="event",
            start_date=datetime(2023, 6, 15, 14, 30, 0, tzinfo=UTC),
            end_date=None,
            precision=DatePrecision.HOUR,
            uncertainty=None,
            original_text="2:30 PM",
            sequence_position=None,
            source_page_id=sample_page_id,
            source_url="https://example.com",
            involved_entities=[],
        )

        result = service.export_ics(events=[event])

        # Should have DATE-TIME format (no VALUE=DATE parameter)
        assert "DTSTART:20230615T143000Z" in result

    def test_export_ics_includes_uncertainty(self, sample_event):
        """Test ICS export includes uncertainty in description."""
        service = TimelineExportService()
        result = service.export_ics(events=[sample_event])

        assert "Date uncertainty: Exact date" in result or "X-UNCERTAINTY:exact" in result

    def test_export_ics_custom_properties(self, sample_event):
        """Test ICS export includes custom X- properties."""
        service = TimelineExportService()
        result = service.export_ics(events=[sample_event])

        assert "X-DATE-PRECISION:day" in result
        assert "X-UNCERTAINTY:exact" in result

    def test_export_ics_export_metadata(self, sample_event, sample_job_id):
        """Test ICS export includes export metadata."""
        service = TimelineExportService()
        result = service.export_ics(
            events=[sample_event],
            job_id=sample_job_id,
            user_id="test-user",
        )

        assert "X-EXPORTED-AT:" in result
        assert f"X-EXPORTED-JOB-ID:{sample_job_id}" in result
        assert "X-EXPORTED-USER:test-user" in result

    def test_export_ics_escapes_special_chars(self, sample_page_id):
        """Test ICS export properly escapes special characters."""
        service = TimelineExportService()
        event = TimelineEvent(
            id=uuid4(),
            name="Event with; special, chars\\n",
            description="Description\nwith newlines",
            entity_type="event",
            start_date=datetime(2023, 6, 15, tzinfo=UTC),
            end_date=None,
            precision=DatePrecision.DAY,
            uncertainty=None,
            original_text=None,
            sequence_position=None,
            source_page_id=sample_page_id,
            source_url="https://example.com",
            involved_entities=[],
        )

        result = service.export_ics(events=[event])

        # Check escaping
        assert "\\;" in result
        assert "\\," in result
        assert "\\n" in result

    def test_export_ics_sequence_only_event(self, sample_event_sequence_only):
        """Test ICS export handles sequence-only events."""
        service = TimelineExportService()
        result = service.export_ics(events=[sample_event_sequence_only])

        # Should still create VEVENT with a placeholder date
        assert "BEGIN:VEVENT" in result
        assert "Sequence position: 5" in result


class TestTimelineExportServiceHelpers:
    """Tests for helper methods."""

    def test_escape_ics_text(self):
        """Test ICS text escaping."""
        service = TimelineExportService()

        assert service._escape_ics_text("normal text") == "normal text"
        assert service._escape_ics_text("semi;colon") == "semi\\;colon"
        assert service._escape_ics_text("com,ma") == "com\\,ma"
        assert service._escape_ics_text("back\\slash") == "back\\\\slash"
        assert service._escape_ics_text("new\nline") == "new\\nline"

    def test_format_date_iso(self, sample_event):
        """Test date formatting with ISO format."""
        service = TimelineExportService()
        result = service._format_date(sample_event.start_date, "iso")

        assert result == sample_event.start_date.isoformat()

    def test_format_date_human(self, sample_event):
        """Test date formatting with human format."""
        service = TimelineExportService()
        result = service._format_date(sample_event.start_date, "human")

        expected = sample_event.start_date.strftime("%Y-%m-%d %H:%M:%S UTC")
        assert result == expected

    def test_format_date_none(self):
        """Test date formatting with None."""
        service = TimelineExportService()
        result = service._format_date(None, "iso")

        assert result == ""

    def test_get_uncertainty_text(self):
        """Test uncertainty text generation."""
        service = TimelineExportService()

        assert service._get_uncertainty_text(UncertaintyMarker.EXACT) == "Exact date"
        assert service._get_uncertainty_text(UncertaintyMarker.APPROXIMATE) == "Approximate date"
        assert service._get_uncertainty_text(UncertaintyMarker.CIRCA) == "Circa (around this date)"

    def test_generate_export_metadata(self, sample_job_id):
        """Test export metadata generation."""
        service = TimelineExportService()
        metadata = service._generate_export_metadata(
            format_type="json",
            event_count=10,
            relationship_count=5,
            job_id=sample_job_id,
            user_id="test-user",
            filters_applied={"start_date": datetime(2023, 1, 1, tzinfo=UTC)},
        )

        assert metadata["format"] == "json"
        assert metadata["event_count"] == 10
        assert metadata["relationship_count"] == 5
        assert metadata["job_id"] == str(sample_job_id)
        assert metadata["exported_by"] == "test-user"
        assert "exported_at" in metadata
        assert metadata["filters_applied"]["start_date"].endswith("+00:00")


class TestGetTimelineExportService:
    """Tests for the factory function."""

    def test_get_timeline_export_service_returns_singleton(self):
        """Test that factory returns singleton instance."""
        # Reset singleton
        import kg_builder.services.timeline_export as export_module

        export_module._export_service = None

        service1 = get_timeline_export_service()
        service2 = get_timeline_export_service()

        assert service1 is service2

        # Cleanup
        export_module._export_service = None

    def test_get_timeline_export_service_returns_instance(self):
        """Test that factory returns TimelineExportService instance."""
        # Reset singleton
        import kg_builder.services.timeline_export as export_module

        export_module._export_service = None

        service = get_timeline_export_service()
        assert isinstance(service, TimelineExportService)

        # Cleanup
        export_module._export_service = None
