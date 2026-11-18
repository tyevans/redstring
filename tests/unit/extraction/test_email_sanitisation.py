"""Content sanitisation before classification: speed, redaction and bounds.

The email pattern was quadratic on text with no `@`: every word boundary inside
a run of `a.a.a.` restarted a backtracking match over the whole run. The
timing tests below use the sizes that exposed it, with a wall-clock bound
generous enough for a linear pattern on any CI runner and far below the
quadratic cost (about 0.8 s at 20 000 characters and 12.8 s at 80 000).

Expected redactions are written as literals, not derived from the pattern.
"""

from __future__ import annotations

import time

import pytest

from redstring.domain.exceptions import LlmProviderError
from redstring.extraction.classifier import (
    MAX_CONTENT_FOR_CLASSIFICATION,
    SANITISE_MARGIN,
    ContentClassifier,
)
from redstring.extraction.domains.models import ClassificationResult

#: Wall-clock ceiling for one sanitisation pass. Linear work takes milliseconds.
TIME_BUDGET_SECONDS = 0.5


class RecordingProvider:
    """Records every prompt it receives and answers with a fixed domain."""

    model = "test/recording"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def extract(self, text, schema, *, system_prompt=None):
        self.prompts.append(text)
        if schema is not ClassificationResult:  # pragma: no cover - guard
            raise LlmProviderError("unexpected schema", model=self.model)
        return ClassificationResult(domain="news_journalism", confidence=0.9)


def classifier() -> ContentClassifier:
    return ContentClassifier(RecordingProvider())


def timed(function, *args):
    started = time.perf_counter()
    result = function(*args)
    return result, time.perf_counter() - started


class TestSanitisationIsLinear:
    def test_eighty_thousand_dotted_letters_sanitise_within_budget(self):
        text = "a." * 40_000

        _, elapsed = timed(classifier()._sanitize_content, text)

        assert elapsed < TIME_BUDGET_SECONDS

    def test_an_at_sign_then_twenty_thousand_dotted_letters_sanitise_within_budget(self):
        text = "a@" + "a." * 20_000

        _, elapsed = timed(classifier()._sanitize_content, text)

        assert elapsed < TIME_BUDGET_SECONDS

    def test_the_pattern_alone_is_linear_on_the_quadratic_input(self):
        from redstring.extraction.classifier import EMAIL_PATTERN

        _, elapsed = timed(EMAIL_PATTERN.sub, "[EMAIL]", "a." * 40_000)

        assert elapsed < TIME_BUDGET_SECONDS


class TestEmailRedactionStillWorks:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("write to jane.doe@example.com today", "write to [EMAIL] today"),
            ("reach dev@mail.eu.example.co.uk now", "reach [EMAIL] now"),
            ("a@b.io starts the line", "[EMAIL] starts the line"),
            ("finally ops-team@corp.example.org", "finally [EMAIL]"),
        ],
    )
    def test_simple_sub_domain_start_and_end_cases_are_redacted(self, text, expected):
        assert classifier()._sanitize_content(text) == expected

    def test_text_with_no_address_is_unchanged(self):
        text = "No addresses here, only a.b.c and words."

        assert classifier()._sanitize_content(text) == text

    def test_a_domain_that_continues_is_not_cut_short(self):
        # The lookahead must refuse a partial top-level label.
        assert classifier()._sanitize_content("x@example.c1") == "x@example.c1"


class TestTruncationBeforeSanitisation:
    async def test_the_sanitiser_never_sees_more_than_the_limit_plus_margin(self):
        seen: list[int] = []
        instance = classifier()
        original = instance._sanitize_content

        def spy(text: str) -> str:
            seen.append(len(text))
            return original(text)

        instance._sanitize_content = spy  # type: ignore[method-assign]
        await instance.classify("word " * 200_000)

        assert seen == [MAX_CONTENT_FOR_CLASSIFICATION + SANITISE_MARGIN]

    async def test_an_address_straddling_the_limit_leaves_no_fragment_in_the_prompt(self):
        provider = RecordingProvider()
        head = "x" * 3990 + " "
        content = head + "jane.doe@example.com" + " tail text " * 50

        await ContentClassifier(provider).classify(content)

        (prompt,) = provider.prompts
        assert "jane" not in prompt
        assert "@" not in prompt
        assert "[EMAIL]" in prompt
