"""
Factory for creating extraction service instances from provider configs.

This module provides a factory facade that delegates to the ExtractionProviderRegistry.
The registry implements the Open/Closed Principle - new providers can be added by
registering them without modifying this factory code.

For adding new providers, see kg_builder.extraction.registry.

Example:
    provider = await db.get(ExtractionProvider, provider_id)
    service = ExtractionProviderFactory.create_service(provider, tenant_id)
    result = await service.extract(content, page_url)
"""

import logging
from uuid import UUID

from kg_builder.extraction.base import BaseExtractionService
from kg_builder.extraction.registry import (
    ExtractionProviderRegistry,
    ProviderConfigError,
    get_extraction_provider_registry,
)
from kg_builder.models.extraction_provider import ExtractionProvider, ExtractionProviderType

logger = logging.getLogger(__name__)

# Re-export for backwards compatibility
__all__ = ["ExtractionProviderFactory", "ProviderConfigError"]


class ExtractionProviderFactory:
    """Factory for creating extraction services from provider configs.

    This factory delegates to the ExtractionProviderRegistry, which implements
    the Open/Closed Principle. New provider types can be registered with the
    registry without modifying this class.

    This class is maintained for backwards compatibility. For new code,
    consider using the registry directly:

        from kg_builder.extraction.registry import get_extraction_provider_registry

        registry = get_extraction_provider_registry()
        service = registry.create_service(provider, tenant_id)

    To add a new provider type:

        from kg_builder.extraction.registry import (
            extraction_provider_registry,
            ExtractionServiceCreator,
        )

        @extraction_provider_registry.register(ExtractionProviderType.CUSTOM)
        class CustomServiceCreator(ExtractionServiceCreator):
            def create(self, provider, config, tenant_id):
                return CustomExtractionService(...)

            def validate_config(self, config):
                return []
    """

    _registry: ExtractionProviderRegistry | None = None

    @classmethod
    def _get_registry(cls) -> ExtractionProviderRegistry:
        """Get the provider registry instance."""
        if cls._registry is None:
            cls._registry = get_extraction_provider_registry()
        return cls._registry

    @classmethod
    def create_service(
        cls,
        provider: ExtractionProvider,
        tenant_id: UUID,
    ) -> BaseExtractionService:
        """Create an extraction service from a provider configuration.

        Args:
            provider: ExtractionProvider model instance
            tenant_id: Tenant ID for key decryption

        Returns:
            Configured extraction service instance

        Raises:
            ProviderConfigError: If configuration is invalid
            ProviderNotRegisteredError: If provider type is not registered
        """
        return cls._get_registry().create_service(provider, tenant_id)

    @classmethod
    def get_supported_provider_types(cls) -> list[str]:
        """Return list of supported provider types.

        Returns:
            List of provider type strings
        """
        return cls._get_registry().get_supported_provider_types()

    @classmethod
    def validate_config(
        cls,
        provider_type: ExtractionProviderType,
        config: dict,
    ) -> list[str]:
        """Validate provider configuration.

        Args:
            provider_type: Type of provider
            config: Configuration dict to validate

        Returns:
            List of validation error messages (empty if valid)
        """
        return cls._get_registry().validate_config(provider_type, config)
