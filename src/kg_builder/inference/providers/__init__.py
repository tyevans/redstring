"""
Inference providers module.

Exports:
    - InferenceProvider: Abstract base class
    - OllamaProvider: Ollama implementation
    - ProviderFactory: Factory for creating providers
    - Data models: InferenceRequest, InferenceResponse, InferenceChunk, etc.
    - Exceptions: ProviderError and subclasses
    - Enums: ProviderType, ProviderStatus
"""

from kg_builder.inference.providers.base import (
    # Models
    InferenceChunk,
    # Interface
    InferenceProvider,
    InferenceRequest,
    InferenceResponse,
    ModelInfo,
    # Exceptions
    ProviderAuthenticationError,
    ProviderConnectionError,
    ProviderError,
    ProviderHealth,
    ProviderInvalidRequestError,
    ProviderRateLimitError,
    # Enums
    ProviderStatus,
    ProviderTimeoutError,
    ProviderType,
)
from kg_builder.inference.providers.factory import (
    ProviderConfigurationError,
    ProviderFactory,
    ProviderNotRegisteredError,
    get_provider_for_config,
)
from kg_builder.inference.providers.ollama import OllamaProvider

__all__ = [
    # Enums
    "ProviderStatus",
    "ProviderType",
    # Models
    "InferenceChunk",
    "InferenceRequest",
    "InferenceResponse",
    "ModelInfo",
    "ProviderHealth",
    # Exceptions
    "ProviderAuthenticationError",
    "ProviderConfigurationError",
    "ProviderConnectionError",
    "ProviderError",
    "ProviderInvalidRequestError",
    "ProviderNotRegisteredError",
    "ProviderRateLimitError",
    "ProviderTimeoutError",
    # Factory
    "ProviderFactory",
    "get_provider_for_config",
    # Interface
    "InferenceProvider",
    # Implementations
    "OllamaProvider",
]
