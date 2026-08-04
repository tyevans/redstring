"""
Embedding service using Ollama with bge-m3.

This module provides text embedding generation using a local Ollama instance
for semantic similarity computation in entity consolidation.

The bge-m3 model is an excellent choice for embeddings because:
- Multi-lingual: Supports 100+ languages
- Multi-granularity: Works well with short entity names and longer descriptions
- High quality: State-of-the-art retrieval performance
- Local: No external API calls, data stays on-premise

Example usage:
    >>> service = OllamaEmbeddingService()
    >>> embedding = await service.encode("DomainEvent")
    >>> assert embedding.shape == (1024,)

    >>> embeddings = await service.encode_batch(["Event", "Command", "Query"])
    >>> assert embeddings.shape == (3, 1024)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import TYPE_CHECKING

import httpx
import numpy as np

from kg_builder.config import settings

if TYPE_CHECKING:
    from kg_builder.config import Settings

logger = logging.getLogger(__name__)

# Default embedding model
DEFAULT_MODEL = "bge-m3:latest"

# Default embedding dimension for bge-m3
DEFAULT_DIMENSION = 1024


class EmbeddingServiceError(Exception):
    """Base exception for embedding service errors."""

    pass


class EmbeddingConnectionError(EmbeddingServiceError):
    """Error connecting to embedding service."""

    pass


class EmbeddingComputationError(EmbeddingServiceError):
    """Error computing embeddings."""

    pass


class OllamaEmbeddingService:
    """
    Service for generating text embeddings via Ollama.

    Uses bge-m3 model for high-quality multilingual embeddings.
    Provides async HTTP client for efficient batch processing.

    Attributes:
        base_url: Ollama API base URL
        model: Model name for embeddings
        timeout: Request timeout in seconds
        embedding_dimension: Expected embedding dimension (1024 for bge-m3)
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = DEFAULT_MODEL,
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ):
        """
        Initialize Ollama embedding service.

        Args:
            base_url: Ollama API base URL
            model: Model name for embeddings (default: bge-m3:latest)
            timeout: Request timeout in seconds
            max_retries: Maximum retry attempts on transient failures
            retry_delay: Delay between retries in seconds
        """
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._client: httpx.AsyncClient | None = None
        self._embedding_dimension: int | None = None
        self._is_initialized = False

    @property
    def base_url(self) -> str:
        """Get the Ollama API base URL."""
        return self._base_url

    @property
    def model(self) -> str:
        """Get the model name."""
        return self._model

    @property
    def embedding_dimension(self) -> int:
        """
        Get embedding dimension for current model.

        Returns 1024 for bge-m3. This may be updated on first
        successful embedding call if the model returns a different
        dimension.
        """
        return self._embedding_dimension or DEFAULT_DIMENSION

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create async HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout, connect=10.0),
            )
        return self._client

    async def close(self) -> None:
        """Close HTTP client and release resources."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            self._is_initialized = False
            logger.debug("Embedding service client closed")

    async def _check_model_available(self) -> bool:
        """
        Check if the embedding model is available.

        Returns:
            True if model is available, False otherwise
        """
        try:
            client = await self._get_client()
            response = await client.get("/api/tags")
            response.raise_for_status()

            data = response.json()
            models = [m.get("name", "") for m in data.get("models", [])]

            # Check if our model is in the list (with or without tag)
            model_base = self._model.split(":")[0]
            for m in models:
                if m == self._model or m.startswith(f"{model_base}:"):
                    return True

            logger.warning(
                f"Embedding model '{self._model}' not found. "
                f"Available models: {models}. "
                f"Run: ollama pull {self._model}"
            )
            return False

        except Exception as e:
            logger.error(f"Failed to check model availability: {e}")
            return False

    async def encode(self, text: str) -> np.ndarray:
        """
        Encode single text into embedding vector.

        Args:
            text: Text to encode

        Returns:
            Numpy array of shape (embedding_dim,)

        Raises:
            EmbeddingConnectionError: If connection to Ollama fails
            EmbeddingComputationError: If embedding computation fails
        """
        if not text or not text.strip():
            logger.warning("Empty text provided for embedding, returning zero vector")
            return np.zeros(self.embedding_dimension, dtype=np.float32)

        client = await self._get_client()

        last_error = None
        for attempt in range(self._max_retries):
            try:
                response = await client.post(
                    "/api/embeddings",
                    json={
                        "model": self._model,
                        "prompt": text.strip(),
                    },
                )
                response.raise_for_status()

                data = response.json()

                if "embedding" not in data:
                    raise EmbeddingComputationError(f"No embedding in response: {data.keys()}")

                embedding = np.array(data["embedding"], dtype=np.float32)

                # Cache dimension on first successful call
                if self._embedding_dimension is None:
                    self._embedding_dimension = len(embedding)
                    self._is_initialized = True
                    logger.info(
                        f"Ollama embedding service initialized: "
                        f"model={self._model}, dimension={self._embedding_dimension}"
                    )

                return embedding

            except httpx.ConnectError as e:
                last_error = EmbeddingConnectionError(
                    f"Failed to connect to Ollama at {self._base_url}: {e}"
                )
                if attempt < self._max_retries - 1:
                    logger.warning(
                        f"Connection error (attempt {attempt + 1}/{self._max_retries}), "
                        f"retrying in {self._retry_delay}s..."
                    )
                    await asyncio.sleep(self._retry_delay)

            except httpx.HTTPStatusError as e:
                # Check for model not found (404)
                if e.response.status_code == 404:
                    raise EmbeddingComputationError(
                        f"Model '{self._model}' not found. Run: ollama pull {self._model}"
                    ) from e
                last_error = EmbeddingComputationError(
                    f"HTTP error from Ollama: {e.response.status_code} - {e.response.text}"
                )
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(self._retry_delay)

            except Exception as e:
                last_error = EmbeddingComputationError(f"Embedding computation failed: {e}")
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(self._retry_delay)

        raise last_error

    async def encode_batch(
        self,
        texts: list[str],
        batch_size: int = 32,
        show_progress: bool = False,
    ) -> np.ndarray:
        """
        Encode multiple texts into embedding vectors.

        Note: Ollama doesn't have native batch support, so we process
        requests concurrently with controlled parallelism.

        Args:
            texts: List of texts to encode
            batch_size: Maximum concurrent requests
            show_progress: Log progress during processing

        Returns:
            Numpy array of shape (len(texts), embedding_dim)

        Raises:
            EmbeddingConnectionError: If connection to Ollama fails
            EmbeddingComputationError: If embedding computation fails
        """
        if not texts:
            return np.array([], dtype=np.float32)

        embeddings = []
        total = len(texts)

        # Process in batches with controlled concurrency
        for i in range(0, total, batch_size):
            batch = texts[i : i + batch_size]

            # Process batch concurrently
            tasks = [self.encode(text) for text in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for j, result in enumerate(batch_results):
                if isinstance(result, Exception):
                    logger.error(f"Failed to encode text at index {i + j}: {result}")
                    # Use zero vector for failed embeddings
                    embeddings.append(np.zeros(self.embedding_dimension, dtype=np.float32))
                else:
                    embeddings.append(result)

            if show_progress:
                logger.info(f"Embedding progress: {min(i + batch_size, total)}/{total}")

        return np.stack(embeddings)

    async def is_healthy(self) -> bool:
        """
        Check if the embedding service is healthy and ready.

        Returns:
            True if service is healthy and model is available
        """
        try:
            client = await self._get_client()
            response = await client.get("/")
            return response.status_code == 200
        except Exception:
            return False

    def compute_text_hash(self, text: str) -> str:
        """
        Compute a hash of the text for cache key purposes.

        Uses SHA-256 truncated to 16 characters for reasonable uniqueness
        while keeping keys short.

        Args:
            text: Text to hash

        Returns:
            Hex-encoded hash string
        """
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class EmbeddingServiceFactory:
    """Factory for creating and managing embedding service instances."""

    _instance: OllamaEmbeddingService | None = None

    @classmethod
    def get_service(cls, settings_override: Settings | None = None) -> OllamaEmbeddingService:
        """
        Get singleton embedding service instance.

        Creates the service on first call using application settings.
        Subsequent calls return the same instance.

        Args:
            settings_override: Application settings (uses module settings if None)

        Returns:
            Configured OllamaEmbeddingService instance
        """
        if cls._instance is None:
            resolved = settings if settings_override is None else settings_override

            cls._instance = OllamaEmbeddingService(
                base_url=resolved.OLLAMA_BASE_URL,
                model=resolved.OLLAMA_EMBEDDING_MODEL,
                timeout=resolved.OLLAMA_EMBEDDING_TIMEOUT,
                max_retries=resolved.OLLAMA_MAX_RETRIES,
            )

            logger.info(
                f"Created embedding service: "
                f"url={resolved.OLLAMA_BASE_URL}, "
                f"model={resolved.OLLAMA_EMBEDDING_MODEL}"
            )

        return cls._instance

    @classmethod
    async def close(cls) -> None:
        """Close the singleton service instance."""
        if cls._instance is not None:
            await cls._instance.close()
            cls._instance = None

    @classmethod
    def reset(cls) -> None:
        """Reset the factory (for testing)."""
        cls._instance = None


def get_embedding_service() -> OllamaEmbeddingService:
    """
    Get the embedding service singleton.

    Convenience function for dependency injection.

    Returns:
        OllamaEmbeddingService instance
    """
    return EmbeddingServiceFactory.get_service()
