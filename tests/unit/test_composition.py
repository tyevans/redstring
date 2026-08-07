"""`build_graph`: the one function that joins the write model to the read model.

The end-to-end example (`test_end_to_end_example.py`) proves the happy path
composes. This file covers what the example does not show: the model-call
budget, domain selection, the partial-extraction refusal, and what the report
says.

Nothing here is mocked. `FakeLlmProvider` validates its canned payloads
against the caller's schema, and `InMemoryGraphStore` is the same adapter the
port-compliance suite runs against.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from redstring import (
    AUTO,
    EMPTY,
    FakeLlmProvider,
    InMemoryGraphStore,
    SourceDocument,
    build_graph,
    domain_system_prompt,
)
from redstring.chunks.adapters.memory import InMemoryChunkStore  # exported in Task 10
from redstring.domain.exceptions import LlmProviderError
from redstring.extraction.pipeline import DEFAULT_SYSTEM_PROMPT, PartialExtractionError

TENANT_ID = uuid4()

TWO_PEOPLE = {
    "entities": [
        {"name": "Ada Lovelace", "entity_type": "Person"},
        {"name": "Charles Babbage", "entity_type": "Person"},
    ],
    "relationships": [
        {
            "source_name": "Ada Lovelace",
            "target_name": "Charles Babbage",
            "relationship_type": "WORKED_WITH",
        }
    ],
}


class CountingProvider:
    """A real provider that records every call's text and system prompt."""

    def __init__(self, *, answer=TWO_PEOPLE, fail_on: str | None = None) -> None:
        self._inner = FakeLlmProvider(by_substring={}, default=answer)
        self._fail_on = fail_on
        self.calls: list[tuple[str, str | None]] = []

    @property
    def model(self) -> str:
        return self._inner.model

    async def extract(self, text, schema, *, system_prompt=None):
        self.calls.append((text, system_prompt))
        if self._fail_on is not None and self._fail_on in text:
            raise LlmProviderError("the server said no", model=self.model)
        return await self._inner.extract(text, schema, system_prompt=system_prompt)


def document(text: str = "Ada Lovelace worked with Charles Babbage.") -> SourceDocument:
    return SourceDocument(id="doc-1", text=text)


class TestTheModelIsAskedOnce:
    async def test_a_one_chunk_document_costs_exactly_one_call(self) -> None:
        # `build_graph` needs both the event (for the projection) and the
        # counters (for the report), and the obvious way to get both is to
        # call `extract` and then `record` -- which extracts a second time.
        # That is invisible in every assertion about the resulting graph,
        # because the second extraction produces the same entities. It is
        # only visible here, and on the bill.
        provider = CountingProvider()

        await build_graph(
            document(), provider=provider, store=InMemoryGraphStore(), tenant_id=TENANT_ID
        )

        assert len(provider.calls) == 1

    async def test_a_domain_costs_no_extra_call(self) -> None:
        provider = CountingProvider()

        await build_graph(
            document(),
            provider=provider,
            store=InMemoryGraphStore(),
            tenant_id=TENANT_ID,
            domain="literature_fiction",
        )

        assert len(provider.calls) == 1

    async def test_auto_costs_exactly_one_extra_call_for_the_classifier(self) -> None:
        # Once per document, not once per chunk: the classifier sees the head
        # of the text, and a per-chunk classifier would multiply the bill by
        # the chunk count while producing the same answer each time.
        classifiable = "Hamlet " * 40
        provider = CountingProvider(
            answer={"domain": "literature_fiction", "confidence": 0.9, "reasoning": "a play"}
        )

        await build_graph(
            document(classifiable),
            provider=provider,
            store=InMemoryGraphStore(),
            tenant_id=TENANT_ID,
            domain=AUTO,
        )

        assert len(provider.calls) == 2


class TestTheSignatureAndTheReportAreThemselvesContracts:
    """Two shapes no behavioural test can see. Both found by cosmic-ray.

    A surviving mutant in each case produced a `build_graph` that did the
    right thing and a `GraphBuildReport` that held the right numbers, so
    every other test in this file passed.
    """

    async def test_everything_after_the_document_must_be_passed_by_name(self) -> None:
        # `*` mutated to `/` survived: keyword calls keep working, and
        # `build_graph(doc, provider, store, tenant)` quietly becomes legal.
        # `provider`, `store` and `tenant_id` are three arguments of three
        # unrelated types whose positional order nothing would remind a
        # caller of, and the point of the `*` is that they never have one.
        with pytest.raises(TypeError):
            await build_graph(  # type: ignore[misc]
                document(), CountingProvider(), InMemoryGraphStore(), TENANT_ID
            )

    async def test_the_report_cannot_be_edited_after_the_fact(self) -> None:
        # `frozen=True` mutated to `frozen=False` survived. A report is a
        # record of what happened; a caller that can rewrite `entities` can
        # make a log line disagree with the store it describes.
        report = await build_graph(
            document(), provider=CountingProvider(), store=InMemoryGraphStore(), tenant_id=TENANT_ID
        )

        with pytest.raises(AttributeError):
            report.entities = 99  # type: ignore[misc]


class TestWhichPromptIsSent:
    async def test_no_domain_sends_the_general_prompt(self) -> None:
        provider = CountingProvider()

        report = await build_graph(
            document(), provider=provider, store=InMemoryGraphStore(), tenant_id=TENANT_ID
        )

        assert {prompt for _, prompt in provider.calls} == {DEFAULT_SYSTEM_PROMPT}
        assert report.domain is None

    async def test_a_domain_id_sends_that_domain_s_prompt(self) -> None:
        provider = CountingProvider()

        report = await build_graph(
            document(),
            provider=provider,
            store=InMemoryGraphStore(),
            tenant_id=TENANT_ID,
            domain="news_journalism",
        )

        assert {prompt for _, prompt in provider.calls} == {domain_system_prompt("news_journalism")}
        assert report.domain == "news_journalism"

    async def test_auto_extracts_with_the_domain_the_classifier_chose(self) -> None:
        provider = CountingProvider(
            answer={"domain": "academic_research", "confidence": 0.9, "reasoning": "a paper"}
        )

        report = await build_graph(
            document("Hamlet " * 40),
            provider=provider,
            store=InMemoryGraphStore(),
            tenant_id=TENANT_ID,
            domain=AUTO,
        )

        assert report.domain == "academic_research"
        # The classification call itself is first and carries the classifier's
        # own prompt; the extraction call after it is the one that must carry
        # the chosen domain's. Asserting on the set would pass if they were
        # swapped.
        _, extraction_prompt = provider.calls[-1]
        assert extraction_prompt == domain_system_prompt("academic_research")


class TestAClassifierThatGaveUpIsDistinguishableFromOneThatChose:
    """`report.domain` alone cannot tell them apart, and both are common.

    `ContentClassifier` falls back to `encyclopedia_wiki` with confidence 0.0
    on three separate paths -- content under 100 characters is never sent at
    all, a below-threshold answer is replaced, and an `LlmProviderError` is
    swallowed. All three produce a report that reads exactly like a confident
    classification into that domain.

    That is the failure shape this project keeps finding: a plausible answer
    nobody investigates. The confidence was already computed and discarded.
    """

    async def test_a_confident_classification_reports_its_confidence(self) -> None:
        provider = CountingProvider(
            answer={"domain": "academic_research", "confidence": 0.92, "reasoning": "a paper"}
        )

        report = await build_graph(
            document("Hamlet " * 40),
            provider=provider,
            store=InMemoryGraphStore(),
            tenant_id=TENANT_ID,
            domain=AUTO,
        )

        assert report.domain == "academic_research"
        assert report.domain_confidence == pytest.approx(0.92)

    async def test_a_document_too_short_to_classify_says_so(self) -> None:
        # Under MIN_CONTENT_LENGTH: no model call is made for classification
        # at all, and the domain is the fallback. Without the confidence this
        # is indistinguishable from the classifier having picked
        # `encyclopedia_wiki` on purpose.
        report = await build_graph(
            document("Ada met Charles."),
            provider=CountingProvider(),
            store=InMemoryGraphStore(),
            tenant_id=TENANT_ID,
            domain=AUTO,
        )

        assert report.domain == "encyclopedia_wiki"
        assert report.domain_confidence == 0.0

    async def test_a_failed_classification_says_so(self) -> None:
        report = await build_graph(
            document("Hamlet " * 40),
            provider=CountingProvider(answer=EMPTY),
            store=InMemoryGraphStore(),
            tenant_id=TENANT_ID,
            domain=AUTO,
            skip_failed_chunks=True,
            allow_partial=True,
        )

        assert report.domain == "encyclopedia_wiki"
        assert report.domain_confidence == 0.0

    async def test_without_auto_there_is_no_confidence_to_report(self) -> None:
        # `None`, not 0.0. A caller filtering on `domain_confidence == 0.0` to
        # find give-ups must not catch every run that named its domain.
        report = await build_graph(
            document(),
            provider=CountingProvider(),
            store=InMemoryGraphStore(),
            tenant_id=TENANT_ID,
            domain="news_journalism",
        )

        assert report.domain == "news_journalism"
        assert report.domain_confidence is None


class TestWhatLandsInTheStore:
    async def test_the_entities_and_the_edge_between_them(self) -> None:
        store = InMemoryGraphStore()

        report = await build_graph(
            document(), provider=CountingProvider(), store=store, tenant_id=TENANT_ID
        )

        entities = await store.find_entities(TENANT_ID)
        assert sorted(entity.name for entity in entities) == ["Ada Lovelace", "Charles Babbage"]
        assert report.entities == 2
        assert report.relationships == 1
        assert report.event is not None
        assert report.event.model_version == CountingProvider().model

    async def test_nothing_is_written_for_another_tenant(self) -> None:
        store = InMemoryGraphStore()
        other = uuid4()

        await build_graph(document(), provider=CountingProvider(), store=store, tenant_id=TENANT_ID)

        assert await store.find_entities(other) == []

    async def test_the_returned_event_is_what_the_projection_consumed(self) -> None:
        # It is returned so a caller with an event store can append it. If it
        # were a copy, or a different event, the log and the store would
        # diverge from the first append.
        store = InMemoryGraphStore()

        report = await build_graph(
            document(), provider=CountingProvider(), store=store, tenant_id=TENANT_ID
        )

        assert report.event is not None
        stored = await store.find_entities(TENANT_ID)
        assert {entity.id for entity in stored} == {entity.id for entity in report.event.entities}


class TestAPartialExtractionIsRefusedBeforeAnythingIsWritten:
    async def test_it_raises_and_leaves_the_store_empty(self) -> None:
        store = InMemoryGraphStore()
        provider = CountingProvider(fail_on="Ada")

        with pytest.raises(PartialExtractionError):
            await build_graph(
                document(),
                provider=provider,
                store=store,
                tenant_id=TENANT_ID,
                skip_failed_chunks=True,
            )

        # The refusal must not itself create the gap it prevents.
        assert await store.find_entities(TENANT_ID) == []

    async def test_allow_partial_writes_what_survived_and_counts_what_did_not(self) -> None:
        store = InMemoryGraphStore()

        report = await build_graph(
            document(),
            provider=CountingProvider(fail_on="Ada"),
            store=store,
            tenant_id=TENANT_ID,
            skip_failed_chunks=True,
            allow_partial=True,
        )

        assert report.failed_chunks == 1
        assert report.total_chunks == 1
        assert report.entities == 0

    async def test_without_skip_failed_chunks_the_provider_error_propagates(self) -> None:
        with pytest.raises(LlmProviderError):
            await build_graph(
                document(),
                provider=CountingProvider(fail_on="Ada"),
                store=InMemoryGraphStore(),
                tenant_id=TENANT_ID,
            )


class TestTheCorpusIsOptionalAndSeparate:
    """`chunks=` maintains a `ChunkStore` beside the graph, or nothing does.

    `InMemoryChunkStore` is the same adapter the port-compliance suite runs
    against, not a spy: what these assert is what a Postgres corpus would
    hold.
    """

    async def test_build_graph_without_a_chunk_store_still_builds_the_graph(self) -> None:
        report = await build_graph(
            document(),
            provider=CountingProvider(),
            store=InMemoryGraphStore(),
            tenant_id=TENANT_ID,
        )

        assert report.entities == 2
        assert report.chunks_written == 0
        # Not the same field wired twice: the document was split into
        # something, and none of it was stored.
        assert report.total_chunks == 1

    async def test_build_graph_with_a_chunk_store_populates_it(self) -> None:
        graph = InMemoryGraphStore()
        corpus = InMemoryChunkStore()

        report = await build_graph(
            document(),
            provider=CountingProvider(),
            store=graph,
            tenant_id=TENANT_ID,
            chunks=corpus,
        )

        stored = await corpus.get_by_source("doc-1", TENANT_ID)
        assert report.chunks_written == len(stored) == 1
        assert [chunk.text for chunk in stored] == [document().text]
        # The passages hold the ids the graph holds -- the link is only worth
        # anything if it resolves on the other side.
        in_graph = {entity.id for entity in await graph.find_entities(TENANT_ID)}
        linked = {entity_id for chunk in stored for entity_id in chunk.entity_ids}
        assert linked
        assert linked <= in_graph

    async def test_a_multi_chunk_document_stores_every_passage(self) -> None:
        """One passage would satisfy the test above and hide a fold that keeps
        only the last chunk."""
        corpus = InMemoryChunkStore()
        long_document = SourceDocument(id="doc-1", text="Ada Lovelace worked. " * 200)

        report = await build_graph(
            long_document,
            provider=CountingProvider(),
            store=InMemoryGraphStore(),
            tenant_id=TENANT_ID,
            chunks=corpus,
        )

        stored = await corpus.get_by_source("doc-1", TENANT_ID)
        assert report.total_chunks > 1
        assert report.chunks_written == len(stored) == report.total_chunks

    async def test_the_corpus_is_scoped_to_the_tenant_that_built_it(self) -> None:
        corpus = InMemoryChunkStore()

        await build_graph(
            document(),
            provider=CountingProvider(),
            store=InMemoryGraphStore(),
            tenant_id=TENANT_ID,
            chunks=corpus,
        )

        assert await corpus.get_by_source("doc-1", uuid4()) == []
