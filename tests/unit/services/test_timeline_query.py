"""Unit tests for TimelineQueryService."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from kg_builder.schemas.timeline import (
    DatePrecision,
    TimelineFilters,
    UncertaintyMarker,
)


@pytest.fixture
def mock_db_session():
    """Create a mock database session."""
    session = AsyncMock()
    return session


@pytest.fixture
def sample_tenant_id():
    """Generate a sample tenant ID."""
    return uuid4()


@pytest.fixture
def sample_job_id():
    """Generate a sample job ID."""
    return uuid4()


@pytest.fixture
def mock_entity():
    """Create a mock ExtractedEntity with temporal data."""
    entity = MagicMock()
    entity.id = uuid4()
    entity.name = "Test Event"
    entity.description = "A test event"
    entity.entity_type = "event"
    entity.start_date = datetime(2023, 6, 15, 12, 0, 0, tzinfo=UTC)
    entity.end_date = datetime(2023, 6, 16, 12, 0, 0, tzinfo=UTC)
    entity.date_precision = "day"
    entity.uncertainty_marker = "exact"
    entity.original_temporal_text = "June 15, 2023"
    entity.sequence_position = 1
    entity.source_page_id = uuid4()
    entity.source_page = MagicMock(url="https://example.com/page1")
    entity.is_canonical = True
    return entity


@pytest.fixture
def mock_entity_sequence_only():
    """Create a mock ExtractedEntity with only sequence position (no date)."""
    entity = MagicMock()
    entity.id = uuid4()
    entity.name = "Sequence Event"
    entity.description = "An event with only sequence"
    entity.entity_type = "event"
    entity.start_date = None
    entity.end_date = None
    entity.date_precision = None
    entity.uncertainty_marker = None
    entity.original_temporal_text = None
    entity.sequence_position = 5
    entity.source_page_id = uuid4()
    entity.source_page = MagicMock(url="https://example.com/page2")
    entity.is_canonical = True
    return entity


class TestTimelineFilters:
    """Unit tests for TimelineFilters schema."""

    def test_default_values(self):
        """Test default filter values."""
        filters = TimelineFilters()

        assert filters.start_date is None
        assert filters.end_date is None
        assert filters.entity_types is None
        assert filters.search is None
        assert filters.include_undated is True
        assert filters.sort_by == "date"

    def test_custom_values(self):
        """Test custom filter values."""
        start = datetime(2023, 1, 1, tzinfo=UTC)
        end = datetime(2023, 12, 31, tzinfo=UTC)

        filters = TimelineFilters(
            start_date=start,
            end_date=end,
            entity_types=["event", "person"],
            search="test",
            include_undated=False,
            sort_by="sequence",
        )

        assert filters.start_date == start
        assert filters.end_date == end
        assert filters.entity_types == ["event", "person"]
        assert filters.search == "test"
        assert filters.include_undated is False
        assert filters.sort_by == "sequence"

    def test_invalid_sort_by_raises_error(self):
        """Test that invalid sort_by value raises validation error."""
        with pytest.raises(ValueError, match="sort_by must be one of"):
            TimelineFilters(sort_by="invalid")

    def test_valid_sort_by_values(self):
        """Test valid sort_by values."""
        for sort_value in ["date", "sequence"]:
            filters = TimelineFilters(sort_by=sort_value)
            assert filters.sort_by == sort_value


class TestDatePrecisionEnum:
    """Unit tests for DatePrecision enum."""

    def test_all_values(self):
        """Test all DatePrecision values."""
        assert DatePrecision.YEAR.value == "year"
        assert DatePrecision.MONTH.value == "month"
        assert DatePrecision.DAY.value == "day"
        assert DatePrecision.HOUR.value == "hour"
        assert DatePrecision.MINUTE.value == "minute"

    def test_from_string(self):
        """Test creating DatePrecision from string."""
        assert DatePrecision("year") == DatePrecision.YEAR
        assert DatePrecision("day") == DatePrecision.DAY

    def test_invalid_value_raises_error(self):
        """Test that invalid value raises error."""
        with pytest.raises(ValueError):
            DatePrecision("invalid")


class TestUncertaintyMarkerEnum:
    """Unit tests for UncertaintyMarker enum."""

    def test_all_values(self):
        """Test all UncertaintyMarker values."""
        assert UncertaintyMarker.EXACT.value == "exact"
        assert UncertaintyMarker.APPROXIMATE.value == "approximate"
        assert UncertaintyMarker.CIRCA.value == "circa"
        assert UncertaintyMarker.BEFORE.value == "before"
        assert UncertaintyMarker.AFTER.value == "after"
        assert UncertaintyMarker.INFERRED.value == "inferred"

    def test_from_string(self):
        """Test creating UncertaintyMarker from string."""
        assert UncertaintyMarker("exact") == UncertaintyMarker.EXACT
        assert UncertaintyMarker("approximate") == UncertaintyMarker.APPROXIMATE

    def test_invalid_value_raises_error(self):
        """Test that invalid value raises error."""
        with pytest.raises(ValueError):
            UncertaintyMarker("invalid")


class TestTimelineQueryServiceEntityConversion:
    """Unit tests for entity to timeline event conversion."""

    def test_entity_to_timeline_event_full_data(self, mock_entity):
        """Test converting entity with full temporal data."""
        from kg_builder.services.timeline_query import TimelineQueryService

        service = TimelineQueryService(AsyncMock())
        event = service._entity_to_timeline_event(mock_entity)

        assert event.id == mock_entity.id
        assert event.name == mock_entity.name
        assert event.description == mock_entity.description
        assert event.entity_type == mock_entity.entity_type
        assert event.start_date == mock_entity.start_date
        assert event.end_date == mock_entity.end_date
        assert event.precision == DatePrecision.DAY
        assert event.uncertainty == UncertaintyMarker.EXACT
        assert event.original_text == mock_entity.original_temporal_text
        assert event.sequence_position == mock_entity.sequence_position
        assert event.source_page_id == mock_entity.source_page_id
        assert event.source_url == mock_entity.source_page.url

    def test_entity_to_timeline_event_sequence_only(self, mock_entity_sequence_only):
        """Test converting entity with only sequence position."""
        from kg_builder.services.timeline_query import TimelineQueryService

        service = TimelineQueryService(AsyncMock())
        event = service._entity_to_timeline_event(mock_entity_sequence_only)

        assert event.id == mock_entity_sequence_only.id
        assert event.name == mock_entity_sequence_only.name
        assert event.start_date is None
        assert event.end_date is None
        assert event.precision is None
        assert event.uncertainty is None
        assert event.sequence_position == mock_entity_sequence_only.sequence_position

    def test_entity_to_timeline_event_invalid_precision(self, mock_entity):
        """Test handling invalid precision value."""
        from kg_builder.services.timeline_query import TimelineQueryService

        mock_entity.date_precision = "invalid_precision"

        service = TimelineQueryService(AsyncMock())
        event = service._entity_to_timeline_event(mock_entity)

        # Should handle gracefully and return None
        assert event.precision is None

    def test_entity_to_timeline_event_invalid_uncertainty(self, mock_entity):
        """Test handling invalid uncertainty value."""
        from kg_builder.services.timeline_query import TimelineQueryService

        mock_entity.uncertainty_marker = "invalid_uncertainty"

        service = TimelineQueryService(AsyncMock())
        event = service._entity_to_timeline_event(mock_entity)

        # Should handle gracefully and return None
        assert event.uncertainty is None


class TestTimelineQueryServiceJobAccess:
    """Unit tests for job access verification."""

    @pytest.mark.asyncio
    async def test_verify_job_access_found(self, mock_db_session, sample_job_id, sample_tenant_id):
        """Test verifying job access when job exists."""
        from kg_builder.services.timeline_query import TimelineQueryService

        mock_job = MagicMock()
        mock_job.id = sample_job_id
        mock_job.tenant_id = sample_tenant_id

        # `Result.scalar_one_or_none()` is synchronous on the awaited result,
        # so the result object must be a MagicMock, not an AsyncMock.
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_job
        mock_db_session.execute.return_value = mock_result

        service = TimelineQueryService(mock_db_session)
        job = await service.verify_job_access(sample_job_id, sample_tenant_id)

        assert job is not None
        assert job.id == sample_job_id
        mock_db_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_verify_job_access_not_found(
        self, mock_db_session, sample_job_id, sample_tenant_id
    ):
        """Test verifying job access when job doesn't exist."""
        from kg_builder.services.timeline_query import TimelineQueryService

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db_session.execute.return_value = mock_result

        service = TimelineQueryService(mock_db_session)
        job = await service.verify_job_access(sample_job_id, sample_tenant_id)

        assert job is None


class TestGetTimelineQueryService:
    """Unit tests for the factory function."""

    def test_returns_service_instance(self, mock_db_session):
        """Test that factory function returns a TimelineQueryService instance."""
        from kg_builder.services.timeline_query import (
            TimelineQueryService,
            get_timeline_query_service,
        )

        service = get_timeline_query_service(mock_db_session)

        assert isinstance(service, TimelineQueryService)
        assert service.db == mock_db_session
