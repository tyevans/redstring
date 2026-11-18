"""Calls in flight across a batch: the product ceiling, and fail-fast.

Each document has its own limiter, so the endpoint can see up to
`max_concurrency * concurrency` calls at once. The provider counts calls in
flight and calls started. Each document is split into several chunks, so one
document can fill its own pipeline's `concurrency`. With two documents at a
time and two calls per document, the peak must be exactly four: reaching it
shows the bound is real, and no more than it shows the bound holds.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from redstring import (
    FakeLlmProvider,
    InMemoryGraphStore,
    SlidingWindowChunker,
    TenantId,
    build_graph_batch,
)
from redstring.domain.exceptions import LlmProviderError

ANSWER: dict[str, Any] = {
    "entities": [
        {"name": "Ada Lovelace", "entity_type": "Person"},
        {"name": "Charles Babbage", "entity_type": "Person"},
    ],
    "relationships": [],
}

PASSAGE = "Ada Lovelace worked with Charles Babbage on the engine. "
CHUNKER = SlidingWindowChunker(default_chunk_size=60, default_overlap=0, min_chunk_size=10)


class _TrackingProvider:
    """Counts calls started and in flight. A text containing `fail_on` raises at once."""

    def __init__(self, *, delay: float, fail_on: str | None = None) -> None:
        self._inner = FakeLlmProvider(by_substring={}, default=ANSWER)
        self._delay = delay
        self._fail_on = fail_on
        self.model = "tracking-test-model"
        self.started = 0
        self.active = 0
        self.peak = 0

    async def extract[S](
        self, text: str, schema: type[S], *, system_prompt: str | None = None
    ) -> S:
        self.started += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self._fail_on is not None and self._fail_on in text:
                raise LlmProviderError("Simulated LLM failure", model=self.model)
            await asyncio.sleep(self._delay)
            return await self._inner.extract(text, schema, system_prompt=system_prompt)
        finally:
            self.active -= 1


def _document(index: int) -> str:
    return f"Document {index}. " + PASSAGE * 6


async def test_in_flight_calls_reach_but_never_exceed_the_product() -> None:
    provider = _TrackingProvider(delay=0.02)
    docs = [_document(i) for i in range(4)]

    report = await build_graph_batch(
        docs,
        provider=provider,  # type: ignore[arg-type]
        store=InMemoryGraphStore(),
        tenant_id=TenantId(uuid4()),
        max_concurrency=2,
        concurrency=2,
        chunker=CHUNKER,
    )

    assert report.successful_documents == 4
    assert provider.peak == 2 * 2


async def test_fail_fast_cancels_the_siblings_and_starts_no_further_calls() -> None:
    """With `continue_on_error=False`, a failure must stop the batch rather than
    let every other document run its remaining chunks."""
    docs = ["POISON " + PASSAGE * 6] + [_document(i) for i in range(8)]
    provider = _TrackingProvider(delay=0.05, fail_on="POISON")

    with pytest.raises(LlmProviderError):
        await build_graph_batch(
            docs,
            provider=provider,  # type: ignore[arg-type]
            store=InMemoryGraphStore(),
            tenant_id=TenantId(uuid4()),
            max_concurrency=len(docs),
            concurrency=1,
            continue_on_error=False,
            chunker=CHUNKER,
        )

    started_when_raised = provider.started
    # Every document had at most its first call in flight when the failure
    # landed. Without cancellation each would go on to call for every chunk.
    assert started_when_raised <= len(docs)

    await asyncio.sleep(0.2)
    assert provider.started == started_when_raised
