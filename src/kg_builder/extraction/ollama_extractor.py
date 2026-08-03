"""
Ollama-based entity extraction service.

Uses pydantic-ai with Ollama's OpenAI-compatible API for
structured entity and relationship extraction from documentation.
"""

import logging
import time
from uuid import UUID

import httpx
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models.openai import OpenAIModel

from kg_builder.config import settings
from kg_builder.extraction.prompts import DocumentationType, build_user_prompt, get_system_prompt
from kg_builder.extraction.schemas import ExtractionResult

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    """Raised when extraction fails.

    This exception wraps underlying errors from the Ollama API,
    pydantic-ai validation failures, or connection issues.

    Attributes:
        message: Human-readable error message
        cause: Optional underlying exception
    """

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.message = message
        self.cause = cause


class OllamaExtractionService:
    """Service for extracting entities and relationships using local Ollama.

    Uses pydantic-ai with Ollama's OpenAI-compatible endpoint for
    structured output extraction. The service is configured to:

    - Connect to local Ollama instance
    - Use specified model (gemma3:12b by default)
    - Produce validated Pydantic output
    - Handle extraction errors gracefully

    Example:
        service = OllamaExtractionService()
        result = await service.extract(
            content="class DomainEvent(BaseModel): ...",
            page_url="https://docs.example.com/events",
        )
        for entity in result.entities:
            print(f"{entity.name}: {entity.entity_type}")
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
        doc_type: DocumentationType = DocumentationType.GENERAL,
    ):
        """Initialize the Ollama extraction service.

        Args:
            base_url: Ollama server URL (defaults to settings.OLLAMA_BASE_URL)
            model: Model name (defaults to settings.OLLAMA_MODEL)
            timeout: Request timeout in seconds (defaults to settings.OLLAMA_TIMEOUT)
            doc_type: Default documentation type for prompt selection
        """
        self._base_url = base_url or settings.OLLAMA_BASE_URL
        self._model = model or settings.OLLAMA_MODEL
        self._timeout = timeout or settings.OLLAMA_TIMEOUT
        self._default_doc_type = doc_type

        # Create custom httpx client with logging event hooks
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout, connect=30.0),
            event_hooks={
                "response": [self._on_response],
            },
        )

        # Create pydantic-ai model with OpenAI-compatible API
        # Ollama exposes /v1/chat/completions at {base_url}/v1
        self._ollama_model = OpenAIModel(
            model_name=self._model,
            base_url=f"{self._base_url}/v1",
            # Ollama doesn't require API key but pydantic-ai needs a placeholder
            api_key="ollama",
            http_client=self._http_client,
        )

        # Create agent with structured output
        # Uses optimized prompts from the prompts module
        # result_retries allows pydantic-ai to retry if LLM output doesn't validate
        self._agent = Agent(
            model=self._ollama_model,
            result_type=ExtractionResult,
            system_prompt=self._get_system_prompt(),
            result_retries=3,  # Allow up to 3 validation retries
        )

        logger.info(
            "OllamaExtractionService initialized",
            extra={
                "base_url": self._base_url,
                "model": self._model,
                "timeout": self._timeout,
                "doc_type": self._default_doc_type.value,
            },
        )

    async def _on_response(self, response: httpx.Response) -> None:
        """Event hook to log HTTP responses for debugging.

        Logs warning for non-200 responses and info for slow responses (>30s).
        This helps diagnose Ollama connectivity issues and 500 errors.

        Note: We intentionally do not read the response body here as that would
        consume it and break downstream processing. The body will be logged
        when an HTTPStatusError is raised if needed.
        """
        # Calculate elapsed time if available
        elapsed_ms = response.elapsed.total_seconds() * 1000 if response.elapsed else 0

        if response.status_code >= 400:
            # Log error responses (4xx/5xx) - don't read body to avoid consuming it
            logger.warning(
                "Ollama HTTP error response",
                extra={
                    "status_code": response.status_code,
                    "url": str(response.url),
                    "elapsed_ms": round(elapsed_ms, 2),
                    "model": self._model,
                    "reason_phrase": response.reason_phrase,
                    "content_type": response.headers.get("content-type"),
                },
            )
        elif elapsed_ms > 30000:  # Log slow responses (>30s)
            logger.info(
                "Ollama slow response",
                extra={
                    "status_code": response.status_code,
                    "url": str(response.url),
                    "elapsed_ms": round(elapsed_ms, 2),
                    "model": self._model,
                },
            )

    def _get_system_prompt(self, doc_type: DocumentationType | None = None) -> str:
        """Get the system prompt for extraction.

        Uses optimized prompts from the prompts module based on documentation type.

        Args:
            doc_type: Documentation type for prompt selection. Uses the service's
                default doc_type if not specified.

        Returns:
            System prompt string for the extraction agent
        """
        effective_doc_type = doc_type or self._default_doc_type
        return get_system_prompt(effective_doc_type)

    def _build_prompt(
        self,
        content: str,
        page_url: str,
        doc_type: DocumentationType | None = None,
        additional_context: str | None = None,
    ) -> str:
        """Build the user prompt for extraction.

        Uses the build_user_prompt function from the prompts module.

        Args:
            content: The documentation content to analyze
            page_url: URL of the page for context
            doc_type: Optional documentation type hint
            additional_context: Optional additional context for extraction

        Returns:
            Formatted user prompt string
        """
        effective_doc_type = doc_type or self._default_doc_type
        return build_user_prompt(
            content=content,
            page_url=page_url,
            doc_type=effective_doc_type,
            additional_context=additional_context,
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
        """Extract entities and relationships from content.

        Args:
            content: Page content to analyze
            page_url: URL of the page (for context)
            max_length: Max content length (defaults to settings.OLLAMA_MAX_CONTEXT_LENGTH)
            doc_type: Documentation type for prompt optimization. Uses service default if not provided.
            additional_context: Optional additional context for extraction guidance.
            tenant_id: Optional tenant ID for rate limiting. If provided, rate limiting
                will be enforced before extraction.

        Returns:
            ExtractionResult with entities and relationships

        Raises:
            ExtractionError: If extraction fails due to connection, timeout, or validation errors
            RateLimitExceeded: If tenant_id is provided and rate limit is exceeded
        """
        # Check rate limit if tenant_id is provided
        if tenant_id is not None:
            from kg_builder.extraction.rate_limiter import get_rate_limiter

            rate_limiter = get_rate_limiter()
            await rate_limiter.acquire(tenant_id)

        max_len = max_length or settings.OLLAMA_MAX_CONTEXT_LENGTH
        effective_doc_type = doc_type or self._default_doc_type

        # Truncate content if needed
        truncated = False
        if len(content) > max_len:
            content = content[:max_len]
            truncated = True
            logger.debug(
                "Content truncated for extraction",
                extra={"max_length": max_len, "page_url": page_url},
            )

        # Build user prompt with doc type and context
        prompt = self._build_prompt(
            content=content,
            page_url=page_url,
            doc_type=effective_doc_type,
            additional_context=additional_context,
        )

        try:
            # Log extraction start with context
            logger.info(
                "Starting Ollama extraction",
                extra={
                    "page_url": page_url,
                    "content_length": len(content),
                    "prompt_length": len(prompt),
                    "truncated": truncated,
                    "doc_type": effective_doc_type.value,
                    "model": self._model,
                },
            )

            start_time = time.time()

            # Run extraction with pydantic-ai
            # pydantic-ai handles structured output validation
            result = await self._agent.run(prompt)

            elapsed_seconds = time.time() - start_time

            logger.info(
                "Extraction completed successfully",
                extra={
                    "page_url": page_url,
                    "entity_count": result.data.entity_count,
                    "relationship_count": result.data.relationship_count,
                    "truncated": truncated,
                    "doc_type": effective_doc_type.value,
                    "elapsed_seconds": round(elapsed_seconds, 2),
                    "content_length": len(content),
                },
            )

            return result.data

        except httpx.HTTPStatusError as e:
            # Log HTTP errors (4xx/5xx) with full context
            logger.error(
                "Ollama HTTP status error",
                extra={
                    "page_url": page_url,
                    "status_code": e.response.status_code,
                    "response_text": e.response.text[:500] if e.response.text else None,
                    "content_length": len(content),
                    "model": self._model,
                },
            )
            raise ExtractionError(
                f"Ollama returned HTTP {e.response.status_code}: {e.response.text[:200] if e.response.text else 'No details'}",
                cause=e,
            ) from e

        except httpx.ConnectError as e:
            logger.error(
                "Failed to connect to Ollama",
                extra={"base_url": self._base_url, "error": str(e)},
            )
            raise ExtractionError(
                f"Failed to connect to Ollama at {self._base_url}: {e}", cause=e
            ) from e

        except httpx.TimeoutException as e:
            logger.error(
                "Ollama request timed out",
                extra={
                    "page_url": page_url,
                    "timeout": self._timeout,
                    "error": str(e),
                },
            )
            raise ExtractionError(
                f"Ollama request timed out after {self._timeout}s: {e}", cause=e
            ) from e

        except UnexpectedModelBehavior as e:
            logger.error(
                "Ollama returned unexpected response",
                extra={"page_url": page_url, "error": str(e)},
            )
            raise ExtractionError(f"Ollama returned unexpected response: {e}", cause=e) from e

        except Exception as e:
            logger.error(
                "Extraction failed",
                extra={"page_url": page_url, "error": str(e), "error_type": type(e).__name__},
            )
            raise ExtractionError(f"Extraction failed: {e}", cause=e) from e

    async def health_check(self) -> dict:
        """Check Ollama connectivity and model availability.

        Returns:
            dict with health status information:
                - status: "healthy" or "unhealthy"
                - base_url: The Ollama server URL
                - model: The configured model name
                - available_models: List of models available on the server (if healthy)
                - model_available: Whether the configured model is available
                - error: Error message (if unhealthy)
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._base_url}/api/tags")
                if response.status_code == 200:
                    data = response.json()
                    models = data.get("models", [])
                    model_names = [m.get("name", "") for m in models]

                    # Check if configured model is available
                    # Ollama model names can include tags like "gemma3:12b"
                    model_available = any(
                        self._model in name or name in self._model for name in model_names
                    )

                    return {
                        "status": "healthy",
                        "base_url": self._base_url,
                        "model": self._model,
                        "available_models": model_names,
                        "model_available": model_available,
                    }
                else:
                    return {
                        "status": "unhealthy",
                        "base_url": self._base_url,
                        "model": self._model,
                        "error": f"HTTP {response.status_code}",
                    }
        except httpx.ConnectError as e:
            return {
                "status": "unhealthy",
                "base_url": self._base_url,
                "model": self._model,
                "error": f"Connection failed: {e}",
            }
        except httpx.TimeoutException as e:
            return {
                "status": "unhealthy",
                "base_url": self._base_url,
                "model": self._model,
                "error": f"Timeout: {e}",
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "base_url": self._base_url,
                "model": self._model,
                "error": str(e),
            }


# Factory function with singleton pattern
_service: OllamaExtractionService | None = None


def get_ollama_extraction_service() -> OllamaExtractionService:
    """Get the global Ollama extraction service instance.

    Creates a new instance on first call, then returns the same
    instance on subsequent calls.

    Returns:
        The global OllamaExtractionService instance
    """
    global _service
    if _service is None:
        _service = OllamaExtractionService()
    return _service


def reset_ollama_extraction_service() -> None:
    """Reset the global service instance.

    Primarily useful for testing to ensure a fresh instance.
    """
    global _service
    _service = None
