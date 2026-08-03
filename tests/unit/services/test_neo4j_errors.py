"""
Unit tests for Neo4j error handling and recovery.

Tests the Neo4jErrorHandler class and custom exception classes for:
- Error classification (transient vs data errors)
- Retry eligibility determination
- Failure event creation
- Error wrapping with consistent handling
"""

import importlib.util
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

# =============================================================================
# Mock Neo4j Dependencies
# =============================================================================

# Create mock neo4j exceptions module before importing neo4j_errors
mock_neo4j_exceptions = MagicMock()


class MockServiceUnavailable(Exception):
    """Mock ServiceUnavailable exception."""

    pass


class MockSessionExpired(Exception):
    """Mock SessionExpired exception."""

    pass


class MockTransientError(Exception):
    """Mock TransientError exception."""

    pass


class MockDatabaseError(Exception):
    """Mock DatabaseError exception."""

    pass


# Assign mock exceptions
mock_neo4j_exceptions.ServiceUnavailable = MockServiceUnavailable
mock_neo4j_exceptions.SessionExpired = MockSessionExpired
mock_neo4j_exceptions.TransientError = MockTransientError
mock_neo4j_exceptions.DatabaseError = MockDatabaseError

# Mock the neo4j.exceptions module
sys.modules["neo4j"] = MagicMock()
sys.modules["neo4j.exceptions"] = mock_neo4j_exceptions

# Mock the event module to avoid deep imports
mock_scraping_events = MagicMock()


class MockNeo4jSyncFailed:
    """Mock Neo4jSyncFailed event."""

    event_type: str = "Neo4jSyncFailed"

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


mock_scraping_events.Neo4jSyncFailed = MockNeo4jSyncFailed
sys.modules["kg_builder.events.scraping"] = mock_scraping_events

# Import the module directly using importlib to avoid triggering __init__.py
spec = importlib.util.spec_from_file_location(
    "neo4j_errors",
    Path(__file__).parent.parent.parent.parent
    / "src"
    / "kg_builder"
    / "services"
    / "neo4j_errors.py",
)
neo4j_errors = importlib.util.module_from_spec(spec)
sys.modules["kg_builder.services.neo4j_errors"] = neo4j_errors
spec.loader.exec_module(neo4j_errors)

# Import symbols from the loaded module
Neo4jErrorHandler = neo4j_errors.Neo4jErrorHandler
Neo4jSyncError = neo4j_errors.Neo4jSyncError
Neo4jTransientError = neo4j_errors.Neo4jTransientError
Neo4jDataError = neo4j_errors.Neo4jDataError


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def entity_id():
    """Generate a test entity ID."""
    return uuid4()


@pytest.fixture
def relationship_id():
    """Generate a test relationship ID."""
    return uuid4()


@pytest.fixture
def tenant_id():
    """Generate a test tenant ID."""
    return uuid4()


@pytest.fixture
def service_unavailable_error():
    """Create a ServiceUnavailable error."""
    return MockServiceUnavailable("Connection refused")


@pytest.fixture
def session_expired_error():
    """Create a SessionExpired error."""
    return MockSessionExpired("Session timed out")


@pytest.fixture
def transient_error():
    """Create a generic TransientError."""
    return MockTransientError("Temporary failure")


@pytest.fixture
def database_error():
    """Create a DatabaseError."""
    return MockDatabaseError("Constraint violation: duplicate key")


@pytest.fixture
def generic_error():
    """Create a generic exception."""
    return Exception("Something unexpected happened")


# =============================================================================
# Custom Exception Tests
# =============================================================================


class TestNeo4jSyncError:
    """Tests for the base Neo4jSyncError exception."""

    def test_init_with_message_only(self):
        """Test creating error with just a message."""
        error = Neo4jSyncError("Sync failed")
        assert str(error) == "Sync failed"
        assert error.message == "Sync failed"
        assert error.original_error is None
        assert error.entity_id is None
        assert error.relationship_id is None

    def test_init_with_all_fields(self, entity_id):
        """Test creating error with all fields."""
        original = ValueError("Original error")
        error = Neo4jSyncError(
            message="Sync failed",
            original_error=original,
            entity_id=entity_id,
        )
        assert error.message == "Sync failed"
        assert error.original_error is original
        assert error.entity_id == entity_id

    def test_str_with_entity_id(self, entity_id):
        """Test string representation includes entity_id."""
        error = Neo4jSyncError("Sync failed", entity_id=entity_id)
        result = str(error)
        assert "Sync failed" in result
        assert str(entity_id) in result

    def test_str_with_relationship_id(self, relationship_id):
        """Test string representation includes relationship_id."""
        error = Neo4jSyncError("Sync failed", relationship_id=relationship_id)
        result = str(error)
        assert "Sync failed" in result
        assert str(relationship_id) in result

    def test_str_with_original_error(self):
        """Test string representation includes original error type."""
        original = ValueError("Original")
        error = Neo4jSyncError("Sync failed", original_error=original)
        result = str(error)
        assert "ValueError" in result

    def test_inheritance(self):
        """Test Neo4jSyncError is an Exception."""
        error = Neo4jSyncError("Test")
        assert isinstance(error, Exception)


class TestNeo4jTransientError:
    """Tests for the Neo4jTransientError exception."""

    def test_inheritance(self):
        """Test Neo4jTransientError inherits from Neo4jSyncError."""
        error = Neo4jTransientError("Transient failure")
        assert isinstance(error, Neo4jSyncError)
        assert isinstance(error, Exception)

    def test_all_fields(self, entity_id):
        """Test all fields are properly set."""
        original = MockServiceUnavailable("Down")
        error = Neo4jTransientError(
            message="Transient failure",
            original_error=original,
            entity_id=entity_id,
        )
        assert error.message == "Transient failure"
        assert error.original_error is original
        assert error.entity_id == entity_id


class TestNeo4jDataError:
    """Tests for the Neo4jDataError exception."""

    def test_inheritance(self):
        """Test Neo4jDataError inherits from Neo4jSyncError."""
        error = Neo4jDataError("Data error")
        assert isinstance(error, Neo4jSyncError)
        assert isinstance(error, Exception)

    def test_all_fields(self, relationship_id):
        """Test all fields are properly set."""
        original = MockDatabaseError("Constraint violation")
        error = Neo4jDataError(
            message="Data error",
            original_error=original,
            relationship_id=relationship_id,
        )
        assert error.message == "Data error"
        assert error.original_error is original
        assert error.relationship_id == relationship_id


# =============================================================================
# is_retryable Tests
# =============================================================================


class TestIsRetryable:
    """Tests for Neo4jErrorHandler.is_retryable()."""

    def test_service_unavailable_is_retryable(self, service_unavailable_error):
        """Test ServiceUnavailable is classified as retryable."""
        assert Neo4jErrorHandler.is_retryable(service_unavailable_error) is True

    def test_session_expired_is_retryable(self, session_expired_error):
        """Test SessionExpired is classified as retryable."""
        assert Neo4jErrorHandler.is_retryable(session_expired_error) is True

    def test_transient_error_is_retryable(self, transient_error):
        """Test TransientError is classified as retryable."""
        assert Neo4jErrorHandler.is_retryable(transient_error) is True

    def test_database_error_not_retryable(self, database_error):
        """Test DatabaseError is NOT classified as retryable."""
        assert Neo4jErrorHandler.is_retryable(database_error) is False

    def test_generic_error_not_retryable(self, generic_error):
        """Test generic Exception is NOT classified as retryable."""
        assert Neo4jErrorHandler.is_retryable(generic_error) is False

    def test_value_error_not_retryable(self):
        """Test ValueError is NOT classified as retryable."""
        error = ValueError("Invalid value")
        assert Neo4jErrorHandler.is_retryable(error) is False


# =============================================================================
# is_data_error Tests
# =============================================================================


class TestIsDataError:
    """Tests for Neo4jErrorHandler.is_data_error()."""

    def test_database_error_is_data_error(self, database_error):
        """Test DatabaseError is classified as data error."""
        assert Neo4jErrorHandler.is_data_error(database_error) is True

    def test_service_unavailable_not_data_error(self, service_unavailable_error):
        """Test ServiceUnavailable is NOT a data error."""
        assert Neo4jErrorHandler.is_data_error(service_unavailable_error) is False

    def test_session_expired_not_data_error(self, session_expired_error):
        """Test SessionExpired is NOT a data error."""
        assert Neo4jErrorHandler.is_data_error(session_expired_error) is False

    def test_generic_error_not_data_error(self, generic_error):
        """Test generic Exception is NOT a data error."""
        assert Neo4jErrorHandler.is_data_error(generic_error) is False


# =============================================================================
# classify_error Tests
# =============================================================================


class TestClassifyError:
    """Tests for Neo4jErrorHandler.classify_error()."""

    def test_classify_transient_errors(
        self, service_unavailable_error, session_expired_error, transient_error
    ):
        """Test transient errors are classified correctly."""
        assert Neo4jErrorHandler.classify_error(service_unavailable_error) == "transient"
        assert Neo4jErrorHandler.classify_error(session_expired_error) == "transient"
        assert Neo4jErrorHandler.classify_error(transient_error) == "transient"

    def test_classify_data_error(self, database_error):
        """Test data errors are classified correctly."""
        assert Neo4jErrorHandler.classify_error(database_error) == "data"

    def test_classify_unknown_error(self, generic_error):
        """Test unknown errors are classified correctly."""
        assert Neo4jErrorHandler.classify_error(generic_error) == "unknown"

    def test_classify_value_error(self):
        """Test ValueError is classified as unknown."""
        error = ValueError("Invalid")
        assert Neo4jErrorHandler.classify_error(error) == "unknown"


# =============================================================================
# handle_sync_error Tests
# =============================================================================


class TestHandleSyncError:
    """Tests for Neo4jErrorHandler.handle_sync_error()."""

    def test_creates_failure_event(self, database_error, entity_id, tenant_id):
        """Test handle_sync_error creates Neo4jSyncFailed event."""
        event = Neo4jErrorHandler.handle_sync_error(
            error=database_error,
            entity_id=entity_id,
            tenant_id=tenant_id,
        )

        assert event.entity_id == entity_id
        assert event.tenant_id == tenant_id
        assert event.relationship_id is None
        assert "Constraint violation" in event.error_message
        assert isinstance(event.failed_at, datetime)

    def test_uses_entity_id_as_aggregate_id(self, database_error, entity_id, tenant_id):
        """Test entity_id is used as aggregate_id when provided."""
        event = Neo4jErrorHandler.handle_sync_error(
            error=database_error,
            entity_id=entity_id,
            tenant_id=tenant_id,
        )

        assert event.aggregate_id == entity_id

    def test_uses_relationship_id_as_aggregate_id(self, database_error, relationship_id, tenant_id):
        """Test relationship_id is used as aggregate_id when entity_id is None."""
        event = Neo4jErrorHandler.handle_sync_error(
            error=database_error,
            relationship_id=relationship_id,
            tenant_id=tenant_id,
        )

        assert event.aggregate_id == relationship_id
        assert event.relationship_id == relationship_id
        assert event.entity_id is None

    def test_uses_zero_uuid_when_no_ids(self, database_error, tenant_id):
        """Test zero UUID is used when no entity or relationship ID provided."""
        event = Neo4jErrorHandler.handle_sync_error(
            error=database_error,
            tenant_id=tenant_id,
        )

        assert event.aggregate_id == UUID(int=0)

    def test_uses_zero_uuid_for_tenant_when_not_provided(self, database_error):
        """Test zero UUID is used for tenant_id when not provided."""
        event = Neo4jErrorHandler.handle_sync_error(error=database_error)

        assert event.tenant_id == UUID(int=0)

    def test_logs_warning_on_failure(self, database_error, entity_id, tenant_id, caplog):
        """Test handle_sync_error logs a warning."""
        with caplog.at_level(logging.WARNING):
            Neo4jErrorHandler.handle_sync_error(
                error=database_error,
                entity_id=entity_id,
                tenant_id=tenant_id,
            )

        assert "Neo4j sync failed" in caplog.text

    def test_event_has_correct_event_type(self, database_error, tenant_id):
        """Test the created event has correct event_type."""
        event = Neo4jErrorHandler.handle_sync_error(
            error=database_error,
            tenant_id=tenant_id,
        )

        assert event.event_type == "Neo4jSyncFailed"

    def test_failed_at_is_recent(self, database_error, tenant_id):
        """Test failed_at timestamp is recent (within last second)."""
        before = datetime.now(UTC)
        event = Neo4jErrorHandler.handle_sync_error(
            error=database_error,
            tenant_id=tenant_id,
        )
        after = datetime.now(UTC)

        assert before <= event.failed_at <= after


# =============================================================================
# with_error_handling Tests
# =============================================================================


class TestWithErrorHandling:
    """Tests for Neo4jErrorHandler.with_error_handling()."""

    @pytest.mark.asyncio
    async def test_returns_result_on_success(self):
        """Test successful operation returns result."""

        async def successful_operation():
            return {"status": "success", "node_id": "123"}

        result = await Neo4jErrorHandler.with_error_handling(successful_operation)

        assert result == {"status": "success", "node_id": "123"}

    @pytest.mark.asyncio
    async def test_raises_transient_error_for_service_unavailable(self):
        """Test ServiceUnavailable is wrapped as Neo4jTransientError."""

        async def failing_operation():
            raise MockServiceUnavailable("Connection refused")

        with pytest.raises(Neo4jTransientError) as exc_info:
            await Neo4jErrorHandler.with_error_handling(failing_operation)

        assert "Transient Neo4j error" in str(exc_info.value)
        assert isinstance(exc_info.value.original_error, MockServiceUnavailable)

    @pytest.mark.asyncio
    async def test_raises_transient_error_for_session_expired(self):
        """Test SessionExpired is wrapped as Neo4jTransientError."""

        async def failing_operation():
            raise MockSessionExpired("Session timed out")

        with pytest.raises(Neo4jTransientError) as exc_info:
            await Neo4jErrorHandler.with_error_handling(failing_operation)

        assert isinstance(exc_info.value.original_error, MockSessionExpired)

    @pytest.mark.asyncio
    async def test_raises_transient_error_for_transient_error(self):
        """Test TransientError is wrapped as Neo4jTransientError."""

        async def failing_operation():
            raise MockTransientError("Temporary failure")

        with pytest.raises(Neo4jTransientError) as exc_info:
            await Neo4jErrorHandler.with_error_handling(failing_operation)

        assert isinstance(exc_info.value.original_error, MockTransientError)

    @pytest.mark.asyncio
    async def test_raises_data_error_for_database_error(self):
        """Test DatabaseError is wrapped as Neo4jDataError."""

        async def failing_operation():
            raise MockDatabaseError("Constraint violation")

        with pytest.raises(Neo4jDataError) as exc_info:
            await Neo4jErrorHandler.with_error_handling(failing_operation)

        assert "Neo4j data error" in str(exc_info.value)
        assert isinstance(exc_info.value.original_error, MockDatabaseError)

    @pytest.mark.asyncio
    async def test_raises_sync_error_for_generic_error(self):
        """Test generic Exception is wrapped as Neo4jSyncError."""

        async def failing_operation():
            raise ValueError("Unexpected error")

        with pytest.raises(Neo4jSyncError) as exc_info:
            await Neo4jErrorHandler.with_error_handling(failing_operation)

        assert "Unexpected Neo4j error" in str(exc_info.value)
        assert isinstance(exc_info.value.original_error, ValueError)

    @pytest.mark.asyncio
    async def test_includes_entity_id_in_error(self, entity_id):
        """Test entity_id is included in raised error."""

        async def failing_operation():
            raise MockServiceUnavailable("Down")

        with pytest.raises(Neo4jTransientError) as exc_info:
            await Neo4jErrorHandler.with_error_handling(
                failing_operation,
                entity_id=entity_id,
            )

        assert exc_info.value.entity_id == entity_id

    @pytest.mark.asyncio
    async def test_includes_relationship_id_in_error(self, relationship_id):
        """Test relationship_id is included in raised error."""

        async def failing_operation():
            raise MockDatabaseError("Constraint violation")

        with pytest.raises(Neo4jDataError) as exc_info:
            await Neo4jErrorHandler.with_error_handling(
                failing_operation,
                relationship_id=relationship_id,
            )

        assert exc_info.value.relationship_id == relationship_id

    @pytest.mark.asyncio
    async def test_logs_warning_for_transient_errors(self, caplog):
        """Test transient errors are logged at WARNING level."""

        async def failing_operation():
            raise MockServiceUnavailable("Connection refused")

        with caplog.at_level(logging.WARNING), pytest.raises(Neo4jTransientError):
            await Neo4jErrorHandler.with_error_handling(failing_operation)

        assert "Transient Neo4j error" in caplog.text

    @pytest.mark.asyncio
    async def test_logs_error_for_data_errors(self, caplog):
        """Test data errors are logged at ERROR level."""

        async def failing_operation():
            raise MockDatabaseError("Constraint violation")

        with caplog.at_level(logging.ERROR), pytest.raises(Neo4jDataError):
            await Neo4jErrorHandler.with_error_handling(failing_operation)

        assert "Neo4j data error" in caplog.text

    @pytest.mark.asyncio
    async def test_logs_error_for_unknown_errors(self, caplog):
        """Test unknown errors are logged at ERROR level."""

        async def failing_operation():
            raise RuntimeError("Something went wrong")

        with caplog.at_level(logging.ERROR), pytest.raises(Neo4jSyncError):
            await Neo4jErrorHandler.with_error_handling(failing_operation)

        assert "Unexpected Neo4j error" in caplog.text

    @pytest.mark.asyncio
    async def test_preserves_exception_chain(self):
        """Test original exception is preserved in chain."""
        original = MockServiceUnavailable("Original error")

        async def failing_operation():
            raise original

        with pytest.raises(Neo4jTransientError) as exc_info:
            await Neo4jErrorHandler.with_error_handling(failing_operation)

        assert exc_info.value.__cause__ is original

    @pytest.mark.asyncio
    async def test_handles_async_operation(self):
        """Test handles async operations correctly."""
        call_count = 0

        async def async_operation():
            nonlocal call_count
            call_count += 1
            return "async result"

        result = await Neo4jErrorHandler.with_error_handling(async_operation)

        assert result == "async result"
        assert call_count == 1


# =============================================================================
# TRANSIENT_ERRORS and DATA_ERRORS Constants Tests
# =============================================================================


class TestErrorConstants:
    """Tests for the error classification constants."""

    def test_transient_errors_is_tuple(self):
        """Test TRANSIENT_ERRORS is a tuple."""
        assert isinstance(Neo4jErrorHandler.TRANSIENT_ERRORS, tuple)

    def test_transient_errors_contains_expected_types(self):
        """Test TRANSIENT_ERRORS contains expected exception types."""
        # Note: We're checking against mock types here
        assert MockServiceUnavailable in Neo4jErrorHandler.TRANSIENT_ERRORS
        assert MockSessionExpired in Neo4jErrorHandler.TRANSIENT_ERRORS
        assert MockTransientError in Neo4jErrorHandler.TRANSIENT_ERRORS

    def test_data_errors_is_tuple(self):
        """Test DATA_ERRORS is a tuple."""
        assert isinstance(Neo4jErrorHandler.DATA_ERRORS, tuple)

    def test_data_errors_contains_expected_types(self):
        """Test DATA_ERRORS contains expected exception types."""
        assert MockDatabaseError in Neo4jErrorHandler.DATA_ERRORS

    def test_transient_and_data_errors_are_disjoint(self):
        """Test TRANSIENT_ERRORS and DATA_ERRORS have no overlap."""
        transient_set = set(Neo4jErrorHandler.TRANSIENT_ERRORS)
        data_set = set(Neo4jErrorHandler.DATA_ERRORS)
        assert transient_set.isdisjoint(data_set)


# =============================================================================
# Integration-like Tests (Testing Error Flow)
# =============================================================================


class TestErrorFlow:
    """Tests for the complete error handling flow."""

    @pytest.mark.asyncio
    async def test_transient_error_can_be_caught_and_retried(self):
        """Test transient errors can be caught for retry logic."""
        attempt_count = 0
        max_attempts = 3

        async def operation_that_eventually_succeeds():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < max_attempts:
                raise MockServiceUnavailable("Try again")
            return "success"

        # Simulate retry logic
        result = None
        for _ in range(max_attempts):
            try:
                result = await Neo4jErrorHandler.with_error_handling(
                    operation_that_eventually_succeeds
                )
                break
            except Neo4jTransientError:
                continue

        assert result == "success"
        assert attempt_count == max_attempts

    @pytest.mark.asyncio
    async def test_data_error_stops_retry(self):
        """Test data errors stop retry attempts."""
        attempt_count = 0

        async def operation_with_data_error():
            nonlocal attempt_count
            attempt_count += 1
            raise MockDatabaseError("Invalid data")

        # Simulate retry logic that respects error type
        should_retry = True
        while should_retry and attempt_count < 5:
            try:
                await Neo4jErrorHandler.with_error_handling(operation_with_data_error)
                break
            except Neo4jTransientError:
                # Would retry
                continue
            except Neo4jDataError:
                # Don't retry data errors
                should_retry = False

        # Should have stopped after first attempt (data error)
        assert attempt_count == 1

    def test_error_classification_is_consistent(self):
        """Test error classification is consistent across methods."""
        errors_and_expected = [
            (MockServiceUnavailable("test"), True, False, "transient"),
            (MockSessionExpired("test"), True, False, "transient"),
            (MockTransientError("test"), True, False, "transient"),
            (MockDatabaseError("test"), False, True, "data"),
            (ValueError("test"), False, False, "unknown"),
        ]

        for error, should_retry, is_data, classification in errors_and_expected:
            assert Neo4jErrorHandler.is_retryable(error) == should_retry
            assert Neo4jErrorHandler.is_data_error(error) == is_data
            assert Neo4jErrorHandler.classify_error(error) == classification
