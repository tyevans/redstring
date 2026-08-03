"""
Unit tests for consolidation API schemas.

Tests the Pydantic models used in the consolidation API endpoints.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from kg_builder.schemas.consolidation import (
    # Batch Operations
    BatchConsolidationRequest,
    ComputeCandidatesRequest,
    ConsolidationConfigRequest,
    EntitySummary,
    # Configuration
    MergeCandidateListResponse,
    MergeDecision,
    MergeEventType,
    # Merge History
    MergeHistoryItemResponse,
    MergeRequest,
    MergeResponse,
    ReviewDecision,
    ReviewDecisionRequest,
    ReviewQueueStatsResponse,
    ReviewStatus,
    # Merge Candidates
    SimilarityBreakdown,
    SplitEntityRequest,
    UndoMergeRequest,
)


class TestEnums:
    """Tests for enumeration types."""

    def test_merge_decision_values(self):
        """Test MergeDecision enum values."""
        assert MergeDecision.AUTO_MERGE == "auto_merge"
        assert MergeDecision.REVIEW == "review"
        assert MergeDecision.REJECT == "reject"

    def test_review_decision_values(self):
        """Test ReviewDecision enum values."""
        assert ReviewDecision.APPROVE == "approve"
        assert ReviewDecision.REJECT == "reject"
        assert ReviewDecision.DEFER == "defer"

    def test_review_status_values(self):
        """Test ReviewStatus enum values."""
        assert ReviewStatus.PENDING == "pending"
        assert ReviewStatus.APPROVED == "approved"
        assert ReviewStatus.REJECTED == "rejected"
        assert ReviewStatus.DEFERRED == "deferred"
        assert ReviewStatus.EXPIRED == "expired"

    def test_merge_event_type_values(self):
        """Test MergeEventType enum values."""
        assert MergeEventType.ENTITIES_MERGED == "entities_merged"
        assert MergeEventType.MERGE_UNDONE == "merge_undone"
        assert MergeEventType.ENTITY_SPLIT == "entity_split"


class TestEntitySummary:
    """Tests for EntitySummary schema."""

    def test_entity_summary_creation(self):
        """Test basic entity summary creation."""
        entity_id = uuid4()
        summary = EntitySummary(
            id=entity_id,
            name="Test Entity",
            entity_type="person",
        )
        assert summary.id == entity_id
        assert summary.name == "Test Entity"
        assert summary.entity_type == "person"
        assert summary.is_canonical is True

    def test_entity_summary_with_all_fields(self):
        """Test entity summary with all optional fields."""
        entity_id = uuid4()
        summary = EntitySummary(
            id=entity_id,
            name="Test Entity",
            normalized_name="test_entity",
            entity_type="organization",
            description="A test organization",
            is_canonical=False,
        )
        assert summary.normalized_name == "test_entity"
        assert summary.description == "A test organization"
        assert summary.is_canonical is False


class TestSimilarityBreakdown:
    """Tests for SimilarityBreakdown schema."""

    def test_empty_breakdown(self):
        """Test creating empty similarity breakdown."""
        breakdown = SimilarityBreakdown()
        assert breakdown.jaro_winkler is None
        assert breakdown.embedding_cosine is None

    def test_breakdown_with_scores(self):
        """Test breakdown with various scores."""
        breakdown = SimilarityBreakdown(
            jaro_winkler=0.85,
            levenshtein=0.75,
            soundex_match=True,
            embedding_cosine=0.92,
        )
        assert breakdown.jaro_winkler == 0.85
        assert breakdown.soundex_match is True
        assert breakdown.embedding_cosine == 0.92

    def test_breakdown_score_validation(self):
        """Test that scores must be in valid range."""
        with pytest.raises(ValidationError):
            SimilarityBreakdown(jaro_winkler=1.5)

        with pytest.raises(ValidationError):
            SimilarityBreakdown(embedding_cosine=-0.1)


class TestMergeRequest:
    """Tests for MergeRequest schema."""

    def test_merge_request_creation(self):
        """Test basic merge request creation."""
        canonical_id = uuid4()
        merged_id = uuid4()
        request = MergeRequest(
            canonical_entity_id=canonical_id,
            merged_entity_ids=[merged_id],
        )
        assert request.canonical_entity_id == canonical_id
        assert request.merged_entity_ids == [merged_id]
        assert request.merge_reason == "manual"

    def test_merge_request_with_multiple_entities(self):
        """Test merge request with multiple entities."""
        canonical_id = uuid4()
        merged_ids = [uuid4(), uuid4(), uuid4()]
        request = MergeRequest(
            canonical_entity_id=canonical_id,
            merged_entity_ids=merged_ids,
            merge_reason="batch",
        )
        assert len(request.merged_entity_ids) == 3
        assert request.merge_reason == "batch"

    def test_merge_request_requires_merged_entities(self):
        """Test that at least one merged entity is required."""
        with pytest.raises(ValidationError):
            MergeRequest(
                canonical_entity_id=uuid4(),
                merged_entity_ids=[],
            )


class TestUndoMergeRequest:
    """Tests for UndoMergeRequest schema."""

    def test_undo_request_creation(self):
        """Test basic undo request creation."""
        request = UndoMergeRequest(
            reason="Incorrect merge - entities are different concepts",
        )
        assert "Incorrect" in request.reason

    def test_undo_request_reason_min_length(self):
        """Test that reason must have minimum length."""
        with pytest.raises(ValidationError):
            UndoMergeRequest(reason="bad")

    def test_undo_request_with_restore_ids(self):
        """Test undo request with specific entity IDs."""
        entity_ids = [uuid4(), uuid4()]
        request = UndoMergeRequest(
            reason="Partial undo - only restore some entities",
            restore_entity_ids=entity_ids,
        )
        assert request.restore_entity_ids == entity_ids


class TestSplitEntityRequest:
    """Tests for SplitEntityRequest schema."""

    def test_split_request_creation(self):
        """Test basic split request creation."""
        request = SplitEntityRequest(
            split_definitions=[
                {"name": "Entity A", "entity_type": "person"},
                {"name": "Entity B", "entity_type": "person"},
            ],
            reason="Entity contains multiple distinct concepts",
        )
        assert len(request.split_definitions) == 2

    def test_split_request_requires_minimum_entities(self):
        """Test that at least 2 entities must be defined."""
        with pytest.raises(ValidationError):
            SplitEntityRequest(
                split_definitions=[{"name": "Only One"}],
                reason="This should fail",
            )

    def test_split_request_requires_name(self):
        """Test that each definition must have a name."""
        with pytest.raises(ValidationError):
            SplitEntityRequest(
                split_definitions=[
                    {"name": "Entity A"},
                    {"entity_type": "person"},  # Missing name
                ],
                reason="This should fail",
            )


class TestReviewDecisionRequest:
    """Tests for ReviewDecisionRequest schema."""

    def test_approve_decision(self):
        """Test approval decision."""
        request = ReviewDecisionRequest(decision=ReviewDecision.APPROVE)
        assert request.decision == ReviewDecision.APPROVE

    def test_reject_decision_with_notes(self):
        """Test rejection with notes."""
        request = ReviewDecisionRequest(
            decision=ReviewDecision.REJECT,
            notes="These entities are not duplicates - different people",
        )
        assert request.decision == ReviewDecision.REJECT
        assert request.notes is not None

    def test_approve_with_canonical_selection(self):
        """Test approval with canonical entity selection."""
        entity_id = uuid4()
        request = ReviewDecisionRequest(
            decision=ReviewDecision.APPROVE,
            select_canonical=entity_id,
        )
        assert request.select_canonical == entity_id


class TestConsolidationConfigRequest:
    """Tests for ConsolidationConfigRequest schema."""

    def test_partial_config_update(self):
        """Test partial configuration update."""
        request = ConsolidationConfigRequest(
            auto_merge_threshold=0.95,
        )
        assert request.auto_merge_threshold == 0.95
        assert request.review_threshold is None

    def test_feature_weights_validation(self):
        """Test that feature weights must be valid."""
        # Valid weights
        request = ConsolidationConfigRequest(
            feature_weights={"jaro_winkler": 0.5, "embedding_cosine": 0.8},
        )
        assert request.feature_weights["jaro_winkler"] == 0.5

        # Invalid weight (> 1.0)
        with pytest.raises(ValidationError):
            ConsolidationConfigRequest(
                feature_weights={"jaro_winkler": 1.5},
            )

        # Invalid weight (< 0.0)
        with pytest.raises(ValidationError):
            ConsolidationConfigRequest(
                feature_weights={"jaro_winkler": -0.1},
            )

    def test_threshold_validation(self):
        """Test threshold range validation."""
        with pytest.raises(ValidationError):
            ConsolidationConfigRequest(auto_merge_threshold=1.5)

        with pytest.raises(ValidationError):
            ConsolidationConfigRequest(review_threshold=-0.1)


class TestMergeResponse:
    """Tests for MergeResponse schema."""

    def test_merge_response_creation(self):
        """Test merge response creation."""
        canonical_id = uuid4()
        merged_ids = [uuid4(), uuid4()]
        history_id = uuid4()
        event_id = uuid4()

        response = MergeResponse(
            success=True,
            canonical_entity_id=canonical_id,
            merged_entity_ids=merged_ids,
            aliases_created=2,
            relationships_transferred=5,
            merge_history_id=history_id,
            event_id=event_id,
        )

        assert response.success is True
        assert response.canonical_entity_id == canonical_id
        assert len(response.merged_entity_ids) == 2
        assert response.aliases_created == 2
        assert response.relationships_transferred == 5


class TestReviewQueueStatsResponse:
    """Tests for ReviewQueueStatsResponse schema."""

    def test_stats_response_creation(self):
        """Test review queue stats response."""
        stats = ReviewQueueStatsResponse(
            total_pending=50,
            total_approved=100,
            total_rejected=25,
            total_deferred=10,
            total_expired=5,
            avg_confidence=0.72,
            oldest_pending_age_hours=48.5,
            by_entity_type={"person": 20, "organization": 30},
        )

        assert stats.total_pending == 50
        assert stats.avg_confidence == 0.72
        assert stats.by_entity_type["person"] == 20


class TestBatchConsolidationRequest:
    """Tests for BatchConsolidationRequest schema."""

    def test_batch_request_defaults(self):
        """Test batch request with defaults."""
        request = BatchConsolidationRequest()
        assert request.entity_type is None
        assert request.min_confidence == 0.9
        assert request.dry_run is False
        assert request.max_merges == 1000

    def test_batch_request_with_options(self):
        """Test batch request with custom options."""
        request = BatchConsolidationRequest(
            entity_type="person",
            min_confidence=0.95,
            dry_run=True,
            max_merges=500,
        )
        assert request.entity_type == "person"
        assert request.dry_run is True

    def test_batch_request_max_merges_validation(self):
        """Test max_merges range validation."""
        with pytest.raises(ValidationError):
            BatchConsolidationRequest(max_merges=0)

        with pytest.raises(ValidationError):
            BatchConsolidationRequest(max_merges=20000)


class TestMergeCandidateListResponse:
    """Tests for MergeCandidateListResponse schema."""

    def test_paginated_response_creation(self):
        """Test paginated response creation."""
        response = MergeCandidateListResponse(
            items=[],
            total=100,
            page=2,
            page_size=20,
            pages=5,
            has_next=True,
            has_prev=True,
        )

        assert response.total == 100
        assert response.page == 2
        assert response.pages == 5
        assert response.has_next is True
        assert response.has_prev is True


class TestComputeCandidatesRequest:
    """Tests for ComputeCandidatesRequest schema."""

    def test_compute_request_defaults(self):
        """Test compute request with defaults."""
        request = ComputeCandidatesRequest()
        assert request.entity_ids is None
        assert request.min_confidence == 0.5
        assert request.include_embedding is True
        assert request.include_graph is True
        assert request.max_candidates_per_entity == 10

    def test_compute_request_with_entity_ids(self):
        """Test compute request with specific entity IDs."""
        entity_ids = [uuid4(), uuid4()]
        request = ComputeCandidatesRequest(
            entity_ids=entity_ids,
            min_confidence=0.7,
        )
        assert request.entity_ids == entity_ids
        assert request.min_confidence == 0.7


class TestMergeHistoryItemResponse:
    """Tests for MergeHistoryItemResponse schema."""

    def test_history_item_creation(self):
        """Test merge history item response."""
        history_id = uuid4()
        event_id = uuid4()

        response = MergeHistoryItemResponse(
            id=history_id,
            event_id=event_id,
            event_type=MergeEventType.ENTITIES_MERGED,
            canonical_entity=None,
            affected_entity_ids=[uuid4(), uuid4()],
            merge_reason="auto_high_confidence",
            similarity_scores={"jaro_winkler": 0.95},
            performed_by_name="admin",
            performed_at=datetime.now(UTC),
            undone=False,
            undone_at=None,
            undone_by_name=None,
            undo_reason=None,
            can_undo=True,
        )

        assert response.event_type == MergeEventType.ENTITIES_MERGED
        assert response.can_undo is True
        assert len(response.affected_entity_ids) == 2
