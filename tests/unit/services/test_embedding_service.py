"""
Unit tests for OllamaEmbeddingService.

Tests the embedding service functionality with mocked HTTP responses.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import numpy as np
import pytest

from kg_builder.services.embedding import (
    DEFAULT_DIMENSION,
    DEFAULT_MODEL,
    EmbeddingComputationError,
    EmbeddingConnectionError,
    EmbeddingServiceFactory,
    OllamaEmbeddingService,
    get_embedding_service,
)


class TestOllamaEmbeddingServiceInit:
    """Tests for OllamaEmbeddingService initialization."""

    def test_default_configuration(self):
        """Test service initializes with correct defaults."""
        service = OllamaEmbeddingService()

        assert service.base_url == "http://localhost:11434"
        assert service.model == DEFAULT_MODEL
        assert service.embedding_dimension == DEFAULT_DIMENSION

    def test_custom_configuration(self):
        """Test service initializes with custom configuration."""
        service = OllamaEmbeddingService(
            base_url="http://custom:8080",
            model="custom-model",
            timeout=60.0,
        )

        assert service.base_url == "http://custom:8080"
        assert service.model == "custom-model"

    def test_base_url_trailing_slash_stripped(self):
        """Test trailing slash is removed from base URL."""
        service = OllamaEmbeddingService(base_url="http://localhost:11434/")

        assert service.base_url == "http://localhost:11434"


class TestOllamaEmbeddingServiceEncode:
    """Tests for single text encoding."""

    @pytest.fixture
    def service(self):
        """Create service instance."""
        return OllamaEmbeddingService()

    @pytest.fixture
    def mock_response(self):
        """Create mock HTTP response with embedding."""

        def create_response(embedding_dim: int = 1024):
            response = MagicMock(spec=httpx.Response)
            response.json.return_value = {
                "embedding": [0.1] * embedding_dim
            }
            response.raise_for_status = MagicMock()
            return response

        return create_response

    @pytest.mark.asyncio
    async def test_encode_returns_numpy_array(self, service, mock_response):
        """Test encode returns correct shape numpy array."""
        with patch.object(service, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response(1024))
            mock_get_client.return_value = mock_client

            embedding = await service.encode("test text")

            assert isinstance(embedding, np.ndarray)
            assert embedding.shape == (1024,)
            assert embedding.dtype == np.float32

    @pytest.mark.asyncio
    async def test_encode_caches_dimension_on_first_call(self, service, mock_response):
        """Test embedding dimension is cached on first successful call."""
        assert service._embedding_dimension is None

        with patch.object(service, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response(1024))
            mock_get_client.return_value = mock_client

            await service.encode("test text")

            assert service._embedding_dimension == 1024
            assert service._is_initialized is True

    @pytest.mark.asyncio
    async def test_encode_empty_text_returns_zero_vector(self, service):
        """Test empty text returns zero vector without API call."""
        embedding = await service.encode("")

        assert isinstance(embedding, np.ndarray)
        assert embedding.shape == (1024,)
        assert np.all(embedding == 0)

    @pytest.mark.asyncio
    async def test_encode_whitespace_text_returns_zero_vector(self, service):
        """Test whitespace-only text returns zero vector."""
        embedding = await service.encode("   ")

        assert np.all(embedding == 0)

    @pytest.mark.asyncio
    async def test_encode_connection_error_raises(self, service):
        """Test connection error is properly raised."""
        with patch.object(service, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            mock_get_client.return_value = mock_client

            # Set max_retries to 1 for faster test
            service._max_retries = 1

            with pytest.raises(EmbeddingConnectionError):
                await service.encode("test text")

    @pytest.mark.asyncio
    async def test_encode_http_error_raises(self, service):
        """Test HTTP error is properly raised."""
        with patch.object(service, "_get_client") as mock_get_client:
            mock_client = AsyncMock()

            # Create a proper HTTPStatusError
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Internal Server Error"
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Server error",
                request=MagicMock(),
                response=mock_response,
            )
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_client

            service._max_retries = 1

            with pytest.raises(EmbeddingComputationError):
                await service.encode("test text")

    @pytest.mark.asyncio
    async def test_encode_model_not_found_error(self, service):
        """Test 404 error when model not found."""
        with patch.object(service, "_get_client") as mock_get_client:
            mock_client = AsyncMock()

            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_response.text = "model not found"
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Not found",
                request=MagicMock(),
                response=mock_response,
            )
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_client

            with pytest.raises(EmbeddingComputationError) as exc_info:
                await service.encode("test text")

            assert "not found" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_encode_missing_embedding_in_response(self, service):
        """Test error when response has no embedding field."""
        with patch.object(service, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.json.return_value = {"error": "something went wrong"}
            mock_response.raise_for_status = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_client

            service._max_retries = 1

            with pytest.raises(EmbeddingComputationError) as exc_info:
                await service.encode("test text")

            assert "No embedding in response" in str(exc_info.value)


class TestOllamaEmbeddingServiceEncodeBatch:
    """Tests for batch text encoding."""

    @pytest.fixture
    def service(self):
        """Create service instance."""
        return OllamaEmbeddingService()

    @pytest.mark.asyncio
    async def test_encode_batch_empty_list(self, service):
        """Test encode_batch with empty list returns empty array."""
        result = await service.encode_batch([])

        assert isinstance(result, np.ndarray)
        assert result.shape == (0,)

    @pytest.mark.asyncio
    async def test_encode_batch_single_text(self, service):
        """Test encode_batch with single text."""
        with patch.object(service, "encode") as mock_encode:
            mock_encode.return_value = np.zeros(1024, dtype=np.float32)

            result = await service.encode_batch(["test"])

            assert result.shape == (1, 1024)
            mock_encode.assert_called_once_with("test")

    @pytest.mark.asyncio
    async def test_encode_batch_multiple_texts(self, service):
        """Test encode_batch with multiple texts."""
        with patch.object(service, "encode") as mock_encode:
            mock_encode.return_value = np.zeros(1024, dtype=np.float32)

            result = await service.encode_batch(["text1", "text2", "text3"])

            assert result.shape == (3, 1024)
            assert mock_encode.call_count == 3

    @pytest.mark.asyncio
    async def test_encode_batch_handles_failures(self, service):
        """Test batch handles individual failures gracefully."""
        call_count = [0]

        async def mock_encode(text):
            call_count[0] += 1
            if text == "fail":
                raise EmbeddingComputationError("Failed")
            return np.ones(1024, dtype=np.float32)

        with patch.object(service, "encode", side_effect=mock_encode):
            result = await service.encode_batch(["good", "fail", "good"])

            assert result.shape == (3, 1024)
            # Failed embedding should be zero vector
            assert np.all(result[1] == 0)
            # Successful embeddings should be ones
            assert np.all(result[0] == 1)
            assert np.all(result[2] == 1)


class TestOllamaEmbeddingServiceHealth:
    """Tests for health check functionality."""

    @pytest.fixture
    def service(self):
        """Create service instance."""
        return OllamaEmbeddingService()

    @pytest.mark.asyncio
    async def test_is_healthy_returns_true(self, service):
        """Test health check returns true when service is available."""
        with patch.object(service, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_client

            result = await service.is_healthy()

            assert result is True

    @pytest.mark.asyncio
    async def test_is_healthy_returns_false_on_error(self, service):
        """Test health check returns false when service unavailable."""
        with patch.object(service, "_get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_get_client.return_value = mock_client

            result = await service.is_healthy()

            assert result is False


class TestOllamaEmbeddingServiceTextHash:
    """Tests for text hashing functionality."""

    def test_compute_text_hash(self):
        """Test text hash computation."""
        service = OllamaEmbeddingService()

        hash1 = service.compute_text_hash("test text")
        hash2 = service.compute_text_hash("test text")
        hash3 = service.compute_text_hash("different text")

        # Same text produces same hash
        assert hash1 == hash2
        # Different text produces different hash
        assert hash1 != hash3
        # Hash is 16 characters
        assert len(hash1) == 16

    def test_compute_text_hash_unicode(self):
        """Test text hash with unicode characters."""
        service = OllamaEmbeddingService()

        hash1 = service.compute_text_hash("unicode text")
        hash2 = service.compute_text_hash("unicode text")

        assert hash1 == hash2
        assert len(hash1) == 16


class TestOllamaEmbeddingServiceClose:
    """Tests for client cleanup."""

    @pytest.mark.asyncio
    async def test_close_releases_client(self):
        """Test close releases the HTTP client."""
        service = OllamaEmbeddingService()

        # Initialize client
        service._client = AsyncMock()
        service._is_initialized = True

        await service.close()

        assert service._client is None
        assert service._is_initialized is False


class TestEmbeddingServiceFactory:
    """Tests for EmbeddingServiceFactory."""

    def setup_method(self):
        """Reset factory before each test."""
        EmbeddingServiceFactory.reset()

    def teardown_method(self):
        """Clean up after each test."""
        EmbeddingServiceFactory.reset()

    def test_get_service_creates_singleton(self):
        """Test factory creates and returns singleton."""
        with patch("kg_builder.services.embedding.settings") as mock_settings:
            mock_settings.OLLAMA_BASE_URL = "http://test:11434"
            mock_settings.OLLAMA_EMBEDDING_MODEL = "test-model"
            mock_settings.OLLAMA_EMBEDDING_TIMEOUT = 30.0
            mock_settings.OLLAMA_MAX_RETRIES = 3

            service1 = EmbeddingServiceFactory.get_service(mock_settings)
            service2 = EmbeddingServiceFactory.get_service(mock_settings)

            assert service1 is service2

    def test_get_service_with_custom_settings(self):
        """Test factory uses provided settings."""
        mock_settings = MagicMock()
        mock_settings.OLLAMA_BASE_URL = "http://custom:11434"
        mock_settings.OLLAMA_EMBEDDING_MODEL = "custom-model"
        mock_settings.OLLAMA_EMBEDDING_TIMEOUT = 60.0
        mock_settings.OLLAMA_MAX_RETRIES = 5

        service = EmbeddingServiceFactory.get_service(mock_settings)

        assert service.base_url == "http://custom:11434"
        assert service.model == "custom-model"

    @pytest.mark.asyncio
    async def test_close_cleans_up_singleton(self):
        """Test factory close cleans up singleton."""
        mock_settings = MagicMock()
        mock_settings.OLLAMA_BASE_URL = "http://test:11434"
        mock_settings.OLLAMA_EMBEDDING_MODEL = "test-model"
        mock_settings.OLLAMA_EMBEDDING_TIMEOUT = 30.0
        mock_settings.OLLAMA_MAX_RETRIES = 3

        service = EmbeddingServiceFactory.get_service(mock_settings)
        assert EmbeddingServiceFactory._instance is not None

        await EmbeddingServiceFactory.close()

        assert EmbeddingServiceFactory._instance is None


class TestGetEmbeddingService:
    """Tests for get_embedding_service convenience function."""

    def setup_method(self):
        """Reset factory before each test."""
        EmbeddingServiceFactory.reset()

    def teardown_method(self):
        """Clean up after each test."""
        EmbeddingServiceFactory.reset()

    def test_get_embedding_service_returns_service(self):
        """Test convenience function returns service instance."""
        with patch("kg_builder.services.embedding.EmbeddingServiceFactory.get_service") as mock_get:
            mock_service = MagicMock()
            mock_get.return_value = mock_service

            result = get_embedding_service()

            assert result is mock_service
