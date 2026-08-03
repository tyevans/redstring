"""
Ollama inference provider implementation.

This module provides an implementation of InferenceProvider for
Ollama, a local LLM server. It uses pydantic-ai for the OpenAI-compatible
API and httpx for direct API calls.

Usage:
    provider = OllamaProvider(
        base_url="http://192.168.1.14:11434",
        default_model="gemma3:12b",
        timeout=60,
    )

    async with provider:
        response = await provider.infer(
            InferenceRequest(prompt="Hello", model="gemma3:12b")
        )
"""

import json
import logging
import time
from datetime import datetime
from typing import AsyncIterator, Optional

import httpx
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel

from kg_builder.inference.providers.base import (
    InferenceChunk,
    InferenceProvider,
    InferenceRequest,
    InferenceResponse,
    ModelInfo,
    ProviderConnectionError,
    ProviderHealth,
    ProviderInvalidRequestError,
    ProviderStatus,
    ProviderTimeoutError,
    ProviderType,
)
from kg_builder.inference.providers.factory import ProviderFactory

logger = logging.getLogger(__name__)


@ProviderFactory.register(ProviderType.OLLAMA)
class OllamaProvider(InferenceProvider):
    """Ollama inference provider.

    Implements InferenceProvider for local Ollama LLM server.
    Uses pydantic-ai for inference and direct httpx for model listing
    and health checks.

    Attributes:
        _base_url: Ollama server URL (e.g., "http://192.168.1.14:11434")
        _default_model: Default model to use if not specified
        _timeout: Request timeout in seconds
        _client: Async HTTP client for direct API calls
    """

    def __init__(
        self,
        base_url: str,
        default_model: str = "gemma3:12b",
        timeout: int = 60,
        max_retries: int = 3,
    ):
        """Initialize Ollama provider.

        Args:
            base_url: Ollama server URL
            default_model: Default model for inference
            timeout: Request timeout in seconds
            max_retries: Maximum retry attempts for failed requests
        """
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout),
            )
        return self._client

    @property
    def provider_type(self) -> ProviderType:
        """Return provider type."""
        return ProviderType.OLLAMA

    @property
    def supports_streaming(self) -> bool:
        """Ollama supports streaming."""
        return True

    async def infer(self, request: InferenceRequest) -> InferenceResponse:
        """Execute synchronous inference using pydantic-ai.

        Uses Ollama's OpenAI-compatible endpoint via pydantic-ai.

        Args:
            request: Inference request parameters

        Returns:
            Complete inference response

        Raises:
            ProviderConnectionError: Connection to Ollama failed
            ProviderTimeoutError: Request timed out
            ProviderInvalidRequestError: Invalid request parameters
        """
        model_name = request.model or self._default_model

        try:
            # Create pydantic-ai model pointing to Ollama's OpenAI-compatible API
            ollama_model = OpenAIModel(
                model_name=model_name,
                base_url=f"{self._base_url}/v1",
                api_key="ollama",  # Placeholder - Ollama doesn't require key
            )

            # Create agent
            agent = Agent(
                model=ollama_model,
                system_prompt=request.system_prompt or "",
            )

            # Execute inference
            start_time = time.time()
            result = await agent.run(request.prompt)
            duration_ms = int((time.time() - start_time) * 1000)

            # Extract token usage from result
            # Note: pydantic-ai may provide usage differently
            usage = getattr(result, "_usage", None) or {}

            return InferenceResponse(
                content=str(result.data),
                model=model_name,
                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                total_tokens=getattr(usage, "total_tokens", 0) or 0,
                duration_ms=duration_ms,
                finish_reason="stop",
            )

        except httpx.ConnectError as e:
            logger.error(
                "Ollama connection failed",
                extra={"base_url": self._base_url, "error": str(e)},
            )
            raise ProviderConnectionError(
                f"Failed to connect to Ollama at {self._base_url}: {e}",
                provider_type="ollama",
            ) from e

        except httpx.TimeoutException as e:
            logger.error(
                "Ollama request timeout",
                extra={"base_url": self._base_url, "timeout": self._timeout},
            )
            raise ProviderTimeoutError(
                f"Ollama request timed out after {self._timeout}s: {e}",
                provider_type="ollama",
            ) from e

        except Exception as e:
            logger.error(
                "Ollama inference failed",
                extra={"base_url": self._base_url, "error": str(e)},
            )
            # Check for specific error types in message
            error_str = str(e).lower()
            if "model" in error_str and (
                "not found" in error_str or "unknown" in error_str
            ):
                raise ProviderInvalidRequestError(
                    f"Model '{model_name}' not found on Ollama server",
                    provider_type="ollama",
                ) from e
            raise ProviderConnectionError(
                f"Ollama inference failed: {e}",
                provider_type="ollama",
            ) from e

    async def infer_stream(
        self,
        request: InferenceRequest,
    ) -> AsyncIterator[InferenceChunk]:
        """Stream inference using Ollama's native streaming API.

        Uses direct HTTP connection to Ollama's /api/generate endpoint
        with stream=True for real-time token delivery.

        Args:
            request: Inference request parameters

        Yields:
            InferenceChunk objects as tokens are generated

        Raises:
            ProviderConnectionError: Connection failed
            ProviderTimeoutError: Request timed out
        """
        model_name = request.model or self._default_model
        client = await self._get_client()

        try:
            async with client.stream(
                "POST",
                "/api/generate",
                json={
                    "model": model_name,
                    "prompt": request.prompt,
                    "system": request.system_prompt or "",
                    "stream": True,
                    "options": {
                        "temperature": request.temperature,
                        "num_predict": request.max_tokens,
                    },
                },
            ) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    yield InferenceChunk(
                        content="",
                        done=True,
                        error=f"Ollama error: {error_text.decode()}",
                    )
                    return

                async for line in response.aiter_lines():
                    if not line:
                        continue

                    try:
                        data = json.loads(line)
                        yield InferenceChunk(
                            content=data.get("response", ""),
                            done=data.get("done", False),
                        )

                        if data.get("done", False):
                            break

                    except json.JSONDecodeError as e:
                        logger.warning(
                            "Failed to parse Ollama stream chunk",
                            extra={"line": line, "error": str(e)},
                        )
                        continue

        except httpx.ConnectError as e:
            yield InferenceChunk(
                content="",
                done=True,
                error=f"Connection failed: {e}",
            )

        except httpx.TimeoutException as e:
            yield InferenceChunk(
                content="",
                done=True,
                error=f"Request timed out: {e}",
            )

        except Exception as e:
            logger.error(
                "Ollama streaming failed",
                extra={"error": str(e)},
            )
            yield InferenceChunk(
                content="",
                done=True,
                error=f"Streaming error: {e}",
            )

    async def list_models(self) -> list[ModelInfo]:
        """List available models from Ollama.

        Calls Ollama's /api/tags endpoint to get installed models.

        Returns:
            List of ModelInfo for available models
        """
        client = await self._get_client()

        try:
            response = await client.get("/api/tags")
            response.raise_for_status()
            data = response.json()

            models = []
            for model_data in data.get("models", []):
                name = model_data.get("name", "")
                models.append(
                    ModelInfo(
                        name=name,
                        display_name=(
                            name.split(":")[0].title() if ":" in name else name.title()
                        ),
                        size_bytes=model_data.get("size"),
                        parameter_count=model_data.get("details", {}).get(
                            "parameter_size"
                        ),
                        capabilities=["chat", "completion"],
                    )
                )

            return models

        except httpx.HTTPError as e:
            logger.error(
                "Failed to list Ollama models",
                extra={"error": str(e)},
            )
            raise ProviderConnectionError(
                f"Failed to list models from Ollama: {e}",
                provider_type="ollama",
            ) from e

    async def health_check(self) -> ProviderHealth:
        """Check Ollama server health.

        Makes a lightweight request to verify connectivity.

        Returns:
            ProviderHealth with status and latency
        """
        client = await self._get_client()
        checked_at = datetime.utcnow()

        try:
            start_time = time.time()
            response = await client.get("/api/tags")
            latency_ms = (time.time() - start_time) * 1000

            if response.status_code == 200:
                data = response.json()
                models = [m.get("name", "") for m in data.get("models", [])]

                return ProviderHealth(
                    status=ProviderStatus.HEALTHY,
                    latency_ms=latency_ms,
                    available_models=models,
                    checked_at=checked_at,
                )
            else:
                return ProviderHealth(
                    status=ProviderStatus.UNHEALTHY,
                    error=f"HTTP {response.status_code}: {response.text}",
                    checked_at=checked_at,
                )

        except httpx.ConnectError as e:
            return ProviderHealth(
                status=ProviderStatus.UNHEALTHY,
                error=f"Connection failed: {e}",
                checked_at=checked_at,
            )

        except httpx.TimeoutException:
            return ProviderHealth(
                status=ProviderStatus.DEGRADED,
                error="Health check timed out",
                checked_at=checked_at,
            )

        except Exception as e:
            return ProviderHealth(
                status=ProviderStatus.UNKNOWN,
                error=f"Health check error: {e}",
                checked_at=checked_at,
            )

    async def close(self) -> None:
        """Close HTTP client connection."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
