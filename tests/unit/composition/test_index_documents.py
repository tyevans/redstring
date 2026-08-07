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
        corpus = InMemoryChunkStore()

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
        corpus = InMemoryChunkStore()

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
    async def test_indexing_the_same_document_twice_is_a_no_op(self) -> None:
        """Same chunker settings, same signature, nothing emitted the second time.

        The two calls share an event store, which is what carries the
        aggregate's recorded signatures between them. Without one the refusal
        has no state to live in -- see the module docstring.
        """
        corpus = InMemoryChunkStore()
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

    async def test_a_document_listed_twice_in_one_call_is_indexed_once(self) -> None:
        corpus = InMemoryChunkStore()

        report = await index_documents(
            [long_document(), long_document()], store=corpus, tenant_id=TENANT_ID
        )

        assert report.documents_indexed == 1
        assert report.documents_skipped == 1

    async def test_re_indexing_with_different_settings_replaces_the_passages(self) -> None:
        corpus = InMemoryChunkStore()
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
        corpus = InMemoryChunkStore()
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
    """Index-then-extract keeps the links; extract-then-index drops them."""

    async def test_extracting_after_indexing_preserves_the_entity_links(self) -> None:
        corpus = InMemoryChunkStore()
        graph = InMemoryGraphStore()

        await index_documents([document()], store=corpus, tenant_id=TENANT_ID)
        await build_graph(
            document(),
            provider=FakeLlmProvider(by_substring={}, default=ADA),
            store=graph,
            tenant_id=TENANT_ID,
            chunks=corpus,
        )

        stored = await corpus.get_by_source("doc-1", TENANT_ID)
        linked = {entity_id for chunk in stored for entity_id in chunk.entity_ids}
        assert linked == {entity.id for entity in await graph.find_entities(TENANT_ID)}

    async def test_indexing_after_extracting_discards_the_entity_links(self) -> None:
        """Documented behaviour, not an accident.

        The chunk ids are identical either way -- they are content-addressed --
        so the passages survive and only the links are lost. Asserting the
        text is still there is what distinguishes "the links were dropped"
        from "the corpus was emptied".
        """
        corpus = InMemoryChunkStore()
        graph = InMemoryGraphStore()

        await build_graph(
            document(),
            provider=FakeLlmProvider(by_substring={}, default=ADA),
            store=graph,
            tenant_id=TENANT_ID,
            chunks=corpus,
        )
        before = await corpus.get_by_source("doc-1", TENANT_ID)
        await index_documents([document()], store=corpus, tenant_id=TENANT_ID)
        after = await corpus.get_by_source("doc-1", TENANT_ID)

        assert any(chunk.entity_ids for chunk in before)
        assert [chunk.id for chunk in after] == [chunk.id for chunk in before]
        assert all(chunk.entity_ids == [] for chunk in after)


class TestTheReport:
    async def test_the_report_counts_documents_and_chunks_separately(self) -> None:
        """Two documents chunking to three and five passages: 2 and 8.

        The two numbers must differ, or a report wiring both fields to one
        count passes. The chunker is sized so each document really does split.
        """
        corpus = InMemoryChunkStore()
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
        corpus = InMemoryChunkStore()

        report = await index_documents(
            [long_document()], store=corpus, tenant_id=TENANT_ID, chunker=small_chunker()
        )

        assert report.documents_indexed == 1
        assert report.chunks_written > 1
        assert report.documents_skipped == 0

    async def test_an_empty_corpus_is_reported_as_zero_of_everything(self) -> None:
        report = await index_documents([], store=InMemoryChunkStore(), tenant_id=TENANT_ID)

        assert report == IndexReport(documents_indexed=0, chunks_written=0, documents_skipped=0)

    async def test_the_report_cannot_be_edited_after_the_fact(self) -> None:
        report = await index_documents([], store=InMemoryChunkStore(), tenant_id=TENANT_ID)

        try:
            report.documents_indexed = 99  # type: ignore[misc]
        except AttributeError:
            return
        raise AssertionError("IndexReport is not frozen")


class TestTenants:
    async def test_the_corpus_is_scoped_to_the_tenant_that_indexed_it(self) -> None:
        corpus = InMemoryChunkStore()

        await index_documents([document()], store=corpus, tenant_id=TENANT_ID)

        assert await corpus.get_by_source("doc-1", uuid4()) == []

    async def test_two_tenants_indexing_one_document_do_not_share_passages(self) -> None:
        """The chunk id is the same for both -- it is the digest of the source
        and the text, and neither carries the tenant. Only the store's
        `(tenant_id, id)` key keeps them apart."""
        corpus = InMemoryChunkStore()
        other = uuid4()

        await index_documents([document()], store=corpus, tenant_id=TENANT_ID)
        await index_documents([document()], store=corpus, tenant_id=other)

        mine = await corpus.get_by_source("doc-1", TENANT_ID)
        theirs = await corpus.get_by_source("doc-1", other)
        assert [c.id for c in mine] == [c.id for c in theirs]
        assert {c.tenant_id for c in mine} == {TENANT_ID}
        assert {c.tenant_id for c in theirs} == {other}
