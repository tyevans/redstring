"""
Merge review queue model for human-in-the-loop entity consolidation.

This module defines the queue for merge candidates that require human review,
typically those with medium confidence scores (50-89%).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from kg_builder.db import Base

if TYPE_CHECKING:
    from kg_builder.models.extracted_entity import ExtractedEntity
    from kg_builder.models.tenant import Tenant
    from kg_builder.models.user import User


class MergeReviewStatus(str, enum.Enum):
    """Status values for merge review items."""

    PENDING = "pending"  # Awaiting review
    APPROVED = "approved"  # Merge approved by reviewer
    REJECTED = "rejected"  # Merge rejected (not duplicates)
    DEFERRED = "deferred"  # Review deferred for later
    EXPIRED = "expired"  # One or both entities no longer exist


class MergeReviewItem(Base):
    """
    Queue item for human review of merge candidates.

    When the consolidation system identifies a potential duplicate with
    medium confidence (50-89%), it creates a review item for human decision.

    Attributes:
        id: UUID primary key
        tenant_id: Foreign key to tenant (RLS enforced)
        entity_a_id: First entity in the candidate pair
        entity_b_id: Second entity in the candidate pair
        confidence: Combined similarity score (0.0-1.0)
        review_priority: Priority for review queue ordering (higher = more urgent)
        similarity_scores: Detailed breakdown of similarity scores
        status: Current review status
        reviewed_by: User who reviewed (if reviewed)
        reviewed_at: When review occurred
        reviewer_notes: Optional notes from reviewer
        created_at: When candidate was queued
    """

    __tablename__ = "merge_review_queue"

    # Exclude inherited columns - this model defines its own id and created_at
    updated_at = None  # Table doesn't have updated_at

    # Primary key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        insert_default=uuid.uuid4,
        comment="UUID primary key",
    )

    # Tenant isolation (RLS enforced)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Tenant this review item belongs to (RLS enforced)",
    )

    # Entity pair being considered for merge
    entity_a_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extracted_entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="First entity in the candidate pair",
    )

    entity_b_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extracted_entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Second entity in the candidate pair",
    )

    # Similarity scoring
    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Combined similarity confidence score (0.0-1.0)",
    )

    review_priority: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Priority for queue ordering (1.0 = highest uncertainty)",
    )

    similarity_scores: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        insert_default=dict,
        server_default="{}",
        comment="Detailed similarity score breakdown by method",
    )

    # Review status
    status: Mapped[MergeReviewStatus] = mapped_column(
        SQLEnum(
            MergeReviewStatus,
            name="merge_review_status",
            values_callable=lambda obj: [e.value for e in obj],
            create_type=False,  # Created by migration
        ),
        nullable=False,
        default=MergeReviewStatus.PENDING,
        index=True,
        comment="Current review status",
    )

    # Reviewer information
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="User who reviewed this item",
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the review occurred",
    )

    reviewer_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Optional notes from the reviewer",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default="now()",
        comment="When the item was queued",
    )

    # Table constraints
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "entity_a_id",
            "entity_b_id",
            name="uq_merge_review_pair",
        ),
    )

    # Relationships
    tenant: Mapped[Tenant] = relationship(
        "Tenant",
        doc="Tenant this review item belongs to",
    )

    entity_a: Mapped[ExtractedEntity] = relationship(
        "ExtractedEntity",
        foreign_keys=[entity_a_id],
        doc="First entity in the candidate pair",
    )

    entity_b: Mapped[ExtractedEntity] = relationship(
        "ExtractedEntity",
        foreign_keys=[entity_b_id],
        doc="Second entity in the candidate pair",
    )

    reviewer: Mapped[User | None] = relationship(
        "User",
        doc="User who reviewed this item",
    )

    def __init__(self, **kwargs):
        """Initialize review item with defaults."""
        if "id" not in kwargs:
            kwargs["id"] = uuid.uuid4()
        if "similarity_scores" not in kwargs:
            kwargs["similarity_scores"] = {}
        if "status" not in kwargs:
            kwargs["status"] = MergeReviewStatus.PENDING
        # Calculate review priority based on uncertainty (highest for 0.5)
        if "review_priority" not in kwargs and "confidence" in kwargs:
            # Highest priority for scores closest to 0.5 (most uncertain)
            kwargs["review_priority"] = 1 - abs(kwargs["confidence"] - 0.5) * 2
        super().__init__(**kwargs)

    @property
    def is_pending(self) -> bool:
        """Check if review is still pending."""
        return self.status == MergeReviewStatus.PENDING

    @property
    def is_resolved(self) -> bool:
        """Check if review has been resolved (approved/rejected)."""
        return self.status in (MergeReviewStatus.APPROVED, MergeReviewStatus.REJECTED)

    def __repr__(self) -> str:
        """Return string representation."""
        return f"<MergeReviewItem {self.id} ({self.status.value}) conf={self.confidence:.2f}>"
