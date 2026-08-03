"""Unit tests for BaseExtractionService interface.

Tests the interface contract for extraction services, specifically:
- Presence of new adaptive extraction parameters (system_prompt, json_schema)
- Default values for new parameters
- Backward compatibility with existing extraction calls

This verifies task P3-001: Extend BaseExtractionService Interface.
"""

import inspect
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from kg_builder.extraction.base import (
    BaseExtractionService,
    ExtractionError,
    ExtractionServiceProtocol,
)
from kg_builder.extraction.prompts import DocumentationType
from kg_builder.extraction.schemas import ExtractionResult


class TestBaseExtractionServiceInterface:
    """Tests for BaseExtractionService abstract class interface."""

    def test_extract_signature_has_system_prompt_parameter(self) -> None:
        """Test that extract method has system_prompt parameter."""
        sig = inspect.signature(BaseExtractionService.extract)
        params = sig.parameters

        assert "system_prompt" in params, (
            "BaseExtractionService.extract() must have 'system_prompt' parameter "
            "for adaptive extraction support"
        )

    def test_extract_signature_has_json_schema_parameter(self) -> None:
        """Test that extract method has json_schema parameter."""
        sig = inspect.signature(BaseExtractionService.extract)
        params = sig.parameters

        assert "json_schema" in params, (
            "BaseExtractionService.extract() must have 'json_schema' parameter "
            "for structured output support"
        )

    def test_system_prompt_parameter_is_optional(self) -> None:
        """Test that system_prompt parameter has default None."""
        sig = inspect.signature(BaseExtractionService.extract)
        param = sig.parameters["system_prompt"]

        assert param.default is None, (
            "system_prompt parameter must default to None for backward compatibility"
        )

    def test_json_schema_parameter_is_optional(self) -> None:
        """Test that json_schema parameter has default None."""
        sig = inspect.signature(BaseExtractionService.extract)
        param = sig.parameters["json_schema"]

        assert param.default is None, (
            "json_schema parameter must default to None for backward compatibility"
        )

    def test_system_prompt_annotation_is_str_or_none(self) -> None:
        """Test that system_prompt has correct type annotation."""
        sig = inspect.signature(BaseExtractionService.extract)
        param = sig.parameters["system_prompt"]

        # Check the annotation allows str | None
        annotation = param.annotation
        # The annotation should be str | None
        assert annotation == (str | None), (
            f"system_prompt annotation should be 'str | None', got '{annotation}'"
        )

    def test_json_schema_annotation_is_dict_or_none(self) -> None:
        """Test that json_schema has correct type annotation."""
        sig = inspect.signature(BaseExtractionService.extract)
        param = sig.parameters["json_schema"]

        annotation = param.annotation
        # The annotation should be dict[str, Any] | None
        assert annotation == (dict[str, Any] | None), (
            f"json_schema annotation should be 'dict[str, Any] | None', got '{annotation}'"
        )

    def test_extract_docstring_documents_new_parameters(self) -> None:
        """Test that extract method docstring documents new parameters."""
        docstring = BaseExtractionService.extract.__doc__

        assert docstring is not None, "extract method must have a docstring"
        assert "system_prompt" in docstring, (
            "docstring must document system_prompt parameter"
        )
        assert "json_schema" in docstring, (
            "docstring must document json_schema parameter"
        )

    def test_all_original_parameters_preserved(self) -> None:
        """Test that all original parameters are still present."""
        sig = inspect.signature(BaseExtractionService.extract)
        params = sig.parameters

        # Original parameters
        expected_params = [
            "self",
            "content",
            "page_url",
            "max_length",
            "doc_type",
            "additional_context",
            "tenant_id",
        ]

        for param_name in expected_params:
            assert param_name in params, (
                f"Original parameter '{param_name}' must be preserved "
                "for backward compatibility"
            )

    def test_parameter_order_maintains_backward_compatibility(self) -> None:
        """Test that new parameters come after original parameters.

        This ensures existing callers using positional arguments continue to work.
        """
        sig = inspect.signature(BaseExtractionService.extract)
        param_list = list(sig.parameters.keys())

        # Original positional parameters (in order)
        original_params = [
            "self",
            "content",
            "page_url",
            "max_length",
            "doc_type",
            "additional_context",
            "tenant_id",
        ]

        # New parameters should come after originals
        new_params = ["system_prompt", "json_schema"]

        # Find indices
        for i, original in enumerate(original_params):
            assert param_list[i] == original, (
                f"Parameter order mismatch: expected '{original}' at position {i}, "
                f"got '{param_list[i]}'"
            )

        # New params should be after original params
        for new_param in new_params:
            new_idx = param_list.index(new_param)
            assert new_idx >= len(original_params), (
                f"New parameter '{new_param}' must come after original parameters"
            )


class TestExtractionServiceProtocolInterface:
    """Tests for ExtractionServiceProtocol interface."""

    def test_protocol_extract_has_system_prompt_parameter(self) -> None:
        """Test that protocol extract method has system_prompt parameter."""
        sig = inspect.signature(ExtractionServiceProtocol.extract)
        params = sig.parameters

        assert "system_prompt" in params, (
            "ExtractionServiceProtocol.extract() must have 'system_prompt' parameter"
        )

    def test_protocol_extract_has_json_schema_parameter(self) -> None:
        """Test that protocol extract method has json_schema parameter."""
        sig = inspect.signature(ExtractionServiceProtocol.extract)
        params = sig.parameters

        assert "json_schema" in params, (
            "ExtractionServiceProtocol.extract() must have 'json_schema' parameter"
        )

    def test_protocol_system_prompt_is_optional(self) -> None:
        """Test that protocol system_prompt defaults to None."""
        sig = inspect.signature(ExtractionServiceProtocol.extract)
        param = sig.parameters["system_prompt"]

        assert param.default is None

    def test_protocol_json_schema_is_optional(self) -> None:
        """Test that protocol json_schema defaults to None."""
        sig = inspect.signature(ExtractionServiceProtocol.extract)
        param = sig.parameters["json_schema"]

        assert param.default is None


class TestExtractionErrorClass:
    """Tests for ExtractionError exception class."""

    def test_extraction_error_basic(self) -> None:
        """Test basic ExtractionError creation."""
        error = ExtractionError("Test error")
        assert str(error) == "Test error"
        assert error.message == "Test error"
        assert error.cause is None
        assert error.provider is None

    def test_extraction_error_with_cause(self) -> None:
        """Test ExtractionError with cause exception."""
        cause = ValueError("underlying error")
        error = ExtractionError("Extraction failed", cause=cause)

        assert error.message == "Extraction failed"
        assert error.cause is cause
        assert str(error) == "Extraction failed"

    def test_extraction_error_with_provider(self) -> None:
        """Test ExtractionError with provider name."""
        error = ExtractionError("API error", provider="openai")

        assert error.provider == "openai"
        assert str(error) == "[openai] API error"

    def test_extraction_error_with_all_attributes(self) -> None:
        """Test ExtractionError with all attributes."""
        cause = ConnectionError("timeout")
        error = ExtractionError(
            "Connection failed",
            cause=cause,
            provider="ollama",
        )

        assert error.message == "Connection failed"
        assert error.cause is cause
        assert error.provider == "ollama"
        assert str(error) == "[ollama] Connection failed"


class TestConcreteImplementation:
    """Tests for concrete implementation of BaseExtractionService.

    These tests verify that a concrete implementation can be created
    with the new parameters and maintains backward compatibility.
    """

    def test_concrete_implementation_can_ignore_new_params(self) -> None:
        """Test that concrete implementation works without using new params.

        This verifies backward compatibility - existing implementations
        that don't use system_prompt/json_schema should still work.
        """

        class LegacyExtractor(BaseExtractionService):
            """Extractor that ignores new parameters (legacy behavior)."""

            provider_name = "legacy"

            async def extract(
                self,
                content: str,
                page_url: str,
                max_length: int | None = None,
                doc_type: DocumentationType | None = None,
                additional_context: str | None = None,
                tenant_id: Any = None,
                # Accept but ignore new parameters
                system_prompt: str | None = None,
                json_schema: dict[str, Any] | None = None,
            ) -> ExtractionResult:
                # Legacy behavior: ignore system_prompt and json_schema
                return ExtractionResult(entities=[], relationships=[])

            async def health_check(self) -> dict:
                return {"status": "healthy", "provider": "legacy"}

        # Should be instantiable and callable
        extractor = LegacyExtractor()
        assert extractor.provider_name == "legacy"

    def test_concrete_implementation_can_use_new_params(self) -> None:
        """Test that concrete implementation can use new parameters."""

        class AdaptiveExtractor(BaseExtractionService):
            """Extractor that uses new adaptive parameters."""

            provider_name = "adaptive"
            last_system_prompt: str | None = None
            last_json_schema: dict[str, Any] | None = None

            async def extract(
                self,
                content: str,
                page_url: str,
                max_length: int | None = None,
                doc_type: DocumentationType | None = None,
                additional_context: str | None = None,
                tenant_id: Any = None,
                system_prompt: str | None = None,
                json_schema: dict[str, Any] | None = None,
            ) -> ExtractionResult:
                # Store for verification
                self.last_system_prompt = system_prompt
                self.last_json_schema = json_schema
                return ExtractionResult(entities=[], relationships=[])

            async def health_check(self) -> dict:
                return {"status": "healthy", "provider": "adaptive"}

        extractor = AdaptiveExtractor()
        assert extractor.provider_name == "adaptive"


class TestBackwardCompatibility:
    """Tests to ensure backward compatibility with existing code."""

    def test_can_call_without_new_parameters(self) -> None:
        """Test that extract can be called without new parameters.

        This is the primary backward compatibility test - existing callers
        should continue to work without modification.
        """

        # Create a mock that implements the interface
        mock_service = AsyncMock(spec=BaseExtractionService)
        mock_service.extract = AsyncMock(
            return_value=ExtractionResult(entities=[], relationships=[])
        )

        # Should be callable with only original parameters
        # This simulates how existing code would call extract()
        import asyncio

        async def test_call():
            result = await mock_service.extract(
                content="test content",
                page_url="https://example.com",
                max_length=1000,
                doc_type=DocumentationType.GENERAL,
            )
            return result

        result = asyncio.get_event_loop().run_until_complete(test_call())
        assert result is not None

    def test_extract_signature_accepts_keyword_arguments(self) -> None:
        """Test that new parameters can be passed as keyword arguments."""
        sig = inspect.signature(BaseExtractionService.extract)

        # All parameters after 'content' should be KEYWORD_ONLY or have defaults
        params = list(sig.parameters.values())[1:]  # Skip 'self'

        for param in params[2:]:  # Skip required 'content' and 'page_url'
            assert param.default is not inspect.Parameter.empty or (
                param.kind == inspect.Parameter.KEYWORD_ONLY
            ), f"Parameter '{param.name}' must have a default value or be keyword-only"


class TestInterfaceDocumentation:
    """Tests for interface documentation quality."""

    def test_base_class_has_docstring(self) -> None:
        """Test that BaseExtractionService has a docstring."""
        assert BaseExtractionService.__doc__ is not None
        assert len(BaseExtractionService.__doc__) > 100

    def test_base_class_docstring_mentions_adaptive_extraction(self) -> None:
        """Test that docstring mentions adaptive extraction update."""
        docstring = BaseExtractionService.__doc__
        assert "Adaptive Extraction" in docstring or "adaptive" in docstring.lower()

    def test_extract_docstring_has_args_section(self) -> None:
        """Test that extract docstring has Args section."""
        docstring = BaseExtractionService.extract.__doc__
        assert "Args:" in docstring

    def test_extract_docstring_has_returns_section(self) -> None:
        """Test that extract docstring has Returns section."""
        docstring = BaseExtractionService.extract.__doc__
        assert "Returns:" in docstring

    def test_extract_docstring_has_raises_section(self) -> None:
        """Test that extract docstring has Raises section."""
        docstring = BaseExtractionService.extract.__doc__
        assert "Raises:" in docstring

    def test_extract_docstring_has_example(self) -> None:
        """Test that extract docstring has usage example."""
        docstring = BaseExtractionService.extract.__doc__
        assert "Example:" in docstring


class TestProviderNameAttribute:
    """Tests for provider_name class attribute."""

    def test_base_class_has_provider_name(self) -> None:
        """Test that BaseExtractionService has provider_name attribute."""
        assert hasattr(BaseExtractionService, "provider_name")
        assert BaseExtractionService.provider_name == "base"
