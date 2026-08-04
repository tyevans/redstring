"""Classifying content into a domain, over the `LlmProvider` port.

Rewritten in slice 6. The classifier used to take an `InferenceProvider` from
the deleted `kg_builder.inference` package and parse a JSON object out of
whatever prose came back, so the old suite was built on an `AsyncMock` whose
`infer` returned a hand-written string. Two whole classes of it --
`TestJsonExtraction` and the "JSON with surrounding text" cases -- tested that
hand-rolled parser, which is gone: `LlmProvider.extract` validates against
`ClassificationResult` and raises when it cannot.

`FakeLlmProvider` replaces the mock, and it validates payloads exactly as the
LangChain adapter does. So a test that programs a malformed answer really does
exercise the classifier's failure path rather than a mock returning `None`.

## Why the classifier falls back where extraction raises

They look inconsistent and are not. A misclassified document is extracted with
the general-purpose schema: a worse answer, but an answer. A silently empty
*extraction* is a missing answer that looks like a real one -- "this document
contained nothing" -- which is the thing the port exists to prevent. So the
classifier degrades and extraction raises.
"""

from __future__ import annotations

import pytest

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
)
from kg_builder.llm.adapters.fake import EMPTY, FakeLlmProvider

DOMAINS = {
    "technical_documentation": "Technical docs and API references",
    "literature_fiction": "Novels, plays, and narrative works",
    "news_journalism": "News articles and journalism",
    "encyclopedia_wiki": "Encyclopedia and wiki content",
}


class FakeRegistry:
    """A real registry of four domains. Small enough not to need a mock.

    The old suite built this out of four `MagicMock`s with `domain_id` and
    `description` attributes, which is more code than the real thing and
    silently accepts any attribute a test asks for.
    """

    def __init__(self, domains: dict[str, str] | None = None) -> None:
        self._domains = dict(DOMAINS if domains is None else domains)

    def list_domains(self):
        return [_Domain(domain_id, description) for domain_id, description in self._domains.items()]

    def has_domain(self, domain_id: str) -> bool:
        return domain_id in self._domains


class _Domain:
    def __init__(self, domain_id: str, description: str) -> None:
        self.domain_id = domain_id
        self.description = description


def answer(domain: str, confidence: float, reasoning: str | None = None) -> dict:
    return {"domain": domain, "confidence": confidence, "reasoning": reasoning}


def classifier_answering(*responses, **kwargs) -> ContentClassifier:
    return ContentClassifier(
        FakeLlmProvider(script=list(responses)),
        registry=FakeRegistry(),
        **kwargs,
    )


@pytest.fixture
def long_content():
    """Long enough to pass the minimum-length gate."""
    return (
        "This is a comprehensive technical documentation about Python programming. "
        "It covers asyncio, type hints, and best practices for maintainable code. "
    ) * 3


class TestInitialisation:
    def test_the_defaults_are_the_module_constants(self):
        classifier = ContentClassifier(FakeLlmProvider(script=[{}]))

        assert classifier._confidence_threshold == DEFAULT_CONFIDENCE_THRESHOLD
        assert classifier._fallback_domain == DEFAULT_FALLBACK_DOMAIN

    def test_every_setting_can_be_overridden(self):
        registry = FakeRegistry()
        classifier = ContentClassifier(
            FakeLlmProvider(script=[{}]),
            timeout_seconds=60.0,
            confidence_threshold=0.7,
            fallback_domain="news_journalism",
            registry=registry,
        )

        assert classifier._timeout == 60.0
        assert classifier._confidence_threshold == 0.7
        assert classifier._fallback_domain == "news_journalism"
        assert classifier._registry is registry


class TestClassification:
    async def test_a_confident_answer_is_returned_as_given(self, long_content):
        classifier = classifier_answering(answer("technical_documentation", 0.92, "API docs"))

        result = await classifier.classify(long_content)

        assert result.domain == "technical_documentation"
        assert result.confidence == 0.92
        assert result.reasoning == "API docs"

    async def test_the_answer_is_a_validated_domain_object(self, long_content):
        """The port validated it against `ClassificationResult`, so this is typed.

        The old classifier hand-parsed a JSON blob, which meant a confidence
        of `"high"` or of `1.5` reached callers unchecked.
        """
        result = await classifier_answering(answer("news_journalism", 0.8)).classify(long_content)

        assert 0.0 <= result.confidence <= 1.0

    async def test_a_tenant_id_is_accepted_for_logging_and_changes_nothing(self, long_content):
        classifier = FakeLlmProvider(
            by_substring={"Python": answer("technical_documentation", 0.9)}
        )
        one = await ContentClassifier(classifier, registry=FakeRegistry()).classify(
            long_content, tenant_id="t-1"
        )
        two = await ContentClassifier(classifier, registry=FakeRegistry()).classify(long_content)

        assert one == two


class TestContentLength:
    async def test_content_below_the_minimum_falls_back_without_calling_a_model(self):
        """The script is empty, so calling the provider would raise loudly.

        That is the assertion: a short document must not cost a model call,
        and a fake that ran out of script is how this test can tell.
        """
        classifier = ContentClassifier(FakeLlmProvider(script=[]), registry=FakeRegistry())

        result = await classifier.classify("too short")

        assert result.domain == DEFAULT_FALLBACK_DOMAIN
        assert result.confidence == 0.0

    async def test_the_minimum_is_measured_after_stripping(self):
        classifier = ContentClassifier(FakeLlmProvider(script=[]), registry=FakeRegistry())

        result = await classifier.classify(" " * 500 + "short" + " " * 500)

        assert result.domain == DEFAULT_FALLBACK_DOMAIN

    async def test_content_at_the_minimum_is_classified(self):
        """Boundary, stated because `<` and `<=` are equally plausible here."""
        classifier = classifier_answering(answer("literature_fiction", 0.8))

        result = await classifier.classify("x" * MIN_CONTENT_LENGTH)

        assert result.domain == "literature_fiction"

    async def test_very_long_content_is_truncated_before_it_is_sent(self):
        """A whole book in one classification prompt is a slow, expensive no.

        Asserted behaviourally: the sentinel sits past the truncation point,
        so a content-addressed fake sees it only if truncation failed.
        Measuring the prompt's *length* instead would pass against a
        classifier that truncated in `_build_prompt` and not in `classify`,
        which is the arrangement that actually ships the whole book.
        """
        beyond_the_cut = "x" * (MAX_CONTENT_FOR_CLASSIFICATION + 100) + "SENTINEL"
        classifier = ContentClassifier(
            FakeLlmProvider(
                by_substring={"SENTINEL": answer("literature_fiction", 0.9)},
                default=answer("news_journalism", 0.9),
            ),
            registry=FakeRegistry(),
        )

        result = await classifier.classify(beyond_the_cut)

        assert result.domain == "news_journalism"


class TestSanitisation:
    @pytest.mark.parametrize(
        ("secret", "placeholder"),
        [
            ("reach me at ada@example.com now", "[EMAIL]"),
            ("call 555-123-4567 today", "[PHONE]"),
            ("ssn 123-45-6789 on file", "[REDACTED]"),
            ("card 4111 1111 1111 1111 charged", "[REDACTED]"),
        ],
    )
    def test_personal_data_is_replaced_before_anything_is_sent(self, secret, placeholder):
        """Classification ships content to a third party; PII must not go with it."""
        sanitized = ContentClassifier(FakeLlmProvider(script=[{}]))._sanitize_content(secret)

        assert placeholder in sanitized

    def test_the_original_secret_is_not_merely_annotated(self):
        """A substitution that appended rather than replaced would pass a
        naive "placeholder is present" check while sending the data anyway."""
        sanitized = ContentClassifier(FakeLlmProvider(script=[{}]))._sanitize_content(
            "reach me at ada@example.com now"
        )

        assert "ada@example.com" not in sanitized

    def test_ordinary_prose_survives_sanitisation_unchanged(self):
        """Over-eager redaction would quietly destroy the thing being classified."""
        prose = "Ada Lovelace wrote about the Analytical Engine in 1843."

        assert ContentClassifier(FakeLlmProvider(script=[{}]))._sanitize_content(prose) == prose

    @pytest.mark.parametrize(
        ("pattern", "sample"),
        [
            (EMAIL_PATTERN, "ada@example.com"),
            (PHONE_PATTERN, "555-123-4567"),
            (SSN_PATTERN, "123-45-6789"),
            (CC_PATTERN, "4111111111111111"),
        ],
    )
    def test_each_pattern_matches_what_it_is_named_for(self, pattern, sample):
        assert pattern.search(sample) is not None


class TestFailure:
    async def test_an_empty_completion_falls_back_rather_than_raising(self, long_content):
        """See the module docstring: a worse answer beats no answer here."""
        classifier = classifier_answering(EMPTY)

        result = await classifier.classify(long_content)

        assert result.domain == DEFAULT_FALLBACK_DOMAIN
        assert result.confidence == 0.0

    async def test_an_answer_that_does_not_validate_falls_back(self, long_content):
        """A confidence above 1.0 is refused by `ClassificationResult` itself.

        The old parser accepted it, so callers ranking on confidence got a
        value outside the range they were promised.
        """
        classifier = classifier_answering({"domain": "news_journalism", "confidence": 1.5})

        result = await classifier.classify(long_content)

        assert result.domain == DEFAULT_FALLBACK_DOMAIN

    async def test_an_answer_missing_a_required_field_falls_back(self, long_content):
        classifier = classifier_answering({"reasoning": "I forgot the domain"})

        assert (await classifier.classify(long_content)).domain == DEFAULT_FALLBACK_DOMAIN

    async def test_the_fallback_explains_itself(self, long_content):
        """So an operator seeing a wall of fallbacks can tell why."""
        result = await classifier_answering(EMPTY).classify(long_content)

        assert result.reasoning
        assert "fail" in result.reasoning.lower()


class TestConfidenceThreshold:
    async def test_an_answer_below_the_threshold_is_replaced_by_the_fallback(self, long_content):
        classifier = classifier_answering(
            answer("literature_fiction", 0.3), confidence_threshold=0.5
        )

        result = await classifier.classify(long_content)

        assert result.domain == DEFAULT_FALLBACK_DOMAIN

    async def test_the_rejected_answer_is_kept_as_an_alternative(self, long_content):
        """Discarding it would throw away the only evidence about the document.

        A caller reviewing fallbacks needs to see what the model actually
        thought, and at what confidence, to decide whether the threshold is
        set wrong.
        """
        classifier = classifier_answering(
            answer("literature_fiction", 0.3), confidence_threshold=0.5
        )

        result = await classifier.classify(long_content)

        assert result.alternatives == [{"domain": "literature_fiction", "confidence": 0.3}]

    async def test_an_answer_exactly_at_the_threshold_is_accepted(self, long_content):
        """Boundary: `<` and `<=` are equally plausible and disagree here."""
        classifier = classifier_answering(
            answer("literature_fiction", 0.5), confidence_threshold=0.5
        )

        assert (await classifier.classify(long_content)).domain == "literature_fiction"

    async def test_an_answer_above_the_threshold_is_accepted(self, long_content):
        classifier = classifier_answering(
            answer("literature_fiction", 0.51), confidence_threshold=0.5
        )

        assert (await classifier.classify(long_content)).domain == "literature_fiction"


class TestPromptBuilding:
    def test_every_registered_domain_is_offered_to_the_model(self):
        """A domain missing from the prompt can never be chosen.

        And the failure is invisible: the model picks the closest of the ones
        it was shown, with high confidence, and nothing looks wrong.
        """
        classifier = ContentClassifier(FakeLlmProvider(script=[{}]), registry=FakeRegistry())

        prompt = classifier._build_prompt("some content")

        for domain_id, description in DOMAINS.items():
            assert domain_id in prompt
            assert description in prompt

    def test_the_content_reaches_the_prompt(self):
        classifier = ContentClassifier(FakeLlmProvider(script=[{}]), registry=FakeRegistry())

        assert "Ada Lovelace" in classifier._build_prompt("Ada Lovelace")
