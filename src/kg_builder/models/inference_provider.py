"""
SQLAlchemy model for inference provider configurations.

This model stores tenant-scoped provider configurations for LLM inference.
Sensitive configuration fields (like API keys) are encrypted at rest using
the application's encryption service.

Multi-tenancy:
- Each provider is scoped to a tenant via tenant_id FK
- RLS policies enforce isolation at database level
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from kg_builder.db import Base

if TYPE_CHECKING:
    from kg_builder.models.inference_request import InferenceRequest
    from kg_builder.models.tenant import Tenant


class ProviderType(str, enum.Enum):
    """Types of inference providers."""

    OLLAMA = "ollama"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GROQ = "groq"


class InferenceProvider(Base):
    """Inference provider configuration for a tenant.

    Stores the configuration needed to connect to and use an LLM provider.
    Configuration is stored as JSON and may contain encrypted fields.

    Attributes:
        id: Unique identifier
        tenant_id: Owning tenant (RLS enforced)
        name: Display name for the provider
        provider_type: Type of provider (ollama, openai, etc.)
        config: Provider-specific configuration (JSON)
        default_model: Default model to use
        default_temperature: Default temperature setting
        default_max_tokens: Default max tokens setting
        is_active: Whether provider is enabled
        rate_limit_preset: Rate limit preset name (conservative, balanced, permissive)
        created_at: When created
        updated_at: Last update time

    RLS Policy:
        All queries filtered by tenant_id via PostgreSQL Row-Level Security
    """

    __tablename__ = "inference_providers"

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

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Display name for the provider",
    )

    provider_type: Mapped[ProviderType] = mapped_column(
        SQLEnum(
            ProviderType,
            name="inference_provider_type",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        comment="Type of inference provider",
    )

    # Configuration stored as JSON - sensitive fields should be encrypted
    # Example keys: base_url, api_key (stored as "enc:v1:...")
    config: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        insert_default=dict,
        comment="Provider-specific configuration (may contain encrypted fields)",
    )

    # Default inference parameters
    default_model: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Default model to use",
    )

    default_temperature: Mapped[float] = mapped_column(
        Float,
        default=0.7,
        insert_default=0.7,
        nullable=False,
        comment="Default temperature for inference",
    )

    default_max_tokens: Mapped[int] = mapped_column(
        Integer,
        default=1024,
        insert_default=1024,
        nullable=False,
        comment="Default max tokens for inference",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        insert_default=True,
        nullable=False,
        comment="Whether this provider is enabled",
    )

    # Rate limiting
    rate_limit_preset: Mapped[str | None] = mapped_column(
        String(50),
        default="balanced",
        insert_default="balanced",
        nullable=True,
        comment="Rate limit preset name",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        onupdate=func.now(),
        nullable=True,
    )

    # Relationships
    tenant: Mapped[Tenant] = relationship(
        "Tenant",
        back_populates="inference_providers",
    )

    inference_requests: Mapped[list[InferenceRequest]] = relationship(
        "InferenceRequest",
        back_populates="provider",
        cascade="all, delete-orphan",
    )

    def __init__(self, **kwargs):
        """Initialize provider with default values for optional fields."""
        if "id" not in kwargs:
            kwargs["id"] = uuid.uuid4()
        if "config" not in kwargs:
            kwargs["config"] = {}
        if "is_active" not in kwargs:
            kwargs["is_active"] = True
        if "rate_limit_preset" not in kwargs:
            kwargs["rate_limit_preset"] = "balanced"
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return (
            f"<InferenceProvider(id={self.id}, name={self.name}, type={self.provider_type.value})>"
        )
