"""
Unit tests for OpenAIExtractionService.

Tests service initialization, extraction, error handling,
and health check. Uses mocking to avoid actual OpenAI API calls.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai import APIConnectionError, APIError, RateLimitError

from kg_builder.extraction.openai_extractor import (
    OpenAIExtractionError,
    OpenAIExtractionService,
)
from kg_builder.extraction.prompts import DocumentationType
from kg_builder.extraction.schemas import (
    ExtractedEntitySchema,
    ExtractedRelationshipSchema,
    ExtractionResult,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def service():
    """Create a test service instance with custom configuration."""
    return OpenAIExtractionService(
        api_key="sk-test-key",
        model="gpt-4o",
        timeout=60,
        max_context_length=8000,
        temperature=0.1,
    )


@pytest.fixture
def mock_extraction_result():
    """Create a mock ExtractionResult for testing."""
    return ExtractionResult(
        entities=[
            ExtractedEntitySchema(
                name="DomainEvent",
                entity_type="class",
                description="Base class for domain events",
                confidence=0.95,
                properties={
                    "base_classes": ["BaseModel"],
                    "methods": ["to_dict"],
                    "is_pydantic_model": True,
                },
            ),
            ExtractedEntitySchema(
                name="to_dict",
                entity_type="function",
                description="Convert event to dictionary",
                confidence=0.9,
                properties={
                    "return_type": "dict",
                    "is_async": False,
                },
            ),
        ],
        relationships=[
            ExtractedRelationshipSchema(
                source_name="DomainEvent",
                target_name="to_dict",
                relationship_type="contains",
                confidence=0.85,
            ),
        ],
        extraction_notes="Extracted from Python class definition",
    )


@pytest.fixture
def mock_openai_response(mock_extraction_result):
    """Create a mock OpenAI API response."""
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(mock_extraction_result.model_dump())

    mock_usage = MagicMock()
    mock_usage.total_tokens = 1500

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = mock_usage

    return mock_response


# =============================================================================
# Initialization Tests
# =============================================================================


class TestOpenAIServiceInit:
    """Tests for OpenAIExtractionService initialization."""

    def test_init_with_custom_config(self):
        """Test service initialization with custom configuration."""
        service = OpenAIExtractionService(
            api_key="sk-custom-key",
            model="gpt-4-turbo",
            timeout=120,
            max_context_length=16000,
            temperature=0.2,
        )

        assert service._api_key == "sk-custom-key"
        assert service._model == "gpt-4-turbo"
        assert service._timeout == 120
        assert service._max_context_length == 16000
        assert service._temperature == 0.2

    def test_init_with_defaults(self):
        """Test service initialization uses default values."""
        service = OpenAIExtractionService(api_key="sk-test")

        assert service._model == "gpt-4o"
        assert service._timeout == 300
        assert service._max_context_length == 8000
        assert service._temperature == 0.1

    def test_init_creates_async_client(self):
        """Test that initialization creates an AsyncOpenAI client."""
        from openai import AsyncOpenAI

        service = OpenAIExtractionService(api_key="sk-test")

        assert service._client is not None
        assert isinstance(service._client, AsyncOpenAI)

    def test_provider_name_is_openai(self, service):
        """Test that provider_name is set correctly."""
        assert service.provider_name == "openai"

    def test_init_with_doc_type(self):
        """Test service initialization with custom doc_type."""
        service = OpenAIExtractionService(
            api_key="sk-test",
            doc_type=DocumentationType.API_REFERENCE,
        )

        assert service._default_doc_type == DocumentationType.API_REFERENCE


# =============================================================================
# Extraction Tests
# =============================================================================


class TestExtraction:
    """Tests for the extract method."""

    @pytest.mark.asyncio
    async def test_extract_returns_extraction_result(
        self, service, mock_extraction_result, mock_openai_response
    ):
        """Test that extract returns an ExtractionResult."""
        with patch.object(
            service._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_openai_response

            result = await service.extract(
                content="class DomainEvent(BaseModel): pass",
                page_url="https://docs.example.com/events",
            )

            assert isinstance(result, ExtractionResult)
            assert result.entity_count == 2
            assert result.relationship_count == 1

    @pytest.mark.asyncio
    async def test_extract_calls_openai_with_json_mode(
        self, service, mock_openai_response
    ):
        """Test that extract uses JSON mode for structured output."""
        with patch.object(
            service._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_openai_response

            await service.extract(
                content="test content",
                page_url="https://example.com",
            )

            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["response_format"] == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_extract_uses_configured_model(self, mock_openai_response):
        """Test that extract uses the configured model."""
        service = OpenAIExtractionService(api_key="sk-test", model="gpt-4-turbo")

        with patch.object(
            service._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_openai_response

            await service.extract(
                content="test",
                page_url="https://example.com",
            )

            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["model"] == "gpt-4-turbo"

    @pytest.mark.asyncio
    async def test_extract_uses_configured_temperature(
        self, service, mock_openai_response
    ):
        """Test that extract uses the configured temperature."""
        with patch.object(
            service._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_openai_response

            await service.extract(
                content="test",
                page_url="https://example.com",
            )

            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["temperature"] == 0.1

    @pytest.mark.asyncio
    async def test_extract_truncates_long_content(
        self, service, mock_openai_response
    ):
        """Test that extract truncates content exceeding max length."""
        with patch.object(
            service._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_openai_response

            long_content = "x" * 10000

            await service.extract(
                content=long_content,
                page_url="https://example.com",
                max_length=1000,
            )

            # Check that message was built with truncated content
            call_kwargs = mock_create.call_args[1]
            user_message = call_kwargs["messages"][1]["content"]
            # Content should be truncated in the message
            assert "x" * 1000 in user_message
            assert "x" * 10000 not in user_message

    @pytest.mark.asyncio
    async def test_extract_includes_system_and_user_messages(
        self, service, mock_openai_response
    ):
        """Test that extract sends both system and user messages."""
        with patch.object(
            service._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_openai_response

            await service.extract(
                content="test content",
                page_url="https://example.com/docs",
            )

            call_kwargs = mock_create.call_args[1]
            messages = call_kwargs["messages"]

            assert len(messages) == 2
            assert messages[0]["role"] == "system"
            assert messages[1]["role"] == "user"
            assert "test content" in messages[1]["content"]
            assert "https://example.com/docs" in messages[1]["content"]

    @pytest.mark.asyncio
    async def test_extract_with_doc_type_override(
        self, service, mock_openai_response
    ):
        """Test that extract can override default doc_type."""
        with patch.object(
            service._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_openai_response

            await service.extract(
                content="def foo(): pass",
                page_url="https://example.com",
                doc_type=DocumentationType.API_REFERENCE,
            )

            # Should complete without error
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_extract_with_additional_context(
        self, service, mock_openai_response
    ):
        """Test that extract includes additional context in prompt."""
        with patch.object(
            service._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_openai_response

            await service.extract(
                content="test content",
                page_url="https://example.com",
                additional_context="This is a Django model definition",
            )

            call_kwargs = mock_create.call_args[1]
            user_message = call_kwargs["messages"][1]["content"]
            assert "Django model" in user_message


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Tests for error handling in extract method."""

    @pytest.mark.asyncio
    async def test_extract_raises_on_empty_response(self, service):
        """Test that extract raises error on empty response."""
        mock_choice = MagicMock()
        mock_choice.message.content = None

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        with patch.object(
            service._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_response

            with pytest.raises(OpenAIExtractionError) as exc_info:
                await service.extract(
                    content="test",
                    page_url="https://example.com",
                )

            assert "Empty response" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_extract_raises_on_invalid_json(self, service):
        """Test that extract raises error on invalid JSON response."""
        mock_choice = MagicMock()
        mock_choice.message.content = "not valid json"

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        with patch.object(
            service._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_response

            with pytest.raises(OpenAIExtractionError) as exc_info:
                await service.extract(
                    content="test",
                    page_url="https://example.com",
                )

            assert "Invalid JSON" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_extract_raises_on_rate_limit(self, service):
        """Test that extract raises error on rate limit."""
        with patch.object(
            service._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.side_effect = RateLimitError(
                message="Rate limit exceeded",
                response=MagicMock(status_code=429),
                body=None,
            )

            with pytest.raises(OpenAIExtractionError) as exc_info:
                await service.extract(
                    content="test",
                    page_url="https://example.com",
                )

            assert "Rate limit" in str(exc_info.value)
            assert exc_info.value.cause is not None

    @pytest.mark.asyncio
    async def test_extract_raises_on_connection_error(self, service):
        """Test that extract raises error on connection failure."""
        with patch.object(
            service._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.side_effect = APIConnectionError(request=MagicMock())

            with pytest.raises(OpenAIExtractionError) as exc_info:
                await service.extract(
                    content="test",
                    page_url="https://example.com",
                )

            assert "Connection failed" in str(exc_info.value)
            assert exc_info.value.cause is not None

    @pytest.mark.asyncio
    async def test_extract_raises_on_api_error(self, service):
        """Test that extract raises error on API error."""
        with patch.object(
            service._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.side_effect = APIError(
                message="Internal server error",
                request=MagicMock(),
                body=None,
            )

            with pytest.raises(OpenAIExtractionError) as exc_info:
                await service.extract(
                    content="test",
                    page_url="https://example.com",
                )

            assert "API error" in str(exc_info.value)
            assert exc_info.value.cause is not None

    @pytest.mark.asyncio
    async def test_extract_raises_on_generic_exception(self, service):
        """Test that extract wraps generic exceptions."""
        with patch.object(
            service._client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.side_effect = RuntimeError("Something went wrong")

            with pytest.raises(OpenAIExtractionError) as exc_info:
                await service.extract(
                    content="test",
                    page_url="https://example.com",
                )

            assert "Extraction failed" in str(exc_info.value)
            assert exc_info.value.cause is not None
            assert isinstance(exc_info.value.cause, RuntimeError)


# =============================================================================
# OpenAIExtractionError Tests
# =============================================================================


class TestOpenAIExtractionError:
    """Tests for the OpenAIExtractionError exception class."""

    def test_error_message(self):
        """Test OpenAIExtractionError stores message."""
        error = OpenAIExtractionError("Test error message")
        # str() includes provider prefix: "[openai] Test error message"
        assert "Test error message" in str(error)
        assert error.message == "Test error message"

    def test_error_with_cause(self):
        """Test OpenAIExtractionError stores cause exception."""
        cause = ValueError("Original error")
        error = OpenAIExtractionError("Wrapped error", cause=cause)

        assert error.cause is cause
        assert error.message == "Wrapped error"

    def test_error_provider_is_openai(self):
        """Test OpenAIExtractionError has provider set to 'openai'."""
        error = OpenAIExtractionError("Test error")
        assert error.provider == "openai"

    def test_error_without_cause(self):
        """Test OpenAIExtractionError works without cause."""
        error = OpenAIExtractionError("No cause")
        assert error.cause is None


# =============================================================================
# Health Check Tests
# =============================================================================


class TestHealthCheck:
    """Tests for the health_check method."""

    @pytest.mark.asyncio
    async def test_health_check_healthy(self, service):
        """Test health check returns healthy status."""
        mock_model = MagicMock()
        mock_model.id = "gpt-4o"

        mock_models = MagicMock()
        mock_models.data = [mock_model]

        with patch.object(
            service._client.models, "list", new_callable=AsyncMock
        ) as mock_list:
            mock_list.return_value = mock_models

            result = await service.health_check()

            assert result["status"] == "healthy"
            assert result["provider"] == "openai"
            assert result["model"] == "gpt-4o"
            assert result["model_available"] is True

    @pytest.mark.asyncio
    async def test_health_check_model_not_available(self, service):
        """Test health check when configured model is not available."""
        mock_model = MagicMock()
        mock_model.id = "gpt-3.5-turbo"

        mock_models = MagicMock()
        mock_models.data = [mock_model]

        with patch.object(
            service._client.models, "list", new_callable=AsyncMock
        ) as mock_list:
            mock_list.return_value = mock_models

            result = await service.health_check()

            assert result["status"] == "healthy"
            assert result["model_available"] is False

    @pytest.mark.asyncio
    async def test_health_check_api_error(self, service):
        """Test health check handles API errors."""
        with patch.object(
            service._client.models, "list", new_callable=AsyncMock
        ) as mock_list:
            mock_list.side_effect = APIError(
                message="Invalid API key",
                request=MagicMock(),
                body=None,
            )

            result = await service.health_check()

            assert result["status"] == "unhealthy"
            assert result["provider"] == "openai"
            assert "error" in result

    @pytest.mark.asyncio
    async def test_health_check_connection_error(self, service):
        """Test health check handles connection errors."""
        with patch.object(
            service._client.models, "list", new_callable=AsyncMock
        ) as mock_list:
            mock_list.side_effect = APIConnectionError(request=MagicMock())

            result = await service.health_check()

            assert result["status"] == "unhealthy"
            assert "error" in result
