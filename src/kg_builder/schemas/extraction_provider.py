"""
Pydantic schemas for extraction provider API.

These schemas define request and response formats for the extraction
provider management endpoints.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from kg_builder.models.extraction_provider import ExtractionProviderType


class CreateExtractionProviderRequest(BaseModel):
    """Request schema for creating an extraction provider."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Display name for the provider",
    )
    provider_type: ExtractionProviderType = Field(
        ...,
        description="Type of provider (ollama, openai, anthropic)",
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider configuration. Include 'api_key' for OpenAI/Anthropic, 'base_url' for Ollama.",
    )
    default_model: str | None = Field(
        None,
        max_length=255,
        description="Default model for extraction (e.g., gpt-4o, gemma3:12b)",
    )
    embedding_model: str | None = Field(
        None,
        max_length=255,
        description="Model for embeddings (e.g., text-embedding-3-small)",
    )
    is_active: bool = Field(
        default=True,
        description="Whether this provider is enabled",
    )
    is_default: bool = Field(
        default=False,
        description="Whether this is the default provider for the tenant",
    )
    rate_limit_rpm: int = Field(
        default=30,
        ge=1,
        le=1000,
        description="Requests per minute limit",
    )
    max_context_length: int = Field(
        default=4000,
        ge=100,
        le=128000,
        description="Maximum context length for extraction",
    )
    timeout_seconds: int = Field(
        default=300,
        ge=10,
        le=3600,
        description="Request timeout in seconds",
    )

    @field_validator("config")
    @classmethod
    def validate_config(cls, v: dict, info) -> dict:
        """Validate config based on provider type."""
        # Note: Provider type validation happens at creation time in the router
        return v


class UpdateExtractionProviderRequest(BaseModel):
    """Request schema for updating an extraction provider."""

    name: str | None = Field(
        None,
        min_length=1,
        max_length=255,
        description="Display name for the provider",
    )
    config: dict[str, Any] | None = Field(
        None,
        description="Provider configuration (replaces existing config)",
    )
    default_model: str | None = Field(
        None,
        max_length=255,
        description="Default model for extraction",
    )
    embedding_model: str | None = Field(
        None,
        max_length=255,
        description="Model for embeddings",
    )
    is_active: bool | None = Field(
        None,
        description="Whether this provider is enabled",
    )
    is_default: bool | None = Field(
        None,
        description="Whether this is the default provider",
    )
    rate_limit_rpm: int | None = Field(
        None,
        ge=1,
        le=1000,
        description="Requests per minute limit",
    )
    max_context_length: int | None = Field(
        None,
        ge=100,
        le=128000,
        description="Maximum context length",
    )
    timeout_seconds: int | None = Field(
        None,
        ge=10,
        le=3600,
        description="Request timeout in seconds",
    )


class ExtractionProviderResponse(BaseModel):
    """Response schema for extraction provider."""

    id: UUID = Field(..., description="Provider ID")
    tenant_id: UUID = Field(..., description="Tenant ID")
    name: str = Field(..., description="Display name")
    provider_type: ExtractionProviderType = Field(..., description="Provider type")
    config: dict[str, Any] = Field(..., description="Provider configuration (API key masked)")
    default_model: str | None = Field(None, description="Default model")
    embedding_model: str | None = Field(None, description="Embedding model")
    is_active: bool = Field(..., description="Whether enabled")
    is_default: bool = Field(..., description="Whether default for tenant")
    rate_limit_rpm: int = Field(..., description="Rate limit RPM")
    max_context_length: int = Field(..., description="Max context length")
    timeout_seconds: int = Field(..., description="Timeout seconds")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime | None = Field(None, description="Last update timestamp")

    class Config:
        from_attributes = True

    @classmethod
    def from_orm_masked(cls, provider) -> "ExtractionProviderResponse":
        """Create response with masked API key.

        Args:
            provider: ExtractionProvider ORM instance

        Returns:
            ExtractionProviderResponse with masked sensitive data
        """
        config = dict(provider.config) if provider.config else {}

        # Mask API key
        if config.get("api_key"):
            key = str(config["api_key"])
            if key.startswith("enc:"):
                config["api_key"] = "****encrypted****"
            elif len(key) > 8:
                config["api_key"] = f"{key[:4]}****{key[-4:]}"
            else:
                config["api_key"] = "****"

        return cls(
            id=provider.id,
            tenant_id=provider.tenant_id,
            name=provider.name,
            provider_type=provider.provider_type,
            config=config,
            default_model=provider.default_model,
            embedding_model=provider.embedding_model,
            is_active=provider.is_active,
            is_default=provider.is_default,
            rate_limit_rpm=provider.rate_limit_rpm,
            max_context_length=provider.max_context_length,
            timeout_seconds=provider.timeout_seconds,
            created_at=provider.created_at,
            updated_at=provider.updated_at,
        )


class TestConnectionRequest(BaseModel):
    """Request for testing provider connection."""

    # Optional test content for extraction test
    test_content: str | None = Field(
        None,
        max_length=1000,
        description="Optional content to test extraction with",
    )


class TestConnectionResponse(BaseModel):
    """Response from provider connection test."""

    success: bool = Field(..., description="Whether connection test succeeded")
    details: dict[str, Any] = Field(..., description="Test details and results")


class ProviderTypeInfo(BaseModel):
    """Information about a provider type."""

    type: str = Field(..., description="Provider type identifier")
    name: str = Field(..., description="Human-readable name")
    description: str = Field(..., description="Provider description")
    requires_api_key: bool = Field(..., description="Whether API key is required")
    default_models: list[str] = Field(..., description="Suggested default models")
    embedding_models: list[str] = Field(
        default_factory=list, description="Available embedding models"
    )


# Provider type metadata
PROVIDER_TYPE_INFO: dict[str, ProviderTypeInfo] = {
    "ollama": ProviderTypeInfo(
        type="ollama",
        name="Ollama",
        description="Local LLM inference using Ollama",
        requires_api_key=False,
        default_models=["gemma3:12b", "llama3.2:latest", "mistral:latest"],
        embedding_models=[],  # Ollama embeddings handled separately
    ),
    "openai": ProviderTypeInfo(
        type="openai",
        name="OpenAI",
        description="OpenAI GPT models for extraction",
        requires_api_key=True,
        default_models=["gpt-4o", "gpt-4-turbo", "gpt-4o-mini"],
        embedding_models=["text-embedding-3-small", "text-embedding-3-large"],
    ),
    "anthropic": ProviderTypeInfo(
        type="anthropic",
        name="Anthropic",
        description="Anthropic Claude models for extraction",
        requires_api_key=True,
        default_models=["claude-sonnet-4-20250514", "claude-3-5-haiku-20241022"],
        embedding_models=[],  # Anthropic doesn't have embeddings API
    ),
}
