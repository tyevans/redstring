"""
Merge history model for tracking entity consolidation operations.

This module provides a denormalized view of merge events for efficient
querying of entity merge history, supporting the audit trail and undo
functionality.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from kg_builder.db import Base

if TYPE_CHECKING:
    from kg_builder.models.extracted_entity import ExtractedEntity
    from kg_builder.models.tenant import Tenant
    from kg_builder.models.user import User


class MergeEventType(str, enum.Enum):
    """Types of merge-related events."""

    ENTITIES_MERGED = "entities_merged"
    MERGE_UNDONE = "merge_undone"
    ENTITY_SPLIT = "entity_split"


class MergeHistory(Base):
    """
    Denormalized merge history for efficient querying.

    This table is populated by the consolidation projection handler,
    providing a queryable view of merge events without requiring
    event store queries.

    Attributes:
        id: UUID primary key
        tenant_id: Foreign key to tenant (RLS enforced)
        event_id: Reference to the source domain event
        event_type: Type of merge operation
        canonical_entity_id: The canonical entity (for merges)
        affected_entity_ids: All entity IDs involved in the operation
        merge_reason: Why the merge occurred
        similarity_scores: Similarity scores at time of merge
        performed_by: User who performed/approved the operation
        performed_at: When the operation occurred
        undone: Whether this merge has been undone
        undone_at: When the merge was undone
        undone_by: Who undone the merge
        undo_reason: Reason for undoing
        created_at: Record creation timestamp
    """

    __tablename__ = "merge_history"

    # Exclude inherited columns - this model defines its own id and created_at
    # and doesn't use updated_at (table doesn't have this column)
    updated_at = None

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
        comment="Tenant this history belongs to (RLS enforced)",
    )

    # Event reference
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        unique=True,
        comment="Reference to the source domain event",
    )

    event_type: Mapped[MergeEventType] = mapped_column(
        SQLEnum(
            MergeEventType,
            name="merge_event_type",
            values_callable=lambda obj: [e.value for e in obj],
            create_type=False,  # Created by migration
        ),
        nullable=False,
        index=True,
        comment="Type of merge operation",
    )

    # Entity references
    canonical_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extracted_entities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="The canonical entity (for merges)",
    )

    affected_entity_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)),
        nullable=False,
        comment="All entity IDs involved in the operation",
    )

    # Operation details
    merge_reason: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Why the merge occurred (auto_high_confidence, user_approved, batch)",
    )

    similarity_scores: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Similarity scores at time of merge",
    )

    details: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Additional operation details (property assignments for split, etc.)",
    )

    # Who and when
    performed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="User who performed/approved the operation",
    )

    performed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="When the operation occurred",
    )

    # Undo tracking
    undone: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="Whether this merge has been undone",
    )

    undone_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the merge was undone",
    )

    undone_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="Who undone the merge",
    )

    undo_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Reason for undoing the merge",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default="now()",
        comment="Record creation timestamp",
    )

    # Relationships
    tenant: Mapped[Tenant] = relationship(
        "Tenant",
        doc="Tenant this history belongs to",
    )

    canonical_entity: Mapped[ExtractedEntity | None] = relationship(
        "ExtractedEntity",
        foreign_keys=[canonical_entity_id],
        doc="The canonical entity (for merges)",
    )

    performer: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[performed_by],
        doc="User who performed the operation",
    )

    undoer: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[undone_by],
        doc="User who undone the merge",
    )

    def __init__(self, **kwargs):
        """Initialize history record with defaults."""
        if "id" not in kwargs:
            kwargs["id"] = uuid.uuid4()
        if "undone" not in kwargs:
            kwargs["undone"] = False
        super().__init__(**kwargs)

    @property
    def can_undo(self) -> bool:
        """Check if this merge can be undone.

        Only ENTITIES_MERGED events that haven't been undone yet can be undone.
        """
        return self.event_type == MergeEventType.ENTITIES_MERGED and not self.undone

    @property
    def affected_entity_count(self) -> int:
        """Return the number of entities affected by this operation."""
        return len(self.affected_entity_ids) if self.affected_entity_ids else 0

    def __repr__(self) -> str:
        """Return string representation."""
        return f"<MergeHistory {self.id} {self.event_type.value} at {self.performed_at}>"
