"""
Provider factory for creating inference provider instances.

This module implements the Factory pattern with a registry for
dynamically creating provider instances based on type and configuration.

The factory follows the Open/Closed Principle - it's open for extension
(new providers can register) but closed for modification (adding a
provider doesn't require changing factory code).

Usage:
    # Register a provider (usually via decorator)
    @ProviderFactory.register(ProviderType.OLLAMA)
    class OllamaProvider(InferenceProvider):
        ...

    # Create provider instance
    provider = ProviderFactory.create(
        ProviderType.OLLAMA,
        config={"base_url": "http://localhost:11434"}
    )
"""

import logging
from collections.abc import Callable
from typing import Any

from kg_builder.inference.providers.base import (
    InferenceProvider,
    ProviderError,
    ProviderType,
)

logger = logging.getLogger(__name__)


class ProviderNotRegisteredError(ProviderError):
    """Raised when attempting to create an unregistered provider type."""

    pass


class ProviderConfigurationError(ProviderError):
    """Raised when provider configuration is invalid."""

    pass


class ProviderFactory:
    """Factory for creating inference provider instances.

    This factory maintains a registry of provider types and their
    implementation classes. Providers register themselves using the
    `@ProviderFactory.register(ProviderType.XXX)` decorator.

    The factory creates provider instances from configuration dictionaries,
    which typically come from database records (tenant-scoped provider configs).

    Class Attributes:
        _registry: Maps ProviderType to implementation class

    Example:
        ```python
        # Register during class definition
        @ProviderFactory.register(ProviderType.OLLAMA)
        class OllamaProvider(InferenceProvider):
            def __init__(self, base_url: str, default_model: str = "gemma3:12b"):
                ...

        # Create instance
        provider = ProviderFactory.create(
            ProviderType.OLLAMA,
            {"base_url": "http://localhost:11434", "default_model": "llama2:7b"}
        )
        ```
    """

    _registry: dict[ProviderType, type[InferenceProvider]] = {}

    @classmethod
    def register(
        cls,
        provider_type: ProviderType,
    ) -> Callable[[type[InferenceProvider]], type[InferenceProvider]]:
        """Decorator to register a provider implementation.

        Args:
            provider_type: The ProviderType this class implements

        Returns:
            Decorator function that registers the class

        Example:
            ```python
            @ProviderFactory.register(ProviderType.OLLAMA)
            class OllamaProvider(InferenceProvider):
                ...
            ```
        """

        def decorator(
            provider_class: type[InferenceProvider],
        ) -> type[InferenceProvider]:
            if provider_type in cls._registry:
                logger.warning(
                    "Overwriting existing provider registration",
                    extra={
                        "provider_type": provider_type.value,
                        "existing_class": cls._registry[provider_type].__name__,
                        "new_class": provider_class.__name__,
                    },
                )

            cls._registry[provider_type] = provider_class
            logger.debug(
                "Registered provider",
                extra={
                    "provider_type": provider_type.value,
                    "class": provider_class.__name__,
                },
            )
            return provider_class

        return decorator

    @classmethod
    def create(
        cls,
        provider_type: ProviderType,
        config: dict[str, Any],
    ) -> InferenceProvider:
        """Create a provider instance from configuration.

        Args:
            provider_type: Type of provider to create
            config: Configuration dictionary passed to provider constructor

        Returns:
            Configured InferenceProvider instance

        Raises:
            ProviderNotRegisteredError: If provider_type is not registered
            ProviderConfigurationError: If config is invalid for provider

        Example:
            ```python
            provider = ProviderFactory.create(
                ProviderType.OLLAMA,
                {
                    "base_url": "http://localhost:11434",
                    "default_model": "gemma3:12b",
                    "timeout": 60,
                }
            )
            ```
        """
        if provider_type not in cls._registry:
            available = ", ".join(p.value for p in cls._registry.keys())
            raise ProviderNotRegisteredError(
                f"Provider type '{provider_type.value}' is not registered. "
                f"Available types: {available or 'none'}",
                provider_type=provider_type.value,
            )

        provider_class = cls._registry[provider_type]

        try:
            provider = provider_class(**config)
            logger.info(
                "Created provider instance",
                extra={
                    "provider_type": provider_type.value,
                    "class": provider_class.__name__,
                },
            )
            return provider

        except TypeError as e:
            raise ProviderConfigurationError(
                f"Invalid configuration for {provider_type.value} provider: {e}",
                provider_type=provider_type.value,
            ) from e

        except Exception as e:
            raise ProviderConfigurationError(
                f"Failed to create {provider_type.value} provider: {e}",
                provider_type=provider_type.value,
            ) from e

    @classmethod
    def get_registered_types(cls) -> list[ProviderType]:
        """Get list of registered provider types.

        Returns:
            List of ProviderType values that are registered
        """
        return list(cls._registry.keys())

    @classmethod
    def is_registered(cls, provider_type: ProviderType) -> bool:
        """Check if a provider type is registered.

        Args:
            provider_type: Type to check

        Returns:
            True if registered, False otherwise
        """
        return provider_type in cls._registry

    @classmethod
    def get_provider_class(
        cls,
        provider_type: ProviderType,
    ) -> type[InferenceProvider]:
        """Get the class for a registered provider type.

        Args:
            provider_type: Type to look up

        Returns:
            The provider implementation class

        Raises:
            ProviderNotRegisteredError: If type is not registered
        """
        if provider_type not in cls._registry:
            raise ProviderNotRegisteredError(
                f"Provider type '{provider_type.value}' is not registered",
                provider_type=provider_type.value,
            )
        return cls._registry[provider_type]

    @classmethod
    def clear_registry(cls) -> None:
        """Clear all registered providers.

        Primarily used for testing.
        """
        cls._registry.clear()
        logger.debug("Cleared provider registry")


# =============================================================================
# Provider Registration Helpers
# =============================================================================


def get_provider_for_config(
    provider_type: str,
    config: dict[str, Any],
) -> InferenceProvider:
    """Helper to create provider from type string and config.

    Convenience function that converts string type to ProviderType enum.

    Args:
        provider_type: String provider type (e.g., "ollama")
        config: Provider configuration dictionary

    Returns:
        Configured InferenceProvider instance

    Raises:
        ValueError: If provider_type string is invalid
        ProviderNotRegisteredError: If type is not registered
        ProviderConfigurationError: If config is invalid
    """
    try:
        ptype = ProviderType(provider_type.lower())
    except ValueError as e:
        valid_types = ", ".join(t.value for t in ProviderType)
        raise ValueError(
            f"Invalid provider type '{provider_type}'. " f"Valid types: {valid_types}"
        ) from e

    return ProviderFactory.create(ptype, config)
