"""
OAuth provider model for multi-tenant authentication.

This module defines the OAuthProvider model and ProviderType enum, which
represent OAuth provider configurations for tenants. Each tenant can have
a custom OAuth provider or use the default Keycloak instance.
"""

from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum as SQLEnum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from kg_builder.db import Base

if TYPE_CHECKING:
    from kg_builder.models.tenant import Tenant


class ProviderType(str, enum.Enum):
    """
    OAuth provider types supported by the system.

    The enum inherits from str to enable JSON serialization and database
    storage as text values.
    """

    KEYCLOAK = "keycloak"
    AZURE_AD = "azure_ad"
    OKTA = "okta"
    GOOGLE = "google"
    CUSTOM = "custom"


class OAuthProvider(Base):
    """
    OAuth provider configuration for a tenant.

    Each tenant can have a custom OAuth provider or use the default Keycloak
    instance. The provider configuration includes the issuer URL, client
    credentials, and OAuth endpoints. Some endpoints can be auto-discovered
    via the OIDC discovery endpoint.

    Attributes:
        id: Integer primary key (internal only, not exposed externally)
        tenant_id: Foreign key to the tenant (unique - one provider per tenant)
        provider_type: Type of OAuth provider (keycloak, azure_ad, etc.)
        issuer: OAuth issuer URL (iss claim from tokens)
        client_id: OAuth client ID
        client_secret: Optional OAuth client secret (some flows don't require it)
        jwks_uri: JWKS endpoint URL for token validation (required)
        authorization_endpoint: Optional OAuth authorization endpoint
        token_endpoint: Optional OAuth token endpoint
        userinfo_endpoint: Optional OAuth userinfo endpoint
        discovery_endpoint: Optional OIDC discovery endpoint
        is_active: Soft delete flag (False = deleted but preserved for audit)
        created_at: UTC timestamp of provider creation (inherited from Base)
        updated_at: UTC timestamp of last update (inherited from Base)
        tenant: Relationship to the tenant this provider belongs to
    """

    __tablename__ = "oauth_providers"

    # Standard integer ID (no need for UUID, not exposed externally)
    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment="Integer primary key (internal use only)",
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # One OAuth provider per tenant
        index=True,
        comment="Tenant this provider belongs to (unique)",
    )

    provider_type: Mapped[ProviderType] = mapped_column(
        SQLEnum(ProviderType, name="provider_type"),
        nullable=False,
        comment="Type of OAuth provider",
    )

    issuer: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="OAuth issuer URL (iss claim)",
    )

    client_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="OAuth client ID",
    )

    client_secret: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="OAuth client secret (encrypted in production)",
    )

    jwks_uri: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="JWKS endpoint URL for token validation",
    )

    authorization_endpoint: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="OAuth authorization endpoint",
    )

    token_endpoint: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="OAuth token endpoint",
    )

    userinfo_endpoint: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="OAuth userinfo endpoint",
    )

    discovery_endpoint: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="OIDC discovery endpoint (/.well-known/openid-configuration)",
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
        back_populates="oauth_providers",
        doc="Tenant this provider belongs to",
    )

    def __init__(self, **kwargs):
        """Initialize OAuth provider with default values for optional fields."""
        # Set default for is_active if not provided
        if "is_active" not in kwargs:
            kwargs["is_active"] = True
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        """Return string representation of the OAuth provider."""
        return f"<OAuthProvider {self.provider_type.value} for tenant {self.tenant_id}>"
