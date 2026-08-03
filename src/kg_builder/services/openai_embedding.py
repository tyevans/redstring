"""
OpenAI embedding service for semantic entity matching.

Provides text embeddings using OpenAI's text-embedding models
for entity consolidation and similarity computation.
"""

import logging
from typing import Sequence

import numpy as np
from openai import AsyncOpenAI, APIError, APIConnectionError

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "text-embedding-3-small"
DEFAULT_DIMENSION = 1536


class OpenAIEmbeddingError(Exception):
    """Raised when OpenAI embedding generation fails."""

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.message = message
        self.cause = cause


class OpenAIEmbeddingService:
    """Service for generating embeddings via OpenAI API.

    Supports text-embedding-3-small (1536 dims) and text-embedding-3-large (3072 dims).

    Example:
        service = OpenAIEmbeddingService(api_key="sk-...")
        embedding = await service.encode("Hello world")
        print(f"Embedding dimension: {len(embedding)}")
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        timeout: float = 30.0,
    ):
        """Initialize the OpenAI embedding service.

        Args:
            api_key: OpenAI API key
            model: Embedding model name (text-embedding-3-small or text-embedding-3-large)
            timeout: Request timeout in seconds
        """
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout)

        # Dimension depends on model
        if "large" in model:
            self._embedding_dimension = 3072
        else:
            self._embedding_dimension = 1536

        logger.info(
            "OpenAIEmbeddingService initialized",
            extra={
                "model": model,
                "dimension": self._embedding_dimension,
            },
        )

    @property
    def embedding_dimension(self) -> int:
        """Return the embedding dimension for the configured model."""
        return self._embedding_dimension

    @property
    def model(self) -> str:
        """Return the configured model name."""
        return self._model

    async def encode(self, text: str) -> np.ndarray:
        """Encode a single text into an embedding vector.

        Args:
            text: Text to encode

        Returns:
            numpy array of shape (embedding_dimension,)

        Raises:
            OpenAIEmbeddingError: If encoding fails
        """
        if not text or not text.strip():
            return np.zeros(self._embedding_dimension, dtype=np.float32)

        try:
            response = await self._client.embeddings.create(
                model=self._model,
                input=text.strip(),
            )

            embedding = np.array(response.data[0].embedding, dtype=np.float32)
            return embedding

        except APIConnectionError as e:
            logger.error("Failed to connect to OpenAI for embeddings", extra={"error": str(e)})
            raise OpenAIEmbeddingError(f"Connection failed: {e}", cause=e)

        except APIError as e:
            logger.error("OpenAI embedding API error", extra={"error": str(e)})
            raise OpenAIEmbeddingError(f"API error: {e}", cause=e)

        except Exception as e:
            logger.error(
                "OpenAI embedding generation failed",
                extra={"error": str(e), "error_type": type(e).__name__},
            )
            raise OpenAIEmbeddingError(f"Embedding failed: {e}", cause=e)

    async def encode_batch(
        self,
        texts: Sequence[str],
        batch_size: int = 100,
    ) -> np.ndarray:
        """Encode multiple texts into embedding vectors.

        Args:
            texts: Sequence of texts to encode
            batch_size: Number of texts per API call (max 2048 for OpenAI)

        Returns:
            numpy array of shape (len(texts), embedding_dimension)

        Raises:
            OpenAIEmbeddingError: If encoding fails
        """
        if not texts:
            return np.array([], dtype=np.float32).reshape(0, self._embedding_dimension)

        # Filter and track empty texts
        valid_texts = []
        valid_indices = []
        for i, text in enumerate(texts):
            if text and text.strip():
                valid_texts.append(text.strip())
                valid_indices.append(i)

        if not valid_texts:
            return np.zeros((len(texts), self._embedding_dimension), dtype=np.float32)

        try:
            # OpenAI supports batch embedding natively
            all_embeddings = []

            for i in range(0, len(valid_texts), batch_size):
                batch = valid_texts[i : i + batch_size]

                response = await self._client.embeddings.create(
                    model=self._model,
                    input=batch,
                )

                # Sort by index to maintain order
                sorted_data = sorted(response.data, key=lambda x: x.index)
                for item in sorted_data:
                    all_embeddings.append(
                        np.array(item.embedding, dtype=np.float32)
                    )

            # Build result array with zeros for empty texts
            result = np.zeros((len(texts), self._embedding_dimension), dtype=np.float32)
            for orig_idx, embedding in zip(valid_indices, all_embeddings):
                result[orig_idx] = embedding

            return result

        except APIConnectionError as e:
            logger.error("Failed to connect to OpenAI for batch embeddings", extra={"error": str(e)})
            raise OpenAIEmbeddingError(f"Connection failed: {e}", cause=e)

        except APIError as e:
            logger.error("OpenAI batch embedding API error", extra={"error": str(e)})
            raise OpenAIEmbeddingError(f"API error: {e}", cause=e)

        except Exception as e:
            logger.error(
                "OpenAI batch embedding generation failed",
                extra={"error": str(e), "error_type": type(e).__name__},
            )
            raise OpenAIEmbeddingError(f"Batch embedding failed: {e}", cause=e)

    async def similarity(self, text1: str, text2: str) -> float:
        """Compute cosine similarity between two texts.

        Args:
            text1: First text
            text2: Second text

        Returns:
            Cosine similarity score between -1 and 1
        """
        emb1 = await self.encode(text1)
        emb2 = await self.encode(text2)

        # Handle zero vectors
        norm1 = np.linalg.norm(emb1)
        norm2 = np.linalg.norm(emb2)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return float(np.dot(emb1, emb2) / (norm1 * norm2))

    async def is_healthy(self) -> bool:
        """Check if the service is healthy.

        Returns:
            True if the service can generate embeddings, False otherwise
        """
        try:
            # Try to generate a simple embedding
            await self._client.embeddings.create(
                model=self._model,
                input="health check",
            )
            return True
        except Exception:
            return False

    async def health_check(self) -> dict:
        """Check service health with detailed status.

        Returns:
            dict with health status information
        """
        try:
            response = await self._client.embeddings.create(
                model=self._model,
                input="health check",
            )
            return {
                "status": "healthy",
                "provider": "openai",
                "model": self._model,
                "dimension": self._embedding_dimension,
                "tokens_used": response.usage.total_tokens if response.usage else None,
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "provider": "openai",
                "model": self._model,
                "error": str(e),
            }
