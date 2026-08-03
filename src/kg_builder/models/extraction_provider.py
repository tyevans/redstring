"""
SQLAlchemy model for extraction provider configurations.

This model stores tenant-scoped provider configurations for LLM-based entity
extraction. Sensitive configuration fields (like API keys) are encrypted at
rest using the application's encryption service.

Multi-tenancy:
- Each provider is scoped to a tenant via tenant_id FK
- RLS policies enforce isolation at database level
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from kg_builder.db import Base

if TYPE_CHECKING:
    from kg_builder.models.scraping_job import ScrapingJob
    from kg_builder.models.tenant import Tenant


class ExtractionProviderType(str, enum.Enum):
    """Types of extraction providers."""

    OLLAMA = "ollama"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class ExtractionProvider(Base):
    """Extraction provider configuration for a tenant.

    Stores the configuration needed to connect to and use an LLM provider
    for entity extraction. Configuration is stored as JSON and may contain
    encrypted fields.

    Attributes:
        id: Unique identifier
        tenant_id: Owning tenant (RLS enforced)
        name: Display name for the provider
        provider_type: Type of provider (ollama, openai, anthropic)
        config: Provider-specific configuration (JSON)
        default_model: Default model for extraction
        embedding_model: Model for generating embeddings (entity matching)
        is_active: Whether provider is enabled
        is_default: Whether this is the default provider for the tenant
        rate_limit_rpm: Requests per minute limit
        max_context_length: Maximum context length for extraction
        timeout_seconds: Request timeout in seconds
        created_at: When created
        updated_at: Last update time

    RLS Policy:
        All queries filtered by tenant_id via PostgreSQL Row-Level Security
    """

    __tablename__ = "extraction_providers"

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

    provider_type: Mapped[ExtractionProviderType] = mapped_column(
        SQLEnum(
            ExtractionProviderType,
            name="extraction_provider_type",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        comment="Type of extraction provider",
    )

    # Configuration stored as JSON - sensitive fields should be encrypted
    # Example: {"base_url": "http://...", "api_key": "enc:v1:..."}
    config: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        insert_default=dict,
        comment="Provider-specific configuration (may contain encrypted fields)",
    )

    # Model settings
    default_model: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Default model for extraction (e.g., gpt-4o, gemma3:12b)",
    )

    embedding_model: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Model for embeddings (e.g., text-embedding-3-small)",
    )

    # Operational settings
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        insert_default=True,
        nullable=False,
        comment="Whether this provider is enabled",
    )

    is_default: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        insert_default=False,
        nullable=False,
        comment="Whether this is the default provider for the tenant",
    )

    rate_limit_rpm: Mapped[int] = mapped_column(
        Integer,
        default=30,
        insert_default=30,
        nullable=False,
        comment="Requests per minute limit",
    )

    max_context_length: Mapped[int] = mapped_column(
        Integer,
        default=4000,
        insert_default=4000,
        nullable=False,
        comment="Maximum context length for extraction",
    )

    timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        default=300,
        insert_default=300,
        nullable=False,
        comment="Request timeout in seconds",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        onupdate=func.now(),
        nullable=True,
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(
        "Tenant",
        back_populates="extraction_providers",
    )

    scraping_jobs: Mapped[list["ScrapingJob"]] = relationship(
        "ScrapingJob",
        back_populates="extraction_provider",
    )

    def __init__(self, **kwargs):
        """Initialize provider with default values for optional fields."""
        if "id" not in kwargs:
            kwargs["id"] = uuid.uuid4()
        if "config" not in kwargs:
            kwargs["config"] = {}
        if "is_active" not in kwargs:
            kwargs["is_active"] = True
        if "is_default" not in kwargs:
            kwargs["is_default"] = False
        if "rate_limit_rpm" not in kwargs:
            kwargs["rate_limit_rpm"] = 30
        if "max_context_length" not in kwargs:
            kwargs["max_context_length"] = 4000
        if "timeout_seconds" not in kwargs:
            kwargs["timeout_seconds"] = 300
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return f"<ExtractionProvider(id={self.id}, name={self.name}, type={self.provider_type.value})>"
