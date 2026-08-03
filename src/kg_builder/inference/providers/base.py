"""
Abstract base class and data models for inference providers.

This module defines the contract that all inference providers must implement,
following SOLID principles for extensibility and maintainability.

Architecture:
- InferenceProvider: Abstract base class defining provider interface
- InferenceRequest: Input model for inference requests
- InferenceResponse: Output model for completed inference
- InferenceChunk: Streaming chunk model for real-time responses
- ProviderHealth: Health check status model

Usage:
    class OllamaProvider(InferenceProvider):
        async def infer(self, request: InferenceRequest) -> InferenceResponse:
            ...
"""

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import AsyncIterator, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# =============================================================================
# Enums
# =============================================================================


class ProviderType(str, Enum):
    """Supported inference provider types."""

    OLLAMA = "ollama"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GROQ = "groq"


class ProviderStatus(str, Enum):
    """Provider health status values."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


# =============================================================================
# Request/Response Models
# =============================================================================


class InferenceRequest(BaseModel):
    """Request model for inference execution.

    Attributes:
        prompt: The input text to send to the model
        model: Model identifier to use (e.g., "gemma3:12b")
        temperature: Sampling temperature (0.0 = deterministic, 2.0 = creative)
        max_tokens: Maximum tokens to generate
        stream: Whether to stream the response
        stop_sequences: Optional list of sequences to stop generation
        system_prompt: Optional system prompt to prepend
    """

    prompt: str = Field(
        min_length=1,
        max_length=100000,
        description="Input prompt text",
    )
    model: str = Field(
        description="Model identifier",
    )
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Sampling temperature",
    )
    max_tokens: int = Field(
        default=1024,
        ge=1,
        le=100000,
        description="Maximum tokens to generate",
    )
    stream: bool = Field(
        default=False,
        description="Enable streaming response",
    )
    stop_sequences: list[str] = Field(
        default_factory=list,
        description="Stop generation at these sequences",
    )
    system_prompt: Optional[str] = Field(
        default=None,
        description="System prompt to prepend",
    )


class InferenceResponse(BaseModel):
    """Response model for completed inference.

    Attributes:
        content: The generated text response
        model: Model that generated the response
        prompt_tokens: Number of tokens in the prompt
        completion_tokens: Number of tokens generated
        total_tokens: Total tokens used
        duration_ms: Time taken in milliseconds
        finish_reason: Why generation stopped (stop, length, etc.)
    """

    content: str = Field(
        description="Generated response text",
    )
    model: str = Field(
        description="Model that generated the response",
    )
    prompt_tokens: int = Field(
        ge=0,
        description="Tokens in the input prompt",
    )
    completion_tokens: int = Field(
        ge=0,
        description="Tokens in the generated response",
    )
    total_tokens: int = Field(
        ge=0,
        description="Total tokens used",
    )
    duration_ms: int = Field(
        ge=0,
        description="Generation time in milliseconds",
    )
    finish_reason: str = Field(
        default="stop",
        description="Reason generation finished",
    )


class InferenceChunk(BaseModel):
    """Streaming chunk model for real-time response delivery.

    Attributes:
        content: The text content of this chunk
        done: Whether this is the final chunk
        error: Error message if chunk indicates an error
        token_count: Number of tokens in this chunk (if available)
    """

    content: str = Field(
        default="",
        description="Chunk content",
    )
    done: bool = Field(
        default=False,
        description="Is this the final chunk",
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message if failed",
    )
    token_count: Optional[int] = Field(
        default=None,
        description="Tokens in this chunk",
    )


class ProviderHealth(BaseModel):
    """Provider health check status.

    Attributes:
        status: Current health status
        latency_ms: Response latency in milliseconds
        available_models: List of available models (if retrievable)
        error: Error message if unhealthy
        checked_at: When the health check was performed
    """

    status: ProviderStatus = Field(
        description="Health status",
    )
    latency_ms: Optional[float] = Field(
        default=None,
        description="Response latency",
    )
    available_models: Optional[list[str]] = Field(
        default=None,
        description="Available models",
    )
    error: Optional[str] = Field(
        default=None,
        description="Error details if unhealthy",
    )
    checked_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Health check timestamp",
    )


class ModelInfo(BaseModel):
    """Information about an available model.

    Attributes:
        name: Model name/identifier
        display_name: Human-readable name
        size_bytes: Model size in bytes (if available)
        parameter_count: Number of parameters (if available)
        capabilities: Model capabilities (chat, completion, embedding)
    """

    name: str = Field(
        description="Model identifier",
    )
    display_name: Optional[str] = Field(
        default=None,
        description="Human-readable name",
    )
    size_bytes: Optional[int] = Field(
        default=None,
        description="Model file size",
    )
    parameter_count: Optional[str] = Field(
        default=None,
        description="Parameter count (e.g., '12B')",
    )
    capabilities: list[str] = Field(
        default_factory=list,
        description="Model capabilities",
    )


# =============================================================================
# Provider Exceptions
# =============================================================================


class ProviderError(Exception):
    """Base exception for provider errors."""

    def __init__(self, message: str, provider_type: Optional[str] = None):
        self.message = message
        self.provider_type = provider_type
        super().__init__(message)


class ProviderConnectionError(ProviderError):
    """Raised when connection to provider fails."""

    pass


class ProviderTimeoutError(ProviderError):
    """Raised when provider request times out."""

    pass


class ProviderRateLimitError(ProviderError):
    """Raised when provider rate limit is exceeded."""

    def __init__(
        self,
        message: str,
        retry_after: Optional[float] = None,
        provider_type: Optional[str] = None,
    ):
        super().__init__(message, provider_type)
        self.retry_after = retry_after


class ProviderInvalidRequestError(ProviderError):
    """Raised when request is invalid for the provider."""

    pass


class ProviderAuthenticationError(ProviderError):
    """Raised when provider authentication fails."""

    pass


# =============================================================================
# Abstract Provider Interface
# =============================================================================


class InferenceProvider(ABC):
    """Abstract base class for inference providers.

    This interface defines the contract that all inference provider
    implementations must follow. It uses Interface Segregation Principle
    by defining focused methods for distinct operations.

    Implementations should be stateless where possible and thread-safe.
    Configuration should be passed at construction time.

    Example:
        ```python
        class OllamaProvider(InferenceProvider):
            def __init__(self, base_url: str, default_model: str):
                self._base_url = base_url
                self._default_model = default_model

            async def infer(self, request: InferenceRequest) -> InferenceResponse:
                # Implementation
                ...
        ```

    Attributes:
        provider_type: The type of this provider (from ProviderType enum)
    """

    @property
    @abstractmethod
    def provider_type(self) -> ProviderType:
        """Return the type of this provider."""
        pass

    @property
    @abstractmethod
    def supports_streaming(self) -> bool:
        """Return whether this provider supports streaming responses."""
        pass

    @abstractmethod
    async def infer(self, request: InferenceRequest) -> InferenceResponse:
        """Execute a synchronous inference request.

        This method sends a prompt to the provider and waits for the
        complete response before returning.

        Args:
            request: The inference request parameters

        Returns:
            Complete inference response with content and metadata

        Raises:
            ProviderConnectionError: If connection to provider fails
            ProviderTimeoutError: If request times out
            ProviderRateLimitError: If rate limit exceeded
            ProviderInvalidRequestError: If request is invalid
            ProviderAuthenticationError: If authentication fails
        """
        pass

    @abstractmethod
    async def infer_stream(
        self,
        request: InferenceRequest,
    ) -> AsyncIterator[InferenceChunk]:
        """Execute a streaming inference request.

        This method yields chunks as they are generated by the provider,
        enabling real-time response display.

        Args:
            request: The inference request parameters (stream flag ignored)

        Yields:
            InferenceChunk objects as they are generated

        Raises:
            ProviderConnectionError: If connection to provider fails
            ProviderTimeoutError: If request times out
            ProviderRateLimitError: If rate limit exceeded
            ProviderInvalidRequestError: If request is invalid
            ProviderAuthenticationError: If authentication fails

        Note:
            The final chunk will have done=True.
            If an error occurs during streaming, a chunk with error set
            will be yielded before the iterator completes.
        """
        pass

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """List available models from this provider.

        Returns:
            List of ModelInfo objects describing available models

        Raises:
            ProviderConnectionError: If connection fails
        """
        pass

    @abstractmethod
    async def health_check(self) -> ProviderHealth:
        """Check provider connectivity and health.

        This method should be quick and non-blocking. It typically
        makes a lightweight request to verify the provider is reachable.

        Returns:
            ProviderHealth object with status and details

        Note:
            This method should not raise exceptions. Failures should
            be indicated via the ProviderHealth.status field.
        """
        pass

    async def validate_model(self, model: str) -> bool:
        """Check if a model is available on this provider.

        Default implementation lists all models and checks membership.
        Providers may override for more efficient validation.

        Args:
            model: Model identifier to validate

        Returns:
            True if model is available, False otherwise
        """
        try:
            models = await self.list_models()
            return any(m.name == model for m in models)
        except ProviderError:
            return False

    async def close(self) -> None:
        """Clean up provider resources.

        Called when provider is no longer needed. Implementations
        should close HTTP connections, etc.

        Default implementation does nothing.
        """
        pass

    async def __aenter__(self) -> "InferenceProvider":
        """Context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - calls close()."""
        await self.close()
