"""`ChunkRetriever`: the chunk-corpus mirror of `Retriever`.

Chunk ids are pinned as literals throughout -- never drawn from `uuid4()` or
`chunk_id()` -- so a test failure names the chunk that mis-ranked rather than
a fresh hash every run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from redstring.chunks.adapters.memory import InMemoryChunkStore
from redstring.composition.retrieval import ChunkRetriever
from redstring.domain.chunk import StoredChunk
from redstring.domain.exceptions import DimensionMismatchError
from redstring.domain.retrieval import RetrievalMode
from redstring.llm.adapters.fake_embedding import FakeEmbeddingProvider

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redstring.domain.ids import TenantId

#: `nomic-embed-text`'s width, and realistic on purpose -- CLAUDE.md records a
#: dimension check written with `is not` that passed at a test dimension of 8
#: and rejected every legitimate write at 768, because CPython caches small
#: integers.
DIMENSION = 768


def _chunk(
    chunk_id: str,
    tenant_id: TenantId,
    *,
    source_id: str = "doc-1",
    text: str,
    chunk_index: int = 0,
    embedding: list[float] | None = None,
) -> StoredChunk:
    return StoredChunk(
        id=chunk_id,
        tenant_id=tenant_id,
        source_id=source_id,
        text=text,
        chunk_index=chunk_index,
        start_char=0,
        end_char=len(text),
        embedding=embedding,
    )


def _retriever(chunks: InMemoryChunkStore, embeddings: FakeEmbeddingProvider) -> ChunkRetriever:
    return ChunkRetriever(embeddings=embeddings, chunks=chunks)


# ----------------------------------------------------------------------
# Construction guards
# ----------------------------------------------------------------------


async def test_a_provider_and_store_of_different_dimensions_are_refused() -> None:
    """At construction, before any text is embedded -- `Retriever`'s rule."""
    with pytest.raises(DimensionMismatchError):
        ChunkRetriever(
            embeddings=FakeEmbeddingProvider(dimension=8),
            chunks=InMemoryChunkStore(dimension=16),
        )


@pytest.mark.parametrize("overfetch", [0, -1])
async def test_an_overfetch_below_one_is_refused(overfetch: int) -> None:
    # Fetching fewer than `k` per channel cannot improve on `k`, so there is
    # no reading of it that is a caller's intent rather than a mistake.
    with pytest.raises(ValueError, match="overfetch"):
        ChunkRetriever(
            embeddings=FakeEmbeddingProvider(dimension=DIMENSION),
            chunks=InMemoryChunkStore(dimension=DIMENSION),
            overfetch=overfetch,
        )


# ----------------------------------------------------------------------
# `retrieve_chunks` guards
# ----------------------------------------------------------------------


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
async def test_a_blank_query_raises(blank: str) -> None:
    """A blank query is a caller bug; neither empty-answer reading hides it."""
    chunks = InMemoryChunkStore(dimension=DIMENSION)
    embeddings = FakeEmbeddingProvider(dimension=DIMENSION)
    with pytest.raises(ValueError, match="query"):
        await _retriever(chunks, embeddings).retrieve_chunks(blank, uuid4())


async def test_k_zero_returns_nothing_and_a_negative_k_raises() -> None:
    """Both pinned as literals -- a property sampling `k` makes boundary
    coverage depend on the sampler and on the lowered example count under
    mutation.
    """
    tenant = uuid4()
    chunks = InMemoryChunkStore(dimension=DIMENSION)
    embeddings = FakeEmbeddingProvider(dimension=DIMENSION)
    await chunks.upsert_many([_chunk("chunk-a", tenant, text="Ada Lovelace")])
    retriever = _retriever(chunks, embeddings)

    empty = await retriever.retrieve_chunks("Ada Lovelace", tenant, k=0)
    assert empty.matches == []
    assert empty.query == "Ada Lovelace"

    with pytest.raises(ValueError, match="k"):
        await retriever.retrieve_chunks("Ada Lovelace", tenant, k=-1)


# ----------------------------------------------------------------------
# Single-channel modes
# ----------------------------------------------------------------------


class _CountingEmbeddingProvider:
    """A real `FakeEmbeddingProvider` that counts `embed` calls.

    Delegation, not a mock: every vector returned is the real provider's, so
    a test using this still exercises the semantic channel. Only the call
    count is observed -- what proves `LEXICAL` mode makes no embedding call.
    """

    def __init__(self, inner: FakeEmbeddingProvider) -> None:
        self._inner = inner
        self.calls = 0

    @property
    def dimension(self) -> int:
        return self._inner.dimension

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return await self._inner.embed(texts)

    # Counting both sides is what keeps the assertion honest. A double
    # counting only `embed` reads zero for a mode that embeds every query,
    # once the semantic channel calls `embed_query` instead.
    async def embed_query(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return await self._inner.embed_query(texts)


async def test_a_lexical_only_mode_makes_no_embedding_call() -> None:
    tenant = uuid4()
    chunks = InMemoryChunkStore(dimension=DIMENSION)
    inner = FakeEmbeddingProvider(dimension=DIMENSION)
    await chunks.upsert_many([_chunk("chunk-a", tenant, text="Ada Lovelace was a mathematician")])

    counting = _CountingEmbeddingProvider(inner)
    result = await ChunkRetriever(embeddings=counting, chunks=chunks).retrieve_chunks(
        "Ada Lovelace", tenant, mode=RetrievalMode.LEXICAL
    )

    assert [match.chunk.id for match in result.matches] == ["chunk-a"]
    assert counting.calls == 0


async def test_a_semantic_only_mode_leaves_lexical_none() -> None:
    tenant = uuid4()
    chunks = InMemoryChunkStore(dimension=DIMENSION)
    embeddings = FakeEmbeddingProvider(dimension=DIMENSION)
    query = "Ada Lovelace"
    [vector] = await embeddings.embed([query])
    # The chunk's text shares no term with the query, so a lexical run would
    # find nothing -- proving the match came from the semantic channel alone.
    await chunks.upsert_many(
        [_chunk("chunk-a", tenant, text="totally unrelated passage", embedding=vector)]
    )

    result = await ChunkRetriever(embeddings=embeddings, chunks=chunks).retrieve_chunks(
        query, tenant, mode=RetrievalMode.SEMANTIC
    )

    [match] = result.matches
    assert match.chunk.id == "chunk-a"
    assert match.semantic is not None
    assert match.lexical is None


# ----------------------------------------------------------------------
# HYBRID fuses both, and an unembedded corpus still answers lexically
# ----------------------------------------------------------------------


async def test_hybrid_fuses_both_channels() -> None:
    tenant = uuid4()
    chunks = InMemoryChunkStore(dimension=DIMENSION)
    embeddings = FakeEmbeddingProvider(dimension=DIMENSION)
    query = "Ada Lovelace"
    [vector] = await embeddings.embed([query])
    await chunks.upsert_many(
        [_chunk("chunk-a", tenant, text="Ada Lovelace, mathematician", embedding=vector)]
    )

    result = await ChunkRetriever(embeddings=embeddings, chunks=chunks).retrieve_chunks(
        query, tenant, mode=RetrievalMode.HYBRID
    )

    [match] = result.matches
    assert match.chunk.id == "chunk-a"
    assert match.semantic is not None
    assert match.lexical is not None


async def test_a_hybrid_query_over_an_unembedded_corpus_still_returns_lexical_results() -> None:
    """The property named in the class docstring: no embeddings, no raise.

    "Unembedded" is a per-row fact on `StoredChunk`, so refusing the query
    cannot be implemented honestly -- a `HYBRID` query over a corpus with no
    embeddings must fall back to lexical results rather than raising or
    silently returning nothing. A wrong implementation that raises, or one
    that returns `[]` believing "hybrid needs both channels", both fail this.
    """
    tenant = uuid4()
    chunks = InMemoryChunkStore(dimension=DIMENSION)
    embeddings = FakeEmbeddingProvider(dimension=DIMENSION)
    await chunks.upsert_many(
        [_chunk("chunk-a", tenant, text="Ada Lovelace, mathematician", embedding=None)]
    )

    result = await ChunkRetriever(embeddings=embeddings, chunks=chunks).retrieve_chunks(
        "Ada Lovelace", tenant, mode=RetrievalMode.HYBRID
    )

    [match] = result.matches
    assert match.chunk.id == "chunk-a"
    assert match.semantic is None
    assert match.lexical is not None


# ----------------------------------------------------------------------
# Overfetch: the property it exists for
# ----------------------------------------------------------------------


async def test_a_chunk_ranked_second_in_both_channels_beats_a_channel_leader() -> None:
    """The property `overfetch` exists for, constructed rather than assumed.

    `chunk-a` tops the lexical channel and is unembedded (absent from the
    semantic channel entirely). `chunk-c`'s stored embedding is set to the
    *query's own vector*, so cosine similarity is exactly `1.0` and it tops
    the semantic channel; its text shares no term with the query, so it is
    absent from the lexical channel. `chunk-b` is second in both: it shares
    the query's terms (fewer times, in a longer passage, so it loses to
    `chunk-a` on BM25's length normalisation) and carries an embedding of
    unrelated text (some cosine less than `1.0`, so it loses to `chunk-c`).

    Reciprocal rank fusion scores by *rank*, not magnitude: `chunk-b` earns a
    contribution from both lists (position 1 in each) while `chunk-a` and
    `chunk-c` each earn one contribution (position 0 in one list only). With
    `RRF_K = 60`, `chunk-b`'s two `1/62` contributions (`≈0.0323`) sum to more
    than either single-channel leader's one `1/61` (`≈0.0164`) -- so at `k=1`
    the fused winner is the chunk that led no channel at all.

    A test at `overfetch=1` could not show this: each channel would be asked
    for exactly `k=1`, `chunk-b` would never be a candidate in either list,
    and the property this test exists to pin would be invisible.
    """
    tenant = uuid4()
    chunks = InMemoryChunkStore(dimension=DIMENSION)
    embeddings = FakeEmbeddingProvider(dimension=DIMENSION)
    query = "ada lovelace"
    [query_vector] = await embeddings.embed([query])
    [other_vector] = await embeddings.embed(["some unrelated embedded text"])

    await chunks.upsert_many(
        [
            # Short and dense in the query's terms -- wins lexically.
            _chunk("chunk-a", tenant, text="ada lovelace", embedding=None),
            # Contains both query terms once, diluted by an otherwise
            # unrelated passage -- second lexically, and carries an
            # embedding that is not the query's exact vector -- second
            # semantically.
            _chunk(
                "chunk-b",
                tenant,
                text=(
                    "ada lovelace mathematician analytical engine pioneer computing "
                    "history science notation"
                ),
                embedding=other_vector,
            ),
            # No lexical overlap with the query at all, but its stored
            # embedding *is* the query's own vector -- wins semantically with
            # cosine similarity exactly 1.0.
            _chunk("chunk-c", tenant, text="grace hopper compiler pioneer", embedding=query_vector),
        ]
    )

    result = await ChunkRetriever(
        embeddings=embeddings, chunks=chunks, overfetch=3
    ).retrieve_chunks(query, tenant, k=1)

    [winner] = result.matches
    assert winner.chunk.id == "chunk-b"


# ----------------------------------------------------------------------
# The query goes through the query side of the port
# ----------------------------------------------------------------------

#: Non-empty and different from each other. Equal prefixes would make a
#: retriever calling `embed` indistinguishable from one calling `embed_query`,
#: which is the defect these two tests exist to catch.
DOCUMENT_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


async def test_the_semantic_channel_embeds_the_query_as_a_query() -> None:
    """`chunk-wanted` sits at the vector a correctly-embedded query produces
    and `chunk-decoy` at the one the document side would produce for the same
    string, so the two implementations return different chunks rather than the
    same chunk with a worse score."""
    tenant = uuid4()
    chunks = InMemoryChunkStore(dimension=DIMENSION)
    embeddings = FakeEmbeddingProvider(
        dimension=DIMENSION,
        document_prefix=DOCUMENT_PREFIX,
        query_prefix=QUERY_PREFIX,
    )
    query = "Ada Lovelace"

    [as_query] = await embeddings.embed_query([query])
    [as_document] = await embeddings.embed([query])
    assert as_query != as_document, "the two prefixes must produce different vectors"
    await chunks.upsert_many(
        [
            _chunk("chunk-wanted", tenant, text="unrelated passage one", embedding=as_query),
            _chunk("chunk-decoy", tenant, text="unrelated passage two", embedding=as_document),
        ]
    )

    result = await _retriever(chunks, embeddings).retrieve_chunks(
        query, tenant, k=1, mode=RetrievalMode.SEMANTIC
    )

    assert [match.chunk.id for match in result.matches] == ["chunk-wanted"]


async def test_the_query_reaches_the_provider_unprefixed_by_the_caller() -> None:
    """A retriever prefixing the query itself and then calling `embed_query`
    would double the prefix on the wire and pass every assertion about which
    chunk came back."""

    class RecordingProvider(FakeEmbeddingProvider):
        def __init__(self) -> None:
            super().__init__(dimension=DIMENSION)
            self.seen: list[tuple[str, list[str]]] = []

        async def embed(self, texts: Sequence[str]) -> list[list[float]]:
            self.seen.append(("embed", list(texts)))
            return await super().embed(texts)

        async def embed_query(self, texts: Sequence[str]) -> list[list[float]]:
            self.seen.append(("embed_query", list(texts)))
            return await super().embed_query(texts)

    tenant = uuid4()
    chunks = InMemoryChunkStore(dimension=DIMENSION)
    embeddings = RecordingProvider()

    await _retriever(chunks, embeddings).retrieve_chunks(
        "Ada Lovelace", tenant, mode=RetrievalMode.SEMANTIC
    )

    assert embeddings.seen == [("embed_query", ["Ada Lovelace"])]
