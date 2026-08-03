"""
Unit tests for ExtractionProviderFactory.

Tests factory creation, service instantiation, config validation,
and error handling. Uses mocking to avoid actual service dependencies.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from kg_builder.extraction.factory import (
    ExtractionProviderFactory,
    ProviderConfigError,
)
from kg_builder.models.extraction_provider import ExtractionProvider, ExtractionProviderType

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def tenant_id():
    """Create a test tenant ID."""
    return uuid.uuid4()


@pytest.fixture
def openai_provider(tenant_id):
    """Create a mock OpenAI ExtractionProvider."""
    provider = MagicMock(spec=ExtractionProvider)
    provider.id = uuid.uuid4()
    provider.tenant_id = tenant_id
    provider.name = "My OpenAI"
    provider.provider_type = ExtractionProviderType.OPENAI
    provider.config = {"api_key": "sk-test-key"}
    provider.default_model = "gpt-4o"
    provider.timeout_seconds = 300
    provider.max_context_length = 8000
    return provider


@pytest.fixture
def ollama_provider(tenant_id):
    """Create a mock Ollama ExtractionProvider."""
    provider = MagicMock(spec=ExtractionProvider)
    provider.id = uuid.uuid4()
    provider.tenant_id = tenant_id
    provider.name = "Local Ollama"
    provider.provider_type = ExtractionProviderType.OLLAMA
    provider.config = {"base_url": "http://localhost:11434"}
    provider.default_model = "llama2"
    provider.timeout_seconds = 300
    provider.max_context_length = 8000
    return provider


@pytest.fixture
def anthropic_provider(tenant_id):
    """Create a mock Anthropic ExtractionProvider."""
    provider = MagicMock(spec=ExtractionProvider)
    provider.id = uuid.uuid4()
    provider.tenant_id = tenant_id
    provider.name = "Anthropic Claude"
    provider.provider_type = ExtractionProviderType.ANTHROPIC
    provider.config = {"api_key": "sk-ant-test-key"}
    provider.default_model = "claude-3-5-sonnet"
    provider.timeout_seconds = 300
    provider.max_context_length = 8000
    return provider


# =============================================================================
# OpenAI Service Creation Tests
# =============================================================================


class TestOpenAIServiceCreation:
    """Tests for creating OpenAI extraction services."""

    def test_create_openai_service(self, openai_provider, tenant_id):
        """Test creating an OpenAI service from provider config."""
        with patch("kg_builder.encryption.get_encryption_service") as mock_enc:
            mock_encryption = MagicMock()
            mock_encryption.is_encrypted.return_value = False
            mock_enc.return_value = mock_encryption

            service = ExtractionProviderFactory.create_service(openai_provider, tenant_id)

            from kg_builder.extraction.openai_extractor import OpenAIExtractionService

            assert isinstance(service, OpenAIExtractionService)
            assert service._model == "gpt-4o"
            assert service._timeout == 300
            assert service._max_context_length == 8000

    def test_create_openai_service_decrypts_key(self, openai_provider, tenant_id):
        """Test that encrypted API keys are decrypted."""
        openai_provider.config = {"api_key": "enc:encrypted-key-data"}

        with patch("kg_builder.encryption.get_encryption_service") as mock_enc:
            mock_encryption = MagicMock()
            mock_encryption.is_encrypted.return_value = True
            mock_encryption.decrypt.return_value = "sk-decrypted-key"
            mock_enc.return_value = mock_encryption

            service = ExtractionProviderFactory.create_service(openai_provider, tenant_id)

            mock_encryption.decrypt.assert_called_once_with(
                "enc:encrypted-key-data",
                tenant_id,
                field_name="api_key",
            )
            assert service._api_key == "sk-decrypted-key"

    def test_create_openai_service_uses_config_model(self, openai_provider, tenant_id):
        """Test that model can come from config if default_model is None."""
        openai_provider.default_model = None
        openai_provider.config = {"api_key": "sk-test", "model": "gpt-4-turbo"}

        with patch("kg_builder.encryption.get_encryption_service") as mock_enc:
            mock_encryption = MagicMock()
            mock_encryption.is_encrypted.return_value = False
            mock_enc.return_value = mock_encryption

            service = ExtractionProviderFactory.create_service(openai_provider, tenant_id)

            assert service._model == "gpt-4-turbo"

    def test_create_openai_service_uses_default_model(self, openai_provider, tenant_id):
        """Test that model defaults to gpt-4o if not specified."""
        openai_provider.default_model = None
        openai_provider.config = {"api_key": "sk-test"}

        with patch("kg_builder.encryption.get_encryption_service") as mock_enc:
            mock_encryption = MagicMock()
            mock_encryption.is_encrypted.return_value = False
            mock_enc.return_value = mock_encryption

            service = ExtractionProviderFactory.create_service(openai_provider, tenant_id)

            assert service._model == "gpt-4o"

    def test_create_openai_service_uses_temperature(self, openai_provider, tenant_id):
        """Test that temperature is passed from config."""
        openai_provider.config = {"api_key": "sk-test", "temperature": 0.5}

        with patch("kg_builder.encryption.get_encryption_service") as mock_enc:
            mock_encryption = MagicMock()
            mock_encryption.is_encrypted.return_value = False
            mock_enc.return_value = mock_encryption

            service = ExtractionProviderFactory.create_service(openai_provider, tenant_id)

            assert service._temperature == 0.5

    def test_create_openai_service_missing_api_key_raises(self, openai_provider, tenant_id):
        """Test that missing API key raises ProviderConfigError."""
        openai_provider.config = {}

        with patch("kg_builder.encryption.get_encryption_service") as mock_enc:
            mock_encryption = MagicMock()
            mock_encryption.is_encrypted.return_value = False
            mock_enc.return_value = mock_encryption

            with pytest.raises(ProviderConfigError) as exc_info:
                ExtractionProviderFactory.create_service(openai_provider, tenant_id)

            assert "api_key" in str(exc_info.value)
            assert exc_info.value.provider == "openai"


# =============================================================================
# Ollama Service Creation Tests
# =============================================================================


class TestOllamaServiceCreation:
    """Tests for creating Ollama extraction services."""

    def test_create_ollama_service(self, ollama_provider, tenant_id):
        """Test creating an Ollama service from provider config."""
        with patch("kg_builder.encryption.get_encryption_service") as mock_enc:
            mock_encryption = MagicMock()
            mock_encryption.is_encrypted.return_value = False
            mock_enc.return_value = mock_encryption

            service = ExtractionProviderFactory.create_service(ollama_provider, tenant_id)

            from kg_builder.extraction.ollama_extractor import OllamaExtractionService

            assert isinstance(service, OllamaExtractionService)
            assert service._model == "llama2"
            assert service._timeout == 300

    def test_create_ollama_service_uses_base_url(self, ollama_provider, tenant_id):
        """Test that base_url is passed from config."""
        ollama_provider.config = {"base_url": "http://custom-ollama:11434"}

        with patch("kg_builder.encryption.get_encryption_service") as mock_enc:
            mock_encryption = MagicMock()
            mock_encryption.is_encrypted.return_value = False
            mock_enc.return_value = mock_encryption

            service = ExtractionProviderFactory.create_service(ollama_provider, tenant_id)

            assert service._base_url == "http://custom-ollama:11434"

    def test_create_ollama_service_without_api_key(self, ollama_provider, tenant_id):
        """Test that Ollama doesn't require API key."""
        ollama_provider.config = {}

        with patch("kg_builder.encryption.get_encryption_service") as mock_enc:
            mock_encryption = MagicMock()
            mock_encryption.is_encrypted.return_value = False
            mock_enc.return_value = mock_encryption

            # Should not raise
            service = ExtractionProviderFactory.create_service(ollama_provider, tenant_id)

            from kg_builder.extraction.ollama_extractor import OllamaExtractionService

            assert isinstance(service, OllamaExtractionService)


# =============================================================================
# Anthropic Service Creation Tests
# =============================================================================


class TestAnthropicServiceCreation:
    """Tests for creating Anthropic extraction services."""

    def test_create_anthropic_service_not_implemented(self, anthropic_provider, tenant_id):
        """Test that Anthropic provider raises NotImplementedError."""
        with patch("kg_builder.encryption.get_encryption_service") as mock_enc:
            mock_encryption = MagicMock()
            mock_encryption.is_encrypted.return_value = False
            mock_enc.return_value = mock_encryption

            with pytest.raises(NotImplementedError) as exc_info:
                ExtractionProviderFactory.create_service(anthropic_provider, tenant_id)

            assert "Anthropic" in str(exc_info.value)


# =============================================================================
# Key Decryption Tests
# =============================================================================


class TestKeyDecryption:
    """Tests for API key decryption behavior."""

    def test_decrypt_failure_raises_config_error(self, openai_provider, tenant_id):
        """Test that decryption failure raises ProviderConfigError."""
        openai_provider.config = {"api_key": "enc:bad-encrypted-data"}

        with patch("kg_builder.encryption.get_encryption_service") as mock_enc:
            mock_encryption = MagicMock()
            mock_encryption.is_encrypted.return_value = True
            mock_encryption.decrypt.side_effect = ValueError("Decryption failed")
            mock_enc.return_value = mock_encryption

            with pytest.raises(ProviderConfigError) as exc_info:
                ExtractionProviderFactory.create_service(openai_provider, tenant_id)

            assert "decrypt" in str(exc_info.value).lower()

    def test_unencrypted_key_not_decrypted(self, openai_provider, tenant_id):
        """Test that plaintext API keys are not decrypted."""
        openai_provider.config = {"api_key": "sk-plaintext-key"}

        with patch("kg_builder.encryption.get_encryption_service") as mock_enc:
            mock_encryption = MagicMock()
            mock_encryption.is_encrypted.return_value = False
            mock_enc.return_value = mock_encryption

            service = ExtractionProviderFactory.create_service(openai_provider, tenant_id)

            mock_encryption.decrypt.assert_not_called()
            assert service._api_key == "sk-plaintext-key"


# =============================================================================
# Unknown Provider Tests
# =============================================================================


class TestUnknownProvider:
    """Tests for handling unknown provider types."""

    def test_unknown_provider_type_raises(self, tenant_id):
        """Test that unknown provider type raises ProviderConfigError."""
        provider = MagicMock(spec=ExtractionProvider)
        provider.id = uuid.uuid4()
        provider.tenant_id = tenant_id
        provider.provider_type = "unknown_type"
        provider.config = {}

        with patch("kg_builder.encryption.get_encryption_service") as mock_enc:
            mock_encryption = MagicMock()
            mock_encryption.is_encrypted.return_value = False
            mock_enc.return_value = mock_encryption

            with pytest.raises(ProviderConfigError) as exc_info:
                ExtractionProviderFactory.create_service(provider, tenant_id)

            assert "Unknown provider type" in str(exc_info.value)


# =============================================================================
# Supported Provider Types Tests
# =============================================================================


class TestSupportedProviderTypes:
    """Tests for get_supported_provider_types method."""

    def test_returns_all_provider_types(self):
        """Test that all provider types are returned."""
        supported = ExtractionProviderFactory.get_supported_provider_types()

        assert "ollama" in supported
        assert "openai" in supported
        assert "anthropic" in supported
        assert len(supported) == 3

    def test_returns_string_values(self):
        """Test that provider types are returned as strings."""
        supported = ExtractionProviderFactory.get_supported_provider_types()

        for provider_type in supported:
            assert isinstance(provider_type, str)


# =============================================================================
# Config Validation Tests
# =============================================================================


class TestConfigValidation:
    """Tests for validate_config method."""

    def test_validate_openai_requires_api_key(self):
        """Test that OpenAI validation requires api_key."""
        errors = ExtractionProviderFactory.validate_config(
            ExtractionProviderType.OPENAI,
            {},
        )

        assert len(errors) == 1
        assert "api_key" in errors[0]

    def test_validate_openai_with_api_key(self):
        """Test that OpenAI validation passes with api_key."""
        errors = ExtractionProviderFactory.validate_config(
            ExtractionProviderType.OPENAI,
            {"api_key": "sk-test"},
        )

        assert len(errors) == 0

    def test_validate_anthropic_requires_api_key(self):
        """Test that Anthropic validation requires api_key."""
        errors = ExtractionProviderFactory.validate_config(
            ExtractionProviderType.ANTHROPIC,
            {},
        )

        assert len(errors) == 1
        assert "api_key" in errors[0]

    def test_validate_anthropic_with_api_key(self):
        """Test that Anthropic validation passes with api_key."""
        errors = ExtractionProviderFactory.validate_config(
            ExtractionProviderType.ANTHROPIC,
            {"api_key": "sk-ant-test"},
        )

        assert len(errors) == 0

    def test_validate_ollama_accepts_empty_config(self):
        """Test that Ollama validation accepts empty config."""
        errors = ExtractionProviderFactory.validate_config(
            ExtractionProviderType.OLLAMA,
            {},
        )

        assert len(errors) == 0

    def test_validate_ollama_accepts_base_url(self):
        """Test that Ollama validation accepts base_url."""
        errors = ExtractionProviderFactory.validate_config(
            ExtractionProviderType.OLLAMA,
            {"base_url": "http://localhost:11434"},
        )

        assert len(errors) == 0


# =============================================================================
# ProviderConfigError Tests
# =============================================================================


class TestProviderConfigError:
    """Tests for the ProviderConfigError exception class."""

    def test_error_message(self):
        """Test ProviderConfigError stores message."""
        error = ProviderConfigError("Test error message")
        assert str(error) == "Test error message"

    def test_error_with_provider_type(self):
        """Test ProviderConfigError stores provider type."""
        error = ProviderConfigError("Test error", provider_type="openai")
        assert error.provider == "openai"

    def test_error_without_provider_type(self):
        """Test ProviderConfigError works without provider type."""
        error = ProviderConfigError("Test error")
        assert error.provider is None
