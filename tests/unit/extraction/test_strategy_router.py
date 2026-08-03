"""Unit tests for ExtractionStrategyRouter.

Tests the strategy routing functionality including:
- Legacy strategy returns non-adaptive strategy
- Manual strategy uses job's content_domain
- Auto_detect strategy classifies content and updates job
- Error handling for missing configuration
- Job update callback behavior
- Convenience functions
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kg_builder.extraction.domains.models import (
    ClassificationResult,
    ConfidenceThresholds,
    DomainSchema,
    EntityTypeSchema,
    ExtractionStrategy,
    RelationshipTypeSchema,
)

# Import directly from modules to avoid database dependencies
from kg_builder.extraction.strategy_router import (
    ExtractionStrategyRouter,
    get_strategy_router,
    reset_strategy_router,
    route_extraction_strategy,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_job():
    """Create a mock scraping job."""
    job = MagicMock()
    job.id = uuid.uuid4()
    job.extraction_strategy = "legacy"
    job.content_domain = None
    return job


@pytest.fixture
def mock_provider():
    """Create a mock inference provider."""
    provider = AsyncMock()
    return provider


@pytest.fixture
def mock_registry():
    """Create a mock domain schema registry."""
    registry = MagicMock()

    # Create a sample schema
    sample_schema = DomainSchema(
        domain_id="test_domain",
        display_name="Test Domain",
        description="A test domain",
        entity_types=[
            EntityTypeSchema(
                id="test_entity",
                description="A test entity type",
            ),
        ],
        relationship_types=[
            RelationshipTypeSchema(
                id="test_relationship",
                description="A test relationship type",
            ),
        ],
        extraction_prompt_template="Extract: {entity_descriptions}\n{relationship_descriptions}",
        confidence_thresholds=ConfidenceThresholds(
            entity_extraction=0.7,
            relationship_extraction=0.6,
        ),
    )

    registry.get_schema.return_value = sample_schema
    registry.has_domain.return_value = True

    return registry


@pytest.fixture
def mock_classifier():
    """Create a mock content classifier."""
    classifier = AsyncMock()
    classifier.classify.return_value = ClassificationResult(
        domain="test_domain",
        confidence=0.85,
        reasoning="Test classification",
    )
    return classifier


@pytest.fixture
def mock_prompt_generator():
    """Create a mock prompt generator."""
    generator = MagicMock()
    generator.generate_system_prompt.return_value = "Test system prompt"
    generator.generate_json_schema.return_value = {
        "type": "object",
        "properties": {
            "entities": {"type": "array"},
            "relationships": {"type": "array"},
        },
    }
    return generator


@pytest.fixture
def router(mock_provider, mock_registry, mock_classifier, mock_prompt_generator):
    """Create a router with all mocked dependencies."""
    return ExtractionStrategyRouter(
        inference_provider=mock_provider,
        classifier=mock_classifier,
        prompt_generator=mock_prompt_generator,
        registry=mock_registry,
    )


@pytest.fixture
def sample_content():
    """Sample content for testing."""
    return (
        "This is a comprehensive test document about various topics. "
        "It contains enough text for classification purposes. "
        "The content covers multiple subjects including technology, "
        "literature, and general knowledge. " * 5
    )


# =============================================================================
# Initialization Tests
# =============================================================================


class TestRouterInit:
    """Tests for ExtractionStrategyRouter initialization."""

    def test_init_with_inference_provider(self, mock_provider):
        """Test initialization with inference provider."""
        router = ExtractionStrategyRouter(inference_provider=mock_provider)

        assert router._inference_provider is mock_provider
        assert router._classifier is None
        assert router._prompt_generator is None
        assert router._registry is None

    def test_init_with_all_dependencies(
        self, mock_provider, mock_classifier, mock_prompt_generator, mock_registry
    ):
        """Test initialization with all pre-configured dependencies."""
        router = ExtractionStrategyRouter(
            inference_provider=mock_provider,
            classifier=mock_classifier,
            prompt_generator=mock_prompt_generator,
            registry=mock_registry,
        )

        assert router._classifier is mock_classifier
        assert router._prompt_generator is mock_prompt_generator
        assert router._registry is mock_registry

    def test_init_with_custom_confidence_threshold(self, mock_provider):
        """Test initialization with custom confidence threshold."""
        router = ExtractionStrategyRouter(
            inference_provider=mock_provider,
            confidence_threshold=0.8,
        )

        assert router._confidence_threshold == 0.8


# =============================================================================
# Legacy Strategy Tests
# =============================================================================


class TestLegacyStrategy:
    """Tests for legacy extraction strategy routing."""

    @pytest.mark.asyncio
    async def test_legacy_returns_non_adaptive_strategy(
        self, router, mock_job, sample_content
    ):
        """Test that legacy strategy returns non-adaptive ExtractionStrategy."""
        mock_job.extraction_strategy = "legacy"

        strategy = await router.route(mock_job, sample_content)

        assert isinstance(strategy, ExtractionStrategy)
        assert strategy.is_adaptive is False
        assert strategy.domain_id is None
        assert strategy.system_prompt is None
        assert strategy.json_schema is None

    @pytest.mark.asyncio
    async def test_legacy_does_not_classify_content(
        self, router, mock_job, mock_classifier, sample_content
    ):
        """Test that legacy strategy does not invoke classifier."""
        mock_job.extraction_strategy = "legacy"

        await router.route(mock_job, sample_content)

        mock_classifier.classify.assert_not_called()

    @pytest.mark.asyncio
    async def test_legacy_does_not_check_registry(
        self, router, mock_job, mock_registry, sample_content
    ):
        """Test that legacy strategy does not check domain registry."""
        mock_job.extraction_strategy = "legacy"

        await router.route(mock_job, sample_content)

        mock_registry.get_schema.assert_not_called()


# =============================================================================
# Manual Strategy Tests
# =============================================================================


class TestManualStrategy:
    """Tests for manual extraction strategy routing."""

    @pytest.mark.asyncio
    async def test_manual_uses_job_content_domain(
        self, router, mock_job, mock_registry, sample_content
    ):
        """Test that manual strategy uses job's content_domain."""
        mock_job.extraction_strategy = "manual"
        mock_job.content_domain = "test_domain"

        strategy = await router.route(mock_job, sample_content)

        mock_registry.get_schema.assert_called_once_with("test_domain")
        assert strategy.is_adaptive is True
        assert strategy.domain_id == "test_domain"

    @pytest.mark.asyncio
    async def test_manual_generates_system_prompt(
        self, router, mock_job, mock_prompt_generator, sample_content
    ):
        """Test that manual strategy generates system prompt."""
        mock_job.extraction_strategy = "manual"
        mock_job.content_domain = "test_domain"

        strategy = await router.route(mock_job, sample_content)

        mock_prompt_generator.generate_system_prompt.assert_called_once()
        assert strategy.system_prompt == "Test system prompt"

    @pytest.mark.asyncio
    async def test_manual_generates_json_schema(
        self, router, mock_job, mock_prompt_generator, sample_content
    ):
        """Test that manual strategy generates JSON schema."""
        mock_job.extraction_strategy = "manual"
        mock_job.content_domain = "test_domain"

        strategy = await router.route(mock_job, sample_content)

        mock_prompt_generator.generate_json_schema.assert_called_once()
        assert strategy.json_schema is not None
        assert "entities" in strategy.json_schema["properties"]

    @pytest.mark.asyncio
    async def test_manual_does_not_classify_content(
        self, router, mock_job, mock_classifier, sample_content
    ):
        """Test that manual strategy does not invoke classifier."""
        mock_job.extraction_strategy = "manual"
        mock_job.content_domain = "test_domain"

        await router.route(mock_job, sample_content)

        mock_classifier.classify.assert_not_called()

    @pytest.mark.asyncio
    async def test_manual_without_domain_raises_error(
        self, router, mock_job, sample_content
    ):
        """Test that manual strategy without content_domain raises ValueError."""
        mock_job.extraction_strategy = "manual"
        mock_job.content_domain = None

        with pytest.raises(ValueError, match="requires content_domain"):
            await router.route(mock_job, sample_content)

    @pytest.mark.asyncio
    async def test_manual_with_invalid_domain_raises_error(
        self, router, mock_job, mock_registry, sample_content
    ):
        """Test that manual strategy with unknown domain raises KeyError."""
        mock_job.extraction_strategy = "manual"
        mock_job.content_domain = "unknown_domain"
        mock_registry.get_schema.side_effect = KeyError("Unknown domain")

        with pytest.raises(KeyError):
            await router.route(mock_job, sample_content)

    @pytest.mark.asyncio
    async def test_manual_includes_confidence_thresholds(
        self, router, mock_job, sample_content
    ):
        """Test that manual strategy includes confidence thresholds from schema."""
        mock_job.extraction_strategy = "manual"
        mock_job.content_domain = "test_domain"

        strategy = await router.route(mock_job, sample_content)

        assert strategy.confidence_thresholds is not None
        assert strategy.confidence_thresholds.entity_extraction == 0.7
        assert strategy.confidence_thresholds.relationship_extraction == 0.6


# =============================================================================
# Auto Detect Strategy Tests
# =============================================================================


class TestAutoDetectStrategy:
    """Tests for auto_detect extraction strategy routing."""

    @pytest.mark.asyncio
    async def test_auto_detect_classifies_content(
        self, router, mock_job, mock_classifier, sample_content
    ):
        """Test that auto_detect strategy classifies content."""
        mock_job.extraction_strategy = "auto_detect"
        mock_job.content_domain = None

        await router.route(mock_job, sample_content)

        mock_classifier.classify.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_detect_returns_adaptive_strategy(
        self, router, mock_job, sample_content
    ):
        """Test that auto_detect returns adaptive strategy."""
        mock_job.extraction_strategy = "auto_detect"
        mock_job.content_domain = None

        strategy = await router.route(mock_job, sample_content)

        assert strategy.is_adaptive is True
        assert strategy.domain_id == "test_domain"
        assert strategy.system_prompt is not None

    @pytest.mark.asyncio
    async def test_auto_detect_uses_classified_domain(
        self, router, mock_job, mock_classifier, mock_registry, sample_content
    ):
        """Test that auto_detect uses the classified domain."""
        mock_job.extraction_strategy = "auto_detect"
        mock_job.content_domain = None
        mock_classifier.classify.return_value = ClassificationResult(
            domain="literature_fiction",
            confidence=0.9,
            reasoning="Literary content detected",
        )

        # Update registry to return correct schema for literature_fiction
        literature_schema = DomainSchema(
            domain_id="literature_fiction",
            display_name="Literature & Fiction",
            description="Novels and plays",
            entity_types=[
                EntityTypeSchema(id="character", description="A character"),
            ],
            relationship_types=[
                RelationshipTypeSchema(id="loves", description="Love"),
            ],
            extraction_prompt_template="{entity_descriptions}\n{relationship_descriptions}",
        )
        mock_registry.get_schema.return_value = literature_schema

        strategy = await router.route(mock_job, sample_content)

        assert strategy.domain_id == "literature_fiction"

    @pytest.mark.asyncio
    async def test_auto_detect_skips_classification_if_domain_resolved(
        self, router, mock_job, mock_classifier, sample_content
    ):
        """Test that auto_detect skips classification if domain already set."""
        mock_job.extraction_strategy = "auto_detect"
        mock_job.content_domain = "test_domain"  # Already resolved

        strategy = await router.route(mock_job, sample_content)

        mock_classifier.classify.assert_not_called()
        assert strategy.domain_id == "test_domain"

    @pytest.mark.asyncio
    async def test_auto_detect_passes_tenant_id_to_classifier(
        self, router, mock_job, mock_classifier, sample_content
    ):
        """Test that auto_detect passes tenant_id to classifier."""
        mock_job.extraction_strategy = "auto_detect"
        mock_job.content_domain = None

        await router.route(mock_job, sample_content, tenant_id="tenant-123")

        mock_classifier.classify.assert_called_once_with(
            sample_content, tenant_id="tenant-123"
        )


# =============================================================================
# Job Update Callback Tests
# =============================================================================


class TestJobUpdateCallback:
    """Tests for job update callback behavior."""

    @pytest.mark.asyncio
    async def test_callback_invoked_on_auto_detect(
        self, mock_provider, mock_registry, mock_classifier, mock_prompt_generator,
        mock_job, sample_content
    ):
        """Test that callback is invoked for auto_detect classification."""
        callback = AsyncMock()
        router = ExtractionStrategyRouter(
            inference_provider=mock_provider,
            classifier=mock_classifier,
            prompt_generator=mock_prompt_generator,
            registry=mock_registry,
            job_update_callback=callback,
        )

        mock_job.extraction_strategy = "auto_detect"
        mock_job.content_domain = None

        await router.route(mock_job, sample_content)

        callback.assert_called_once()
        call_kwargs = callback.call_args[1]
        assert call_kwargs["job_id"] == str(mock_job.id)
        assert call_kwargs["content_domain"] == "test_domain"
        assert call_kwargs["classification_confidence"] == 0.85

    @pytest.mark.asyncio
    async def test_callback_includes_schema_snapshot(
        self, mock_provider, mock_registry, mock_classifier, mock_prompt_generator,
        mock_job, sample_content
    ):
        """Test that callback includes schema snapshot."""
        callback = AsyncMock()
        router = ExtractionStrategyRouter(
            inference_provider=mock_provider,
            classifier=mock_classifier,
            prompt_generator=mock_prompt_generator,
            registry=mock_registry,
            job_update_callback=callback,
        )

        mock_job.extraction_strategy = "auto_detect"
        mock_job.content_domain = None

        await router.route(mock_job, sample_content)

        call_kwargs = callback.call_args[1]
        schema_snapshot = call_kwargs["inferred_schema"]

        assert schema_snapshot is not None
        assert schema_snapshot["domain_id"] == "test_domain"
        assert "entity_types" in schema_snapshot
        assert "relationship_types" in schema_snapshot
        assert "confidence_thresholds" in schema_snapshot

    @pytest.mark.asyncio
    async def test_callback_not_invoked_for_legacy(
        self, mock_provider, mock_registry, mock_classifier, mock_prompt_generator,
        mock_job, sample_content
    ):
        """Test that callback is not invoked for legacy strategy."""
        callback = AsyncMock()
        router = ExtractionStrategyRouter(
            inference_provider=mock_provider,
            classifier=mock_classifier,
            prompt_generator=mock_prompt_generator,
            registry=mock_registry,
            job_update_callback=callback,
        )

        mock_job.extraction_strategy = "legacy"

        await router.route(mock_job, sample_content)

        callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_callback_not_invoked_for_manual(
        self, mock_provider, mock_registry, mock_classifier, mock_prompt_generator,
        mock_job, sample_content
    ):
        """Test that callback is not invoked for manual strategy."""
        callback = AsyncMock()
        router = ExtractionStrategyRouter(
            inference_provider=mock_provider,
            classifier=mock_classifier,
            prompt_generator=mock_prompt_generator,
            registry=mock_registry,
            job_update_callback=callback,
        )

        mock_job.extraction_strategy = "manual"
        mock_job.content_domain = "test_domain"

        await router.route(mock_job, sample_content)

        callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_callback_failure_does_not_break_routing(
        self, mock_provider, mock_registry, mock_classifier, mock_prompt_generator,
        mock_job, sample_content
    ):
        """Test that callback failure does not prevent strategy return."""
        callback = AsyncMock(side_effect=RuntimeError("Callback failed"))
        router = ExtractionStrategyRouter(
            inference_provider=mock_provider,
            classifier=mock_classifier,
            prompt_generator=mock_prompt_generator,
            registry=mock_registry,
            job_update_callback=callback,
        )

        mock_job.extraction_strategy = "auto_detect"
        mock_job.content_domain = None

        # Should not raise - strategy should still be returned
        strategy = await router.route(mock_job, sample_content)

        assert strategy is not None
        assert strategy.domain_id == "test_domain"


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Tests for error handling in strategy router."""

    @pytest.mark.asyncio
    async def test_unknown_strategy_falls_back_to_legacy(
        self, router, mock_job, sample_content
    ):
        """Test that unknown strategy type falls back to legacy."""
        mock_job.extraction_strategy = "unknown_strategy_xyz"

        strategy = await router.route(mock_job, sample_content)

        assert strategy.is_adaptive is False

    @pytest.mark.asyncio
    async def test_classifier_creation_without_provider_raises_error(
        self, mock_registry, mock_prompt_generator, mock_job, sample_content
    ):
        """Test that creating classifier without provider raises error."""
        router = ExtractionStrategyRouter(
            inference_provider=None,  # No provider
            registry=mock_registry,
            prompt_generator=mock_prompt_generator,
        )

        mock_job.extraction_strategy = "auto_detect"
        mock_job.content_domain = None

        with pytest.raises(ValueError, match="no inference provider"):
            await router.route(mock_job, sample_content)


# =============================================================================
# Classify Content Method Tests
# =============================================================================


class TestClassifyContentMethod:
    """Tests for the classify_content convenience method."""

    @pytest.mark.asyncio
    async def test_classify_content_uses_classifier(
        self, router, mock_classifier, sample_content
    ):
        """Test that classify_content uses the classifier."""
        result = await router.classify_content(sample_content)

        mock_classifier.classify.assert_called_once()
        assert isinstance(result, ClassificationResult)
        assert result.domain == "test_domain"

    @pytest.mark.asyncio
    async def test_classify_content_passes_tenant_id(
        self, router, mock_classifier, sample_content
    ):
        """Test that classify_content passes tenant_id."""
        await router.classify_content(sample_content, tenant_id="tenant-456")

        mock_classifier.classify.assert_called_once_with(
            sample_content, tenant_id="tenant-456"
        )


# =============================================================================
# Schema Snapshot Tests
# =============================================================================


class TestSchemaSnapshot:
    """Tests for schema snapshot creation."""

    def test_create_schema_snapshot(self, router, mock_registry):
        """Test that schema snapshot contains essential information."""
        schema = mock_registry.get_schema("test_domain")
        snapshot = router._create_schema_snapshot(schema)

        assert snapshot["domain_id"] == "test_domain"
        assert snapshot["display_name"] == "Test Domain"
        assert snapshot["version"] == "1.0.0"  # Default version
        assert "test_entity" in snapshot["entity_types"]
        assert "test_relationship" in snapshot["relationship_types"]
        assert snapshot["confidence_thresholds"]["entity_extraction"] == 0.7
        assert snapshot["confidence_thresholds"]["relationship_extraction"] == 0.6


# =============================================================================
# Convenience Function Tests
# =============================================================================


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before each test."""
        reset_strategy_router()
        yield
        reset_strategy_router()

    def test_get_strategy_router_returns_singleton(self, mock_provider):
        """Test that get_strategy_router returns same instance."""
        router1 = get_strategy_router(inference_provider=mock_provider)
        router2 = get_strategy_router()

        assert router1 is router2

    def test_reset_strategy_router(self, mock_provider):
        """Test that reset creates new instance."""
        router1 = get_strategy_router(inference_provider=mock_provider)
        reset_strategy_router()
        router2 = get_strategy_router(inference_provider=mock_provider)

        assert router1 is not router2

    @pytest.mark.asyncio
    async def test_route_extraction_strategy_function(
        self, mock_job, sample_content
    ):
        """Test the convenience function for routing."""
        mock_job.extraction_strategy = "legacy"

        with patch(
            "kg_builder.extraction.strategy_router.ExtractionStrategyRouter"
        ) as MockRouter:
            mock_router_instance = AsyncMock()
            mock_router_instance.route.return_value = ExtractionStrategy.legacy()
            MockRouter.return_value = mock_router_instance

            strategy = await route_extraction_strategy(
                job=mock_job,
                content=sample_content,
            )

            assert strategy is not None
            MockRouter.assert_called_once()
            mock_router_instance.route.assert_called_once()

    @pytest.mark.asyncio
    async def test_route_extraction_strategy_with_callback(
        self, mock_provider, mock_job, sample_content
    ):
        """Test convenience function with job update callback."""
        callback = AsyncMock()
        mock_job.extraction_strategy = "legacy"

        with patch(
            "kg_builder.extraction.strategy_router.ExtractionStrategyRouter"
        ) as MockRouter:
            mock_router_instance = AsyncMock()
            mock_router_instance.route.return_value = ExtractionStrategy.legacy()
            MockRouter.return_value = mock_router_instance

            await route_extraction_strategy(
                job=mock_job,
                content=sample_content,
                inference_provider=mock_provider,
                job_update_callback=callback,
            )

            # Verify callback was passed to router
            call_kwargs = MockRouter.call_args[1]
            assert call_kwargs["job_update_callback"] is callback


# =============================================================================
# Integration-Like Tests
# =============================================================================


class TestFullRoutingFlow:
    """End-to-end style tests for complete routing flows."""

    @pytest.mark.asyncio
    async def test_full_manual_flow(
        self, mock_provider, mock_registry, mock_prompt_generator, mock_job
    ):
        """Test complete manual strategy flow."""
        router = ExtractionStrategyRouter(
            inference_provider=mock_provider,
            registry=mock_registry,
            prompt_generator=mock_prompt_generator,
        )

        mock_job.extraction_strategy = "manual"
        mock_job.content_domain = "test_domain"

        strategy = await router.route(mock_job, "Some content")

        # Verify full strategy is built
        assert strategy.is_adaptive is True
        assert strategy.domain_id == "test_domain"
        assert strategy.system_prompt == "Test system prompt"
        assert strategy.json_schema is not None
        assert strategy.confidence_thresholds.entity_extraction == 0.7

    @pytest.mark.asyncio
    async def test_full_auto_detect_flow_with_callback(
        self, mock_provider, mock_registry, mock_classifier, mock_prompt_generator,
        mock_job, sample_content
    ):
        """Test complete auto_detect strategy flow with callback."""
        callback = AsyncMock()

        router = ExtractionStrategyRouter(
            inference_provider=mock_provider,
            registry=mock_registry,
            classifier=mock_classifier,
            prompt_generator=mock_prompt_generator,
            job_update_callback=callback,
        )

        mock_job.extraction_strategy = "auto_detect"
        mock_job.content_domain = None

        strategy = await router.route(mock_job, sample_content, tenant_id="tenant-abc")

        # Verify classification happened
        mock_classifier.classify.assert_called_once_with(
            sample_content, tenant_id="tenant-abc"
        )

        # Verify strategy is built
        assert strategy.is_adaptive is True
        assert strategy.domain_id == "test_domain"

        # Verify callback was called with correct data
        callback.assert_called_once()
        call_kwargs = callback.call_args[1]
        assert call_kwargs["job_id"] == str(mock_job.id)
        assert call_kwargs["content_domain"] == "test_domain"
        assert call_kwargs["classification_confidence"] == 0.85
        assert call_kwargs["inferred_schema"] is not None

    @pytest.mark.asyncio
    async def test_strategy_modes_are_distinct(
        self, mock_provider, mock_registry, mock_classifier, mock_prompt_generator,
        sample_content
    ):
        """Test that different strategy modes produce distinct results."""
        router = ExtractionStrategyRouter(
            inference_provider=mock_provider,
            registry=mock_registry,
            classifier=mock_classifier,
            prompt_generator=mock_prompt_generator,
        )

        # Legacy job
        legacy_job = MagicMock()
        legacy_job.id = uuid.uuid4()
        legacy_job.extraction_strategy = "legacy"
        legacy_job.content_domain = None

        # Manual job
        manual_job = MagicMock()
        manual_job.id = uuid.uuid4()
        manual_job.extraction_strategy = "manual"
        manual_job.content_domain = "test_domain"

        # Auto detect job
        auto_job = MagicMock()
        auto_job.id = uuid.uuid4()
        auto_job.extraction_strategy = "auto_detect"
        auto_job.content_domain = None

        legacy_strategy = await router.route(legacy_job, sample_content)
        manual_strategy = await router.route(manual_job, sample_content)
        auto_strategy = await router.route(auto_job, sample_content)

        # Legacy should be non-adaptive
        assert legacy_strategy.is_adaptive is False
        assert legacy_strategy.domain_id is None

        # Manual should be adaptive with domain
        assert manual_strategy.is_adaptive is True
        assert manual_strategy.domain_id == "test_domain"
        assert manual_strategy.system_prompt is not None

        # Auto should be adaptive with classified domain
        assert auto_strategy.is_adaptive is True
        assert auto_strategy.domain_id == "test_domain"
        assert auto_strategy.system_prompt is not None
