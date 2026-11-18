"""The classifier enforces `timeout_seconds` on its one model call.

Before this was enforced, `timeout_seconds` was stored and never read, and the
`except TimeoutError` branch was unreachable. The provider here never returns,
so the only way `classify` can finish is by the timeout.
"""

from __future__ import annotations

import asyncio
import time

from redstring.domain.limiter import CallLimiter
from redstring.extraction.classifier import DEFAULT_FALLBACK_DOMAIN, ContentClassifier
from redstring.extraction.domains.models import ClassificationResult

LONG = "This is a comprehensive technical documentation about Python. " * 4


class NeverReturns:
    model = "test/never"

    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, text, schema, *, system_prompt=None):
        self.calls += 1
        await asyncio.Event().wait()  # never set
        raise AssertionError("unreachable")  # pragma: no cover


class HangsOnceThenAnswers:
    model = "test/hang-once"

    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, text, schema, *, system_prompt=None):
        self.calls += 1
        if self.calls == 1:
            await asyncio.Event().wait()
        return ClassificationResult(domain="news_journalism", confidence=0.9)


async def test_a_provider_that_never_returns_falls_back_at_the_timeout():
    classifier = ContentClassifier(NeverReturns(), timeout_seconds=0.05)

    started = time.perf_counter()
    # The outer bound turns a missing timeout into a failure, not a hang.
    result = await asyncio.wait_for(classifier.classify(LONG), timeout=5.0)
    elapsed = time.perf_counter() - started

    assert result.domain == DEFAULT_FALLBACK_DOMAIN
    assert result.confidence == 0.0
    assert result.reasoning == "Fallback classification: Classification timeout"
    assert elapsed < 5.0


async def test_the_timeout_releases_the_call_slot_for_the_next_caller():
    limiter = CallLimiter(1)
    provider = HangsOnceThenAnswers()
    classifier = ContentClassifier(provider, timeout_seconds=0.05, limiter=limiter)

    first = await asyncio.wait_for(classifier.classify(LONG), timeout=5.0)
    second = await asyncio.wait_for(classifier.classify(LONG), timeout=5.0)

    assert first.confidence == 0.0
    assert second.domain == "news_journalism"
    assert provider.calls == 2
