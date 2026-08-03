"""
Unit tests for ContentClassifier.

Tests classification functionality including:
- Successful classification with mocked LLM responses
- Content sanitization (PII removal)
- Fallback behavior on errors and timeouts
- Short content handling
- Unknown domain handling
- Confidence threshold enforcement
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import directly from modules to avoid triggering database dependencies
# through the kg_builder.extraction package init
from kg_builder.extraction.classifier import (
    CC_PATTERN,
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_FALLBACK_DOMAIN,
    EMAIL_PATTERN,
    MAX_CONTENT_FOR_CLASSIFICATION,
    MIN_CONTENT_LENGTH,
    PHONE_PATTERN,
    SSN_PATTERN,
    ContentClassifier,
    classify_content,
)
from kg_builder.extraction.domains.models import ClassificationResult

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_inference_response():
    """Create a mock inference response."""

    def _create_response(content: str):
        response = MagicMock()
        response.content = content
        return response

    return _create_response


@pytest.fixture
def mock_provider(mock_inference_response):
    """Create a mock inference provider."""
    provider = AsyncMock()
    provider.infer = AsyncMock()
    return provider


@pytest.fixture
def mock_registry():
    """Create a mock domain schema registry."""
    registry = MagicMock()

    # Mock domain list
    domain1 = MagicMock()
    domain1.domain_id = "technical_documentation"
    domain1.description = "Technical docs and API references"

    domain2 = MagicMock()
    domain2.domain_id = "literature_fiction"
    domain2.description = "Novels, plays, and narrative works"

    domain3 = MagicMock()
    domain3.domain_id = "news_journalism"
    domain3.description = "News articles and journalism"

    domain4 = MagicMock()
    domain4.domain_id = "encyclopedia_wiki"
    domain4.description = "Encyclopedia and wiki content"

    registry.list_domains.return_value = [domain1, domain2, domain3, domain4]
    registry.has_domain.side_effect = lambda d: (
        d
        in [
            "technical_documentation",
            "literature_fiction",
            "news_journalism",
            "encyclopedia_wiki",
        ]
    )

    return registry


@pytest.fixture
def classifier(mock_provider, mock_registry):
    """Create a classifier with mocked dependencies."""
    return ContentClassifier(
        inference_provider=mock_provider,
        registry=mock_registry,
    )


@pytest.fixture
def sample_long_content():
    """Sample content long enough for classification."""
    return (
        "This is a comprehensive technical documentation about Python programming. "
        "It covers topics like asyncio, type hints, and best practices for writing "
        "clean, maintainable code. The document includes examples of FastAPI endpoints, "
        "Pydantic models for validation, and SQLAlchemy ORM patterns. "
        "Additional content is provided to ensure minimum length requirements are met. "
    ) * 3  # Repeat to ensure > 100 characters


# =============================================================================
# Initialization Tests
# =============================================================================


class TestClassifierInit:
    """Tests for ContentClassifier initialization."""

    def test_init_with_defaults(self, mock_provider):
        """Test initialization with default parameters."""
        classifier = ContentClassifier(inference_provider=mock_provider)

        assert classifier._provider is mock_provider
        assert classifier._confidence_threshold == DEFAULT_CONFIDENCE_THRESHOLD
        assert classifier._fallback_domain == DEFAULT_FALLBACK_DOMAIN

    def test_init_with_custom_parameters(self, mock_provider, mock_registry):
        """Test initialization with custom parameters."""
        classifier = ContentClassifier(
            inference_provider=mock_provider,
            timeout_seconds=60.0,
            confidence_threshold=0.7,
            fallback_domain="news_journalism",
            registry=mock_registry,
        )

        assert classifier._timeout == 60.0
        assert classifier._confidence_threshold == 0.7
        assert classifier._fallback_domain == "news_journalism"
        assert classifier._registry is mock_registry


# =============================================================================
# Classification Tests
# =============================================================================


class TestClassification:
    """Tests for the classify method."""

    @pytest.mark.asyncio
    async def test_classify_returns_correct_domain(
        self, classifier, mock_provider, mock_inference_response, sample_long_content
    ):
        """Test successful classification returns correct domain."""
        mock_provider.infer.return_value = mock_inference_response(
            '{"domain": "technical_documentation", "confidence": 0.9, '
            '"reasoning": "Contains Python and FastAPI references"}'
        )

        result = await classifier.classify(sample_long_content)

        assert isinstance(result, ClassificationResult)
        assert result.domain == "technical_documentation"
        assert result.confidence == 0.9
        assert result.reasoning == "Contains Python and FastAPI references"

    @pytest.mark.asyncio
    async def test_classify_with_tenant_id(
        self, classifier, mock_provider, mock_inference_response, sample_long_content
    ):
        """Test classification logs tenant_id correctly."""
        mock_provider.infer.return_value = mock_inference_response(
            '{"domain": "literature_fiction", "confidence": 0.85}'
        )

        result = await classifier.classify(sample_long_content, tenant_id="tenant-123")

        assert result.domain == "literature_fiction"
        # Verify the provider was called
        mock_provider.infer.assert_called_once()

    @pytest.mark.asyncio
    async def test_classify_normalizes_confidence(
        self, classifier, mock_provider, mock_inference_response, sample_long_content
    ):
        """Test that confidence is normalized to 0.0-1.0 range."""
        mock_provider.infer.return_value = mock_inference_response(
            '{"domain": "news_journalism", "confidence": 1.5}'
        )

        result = await classifier.classify(sample_long_content)

        assert result.confidence == 1.0  # Should be clamped

    @pytest.mark.asyncio
    async def test_classify_handles_negative_confidence(
        self, classifier, mock_provider, mock_inference_response, sample_long_content
    ):
        """Test that negative confidence is clamped to 0.0."""
        mock_provider.infer.return_value = mock_inference_response(
            '{"domain": "news_journalism", "confidence": -0.5}'
        )

        result = await classifier.classify(sample_long_content)

        assert result.confidence == 0.0  # Should be clamped


# =============================================================================
# Content Length Tests
# =============================================================================


class TestContentLength:
    """Tests for content length handling."""

    @pytest.mark.asyncio
    async def test_short_content_returns_fallback(self, classifier):
        """Test that short content returns fallback without calling LLM."""
        result = await classifier.classify("Too short")

        assert result.domain == DEFAULT_FALLBACK_DOMAIN
        assert result.confidence == 0.0
        assert "too short" in result.reasoning.lower()

    @pytest.mark.asyncio
    async def test_minimum_length_content(self, classifier, mock_provider, mock_inference_response):
        """Test content exactly at minimum length threshold."""
        # Content just at MIN_CONTENT_LENGTH (100 chars)
        content = "A" * MIN_CONTENT_LENGTH

        mock_provider.infer.return_value = mock_inference_response(
            '{"domain": "encyclopedia_wiki", "confidence": 0.6}'
        )

        result = await classifier.classify(content)

        # Should proceed with classification
        mock_provider.infer.assert_called_once()
        assert result.domain == "encyclopedia_wiki"

    @pytest.mark.asyncio
    async def test_content_truncated_for_long_text(
        self, classifier, mock_provider, mock_inference_response
    ):
        """Test that very long content is truncated."""
        # Content much longer than MAX_CONTENT_FOR_CLASSIFICATION
        long_content = "X" * (MAX_CONTENT_FOR_CLASSIFICATION * 2)

        mock_provider.infer.return_value = mock_inference_response(
            '{"domain": "technical_documentation", "confidence": 0.8}'
        )

        await classifier.classify(long_content)

        # Verify the prompt was built with truncated content
        call_args = mock_provider.infer.call_args
        request = call_args[0][0]
        # The prompt should not contain the full long content
        assert len(request.prompt) < len(long_content)


# =============================================================================
# Sanitization Tests
# =============================================================================


class TestSanitization:
    """Tests for content sanitization."""

    @pytest.mark.asyncio
    async def test_sanitizes_email_addresses(
        self, classifier, mock_provider, mock_inference_response
    ):
        """Test that email addresses are replaced."""
        content = (
            "Contact john.doe@example.com for support. Also admin@company.org is available. " * 5
        )

        mock_provider.infer.return_value = mock_inference_response(
            '{"domain": "news_journalism", "confidence": 0.75}'
        )

        await classifier.classify(content)

        call_args = mock_provider.infer.call_args
        request = call_args[0][0]

        # Original emails should not be in the prompt
        assert "john.doe@example.com" not in request.prompt
        assert "admin@company.org" not in request.prompt
        # Replacement should be there
        assert "[EMAIL]" in request.prompt

    @pytest.mark.asyncio
    async def test_sanitizes_phone_numbers(
        self, classifier, mock_provider, mock_inference_response
    ):
        """Test that phone numbers are replaced."""
        content = (
            "Call us at 555-123-4567 or 555.987.6543 for assistance. Some more content here. " * 5
        )

        mock_provider.infer.return_value = mock_inference_response(
            '{"domain": "news_journalism", "confidence": 0.7}'
        )

        await classifier.classify(content)

        call_args = mock_provider.infer.call_args
        request = call_args[0][0]

        assert "555-123-4567" not in request.prompt
        assert "555.987.6543" not in request.prompt
        assert "[PHONE]" in request.prompt

    @pytest.mark.asyncio
    async def test_sanitizes_ssn_patterns(self, classifier, mock_provider, mock_inference_response):
        """Test that SSN-like patterns are replaced."""
        content = (
            "SSN: 123-45-6789 is sensitive. Also 987-65-4321 should be hidden. "
            "Additional content for length. " * 5
        )

        mock_provider.infer.return_value = mock_inference_response(
            '{"domain": "encyclopedia_wiki", "confidence": 0.65}'
        )

        await classifier.classify(content)

        call_args = mock_provider.infer.call_args
        request = call_args[0][0]

        assert "123-45-6789" not in request.prompt
        assert "987-65-4321" not in request.prompt
        assert "[REDACTED]" in request.prompt

    @pytest.mark.asyncio
    async def test_sanitizes_credit_card_patterns(
        self, classifier, mock_provider, mock_inference_response
    ):
        """Test that credit card-like patterns are replaced."""
        content = (
            "Card: 1234-5678-9012-3456 is on file. "
            "Another card 4111 1111 1111 1111 here. "
            "More content for length requirements. " * 3
        )

        mock_provider.infer.return_value = mock_inference_response(
            '{"domain": "news_journalism", "confidence": 0.7}'
        )

        await classifier.classify(content)

        call_args = mock_provider.infer.call_args
        request = call_args[0][0]

        assert "1234-5678-9012-3456" not in request.prompt
        assert "[REDACTED]" in request.prompt


class TestSanitizationPatterns:
    """Tests for individual sanitization regex patterns."""

    def test_email_pattern_matches(self):
        """Test email pattern matches various email formats."""
        test_cases = [
            "user@example.com",
            "first.last@company.org",
            "name+tag@domain.co.uk",
            "123@test.io",
        ]
        for email in test_cases:
            assert EMAIL_PATTERN.search(email) is not None, f"Failed for: {email}"

    def test_phone_pattern_matches(self):
        """Test phone pattern matches various formats."""
        test_cases = [
            "555-123-4567",
            "555.123.4567",
            "5551234567",
        ]
        for phone in test_cases:
            assert PHONE_PATTERN.search(phone) is not None, f"Failed for: {phone}"

    def test_ssn_pattern_matches(self):
        """Test SSN pattern matches various formats."""
        test_cases = [
            "123-45-6789",
            "123456789",
        ]
        for ssn in test_cases:
            assert SSN_PATTERN.search(ssn) is not None, f"Failed for: {ssn}"

    def test_cc_pattern_matches(self):
        """Test credit card pattern matches various formats."""
        test_cases = [
            "1234-5678-9012-3456",
            "1234 5678 9012 3456",
            "1234567890123456",
        ]
        for cc in test_cases:
            assert CC_PATTERN.search(cc) is not None, f"Failed for: {cc}"


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Tests for error handling in classify method."""

    @pytest.mark.asyncio
    async def test_timeout_returns_fallback(self, classifier, mock_provider, sample_long_content):
        """Test timeout handling returns fallback."""
        mock_provider.infer.side_effect = TimeoutError("Request timed out")

        result = await classifier.classify(sample_long_content)

        assert result.domain == DEFAULT_FALLBACK_DOMAIN
        assert result.confidence == 0.0
        assert "timeout" in result.reasoning.lower()

    @pytest.mark.asyncio
    async def test_provider_timeout_error_returns_fallback(
        self, classifier, mock_provider, sample_long_content
    ):
        """Test provider timeout error handling."""
        from kg_builder.inference.providers.base import ProviderTimeoutError

        mock_provider.infer.side_effect = ProviderTimeoutError(
            "Request timed out", provider_type="ollama"
        )

        result = await classifier.classify(sample_long_content)

        assert result.domain == DEFAULT_FALLBACK_DOMAIN
        assert result.confidence == 0.0
        assert "timeout" in result.reasoning.lower()

    @pytest.mark.asyncio
    async def test_connection_error_returns_fallback(
        self, classifier, mock_provider, sample_long_content
    ):
        """Test connection error handling."""
        from kg_builder.inference.providers.base import ProviderConnectionError

        mock_provider.infer.side_effect = ProviderConnectionError(
            "Connection refused", provider_type="ollama"
        )

        result = await classifier.classify(sample_long_content)

        assert result.domain == DEFAULT_FALLBACK_DOMAIN
        assert result.confidence == 0.0
        assert "connection" in result.reasoning.lower()

    @pytest.mark.asyncio
    async def test_generic_exception_returns_fallback(
        self, classifier, mock_provider, sample_long_content
    ):
        """Test generic exception handling."""
        mock_provider.infer.side_effect = RuntimeError("Something went wrong")

        result = await classifier.classify(sample_long_content)

        assert result.domain == DEFAULT_FALLBACK_DOMAIN
        assert result.confidence == 0.0
        assert "error" in result.reasoning.lower()

    @pytest.mark.asyncio
    async def test_invalid_json_returns_fallback(
        self, classifier, mock_provider, mock_inference_response, sample_long_content
    ):
        """Test handling of invalid JSON response."""
        mock_provider.infer.return_value = mock_inference_response("Not valid JSON at all")

        result = await classifier.classify(sample_long_content)

        assert result.domain == DEFAULT_FALLBACK_DOMAIN
        assert result.confidence == 0.0
        # The error message may contain "no json" or "classification error"
        assert "json" in result.reasoning.lower() or "error" in result.reasoning.lower()

    @pytest.mark.asyncio
    async def test_json_without_required_fields_returns_fallback(
        self, classifier, mock_provider, mock_inference_response, sample_long_content
    ):
        """Test handling of JSON without required fields."""
        mock_provider.infer.return_value = mock_inference_response('{"foo": "bar", "baz": 123}')

        result = await classifier.classify(sample_long_content)

        # Should use fallback domain (from missing "domain" field)
        assert result.domain == DEFAULT_FALLBACK_DOMAIN


# =============================================================================
# Unknown Domain Tests
# =============================================================================


class TestUnknownDomainHandling:
    """Tests for handling unknown domains in responses."""

    @pytest.mark.asyncio
    async def test_unknown_domain_returns_fallback(
        self, classifier, mock_provider, mock_inference_response, sample_long_content
    ):
        """Test that unknown domain falls back gracefully."""
        mock_provider.infer.return_value = mock_inference_response(
            '{"domain": "unknown_domain_xyz", "confidence": 0.95, '
            '"reasoning": "This domain does not exist"}'
        )

        result = await classifier.classify(sample_long_content)

        assert result.domain == DEFAULT_FALLBACK_DOMAIN
        # Confidence should be reduced
        assert result.confidence < 0.95
        assert "unknown" in result.reasoning.lower()


# =============================================================================
# Confidence Threshold Tests
# =============================================================================


class TestConfidenceThreshold:
    """Tests for confidence threshold enforcement."""

    @pytest.mark.asyncio
    async def test_low_confidence_uses_fallback(
        self, mock_provider, mock_registry, mock_inference_response, sample_long_content
    ):
        """Test that low confidence classification uses fallback domain."""
        classifier = ContentClassifier(
            inference_provider=mock_provider,
            registry=mock_registry,
            confidence_threshold=0.7,
        )

        mock_provider.infer.return_value = mock_inference_response(
            '{"domain": "literature_fiction", "confidence": 0.5, "reasoning": "Not very sure"}'
        )

        result = await classifier.classify(sample_long_content)

        # Should use fallback due to low confidence
        assert result.domain == DEFAULT_FALLBACK_DOMAIN
        assert result.confidence == 0.5  # Original confidence preserved
        assert "low confidence" in result.reasoning.lower()
        # Should include alternative
        assert result.alternatives is not None
        assert len(result.alternatives) > 0
        assert result.alternatives[0]["domain"] == "literature_fiction"

    @pytest.mark.asyncio
    async def test_above_threshold_uses_original(
        self, mock_provider, mock_registry, mock_inference_response, sample_long_content
    ):
        """Test that above-threshold classification uses original domain."""
        classifier = ContentClassifier(
            inference_provider=mock_provider,
            registry=mock_registry,
            confidence_threshold=0.6,
        )

        mock_provider.infer.return_value = mock_inference_response(
            '{"domain": "literature_fiction", "confidence": 0.8}'
        )

        result = await classifier.classify(sample_long_content)

        assert result.domain == "literature_fiction"
        assert result.confidence == 0.8

    @pytest.mark.asyncio
    async def test_exact_threshold_uses_original(
        self, mock_provider, mock_registry, mock_inference_response, sample_long_content
    ):
        """Test that exact threshold classification uses original domain."""
        classifier = ContentClassifier(
            inference_provider=mock_provider,
            registry=mock_registry,
            confidence_threshold=0.7,
        )

        mock_provider.infer.return_value = mock_inference_response(
            '{"domain": "news_journalism", "confidence": 0.7}'
        )

        result = await classifier.classify(sample_long_content)

        assert result.domain == "news_journalism"
        assert result.confidence == 0.7


# =============================================================================
# Prompt Building Tests
# =============================================================================


class TestPromptBuilding:
    """Tests for classification prompt building."""

    @pytest.mark.asyncio
    async def test_prompt_includes_all_domains(
        self, classifier, mock_provider, mock_inference_response, sample_long_content
    ):
        """Test that the prompt includes all available domains."""
        mock_provider.infer.return_value = mock_inference_response(
            '{"domain": "technical_documentation", "confidence": 0.9}'
        )

        await classifier.classify(sample_long_content)

        call_args = mock_provider.infer.call_args
        request = call_args[0][0]

        # Check that all domains are in the prompt
        assert "technical_documentation" in request.prompt
        assert "literature_fiction" in request.prompt
        assert "news_journalism" in request.prompt
        assert "encyclopedia_wiki" in request.prompt

    @pytest.mark.asyncio
    async def test_prompt_includes_content(
        self, classifier, mock_provider, mock_inference_response
    ):
        """Test that the prompt includes the content to classify."""
        content = (
            "This is unique content about machine learning "
            "and neural networks that should appear in the prompt. " * 3
        )

        mock_provider.infer.return_value = mock_inference_response(
            '{"domain": "technical_documentation", "confidence": 0.85}'
        )

        await classifier.classify(content)

        call_args = mock_provider.infer.call_args
        request = call_args[0][0]

        assert "machine learning" in request.prompt
        assert "neural networks" in request.prompt


# =============================================================================
# Convenience Function Tests
# =============================================================================


class TestClassifyContentFunction:
    """Tests for the classify_content convenience function."""

    @pytest.mark.asyncio
    async def test_classify_content_function(self, mock_provider, mock_inference_response):
        """Test the convenience function works correctly."""
        content = "A" * 150  # Long enough content

        mock_provider.infer.return_value = mock_inference_response(
            '{"domain": "encyclopedia_wiki", "confidence": 0.75}'
        )

        with patch("kg_builder.extraction.classifier.get_domain_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.list_domains.return_value = []
            mock_registry.has_domain.return_value = True
            mock_get_registry.return_value = mock_registry

            result = await classify_content(
                content=content,
                inference_provider=mock_provider,
            )

            assert isinstance(result, ClassificationResult)
            assert result.domain == "encyclopedia_wiki"
            assert result.confidence == 0.75

    @pytest.mark.asyncio
    async def test_classify_content_with_custom_threshold(
        self, mock_provider, mock_inference_response
    ):
        """Test convenience function with custom confidence threshold."""
        content = "A" * 150

        mock_provider.infer.return_value = mock_inference_response(
            '{"domain": "literature_fiction", "confidence": 0.4}'
        )

        with patch("kg_builder.extraction.classifier.get_domain_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.list_domains.return_value = []
            mock_registry.has_domain.return_value = True
            mock_get_registry.return_value = mock_registry

            result = await classify_content(
                content=content,
                inference_provider=mock_provider,
                confidence_threshold=0.3,  # Lower threshold
            )

            # Should use original domain since above threshold
            assert result.domain == "literature_fiction"

    @pytest.mark.asyncio
    async def test_classify_content_with_custom_fallback(
        self, mock_provider, mock_inference_response
    ):
        """Test convenience function with custom fallback domain."""
        content = "A" * 150

        mock_provider.infer.return_value = mock_inference_response(
            '{"domain": "unknown_xyz", "confidence": 0.9}'
        )

        with patch("kg_builder.extraction.classifier.get_domain_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.list_domains.return_value = []
            mock_registry.has_domain.return_value = False  # Unknown domain
            mock_get_registry.return_value = mock_registry

            result = await classify_content(
                content=content,
                inference_provider=mock_provider,
                fallback_domain="news_journalism",
            )

            # Should use custom fallback
            assert result.domain == "news_journalism"


# =============================================================================
# JSON Extraction Tests
# =============================================================================


class TestJsonExtraction:
    """Tests for JSON extraction from LLM responses."""

    @pytest.mark.asyncio
    async def test_extracts_json_with_surrounding_text(
        self, classifier, mock_provider, mock_inference_response, sample_long_content
    ):
        """Test JSON extraction when LLM includes extra text."""
        mock_provider.infer.return_value = mock_inference_response(
            "Here is my analysis:\n"
            '{"domain": "technical_documentation", "confidence": 0.88, '
            '"reasoning": "Contains code examples"}\n'
            "That's my classification."
        )

        result = await classifier.classify(sample_long_content)

        assert result.domain == "technical_documentation"
        assert result.confidence == 0.88

    @pytest.mark.asyncio
    async def test_extracts_json_with_markdown_code_block(
        self, classifier, mock_provider, mock_inference_response, sample_long_content
    ):
        """Test JSON extraction from markdown code blocks."""
        mock_provider.infer.return_value = mock_inference_response(
            '```json\n{"domain": "news_journalism", "confidence": 0.77}\n```'
        )

        result = await classifier.classify(sample_long_content)

        assert result.domain == "news_journalism"
        assert result.confidence == 0.77

    @pytest.mark.asyncio
    async def test_handles_missing_reasoning_field(
        self, classifier, mock_provider, mock_inference_response, sample_long_content
    ):
        """Test handling when reasoning field is missing."""
        mock_provider.infer.return_value = mock_inference_response(
            '{"domain": "literature_fiction", "confidence": 0.82}'
        )

        result = await classifier.classify(sample_long_content)

        assert result.domain == "literature_fiction"
        assert result.confidence == 0.82
        assert result.reasoning is None


# =============================================================================
# Integration-Like Tests
# =============================================================================


class TestClassificationFlow:
    """End-to-end style tests for complete classification flow."""

    @pytest.mark.asyncio
    async def test_full_classification_flow(
        self, mock_provider, mock_registry, mock_inference_response
    ):
        """Test complete classification flow from start to finish."""
        # Create classifier
        classifier = ContentClassifier(
            inference_provider=mock_provider,
            registry=mock_registry,
            confidence_threshold=0.6,
        )

        # Sample technical content
        content = """
        This is a comprehensive API reference documentation for the
        FastAPI web framework. It covers decorators like @app.get(),
        dependency injection with Depends(), and Pydantic model validation.
        The documentation includes code examples demonstrating how to
        create RESTful endpoints with proper type hints and validation.
        """

        # Mock LLM response
        mock_provider.infer.return_value = mock_inference_response(
            '{"domain": "technical_documentation", "confidence": 0.92, '
            '"reasoning": "Contains API reference, code examples, and '
            'framework documentation patterns"}'
        )

        # Execute classification
        result = await classifier.classify(content, tenant_id="test-tenant")

        # Verify result
        assert result.domain == "technical_documentation"
        assert result.confidence == 0.92
        assert "API reference" in result.reasoning or result.reasoning is not None

        # Verify provider was called
        mock_provider.infer.assert_called_once()

        # Verify the request had appropriate settings
        call_args = mock_provider.infer.call_args
        request = call_args[0][0]
        assert request.temperature == 0.1  # Low temperature for classification
        assert request.max_tokens == 500
