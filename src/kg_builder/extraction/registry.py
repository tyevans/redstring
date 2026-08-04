"""
Extraction Provider Registry for Open/Closed Principle compliance.

This module implements a registry pattern that allows new extraction providers
to be registered without modifying existing code. Each provider registers itself
with the registry using a decorator, and the factory uses the registry to
instantiate the appropriate provider.

Usage:
    # Registering a new provider (in provider module):
    @extraction_provider_registry.register(ExtractionProviderType.OPENAI)
    class OpenAIServiceCreator(ExtractionServiceCreator):
        def create(self, provider: ExtractionProvider, tenant_id: UUID) -> BaseExtractionService:
            ...

    # Using the registry (in factory):
    service = extraction_provider_registry.create_service(provider, tenant_id)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING
from uuid import UUID

from kg_builder.extraction.base import BaseExtractionService, ExtractionError
from kg_builder.models.extraction_provider import ExtractionProviderType

if TYPE_CHECKING:
    from kg_builder.models.extraction_provider import ExtractionProvider

logger = logging.getLogger(__name__)


class ProviderNotRegisteredError(ExtractionError):
    """Raised when attempting to create a service for an unregistered provider type."""

    def __init__(self, provider_type: ExtractionProviderType | str):
        # An unregistered type may never have been an enum member in the first
        # place -- callers can hand us a raw string from config or the DB.
        name = getattr(provider_type, "value", provider_type)
        super().__init__(
            f"No creator registered for provider type: {name}",
            provider=name,
        )
        self.provider_type = provider_type


class ProviderConfigError(ExtractionError):
    """Raised when provider configuration is invalid."""

    def __init__(self, message: str, provider_type: str | None = None):
        super().__init__(message, provider=provider_type)


class ExtractionServiceCreator(ABC):
    """Abstract base class for extraction service creators.

    Each provider type should have a creator that knows how to instantiate
    the appropriate service class with the correct configuration.
    """

    @abstractmethod
    def create(
        self,
        provider: ExtractionProvider,
        config: dict,
        tenant_id: UUID,
    ) -> BaseExtractionService:
        """Create an extraction service instance.

        Args:
            provider: ExtractionProvider model with configuration
            config: Decrypted configuration dictionary
            tenant_id: Tenant ID for context

        Returns:
            Configured extraction service instance

        Raises:
            ProviderConfigError: If configuration is invalid
        """
        pass

    @abstractmethod
    def validate_config(self, config: dict) -> list[str]:
        """Validate provider configuration.

        Args:
            config: Configuration dictionary to validate

        Returns:
            List of validation error messages (empty if valid)
        """
        pass


class OllamaServiceCreator(ExtractionServiceCreator):
    """Creator for Ollama extraction services."""

    def create(
        self,
        provider: ExtractionProvider,
        config: dict,
        tenant_id: UUID,
    ) -> BaseExtractionService:
        from kg_builder.extraction.ollama_extractor import OllamaExtractionService

        base_url = config.get("base_url")
        model = provider.default_model or config.get("model")

        logger.info(
            "Creating Ollama extraction service",
            extra={
                "provider_id": str(provider.id),
                "base_url": base_url,
                "model": model,
            },
        )

        return OllamaExtractionService(
            base_url=base_url,
            model=model,
            timeout=provider.timeout_seconds,
        )

    def validate_config(self, config: dict) -> list[str]:
        # Ollama can use defaults from settings
        return []


class OpenAIServiceCreator(ExtractionServiceCreator):
    """Creator for OpenAI extraction services."""

    def create(
        self,
        provider: ExtractionProvider,
        config: dict,
        tenant_id: UUID,
    ) -> BaseExtractionService:
        from kg_builder.extraction.openai_extractor import OpenAIExtractionService

        api_key = config.get("api_key")
        if not api_key:
            raise ProviderConfigError(
                "OpenAI provider requires api_key in config",
                provider_type="openai",
            )

        model = provider.default_model or config.get("model") or "gpt-4o"
        temperature = config.get("temperature", 0.1)

        logger.info(
            "Creating OpenAI extraction service",
            extra={
                "provider_id": str(provider.id),
                "model": model,
            },
        )

        return OpenAIExtractionService(
            api_key=api_key,
            model=model,
            timeout=provider.timeout_seconds,
            max_context_length=provider.max_context_length,
            temperature=temperature,
        )

    def validate_config(self, config: dict) -> list[str]:
        errors = []
        if not config.get("api_key"):
            errors.append("api_key is required for OpenAI provider")
        return errors


class AnthropicServiceCreator(ExtractionServiceCreator):
    """Creator for Anthropic extraction services (placeholder)."""

    def create(
        self,
        provider: ExtractionProvider,
        config: dict,
        tenant_id: UUID,
    ) -> BaseExtractionService:
        raise NotImplementedError(
            "Anthropic extraction provider is not yet fully integrated. "
            "Use Ollama or OpenAI providers."
        )

    def validate_config(self, config: dict) -> list[str]:
        errors = []
        if not config.get("api_key"):
            errors.append("api_key is required for Anthropic provider")
        return errors


class ExtractionProviderRegistry:
    """Registry for extraction service creators.

    This registry implements the Open/Closed Principle by allowing new
    provider types to be registered without modifying the factory code.

    Example:
        # Register a custom provider
        @extraction_provider_registry.register(ExtractionProviderType.CUSTOM)
        class CustomServiceCreator(ExtractionServiceCreator):
            def create(self, provider, config, tenant_id):
                return CustomExtractionService(...)

            def validate_config(self, config):
                return []

        # Or register programmatically
        extraction_provider_registry.register_creator(
            ExtractionProviderType.CUSTOM,
            CustomServiceCreator()
        )
    """

    def __init__(self):
        self._creators: dict[ExtractionProviderType, ExtractionServiceCreator] = {}

    def register(
        self, provider_type: ExtractionProviderType
    ) -> Callable[[type[ExtractionServiceCreator]], type[ExtractionServiceCreator]]:
        """Decorator to register a service creator for a provider type.

        Args:
            provider_type: The provider type to register

        Returns:
            Decorator function that registers the creator class

        Example:
            @registry.register(ExtractionProviderType.OPENAI)
            class OpenAIServiceCreator(ExtractionServiceCreator):
                ...
        """

        def decorator(
            creator_cls: type[ExtractionServiceCreator],
        ) -> type[ExtractionServiceCreator]:
            self._creators[provider_type] = creator_cls()
            logger.debug(f"Registered extraction service creator for {provider_type.value}")
            return creator_cls

        return decorator

    def register_creator(
        self,
        provider_type: ExtractionProviderType,
        creator: ExtractionServiceCreator,
    ) -> None:
        """Programmatically register a service creator.

        Args:
            provider_type: The provider type to register
            creator: The creator instance to register
        """
        self._creators[provider_type] = creator
        logger.debug(f"Registered extraction service creator for {provider_type.value}")

    def get_creator(self, provider_type: ExtractionProviderType) -> ExtractionServiceCreator:
        """Get the creator for a provider type.

        Args:
            provider_type: The provider type to get creator for

        Returns:
            The registered creator instance

        Raises:
            ProviderNotRegisteredError: If no creator is registered
        """
        creator = self._creators.get(provider_type)
        if creator is None:
            raise ProviderNotRegisteredError(provider_type)
        return creator

    def create_service(
        self,
        provider: ExtractionProvider,
        tenant_id: UUID,
    ) -> BaseExtractionService:
        """Create an extraction service from a provider configuration.

        Handles API key decryption and delegates to the registered creator.

        Args:
            provider: ExtractionProvider model instance
            tenant_id: Tenant ID for key decryption and context

        Returns:
            Configured extraction service instance

        Raises:
            ProviderNotRegisteredError: If provider type is not registered
            ProviderConfigError: If configuration is invalid
        """
        config = self._decrypt_config(provider, tenant_id)
        creator = self.get_creator(provider.provider_type)
        return creator.create(provider, config, tenant_id)

    def validate_config(
        self,
        provider_type: ExtractionProviderType,
        config: dict,
    ) -> list[str]:
        """Validate provider configuration.

        Args:
            provider_type: Type of provider
            config: Configuration dict to validate

        Returns:
            List of validation error messages (empty if valid)

        Raises:
            ProviderNotRegisteredError: If provider type is not registered
        """
        creator = self.get_creator(provider_type)
        return creator.validate_config(config)

    def get_supported_provider_types(self) -> list[str]:
        """Return list of registered provider types.

        Returns:
            List of provider type strings
        """
        return [t.value for t in self._creators]

    def is_registered(self, provider_type: ExtractionProviderType) -> bool:
        """Check if a provider type is registered.

        Args:
            provider_type: The provider type to check

        Returns:
            True if registered, False otherwise
        """
        return provider_type in self._creators

    def _decrypt_config(
        self,
        provider: ExtractionProvider,
        tenant_id: UUID,
    ) -> dict:
        """Decrypt sensitive fields in provider configuration.

        Args:
            provider: ExtractionProvider model
            tenant_id: Tenant ID for decryption context

        Returns:
            Configuration dict with decrypted fields
        """
        config = provider.config.copy() if provider.config else {}

        # Decrypt API key if present and encrypted
        if config.get("api_key"):
            from kg_builder.encryption import get_encryption_service

            encryption = get_encryption_service()
            api_key = config["api_key"]

            if encryption.is_encrypted(api_key):
                try:
                    config["api_key"] = encryption.decrypt(
                        api_key,
                        tenant_id,
                        field_name="api_key",
                    )
                except Exception as e:
                    logger.error(
                        "Failed to decrypt API key",
                        extra={
                            "provider_id": str(provider.id),
                            "provider_type": provider.provider_type.value,
                            "error": str(e),
                        },
                    )
                    raise ProviderConfigError(
                        f"Failed to decrypt API key: {e}",
                        provider_type=provider.provider_type.value,
                    ) from e

        return config


# Global registry instance
extraction_provider_registry = ExtractionProviderRegistry()

# Register built-in providers
extraction_provider_registry.register_creator(ExtractionProviderType.OLLAMA, OllamaServiceCreator())
extraction_provider_registry.register_creator(ExtractionProviderType.OPENAI, OpenAIServiceCreator())
extraction_provider_registry.register_creator(
    ExtractionProviderType.ANTHROPIC, AnthropicServiceCreator()
)


def get_extraction_provider_registry() -> ExtractionProviderRegistry:
    """Get the global extraction provider registry.

    Returns:
        The global ExtractionProviderRegistry instance
    """
    return extraction_provider_registry
