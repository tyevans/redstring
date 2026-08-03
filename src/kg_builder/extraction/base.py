"""
Base interface for extraction services.

All extraction providers must implement this interface to ensure
consistent behavior across Ollama, OpenAI, Anthropic, etc.

Updated for Adaptive Extraction Strategy (P3-001) to support:
- Custom system prompts from domain schemas via `system_prompt` parameter
- Structured JSON output schemas via `json_schema` parameter
- Backward compatibility: existing callers work without changes

When `system_prompt` is provided, extractors should use it instead of
the default prompt based on `doc_type`. When `json_schema` is provided,
extractors may use it to validate or guide structured output.
"""

from abc import ABC, abstractmethod
from typing import Any, Protocol
from uuid import UUID

from kg_builder.extraction.schemas import ExtractionResult
from kg_builder.extraction.prompts import DocumentationType


class ExtractionServiceProtocol(Protocol):
    """Protocol defining extraction service interface.

    This protocol allows duck-typing for extraction services without
    requiring inheritance from a specific base class.

    Updated for Adaptive Extraction Strategy to support custom prompts
    and JSON schemas from domain configurations.
    """

    async def extract(
        self,
        content: str,
        page_url: str,
        max_length: int | None = None,
        doc_type: DocumentationType | None = None,
        additional_context: str | None = None,
        tenant_id: UUID | None = None,
        # Adaptive extraction parameters (P3-001)
        system_prompt: str | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> ExtractionResult:
        """Extract entities and relationships from content.

        Args:
            content: The text content to analyze.
            page_url: URL of the source page (for context).
            max_length: Maximum content length (truncate if exceeded).
            doc_type: Type of documentation for prompt optimization.
                     Ignored when system_prompt is provided.
            additional_context: Additional context to guide extraction.
            tenant_id: Tenant ID for rate limiting.
            system_prompt: Custom system prompt from domain schema.
                          If provided, overrides doc_type-based prompt.
                          This enables domain-specific extraction.
            json_schema: JSON schema for structured LLM output.
                        If provided, extractors may use it to validate
                        or guide the structure of the response.

        Returns:
            ExtractionResult with entities and relationships.

        Note:
            When system_prompt is provided (adaptive extraction mode),
            the extractor should use it instead of the default prompt
            based on doc_type. This allows domain-specific prompts to
            guide extraction toward domain-relevant entities.
        """
        ...

    async def health_check(self) -> dict:
        """Check provider connectivity and availability."""
        ...


class BaseExtractionService(ABC):
    """Abstract base class for extraction services.

    All extraction provider implementations should inherit from this class
    to ensure consistent interface and behavior.

    Updated for Adaptive Extraction Strategy (P3-001) to support:
    - Custom system prompts from domain schemas
    - Structured JSON output schemas
    - Backward compatibility with legacy extraction

    Attributes:
        provider_name: Human-readable name of the provider (e.g., "ollama", "openai")
    """

    provider_name: str = "base"

    @abstractmethod
    async def extract(
        self,
        content: str,
        page_url: str,
        max_length: int | None = None,
        doc_type: DocumentationType | None = None,
        additional_context: str | None = None,
        tenant_id: UUID | None = None,
        # Adaptive extraction parameters (P3-001)
        system_prompt: str | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> ExtractionResult:
        """Extract entities and relationships from content.

        Args:
            content: The text content to analyze.
            page_url: URL of the source page (for context).
            max_length: Maximum content length (truncate if exceeded).
            doc_type: Type of documentation for prompt optimization.
                     This is ignored when system_prompt is provided.
            additional_context: Additional context to guide extraction.
            tenant_id: Tenant ID for rate limiting.
            system_prompt: Custom system prompt from domain schema.
                          If provided, overrides the doc_type-based prompt.
                          This enables domain-specific extraction where prompts
                          are tailored to the content domain (e.g., literature,
                          news, technical docs).
            json_schema: JSON schema for structured LLM output.
                        If provided, extractors may use this to:
                        1. Constrain LLM output structure (if supported)
                        2. Validate response format post-extraction
                        3. Guide entity/relationship type extraction

        Returns:
            ExtractionResult with entities and relationships.

        Raises:
            ExtractionError: If extraction fails.

        Note:
            When system_prompt is provided (adaptive extraction mode),
            extractors should use it instead of the default prompt based
            on doc_type. The doc_type parameter is retained for backward
            compatibility with legacy extraction pipelines.

        Example:
            # Legacy extraction (no system_prompt)
            result = await service.extract(
                content="...",
                page_url="https://example.com",
                doc_type=DocumentationType.API,
            )

            # Adaptive extraction (with domain-specific prompt)
            result = await service.extract(
                content="...",
                page_url="https://example.com",
                system_prompt="Extract literary characters and themes...",
                json_schema={"entities": [...], "relationships": [...]},
            )
        """
        pass

    @abstractmethod
    async def health_check(self) -> dict:
        """Check provider connectivity and availability.

        Returns:
            dict with health status information:
                - status: "healthy" or "unhealthy"
                - provider: Provider name
                - model: Configured model
                - error: Error message (if unhealthy)
        """
        pass


class ExtractionError(Exception):
    """Base exception for extraction errors.

    Attributes:
        message: Human-readable error message
        cause: Optional underlying exception
        provider: Name of the provider that raised the error
    """

    def __init__(
        self,
        message: str,
        cause: Exception | None = None,
        provider: str | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.cause = cause
        self.provider = provider

    def __str__(self) -> str:
        if self.provider:
            return f"[{self.provider}] {self.message}"
        return self.message
