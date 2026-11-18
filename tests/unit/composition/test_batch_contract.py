"""Contract tests for `build_graph_stream` error reporting and early-close cancellation.

Documented contract: with `continue_on_error=True` a failed document is
reported as a `BatchDocumentError` and the rest of the stream continues; with
`continue_on_error=False` the first failure is raised. A bad input that never
becomes a document is reported under the position it held (`item-<index>`).
Closing the stream early cancels the documents still running.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest

from redstring import (
    BatchDocumentError,
    FakeLlmProvider,
    InMemoryGraphStore,
    SourceDocument,
    SourceId,
    TenantId,
)
from redstring.composition.batch import build_graph_stream
from redstring.composition.build_graph import GraphBuildReport
from redstring.domain.exceptions import LlmProviderError

TENANT = TenantId(UUID("00000000-0000-4000-8000-0000000000aa"))
ANSWER: dict[str, Any] = {
    "entities": [{"name": "Ada Lovelace", "entity_type": "Person"}],
    "relationships": [],
}
FAIL_MARK = "FAIL-MARK"
BLOCK_MARK = "BLOCK-MARK"


class _RoutingProvider:
    """Answers normally, refuses text carrying `FAIL_MARK`, and blocks on `BLOCK_MARK`."""

    model = "routing-test-model"

    def __init__(self) -> None:
        self._inner = FakeLlmProvider(by_substring={}, default=ANSWER)
        self.blocked_entered = asyncio.Event()
        self.blocked_cancelled = asyncio.Event()

    async def extract[S](
        self,
        text: str,
        schema: type[S],
        *,
        system_prompt: str | None = None,
    ) -> S:
        if FAIL_MARK in text:
            raise LlmProviderError(f"refused text containing {FAIL_MARK}", model=self.model)
        if BLOCK_MARK in text:
            self.blocked_entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.blocked_cancelled.set()
                raise
        return await self._inner.extract(text, schema, system_prompt=system_prompt)


def _doc(source: str, text: str) -> SourceDocument:
    return SourceDocument(id=SourceId(source), text=text)


async def _collect(stream: Any) -> list[Any]:
    return [item async for item in stream]


async def test_bad_input_is_reported_under_its_position_when_continuing() -> None:
    items: list[Any] = [
        _doc("doc-first", "Ada Lovelace wrote notes."),
        "   ",
        _doc("doc-third", "Ada Lovelace wrote more notes."),
    ]

    results = await _collect(
        build_graph_stream(
            items,
            provider=_RoutingProvider(),  # type: ignore[arg-type]
            store=InMemoryGraphStore(),
            tenant_id=TENANT,
        )
    )

    errors = [r for r in results if isinstance(r, BatchDocumentError)]
    reports = [r for r in results if isinstance(r, GraphBuildReport)]
    assert len(reports) == 2
    assert len(errors) == 1
    assert errors[0].source_id == SourceId("item-1")
    assert isinstance(errors[0].error, ValueError)
    assert str(errors[0].error) == "text must not be blank"


async def test_bad_input_is_raised_when_not_continuing() -> None:
    items: list[Any] = [_doc("doc-first", "Ada Lovelace wrote notes."), "   "]

    with pytest.raises(ValueError, match="text must not be blank"):
        await _collect(
            build_graph_stream(
                items,
                provider=_RoutingProvider(),  # type: ignore[arg-type]
                store=InMemoryGraphStore(),
                tenant_id=TENANT,
                continue_on_error=False,
            )
        )


async def test_failed_document_is_reported_by_its_id_when_continuing() -> None:
    items = [
        _doc("doc-broken", f"{FAIL_MARK} Ada Lovelace."),
        _doc("doc-ok", "Ada Lovelace wrote notes."),
    ]

    results = await _collect(
        build_graph_stream(
            items,
            provider=_RoutingProvider(),  # type: ignore[arg-type]
            store=InMemoryGraphStore(),
            tenant_id=TENANT,
        )
    )

    errors = [r for r in results if isinstance(r, BatchDocumentError)]
    assert len(errors) == 1
    assert errors[0].source_id == SourceId("doc-broken")
    assert isinstance(errors[0].error, LlmProviderError)
    assert FAIL_MARK in str(errors[0].error)


async def test_failed_document_is_raised_when_not_continuing() -> None:
    items = [_doc("doc-broken", f"{FAIL_MARK} Ada Lovelace.")]

    with pytest.raises(LlmProviderError, match=FAIL_MARK):
        await _collect(
            build_graph_stream(
                items,
                provider=_RoutingProvider(),  # type: ignore[arg-type]
                store=InMemoryGraphStore(),
                tenant_id=TENANT,
                continue_on_error=False,
            )
        )


async def test_closing_the_stream_early_cancels_the_document_still_running() -> None:
    provider = _RoutingProvider()
    items = [
        _doc("doc-ok", "Ada Lovelace wrote notes."),
        _doc("doc-block", f"{BLOCK_MARK} Ada Lovelace."),
    ]
    stream = build_graph_stream(
        items,
        provider=provider,  # type: ignore[arg-type]
        store=InMemoryGraphStore(),
        tenant_id=TENANT,
        max_concurrency=2,
    )

    first = await stream.__anext__()
    assert isinstance(first, GraphBuildReport)
    # The blocking document must be inside the provider before the stream closes,
    # otherwise there is no in-flight call for the cancel to reach.
    await asyncio.wait_for(provider.blocked_entered.wait(), timeout=5)
    await stream.aclose()

    await asyncio.wait_for(provider.blocked_cancelled.wait(), timeout=5)
