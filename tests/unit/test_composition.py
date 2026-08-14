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

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from redstring import (
    AUTO,
    EMPTY,
    FakeLlmProvider,
    InMemoryChunkStore,
    InMemoryGraphStore,
    SourceDocument,
    build_graph,
    domain_system_prompt,
)
from redstring.domain.exceptions import LlmProviderError
from redstring.extraction.constrained import permitted_entity_types
from redstring.extraction.domains.registry import get_domain_schema
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
        #: The schema *class* each call was given. The vocabulary constraint
        #: lives in the type rather than in the answer, so nothing about the
        #: entities that come back can show whether it was applied -- this is
        #: the only place the wiring is observable without a live server.
        self.schemas: list[type] = []

    @property
    def model(self) -> str:
        return self._inner.model

    async def extract(self, text, schema, *, system_prompt=None):
        self.calls.append((text, system_prompt))
        self.schemas.append(schema)
        if self._fail_on is not None and self._fail_on in text:
            raise LlmProviderError("the server said no", model=self.model)
        return await self._inner.extract(text, schema, system_prompt=system_prompt)


def document(text: str = "Ada Lovelace worked with Charles Babbage.") -> SourceDocument:
    return SourceDocument(id="doc-1", text=text)


class TestWhereTheObservationInstantComesFrom:
    """`composition` is the only layer permitted to read a clock, and
    `build_graph` is the only place in it that does. Both halves of that are
    asserted here, because each is invisible to the other:
    `observed_at or datetime.now(UTC)` collapsed to *always* the clock passes
    the second test, and collapsed to *always* the parameter passes the first.
    """

    async def test_a_supplied_instant_reaches_every_stored_entity(self) -> None:
        """The whole point of the parameter: a caller taking determinism back
        from the clock. The value is well outside any plausible clock reading,
        so it cannot be confused with one."""
        observed = datetime(2011, 5, 6, 1, 2, tzinfo=UTC)
        store = InMemoryGraphStore()

        await build_graph(
            document(),
            provider=CountingProvider(),
            store=store,
            tenant_id=TENANT_ID,
            observed_at=observed,
        )

        stored = await store.find_entities(TENANT_ID)
        assert stored, "nothing was written, so the assertion below cannot fail"
        assert {e.provenance.observed_at for e in stored} == {observed}

    async def test_omitting_it_reads_the_clock(self) -> None:
        """Bracketed by two real readings rather than compared to one.

        Asserting merely that *something* was stamped would pass against a
        hard-coded constant, and asserting equality with a clock read here
        would be flaky. The interval is what distinguishes them.
        """
        before = datetime.now(UTC)
        store = InMemoryGraphStore()

        await build_graph(document(), provider=CountingProvider(), store=store, tenant_id=TENANT_ID)

        after = datetime.now(UTC)
        stored = await store.find_entities(TENANT_ID)
        assert stored, "nothing was written, so the assertion below cannot fail"
        for entity in stored:
            assert before <= entity.provenance.observed_at <= after

    async def test_two_runs_given_one_instant_agree_where_two_clock_reads_would_not(
        self,
    ) -> None:
        """The determinism the parameter buys, stated as the difference
        between passing one and not. Two default runs stamp different instants;
        two runs given the same instant stamp the same one."""
        observed = datetime(2011, 5, 6, 1, 2, tzinfo=UTC)

        pinned = []
        for _ in range(2):
            store = InMemoryGraphStore()
            await build_graph(
                document(),
                provider=CountingProvider(),
                store=store,
                tenant_id=TENANT_ID,
                observed_at=observed,
            )
            pinned.append({e.provenance.observed_at for e in await store.find_entities(TENANT_ID)})

        assert pinned[0] == pinned[1] == {observed}


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


class TestTheClassifierCallSharesTheCeiling:
    """`domain=AUTO`'s classification call must go through the same
    `CallLimiter` as extraction, gleaning and embedding -- not run before it
    exists, which is what `BACKLOG.md`'s (now closed) B-BENCH-6 described.

    `build_graph` builds its `CallLimiter` internally and never hands it out,
    so a counting subclass has to be substituted for the real class at the
    point `build_graph` constructs one, via `monkeypatch`. The signal is a
    *count*, not a peak or a timing: `AUTO` must pass exactly one more call
    through the ceiling than an explicit domain does, since a classification
    call is the only extra work `AUTO` adds before extraction even starts.
    """

    async def test_auto_passes_one_more_call_through_the_ceiling_than_an_explicit_domain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import importlib

        from redstring.extraction.limiter import CallLimiter

        # `redstring.composition.__init__` re-exports the `build_graph`
        # function under the same name as this submodule, which shadows the
        # submodule as an attribute of the package -- `import
        # redstring.composition.build_graph as m` resolves `m` to the
        # function, not the module, because that form is attribute lookup
        # after the plain import. `importlib.import_module` goes through
        # `sys.modules` instead and returns the actual module `CallLimiter`
        # is patched on.
        build_graph_module = importlib.import_module("redstring.composition.build_graph")

        class _CountingLimiter(CallLimiter):
            def __init__(self, limit: int) -> None:
                super().__init__(limit)
                self.enters = 0

            async def __aenter__(self) -> None:
                self.enters += 1
                await super().__aenter__()

        built: list[_CountingLimiter] = []

        def _build(limit: int) -> _CountingLimiter:
            limiter = _CountingLimiter(limit)
            built.append(limiter)
            return limiter

        monkeypatch.setattr(build_graph_module, "CallLimiter", _build)

        # Long enough to actually reach the classifier: `ContentClassifier`
        # falls back without a model call below `MIN_CONTENT_LENGTH`, and
        # that fallback would go through no limiter at all -- the same
        # non-distinguishing-input trap as `document()`'s short default text.
        classifiable = document("Hamlet " * 40)

        await build_graph(
            classifiable,
            provider=CountingProvider(
                answer={"domain": "literature_fiction", "confidence": 0.9, "reasoning": "x"}
            ),
            store=InMemoryGraphStore(),
            tenant_id=TENANT_ID,
            domain=AUTO,
        )
        auto_enters = built[-1].enters

        await build_graph(
            classifiable,
            provider=CountingProvider(),
            store=InMemoryGraphStore(),
            tenant_id=TENANT_ID,
            domain="literature_fiction",
        )
        explicit_enters = built[-1].enters

        # The difference alone is invariant to the *pipeline's* limiter use: a
        # `build_graph` whose `ExtractionPipeline` had stopped passing calls
        # through the shared limiter would count 1 and 0, and the difference
        # would still be exactly one. Pinning the explicit arm above zero is
        # what stops this test passing for that second, unrelated defect.
        assert explicit_enters >= 1
        assert auto_enters == explicit_enters + 1


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
        corpus = InMemoryChunkStore(dimension=4)

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
        corpus = InMemoryChunkStore(dimension=4)
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
        corpus = InMemoryChunkStore(dimension=4)

        await build_graph(
            document(),
            provider=CountingProvider(),
            store=InMemoryGraphStore(),
            tenant_id=TENANT_ID,
            chunks=corpus,
        )

        assert await corpus.get_by_source("doc-1", uuid4()) == []


class TestConstrainingToADomainsVocabulary:
    async def test_it_is_off_by_default(self) -> None:
        """The control. Every assertion below is about a difference from this,
        and without it they could all be describing the default."""
        provider = CountingProvider()

        await build_graph(
            document(),
            provider=provider,
            store=InMemoryGraphStore(),
            tenant_id=TENANT_ID,
            domain="news_journalism",
        )

        assert permitted_entity_types(provider.schemas[0]) == ()

    async def test_asking_for_it_constrains_the_schema_the_provider_receives(self) -> None:
        provider = CountingProvider(
            answer={"entities": [{"name": "Maria Chen", "entity_type": "person"}]}
        )

        await build_graph(
            document(),
            provider=provider,
            store=InMemoryGraphStore(),
            tenant_id=TENANT_ID,
            domain="news_journalism",
            constrain_to_domain=True,
        )

        assert "person" in permitted_entity_types(provider.schemas[0])

    async def test_it_is_refused_without_a_domain_before_any_model_call(self) -> None:
        """Checked ahead of extraction, as the embedding pair is.

        `provider.calls` is what makes this more than an argument-validation
        test: raising *after* a document has been through a model is
        discovering the misconfiguration too late, and costs exactly what the
        check exists to save.
        """
        provider = CountingProvider()

        with pytest.raises(ValueError, match="needs a domain"):
            await build_graph(
                document(),
                provider=provider,
                store=InMemoryGraphStore(),
                tenant_id=TENANT_ID,
                constrain_to_domain=True,
            )

        assert provider.calls == []

    async def test_the_domain_the_classifier_chose_supplies_the_vocabulary(self) -> None:
        """`AUTO` picks the domain, so it must pick the vocabulary with it.

        The prompt and the schema come from one resolution. Looked up
        separately they could disagree -- above all on the fallback path,
        where the classifier's answer is discarded and the prompt is
        `encyclopedia_wiki`'s while a second lookup would have no way to know.
        """
        provider = CountingProvider(
            answer={"domain": "academic_research", "confidence": 0.9, "reasoning": "a paper"}
        )

        report = await build_graph(
            document("Hamlet " * 40),
            provider=provider,
            store=InMemoryGraphStore(),
            tenant_id=TENANT_ID,
            domain=AUTO,
            constrain_to_domain=True,
        )

        assert report.domain == "academic_research"
        # The last call is the extraction; the first is the classification,
        # which is asked for `ClassificationResult` and never for this.
        assert permitted_entity_types(provider.schemas[-1]) == tuple(
            t.id for t in get_domain_schema("academic_research").entity_types
        )

    async def test_a_schema_object_constrains_without_being_registered(self) -> None:
        """`domain=` takes a `DomainSchema` as well as an id, and a caller
        with their own YAML should not have to register it to constrain to
        it. Resolving through the registry would break exactly that caller."""
        schema = get_domain_schema("news_journalism")
        provider = CountingProvider(
            answer={"entities": [{"name": "Maria Chen", "entity_type": "person"}]}
        )

        await build_graph(
            document(),
            provider=provider,
            store=InMemoryGraphStore(),
            tenant_id=TENANT_ID,
            domain=schema,
            constrain_to_domain=True,
        )

        assert permitted_entity_types(provider.schemas[0]) == tuple(
            t.id for t in schema.entity_types
        )
