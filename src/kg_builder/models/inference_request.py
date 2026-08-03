"""
SQLAlchemy model for inference request history.

This model serves as a read projection for inference history, built
from event sourcing data. It enables efficient querying of historical
inference requests without replaying events.

Multi-tenancy:
- Each request is scoped to a tenant via tenant_id FK
- RLS policies enforce isolation at database level
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from kg_builder.db import Base

if TYPE_CHECKING:
    from kg_builder.models.inference_provider import InferenceProvider
    from kg_builder.models.tenant import Tenant


class InferenceStatus(str, enum.Enum):
    """Status of an inference request."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InferenceRequest(Base):
    """Record of an inference request (projection from events).

    This table is populated by event handlers listening to inference events.
    It provides a denormalized view for efficient history queries.

    Attributes:
        id: Unique identifier (matches aggregate_id in events)
        tenant_id: Owning tenant (RLS enforced)
        provider_id: Provider used (may be null if provider deleted)
        user_id: User who made request (may be null if user deleted)
        model: Model used for inference
        prompt: Input prompt text
        response: Generated response (null if pending/failed)
        status: Current status
        prompt_tokens: Token count for prompt
        completion_tokens: Token count for completion
        total_tokens: Total tokens used
        duration_ms: Request duration in milliseconds
        error: Error message if failed
        parameters: Request parameters (temperature, etc.)
        created_at: When request was made
        completed_at: When request completed

    RLS Policy:
        All queries filtered by tenant_id via PostgreSQL Row-Level Security
    """

    __tablename__ = "inference_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        insert_default=uuid.uuid4,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Tenant (RLS enforced)",
    )

    provider_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inference_providers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Provider used (null if provider deleted)",
    )

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,  # No FK to users table to avoid coupling
        index=True,
        comment="User who made request",
    )

    model: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Model used for inference",
    )

    prompt: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Input prompt",
    )

    response: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Generated response",
    )

    status: Mapped[InferenceStatus] = mapped_column(
        SQLEnum(
            InferenceStatus,
            name="inference_status",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=InferenceStatus.PENDING,
        insert_default=InferenceStatus.PENDING,
        comment="Request status",
    )

    # Metadata
    prompt_tokens: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Number of prompt tokens",
    )

    completion_tokens: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Number of completion tokens",
    )

    total_tokens: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Total tokens used",
    )

    duration_ms: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Request duration in milliseconds",
    )

    error: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Error message if failed",
    )

    parameters: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        insert_default=dict,
        comment="Request parameters (temperature, max_tokens, etc.)",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
        comment="When request was created",
    )

    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When request completed",
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(
        "Tenant",
    )

    provider: Mapped[Optional["InferenceProvider"]] = relationship(
        "InferenceProvider",
        back_populates="inference_requests",
    )

    def __init__(self, **kwargs):
        """Initialize request with default values for optional fields."""
        if "id" not in kwargs:
            kwargs["id"] = uuid.uuid4()
        if "parameters" not in kwargs:
            kwargs["parameters"] = {}
        if "status" not in kwargs:
            kwargs["status"] = InferenceStatus.PENDING
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return f"<InferenceRequest(id={self.id}, model={self.model}, status={self.status.value})>"

    @property
    def prompt_preview(self) -> str:
        """Get truncated prompt for list display."""
        if len(self.prompt) <= 100:
            return self.prompt
        return self.prompt[:97] + "..."
