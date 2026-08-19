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

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from redstring.aggregates.document import Document
from redstring.domain.exceptions import (
    EmptyCompletionError,
    LlmProviderError,
    MalformedCompletionError,
)
from redstring.domain.source import SourceDocument
from redstring.events import DocumentExtracted
from redstring.events.streams import document_stream
from redstring.extraction.chunkers import SlidingWindowChunker
from redstring.extraction.pipeline import (
    DEFAULT_SYSTEM_PROMPT,
    ExtractionPipeline,
    PartialExtractionError,
    PipelineResult,
)
from redstring.llm.adapters.fake import EMPTY, FakeLlmProvider

MODEL = "fake/canned-v1"

#: The observation instant every call here supplies. Fixed rather than
#: `datetime.now(UTC)`: several tests below compare two extractions of the
#: same document for equality, and a per-call clock would make them differ for
#: a reason that has nothing to do with what they are testing.
OBSERVED = datetime(2026, 2, 9, 11, 7, tzinfo=UTC)


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

        result = await pipeline.extract(
            document("Ada Lovelace was a mathematician."), tenant_id, observed_at=OBSERVED
        )

        assert [e.name for e in result.entities] == ["Ada Lovelace"]

    async def test_the_entities_carry_the_document_and_the_model_that_found_them(self, tenant_id):
        pipeline = ExtractionPipeline(
            FakeLlmProvider(model="fake/v7", script=[payload("Ada Lovelace")])
        )

        result = await pipeline.extract(
            document("Ada was a mathematician.", "essay-9"), tenant_id, observed_at=OBSERVED
        )

        [ada] = result.entities
        assert (ada.provenance.source_id, ada.provenance.model, ada.tenant_id) == (
            "essay-9",
            "fake/v7",
            tenant_id,
        )

    async def test_the_pipeline_stamps_every_entity_with_the_observation_instant(self, tenant_id):
        """The instant comes from the caller and reaches every entity.

        Asserted as a *set* over several entities rather than on one, because
        the claim is that they all share one instant: an implementation
        reading a clock per entity would satisfy a single-entity assertion and
        fail this.
        """
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

        result = await pipeline.extract(document(text), tenant_id, observed_at=OBSERVED)

        assert len(result.entities) > 1
        assert {e.provenance.observed_at for e in result.entities} == {OBSERVED}

    async def test_two_extractions_of_one_document_agree_on_observed_at(self, tenant_id):
        """The determinism `observed_at` being an argument buys. A clock below
        `composition` would make these differ, and nothing else in this file
        would notice."""
        pipeline = ExtractionPipeline(FakeLlmProvider(script=[payload("Ada"), payload("Ada")]))
        doc = document("Ada Lovelace was a mathematician.")

        first = await pipeline.extract(doc, tenant_id, observed_at=OBSERVED)
        second = await pipeline.extract(doc, tenant_id, observed_at=OBSERVED)

        assert first.entities == second.entities

    async def test_a_document_the_model_found_nothing_in_extracts_to_nothing(self, tenant_id):
        """Not an error. This is the outcome every failure path must stay distinct from."""
        pipeline = ExtractionPipeline(FakeLlmProvider(script=[payload()]))

        result = await pipeline.extract(
            document("The weather was fine."), tenant_id, observed_at=OBSERVED
        )

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

        result = await pipeline.extract(document(text), tenant_id, observed_at=OBSERVED)

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

        result = await pipeline.extract(document(text), tenant_id, observed_at=OBSERVED)

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

        result = await pipeline.extract(document(text), tenant_id, observed_at=OBSERVED)

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
                document("Ada Lovelace. " * 12 + "Charles Babbage. " * 12),
                tenant_id,
                observed_at=OBSERVED,
            )

    async def test_a_malformed_chunk_also_fails_the_document_by_default(self, tenant_id):
        pipeline = ExtractionPipeline(
            FakeLlmProvider(script=[{"entities": [{"name": "Ada"}]}]),
        )

        with pytest.raises(MalformedCompletionError):
            await pipeline.extract(document("Ada Lovelace."), tenant_id, observed_at=OBSERVED)

    async def test_skipping_failed_chunks_keeps_the_rest_and_counts_the_losses(self, tenant_id):
        pipeline = ExtractionPipeline(
            FakeLlmProvider(by_substring={"Ada": EMPTY, "Charles": payload("Charles Babbage")}),
            chunker=small_chunker(),
            skip_failed_chunks=True,
        )

        result = await pipeline.extract(
            document("Ada Lovelace. " * 12 + "Charles Babbage. " * 12),
            tenant_id,
            observed_at=OBSERVED,
        )

        assert [e.name for e in result.entities] == ["Charles Babbage"]
        assert result.failed_chunks >= 1


class TestRecording:
    async def test_recording_emits_one_event_carrying_everything_found(self, aggregate, tenant_id):
        pipeline = ExtractionPipeline(
            FakeLlmProvider(script=[payload("Ada Lovelace", "Charles Babbage")])
        )

        event = await pipeline.record(
            aggregate, document("Ada and Charles."), tenant_id, observed_at=OBSERVED
        )

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

        event = await pipeline.record(aggregate, document("Ada."), tenant_id, observed_at=OBSERVED)

        assert event.model_version == "fake/v7"

    async def test_re_recording_under_the_same_model_emits_nothing(self, aggregate, tenant_id):
        pipeline = ExtractionPipeline(
            FakeLlmProvider(
                by_substring={"Ada": payload("Ada Lovelace")},
            )
        )
        await pipeline.record(aggregate, document("Ada."), tenant_id, observed_at=OBSERVED)

        assert (
            await pipeline.record(aggregate, document("Ada."), tenant_id, observed_at=OBSERVED)
            is None
        )

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
                aggregate,
                document("Ada Lovelace. " * 12 + "Charles Babbage. " * 12),
                tenant_id,
                observed_at=OBSERVED,
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
            observed_at=OBSERVED,
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
                aggregate,
                document("Ada Lovelace. " * 12 + "Charles Babbage. " * 12),
                tenant_id,
                observed_at=OBSERVED,
            )

        assert aggregate.uncommitted_events == []

    async def test_an_empty_extraction_is_recorded_rather_than_skipped(self, aggregate, tenant_id):
        """ "This document held nothing" is a finding and belongs in the log.

        Skipping it would leave the document looking un-extracted, so every
        replay and every backfill would try it again forever.
        """
        pipeline = ExtractionPipeline(FakeLlmProvider(script=[payload()]))

        event = await pipeline.record(
            aggregate, document("The weather was fine."), tenant_id, observed_at=OBSERVED
        )

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
        result = await pipeline.extract(document("Ada Lovelace."), tenant_id, observed_at=OBSERVED)
        assert [e.name for e in result.entities] == ["Ada Lovelace"]

    def test_there_is_a_default_prompt_and_it_is_not_empty(self):
        """A blank system prompt gets shapeless output from every real model."""
        assert ExtractionPipeline(FakeLlmProvider(script=[{}])).system_prompt.strip()


ONE_ENTITY = {"entities": [{"name": "Ada", "entity_type": "P"}]}


class TestRecordGetsItsInstantFromExactlyOnePlace:
    """`record` has two ways to be given an observation instant, so the
    precedence between them is pinned rather than only documented -- defect
    shape §2, which asks for a test that sets both to *conflicting* values.
    """

    async def test_neither_a_result_nor_an_observed_at_is_refused(self, aggregate, tenant_id):
        """`record` extracts when given no `result`, and extracting needs an
        instant. This layer is below `composition` and reads no clock, so the
        only honest answer is to refuse -- defaulting here is how the
        determinism `observed_at` exists to provide leaks away one caller at a
        time.
        """
        pipeline = ExtractionPipeline(FakeLlmProvider(by_substring={"Ada": ONE_ENTITY}))

        with pytest.raises(ValueError, match="observation instant"):
            await pipeline.record(aggregate, document("Ada."), tenant_id)

    async def test_a_supplied_result_wins_over_a_conflicting_observed_at(
        self, aggregate, tenant_id
    ):
        """The two instants are made to disagree, which is the whole point.

        Passing `observed_at=B` alongside a `result` stamped `A` must record
        `A`: the result already carries the instant its own caller chose, and
        honouring `B` would mean the recorded entities disagree with the
        `PipelineResult` the caller is holding. With both set to the same
        value -- the natural way to write this test -- an implementation that
        silently re-extracted with `B` would pass.
        """
        other_instant = datetime(2011, 5, 6, 1, 2, tzinfo=UTC)
        assert other_instant != OBSERVED, "the two instants must differ or this proves nothing"

        pipeline = ExtractionPipeline(FakeLlmProvider(by_substring={"Ada": ONE_ENTITY}))
        result = await pipeline.extract(document("Ada."), tenant_id, observed_at=OBSERVED)

        event = await pipeline.record(
            aggregate, document("Ada."), tenant_id, observed_at=other_instant, result=result
        )

        assert event is not None
        assert {e.provenance.observed_at for e in event.entities} == {OBSERVED}

    async def test_an_observed_at_is_used_when_there_is_no_result(self, aggregate, tenant_id):
        """The other side of the precedence. Without this, `record` could
        ignore `observed_at` entirely and the test above would still pass."""
        pipeline = ExtractionPipeline(FakeLlmProvider(by_substring={"Ada": ONE_ENTITY}))

        event = await pipeline.record(aggregate, document("Ada."), tenant_id, observed_at=OBSERVED)

        assert event is not None
        assert {e.provenance.observed_at for e in event.entities} == {OBSERVED}


class TestRecordRefusesAResultFromAnotherDocument:
    """The one misuse `result=` admits is already refused, one layer down.

    Slice 10's review asked for a guard in `record`, on the premise that
    passing document B's extraction while recording document A would write B's
    entities into A's stream with nothing raising. **That premise is wrong,
    and these tests are what establish it.** `DocumentExtracted` has carried
    `_payloads_belong_to_this_document_and_tenant` since slice 5b:

        ValidationError: entities must be attributed to the document they were
        extracted from; found source_id ['doc-2'] in an event for 'doc-1'

    which is a `ValueError`, because pydantic's is. So the misuse is caught,
    and caught in the better place: the invariant belongs to the event, where
    it also holds for a caller who builds one directly, rather than to the one
    method that happens to have an argument admitting the mistake.

    Adding a second check in `record` would have been a duplicate invariant
    that can drift from the first -- the shape this codebase has removed twice
    already, in `prompt_generator`'s JSON schema and in `prompts.py`'s
    hand-synchronised vocabulary lists. What was missing was not the guard but
    the test, so `record`'s docstring could say "the aggregate refuses it"
    instead of "do not do this".
    """

    async def test_a_result_for_a_different_document_is_refused(self, tenant_id) -> None:
        pipeline = ExtractionPipeline(FakeLlmProvider(by_substring={"Ada": ONE_ENTITY}))
        other = await pipeline.extract(
            SourceDocument(id="doc-2", text="Ada."), tenant_id, observed_at=OBSERVED
        )

        with pytest.raises(ValueError, match="doc-2"):
            await pipeline.record(
                Document(document_stream(tenant_id=tenant_id, source_id="doc-1").aggregate_id),
                SourceDocument(id="doc-1", text="Ada."),
                tenant_id,
                result=other,
            )

    async def test_the_matching_result_is_accepted(self, tenant_id) -> None:
        pipeline = ExtractionPipeline(FakeLlmProvider(by_substring={"Ada": ONE_ENTITY}))
        document = SourceDocument(id="doc-1", text="Ada.")
        result = await pipeline.extract(document, tenant_id, observed_at=OBSERVED)

        event = await pipeline.record(
            Document(document_stream(tenant_id=tenant_id, source_id="doc-1").aggregate_id),
            document,
            tenant_id,
            result=result,
        )

        assert event is not None
        assert [entity.name for entity in event.entities] == ["Ada"]

    async def test_an_empty_result_is_accepted_for_any_document(self, tenant_id) -> None:
        # There is no entity to carry a `source_id`, so there is nothing to
        # check against -- and an extraction that found nothing is a finding,
        # not an error. The guard must not turn it into one.
        pipeline = ExtractionPipeline(FakeLlmProvider(by_substring={}, default={}))
        document = SourceDocument(id="doc-1", text="Nothing here.")
        result = await pipeline.extract(document, tenant_id, observed_at=OBSERVED)

        assert result.entities == []
        assert (
            await pipeline.record(
                Document(document_stream(tenant_id=tenant_id, source_id="doc-1").aggregate_id),
                document,
                tenant_id,
                result=result,
            )
            is not None
        )


class TestTheChunkingIsCarriedOut:
    """The passages, and which entities each one produced.

    `merge_extractions` folds the chunk boundary away, so every assertion here
    is about something captured one loop iteration earlier. The documents are
    built so that each chunk names a *different* entity: a payload attaching
    every entity to every chunk satisfies "some chunk has some entities" and
    fails these.
    """

    async def test_an_extracted_chunk_records_the_entities_it_produced(self, tenant_id):
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

        result = await pipeline.extract(document(text), tenant_id, observed_at=OBSERVED)

        by_id = {entity.id: entity.name for entity in result.entities}
        named = [{by_id[entity_id] for entity_id in chunk.entity_ids} for chunk in result.chunks]
        # The *mapping*. A chunk may only be credited with a name its own text
        # spells -- which is what a payload attaching every entity to every
        # chunk violates, on every chunk that names only one of them.
        for chunk, names in zip(result.chunks, named, strict=True):
            assert names <= {name for name in by_id.values() if name in chunk.text}
        # And both names really were credited to *some* chunk, so the check
        # above is not satisfied by attaching nothing to anything.
        assert {"Ada Lovelace"} in named
        assert {"Charles Babbage"} in named

    async def test_the_two_entities_really_did_come_from_different_chunks(self, tenant_id):
        """Guards the test above, which proves nothing if every chunk holds both."""
        chunks = small_chunker().chunk("Ada Lovelace. " * 12 + "Charles Babbage. " * 12).chunks

        assert any("Ada Lovelace" in c.text and "Charles Babbage" not in c.text for c in chunks)
        assert any("Charles Babbage" in c.text and "Ada Lovelace" not in c.text for c in chunks)

    async def test_a_chunk_that_produced_no_entities_records_an_empty_list(self, tenant_id):
        """A barren chunk *followed by* a productive one, so an early exit differs.

        Stopping at the first chunk with nothing in it would leave the later
        chunk's entities unattributed, and on a document whose empty chunk is
        last that is indistinguishable from correct.
        """
        text = "The weather was fine. " * 8 + "Ada Lovelace. " * 12
        pipeline = ExtractionPipeline(
            FakeLlmProvider(
                by_substring={"Ada Lovelace": payload("Ada Lovelace")},
                # An answer naming nothing, not `EMPTY` -- that is an empty
                # *completion*, which is a provider failure and raises.
                default=payload(),
            ),
            chunker=small_chunker(),
        )

        result = await pipeline.extract(document(text), tenant_id, observed_at=OBSERVED)

        barren = [c for c in result.chunks if "Ada Lovelace" not in c.text]
        productive = [c for c in result.chunks if "Ada Lovelace" in c.text]
        assert barren
        assert productive
        assert all(chunk.entity_ids == [] for chunk in barren)
        assert all(chunk.entity_ids for chunk in productive)

    async def test_a_failed_chunk_is_still_stored_as_a_passage_with_no_links(self, tenant_id):
        """The corpus is a faithful split, so a model failure is not a hole in it.

        The failing chunk is not the last one: a loop that abandoned the
        chunking on the first failure would still produce a complete-looking
        result if the failure came at the end.
        """
        text = "The weather was fine. " * 8 + "Ada Lovelace. " * 12
        pipeline = ExtractionPipeline(
            FailOnSubstring("weather", payload("Ada Lovelace")),
            chunker=small_chunker(),
            skip_failed_chunks=True,
        )

        result = await pipeline.extract(document(text), tenant_id, observed_at=OBSERVED)

        assert result.failed_chunks
        assert len(result.chunks) == result.total_chunks
        assert any(chunk.entity_ids for chunk in result.chunks)
        assert all(chunk.entity_ids == [] for chunk in result.chunks if "weather" in chunk.text)

    async def test_every_passage_carries_the_document_and_the_tenant(self, tenant_id):
        pipeline = ExtractionPipeline(FakeLlmProvider(script=[payload("Ada Lovelace")]))

        result = await pipeline.extract(
            document("Ada Lovelace.", "essay-9"), tenant_id, observed_at=OBSERVED
        )

        assert [(c.source_id, c.tenant_id) for c in result.chunks] == [("essay-9", tenant_id)]

    async def test_the_signature_carries_the_model_version(self, tenant_id):
        """What keeps this path's key space out of `index_documents`'s.

        Asserted as a suffix on the literal model string rather than by
        rebuilding the signature from the pipeline, which would be true for
        any signature the pipeline happened to produce.
        """
        pipeline = ExtractionPipeline(
            FakeLlmProvider(model="fake/v7", script=[payload("Ada Lovelace")])
        )

        result = await pipeline.extract(document("Ada Lovelace."), tenant_id, observed_at=OBSERVED)

        assert result.chunking_signature.endswith(":fake/v7")
        assert result.chunking_signature.startswith("sliding_window:")
        assert result.chunking_signature.count(":") == 2

    async def test_a_different_split_produces_a_different_signature(self, tenant_id):
        """Chunker settings are not on the `Chunker` protocol, so the digest is
        over the split. Two sizes that really do split differently must differ."""
        text = "Ada Lovelace. " * 12
        answer = FakeLlmProvider(by_substring={"Ada": payload("Ada Lovelace")})

        one = await ExtractionPipeline(answer).extract(
            document(text), tenant_id, observed_at=OBSERVED
        )
        many = await ExtractionPipeline(answer, chunker=small_chunker()).extract(
            document(text), tenant_id, observed_at=OBSERVED
        )

        assert one.total_chunks != many.total_chunks
        assert one.chunking_signature != many.chunking_signature

    async def test_the_same_document_chunked_the_same_way_signs_the_same(self, tenant_id):
        text = "Ada Lovelace. " * 12
        answer = FakeLlmProvider(by_substring={"Ada": payload("Ada Lovelace")})

        first = await ExtractionPipeline(answer, chunker=small_chunker()).extract(
            document(text), tenant_id, observed_at=OBSERVED
        )
        second = await ExtractionPipeline(answer, chunker=small_chunker()).extract(
            document(text), tenant_id, observed_at=OBSERVED
        )

        assert first.chunking_signature == second.chunking_signature

    async def test_a_repeated_passage_becomes_one_content_addressed_row(self, tenant_id):
        """Two identical chunks share one id, so passing both would drop one
        silently -- and which one depends on the adapter's write order."""
        pipeline = ExtractionPipeline(
            FakeLlmProvider(by_substring={"Ada": payload("Ada Lovelace")}),
            chunker=SlidingWindowChunker(
                default_chunk_size=14, default_overlap=0, min_chunk_size=1
            ),
        )

        result = await pipeline.extract(
            document("Ada Lovelace. " * 4), tenant_id, observed_at=OBSERVED
        )

        assert result.total_chunks > len(result.chunks)
        assert len({chunk.id for chunk in result.chunks}) == len(result.chunks)


class FailOnSubstring:
    """A real provider that refuses the chunks holding `marker`.

    `FakeLlmProvider` has no way to fail on some chunks and answer others, and
    a test that failed *every* chunk could not tell "the failure was skipped"
    from "nothing was ever asked".
    """

    def __init__(self, marker: str, answer: dict) -> None:
        self._marker = marker
        self._inner = FakeLlmProvider(by_substring={}, default=answer)

    @property
    def model(self) -> str:
        return self._inner.model

    async def extract(self, text, schema, *, system_prompt=None):
        if self._marker in text:
            raise LlmProviderError("the server said no", model=self.model)
        return await self._inner.extract(text, schema, system_prompt=system_prompt)


class RecordingProvider:
    """A real provider that keeps the system prompt it was sent per chunk.

    `FakeLlmProvider` accepts `system_prompt` and ignores it deliberately, so
    nothing in this suite could see the carryover block at all -- which is the
    §3 shape waiting to happen: the whole feature could be wired to nothing
    and every existing test would stay green.

    It delegates rather than reimplements, so what the pipeline gets back is
    still validated through the same gate the LangChain adapter uses.
    """

    def __init__(self, **kwargs) -> None:
        self._inner = FakeLlmProvider(**kwargs)
        self.prompts: list[str] = []
        self.texts: list[str] = []

    @property
    def model(self) -> str:
        return self._inner.model

    async def extract(self, text, schema, *, system_prompt=None):
        self.prompts.append(system_prompt or "")
        self.texts.append(text)
        return await self._inner.extract(text, schema, system_prompt=system_prompt)


#: Two paragraphs, each short enough that `small_chunker` puts them in
#: separate chunks and long enough that it does not merge them. Written so the
#: *second* names nobody the first does: a document where every chunk mentions
#: every entity cannot show that anything was carried.
TWO_PARAGRAPHS = (
    "Ada Lovelace wrote the first published algorithm, intended to be "
    "carried out by a machine, in eighteen forty-three.\n\n"
    "Charles Babbage designed that machine, and the two of them corresponded "
    "about it for a great many years afterwards."
)

CARRYOVER_ANSWERS = {
    "Lovelace": payload("Ada Lovelace"),
    "Babbage": payload("Charles Babbage"),
}


class TestEachChunkIsToldWhatTheEarlierOnesFound:
    async def test_the_first_chunk_gets_the_configured_prompt_unchanged(self, tenant_id):
        """Nothing has been found yet, so there is nothing to append.

        Pinned as *equality*, not as "starts with": it is what makes the first
        chunk of a carryover run and of a baseline run the same request, so a
        quality comparison between them is about the later chunks.
        """
        provider = RecordingProvider(by_substring=CARRYOVER_ANSWERS)
        pipeline = ExtractionPipeline(provider, chunker=small_chunker())

        await pipeline.extract(document(TWO_PARAGRAPHS), tenant_id, observed_at=OBSERVED)

        assert len(provider.prompts) > 1, "the document must split for this test to mean anything"
        assert provider.prompts[0] == DEFAULT_SYSTEM_PROMPT

    async def test_a_later_chunk_is_told_what_an_earlier_one_named(self, tenant_id):
        provider = RecordingProvider(by_substring=CARRYOVER_ANSWERS)
        pipeline = ExtractionPipeline(provider, chunker=small_chunker())

        await pipeline.extract(document(TWO_PARAGRAPHS), tenant_id, observed_at=OBSERVED)

        babbage = next(i for i, text in enumerate(provider.texts) if "Babbage" in text)
        assert "Ada Lovelace (Person)" in provider.prompts[babbage]

    async def test_turning_it_off_sends_the_configured_prompt_to_every_chunk(self, tenant_id):
        """`carryover_entities=0` is the pre-feature pipeline, byte for byte."""
        provider = RecordingProvider(by_substring=CARRYOVER_ANSWERS)
        pipeline = ExtractionPipeline(provider, chunker=small_chunker(), carryover_entities=0)

        await pipeline.extract(document(TWO_PARAGRAPHS), tenant_id, observed_at=OBSERVED)

        assert len(provider.prompts) > 1
        assert set(provider.prompts) == {DEFAULT_SYSTEM_PROMPT}

    async def test_a_row_the_mapper_refused_is_never_carried(self, tenant_id):
        """The carryover is built from mapped entities, not from the answer.

        A blank name is dropped and counted by `map_extraction`; carrying it
        would offer later chunks a spelling no entity in this document has,
        and would spend a slot of the bound on nothing. Built from the raw
        answer this fails; built from the mapping it passes, and no other test
        here distinguishes the two.
        """
        provider = RecordingProvider(
            by_substring={
                "Lovelace": {
                    "entities": [
                        {"name": "   ", "entity_type": "Person"},
                        {"name": "Ada Lovelace", "entity_type": "Person"},
                    ]
                },
                "Babbage": payload("Charles Babbage"),
            }
        )
        pipeline = ExtractionPipeline(provider, chunker=small_chunker())

        result = await pipeline.extract(document(TWO_PARAGRAPHS), tenant_id, observed_at=OBSERVED)

        assert result.dropped_entities == 1
        babbage = next(i for i, text in enumerate(provider.texts) if "Babbage" in text)
        assert "Ada Lovelace (Person)" in provider.prompts[babbage]
        listed = [line for line in provider.prompts[babbage].splitlines() if line.startswith("- ")]
        assert listed == ["- Ada Lovelace (Person)"]

    def test_a_negative_bound_is_refused_at_construction(self):
        """Not on the first document. A constructor argument that validates
        later is one that validates in production."""
        with pytest.raises(ValueError, match="must be >= 0"):
            ExtractionPipeline(FakeLlmProvider(script=[{}]), carryover_entities=-1)


class TestAskingTheSameChunkTwice:
    async def test_off_by_default_means_one_call_per_chunk(self, tenant_id):
        """`gleanings` costs money, so its default has to be observable.

        A script is the right fake here precisely because the call *count* is
        what is under test.
        """
        provider = FakeLlmProvider(script=[payload("Ada Lovelace")])
        pipeline = ExtractionPipeline(provider)

        result = await pipeline.extract(document("Ada Lovelace."), tenant_id, observed_at=OBSERVED)

        assert result.gleaning_passes == 0
        assert [e.name for e in result.entities] == ["Ada Lovelace"]

    async def test_a_second_pass_adds_what_the_first_missed(self, tenant_id):
        provider = FakeLlmProvider(script=[payload("Ada Lovelace"), payload("Charles Babbage")])
        pipeline = ExtractionPipeline(provider, gleanings=1)

        result = await pipeline.extract(
            document("Ada Lovelace and Charles Babbage."), tenant_id, observed_at=OBSERVED
        )

        assert sorted(e.name for e in result.entities) == ["Ada Lovelace", "Charles Babbage"]
        assert result.gleaning_passes == 1

    async def test_the_second_pass_is_shown_the_first_answer(self, tenant_id):
        provider = RecordingProvider(script=[payload("Ada Lovelace"), payload("Charles Babbage")])
        pipeline = ExtractionPipeline(provider, gleanings=1)

        await pipeline.extract(
            document("Ada Lovelace and Charles Babbage."), tenant_id, observed_at=OBSERVED
        )

        assert "- Ada Lovelace (Person)" in provider.prompts[1]
        assert provider.texts[0] == provider.texts[1], "the same chunk, re-read"

    async def test_an_edge_between_the_two_passes_resolves(self, tenant_id):
        """The case that justifies combining before mapping rather than after.

        The first pass names Ada; the second names Charles *and* the edge
        between them. Folded after mapping, that edge has an endpoint the
        second answer never listed and is counted unresolved.
        """
        provider = FakeLlmProvider(
            script=[
                payload("Ada Lovelace"),
                payload(
                    "Charles Babbage",
                    links=[("Ada Lovelace", "Charles Babbage", "COLLABORATED_WITH")],
                ),
            ]
        )
        pipeline = ExtractionPipeline(provider, gleanings=1)

        result = await pipeline.extract(
            document("Ada Lovelace and Charles Babbage."), tenant_id, observed_at=OBSERVED
        )

        assert result.unresolved_relationships == 0
        assert [r.relationship_type for r in result.relationships] == ["COLLABORATED_WITH"]

    async def test_a_pass_that_finds_nothing_stops_the_loop(self, tenant_id):
        """Two gleanings asked for, one spent: the script has three entries
        and would raise if the pipeline called a fourth or a third time."""
        provider = FakeLlmProvider(script=[payload("Ada Lovelace"), {}])
        pipeline = ExtractionPipeline(provider, gleanings=2)

        result = await pipeline.extract(document("Ada Lovelace."), tenant_id, observed_at=OBSERVED)

        assert result.gleaning_passes == 1
        assert [e.name for e in result.entities] == ["Ada Lovelace"]

    async def test_a_failed_gleaning_keeps_the_first_answer_and_is_counted(self, tenant_id):
        """A failed second look is not a failed chunk.

        `skip_failed_chunks` is off here on purpose: were the two treated
        alike, this would raise and throw away a complete first answer over an
        optional enhancement.
        """
        provider = GleaningFails(payload("Ada Lovelace"))
        pipeline = ExtractionPipeline(provider, gleanings=1)

        result = await pipeline.extract(document("Ada Lovelace."), tenant_id, observed_at=OBSERVED)

        assert result.failed_gleanings == 1
        assert result.gleaning_passes == 0
        assert result.failed_chunks == 0, "the chunk itself succeeded"
        assert [e.name for e in result.entities] == ["Ada Lovelace"]

    def test_a_negative_count_is_refused_at_construction(self):
        with pytest.raises(ValueError, match="gleanings must be >= 0"):
            ExtractionPipeline(FakeLlmProvider(script=[{}]), gleanings=-1)


class GleaningFails:
    """Answers the first call for any text and refuses every later one.

    Keyed on the call count rather than on the prompt: a provider that failed
    on seeing the gleaning instruction would make the instruction's wording
    load-bearing in a test about failure handling.
    """

    def __init__(self, first: dict) -> None:
        self._inner = FakeLlmProvider(by_substring={}, default=first)
        self._calls = 0

    @property
    def model(self) -> str:
        return self._inner.model

    async def extract(self, text, schema, *, system_prompt=None):
        self._calls += 1
        if self._calls > 1:
            raise LlmProviderError("the server said no", model=self.model)
        return await self._inner.extract(text, schema, system_prompt=system_prompt)


#: Five segments of identical length, each opening with a marker no other
#: segment contains, chunked with **no overlap** so that every chunk holds
#: exactly one marker. `by_substring` answers on the first key it finds, so a
#: chunk that held two markers would silently make the script positional.
MENTION_SEGMENTS = tuple(f"Zulu{i}. " + "padding word " * 6 for i in range(1, 6))

#: Ada in segments 1, 3 and 5; Babbage in 2 and 5; Turing in 4 alone. Three
#: different counts, and no two reports of one entity in adjacent chunks --
#: with a single duplicate, "count" and "count = 1" are the same function.
MENTION_SCRIPT = {
    "Zulu1": payload("Ada Lovelace"),
    "Zulu2": payload("Charles Babbage"),
    "Zulu3": payload("Ada Lovelace"),
    "Zulu4": payload("Alan Turing"),
    "Zulu5": payload("Ada Lovelace", "Charles Babbage"),
}


class _FailsOneMarkedChunk:
    """Answers `MENTION_SCRIPT` for every chunk but the one holding `marker`.

    `FailOnSubstring` above takes a single default answer, which cannot serve
    a test that needs a *different* answer per chunk and one failure among
    them.
    """

    def __init__(self, marker: str) -> None:
        self._marker = marker
        self._inner = FakeLlmProvider(by_substring=MENTION_SCRIPT)

    @property
    def model(self) -> str:
        return self._inner.model

    async def extract(self, text, schema, *, system_prompt=None):
        if self._marker in text:
            raise LlmProviderError("the server said no", model=self.model)
        return await self._inner.extract(text, schema, system_prompt=system_prompt)


def mention_pipeline(**kwargs) -> ExtractionPipeline:
    return ExtractionPipeline(
        FakeLlmProvider(by_substring=MENTION_SCRIPT),
        chunker=SlidingWindowChunker(
            default_chunk_size=len(MENTION_SEGMENTS[0]), default_overlap=0, min_chunk_size=5
        ),
        **kwargs,
    )


class TestHowManyChunksReportedEachEntity:
    """`mention_counts` -- the within-document, within-run tally.

    Deliberately not a count on `Entity`: that number would ride in every
    event payload and be a fact two documents had to agree about, which makes
    it consolidation's problem and a projection's job. See BACKLOG B143.
    """

    async def test_the_document_really_did_split_into_five_marked_chunks(self, tenant_id):
        """Guards every test below, each of which would pass on one chunk."""
        chunking = mention_pipeline()._chunker.chunk("".join(MENTION_SEGMENTS))

        assert chunking.total_chunks == 5
        assert [sum(m in c.text for m in MENTION_SCRIPT) for c in chunking.chunks] == [1] * 5

    async def test_each_entity_carries_the_number_of_chunks_that_reported_it(self, tenant_id):
        result = await mention_pipeline().extract(
            document("".join(MENTION_SEGMENTS)), tenant_id, observed_at=OBSERVED
        )

        by_name = {e.name: result.mention_counts[e.id] for e in result.entities}
        assert by_name == {"Ada Lovelace": 3, "Charles Babbage": 2, "Alan Turing": 1}

    async def test_the_keys_are_exactly_the_ids_of_the_entities_returned(self, tenant_id):
        """Equality, not containment. A subset is a `KeyError` for the caller
        and a superset is a count for an entity they never receive."""
        result = await mention_pipeline().extract(
            document("".join(MENTION_SEGMENTS)), tenant_id, observed_at=OBSERVED
        )

        assert set(result.mention_counts) == {e.id for e in result.entities}
        assert all(result.mention_counts[e.id] >= 1 for e in result.entities)

    async def test_a_chunk_whose_call_failed_contributes_no_mentions(self, tenant_id):
        """The count is bounded by the chunks that answered, and says so.

        Ada is reported by chunks 1, 3 and 5; chunk 3's call fails, so the
        number the caller sees is 2 -- lower than the truth, in a way
        `failed_chunks` is what makes visible.
        """
        pipeline = ExtractionPipeline(
            _FailsOneMarkedChunk("Zulu3"),
            chunker=SlidingWindowChunker(
                default_chunk_size=len(MENTION_SEGMENTS[0]), default_overlap=0, min_chunk_size=5
            ),
            skip_failed_chunks=True,
        )

        result = await pipeline.extract(
            document("".join(MENTION_SEGMENTS)), tenant_id, observed_at=OBSERVED
        )

        by_name = {e.name: result.mention_counts[e.id] for e in result.entities}
        assert result.failed_chunks == 1
        assert by_name == {"Ada Lovelace": 2, "Charles Babbage": 2, "Alan Turing": 1}

    async def test_a_document_that_yields_nothing_yields_no_counts(self, tenant_id):
        pipeline = ExtractionPipeline(FakeLlmProvider(script=[payload()]))

        result = await pipeline.extract(document("Nothing here."), tenant_id, observed_at=OBSERVED)

        assert result.entities == []
        assert dict(result.mention_counts) == {}

    async def test_the_counts_do_not_change_when_chunks_are_extracted_concurrently(self, tenant_id):
        """`concurrency` batches the calls; it must not batch the tally."""
        serial = await mention_pipeline().extract(
            document("".join(MENTION_SEGMENTS)), tenant_id, observed_at=OBSERVED
        )
        batched = await mention_pipeline(concurrency=3).extract(
            document("".join(MENTION_SEGMENTS)), tenant_id, observed_at=OBSERVED
        )

        assert dict(batched.mention_counts) == dict(serial.mention_counts)

    def test_a_result_built_without_counts_has_an_empty_unwritable_default(self):
        """Constructed directly, not through a helper that passes every field.

        A `NamedTuple`'s default is shared by every instance that takes it, so
        a `dict` here would be a default any caller could write into and every
        later caller would then receive.
        """
        result = PipelineResult(entities=[], relationships=[])

        assert dict(result.mention_counts) == {}
        with pytest.raises(TypeError):
            result.mention_counts[uuid4()] = 1  # type: ignore[index]
        assert dict(PipelineResult(entities=[], relationships=[]).mention_counts) == {}
