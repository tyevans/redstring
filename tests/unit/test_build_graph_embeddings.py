"""`build_graph` populating a `VectorStore`, and refusing to half-do it.

Before this, **nothing in the library could put a vector in a `VectorStore`**
(reported downstream). `VectorProjection` and
`Document.record_embeddings` both existed and neither had a caller — the
inert-code shape from `recurring-defects.md` §3, sitting in the tree for six
slices while the port it served looked complete.

So the tests that matter here are not "does it embed" but the three ways this
wiring can be wrong while looking right: writing nothing and reporting success,
writing vectors that belong to the wrong entities, and writing to a store whose
width disagrees with the model.

See `docs/adr/0017-the-embedding-provider-port.md`.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from redstring import (
    EmbeddingProviderError,
    FakeEmbeddingProvider,
    FakeLlmProvider,
    InMemoryGraphStore,
    InMemoryVectorStore,
    SourceDocument,
    build_graph,
)

TENANT_ID = uuid4()

ANSWER = {
    "entities": [
        {"name": "Ada Lovelace", "entity_type": "person"},
        {"name": "Charles Babbage", "entity_type": "person"},
    ],
    "relationships": [
        {
            "source_name": "Ada Lovelace",
            "target_name": "Charles Babbage",
            "relationship_type": "knows",
        }
    ],
}

#: A realistic width, not 8. CLAUDE.md records a dimension check written with
#: `is not` that passed at 8 and rejected every legitimate write at 768,
#: because CPython caches small integers. Every test here that involves a
#: dimension uses a value outside that cache.
DIMENSION = 768


def document(text: str = "Ada Lovelace knows Charles Babbage.") -> SourceDocument:
    return SourceDocument(id=f"doc-{uuid4()}", text=text)


def provider() -> FakeLlmProvider:
    return FakeLlmProvider(by_substring={}, default=ANSWER)


class TestTheVectorStoreIsPopulated:
    async def test_every_extracted_entity_gets_a_vector(self):
        store, vectors = InMemoryGraphStore(), InMemoryVectorStore(dimension=DIMENSION)

        report = await build_graph(
            document(),
            provider=provider(),
            store=store,
            tenant_id=TENANT_ID,
            embedding_provider=FakeEmbeddingProvider(dimension=DIMENSION),
            vector_store=vectors,
        )

        assert report.embedded == 2
        for entity in await store.find_entities(TENANT_ID):
            assert await vectors.get(entity.id, TENANT_ID) is not None, entity.name

    async def test_each_vector_belongs_to_the_entity_it_was_computed_from(self):
        """The failure a count cannot see.

        `embed` is positional, so a wiring that zipped the results onto the
        entities in a different order would still write two vectors for two
        entities and still report `embedded == 2`. Recomputing each entity's
        vector independently and comparing is the only assertion that
        distinguishes correct wiring from scrambled wiring.
        """
        store, vectors = InMemoryGraphStore(), InMemoryVectorStore(dimension=DIMENSION)
        embedder = FakeEmbeddingProvider(dimension=DIMENSION)

        await build_graph(
            document(),
            provider=provider(),
            store=store,
            tenant_id=TENANT_ID,
            embedding_provider=embedder,
            vector_store=vectors,
        )

        for entity in await store.find_entities(TENANT_ID):
            stored = await vectors.get(entity.id, TENANT_ID)
            expected = (await embedder.embed([entity.name]))[0]
            assert stored is not None
            assert stored.vector == pytest.approx(expected), (
                f"the vector stored for {entity.name!r} is not the embedding of "
                f"its name, so results were matched to the wrong entities"
            )

    async def test_nothing_is_written_without_an_embedding_provider(self):
        """The default path must stay a graph-only path."""
        store = InMemoryGraphStore()

        report = await build_graph(
            document(), provider=provider(), store=store, tenant_id=TENANT_ID
        )

        assert report.embedded == 0

    async def test_re_running_embeds_again_and_the_store_absorbs_it(self):
        """Re-running is idempotent in the store, not suppressed at the aggregate.

        `Document.record_embeddings` does refuse a second `EntitiesEmbedded`
        for a model it has already seen — but `build_graph` constructs a fresh
        aggregate on every call, the same shape that makes `event is None`
        unreachable for extraction, so that refusal is never reached from here.
        The second run embeds again and reports the full count.

        This test asserted the opposite first, and failed. Worth keeping in the
        honest direction rather than deleting: "the count drops to zero on a
        repeat" is exactly what a reader would assume from the aggregate's
        docstring, and the assumption is wrong at this layer.
        """
        doc = document()
        store, vectors = InMemoryGraphStore(), InMemoryVectorStore(dimension=DIMENSION)
        embedder = FakeEmbeddingProvider(dimension=DIMENSION)

        first = await build_graph(
            doc,
            provider=provider(),
            store=store,
            tenant_id=TENANT_ID,
            embedding_provider=embedder,
            vector_store=vectors,
        )
        second = await build_graph(
            doc,
            provider=provider(),
            store=store,
            tenant_id=TENANT_ID,
            embedding_provider=embedder,
            vector_store=vectors,
        )

        assert first.embedded == 2
        assert second.embedded == 2, "build_graph does not reuse an aggregate"

        # Idempotent where it counts: two entities, not four rows.
        entities = await store.find_entities(TENANT_ID)
        assert len(entities) == 2
        for entity in entities:
            assert await vectors.get(entity.id, TENANT_ID) is not None


class TestHalfConfiguredIsRefused:
    """One without the other is a silent no-op, so it raises instead."""

    async def test_a_provider_without_a_store_raises(self):
        with pytest.raises(ValueError, match="embedding_provider was given without vector_store"):
            await build_graph(
                document(),
                provider=provider(),
                store=InMemoryGraphStore(),
                tenant_id=TENANT_ID,
                embedding_provider=FakeEmbeddingProvider(dimension=DIMENSION),
            )

    async def test_a_store_without_a_provider_raises(self):
        with pytest.raises(ValueError, match="vector_store was given without embedding_provider"):
            await build_graph(
                document(),
                provider=provider(),
                store=InMemoryGraphStore(),
                tenant_id=TENANT_ID,
                vector_store=InMemoryVectorStore(dimension=DIMENSION),
            )

    async def test_the_refusal_happens_before_the_model_is_called(self):
        """Fail at the seam, not after paying for extraction.

        A caller who mis-wired this should not have spent a model call finding
        out. Observed through a provider that records calls — asserting the
        exception alone would pass whether the check ran first or last.
        """
        calls: list[str] = []

        class CountingProvider(FakeLlmProvider):
            async def extract(self, text, schema, *, system_prompt=None):
                calls.append(text)
                return await super().extract(text, schema, system_prompt=system_prompt)

        with pytest.raises(ValueError, match="without vector_store"):
            await build_graph(
                document(),
                provider=CountingProvider(by_substring={}, default=ANSWER),
                store=InMemoryGraphStore(),
                tenant_id=TENANT_ID,
                embedding_provider=FakeEmbeddingProvider(dimension=DIMENSION),
            )

        assert calls == [], "the document was extracted before the wiring was checked"


class TestDimensionsMustAgree:
    async def test_a_mismatch_raises_naming_both_numbers(self):
        """`VectorStore` would catch this per-write; that is too late.

        By then the embedding API call has been paid for, and pgvector's
        version of the message is about a column type rather than about a
        configuration mistake. The message here names the model and both
        widths, because those are the two things a caller has to reconcile.
        """
        with pytest.raises(ValueError, match="768-dimensional") as caught:
            await build_graph(
                document(),
                provider=provider(),
                store=InMemoryGraphStore(),
                tenant_id=TENANT_ID,
                embedding_provider=FakeEmbeddingProvider(dimension=768),
                vector_store=InMemoryVectorStore(dimension=384),
            )

        assert "384" in str(caught.value)

    async def test_a_realistic_matching_dimension_is_accepted(self):
        """The `is not` trap, in its positive direction.

        A dimension check written with `is` rather than `==` passes for every
        small integer CPython caches and fails for 768. This test and the one
        above are a pair: the first proves a mismatch is caught, this proves a
        *match* at a realistic width is not spuriously rejected. Either alone
        can be satisfied by an identity comparison.
        """
        report = await build_graph(
            document(),
            provider=provider(),
            store=InMemoryGraphStore(),
            tenant_id=TENANT_ID,
            embedding_provider=FakeEmbeddingProvider(dimension=768),
            vector_store=InMemoryVectorStore(dimension=768),
        )

        assert report.embedded == 2

    async def test_a_small_matching_dimension_is_also_accepted(self):
        """Inside the small-int cache, where an identity check happens to work.

        Kept alongside the 768 case so a future reader can see that both were
        considered: this one passes under the defect and the one above does
        not, which is what makes them a discriminating pair rather than two
        similar tests.
        """
        report = await build_graph(
            document(),
            provider=provider(),
            store=InMemoryGraphStore(),
            tenant_id=TENANT_ID,
            embedding_provider=FakeEmbeddingProvider(dimension=8),
            vector_store=InMemoryVectorStore(dimension=8),
        )

        assert report.embedded == 2


class TestTheEdgesOfEmbedding:
    async def test_a_document_with_no_entities_embeds_nothing(self):
        """No entities means no request, not an empty request.

        Most embedding endpoints reject an empty `input`, so a document the
        model found nothing in would otherwise turn a successful extraction
        into a 400 from someone else's server.
        """
        empty_answer = {"entities": [], "relationships": []}
        vectors = InMemoryVectorStore(dimension=DIMENSION)

        report = await build_graph(
            document("Nothing extractable here."),
            provider=FakeLlmProvider(by_substring={}, default=empty_answer),
            store=InMemoryGraphStore(),
            tenant_id=TENANT_ID,
            embedding_provider=FakeEmbeddingProvider(dimension=DIMENSION),
            vector_store=vectors,
        )

        assert report.embedded == 0

    async def test_a_provider_returning_too_few_vectors_raises(self):
        """The composition root checks the count too, and it must.

        `LangChainEmbeddingProvider` checks it at the adapter, but the port is
        open — any implementation can be handed in. A short list zipped onto
        entities attaches vectors to the wrong ones and stores them happily, so
        the last place that can still catch it checks as well. Belt and braces
        is right here: the failure is silent corruption, not an error.
        """

        class ShortProvider:
            model = "stub/short"
            dimension = DIMENSION

            async def embed(self, texts):
                return [[0.0] * DIMENSION]  # one, however many were asked for

        with pytest.raises(EmbeddingProviderError, match="asked for 2 embeddings and got 1"):
            await build_graph(
                document(),
                provider=provider(),
                store=InMemoryGraphStore(),
                tenant_id=TENANT_ID,
                embedding_provider=ShortProvider(),  # type: ignore[arg-type]
                vector_store=InMemoryVectorStore(dimension=DIMENSION),
            )
