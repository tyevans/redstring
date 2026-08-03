"""Extraction Strategy Router for adaptive extraction.

This module provides the main orchestration service that decides which
extraction strategy to use based on job configuration and content.

The ExtractionStrategyRouter coordinates between:
- ContentClassifier: For auto_detect strategy
- DomainPromptGenerator: For generating domain-specific prompts
- DomainSchemaRegistry: For accessing domain schemas

Usage:
    from kg_builder.extraction.strategy_router import (
        ExtractionStrategyRouter,
        get_strategy_router,
        route_extraction_strategy,
    )
    from kg_builder.models.scraping_job import ScrapingJob

    # Using the router class
    router = ExtractionStrategyRouter(
        inference_provider=ollama_provider,
    )
    strategy = await router.route(job, content)

    # Or use the convenience function
    strategy = await route_extraction_strategy(
        job=job,
        content=content,
        inference_provider=provider,
    )
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from kg_builder.extraction.classifier import ContentClassifier
from kg_builder.extraction.domains.models import (
    ClassificationResult,
    DomainSchema,
    ExtractionStrategy,
)
from kg_builder.extraction.domains.registry import (
    DomainSchemaRegistry,
    get_domain_registry,
)
from kg_builder.extraction.prompt_generator import DomainPromptGenerator

if TYPE_CHECKING:
    from kg_builder.inference.providers.base import InferenceProvider
    from kg_builder.models.scraping_job import ScrapingJob

logger = logging.getLogger(__name__)


class JobUpdateCallback(Protocol):
    """Protocol for job update callback.

    This callback is invoked when the router needs to update the job
    with classification results (for auto_detect strategy).
    """

    async def __call__(
        self,
        job_id: str,
        content_domain: str,
        classification_confidence: float,
        inferred_schema: dict | None,
    ) -> None:
        """Update job with classification results.

        Args:
            job_id: The job ID to update
            content_domain: The detected domain ID
            classification_confidence: Confidence score from classification
            inferred_schema: Snapshot of the domain schema used
        """
        ...


class ExtractionStrategyRouter:
    """Routes extraction requests to the appropriate strategy.

    The router determines which extraction strategy to use based on the
    job's configuration:

    - **legacy**: Returns a simple ExtractionStrategy.legacy() that uses
      the default (non-adaptive) extraction behavior.

    - **manual**: Uses the job's pre-configured content_domain to get the
      domain schema and generate the extraction strategy.

    - **auto_detect**: Uses ContentClassifier to analyze content and
      determine the appropriate domain, then generates the strategy.
      Also updates the job with classification results via callback.

    Attributes:
        _classifier: ContentClassifier instance for auto_detect
        _prompt_generator: DomainPromptGenerator for creating prompts
        _registry: DomainSchemaRegistry for accessing domain schemas
        _job_update_callback: Optional callback for updating jobs
    """

    def __init__(
        self,
        inference_provider: "InferenceProvider | None" = None,
        *,
        classifier: ContentClassifier | None = None,
        prompt_generator: DomainPromptGenerator | None = None,
        registry: DomainSchemaRegistry | None = None,
        job_update_callback: JobUpdateCallback | None = None,
        confidence_threshold: float = 0.5,
    ) -> None:
        """Initialize the strategy router.

        Args:
            inference_provider: LLM provider for classification. Required
                if using auto_detect strategy without pre-built classifier.
            classifier: Optional pre-configured ContentClassifier (for testing).
            prompt_generator: Optional pre-configured DomainPromptGenerator.
            registry: Optional pre-configured DomainSchemaRegistry.
            job_update_callback: Optional callback to update job with
                classification results.
            confidence_threshold: Minimum confidence for classification
                acceptance (default: 0.5).
        """
        self._inference_provider = inference_provider
        self._classifier = classifier
        self._prompt_generator = prompt_generator
        self._registry = registry
        self._job_update_callback = job_update_callback
        self._confidence_threshold = confidence_threshold

    def _get_classifier(self) -> ContentClassifier:
        """Get or create the content classifier.

        Returns:
            ContentClassifier instance.

        Raises:
            ValueError: If no inference provider was provided and classifier
                is not pre-configured.
        """
        if self._classifier is not None:
            return self._classifier

        if self._inference_provider is None:
            raise ValueError(
                "Cannot create classifier: no inference provider configured. "
                "Either provide an inference_provider or a pre-configured classifier."
            )

        self._classifier = ContentClassifier(
            inference_provider=self._inference_provider,
            confidence_threshold=self._confidence_threshold,
            registry=self._get_registry(),
        )
        return self._classifier

    def _get_prompt_generator(self) -> DomainPromptGenerator:
        """Get or create the prompt generator.

        Returns:
            DomainPromptGenerator instance.
        """
        if self._prompt_generator is None:
            self._prompt_generator = DomainPromptGenerator()
        return self._prompt_generator

    def _get_registry(self) -> DomainSchemaRegistry:
        """Get or create the domain schema registry.

        Returns:
            DomainSchemaRegistry instance with schemas loaded.
        """
        if self._registry is None:
            self._registry = get_domain_registry()
        return self._registry

    async def route(
        self,
        job: "ScrapingJob",
        content: str,
        *,
        tenant_id: str | None = None,
    ) -> ExtractionStrategy:
        """Route to the appropriate extraction strategy.

        Based on the job's extraction_strategy field, determines how to
        extract entities from the content:

        - 'legacy': Returns legacy (non-adaptive) strategy
        - 'manual': Uses job.content_domain to get schema and build strategy
        - 'auto_detect': Classifies content, updates job, returns strategy

        Args:
            job: The scraping job with extraction configuration.
            content: The content to extract from (used for auto_detect).
            tenant_id: Optional tenant ID for logging context.

        Returns:
            ExtractionStrategy configured for the job.

        Raises:
            ValueError: If manual strategy has no content_domain, or if
                auto_detect fails to classify and no fallback is available.
            KeyError: If the specified domain is not found in registry.
        """
        strategy_type = job.extraction_strategy

        logger.info(
            "strategy_router.routing",
            extra={
                "job_id": str(job.id),
                "strategy": strategy_type,
                "content_domain": job.content_domain,
                "tenant_id": tenant_id,
            },
        )

        if strategy_type == "legacy":
            return self._handle_legacy()

        if strategy_type == "manual":
            return self._handle_manual(job, tenant_id)

        if strategy_type == "auto_detect":
            return await self._handle_auto_detect(job, content, tenant_id)

        # Unknown strategy - fall back to legacy with warning
        logger.warning(
            "strategy_router.unknown_strategy",
            extra={
                "job_id": str(job.id),
                "strategy": strategy_type,
                "tenant_id": tenant_id,
            },
        )
        return self._handle_legacy()

    def _handle_legacy(self) -> ExtractionStrategy:
        """Handle legacy extraction strategy.

        Returns:
            ExtractionStrategy configured for legacy extraction.
        """
        logger.debug("strategy_router.legacy")
        return ExtractionStrategy.legacy()

    def _handle_manual(
        self,
        job: "ScrapingJob",
        tenant_id: str | None,
    ) -> ExtractionStrategy:
        """Handle manual extraction strategy.

        Uses the job's pre-configured content_domain to retrieve the
        domain schema and generate the extraction strategy.

        Args:
            job: The scraping job with content_domain set.
            tenant_id: Optional tenant ID for logging.

        Returns:
            ExtractionStrategy configured for the specified domain.

        Raises:
            ValueError: If content_domain is not set.
            KeyError: If the domain is not found in registry.
        """
        if not job.content_domain:
            raise ValueError(
                f"Manual strategy requires content_domain but job {job.id} has none"
            )

        domain_id = job.content_domain

        logger.debug(
            "strategy_router.manual",
            extra={
                "job_id": str(job.id),
                "domain_id": domain_id,
                "tenant_id": tenant_id,
            },
        )

        return self._build_strategy_from_domain(domain_id)

    async def _handle_auto_detect(
        self,
        job: "ScrapingJob",
        content: str,
        tenant_id: str | None,
    ) -> ExtractionStrategy:
        """Handle auto_detect extraction strategy.

        Classifies the content to determine the appropriate domain,
        optionally updates the job with results, and returns the strategy.

        Args:
            job: The scraping job.
            content: The content to classify.
            tenant_id: Optional tenant ID for logging.

        Returns:
            ExtractionStrategy configured for the detected domain.
        """
        # If domain is already resolved, use it directly
        if job.content_domain:
            logger.debug(
                "strategy_router.auto_detect.domain_already_resolved",
                extra={
                    "job_id": str(job.id),
                    "domain_id": job.content_domain,
                    "tenant_id": tenant_id,
                },
            )
            return self._build_strategy_from_domain(job.content_domain)

        # Classify the content
        classifier = self._get_classifier()
        classification = await classifier.classify(content, tenant_id=tenant_id)

        logger.info(
            "strategy_router.auto_detect.classified",
            extra={
                "job_id": str(job.id),
                "domain": classification.domain,
                "confidence": classification.confidence,
                "tenant_id": tenant_id,
            },
        )

        # Build the strategy
        strategy = self._build_strategy_from_domain(classification.domain)

        # Update job with classification results if callback provided
        if self._job_update_callback is not None:
            try:
                # Get schema to store snapshot
                schema = self._get_registry().get_schema(classification.domain)
                schema_snapshot = self._create_schema_snapshot(schema)

                await self._job_update_callback(
                    job_id=str(job.id),
                    content_domain=classification.domain,
                    classification_confidence=classification.confidence,
                    inferred_schema=schema_snapshot,
                )
                logger.debug(
                    "strategy_router.job_updated",
                    extra={
                        "job_id": str(job.id),
                        "domain": classification.domain,
                        "tenant_id": tenant_id,
                    },
                )
            except Exception as e:
                # Log but don't fail - the strategy is still valid
                logger.warning(
                    "strategy_router.job_update_failed",
                    extra={
                        "job_id": str(job.id),
                        "error": str(e),
                        "tenant_id": tenant_id,
                    },
                )

        return strategy

    def _build_strategy_from_domain(self, domain_id: str) -> ExtractionStrategy:
        """Build extraction strategy from a domain ID.

        Retrieves the domain schema from the registry, generates the
        system prompt and JSON schema, and returns a configured strategy.

        Args:
            domain_id: The domain identifier.

        Returns:
            ExtractionStrategy configured for the domain.

        Raises:
            KeyError: If the domain is not found in registry.
        """
        registry = self._get_registry()
        schema = registry.get_schema(domain_id)

        generator = self._get_prompt_generator()
        system_prompt = generator.generate_system_prompt(schema)
        json_schema = generator.generate_json_schema(schema)

        logger.debug(
            "strategy_router.strategy_built",
            extra={
                "domain_id": domain_id,
                "prompt_length": len(system_prompt),
                "entity_types": len(schema.entity_types),
                "relationship_types": len(schema.relationship_types),
            },
        )

        return ExtractionStrategy.from_domain(
            domain_id=domain_id,
            system_prompt=system_prompt,
            json_schema=json_schema,
            confidence_thresholds=schema.confidence_thresholds,
        )

    def _create_schema_snapshot(self, schema: DomainSchema) -> dict:
        """Create a snapshot of the domain schema for storage.

        Creates a serializable dictionary representation of the schema
        that can be stored with the job for reference.

        Args:
            schema: The domain schema to snapshot.

        Returns:
            Dictionary representation of the schema.
        """
        return {
            "domain_id": schema.domain_id,
            "display_name": schema.display_name,
            "version": schema.version,
            "entity_types": [et.id for et in schema.entity_types],
            "relationship_types": [rt.id for rt in schema.relationship_types],
            "confidence_thresholds": {
                "entity_extraction": schema.confidence_thresholds.entity_extraction,
                "relationship_extraction": schema.confidence_thresholds.relationship_extraction,
            },
        }

    async def classify_content(
        self,
        content: str,
        tenant_id: str | None = None,
    ) -> ClassificationResult:
        """Classify content into a domain without routing.

        This is a convenience method for classifying content independently
        of a scraping job. Useful for testing or preview functionality.

        Args:
            content: The content to classify.
            tenant_id: Optional tenant ID for logging.

        Returns:
            ClassificationResult with domain and confidence.

        Raises:
            ValueError: If no inference provider is configured.
        """
        classifier = self._get_classifier()
        return await classifier.classify(content, tenant_id=tenant_id)


# Dependency Injection Support
#
# Instead of using global singletons, prefer creating ExtractionStrategyRouter
# instances via dependency injection. This enables:
# - Easy testing with mock dependencies
# - Clear dependency flow
# - No hidden global state


class ExtractionStrategyRouterFactory:
    """Factory for creating ExtractionStrategyRouter instances.

    Use this factory with dependency injection frameworks (like FastAPI's Depends)
    instead of global singletons.

    Example with FastAPI:
        from fastapi import Depends

        def get_strategy_router_factory() -> ExtractionStrategyRouterFactory:
            return ExtractionStrategyRouterFactory()

        @router.post("/extract")
        async def extract(
            factory: ExtractionStrategyRouterFactory = Depends(get_strategy_router_factory),
            inference_provider: InferenceProvider = Depends(get_inference_provider),
        ):
            router = factory.create(inference_provider=inference_provider)
            strategy = await router.route(job, content)
    """

    def create(
        self,
        inference_provider: "InferenceProvider | None" = None,
        *,
        classifier: ContentClassifier | None = None,
        prompt_generator: DomainPromptGenerator | None = None,
        registry: DomainSchemaRegistry | None = None,
        job_update_callback: JobUpdateCallback | None = None,
        confidence_threshold: float = 0.5,
    ) -> ExtractionStrategyRouter:
        """Create a new ExtractionStrategyRouter instance.

        Args:
            inference_provider: LLM provider for classification
            classifier: Optional pre-configured ContentClassifier
            prompt_generator: Optional pre-configured DomainPromptGenerator
            registry: Optional pre-configured DomainSchemaRegistry
            job_update_callback: Optional callback for updating jobs
            confidence_threshold: Minimum confidence for classification

        Returns:
            Configured ExtractionStrategyRouter instance
        """
        return ExtractionStrategyRouter(
            inference_provider=inference_provider,
            classifier=classifier,
            prompt_generator=prompt_generator,
            registry=registry,
            job_update_callback=job_update_callback,
            confidence_threshold=confidence_threshold,
        )


# Factory singleton for dependency injection
_factory = ExtractionStrategyRouterFactory()


def get_strategy_router_factory() -> ExtractionStrategyRouterFactory:
    """Get the ExtractionStrategyRouterFactory for dependency injection.

    Returns:
        ExtractionStrategyRouterFactory instance
    """
    return _factory


async def route_extraction_strategy(
    job: "ScrapingJob",
    content: str,
    *,
    inference_provider: "InferenceProvider | None" = None,
    tenant_id: str | None = None,
    job_update_callback: JobUpdateCallback | None = None,
) -> ExtractionStrategy:
    """Convenience function to route extraction strategy.

    Creates a temporary router and routes the strategy. For repeated
    use, consider using ExtractionStrategyRouterFactory with dependency injection.

    Args:
        job: The scraping job with extraction configuration.
        content: The content to extract from.
        inference_provider: LLM provider for classification (auto_detect only).
        tenant_id: Optional tenant ID for logging.
        job_update_callback: Optional callback for updating job state.

    Returns:
        ExtractionStrategy configured for the job.

    Example:
        strategy = await route_extraction_strategy(
            job=scraping_job,
            content=page_content,
            inference_provider=ollama_provider,
        )

        if strategy.is_adaptive:
            # Use domain-specific extraction
            prompt = strategy.system_prompt
            schema = strategy.json_schema
        else:
            # Use legacy extraction
            pass
    """
    router = ExtractionStrategyRouter(
        inference_provider=inference_provider,
        job_update_callback=job_update_callback,
    )
    return await router.route(job, content, tenant_id=tenant_id)


__all__ = [
    "ExtractionStrategyRouter",
    "ExtractionStrategyRouterFactory",
    "JobUpdateCallback",
    "get_strategy_router_factory",
    "route_extraction_strategy",
]
