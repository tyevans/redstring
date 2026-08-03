"""
Unit tests for merge undo functionality.

Tests the MergeService.undo_merge() method and related helper methods.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from kg_builder.models.entity_alias import EntityAlias
from kg_builder.models.extracted_entity import EntityType, ExtractedEntity
from kg_builder.models.merge_history import MergeEventType, MergeHistory
from kg_builder.services.consolidation.merge_service import (
    MergeService,
    MergeUndoError,
    UndoResult,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_session():
    """Create a mock async session."""
    session = AsyncMock()
    session.add = MagicMock()
    session.delete = AsyncMock()
    session.execute = AsyncMock()
    return session


@pytest.fixture
def mock_event_bus():
    """Create a mock event bus."""
    return AsyncMock()


@pytest.fixture
def tenant_id():
    """Create a test tenant ID."""
    return uuid.uuid4()


@pytest.fixture
def user_id():
    """Create a test user ID."""
    return uuid.uuid4()


@pytest.fixture
def canonical_entity(tenant_id):
    """Create a test canonical entity."""
    entity = MagicMock(spec=ExtractedEntity)
    entity.id = uuid.uuid4()
    entity.tenant_id = tenant_id
    entity.name = "Canonical Entity"
    entity.entity_type = EntityType.CONCEPT
    entity.is_canonical = True
    return entity


@pytest.fixture
def merged_entity(tenant_id):
    """Create a test merged entity."""
    entity = MagicMock(spec=ExtractedEntity)
    entity.id = uuid.uuid4()
    entity.tenant_id = tenant_id
    entity.name = "Merged Entity"
    entity.normalized_name = "merged entity"
    entity.entity_type = EntityType.CONCEPT
    entity.description = "Original description"
    entity.properties = {"key": "value"}
    entity.external_ids = {}
    entity.confidence_score = 0.95
    entity.source_text = "Source text"
    entity.source_page_id = uuid.uuid4()
    entity.is_canonical = False
    return entity


@pytest.fixture
def merge_event_id():
    """Create a test merge event ID."""
    return uuid.uuid4()


@pytest.fixture
def merge_history(tenant_id, canonical_entity, merged_entity, merge_event_id):
    """Create a test merge history record."""
    history = MagicMock(spec=MergeHistory)
    history.id = uuid.uuid4()
    history.tenant_id = tenant_id
    history.event_id = merge_event_id
    history.event_type = MergeEventType.ENTITIES_MERGED
    history.canonical_entity_id = canonical_entity.id
    history.affected_entity_ids = [canonical_entity.id, merged_entity.id]
    history.merge_reason = "auto_high_confidence"
    history.undone = False
    history.undone_at = None
    history.undone_by = None
    history.undo_reason = None
    history.details = {}
    return history


@pytest.fixture
def entity_alias(tenant_id, canonical_entity, merged_entity, merge_event_id):
    """Create a test entity alias."""
    alias = MagicMock(spec=EntityAlias)
    alias.id = uuid.uuid4()
    alias.tenant_id = tenant_id
    alias.canonical_entity_id = canonical_entity.id
    alias.alias_name = merged_entity.name
    alias.alias_normalized_name = merged_entity.normalized_name
    alias.original_entity_id = merged_entity.id
    alias.source_page_id = merged_entity.source_page_id
    alias.merge_event_id = merge_event_id
    alias.original_entity_type = merged_entity.entity_type.value
    alias.original_normalized_name = merged_entity.normalized_name
    alias.original_description = merged_entity.description
    alias.original_properties = merged_entity.properties
    alias.original_external_ids = merged_entity.external_ids
    alias.original_confidence_score = merged_entity.confidence_score
    alias.original_source_text = merged_entity.source_text
    return alias


# =============================================================================
# Test UndoResult
# =============================================================================


class TestUndoResult:
    """Tests for UndoResult dataclass."""

    def test_undo_result_creation(self):
        """Test UndoResult can be created with all fields."""
        original_merge_id = uuid.uuid4()
        canonical_id = uuid.uuid4()
        restored_ids = [uuid.uuid4(), uuid.uuid4()]
        undo_history_id = uuid.uuid4()
        event_id = uuid.uuid4()

        result = UndoResult(
            original_merge_event_id=original_merge_id,
            canonical_entity_id=canonical_id,
            restored_entity_ids=restored_ids,
            aliases_removed=2,
            relationships_restored=5,
            undo_history_id=undo_history_id,
            event_id=event_id,
        )

        assert result.original_merge_event_id == original_merge_id
        assert result.canonical_entity_id == canonical_id
        assert result.restored_entity_ids == restored_ids
        assert result.aliases_removed == 2
        assert result.relationships_restored == 5
        assert result.undo_history_id == undo_history_id
        assert result.event_id == event_id

    def test_undo_result_repr(self):
        """Test UndoResult string representation."""
        original_merge_id = uuid.uuid4()
        result = UndoResult(
            original_merge_event_id=original_merge_id,
            canonical_entity_id=uuid.uuid4(),
            restored_entity_ids=[uuid.uuid4(), uuid.uuid4()],
            aliases_removed=2,
            relationships_restored=3,
            undo_history_id=uuid.uuid4(),
            event_id=uuid.uuid4(),
        )

        repr_str = repr(result)
        assert "UndoResult" in repr_str
        assert "restored=2 entities" in repr_str
        assert "relationships=3" in repr_str


# =============================================================================
# Test MergeUndoError
# =============================================================================


class TestMergeUndoError:
    """Tests for MergeUndoError exception."""

    def test_merge_undo_error_creation(self):
        """Test MergeUndoError can be created with message."""
        error = MergeUndoError("Merge not found")
        assert str(error) == "Merge not found"

    def test_merge_undo_error_is_merge_error(self):
        """Test MergeUndoError is subclass of MergeError."""
        from kg_builder.services.consolidation.merge_service import MergeError
        error = MergeUndoError("Test error")
        assert isinstance(error, MergeError)


# =============================================================================
# Test Undo Validation
# =============================================================================


class TestUndoValidation:
    """Tests for undo validation logic."""

    @pytest.mark.asyncio
    async def test_undo_merge_not_found_raises_error(
        self, mock_session, tenant_id, user_id, merge_event_id
    ):
        """Test undo raises error when merge event not found."""
        # Setup: Return None for merge history query
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        service = MergeService(mock_session)

        with pytest.raises(MergeUndoError, match="not found"):
            await service.undo_merge(
                merge_event_id=merge_event_id,
                user_id=user_id,
                reason="Testing undo",
            )

    @pytest.mark.asyncio
    async def test_undo_already_undone_raises_error(
        self, mock_session, tenant_id, user_id, merge_history, merge_event_id
    ):
        """Test undo raises error when merge already undone."""
        # Setup: Mark merge as already undone
        merge_history.undone = True
        merge_history.undone_at = datetime.now(UTC)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = merge_history
        mock_session.execute.return_value = mock_result

        service = MergeService(mock_session)

        with pytest.raises(MergeUndoError, match="already undone"):
            await service.undo_merge(
                merge_event_id=merge_event_id,
                user_id=user_id,
                reason="Testing undo",
            )

    @pytest.mark.asyncio
    async def test_undo_no_entities_to_restore_raises_error(
        self, mock_session, tenant_id, user_id, merge_history, canonical_entity, merge_event_id
    ):
        """Test undo raises error when no entities to restore (partial undo with wrong IDs)."""
        # Setup: affected_entity_ids only contains canonical
        merge_history.affected_entity_ids = [canonical_entity.id]

        # First call returns merge history
        mock_result1 = MagicMock()
        mock_result1.scalar_one_or_none.return_value = merge_history

        mock_session.execute.return_value = mock_result1

        service = MergeService(mock_session)

        with pytest.raises(MergeUndoError, match="No entities to restore"):
            await service.undo_merge(
                merge_event_id=merge_event_id,
                user_id=user_id,
                reason="Testing undo",
            )


# =============================================================================
# Test Entity Restoration
# =============================================================================


class TestEntityRestoration:
    """Tests for entity restoration from aliases."""

    @pytest.mark.asyncio
    async def test_restore_entity_from_alias_creates_new_entity(
        self, mock_session, tenant_id, entity_alias
    ):
        """Test that _restore_entity_from_alias creates entity with correct properties."""
        service = MergeService(mock_session)

        # Call the method
        restored = await service._restore_entity_from_alias(entity_alias, tenant_id)

        # Verify entity was added to session
        mock_session.add.assert_called_once()

        # Verify entity properties
        assert restored.tenant_id == tenant_id
        assert restored.name == entity_alias.alias_name
        assert restored.normalized_name == entity_alias.original_normalized_name
        assert restored.description == entity_alias.original_description
        assert restored.properties == entity_alias.original_properties
        assert restored.confidence_score == entity_alias.original_confidence_score
        assert restored.is_canonical is True
        assert restored.is_alias_of is None
        assert restored.synced_to_neo4j is False

    @pytest.mark.asyncio
    async def test_restore_entity_uses_correct_entity_type(
        self, mock_session, tenant_id, entity_alias
    ):
        """Test that entity type is correctly restored from alias."""
        service = MergeService(mock_session)

        # Test with PERSON type
        entity_alias.original_entity_type = "person"
        restored = await service._restore_entity_from_alias(entity_alias, tenant_id)
        assert restored.entity_type == EntityType.PERSON

    @pytest.mark.asyncio
    async def test_restore_entity_handles_unknown_type(
        self, mock_session, tenant_id, entity_alias
    ):
        """Test that unknown entity type defaults to CONCEPT."""
        service = MergeService(mock_session)

        entity_alias.original_entity_type = "unknown_type"
        restored = await service._restore_entity_from_alias(entity_alias, tenant_id)
        assert restored.entity_type == EntityType.CONCEPT

    @pytest.mark.asyncio
    async def test_restore_entity_handles_missing_properties(
        self, mock_session, tenant_id, entity_alias
    ):
        """Test restoration handles missing optional properties gracefully."""
        service = MergeService(mock_session)

        # Set optional properties to None
        entity_alias.original_description = None
        entity_alias.original_properties = None
        entity_alias.original_external_ids = None
        entity_alias.original_source_text = None

        restored = await service._restore_entity_from_alias(entity_alias, tenant_id)

        assert restored.description is None
        assert restored.properties == {}
        assert restored.external_ids == {}
        assert restored.source_text is None


# =============================================================================
# Test Relationship Restoration
# =============================================================================


class TestRelationshipRestoration:
    """Tests for relationship restoration from snapshot."""

    @pytest.mark.asyncio
    async def test_restore_relationships_empty_snapshot(
        self, mock_session, tenant_id, entity_alias, canonical_entity
    ):
        """Test that empty snapshot returns 0 relationships."""
        service = MergeService(mock_session)

        aliases = [entity_alias]
        restored_entity = MagicMock(spec=ExtractedEntity)
        restored_entity.id = uuid.uuid4()
        restored_entities = [restored_entity]

        count = await service._restore_relationships_from_snapshot(
            relationship_snapshot={},
            aliases=aliases,
            restored_entities=restored_entities,
            canonical_id=canonical_entity.id,
            tenant_id=tenant_id,
        )

        assert count == 0

    @pytest.mark.asyncio
    async def test_restore_relationships_from_valid_snapshot(
        self, mock_session, tenant_id, entity_alias, canonical_entity
    ):
        """Test that relationships are restored from valid snapshot."""
        service = MergeService(mock_session)

        # Create restored entity
        restored_entity = MagicMock(spec=ExtractedEntity)
        restored_entity.id = uuid.uuid4()

        # Create snapshot with relationship data
        other_entity_id = uuid.uuid4()
        original_entity_id_str = str(entity_alias.original_entity_id)

        relationship_snapshot = {
            original_entity_id_str: [
                {
                    "source_entity_id": original_entity_id_str,
                    "target_entity_id": str(other_entity_id),
                    "relationship_type": "RELATED_TO",
                    "properties": {},
                    "confidence_score": 0.95,
                }
            ]
        }

        count = await service._restore_relationships_from_snapshot(
            relationship_snapshot=relationship_snapshot,
            aliases=[entity_alias],
            restored_entities=[restored_entity],
            canonical_id=canonical_entity.id,
            tenant_id=tenant_id,
        )

        assert count == 1
        # Verify relationship was added to session
        mock_session.add.assert_called()

    @pytest.mark.asyncio
    async def test_restore_relationships_skips_self_referential(
        self, mock_session, tenant_id, entity_alias, canonical_entity
    ):
        """Test that self-referential relationships are skipped."""
        service = MergeService(mock_session)

        restored_entity = MagicMock(spec=ExtractedEntity)
        restored_entity.id = uuid.uuid4()

        original_entity_id_str = str(entity_alias.original_entity_id)

        # Snapshot with self-referential relationship
        relationship_snapshot = {
            original_entity_id_str: [
                {
                    "source_entity_id": original_entity_id_str,
                    "target_entity_id": original_entity_id_str,  # Self-reference
                    "relationship_type": "RELATED_TO",
                    "properties": {},
                    "confidence_score": 0.95,
                }
            ]
        }

        count = await service._restore_relationships_from_snapshot(
            relationship_snapshot=relationship_snapshot,
            aliases=[entity_alias],
            restored_entities=[restored_entity],
            canonical_id=canonical_entity.id,
            tenant_id=tenant_id,
        )

        assert count == 0  # Self-referential should be skipped


# =============================================================================
# Test Full Undo Flow
# =============================================================================


class TestFullUndoFlow:
    """Tests for the complete undo operation flow."""

    @pytest.mark.asyncio
    async def test_undo_merge_success(
        self,
        mock_session,
        mock_event_bus,
        tenant_id,
        user_id,
        merge_history,
        entity_alias,
        canonical_entity,
        merged_entity,
        merge_event_id,
    ):
        """Test successful undo of a merge."""
        # Setup mock responses
        def mock_execute_side_effect(*args, **kwargs):
            result = MagicMock()
            # Check what kind of query is being executed
            query_str = str(args[0]) if args else ""

            if "merge_history" in query_str.lower() or merge_history.event_id:
                result.scalar_one_or_none.return_value = merge_history
            elif "entity_aliases" in query_str.lower():
                result.scalars.return_value.all.return_value = [entity_alias]
            else:
                result.scalar_one_or_none.return_value = merge_history
                result.scalars.return_value.all.return_value = [entity_alias]

            return result

        # First call for merge history, second for aliases
        call_count = [0]

        async def execute_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.scalar_one_or_none.return_value = merge_history
            else:
                result.scalars.return_value.all.return_value = [entity_alias]
            return result

        mock_session.execute = execute_effect

        service = MergeService(mock_session, event_bus=mock_event_bus)

        result = await service.undo_merge(
            merge_event_id=merge_event_id,
            user_id=user_id,
            reason="Testing undo",
        )

        # Verify result
        assert isinstance(result, UndoResult)
        assert result.original_merge_event_id == merge_event_id
        assert result.canonical_entity_id == canonical_entity.id
        assert result.aliases_removed == 1
        assert len(result.restored_entity_ids) == 1

        # Verify merge history was updated
        assert merge_history.undone is True
        assert merge_history.undone_by == user_id
        assert merge_history.undo_reason == "Testing undo"

        # Verify event was published
        mock_event_bus.publish.assert_called()

    @pytest.mark.asyncio
    async def test_undo_merge_partial_restore(
        self,
        mock_session,
        tenant_id,
        user_id,
        merge_history,
        entity_alias,
        canonical_entity,
        merge_event_id,
    ):
        """Test partial undo restoring only specified entities."""
        # Add second merged entity
        merged_entity_2_id = uuid.uuid4()
        merge_history.affected_entity_ids = [
            canonical_entity.id,
            entity_alias.original_entity_id,
            merged_entity_2_id,
        ]

        # Only restore the second merged entity (which has no alias)
        call_count = [0]

        async def execute_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.scalar_one_or_none.return_value = merge_history
            else:
                # Return empty aliases for the second entity
                result.scalars.return_value.all.return_value = []
            return result

        mock_session.execute = execute_effect

        service = MergeService(mock_session)

        # Request to restore only the entity without alias
        with pytest.raises(MergeUndoError, match="No alias records found"):
            await service.undo_merge(
                merge_event_id=merge_event_id,
                user_id=user_id,
                reason="Partial undo",
                restore_entity_ids=[merged_entity_2_id],
            )


# =============================================================================
# Test Helper Methods
# =============================================================================


class TestHelperMethods:
    """Tests for helper methods."""

    @pytest.mark.asyncio
    async def test_load_merge_history(self, mock_session, merge_history, merge_event_id):
        """Test _load_merge_history loads record by event ID."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = merge_history
        mock_session.execute.return_value = mock_result

        service = MergeService(mock_session)
        result = await service._load_merge_history(merge_event_id)

        assert result == merge_history

    @pytest.mark.asyncio
    async def test_load_aliases_for_entities(
        self, mock_session, tenant_id, entity_alias, canonical_entity, merged_entity
    ):
        """Test _load_aliases_for_entities loads correct aliases."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [entity_alias]
        mock_session.execute.return_value = mock_result

        service = MergeService(mock_session)
        result = await service._load_aliases_for_entities(
            entity_ids=[merged_entity.id],
            canonical_id=canonical_entity.id,
            tenant_id=tenant_id,
        )

        assert result == [entity_alias]
