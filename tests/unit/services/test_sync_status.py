"""
Unit tests for Neo4j sync status tracking service.

Tests the SyncStatusService's public interface by mocking the session.execute method.
These tests verify the service's logic for processing database results and
calculating statistics.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def tenant_id():
    """Generate a test tenant ID."""
    return uuid4()


@pytest.fixture
def other_tenant_id():
    """Generate a different tenant ID for cross-tenant tests."""
    return uuid4()


@pytest.fixture
def entity_id():
    """Generate a test entity ID."""
    return uuid4()


@pytest.fixture
def relationship_id():
    """Generate a test relationship ID."""
    return uuid4()


@pytest.fixture
def mock_session():
    """Create a mock AsyncSession."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    return session


@pytest.fixture
def mock_entity(tenant_id, entity_id):
    """Create a mock unsynced entity."""
    entity = MagicMock()
    entity.id = entity_id
    entity.tenant_id = tenant_id
    entity.name = "Test Entity"
    entity.entity_type = "person"
    entity.synced_to_neo4j = False
    entity.neo4j_node_id = None
    entity.synced_at = None
    return entity


@pytest.fixture
def mock_synced_entity(tenant_id):
    """Create a mock synced entity."""
    entity = MagicMock()
    entity.id = uuid4()
    entity.tenant_id = tenant_id
    entity.name = "Synced Entity"
    entity.entity_type = "organization"
    entity.synced_to_neo4j = True
    entity.neo4j_node_id = "4:abc:123"
    entity.synced_at = datetime.now(UTC)
    return entity


@pytest.fixture
def mock_relationship(tenant_id, relationship_id):
    """Create a mock unsynced relationship."""
    rel = MagicMock()
    rel.id = relationship_id
    rel.tenant_id = tenant_id
    rel.relationship_type = "WORKS_FOR"
    rel.synced_to_neo4j = False
    rel.neo4j_relationship_id = None
    return rel


# =============================================================================
# Test Helpers
# =============================================================================


def create_mock_scalars_result(items):
    """Create a mock result that behaves like SQLAlchemy scalars result."""
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = items
    mock_result.scalars.return_value = mock_scalars
    return mock_result


def create_mock_row_result(total, synced):
    """Create a mock result that behaves like SQLAlchemy row result."""
    mock_result = MagicMock()
    mock_row = MagicMock()
    mock_row.total = total
    mock_row.synced = synced
    mock_result.one.return_value = mock_row
    return mock_result


def create_mock_update_result(rowcount):
    """Create a mock result for update operations."""
    mock_result = MagicMock()
    mock_result.rowcount = rowcount
    return mock_result


# =============================================================================
# Service Method Patching Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_unsynced_entities_returns_results(mock_session, mock_entity, tenant_id):
    """Test get_unsynced_entities returns entities from execute result."""
    from kg_builder.services.sync_status import SyncStatusService

    mock_session.execute.return_value = create_mock_scalars_result([mock_entity])

    with patch("kg_builder.services.sync_status.select") as mock_select:
        mock_select.return_value.where.return_value.where.return_value.order_by.return_value.limit.return_value = MagicMock()

        service = SyncStatusService(mock_session)
        entities = await service.get_unsynced_entities(tenant_id=tenant_id)

        assert len(entities) == 1
        assert entities[0] is mock_entity
        mock_session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_unsynced_entities_empty_result(mock_session, tenant_id):
    """Test get_unsynced_entities handles empty results."""
    from kg_builder.services.sync_status import SyncStatusService

    mock_session.execute.return_value = create_mock_scalars_result([])

    with patch("kg_builder.services.sync_status.select") as mock_select:
        mock_select.return_value.where.return_value.where.return_value.order_by.return_value.limit.return_value = MagicMock()

        service = SyncStatusService(mock_session)
        entities = await service.get_unsynced_entities(tenant_id=tenant_id)

        assert entities == []


@pytest.mark.asyncio
async def test_get_unsynced_relationships_returns_results(
    mock_session, mock_relationship, tenant_id
):
    """Test get_unsynced_relationships returns relationships from execute result."""
    from kg_builder.services.sync_status import SyncStatusService

    mock_session.execute.return_value = create_mock_scalars_result([mock_relationship])

    with patch("kg_builder.services.sync_status.select") as mock_select:
        mock_select.return_value.where.return_value.where.return_value.order_by.return_value.limit.return_value = MagicMock()

        service = SyncStatusService(mock_session)
        relationships = await service.get_unsynced_relationships(tenant_id=tenant_id)

        assert len(relationships) == 1
        assert relationships[0] is mock_relationship


@pytest.mark.asyncio
async def test_get_sync_stats_calculates_correctly(mock_session, tenant_id):
    """Test get_sync_stats returns correct statistics from query results."""
    from kg_builder.services.sync_status import SyncStatusService

    # Setup entity stats mock (10 total, 7 synced)
    entity_result = create_mock_row_result(total=10, synced=7)

    # Setup relationship stats mock (5 total, 3 synced)
    rel_result = create_mock_row_result(total=5, synced=3)

    # Configure mock to return different results for each call
    mock_session.execute.side_effect = [entity_result, rel_result]

    with (
        patch("kg_builder.services.sync_status.select") as mock_select,
        patch("kg_builder.services.sync_status.func"),
        patch("kg_builder.services.sync_status.case"),
    ):
        mock_select.return_value.select_from.return_value = MagicMock()
        mock_select.return_value.select_from.return_value.where.return_value = MagicMock()

        service = SyncStatusService(mock_session)
        stats = await service.get_sync_stats(tenant_id=tenant_id)

        assert stats["total_entities"] == 10
        assert stats["synced_entities"] == 7
        assert stats["pending_entities"] == 3
        assert stats["sync_percentage_entities"] == 70.0

        assert stats["total_relationships"] == 5
        assert stats["synced_relationships"] == 3
        assert stats["pending_relationships"] == 2
        assert stats["sync_percentage_relationships"] == 60.0


@pytest.mark.asyncio
async def test_get_sync_stats_handles_zero_entities(mock_session, tenant_id):
    """Test get_sync_stats handles zero entities gracefully."""
    from kg_builder.services.sync_status import SyncStatusService

    entity_result = create_mock_row_result(total=0, synced=None)
    rel_result = create_mock_row_result(total=0, synced=None)

    mock_session.execute.side_effect = [entity_result, rel_result]

    with (
        patch("kg_builder.services.sync_status.select") as mock_select,
        patch("kg_builder.services.sync_status.func"),
        patch("kg_builder.services.sync_status.case"),
    ):
        mock_select.return_value.select_from.return_value = MagicMock()
        mock_select.return_value.select_from.return_value.where.return_value = MagicMock()

        service = SyncStatusService(mock_session)
        stats = await service.get_sync_stats(tenant_id=tenant_id)

        assert stats["total_entities"] == 0
        assert stats["synced_entities"] == 0
        assert stats["pending_entities"] == 0
        assert stats["sync_percentage_entities"] == 100.0

        assert stats["total_relationships"] == 0
        assert stats["synced_relationships"] == 0
        assert stats["pending_relationships"] == 0
        assert stats["sync_percentage_relationships"] == 100.0


@pytest.mark.asyncio
async def test_get_sync_stats_handles_none_values(mock_session, tenant_id):
    """Test get_sync_stats handles None values from database."""
    from kg_builder.services.sync_status import SyncStatusService

    entity_result = create_mock_row_result(total=None, synced=None)
    rel_result = create_mock_row_result(total=None, synced=None)

    mock_session.execute.side_effect = [entity_result, rel_result]

    with (
        patch("kg_builder.services.sync_status.select") as mock_select,
        patch("kg_builder.services.sync_status.func"),
        patch("kg_builder.services.sync_status.case"),
    ):
        mock_select.return_value.select_from.return_value = MagicMock()
        mock_select.return_value.select_from.return_value.where.return_value = MagicMock()

        service = SyncStatusService(mock_session)
        stats = await service.get_sync_stats(tenant_id=tenant_id)

        assert stats["total_entities"] == 0
        assert stats["synced_entities"] == 0
        assert stats["pending_entities"] == 0


@pytest.mark.asyncio
async def test_get_sync_stats_percentage_rounding(mock_session, tenant_id):
    """Test get_sync_stats rounds percentages to 2 decimal places."""
    from kg_builder.services.sync_status import SyncStatusService

    # 1/3 synced = 33.33...%
    entity_result = create_mock_row_result(total=3, synced=1)
    rel_result = create_mock_row_result(total=7, synced=2)  # 2/7 = 28.57...%

    mock_session.execute.side_effect = [entity_result, rel_result]

    with (
        patch("kg_builder.services.sync_status.select") as mock_select,
        patch("kg_builder.services.sync_status.func"),
        patch("kg_builder.services.sync_status.case"),
    ):
        mock_select.return_value.select_from.return_value = MagicMock()
        mock_select.return_value.select_from.return_value.where.return_value = MagicMock()

        service = SyncStatusService(mock_session)
        stats = await service.get_sync_stats(tenant_id=tenant_id)

        assert stats["sync_percentage_entities"] == 33.33
        assert stats["sync_percentage_relationships"] == 28.57


@pytest.mark.asyncio
async def test_mark_entity_synced_success(mock_session, entity_id):
    """Test mark_entity_synced returns True when entity updated."""
    from kg_builder.services.sync_status import SyncStatusService

    mock_session.execute.return_value = create_mock_update_result(rowcount=1)

    with patch("kg_builder.services.sync_status.update") as mock_update:
        mock_update.return_value.where.return_value.values.return_value = MagicMock()

        service = SyncStatusService(mock_session)
        result = await service.mark_entity_synced(entity_id, "4:abc:123")

        assert result is True
        mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_mark_entity_synced_not_found(mock_session, entity_id):
    """Test mark_entity_synced returns False when entity not found."""
    from kg_builder.services.sync_status import SyncStatusService

    mock_session.execute.return_value = create_mock_update_result(rowcount=0)

    with patch("kg_builder.services.sync_status.update") as mock_update:
        mock_update.return_value.where.return_value.values.return_value = MagicMock()

        service = SyncStatusService(mock_session)
        result = await service.mark_entity_synced(entity_id, "4:abc:123")

        assert result is False


@pytest.mark.asyncio
async def test_mark_relationship_synced_success(mock_session, relationship_id):
    """Test mark_relationship_synced returns True when relationship updated."""
    from kg_builder.services.sync_status import SyncStatusService

    mock_session.execute.return_value = create_mock_update_result(rowcount=1)

    with patch("kg_builder.services.sync_status.update") as mock_update:
        mock_update.return_value.where.return_value.values.return_value = MagicMock()

        service = SyncStatusService(mock_session)
        result = await service.mark_relationship_synced(relationship_id, "5:abc:456")

        assert result is True
        mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_mark_relationship_synced_not_found(mock_session, relationship_id):
    """Test mark_relationship_synced returns False when relationship not found."""
    from kg_builder.services.sync_status import SyncStatusService

    mock_session.execute.return_value = create_mock_update_result(rowcount=0)

    with patch("kg_builder.services.sync_status.update") as mock_update:
        mock_update.return_value.where.return_value.values.return_value = MagicMock()

        service = SyncStatusService(mock_session)
        result = await service.mark_relationship_synced(relationship_id, "5:abc:456")

        assert result is False


@pytest.mark.asyncio
async def test_mark_sync_failed_logs_warning(mock_session, entity_id):
    """Test mark_sync_failed logs a warning message."""
    from kg_builder.services.sync_status import SyncStatusService

    with patch("kg_builder.services.sync_status.logging.getLogger") as mock_get_logger:
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        service = SyncStatusService(mock_session)
        await service.mark_sync_failed(entity_id, "Connection timeout")

        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        # Check that entity_id and error message are in the call
        assert "Sync failed for entity" in call_args[0][0]


@pytest.mark.asyncio
async def test_mark_sync_failed_includes_entity_id_and_error(mock_session, entity_id):
    """Test mark_sync_failed includes entity_id and error in log extra."""
    from kg_builder.services.sync_status import SyncStatusService

    with patch("kg_builder.services.sync_status.logging.getLogger") as mock_get_logger:
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        service = SyncStatusService(mock_session)
        error_msg = "Neo4j connection failed"
        await service.mark_sync_failed(entity_id, error_msg)

        call_kwargs = mock_logger.warning.call_args[1]
        assert "extra" in call_kwargs
        assert call_kwargs["extra"]["entity_id"] == str(entity_id)
        assert call_kwargs["extra"]["error"] == error_msg


@pytest.mark.asyncio
async def test_retry_failed_syncs_calls_get_unsynced(mock_session, mock_entity, tenant_id):
    """Test retry_failed_syncs delegates to get_unsynced_entities."""
    from kg_builder.services.sync_status import SyncStatusService

    mock_session.execute.return_value = create_mock_scalars_result([mock_entity])

    with patch("kg_builder.services.sync_status.select") as mock_select:
        mock_select.return_value.where.return_value.order_by.return_value.limit.return_value = (
            MagicMock()
        )
        mock_select.return_value.where.return_value.where.return_value.order_by.return_value.limit.return_value = MagicMock()

        service = SyncStatusService(mock_session)
        entities = await service.retry_failed_syncs(batch_size=10, tenant_id=tenant_id)

        assert len(entities) == 1
        assert entities[0] is mock_entity


@pytest.mark.asyncio
async def test_reset_sync_status_success(mock_session, entity_id):
    """Test reset_sync_status clears sync data."""
    from kg_builder.services.sync_status import SyncStatusService

    mock_session.execute.return_value = create_mock_update_result(rowcount=1)

    with patch("kg_builder.services.sync_status.update") as mock_update:
        mock_update.return_value.where.return_value.values.return_value = MagicMock()

        service = SyncStatusService(mock_session)
        result = await service.reset_sync_status(entity_id)

        assert result is True
        mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_reset_sync_status_not_found(mock_session, entity_id):
    """Test reset_sync_status returns False when entity not found."""
    from kg_builder.services.sync_status import SyncStatusService

    mock_session.execute.return_value = create_mock_update_result(rowcount=0)

    with patch("kg_builder.services.sync_status.update") as mock_update:
        mock_update.return_value.where.return_value.values.return_value = MagicMock()

        service = SyncStatusService(mock_session)
        result = await service.reset_sync_status(entity_id)

        assert result is False


# =============================================================================
# Service Initialization Tests
# =============================================================================


def test_sync_status_service_init(mock_session):
    """Test SyncStatusService initialization stores session."""
    from kg_builder.services.sync_status import SyncStatusService

    service = SyncStatusService(mock_session)

    assert service._session is mock_session


@pytest.mark.asyncio
async def test_get_sync_status_service_factory(mock_session):
    """Test factory function creates service correctly."""
    from kg_builder.services.sync_status import SyncStatusService, get_sync_status_service

    service = await get_sync_status_service(mock_session)

    assert isinstance(service, SyncStatusService)
    assert service._session is mock_session
