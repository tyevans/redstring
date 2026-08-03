"""
User-Tenant Membership model for multi-tenant user access.

This module defines the UserTenantMembership model, which represents a user's
membership in a tenant. This enables users to belong to multiple tenants with
different roles in each.
"""

from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from kg_builder.db import Base

if TYPE_CHECKING:
    from kg_builder.models.tenant import Tenant
    from kg_builder.models.user import User


class MembershipRole(str, enum.Enum):
    """
    Role levels for tenant membership.

    Defines the permission level a user has within a tenant.
    """

    OWNER = "owner"  # Full control, can delete tenant
    ADMIN = "admin"  # Can manage users and settings
    MEMBER = "member"  # Standard access


class UserTenantMembership(Base):
    """
    Represents a user's membership in a tenant.

    This junction table enables many-to-many relationships between users and
    tenants, allowing a single user to belong to multiple tenants with different
    roles in each.

    Attributes:
        id: UUID primary key for security (prevents enumeration attacks)
        user_id: Foreign key to the user
        tenant_id: Foreign key to the tenant
        role: The user's role within this tenant
        is_default: Whether this is the user's default tenant
        is_active: Soft delete flag (False = membership revoked but preserved)
        created_at: UTC timestamp of membership creation (inherited from Base)
        updated_at: UTC timestamp of last update (inherited from Base)
        user: Relationship to the User model
        tenant: Relationship to the Tenant model
    """

    __tablename__ = "user_tenant_memberships"

    # Override id from Base to use UUID instead of integer
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        insert_default=uuid.uuid4,
        index=True,
        comment="UUID primary key for security and distributed ID generation",
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="User who has membership in the tenant",
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Tenant the user has membership in",
    )

    role: Mapped[MembershipRole] = mapped_column(
        Enum(MembershipRole, name="membership_role", create_constraint=True),
        nullable=False,
        default=MembershipRole.MEMBER,
        insert_default=MembershipRole.MEMBER,
        comment="User's role within this tenant",
    )

    is_default: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        insert_default=False,
        comment="Whether this is the user's default tenant",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        insert_default=True,
        comment="Soft delete flag (False = membership revoked but preserved)",
    )

    # Relationships
    user: Mapped[User] = relationship(
        "User",
        back_populates="memberships",
        doc="User who has this membership",
    )

    tenant: Mapped[Tenant] = relationship(
        "Tenant",
        back_populates="memberships",
        doc="Tenant this membership is for",
    )

    # Constraints and indexes
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "tenant_id",
            name="uq_user_tenant_membership",
        ),
        Index(
            "ix_user_tenant_memberships_user_tenant",
            "user_id",
            "tenant_id",
        ),
    )

    def __init__(self, **kwargs):
        """Initialize membership with default values for optional fields."""
        if "id" not in kwargs:
            kwargs["id"] = uuid.uuid4()
        if "role" not in kwargs:
            kwargs["role"] = MembershipRole.MEMBER
        if "is_default" not in kwargs:
            kwargs["is_default"] = False
        if "is_active" not in kwargs:
            kwargs["is_active"] = True
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        """Return string representation of the membership."""
        return f"<UserTenantMembership user={self.user_id} tenant={self.tenant_id} role={self.role.value}>"
