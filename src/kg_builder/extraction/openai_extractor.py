"""
OpenAI-based entity extraction service.

Uses OpenAI's Chat Completions API with structured outputs (JSON mode)
for entity and relationship extraction from documentation.
"""

import json
import logging
import time
from uuid import UUID

import httpx
from openai import APIConnectionError, APIError, AsyncOpenAI, RateLimitError
from pydantic import ValidationError

from kg_builder.extraction.base import BaseExtractionService, ExtractionError
from kg_builder.extraction.prompts import DocumentationType, build_user_prompt, get_system_prompt
from kg_builder.extraction.schemas import ExtractionResult

logger = logging.getLogger(__name__)


class OpenAIExtractionError(ExtractionError):
    """Raised when OpenAI extraction fails."""

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message, cause=cause, provider="openai")


class OpenAIExtractionService(BaseExtractionService):
    """Service for extracting entities using OpenAI API.

    Features:
    - Structured outputs via JSON mode
    - Configurable model (gpt-4o, gpt-4-turbo, etc.)
    - Rate limiting support
    - Tenant-specific API keys

    Example:
        service = OpenAIExtractionService(api_key="sk-...")
        result = await service.extract(
            content="class DomainEvent(BaseModel): ...",
            page_url="https://docs.example.com/events",
        )
        for entity in result.entities:
            print(f"{entity.name}: {entity.entity_type}")
    """

    provider_name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        timeout: int = 300,
        max_context_length: int = 8000,
        temperature: float = 0.1,
        doc_type: DocumentationType = DocumentationType.GENERAL,
    ):
        """Initialize the OpenAI extraction service.

        Args:
            api_key: OpenAI API key
            model: Model name (e.g., gpt-4o, gpt-4-turbo, gpt-3.5-turbo)
            timeout: Request timeout in seconds
            max_context_length: Maximum content length
            temperature: Sampling temperature (lower = more deterministic)
            doc_type: Default documentation type for prompt selection
        """
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._max_context_length = max_context_length
        self._temperature = temperature
        self._default_doc_type = doc_type

        # Initialize async client
        self._client = AsyncOpenAI(
            api_key=api_key,
            timeout=httpx.Timeout(timeout, connect=30.0),
        )

        logger.info(
            "OpenAIExtractionService initialized",
            extra={
                "model": model,
                "timeout": timeout,
                "max_context_length": max_context_length,
            },
        )

    async def extract(
        self,
        content: str,
        page_url: str,
        max_length: int | None = None,
        doc_type: DocumentationType | None = None,
        additional_context: str | None = None,
        tenant_id: UUID | None = None,
    ) -> ExtractionResult:
        """Extract entities and relationships using OpenAI.

        Args:
            content: The text content to analyze
            page_url: URL of the source page
            max_length: Maximum content length (defaults to max_context_length)
            doc_type: Documentation type for prompt optimization
            additional_context: Additional context for extraction
            tenant_id: Tenant ID for rate limiting

        Returns:
            ExtractionResult with entities and relationships

        Raises:
            OpenAIExtractionError: If extraction fails
        """
        # Rate limiting
        if tenant_id is not None:
            from kg_builder.extraction.rate_limiter import get_rate_limiter

            rate_limiter = get_rate_limiter()
            await rate_limiter.acquire(tenant_id)

        max_len = max_length or self._max_context_length
        effective_doc_type = doc_type or self._default_doc_type

        # Truncate if needed
        truncated = False
        if len(content) > max_len:
            content = content[:max_len]
            truncated = True
            logger.debug(
                "Content truncated for extraction",
                extra={"max_length": max_len, "page_url": page_url},
            )

        # Build prompts
        system_prompt = get_system_prompt(effective_doc_type)
        user_prompt = build_user_prompt(
            content=content,
            page_url=page_url,
            doc_type=effective_doc_type,
            additional_context=additional_context,
        )

        # Add JSON output instruction to system prompt
        json_instruction = """

IMPORTANT: You MUST respond with valid JSON that conforms to this schema:
{
  "entities": [
    {
      "name": "string (canonical name)",
      "entity_type": "string (one of: person, organization, location, event, product, concept, document, date, custom, function, class, module, pattern, example, parameter, return_type, exception)",
      "description": "string or null",
      "properties": {"key": "value"},
      "confidence": 0.0-1.0,
      "source_text": "string or null",
      "aliases": ["string"]
    }
  ],
  "relationships": [
    {
      "source_name": "string (must match an entity name)",
      "target_name": "string (must match an entity name)",
      "relationship_type": "string (one of: uses, implements, extends, inherits_from, contains, part_of, calls, returns, accepts, raises, depends_on, imports, requires, documented_in, example_of, demonstrates, related_to, references, defines, instantiates)",
      "confidence": 0.0-1.0,
      "context": "string or null",
      "properties": {}
    }
  ],
  "extraction_notes": "string or null"
}"""

        full_system_prompt = system_prompt + json_instruction

        try:
            logger.info(
                "Starting OpenAI extraction",
                extra={
                    "page_url": page_url,
                    "content_length": len(content),
                    "truncated": truncated,
                    "doc_type": effective_doc_type.value,
                    "model": self._model,
                },
            )

            start_time = time.time()

            # Build request kwargs - some models (gpt-5*, o-series) don't support temperature
            request_kwargs = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": full_system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
            }

            # Only add temperature for models that support it
            # gpt-5-*, o1-*, o3-*, o4-* models don't support custom temperature
            model_lower = self._model.lower()
            supports_temperature = not any(
                model_lower.startswith(prefix) for prefix in ("gpt-5", "o1", "o3", "o4")
            )
            if supports_temperature:
                request_kwargs["temperature"] = self._temperature

            # Use structured outputs (JSON mode)
            response = await self._client.chat.completions.create(**request_kwargs)

            elapsed_seconds = time.time() - start_time

            # Parse response
            json_content = response.choices[0].message.content
            if not json_content:
                raise OpenAIExtractionError("Empty response from OpenAI")

            # Parse JSON and validate with Pydantic
            try:
                json_data = json.loads(json_content)
                result = ExtractionResult.model_validate(json_data)
            except json.JSONDecodeError as e:
                logger.error(
                    "OpenAI response JSON parse failed",
                    extra={
                        "page_url": page_url,
                        "error": str(e),
                        "response_preview": json_content[:500] if json_content else None,
                    },
                )
                raise OpenAIExtractionError(f"Invalid JSON response: {e}", cause=e)

            logger.info(
                "OpenAI extraction completed successfully",
                extra={
                    "page_url": page_url,
                    "entity_count": result.entity_count,
                    "relationship_count": result.relationship_count,
                    "truncated": truncated,
                    "doc_type": effective_doc_type.value,
                    "elapsed_seconds": round(elapsed_seconds, 2),
                    "model": self._model,
                    "tokens": response.usage.total_tokens if response.usage else None,
                },
            )

            return result

        except ValidationError as e:
            preview = json_content[:3000] if json_content else "None"
            logger.error(
                f"OpenAI response validation failed for {page_url}:\n"
                f"Validation Error: {e}\n"
                f"Response Preview: {preview}"
            )
            raise OpenAIExtractionError(f"Invalid response format: {e}", cause=e)

        except RateLimitError as e:
            logger.warning(
                "OpenAI rate limit exceeded",
                extra={"page_url": page_url, "error": str(e)},
            )
            raise OpenAIExtractionError(f"Rate limit exceeded: {e}", cause=e)

        except APIConnectionError as e:
            logger.error(
                "Failed to connect to OpenAI",
                extra={"error": str(e)},
            )
            raise OpenAIExtractionError(f"Connection failed: {e}", cause=e)

        except APIError as e:
            logger.error(
                "OpenAI API error",
                extra={
                    "page_url": page_url,
                    "error": str(e),
                    "status_code": getattr(e, "status_code", None),
                },
            )
            raise OpenAIExtractionError(f"API error: {e}", cause=e)

        except Exception as e:
            logger.error(
                "OpenAI extraction failed",
                extra={
                    "page_url": page_url,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
            )
            raise OpenAIExtractionError(f"Extraction failed: {e}", cause=e)

    async def health_check(self) -> dict:
        """Check OpenAI API connectivity.

        Returns:
            dict with health status:
                - status: "healthy" or "unhealthy"
                - provider: "openai"
                - model: Configured model name
                - model_available: Whether model is accessible
                - error: Error message (if unhealthy)
        """
        try:
            # Simple models list call to verify API key
            models = await self._client.models.list()
            model_ids = [m.id for m in models.data]

            # Check if configured model is available
            model_available = self._model in model_ids

            return {
                "status": "healthy",
                "provider": "openai",
                "model": self._model,
                "model_available": model_available,
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "provider": "openai",
                "model": self._model,
                "error": str(e),
            }
