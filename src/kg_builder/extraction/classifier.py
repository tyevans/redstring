"""Content classifier for adaptive extraction.

This module provides LLM-based content classification to determine
the appropriate domain schema for extraction.

The classifier analyzes content samples and determines the most
appropriate domain schema using a configurable inference provider
(e.g., Ollama or OpenAI).

Usage:
    from kg_builder.extraction.classifier import ContentClassifier
    from kg_builder.llm.adapters.langchain import LangChainLlmProvider

    provider = OllamaProvider(base_url="http://localhost:11434")
    classifier = ContentClassifier(provider)

    result = await classifier.classify(content="some text content...")
    print(f"Domain: {result.domain}, Confidence: {result.confidence}")

    # Or use the convenience function
    from kg_builder.extraction.classifier import classify_content

    result = await classify_content(
        content="some text...",
        provider,
    )
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

# Import directly from submodules to avoid circular imports through __init__
from kg_builder.domain.exceptions import LlmProviderError
from kg_builder.extraction.domains.models import ClassificationResult
from kg_builder.extraction.domains.registry import get_domain_registry

if TYPE_CHECKING:
    from kg_builder.extraction.domains.registry import DomainSchemaRegistry
    from kg_builder.ports.llm_provider import LlmProvider

logger = logging.getLogger(__name__)

# What the classifier asks. The *shape* of the answer is pinned by the JSON
# schema `ClassificationResult` generates and by the port's validation, so the
# instructions the old template carried -- "respond with ONLY a JSON object",
# and a hand-written example of that object -- are gone. They were a second
# specification of the same thing, free to drift from the first, and they were
# the reason the classifier needed its own `_parse_response` that dug a JSON
# object out of surrounding prose.
CLASSIFICATION_PROMPT = """Classify the following content into exactly one of
these domains:

{domain_list}

Content to classify:
---
{content}
---"""

# Content length limits
MIN_CONTENT_LENGTH = 100  # Characters
MAX_CONTENT_FOR_CLASSIFICATION = 4000  # Characters

# Default confidence threshold below which we use fallback
DEFAULT_CONFIDENCE_THRESHOLD = 0.5

# Default domain to use as fallback
DEFAULT_FALLBACK_DOMAIN = "encyclopedia_wiki"

# Patterns for content sanitization
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
PHONE_PATTERN = re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b")
SSN_PATTERN = re.compile(r"\b\d{3}[-]?\d{2}[-]?\d{4}\b")
# Credit card patterns (basic - catches most formats)
CC_PATTERN = re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b")


class ContentClassifier:
    """Classifies content into domains using LLM.

    The classifier analyzes content samples and determines the most
    appropriate domain schema for extraction. It uses a configured
    inference provider (Ollama or OpenAI) for classification.

    The classification process:
    1. Validates content length (minimum 100 characters)
    2. Sanitizes content to remove PII (emails, phones, SSNs, credit cards)
    3. Truncates content to max 4000 characters
    4. Builds a classification prompt with available domains
    5. Calls the LLM to classify the content
    6. Parses the JSON response and validates the domain
    7. Returns a fallback if classification fails or confidence is low

    Attributes:
        _provider: The inference provider for LLM calls
        _timeout: Request timeout in seconds
        _registry: Domain schema registry
        _confidence_threshold: Minimum confidence for accepting classification
        _fallback_domain: Domain to use when classification fails/low confidence
    """

    def __init__(
        self,
        provider: LlmProvider,
        timeout_seconds: float = 30.0,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        fallback_domain: str = DEFAULT_FALLBACK_DOMAIN,
        registry: DomainSchemaRegistry | None = None,
    ) -> None:
        """Initialize the classifier.

        Args:
            provider: The `LlmProvider` used for classification.
            timeout_seconds: Timeout for classification calls.
            confidence_threshold: Minimum confidence to accept classification.
                Classifications below this threshold will use fallback domain.
            fallback_domain: Domain to use when classification fails or
                confidence is below threshold.
            registry: Optional custom domain registry (for testing).
        """
        self._provider = provider
        self._timeout = timeout_seconds
        self._confidence_threshold = confidence_threshold
        self._fallback_domain = fallback_domain
        self._registry = registry

    def _get_registry(self) -> DomainSchemaRegistry:
        """Get the domain schema registry.

        Lazy-loads the registry on first access to avoid import cycles.
        """
        if self._registry is None:
            self._registry = get_domain_registry()
        return self._registry

    async def classify(
        self,
        content: str,
        tenant_id: str | None = None,
    ) -> ClassificationResult:
        """Classify content into a domain.

        Args:
            content: The content to classify.
            tenant_id: Optional tenant ID for logging.

        Returns:
            ClassificationResult with domain, confidence, and reasoning.

        Note:
            On error or timeout, returns fallback domain with 0.0 confidence.
        """
        # Check minimum content length
        content_stripped = content.strip()
        if len(content_stripped) < MIN_CONTENT_LENGTH:
            logger.info(
                "Content too short for classification",
                extra={"content_length": len(content_stripped), "tenant_id": tenant_id},
            )
            return self._fallback_result("Content too short")

        # Sanitize and truncate content
        sanitized = self._sanitize_content(content_stripped)
        truncated = sanitized[:MAX_CONTENT_FOR_CLASSIFICATION]

        # Build classification prompt
        prompt = self._build_prompt(truncated)

        try:
            result = await self._provider.extract(
                prompt,
                ClassificationResult,
                system_prompt=(
                    "You are a content classifier. Classify the text into one "
                    "of the domains listed, and say how confident you are."
                ),
            )

            # Check confidence threshold
            if result.confidence < self._confidence_threshold:
                logger.info(
                    "classification.low_confidence",
                    extra={
                        "domain": result.domain,
                        "confidence": result.confidence,
                        "threshold": self._confidence_threshold,
                        "tenant_id": tenant_id,
                    },
                )
                # Return the result but with the fallback domain
                return ClassificationResult(
                    domain=self._fallback_domain,
                    confidence=result.confidence,
                    reasoning=(
                        f"Low confidence classification ({result.confidence:.2f} < "
                        f"{self._confidence_threshold:.2f}). "
                        f"Original: {result.domain}. {result.reasoning or ''}"
                    ),
                    alternatives=[{"domain": result.domain, "confidence": result.confidence}],
                )

            logger.info(
                "classification.completed",
                extra={
                    "domain": result.domain,
                    "confidence": result.confidence,
                    "tenant_id": tenant_id,
                },
            )
            return result

        except LlmProviderError as error:
            # The port's whole failure family: an empty completion, or one
            # that did not validate as a ClassificationResult. Falling back
            # is right *here* and wrong in extraction: a misclassified
            # document is extracted with the general-purpose schema, which is
            # a worse answer, while a silently empty extraction is a missing
            # answer that looks like a real one.
            logger.warning(
                "Classification failed; using the fallback domain",
                extra={"tenant_id": tenant_id, "error": str(error)},
            )
            return self._fallback_result(f"Classification failed: {error}")

        except TimeoutError:
            logger.warning(
                "Classification timeout",
                extra={"tenant_id": tenant_id, "timeout": self._timeout},
            )
            return self._fallback_result("Classification timeout")

    def _sanitize_content(self, content: str) -> str:
        """Remove PII and sensitive data before classification.

        Replaces patterns that may contain personal information with
        placeholder tokens. This ensures sensitive data is not sent
        to the LLM for classification.

        Args:
            content: Raw content to sanitize.

        Returns:
            Sanitized content safe for LLM processing.
        """
        sanitized = content

        # Remove email addresses
        sanitized = EMAIL_PATTERN.sub("[EMAIL]", sanitized)

        # Remove phone numbers
        sanitized = PHONE_PATTERN.sub("[PHONE]", sanitized)

        # Remove SSN-like patterns
        sanitized = SSN_PATTERN.sub("[REDACTED]", sanitized)

        # Remove credit card-like patterns
        return CC_PATTERN.sub("[REDACTED]", sanitized)

    def _build_prompt(self, content: str) -> str:
        """Build the classification prompt.

        Constructs a prompt that lists all available domains with their
        descriptions and asks the LLM to classify the provided content.

        Args:
            content: Sanitized content to classify.

        Returns:
            Complete prompt for LLM classification.
        """
        # Build domain list from registry
        registry = self._get_registry()
        domains = registry.list_domains()
        domain_list = "\n".join(f"- {d.domain_id}: {d.description}" for d in domains)

        return CLASSIFICATION_PROMPT.format(
            domain_list=domain_list,
            content=content,
        )

    def _fallback_result(self, reason: str) -> ClassificationResult:
        """Create a fallback classification result.

        Used when classification fails for any reason. Returns the
        configured fallback domain with zero confidence.

        Args:
            reason: Reason for falling back.

        Returns:
            ClassificationResult with fallback domain.
        """
        return ClassificationResult(
            domain=self._fallback_domain,
            confidence=0.0,
            reasoning=f"Fallback classification: {reason}",
        )


async def classify_content(
    content: str,
    provider: LlmProvider,
    tenant_id: str | None = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    fallback_domain: str = DEFAULT_FALLBACK_DOMAIN,
) -> ClassificationResult:
    """Convenience function for content classification.

    Creates a ContentClassifier instance and classifies the provided
    content. Use this for one-off classifications. For repeated
    classifications, create a ContentClassifier instance directly.

    Args:
        content: Content to classify.
        provider: The `LlmProvider` to classify with.
        tenant_id: Optional tenant ID for logging.
        confidence_threshold: Minimum confidence for accepting classification.
        fallback_domain: Domain to use when classification fails.

    Returns:
        ClassificationResult with domain and confidence.

    Example:
        from kg_builder.llm.adapters.langchain import LangChainLlmProvider
        from kg_builder.extraction.classifier import classify_content

        provider = OllamaProvider(base_url="http://localhost:11434")
        result = await classify_content(
            content="The quick brown fox...",
            provider,
        )
        print(f"Classified as: {result.domain}")
    """
    classifier = ContentClassifier(
        provider,
        confidence_threshold=confidence_threshold,
        fallback_domain=fallback_domain,
    )
    return await classifier.classify(content, tenant_id)


# Type alias for exported symbols
__all__ = [
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "DEFAULT_FALLBACK_DOMAIN",
    "MAX_CONTENT_FOR_CLASSIFICATION",
    "MIN_CONTENT_LENGTH",
    "ContentClassifier",
    "classify_content",
]
