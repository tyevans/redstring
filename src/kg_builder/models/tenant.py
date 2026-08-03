"""
Tenant model for multi-tenant architecture.

This module defines the Tenant model, which represents an organization or tenant
in the multi-tenant system. Each tenant has isolated data via Row-Level Security
policies and can configure their own OAuth provider.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from kg_builder.db import Base

if TYPE_CHECKING:
    from kg_builder.models.extraction_provider import ExtractionProvider
    from kg_builder.models.inference_provider import InferenceProvider
    from kg_builder.models.oauth_provider import OAuthProvider
    from kg_builder.models.user import User
    from kg_builder.models.user_tenant_membership import UserTenantMembership


class Tenant(Base):
    """
    Represents an organization or tenant in the multi-tenant system.

    Each tenant has isolated data via Row-Level Security policies and can
    configure custom authentication via their own OAuth provider or use the
    default Keycloak instance.

    Attributes:
        id: UUID primary key for security (prevents enumeration attacks)
        slug: URL-safe identifier used for tenant resolution from subdomain
        name: Human-readable display name for the tenant
        settings: JSON field for tenant-specific configuration
        is_active: Soft delete flag (False = deleted but preserved for audit)
        created_at: UTC timestamp of tenant creation (inherited from Base)
        updated_at: UTC timestamp of last update (inherited from Base)
        users: Relationship to users belonging to this tenant
        oauth_providers: Relationship to OAuth providers configured for this tenant
    """

    __tablename__ = "tenants"

    # Override id from Base to use UUID instead of integer
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        insert_default=uuid.uuid4,
        index=True,
        comment="UUID primary key for security and distributed ID generation",
    )

    slug: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
        comment="URL-safe identifier for tenant (e.g., 'acme-corp')",
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Display name for tenant (e.g., 'Acme Corporation')",
    )

    settings: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        insert_default=dict,
        comment="Tenant-specific configuration as JSON",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        insert_default=True,
        comment="Soft delete flag (False = deleted but preserved)",
    )

    # Relationships
    users: Mapped[list[User]] = relationship(
        "User",
        back_populates="tenant",
        cascade="all, delete-orphan",
        doc="Users belonging to this tenant",
    )

    oauth_providers: Mapped[list[OAuthProvider]] = relationship(
        "OAuthProvider",
        back_populates="tenant",
        cascade="all, delete-orphan",
        doc="OAuth providers configured for this tenant",
    )

    memberships: Mapped[list[UserTenantMembership]] = relationship(
        "UserTenantMembership",
        back_populates="tenant",
        cascade="all, delete-orphan",
        doc="User memberships in this tenant (multi-tenant support)",
    )

    inference_providers: Mapped[list[InferenceProvider]] = relationship(
        "InferenceProvider",
        back_populates="tenant",
        cascade="all, delete-orphan",
        doc="Inference providers configured for this tenant",
    )

    extraction_providers: Mapped[list[ExtractionProvider]] = relationship(
        "ExtractionProvider",
        back_populates="tenant",
        cascade="all, delete-orphan",
        doc="Extraction providers configured for this tenant",
    )

    def __init__(self, **kwargs):
        """Initialize tenant with default values for optional fields."""
        # Set defaults for optional fields if not provided
        if "id" not in kwargs:
            kwargs["id"] = uuid.uuid4()
        if "settings" not in kwargs:
            kwargs["settings"] = {}
        if "is_active" not in kwargs:
            kwargs["is_active"] = True
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        """Return string representation of the tenant."""
        return f"<Tenant {self.slug}>"
