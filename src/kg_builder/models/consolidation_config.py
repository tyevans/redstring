"""
Consolidation configuration model for tenant-level settings.

This module defines per-tenant configuration for the entity consolidation
system, including confidence thresholds, feature weights, and operational
settings.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from kg_builder.db import Base

if TYPE_CHECKING:
    from kg_builder.models.tenant import Tenant


# Default configuration values
DEFAULT_AUTO_MERGE_THRESHOLD = 0.90
DEFAULT_REVIEW_THRESHOLD = 0.50
DEFAULT_MAX_BLOCK_SIZE = 500
DEFAULT_EMBEDDING_MODEL = "bge-m3"

DEFAULT_FEATURE_WEIGHTS = {
    "jaro_winkler": 0.3,
    "normalized_exact": 0.4,
    "type_match": 0.2,
    "same_page_bonus": 0.1,
    "embedding_cosine": 0.5,
    "graph_neighborhood": 0.3,
    "fast_composite": 0.2,
}


class ConsolidationConfig(Base):
    """
    Per-tenant configuration for entity consolidation.

    Each tenant can customize their consolidation behavior including
    confidence thresholds, feature weights, and which similarity
    methods to enable.

    Attributes:
        tenant_id: Primary key and foreign key to tenant
        auto_merge_threshold: Confidence threshold for automatic merging
        review_threshold: Confidence threshold for queueing human review
        max_block_size: Maximum entities per blocking group
        enable_embedding_similarity: Whether to compute embedding similarity
        enable_graph_similarity: Whether to compute graph neighborhood similarity
        enable_auto_consolidation: Whether to run consolidation on new extraction
        embedding_model: Embedding model to use for semantic similarity
        feature_weights: Weights for combining similarity scores
        created_at: When config was created
        updated_at: When config was last updated
    """

    __tablename__ = "consolidation_config"

    # Exclude inherited id column - this table uses tenant_id as primary key
    id = None

    # Primary key is tenant_id (one config per tenant)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Tenant this configuration belongs to",
    )

    # Confidence thresholds
    auto_merge_threshold: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=DEFAULT_AUTO_MERGE_THRESHOLD,
        server_default=str(DEFAULT_AUTO_MERGE_THRESHOLD),
        comment="Confidence threshold for automatic merging (0.0-1.0)",
    )

    review_threshold: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=DEFAULT_REVIEW_THRESHOLD,
        server_default=str(DEFAULT_REVIEW_THRESHOLD),
        comment="Confidence threshold for queueing human review (0.0-1.0)",
    )

    # Operational settings
    max_block_size: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_MAX_BLOCK_SIZE,
        server_default=str(DEFAULT_MAX_BLOCK_SIZE),
        comment="Maximum entities per blocking group",
    )

    # Feature toggles
    enable_embedding_similarity: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="Whether to compute embedding similarity",
    )

    enable_graph_similarity: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="Whether to compute graph neighborhood similarity",
    )

    enable_auto_consolidation: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="Whether to run consolidation on new entity extraction",
    )

    # Model configuration
    embedding_model: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default=DEFAULT_EMBEDDING_MODEL,
        server_default=f"'{DEFAULT_EMBEDDING_MODEL}'",
        comment="Embedding model to use for semantic similarity",
    )

    # Feature weights (stored as JSONB for flexibility)
    feature_weights: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: DEFAULT_FEATURE_WEIGHTS.copy(),
        comment="Weights for combining similarity scores",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default="now()",
        comment="When config was created",
    )

    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=lambda: datetime.now(UTC),
        comment="When config was last updated",
    )

    # Relationships
    tenant: Mapped[Tenant] = relationship(
        "Tenant",
        doc="Tenant this configuration belongs to",
    )

    def __init__(self, **kwargs):
        """Initialize config with defaults."""
        if "auto_merge_threshold" not in kwargs:
            kwargs["auto_merge_threshold"] = DEFAULT_AUTO_MERGE_THRESHOLD
        if "review_threshold" not in kwargs:
            kwargs["review_threshold"] = DEFAULT_REVIEW_THRESHOLD
        if "max_block_size" not in kwargs:
            kwargs["max_block_size"] = DEFAULT_MAX_BLOCK_SIZE
        if "enable_embedding_similarity" not in kwargs:
            kwargs["enable_embedding_similarity"] = True
        if "enable_graph_similarity" not in kwargs:
            kwargs["enable_graph_similarity"] = True
        if "enable_auto_consolidation" not in kwargs:
            kwargs["enable_auto_consolidation"] = True
        if "embedding_model" not in kwargs:
            kwargs["embedding_model"] = DEFAULT_EMBEDDING_MODEL
        if "feature_weights" not in kwargs:
            kwargs["feature_weights"] = DEFAULT_FEATURE_WEIGHTS.copy()
        super().__init__(**kwargs)

    @classmethod
    def get_defaults(cls) -> dict:
        """Return dictionary of default configuration values."""
        return {
            "auto_merge_threshold": DEFAULT_AUTO_MERGE_THRESHOLD,
            "review_threshold": DEFAULT_REVIEW_THRESHOLD,
            "max_block_size": DEFAULT_MAX_BLOCK_SIZE,
            "enable_embedding_similarity": True,
            "enable_graph_similarity": True,
            "enable_auto_consolidation": True,
            "embedding_model": DEFAULT_EMBEDDING_MODEL,
            "feature_weights": DEFAULT_FEATURE_WEIGHTS.copy(),
        }

    def get_weight(self, feature: str) -> float:
        """Get weight for a specific feature, with fallback to default.

        Args:
            feature: Feature name (e.g., 'jaro_winkler', 'embedding_cosine')

        Returns:
            Weight value between 0.0 and 1.0
        """
        return self.feature_weights.get(feature, DEFAULT_FEATURE_WEIGHTS.get(feature, 0.0))

    def set_weight(self, feature: str, weight: float) -> None:
        """Set weight for a specific feature.

        Args:
            feature: Feature name
            weight: Weight value (should be between 0.0 and 1.0)

        Raises:
            ValueError: If weight is not between 0.0 and 1.0
        """
        if not 0.0 <= weight <= 1.0:
            raise ValueError(f"Weight must be between 0.0 and 1.0, got {weight}")
        self.feature_weights[feature] = weight

    @property
    def is_valid(self) -> bool:
        """Check if configuration is valid.

        Returns:
            True if thresholds are valid and review < auto_merge
        """
        return (
            0.0 <= self.auto_merge_threshold <= 1.0
            and 0.0 <= self.review_threshold <= 1.0
            and self.review_threshold < self.auto_merge_threshold
            and self.max_block_size > 0
        )

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"<ConsolidationConfig tenant={self.tenant_id} "
            f"auto={self.auto_merge_threshold} review={self.review_threshold}>"
        )
