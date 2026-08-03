"""Unit tests for MergeReviewItem model."""

from datetime import UTC, datetime
from uuid import uuid4

from kg_builder.models.merge_review_queue import MergeReviewItem, MergeReviewStatus


class TestMergeReviewStatusEnum:
    """Tests for MergeReviewStatus enum."""

    def test_status_values(self):
        """Test all status enum values."""
        assert MergeReviewStatus.PENDING.value == "pending"
        assert MergeReviewStatus.APPROVED.value == "approved"
        assert MergeReviewStatus.REJECTED.value == "rejected"
        assert MergeReviewStatus.DEFERRED.value == "deferred"
        assert MergeReviewStatus.EXPIRED.value == "expired"

    def test_status_is_string_enum(self):
        """Test that status values are strings."""
        for status in MergeReviewStatus:
            assert isinstance(status.value, str)


class TestMergeReviewItemCreation:
    """Tests for MergeReviewItem model creation."""

    def test_merge_review_item_creation(self):
        """Test creating a review item with required fields."""
        tenant_id = uuid4()
        entity_a_id = uuid4()
        entity_b_id = uuid4()

        item = MergeReviewItem(
            tenant_id=tenant_id,
            entity_a_id=entity_a_id,
            entity_b_id=entity_b_id,
            confidence=0.75,
        )

        assert item.tenant_id == tenant_id
        assert item.entity_a_id == entity_a_id
        assert item.entity_b_id == entity_b_id
        assert item.confidence == 0.75
        assert item.status == MergeReviewStatus.PENDING
        assert item.id is not None

    def test_merge_review_item_auto_generates_id(self):
        """Test that item ID is auto-generated."""
        item = MergeReviewItem(
            tenant_id=uuid4(),
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            confidence=0.75,
        )
        assert item.id is not None

    def test_merge_review_item_default_status(self):
        """Test default status is PENDING."""
        item = MergeReviewItem(
            tenant_id=uuid4(),
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            confidence=0.75,
        )
        assert item.status == MergeReviewStatus.PENDING

    def test_merge_review_item_default_similarity_scores(self):
        """Test default similarity_scores is empty dict."""
        item = MergeReviewItem(
            tenant_id=uuid4(),
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            confidence=0.75,
        )
        assert item.similarity_scores == {}


class TestReviewPriorityCalculation:
    """Tests for review priority calculation."""

    def test_review_priority_at_50_percent(self):
        """Test priority is highest (1.0) at 50% confidence."""
        item = MergeReviewItem(
            tenant_id=uuid4(),
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            confidence=0.50,
        )
        assert item.review_priority == 1.0

    def test_review_priority_at_100_percent(self):
        """Test priority is lowest (0.0) at 100% confidence."""
        item = MergeReviewItem(
            tenant_id=uuid4(),
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            confidence=1.0,
        )
        assert item.review_priority == 0.0

    def test_review_priority_at_0_percent(self):
        """Test priority is lowest (0.0) at 0% confidence."""
        item = MergeReviewItem(
            tenant_id=uuid4(),
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            confidence=0.0,
        )
        assert item.review_priority == 0.0

    def test_review_priority_at_75_percent(self):
        """Test priority at 75% confidence."""
        item = MergeReviewItem(
            tenant_id=uuid4(),
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            confidence=0.75,
        )
        # |0.75 - 0.5| * 2 = 0.5, so priority = 1 - 0.5 = 0.5
        assert item.review_priority == 0.5

    def test_review_priority_at_60_percent(self):
        """Test priority at 60% confidence."""
        item = MergeReviewItem(
            tenant_id=uuid4(),
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            confidence=0.60,
        )
        # |0.60 - 0.5| * 2 = 0.2, so priority = 1 - 0.2 = 0.8
        assert abs(item.review_priority - 0.8) < 0.001

    def test_uncertain_higher_priority_than_certain(self):
        """Test that more uncertain items have higher priority."""
        uncertain = MergeReviewItem(
            tenant_id=uuid4(),
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            confidence=0.50,  # Most uncertain
        )
        certain = MergeReviewItem(
            tenant_id=uuid4(),
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            confidence=0.85,  # More certain
        )
        assert uncertain.review_priority > certain.review_priority

    def test_explicit_review_priority_not_overwritten(self):
        """Test that explicit priority is not overwritten."""
        item = MergeReviewItem(
            tenant_id=uuid4(),
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            confidence=0.50,
            review_priority=0.3,  # Explicit value
        )
        assert item.review_priority == 0.3


class TestMergeReviewItemProperties:
    """Tests for MergeReviewItem properties."""

    def test_is_pending_true(self):
        """Test is_pending returns True for PENDING status."""
        item = MergeReviewItem(
            tenant_id=uuid4(),
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            confidence=0.75,
        )
        assert item.is_pending is True

    def test_is_pending_false(self):
        """Test is_pending returns False for non-PENDING status."""
        item = MergeReviewItem(
            tenant_id=uuid4(),
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            confidence=0.75,
            status=MergeReviewStatus.APPROVED,
        )
        assert item.is_pending is False

    def test_is_resolved_approved(self):
        """Test is_resolved returns True for APPROVED status."""
        item = MergeReviewItem(
            tenant_id=uuid4(),
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            confidence=0.75,
            status=MergeReviewStatus.APPROVED,
        )
        assert item.is_resolved is True

    def test_is_resolved_rejected(self):
        """Test is_resolved returns True for REJECTED status."""
        item = MergeReviewItem(
            tenant_id=uuid4(),
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            confidence=0.75,
            status=MergeReviewStatus.REJECTED,
        )
        assert item.is_resolved is True

    def test_is_resolved_pending(self):
        """Test is_resolved returns False for PENDING status."""
        item = MergeReviewItem(
            tenant_id=uuid4(),
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            confidence=0.75,
        )
        assert item.is_resolved is False

    def test_is_resolved_deferred(self):
        """Test is_resolved returns False for DEFERRED status."""
        item = MergeReviewItem(
            tenant_id=uuid4(),
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            confidence=0.75,
            status=MergeReviewStatus.DEFERRED,
        )
        assert item.is_resolved is False


class TestMergeReviewItemOptionalFields:
    """Tests for MergeReviewItem optional fields."""

    def test_item_with_similarity_scores(self):
        """Test item with detailed similarity scores."""
        scores = {
            "jaro_winkler": 0.85,
            "normalized_exact": 0.0,
            "embedding_cosine": 0.78,
        }
        item = MergeReviewItem(
            tenant_id=uuid4(),
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            confidence=0.75,
            similarity_scores=scores,
        )
        assert item.similarity_scores == scores

    def test_item_with_reviewer_info(self):
        """Test item with reviewer information."""
        reviewer_id = uuid4()
        reviewed_at = datetime.now(UTC)
        notes = "These are clearly the same entity based on context."

        item = MergeReviewItem(
            tenant_id=uuid4(),
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            confidence=0.75,
            status=MergeReviewStatus.APPROVED,
            reviewed_by=reviewer_id,
            reviewed_at=reviewed_at,
            reviewer_notes=notes,
        )
        assert item.reviewed_by == reviewer_id
        assert item.reviewed_at == reviewed_at
        assert item.reviewer_notes == notes


class TestMergeReviewItemRepr:
    """Tests for MergeReviewItem string representation."""

    def test_repr_pending(self):
        """Test repr for pending item."""
        item = MergeReviewItem(
            tenant_id=uuid4(),
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            confidence=0.75,
        )
        repr_str = repr(item)
        assert "MergeReviewItem" in repr_str
        assert "pending" in repr_str
        assert "0.75" in repr_str

    def test_repr_approved(self):
        """Test repr for approved item."""
        item = MergeReviewItem(
            tenant_id=uuid4(),
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            confidence=0.85,
            status=MergeReviewStatus.APPROVED,
        )
        repr_str = repr(item)
        assert "approved" in repr_str
        assert "0.85" in repr_str
