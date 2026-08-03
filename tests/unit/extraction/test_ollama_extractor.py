"""
Unit tests for OllamaExtractionService.

Tests service initialization, prompt building, error handling,
and the factory function. Uses mocking to avoid actual Ollama calls.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from kg_builder.extraction.ollama_extractor import (
    ExtractionError,
    OllamaExtractionService,
    get_ollama_extraction_service,
    reset_ollama_extraction_service,
)
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
    return OllamaExtractionService(
        base_url="http://localhost:11434",
        model="test-model",
        timeout=30,
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


@pytest.fixture(autouse=True)
def reset_global_service():
    """Reset the global service instance before each test."""
    reset_ollama_extraction_service()
    yield
    reset_ollama_extraction_service()


# =============================================================================
# Initialization Tests
# =============================================================================


class TestOllamaServiceInit:
    """Tests for OllamaExtractionService initialization."""

    def test_init_with_custom_config(self):
        """Test service initialization with custom configuration."""
        service = OllamaExtractionService(
            base_url="http://custom-ollama:11434",
            model="custom-model:7b",
            timeout=120,
        )

        assert service._base_url == "http://custom-ollama:11434"
        assert service._model == "custom-model:7b"
        assert service._timeout == 120

    def test_init_with_defaults(self):
        """Test service initialization uses settings defaults."""
        with patch("kg_builder.extraction.ollama_extractor.settings") as mock_settings:
            mock_settings.OLLAMA_BASE_URL = "http://default:11434"
            mock_settings.OLLAMA_MODEL = "default-model"
            mock_settings.OLLAMA_TIMEOUT = 60
            mock_settings.OLLAMA_MAX_CONTEXT_LENGTH = 8000

            service = OllamaExtractionService()

            assert service._base_url == "http://default:11434"
            assert service._model == "default-model"
            assert service._timeout == 60

    def test_ollama_model_is_openai_model(self, service):
        """Test that the model is an OpenAIModel instance."""
        from pydantic_ai.models.openai import OpenAIModel

        # The model should be an OpenAIModel instance
        assert isinstance(service._ollama_model, OpenAIModel)

    def test_agent_has_extraction_result_type(self, service):
        """Test that the agent is configured with ExtractionResult."""
        # The agent should be configured to return ExtractionResult
        assert service._agent.result_type == ExtractionResult


# =============================================================================
# Prompt Building Tests
# =============================================================================


class TestPromptBuilding:
    """Tests for prompt building methods."""

    def test_build_prompt_includes_content(self, service):
        """Test that build_prompt includes the content."""
        content = "class Foo:\n    pass"
        page_url = "https://example.com/docs"

        prompt = service._build_prompt(content, page_url)

        assert "class Foo:" in prompt
        assert "pass" in prompt

    def test_build_prompt_includes_url(self, service):
        """Test that build_prompt includes the page URL."""
        content = "Some content"
        page_url = "https://example.com/docs/module"

        prompt = service._build_prompt(content, page_url)

        assert page_url in prompt

    def test_build_prompt_has_instructions(self, service):
        """Test that build_prompt includes extraction instructions."""
        prompt = service._build_prompt("content", "http://example.com")

        assert "entities" in prompt.lower()
        assert "relationships" in prompt.lower()

    def test_system_prompt_mentions_entity_types(self, service):
        """Test that system prompt describes entity types."""
        system_prompt = service._get_system_prompt()

        assert "function" in system_prompt.lower()
        assert "class" in system_prompt.lower()
        assert "module" in system_prompt.lower()
        assert "pattern" in system_prompt.lower()

    def test_system_prompt_mentions_relationship_types(self, service):
        """Test that system prompt describes relationship types."""
        system_prompt = service._get_system_prompt()

        assert "uses" in system_prompt.lower()
        assert "implements" in system_prompt.lower()
        assert "extends" in system_prompt.lower()


# =============================================================================
# Extraction Tests
# =============================================================================


class TestExtraction:
    """Tests for the extract method."""

    @pytest.mark.asyncio
    async def test_extract_returns_extraction_result(self, service, mock_extraction_result):
        """Test that extract returns an ExtractionResult."""
        # Mock the agent's run method
        mock_run_result = MagicMock()
        mock_run_result.data = mock_extraction_result

        with patch.object(service._agent, "run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_run_result

            result = await service.extract(
                content="class DomainEvent(BaseModel): pass",
                page_url="https://docs.example.com/events",
            )

            assert isinstance(result, ExtractionResult)
            assert result.entity_count == 2
            assert result.relationship_count == 1

    @pytest.mark.asyncio
    async def test_extract_truncates_long_content(self, service, mock_extraction_result):
        """Test that extract truncates content exceeding max length."""
        mock_run_result = MagicMock()
        mock_run_result.data = mock_extraction_result

        with patch.object(service._agent, "run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_run_result

            # Create content longer than max_length
            long_content = "x" * 10000

            await service.extract(
                content=long_content,
                page_url="https://example.com",
                max_length=1000,
            )

            # Check that the prompt was built with truncated content
            call_args = mock_run.call_args[0][0]  # First positional argument
            # Content should be truncated (prompt has URL + content)
            assert len(call_args) < len(long_content) + 500

    @pytest.mark.asyncio
    async def test_extract_uses_custom_max_length(self, service, mock_extraction_result):
        """Test that extract respects custom max_length parameter."""
        mock_run_result = MagicMock()
        mock_run_result.data = mock_extraction_result

        with patch.object(service._agent, "run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_run_result

            content = "a" * 500

            await service.extract(
                content=content,
                page_url="https://example.com",
                max_length=100,
            )

            # Check truncation happened
            call_args = mock_run.call_args[0][0]
            # The content in the prompt should be at most 100 chars
            assert "a" * 100 in call_args
            assert "a" * 500 not in call_args

    @pytest.mark.asyncio
    async def test_extract_calls_agent_with_prompt(self, service, mock_extraction_result):
        """Test that extract calls agent.run with the built prompt."""
        mock_run_result = MagicMock()
        mock_run_result.data = mock_extraction_result

        with patch.object(service._agent, "run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_run_result

            await service.extract(
                content="test content",
                page_url="https://example.com/test",
            )

            mock_run.assert_called_once()
            prompt = mock_run.call_args[0][0]
            assert "test content" in prompt
            assert "https://example.com/test" in prompt


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Tests for error handling in extract method."""

    @pytest.mark.asyncio
    async def test_extract_raises_on_connect_error(self, service):
        """Test that extract raises ExtractionError on connection failure."""
        with patch.object(service._agent, "run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = httpx.ConnectError("Connection refused")

            with pytest.raises(ExtractionError) as exc_info:
                await service.extract(
                    content="test",
                    page_url="https://example.com",
                )

            assert "Failed to connect to Ollama" in str(exc_info.value)
            assert exc_info.value.cause is not None

    @pytest.mark.asyncio
    async def test_extract_raises_on_timeout(self, service):
        """Test that extract raises ExtractionError on timeout."""
        with patch.object(service._agent, "run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = httpx.TimeoutException("Request timed out")

            with pytest.raises(ExtractionError) as exc_info:
                await service.extract(
                    content="test",
                    page_url="https://example.com",
                )

            assert "timed out" in str(exc_info.value)
            assert exc_info.value.cause is not None

    @pytest.mark.asyncio
    async def test_extract_raises_on_unexpected_model_behavior(self, service):
        """Test that extract handles UnexpectedModelBehavior."""
        from pydantic_ai.exceptions import UnexpectedModelBehavior

        with patch.object(service._agent, "run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = UnexpectedModelBehavior("Model returned invalid JSON")

            with pytest.raises(ExtractionError) as exc_info:
                await service.extract(
                    content="test",
                    page_url="https://example.com",
                )

            assert "unexpected response" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_extract_raises_on_generic_exception(self, service):
        """Test that extract wraps generic exceptions in ExtractionError."""
        with patch.object(service._agent, "run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = RuntimeError("Something went wrong")

            with pytest.raises(ExtractionError) as exc_info:
                await service.extract(
                    content="test",
                    page_url="https://example.com",
                )

            assert "Extraction failed" in str(exc_info.value)
            assert exc_info.value.cause is not None
            assert isinstance(exc_info.value.cause, RuntimeError)


# =============================================================================
# ExtractionError Tests
# =============================================================================


class TestExtractionError:
    """Tests for the ExtractionError exception class."""

    def test_error_message(self):
        """Test ExtractionError stores message."""
        error = ExtractionError("Test error message")
        assert str(error) == "Test error message"
        assert error.message == "Test error message"

    def test_error_with_cause(self):
        """Test ExtractionError stores cause exception."""
        cause = ValueError("Original error")
        error = ExtractionError("Wrapped error", cause=cause)

        assert error.cause is cause
        assert error.message == "Wrapped error"

    def test_error_without_cause(self):
        """Test ExtractionError works without cause."""
        error = ExtractionError("No cause")
        assert error.cause is None


# =============================================================================
# Health Check Tests
# =============================================================================


class TestHealthCheck:
    """Tests for the health_check method."""

    @pytest.mark.asyncio
    async def test_health_check_healthy(self, service):
        """Test health check returns healthy status."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {"name": "test-model"},
                {"name": "other-model:7b"},
            ]
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            result = await service.health_check()

            assert result["status"] == "healthy"
            assert result["base_url"] == "http://localhost:11434"
            assert result["model"] == "test-model"
            assert "test-model" in result["available_models"]
            assert result["model_available"] is True

    @pytest.mark.asyncio
    async def test_health_check_model_not_available(self, service):
        """Test health check when configured model is not available."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {"name": "other-model:7b"},
            ]
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            result = await service.health_check()

            assert result["status"] == "healthy"
            assert result["model_available"] is False

    @pytest.mark.asyncio
    async def test_health_check_http_error(self, service):
        """Test health check handles HTTP error status."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            result = await service.health_check()

            assert result["status"] == "unhealthy"
            assert "500" in result["error"]

    @pytest.mark.asyncio
    async def test_health_check_connection_error(self, service):
        """Test health check handles connection errors."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_client_class.return_value = mock_client

            result = await service.health_check()

            assert result["status"] == "unhealthy"
            assert "Connection failed" in result["error"]

    @pytest.mark.asyncio
    async def test_health_check_timeout(self, service):
        """Test health check handles timeout."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
            mock_client_class.return_value = mock_client

            result = await service.health_check()

            assert result["status"] == "unhealthy"
            assert "Timeout" in result["error"]


# =============================================================================
# Factory Function Tests
# =============================================================================


class TestFactoryFunction:
    """Tests for the get_ollama_extraction_service factory function."""

    def test_get_service_creates_instance(self):
        """Test factory creates a service instance."""
        with patch("kg_builder.extraction.ollama_extractor.settings") as mock_settings:
            mock_settings.OLLAMA_BASE_URL = "http://test:11434"
            mock_settings.OLLAMA_MODEL = "test-model"
            mock_settings.OLLAMA_TIMEOUT = 60
            mock_settings.OLLAMA_MAX_CONTEXT_LENGTH = 8000

            service = get_ollama_extraction_service()

            assert isinstance(service, OllamaExtractionService)

    def test_get_service_returns_singleton(self):
        """Test factory returns the same instance on multiple calls."""
        with patch("kg_builder.extraction.ollama_extractor.settings") as mock_settings:
            mock_settings.OLLAMA_BASE_URL = "http://test:11434"
            mock_settings.OLLAMA_MODEL = "test-model"
            mock_settings.OLLAMA_TIMEOUT = 60
            mock_settings.OLLAMA_MAX_CONTEXT_LENGTH = 8000

            service1 = get_ollama_extraction_service()
            service2 = get_ollama_extraction_service()

            assert service1 is service2

    def test_reset_service_clears_singleton(self):
        """Test reset_ollama_extraction_service clears the singleton."""
        with patch("kg_builder.extraction.ollama_extractor.settings") as mock_settings:
            mock_settings.OLLAMA_BASE_URL = "http://test:11434"
            mock_settings.OLLAMA_MODEL = "test-model"
            mock_settings.OLLAMA_TIMEOUT = 60
            mock_settings.OLLAMA_MAX_CONTEXT_LENGTH = 8000

            service1 = get_ollama_extraction_service()
            reset_ollama_extraction_service()
            service2 = get_ollama_extraction_service()

            assert service1 is not service2
