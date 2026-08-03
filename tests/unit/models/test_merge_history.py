"""Unit tests for MergeHistory model."""

from datetime import UTC, datetime
from uuid import uuid4

from kg_builder.models.merge_history import MergeEventType, MergeHistory


class TestMergeEventTypeEnum:
    """Tests for MergeEventType enum."""

    def test_event_type_values(self):
        """Test all event type enum values."""
        assert MergeEventType.ENTITIES_MERGED.value == "entities_merged"
        assert MergeEventType.MERGE_UNDONE.value == "merge_undone"
        assert MergeEventType.ENTITY_SPLIT.value == "entity_split"

    def test_event_type_is_string_enum(self):
        """Test that event type values are strings."""
        for event_type in MergeEventType:
            assert isinstance(event_type.value, str)


class TestMergeHistoryCreation:
    """Tests for MergeHistory model creation."""

    def test_merge_history_creation(self):
        """Test creating a merge history record with required fields."""
        tenant_id = uuid4()
        event_id = uuid4()
        canonical_id = uuid4()
        merged_id = uuid4()
        performed_at = datetime.now(UTC)

        history = MergeHistory(
            tenant_id=tenant_id,
            event_id=event_id,
            event_type=MergeEventType.ENTITIES_MERGED,
            canonical_entity_id=canonical_id,
            affected_entity_ids=[canonical_id, merged_id],
            performed_at=performed_at,
        )

        assert history.tenant_id == tenant_id
        assert history.event_id == event_id
        assert history.event_type == MergeEventType.ENTITIES_MERGED
        assert history.canonical_entity_id == canonical_id
        assert len(history.affected_entity_ids) == 2
        assert history.performed_at == performed_at
        assert history.id is not None

    def test_merge_history_auto_generates_id(self):
        """Test that history ID is auto-generated."""
        history = MergeHistory(
            tenant_id=uuid4(),
            event_id=uuid4(),
            event_type=MergeEventType.ENTITIES_MERGED,
            affected_entity_ids=[uuid4()],
            performed_at=datetime.now(UTC),
        )
        assert history.id is not None

    def test_merge_history_default_undone(self):
        """Test default undone value is False."""
        history = MergeHistory(
            tenant_id=uuid4(),
            event_id=uuid4(),
            event_type=MergeEventType.ENTITIES_MERGED,
            affected_entity_ids=[uuid4()],
            performed_at=datetime.now(UTC),
        )
        assert history.undone is False


class TestMergeHistoryCanUndo:
    """Tests for can_undo property."""

    def test_can_undo_fresh_merge(self):
        """Test can_undo returns True for fresh ENTITIES_MERGED."""
        history = MergeHistory(
            tenant_id=uuid4(),
            event_id=uuid4(),
            event_type=MergeEventType.ENTITIES_MERGED,
            affected_entity_ids=[uuid4(), uuid4()],
            performed_at=datetime.now(UTC),
        )
        assert history.can_undo is True

    def test_can_undo_already_undone(self):
        """Test can_undo returns False for already undone merge."""
        history = MergeHistory(
            tenant_id=uuid4(),
            event_id=uuid4(),
            event_type=MergeEventType.ENTITIES_MERGED,
            affected_entity_ids=[uuid4()],
            performed_at=datetime.now(UTC),
            undone=True,
        )
        assert history.can_undo is False

    def test_can_undo_merge_undone_event(self):
        """Test can_undo returns False for MERGE_UNDONE event type."""
        history = MergeHistory(
            tenant_id=uuid4(),
            event_id=uuid4(),
            event_type=MergeEventType.MERGE_UNDONE,
            affected_entity_ids=[uuid4()],
            performed_at=datetime.now(UTC),
        )
        assert history.can_undo is False

    def test_can_undo_entity_split_event(self):
        """Test can_undo returns False for ENTITY_SPLIT event type."""
        history = MergeHistory(
            tenant_id=uuid4(),
            event_id=uuid4(),
            event_type=MergeEventType.ENTITY_SPLIT,
            affected_entity_ids=[uuid4(), uuid4()],
            performed_at=datetime.now(UTC),
        )
        assert history.can_undo is False


class TestMergeHistoryAffectedEntityCount:
    """Tests for affected_entity_count property."""

    def test_affected_entity_count_single(self):
        """Test count with single affected entity."""
        history = MergeHistory(
            tenant_id=uuid4(),
            event_id=uuid4(),
            event_type=MergeEventType.ENTITIES_MERGED,
            affected_entity_ids=[uuid4()],
            performed_at=datetime.now(UTC),
        )
        assert history.affected_entity_count == 1

    def test_affected_entity_count_multiple(self):
        """Test count with multiple affected entities."""
        history = MergeHistory(
            tenant_id=uuid4(),
            event_id=uuid4(),
            event_type=MergeEventType.ENTITIES_MERGED,
            affected_entity_ids=[uuid4(), uuid4(), uuid4()],
            performed_at=datetime.now(UTC),
        )
        assert history.affected_entity_count == 3


class TestMergeHistoryOptionalFields:
    """Tests for MergeHistory optional fields."""

    def test_history_with_merge_reason(self):
        """Test history with merge_reason."""
        history = MergeHistory(
            tenant_id=uuid4(),
            event_id=uuid4(),
            event_type=MergeEventType.ENTITIES_MERGED,
            affected_entity_ids=[uuid4()],
            merge_reason="auto_high_confidence",
            performed_at=datetime.now(UTC),
        )
        assert history.merge_reason == "auto_high_confidence"

    def test_history_with_similarity_scores(self):
        """Test history with similarity_scores."""
        scores = {
            "jaro_winkler": 0.92,
            "embedding_cosine": 0.88,
            "combined": 0.91,
        }
        history = MergeHistory(
            tenant_id=uuid4(),
            event_id=uuid4(),
            event_type=MergeEventType.ENTITIES_MERGED,
            affected_entity_ids=[uuid4()],
            similarity_scores=scores,
            performed_at=datetime.now(UTC),
        )
        assert history.similarity_scores == scores

    def test_history_with_details(self):
        """Test history with additional details."""
        details = {
            "property_assignments": {"name": "entity_a", "description": "entity_b"},
        }
        history = MergeHistory(
            tenant_id=uuid4(),
            event_id=uuid4(),
            event_type=MergeEventType.ENTITY_SPLIT,
            affected_entity_ids=[uuid4(), uuid4()],
            details=details,
            performed_at=datetime.now(UTC),
        )
        assert history.details == details

    def test_history_with_performer(self):
        """Test history with performed_by user."""
        user_id = uuid4()
        history = MergeHistory(
            tenant_id=uuid4(),
            event_id=uuid4(),
            event_type=MergeEventType.ENTITIES_MERGED,
            affected_entity_ids=[uuid4()],
            performed_by=user_id,
            performed_at=datetime.now(UTC),
        )
        assert history.performed_by == user_id

    def test_history_with_undo_info(self):
        """Test history with undo information."""
        undoer_id = uuid4()
        undone_at = datetime.now(UTC)

        history = MergeHistory(
            tenant_id=uuid4(),
            event_id=uuid4(),
            event_type=MergeEventType.ENTITIES_MERGED,
            affected_entity_ids=[uuid4()],
            performed_at=datetime.now(UTC),
            undone=True,
            undone_at=undone_at,
            undone_by=undoer_id,
            undo_reason="Entities are actually different concepts",
        )
        assert history.undone is True
        assert history.undone_at == undone_at
        assert history.undone_by == undoer_id
        assert history.undo_reason == "Entities are actually different concepts"


class TestMergeHistoryRepr:
    """Tests for MergeHistory string representation."""

    def test_repr_entities_merged(self):
        """Test repr for ENTITIES_MERGED event."""
        history = MergeHistory(
            tenant_id=uuid4(),
            event_id=uuid4(),
            event_type=MergeEventType.ENTITIES_MERGED,
            affected_entity_ids=[uuid4()],
            performed_at=datetime.now(UTC),
        )
        repr_str = repr(history)
        assert "MergeHistory" in repr_str
        assert "entities_merged" in repr_str

    def test_repr_entity_split(self):
        """Test repr for ENTITY_SPLIT event."""
        history = MergeHistory(
            tenant_id=uuid4(),
            event_id=uuid4(),
            event_type=MergeEventType.ENTITY_SPLIT,
            affected_entity_ids=[uuid4(), uuid4()],
            performed_at=datetime.now(UTC),
        )
        repr_str = repr(history)
        assert "entity_split" in repr_str
