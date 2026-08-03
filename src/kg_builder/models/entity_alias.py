"""
Entity alias model for tracking merged entity names.

This module tracks the original names of entities that have been merged
into a canonical entity, preserving provenance and enabling undo operations.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from kg_builder.db import Base

if TYPE_CHECKING:
    from kg_builder.models.extracted_entity import ExtractedEntity
    from kg_builder.models.scraped_page import ScrapedPage
    from kg_builder.models.tenant import Tenant


class EntityAlias(Base):
    """
    Tracks original entity names that were merged into a canonical entity.

    When entities are merged, the merged entity's name becomes an alias
    of the canonical entity. This preserves the original names for:
    - Querying by any historical name
    - Displaying alias information in UI
    - Enabling undo operations

    Attributes:
        id: UUID primary key
        tenant_id: Foreign key to tenant (RLS enforced)
        canonical_entity_id: The canonical entity this alias belongs to
        alias_name: The original name of the merged entity
        alias_normalized_name: Normalized version for searching
        original_entity_id: The ID of the entity before it was merged (for undo)
        source_page_id: Where the original entity was extracted from
        merged_at: When the merge occurred
        merge_event_id: Reference to the merge event for provenance
        merge_reason: Reason for the merge (auto_high_confidence, user_approved, batch)
        created_at: Record creation timestamp
    """

    __tablename__ = "entity_aliases"

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
        comment="Tenant this alias belongs to (RLS enforced)",
    )

    # Canonical entity reference
    canonical_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extracted_entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="The canonical entity this is an alias of",
    )

    # Alias information
    alias_name: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="Original name of the merged entity",
    )

    alias_normalized_name: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="Normalized version of alias name for searching",
    )

    # Provenance tracking
    original_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="Original entity ID before merge (for undo)",
    )

    source_page_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scraped_pages.id", ondelete="SET NULL"),
        nullable=True,
        comment="Page the original entity was extracted from",
    )

    merge_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Reference to the merge event for provenance",
    )

    merged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="When the entity was merged",
    )

    merge_reason: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Reason for merge (auto_high_confidence, user_approved, batch)",
    )

    # Original entity properties for undo support
    original_entity_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Original entity type before merge",
    )

    original_normalized_name: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        comment="Original normalized name before merge",
    )

    original_description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Original description before merge",
    )

    original_properties: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        default=dict,
        server_default="{}",
        comment="Original entity properties JSONB before merge",
    )

    original_external_ids: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        default=dict,
        server_default="{}",
        comment="Original external identifiers JSONB before merge",
    )

    original_confidence_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        default=1.0,
        server_default="1.0",
        comment="Original confidence score before merge",
    )

    original_source_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Original source text snippet before merge",
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
        doc="Tenant this alias belongs to",
    )

    canonical_entity: Mapped[ExtractedEntity] = relationship(
        "ExtractedEntity",
        back_populates="aliases",
        doc="The canonical entity this is an alias of",
    )

    source_page: Mapped[ScrapedPage | None] = relationship(
        "ScrapedPage",
        doc="Page the original entity was extracted from",
    )

    def __init__(self, **kwargs):
        """Initialize alias with default values."""
        if "id" not in kwargs:
            kwargs["id"] = uuid.uuid4()
        # Auto-normalize alias name if not provided
        if "alias_normalized_name" not in kwargs and "alias_name" in kwargs:
            from kg_builder.models.extracted_entity import ExtractedEntity

            kwargs["alias_normalized_name"] = ExtractedEntity._normalize_name(kwargs["alias_name"])
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        """Return string representation."""
        return f"<EntityAlias {self.id} '{self.alias_name}' -> {self.canonical_entity_id}>"
