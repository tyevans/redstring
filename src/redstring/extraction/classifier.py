"""Deciding which domain schema a document should be extracted with.

One model call, whose answer selects the `system_prompt` the extraction calls
then use. `redstring.composition.build_graph(domain=AUTO)` is the caller;
this is also usable directly:

```python
from redstring import domain_system_prompt
from redstring.extraction.classifier import ContentClassifier

result = await ContentClassifier(provider).classify(document.text)
prompt = domain_system_prompt(result.domain)
```

The `provider` is any `LlmProvider` -- `LangChainLlmProvider` against an
OpenAI-compatible server, or `FakeLlmProvider` in a test. This docstring used
to construct an `OllamaProvider(base_url=...)`, a class that has not existed
in this repository since slice 6 replaced the vendor extractors with one port.
Slice 10 put the module on the public `AUTO` path without reading it; the
review caught it.

## It never fails, and that is the thing to know about it

Three paths return `encyclopedia_wiki` with **confidence 0.0**: content under
`MIN_CONTENT_LENGTH`, which is never sent to the model at all; an answer below
`confidence_threshold`; and any `LlmProviderError`. Falling back is right
*here* and wrong in extraction -- a misclassified document is extracted with
the general-purpose schema, which is a worse answer, while a silently empty
extraction is a missing answer that looks like a real one.

But a fallback that reports the same shape as a choice is a plausible answer
nobody investigates, so the confidence is carried out rather than logged and
dropped: `GraphBuildReport.domain_confidence` is where it surfaces.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

# Import directly from submodules to avoid circular imports through __init__
from redstring.domain.exceptions import LlmProviderError
from redstring.extraction.domains.models import ClassificationResult
from redstring.extraction.domains.registry import get_domain_registry
from redstring.extraction.limiter import CallLimiter

if TYPE_CHECKING:
    from redstring.domain.ids import TenantId
    from redstring.extraction.domains.registry import DomainSchemaRegistry
    from redstring.ports.llm_provider import LlmProvider

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
        limiter: CallLimiter | None = None,
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
            limiter: The ceiling this classifier's one call against `provider`
                passes through. `CallLimiter(1)` when omitted, which admits
                that single call immediately and changes nothing for a caller
                using this class on its own -- pass one explicitly to bound
                this call jointly with other calls against the same endpoint,
                which is what `redstring.composition.build_graph` does for
                `domain=AUTO`: without this, its classification call would sit
                outside the same ceiling that bounds extraction, gleaning and
                embedding.
        """
        self._provider = provider
        self._timeout = timeout_seconds
        self._confidence_threshold = confidence_threshold
        self._fallback_domain = fallback_domain
        self._registry = registry
        self._limiter = limiter if limiter is not None else CallLimiter(1)

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
        tenant_id: TenantId | None = None,
    ) -> ClassificationResult:
        """Classify content into a domain.

        Args:
            content: The content to classify.
            tenant_id: Optional, and only for logging -- nothing here is
                scoped by tenant. Typed `TenantId` (a `UUID`) rather than the
                `str` it was, because every other tenant parameter in the
                library is a `UUID` and a lone `str` here is a trap for the
                next caller rather than a flexibility anyone wanted.

        Returns:
            A `ClassificationResult`. **Confidence 0.0 means it gave up** --
            see the module docstring for the three ways that happens.
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
            async with self._limiter:
                result = await self._provider.extract(
                    prompt,
                    ClassificationResult,
                    system_prompt=(
                        "You are a content classifier. Classify the text into "
                        "one of the domains listed, and say how confident you "
                        "are."
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


__all__ = [
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "DEFAULT_FALLBACK_DOMAIN",
    "MAX_CONTENT_FOR_CLASSIFICATION",
    "MIN_CONTENT_LENGTH",
    # `classify_content` was here: a module-level wrapper that built a
    # `ContentClassifier` and called it. `ContentClassifier(provider).classify(text)`
    # is the same line without the indirection, and the wrapper had no caller.
    # Same test slice 10 applied to `prompt_generator`'s three dead halves,
    # applied here in the fix round rather than skipped again.
    "ContentClassifier",
]
