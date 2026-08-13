"""`index_documents`: a corpus without a model call.

Nothing here is mocked. `InMemoryChunkStore` is the adapter the port
compliance suite runs against, and where an extraction is needed for
comparison it is a real `build_graph` over `FakeLlmProvider`, which validates
its canned payloads against the caller's schema.

The two orderings -- index then extract, extract then index -- are the pair
this module exists to pin. They are asymmetric on purpose; see
`redstring.composition.index_documents`.
"""

from __future__ import annotations

import inspect
from uuid import uuid4

from eventsource.adapters.memory import InMemoryEventStore

from redstring import FakeLlmProvider, InMemoryGraphStore, SourceDocument, build_graph
from redstring.chunks.adapters.memory import InMemoryChunkStore  # exported in Task 10
from redstring.composition import IndexReport, index_documents
from redstring.extraction.chunkers import SlidingWindowChunker

TENANT_ID = uuid4()

ADA = {
    "entities": [{"name": "Ada Lovelace", "entity_type": "Person"}],
    "relationships": [],
}


def document(text: str = "Ada Lovelace was a mathematician.", source_id: str = "doc-1"):
    return SourceDocument(id=source_id, text=text)


def long_document(source_id: str = "doc-1", repeats: int = 60) -> SourceDocument:
    """Long enough that the default chunker really splits it."""
    return SourceDocument(id=source_id, text="Ada Lovelace was a mathematician. " * repeats)


def numbered(stem: str, count: int) -> str:
    """Text whose every sentence differs, so no two chunks share an id."""
    return " ".join(f"{stem} {index}." for index in range(count))


def small_chunker() -> SlidingWindowChunker:
    return SlidingWindowChunker(default_chunk_size=120, default_overlap=30, min_chunk_size=10)


class TestThereIsNoModel:
    async def test_indexing_a_document_stores_its_passages_without_an_llm(self) -> None:
        corpus = InMemoryChunkStore(dimension=4)

        report = await index_documents([document()], store=corpus, tenant_id=TENANT_ID)

        stored = await corpus.get_by_source("doc-1", TENANT_ID)
        assert report.documents_indexed == 1
        assert [chunk.text for chunk in stored] == [document().text]

    def test_the_signature_has_no_provider_shaped_parameter_at_all(self) -> None:
        """A fake provider passed by a test would prove only that a fake works.

        The claim is that a caller *cannot* supply one, which is a property of
        the signature rather than of any call, so it is read off the signature.
        """
        parameters = set(inspect.signature(index_documents).parameters)

        assert not {p for p in parameters if "provider" in p.lower() or "llm" in p.lower()}

    async def test_indexed_chunks_carry_no_entity_ids(self) -> None:
        corpus = InMemoryChunkStore(dimension=4)

        await index_documents(
            [long_document()], store=corpus, tenant_id=TENANT_ID, chunker=small_chunker()
        )

        stored = await corpus.get_by_source("doc-1", TENANT_ID)
        assert len(stored) > 1
        assert all(chunk.entity_ids == [] for chunk in stored)

    async def test_everything_after_the_documents_must_be_passed_by_name(self) -> None:
        """`store` and `tenant_id` have no order a caller would be reminded of."""
        store = inspect.signature(index_documents).parameters["store"]

        assert store.kind is inspect.Parameter.KEYWORD_ONLY


class TestRepeats:
    async def test_indexing_the_same_document_twice_over_one_log_is_a_no_op(self) -> None:
        """Same chunker settings, same signature, nothing emitted the second time.

        Named for the log because the log is what makes it true. The two calls
        share an event store, which is what carries the aggregate's recorded
        signatures between them; without one the refusal has no state to live
        in, which the test below pins rather than leaving to the docstring.
        """
        corpus = InMemoryChunkStore(dimension=4)
        log = InMemoryEventStore()

        first = await index_documents(
            [long_document()], store=corpus, tenant_id=TENANT_ID, event_store=log
        )
        second = await index_documents(
            [long_document()], store=corpus, tenant_id=TENANT_ID, event_store=log
        )

        assert first.documents_indexed == 1
        assert first.documents_skipped == 0
        assert second.documents_indexed == 0
        assert second.documents_skipped == 1
        assert second.chunks_written == 0
        # And the corpus is unchanged rather than emptied.
        assert len(await corpus.get_by_source("doc-1", TENANT_ID)) == first.chunks_written

    async def test_without_a_log_a_repeat_is_re_indexed_rather_than_skipped(self) -> None:
        """What the default actually guarantees, asserted rather than described.

        The corpus is unharmed -- content-addressed rows are rewritten
        identically -- but the *report* says the work was new, so
        `documents_indexed` cannot be read as "work that needed doing". A
        docstring saying so is not a test; this is.
        """
        corpus = InMemoryChunkStore(dimension=4)

        first = await index_documents([long_document()], store=corpus, tenant_id=TENANT_ID)
        second = await index_documents([long_document()], store=corpus, tenant_id=TENANT_ID)

        assert second.documents_indexed == 1
        assert second.documents_skipped == 0
        assert second.chunks_written == first.chunks_written
        # Rewritten, not duplicated.
        assert len(await corpus.get_by_source("doc-1", TENANT_ID)) == first.chunks_written

    async def test_a_document_listed_twice_in_one_call_is_indexed_once(self) -> None:
        corpus = InMemoryChunkStore(dimension=4)

        report = await index_documents(
            [long_document(), long_document()], store=corpus, tenant_id=TENANT_ID
        )

        assert report.documents_indexed == 1
        assert report.documents_skipped == 1

    async def test_re_indexing_with_different_settings_replaces_the_passages(self) -> None:
        corpus = InMemoryChunkStore(dimension=4)
        log = InMemoryEventStore()

        whole = await index_documents(
            [long_document()],
            store=corpus,
            tenant_id=TENANT_ID,
            chunker=SlidingWindowChunker(default_chunk_size=10_000, default_overlap=0),
            event_store=log,
        )
        split = await index_documents(
            [long_document()],
            store=corpus,
            tenant_id=TENANT_ID,
            chunker=small_chunker(),
            event_store=log,
        )

        stored = await corpus.get_by_source("doc-1", TENANT_ID)
        assert whole.chunks_written == 1
        assert split.chunks_written > 1
        # Replaced, not accumulated: the single whole-document passage is gone.
        assert len(stored) == split.chunks_written
        assert not any(chunk.text == long_document().text for chunk in stored)

    async def test_a_second_document_is_not_a_repeat_of_the_first(self) -> None:
        """Guards the tests above: idempotence is per document, not per call."""
        corpus = InMemoryChunkStore(dimension=4)
        log = InMemoryEventStore()

        report = await index_documents(
            [long_document("doc-1"), long_document("doc-2")],
            store=corpus,
            tenant_id=TENANT_ID,
            event_store=log,
        )

        assert report.documents_indexed == 2
        assert report.documents_skipped == 0


class TestTheTwoOrderings:
    """Index-then-extract keeps the links; extract-then-index drops them.

    **Every test here shares one event store between the two calls, and that
    is what gives them teeth.** The signatures are two key spaces so that a
    repeat is refused in one and not the other, and a refusal lives in the
    aggregate's state -- so with a fresh aggregate per call there is no
    refusal to observe and these assertions hold for a *unified* signature
    too. That was the gap: the design's central claim rested on one
    string-shape assertion about `":{model_version}"`, which a future author
    unifying the two signatures would have deleted as redundant.
    """

    async def test_extracting_after_indexing_preserves_the_entity_links(self) -> None:
        corpus = InMemoryChunkStore(dimension=4)
        graph = InMemoryGraphStore()
        log = InMemoryEventStore()

        await index_documents([document()], store=corpus, tenant_id=TENANT_ID, event_store=log)
        report = await build_graph(
            document(),
            provider=FakeLlmProvider(by_substring={}, default=ADA),
            store=graph,
            tenant_id=TENANT_ID,
            chunks=corpus,
            event_store=log,
        )

        # The extraction's chunking was *recorded*, not refused as a repeat.
        # Unify the two signatures and this is 0, and everything below it is
        # an assertion about an empty set.
        assert report.chunks_written == 1
        stored = await corpus.get_by_source("doc-1", TENANT_ID)
        linked = {entity_id for chunk in stored for entity_id in chunk.entity_ids}
        assert linked == {entity.id for entity in await graph.find_entities(TENANT_ID)}
        assert linked

    async def test_a_repeat_of_the_same_path_over_one_log_is_refused(self) -> None:
        """The other half of the key-space claim: a repeat *is* suppressed.

        Without this, "the signatures differ" would be satisfied by a
        signature that differs from everything, including itself.
        """
        corpus = InMemoryChunkStore(dimension=4)
        log = InMemoryEventStore()
        provider = FakeLlmProvider(by_substring={}, default=ADA)

        first = await build_graph(
            document(),
            provider=provider,
            store=InMemoryGraphStore(),
            tenant_id=TENANT_ID,
            chunks=corpus,
            event_store=log,
        )
        second = await build_graph(
            document(),
            provider=provider,
            store=InMemoryGraphStore(),
            tenant_id=TENANT_ID,
            chunks=corpus,
            event_store=log,
        )

        assert first.chunks_written == 1
        assert second.chunks_written == 0
        assert second.event is None
        # And the links the first run wrote are still there.
        stored = await corpus.get_by_source("doc-1", TENANT_ID)
        assert all(chunk.entity_ids for chunk in stored)

    async def test_indexing_after_extracting_discards_the_entity_links(self) -> None:
        """Documented behaviour, not an accident.

        The chunk ids are identical either way -- they are content-addressed --
        so the passages survive and only the links are lost. Asserting the
        text is still there is what distinguishes "the links were dropped"
        from "the corpus was emptied".
        """
        corpus = InMemoryChunkStore(dimension=4)
        graph = InMemoryGraphStore()
        log = InMemoryEventStore()

        await build_graph(
            document(),
            provider=FakeLlmProvider(by_substring={}, default=ADA),
            store=graph,
            tenant_id=TENANT_ID,
            chunks=corpus,
            event_store=log,
        )
        before = await corpus.get_by_source("doc-1", TENANT_ID)
        report = await index_documents(
            [document()], store=corpus, tenant_id=TENANT_ID, event_store=log
        )
        after = await corpus.get_by_source("doc-1", TENANT_ID)

        # The indexing was *recorded* -- so the loss is the design's stated
        # cost, not a repeat being suppressed by accident.
        assert report.documents_indexed == 1
        assert any(chunk.entity_ids for chunk in before)
        assert [chunk.id for chunk in after] == [chunk.id for chunk in before]
        assert all(chunk.entity_ids == [] for chunk in after)


class TestTheReport:
    async def test_the_report_counts_documents_and_chunks_separately(self) -> None:
        """Two documents chunking to three and five passages: 2 and 8.

        The two numbers must differ, or a report wiring both fields to one
        count passes. The chunker is sized so each document really does split.
        """
        corpus = InMemoryChunkStore(dimension=4)
        # Every sentence distinct. Repeated text collapses under content
        # addressing, so `"...". * 24` yields no more *rows* than `* 12` and
        # the two lengths come out equal -- which is the assertion below
        # failing to distinguish anything.
        three = SourceDocument(id="doc-1", text=numbered("Ada Lovelace wrote note", 12))
        five = SourceDocument(id="doc-2", text=numbered("Charles Babbage built engine", 30))

        report = await index_documents(
            [three, five], store=corpus, tenant_id=TENANT_ID, chunker=small_chunker()
        )

        first = await corpus.get_by_source("doc-1", TENANT_ID)
        second = await corpus.get_by_source("doc-2", TENANT_ID)
        assert report.documents_indexed == 2
        assert report.chunks_written == len(first) + len(second)
        assert report.chunks_written > report.documents_indexed
        assert len(first) != len(second)

    async def test_documents_skipped_is_zero_when_nothing_was_a_repeat(self) -> None:
        """The sibling counters must not all move together."""
        corpus = InMemoryChunkStore(dimension=4)

        report = await index_documents(
            [long_document()], store=corpus, tenant_id=TENANT_ID, chunker=small_chunker()
        )

        assert report.documents_indexed == 1
        assert report.chunks_written > 1
        assert report.documents_skipped == 0

    async def test_an_empty_corpus_is_reported_as_zero_of_everything(self) -> None:
        report = await index_documents(
            [], store=InMemoryChunkStore(dimension=4), tenant_id=TENANT_ID
        )

        assert report == IndexReport(documents_indexed=0, chunks_written=0, documents_skipped=0)

    async def test_the_report_cannot_be_edited_after_the_fact(self) -> None:
        report = await index_documents(
            [], store=InMemoryChunkStore(dimension=4), tenant_id=TENANT_ID
        )

        try:
            report.documents_indexed = 99  # type: ignore[misc]
        except AttributeError:
            return
        raise AssertionError("IndexReport is not frozen")


class TestTenants:
    async def test_the_corpus_is_scoped_to_the_tenant_that_indexed_it(self) -> None:
        corpus = InMemoryChunkStore(dimension=4)

        await index_documents([document()], store=corpus, tenant_id=TENANT_ID)

        assert await corpus.get_by_source("doc-1", uuid4()) == []

    async def test_two_tenants_indexing_one_document_do_not_share_passages(self) -> None:
        """The chunk id is the same for both -- it is the digest of the source
        and the text, and neither carries the tenant. Only the store's
        `(tenant_id, id)` key keeps them apart."""
        corpus = InMemoryChunkStore(dimension=4)
        other = uuid4()

        await index_documents([document()], store=corpus, tenant_id=TENANT_ID)
        await index_documents([document()], store=corpus, tenant_id=other)

        mine = await corpus.get_by_source("doc-1", TENANT_ID)
        theirs = await corpus.get_by_source("doc-1", other)
        assert [c.id for c in mine] == [c.id for c in theirs]
        assert {c.tenant_id for c in mine} == {TENANT_ID}
        assert {c.tenant_id for c in theirs} == {other}
