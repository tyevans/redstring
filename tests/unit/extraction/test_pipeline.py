"""Chunk, extract, merge, emit -- and refuse to record a partial run as a whole one.

Every test here runs against `FakeLlmProvider`, which is a real
implementation: it validates payloads against the caller's schema exactly as
the LangChain adapter does. Nothing asserts how the provider was *called*.

The fake is programmed `by_substring` wherever more than one chunk is
involved. A positional script would answer the first chunk with the first
entry whatever that chunk contained, so a test claiming "the entity in chunk
three survives" would be asserting nothing about which chunk it came from.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from kg_builder.aggregates.document import Document
from kg_builder.domain.exceptions import EmptyCompletionError, MalformedCompletionError
from kg_builder.domain.source import SourceDocument
from kg_builder.events import DocumentExtracted
from kg_builder.events.streams import document_stream
from kg_builder.extraction.chunkers import SlidingWindowChunker
from kg_builder.extraction.pipeline import (
    ExtractionPipeline,
    PartialExtractionError,
)
from kg_builder.llm.adapters.fake import EMPTY, FakeLlmProvider

MODEL = "fake/canned-v1"


@pytest.fixture
def tenant_id():
    return uuid4()


@pytest.fixture
def aggregate(tenant_id):
    return Document(document_stream(tenant_id=tenant_id, source_id="doc-1").aggregate_id)


def document(text: str, source_id: str = "doc-1") -> SourceDocument:
    return SourceDocument(id=source_id, text=text)


def payload(*names: str, links: list[tuple[str, str, str]] | None = None) -> dict:
    return {
        "entities": [{"name": name, "entity_type": "Person"} for name in names],
        "relationships": [
            {"source_name": a, "target_name": b, "relationship_type": kind}
            for a, b, kind in (links or [])
        ],
    }


def small_chunker() -> SlidingWindowChunker:
    """Small enough that the test documents below really do split."""
    return SlidingWindowChunker(default_chunk_size=120, default_overlap=30, min_chunk_size=10)


class TestOneChunk:
    async def test_a_short_document_yields_the_entities_the_model_found(self, tenant_id):
        pipeline = ExtractionPipeline(FakeLlmProvider(script=[payload("Ada Lovelace")]))

        result = await pipeline.extract(document("Ada Lovelace was a mathematician."), tenant_id)

        assert [e.name for e in result.entities] == ["Ada Lovelace"]

    async def test_the_entities_carry_the_document_and_the_model_that_found_them(self, tenant_id):
        pipeline = ExtractionPipeline(
            FakeLlmProvider(model="fake/v7", script=[payload("Ada Lovelace")])
        )

        result = await pipeline.extract(document("Ada was a mathematician.", "essay-9"), tenant_id)

        [ada] = result.entities
        assert (ada.source_id, ada.model, ada.tenant_id) == ("essay-9", "fake/v7", tenant_id)

    async def test_a_document_the_model_found_nothing_in_extracts_to_nothing(self, tenant_id):
        """Not an error. This is the outcome every failure path must stay distinct from."""
        pipeline = ExtractionPipeline(FakeLlmProvider(script=[payload()]))

        result = await pipeline.extract(document("The weather was fine."), tenant_id)

        assert result.entities == []
        assert result.failed_chunks == 0


class TestManyChunks:
    async def test_entities_from_every_chunk_reach_the_result(self, tenant_id):
        text = "Ada Lovelace. " * 12 + "Charles Babbage. " * 12
        pipeline = ExtractionPipeline(
            FakeLlmProvider(
                by_substring={
                    "Ada Lovelace": payload("Ada Lovelace"),
                    "Charles Babbage": payload("Charles Babbage"),
                }
            ),
            chunker=small_chunker(),
        )

        result = await pipeline.extract(document(text), tenant_id)

        assert sorted(e.name for e in result.entities) == ["Ada Lovelace", "Charles Babbage"]

    async def test_the_document_really_did_split(self, tenant_id):
        """Guards the test above, which would pass on one chunk and prove nothing."""
        text = "Ada Lovelace. " * 12 + "Charles Babbage. " * 12

        assert small_chunker().chunk(text).total_chunks > 1

    async def test_an_entity_several_chunks_report_appears_once(self, tenant_id):
        """The duplicate chunk overlap manufactures, removed."""
        text = "Ada Lovelace. " * 40
        pipeline = ExtractionPipeline(
            FakeLlmProvider(by_substring={"Ada": payload("Ada Lovelace")}),
            chunker=small_chunker(),
        )

        result = await pipeline.extract(document(text), tenant_id)

        assert [e.name for e in result.entities] == ["Ada Lovelace"]

    async def test_an_edge_between_entities_from_two_different_chunks_is_dropped(self, tenant_id):
        """A real limit of chunking, asserted rather than left to be discovered.

        Each chunk is extracted alone, so a chunk naming only Ada cannot state
        a resolvable edge to Charles -- the endpoint is not in *its* answer.
        The edge is counted as unresolved, not silently absent, which is the
        difference between a known limitation and a mystery.
        """
        text = "Ada Lovelace. " * 12 + "Charles Babbage. " * 12
        pipeline = ExtractionPipeline(
            FakeLlmProvider(
                by_substring={
                    "Ada Lovelace": payload(
                        "Ada Lovelace", links=[("Ada Lovelace", "Charles Babbage", "KNEW")]
                    ),
                    "Charles Babbage": payload("Charles Babbage"),
                }
            ),
            chunker=small_chunker(),
        )

        result = await pipeline.extract(document(text), tenant_id)

        assert result.relationships == []
        assert result.unresolved_relationships >= 1


class TestFailingChunks:
    async def test_by_default_one_failed_chunk_fails_the_whole_document(self, tenant_id):
        """Loud beats partial, and this is why.

        A run that quietly recorded nine of ten chunks would then mark the
        model version as extracted, so the retry `Document` idempotency
        provides becomes a no-op -- the missing tenth is permanent and
        invisible.
        """
        pipeline = ExtractionPipeline(
            FakeLlmProvider(by_substring={"Ada": EMPTY, "Charles": payload("Charles Babbage")}),
            chunker=small_chunker(),
        )

        with pytest.raises(EmptyCompletionError):
            await pipeline.extract(
                document("Ada Lovelace. " * 12 + "Charles Babbage. " * 12), tenant_id
            )

    async def test_a_malformed_chunk_also_fails_the_document_by_default(self, tenant_id):
        pipeline = ExtractionPipeline(
            FakeLlmProvider(script=[{"entities": [{"name": "Ada"}]}]),
        )

        with pytest.raises(MalformedCompletionError):
            await pipeline.extract(document("Ada Lovelace."), tenant_id)

    async def test_skipping_failed_chunks_keeps_the_rest_and_counts_the_losses(self, tenant_id):
        pipeline = ExtractionPipeline(
            FakeLlmProvider(by_substring={"Ada": EMPTY, "Charles": payload("Charles Babbage")}),
            chunker=small_chunker(),
            skip_failed_chunks=True,
        )

        result = await pipeline.extract(
            document("Ada Lovelace. " * 12 + "Charles Babbage. " * 12), tenant_id
        )

        assert [e.name for e in result.entities] == ["Charles Babbage"]
        assert result.failed_chunks >= 1


class TestRecording:
    async def test_recording_emits_one_event_carrying_everything_found(self, aggregate, tenant_id):
        pipeline = ExtractionPipeline(
            FakeLlmProvider(script=[payload("Ada Lovelace", "Charles Babbage")])
        )

        event = await pipeline.record(aggregate, document("Ada and Charles."), tenant_id)

        assert isinstance(event, DocumentExtracted)
        assert sorted(e.name for e in event.entities) == ["Ada Lovelace", "Charles Babbage"]

    async def test_the_event_records_the_provider_as_the_model_version(self, aggregate, tenant_id):
        """`Document` keys idempotency on it, so it must identify the artifact.

        A constant here would make every model's re-extraction a no-op after
        the first, which is silent and permanent.
        """
        pipeline = ExtractionPipeline(
            FakeLlmProvider(model="fake/v7", script=[payload("Ada Lovelace")])
        )

        event = await pipeline.record(aggregate, document("Ada."), tenant_id)

        assert event.model_version == "fake/v7"

    async def test_re_recording_under_the_same_model_emits_nothing(self, aggregate, tenant_id):
        pipeline = ExtractionPipeline(
            FakeLlmProvider(
                by_substring={"Ada": payload("Ada Lovelace")},
            )
        )
        await pipeline.record(aggregate, document("Ada."), tenant_id)

        assert await pipeline.record(aggregate, document("Ada."), tenant_id) is None

    async def test_a_partial_extraction_is_refused_rather_than_recorded_as_complete(
        self, aggregate, tenant_id
    ):
        """The combination that would be silently unrecoverable.

        `skip_failed_chunks` makes a partial result *available*, which is
        legitimate -- a caller may want nine chunks. Writing it to the log is
        different: `Document` then holds this model version, and the retry
        that would have fixed it returns `None` forever.
        """
        pipeline = ExtractionPipeline(
            FakeLlmProvider(by_substring={"Ada": EMPTY, "Charles": payload("Charles Babbage")}),
            chunker=small_chunker(),
            skip_failed_chunks=True,
        )

        with pytest.raises(PartialExtractionError):
            await pipeline.record(
                aggregate, document("Ada Lovelace. " * 12 + "Charles Babbage. " * 12), tenant_id
            )

    async def test_a_partial_extraction_can_be_recorded_when_asked_for_explicitly(
        self, aggregate, tenant_id
    ):
        pipeline = ExtractionPipeline(
            FakeLlmProvider(by_substring={"Ada": EMPTY, "Charles": payload("Charles Babbage")}),
            chunker=small_chunker(),
            skip_failed_chunks=True,
        )

        event = await pipeline.record(
            aggregate,
            document("Ada Lovelace. " * 12 + "Charles Babbage. " * 12),
            tenant_id,
            allow_partial=True,
        )

        assert event is not None

    async def test_the_refusal_leaves_the_aggregate_untouched(self, aggregate, tenant_id):
        """Otherwise the refusal would itself cause the damage it prevents."""
        pipeline = ExtractionPipeline(
            FakeLlmProvider(by_substring={"Ada": EMPTY, "Charles": payload("Charles Babbage")}),
            chunker=small_chunker(),
            skip_failed_chunks=True,
        )

        with pytest.raises(PartialExtractionError):
            await pipeline.record(
                aggregate, document("Ada Lovelace. " * 12 + "Charles Babbage. " * 12), tenant_id
            )

        assert aggregate.uncommitted_events == []

    async def test_an_empty_extraction_is_recorded_rather_than_skipped(self, aggregate, tenant_id):
        """ "This document held nothing" is a finding and belongs in the log.

        Skipping it would leave the document looking un-extracted, so every
        replay and every backfill would try it again forever.
        """
        pipeline = ExtractionPipeline(FakeLlmProvider(script=[payload()]))

        event = await pipeline.record(aggregate, document("The weather was fine."), tenant_id)

        assert event is not None
        assert event.entities == []


class TestNoStoreReachesExtraction:
    def test_the_pipeline_takes_no_store_of_any_kind(self):
        """The architectural claim of the whole slice, asserted rather than asserted-to.

        Extraction emits events; projections write. A `GraphStore` parameter
        appearing here would be the re-architecture quietly reverting, and it
        would look perfectly reasonable in review.
        """
        import inspect

        parameters = set(inspect.signature(ExtractionPipeline.__init__).parameters)

        assert not {p for p in parameters if "store" in p.lower()}


class TestPromptIsExtractionsBusiness:
    async def test_a_custom_system_prompt_is_accepted(self, tenant_id):
        """Pinned as a constructor argument because domain schemas supply one.

        Asserted by reading it back rather than by observing the provider,
        which would be a mock assertion about a real implementation.
        """
        pipeline = ExtractionPipeline(
            FakeLlmProvider(script=[payload("Ada Lovelace")]),
            system_prompt="Extract only mathematicians.",
        )

        assert pipeline.system_prompt == "Extract only mathematicians."
        result = await pipeline.extract(document("Ada Lovelace."), tenant_id)
        assert [e.name for e in result.entities] == ["Ada Lovelace"]

    def test_there_is_a_default_prompt_and_it_is_not_empty(self):
        """A blank system prompt gets shapeless output from every real model."""
        assert ExtractionPipeline(FakeLlmProvider(script=[{}])).system_prompt.strip()
