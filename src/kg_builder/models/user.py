"""
User model for multi-tenant authentication.

This module defines the User model, which represents a user within a tenant.
Users are authenticated via OAuth and identified by their oauth_subject claim
from the OAuth provider's token.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from kg_builder.db import Base

if TYPE_CHECKING:
    from kg_builder.models.tenant import Tenant
    from kg_builder.models.user_tenant_membership import UserTenantMembership


class User(Base):
    """
    Represents a user within a tenant.

    Users are authenticated via OAuth and identified by their oauth_subject
    claim from the OAuth provider. The same oauth_subject can exist in
    different tenants (e.g., the same Google account used in multiple
    organizations), but must be unique within a tenant.

    Attributes:
        id: UUID primary key for security (prevents enumeration attacks)
        tenant_id: Foreign key to the tenant this user belongs to
        oauth_subject: Subject claim from OAuth token (e.g., 'google|12345')
        email: User's email address from OAuth token
        display_name: Optional display name for the user
        is_active: Soft delete flag (False = deleted but preserved for audit)
        created_at: UTC timestamp of user creation (inherited from Base)
        updated_at: UTC timestamp of last update (inherited from Base)
        tenant: Relationship to the tenant this user belongs to
    """

    __tablename__ = "users"

    # Override id from Base to use UUID instead of integer
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        insert_default=uuid.uuid4,
        index=True,
        comment="UUID primary key for security and distributed ID generation",
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Tenant this user belongs to",
    )

    oauth_subject: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="Subject claim from OAuth token (sub)",
    )

    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="User's email address",
    )

    display_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Optional display name for user",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        insert_default=True,
        comment="Soft delete flag (False = deleted but preserved)",
    )

    # Relationships
    tenant: Mapped[Tenant] = relationship(
        "Tenant",
        back_populates="users",
        doc="Tenant this user belongs to (legacy, use memberships for multi-tenant)",
    )

    memberships: Mapped[list[UserTenantMembership]] = relationship(
        "UserTenantMembership",
        back_populates="user",
        cascade="all, delete-orphan",
        doc="Tenant memberships for this user (multi-tenant support)",
    )

    # Constraints
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "oauth_subject",
            name="uq_tenant_oauth_subject",
        ),
    )

    def __init__(self, **kwargs):
        """Initialize user with default values for optional fields."""
        # Set defaults for optional fields if not provided
        if "id" not in kwargs:
            kwargs["id"] = uuid.uuid4()
        if "is_active" not in kwargs:
            kwargs["is_active"] = True
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        """Return string representation of the user."""
        return f"<User {self.email} (tenant: {self.tenant_id})>"
