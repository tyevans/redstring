"""Turning a query string into ranked entities or ranked chunks.

This is a composition and nothing else: every decision it makes is delegated
to a pure function in `domain/`. `Retriever` lives on the top layer because it
holds an embedding provider, a vector store and a graph store at once, and
`vector` and `graph` are siblings that may not import each other while
neither may import `llm` -- so no lower layer can hold all three.
`ChunkRetriever`, below, holds an embedding provider and a chunk store --
`llm` and `chunks` are likewise forbidden from importing each other, so no
sibling can hold both.

It holds the *narrowest* form of each: `VectorReader` and `EntityReader`, not
the composed ports. Retrieval reads, and nothing else -- two methods of seven
on the vector side, two of eighteen on the graph side -- so the composed
annotations were promising a caller's adapter far more than this class ever
asks it for. Note what the narrowing rules out at the type level: a retriever
holding `GraphStore` could wipe a tenant, which is precisely the fact
`TenantPurge` exists to make visible in a signature.

## Lexical recall is bounded by blocking

A query that shares no blocking key with an entity cannot be retrieved
lexically, however high its string similarity would have been. There is no
text index in this library, so candidates come from the same prefix and
soundex keys consolidation uses. This is the honest cost of storing no text,
and it is the second reason this channel is not named after a term-weighted
ranker.

## A dangling vector match is skipped, and the result is not backfilled

A vector match whose entity the graph store does not have is **skipped, and
the result is not backfilled to `k`**. The two stores are independent
projections of one log and lag independently, so this is ordinary, not
exceptional -- raising would make retrieval fail during replay, and topping up
would turn a badly lagging projection into silence.

## A lexical-only retriever needs no embedding provider

`Retriever.lexical_only` and `ChunkRetriever.lexical_only` build a retriever
with no `EmbeddingProvider` and, for entities, no `VectorReader`. The lexical
channel reaches neither, so requiring them to *construct* one obliged a caller
who wanted only that channel to keep an endpoint healthy it never called --
and a consumer whose probe degrades to "no embeddings" therefore lost
misspelling-tolerant entity search silently. ADR 0045 records why this is a
constructor rather than optional arguments, and why `HYBRID` is refused rather
than quietly reduced to its lexical half.

## The channels are fused by rank

`domain/fusion.py` says why: the two scores share no unit, and a weighted
blend of them invents an exchange rate that is unfalsifiable. The component
scores are carried through onto `ScoredEntity` so a caller can see what
fusion discarded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from redstring.domain.blocking import query_blocking_keys
from redstring.domain.chunk_ranking import rank_chunks
from redstring.domain.chunk_retrieval import ChunkRetrievalResult, ScoredChunk
from redstring.domain.exceptions import DimensionMismatchError
from redstring.domain.fusion import reciprocal_rank_fusion
from redstring.domain.lexical import lexical_score
from redstring.domain.retrieval import RetrievalMode, RetrievalResult, ScoredEntity
from redstring.domain.tokenize import tokenize

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redstring.domain.chunk import ChunkId, StoredChunk
    from redstring.domain.entity import Entity
    from redstring.domain.ids import EntityId, TenantId
    from redstring.ports.chunk_store import ChunkStore
    from redstring.ports.embedding_provider import EmbeddingProvider
    from redstring.ports.graph_store import EntityReader
    from redstring.ports.vector_store import VectorReader


class Retriever:
    """Ranked entity retrieval, fusing a semantic and a lexical channel."""

    def __init__(
        self,
        *,
        embeddings: EmbeddingProvider,
        vectors: VectorReader,
        graph: EntityReader,
        overfetch: int = 3,
    ) -> None:
        """Wire the three collaborators, refusing a mismatched pair.

        `overfetch` multiplies how many candidates each channel is asked for
        before fusion truncates to `k`. It defaults to 3 rather than 1, and
        the reason is a property of rank fusion rather than a tuning
        preference: RRF scores an entity by the ranks it holds in *each*
        list, so an entity ranked k+1 in both channels can legitimately beat
        one ranked first in a single channel -- and asking each channel for
        exactly `k` makes that entity invisible, because neither list
        contains it. The candidates that decide the fused ordering are the
        ones just past each channel's cutoff.

        `overfetch=1` restores the old behaviour and is the cheapest setting;
        raising it costs a wider `VectorStore.search` and a wider blocking-key
        scan per query, and buys recall. Values below 1 raise `ValueError` --
        fetching fewer than `k` per channel cannot improve on `k` and is
        always a mistake.

        The dimension check is at construction, before any text is embedded --
        the same rule `build_graph` applies, and for the same reason: the
        mistake is in the configuration, so it should surface at the seam
        rather than once per vector at the end of a pipeline, after the
        embedding call has been paid for.

        The comparison is `!=` and not `is not`. CPython caches small
        integers, so an identity check passes at a test dimension of 8 and
        rejects every legitimate vector at 768.
        """
        if embeddings.dimension != vectors.dimension:
            raise DimensionMismatchError(expected=vectors.dimension, actual=embeddings.dimension)
        self._wire(embeddings, vectors, graph, overfetch, RetrievalMode.HYBRID)

    @classmethod
    def lexical_only(cls, *, graph: EntityReader, overfetch: int = 3) -> Retriever:
        """A retriever with no embedding provider and no vector store.

        `RetrievalMode.LEXICAL` reaches neither collaborator -- it is
        `find_by_blocking_keys` plus `lexical_score` over the entity's name --
        so requiring both to *construct* one obliged a caller who wanted only
        the blocking-key channel to wire and keep healthy an endpoint it never
        called. A consumer found this the expensive way: its embedding probe
        degrades to "absent" when the endpoint is misconfigured, which silently
        removed misspelling-tolerant entity search, a feature with no embedding
        in it.

        This is a *constructor* rather than optional arguments on `__init__`
        deliberately, and ADR 0045 records the trade. Optional arguments make
        "lexical only" and "I forgot to pass the provider" the same call, which
        would move a configuration mistake from the seam to the first semantic
        query -- exactly what ADR 0017's construction-time dimension check
        exists to prevent. A caller naming `lexical_only` has said what it
        wants; a caller omitting an argument has not said anything.

        The retriever's default mode is `LEXICAL`, so `retrieve` needs no
        `mode=` from a caller that has already made the choice here. Asking it
        for `SEMANTIC` or `HYBRID` raises `ValueError` -- `HYBRID` especially,
        because it has a lexical half that would answer and so is the mode a
        silent skip would corrupt rather than break.
        """
        self = cls.__new__(cls)
        self._wire(None, None, graph, overfetch, RetrievalMode.LEXICAL)
        return self

    def _wire(
        self,
        embeddings: EmbeddingProvider | None,
        vectors: VectorReader | None,
        graph: EntityReader,
        overfetch: int,
        default_mode: RetrievalMode,
    ) -> None:
        """Store the collaborators, applying the guards both constructors share.

        `lexical_only` reaches this without going through `__init__`, so a
        guard written in `__init__` would hold for one construction path and
        not the other. Only the dimension check stays there, because it is the
        one guard with nothing to check when there is no provider.
        """
        if overfetch < 1:
            raise ValueError(f"overfetch must be at least 1, got {overfetch}")
        self._overfetch = overfetch
        self._embeddings = embeddings
        self._vectors = vectors
        self._graph = graph
        self._default_mode = default_mode

    async def retrieve(
        self,
        query: str,
        tenant_id: TenantId,
        *,
        k: int = 10,
        entity_types: Sequence[str] | None = None,
        mode: RetrievalMode | None = None,
    ) -> RetrievalResult:
        """The `k` best matches for `query` in `tenant_id`, best first.

        `k=0` returns nothing; a negative `k` raises `ValueError`, matching
        `VectorStore.search`. A blank query raises rather than returning
        everything or nothing -- a query that normalizes away is a caller bug,
        and both plausible empty-query answers hide it.

        `entity_types` restricts both channels; `None` means no filter and
        `[]` means nothing matches, again matching the port. The filter is
        applied to the lexical candidates **before** they are truncated to
        `k`, because truncating first returns fewer results than exist while
        matching entities sit further down the ranking.

        `mode` defaults to the retriever's own default -- `HYBRID` for one
        built with a provider and a vector store, `LEXICAL` for one built by
        `lexical_only`. A signature default cannot express that, and a
        lexical-only retriever defaulting to `HYBRID` would refuse every call
        a caller did not annotate.
        """
        mode = self._default_mode if mode is None else mode
        if mode is not RetrievalMode.LEXICAL and self._embeddings is None:
            raise ValueError(
                f"this is a lexical-only retriever and cannot serve mode {mode.value!r}; "
                "construct Retriever(embeddings=..., vectors=..., graph=...) for that"
            )
        if not query.strip():
            raise ValueError("query must not be blank")
        if k < 0:
            raise ValueError(f"k must not be negative, got {k}")
        if k == 0:
            return RetrievalResult(query=query, matches=[])

        # Each channel is asked for more than `k`; see `overfetch` on
        # `__init__` for why fusion needs the candidates past each cutoff.
        per_channel = k * self._overfetch

        semantic_scores: dict[EntityId, float] = {}
        if mode in (RetrievalMode.SEMANTIC, RetrievalMode.HYBRID):
            semantic_scores = await self._semantic(query, tenant_id, per_channel, entity_types)

        lexical_scores: dict[EntityId, float] = {}
        lexical_entities: dict[EntityId, Entity] = {}
        if mode in (RetrievalMode.LEXICAL, RetrievalMode.HYBRID):
            lexical_scores, lexical_entities = await self._lexical(
                query, tenant_id, per_channel, entity_types
            )

        fused = reciprocal_rank_fusion([list(semantic_scores), list(lexical_scores)])[:k]

        resolved = await self._resolve(
            [entity_id for entity_id, _ in fused], lexical_entities, tenant_id
        )
        matches = [
            ScoredEntity(
                entity=resolved[entity_id],
                score=score,
                semantic=semantic_scores.get(entity_id),
                lexical=lexical_scores.get(entity_id),
            )
            for entity_id, score in fused
            if entity_id in resolved
        ]
        return RetrievalResult(query=query, matches=matches)

    async def _semantic(
        self,
        query: str,
        tenant_id: TenantId,
        k: int,
        entity_types: Sequence[str] | None,
    ) -> dict[EntityId, float]:
        """Embed the query and search, best first.

        A `dict` rather than a list plus a mapping: it carries the ranking in
        its insertion order *and* the score per id, and the two cannot fall
        out of step because there is only one of them.
        """
        embeddings, vectors = self._embeddings, self._vectors
        if embeddings is None or vectors is None:  # pragma: no cover -- `retrieve` refuses first
            raise ValueError("this is a lexical-only retriever")
        [vector] = await embeddings.embed_query([query])
        matches = await vectors.search(vector, tenant_id, k=k, entity_types=entity_types)
        return {match.entity_id: match.score for match in matches}

    async def _lexical(
        self,
        query: str,
        tenant_id: TenantId,
        k: int,
        entity_types: Sequence[str] | None,
    ) -> tuple[dict[EntityId, float], dict[EntityId, Entity]]:
        """Score this query's blocking-key candidates, best first.

        Candidates are deduplicated by id before scoring: an entity carrying
        several of the requested keys appears under each, and scoring it twice
        would let the blocking scheme decide the ranking.

        The type filter runs here, over every candidate, and the truncation to
        `k` runs after the sort -- filtering after truncating returns fewer
        results than exist whenever a non-matching candidate outranks a
        matching one.
        """
        groups = await self._graph.find_by_blocking_keys(query_blocking_keys(query), tenant_id)

        candidates: dict[EntityId, Entity] = {}
        for found in groups.values():
            for entity in found:
                if entity.id in candidates:
                    continue
                if entity_types is not None and entity.entity_type not in entity_types:
                    continue
                candidates[entity.id] = entity

        ranked = sorted(
            ((entity, lexical_score(query, entity)) for entity in candidates.values()),
            key=lambda item: (-item[1], str(item[0].id)),
        )[:k]
        return (
            {entity.id: score for entity, score in ranked},
            {entity.id: entity for entity, _ in ranked},
        )

    async def _resolve(
        self,
        entity_ids: Sequence[EntityId],
        known: dict[EntityId, Entity],
        tenant_id: TenantId,
    ) -> dict[EntityId, Entity]:
        """The entities behind `entity_ids`, in one round trip for the unknown.

        The lexical channel already holds its candidates, so only the ids it
        did not supply are fetched. `get_entities` returns them in unspecified
        order and omits ids it does not have, so the result is keyed by `id`
        rather than zipped -- and an id neither source has is simply absent,
        which is what makes a dangling vector match a skip rather than a
        `KeyError`.
        """
        resolved = {entity_id: known[entity_id] for entity_id in entity_ids if entity_id in known}
        missing = [entity_id for entity_id in entity_ids if entity_id not in resolved]
        if missing:
            for entity in await self._graph.get_entities(missing, tenant_id):
                resolved[entity.id] = entity
        return resolved


class ChunkRetriever:
    """Ranked chunk retrieval, fusing a semantic and a lexical channel.

    The chunk-corpus mirror of `Retriever` above: same guard order, same
    `overfetch` semantics, the same reciprocal-rank fusion over two channels'
    id lists. It holds `chunks` as `ChunkStore` rather than the narrower
    intersection of `LexicalCandidateSource` and `SemanticCandidateSource` it
    actually calls -- Python cannot express a Protocol intersection without
    inventing a synthetic one, and inventing a port nobody asked for is worse
    than the honest overstatement in the annotation. Only those two
    capabilities are used; `ChunkWriter`, `ChunkReader` and `ChunkPurge` are
    never called.

    Two limits a caller meets as results that look like bugs:

    - **Lexical recall is bounded by the candidate `limit`** passed to
      `lexical_candidates` (`ports/chunk_store.py`'s truncation contract): a
      chunk matching one rare, informative term can be cut before one
      matching two common ones, so a passage that would have ranked first can
      be absent entirely.
    - **A corpus with no embeddings answers a semantic query with nothing,
      and does not raise.** "Unembedded" is a per-row fact on `StoredChunk`,
      not a property of the corpus as a whole, so there is no honest way to
      refuse the query -- refusing would mean refusing some rows and not
      others, mid-answer. A `HYBRID` query over such a corpus still returns
      its lexical results; only the semantic channel goes silent.
    """

    def __init__(
        self,
        *,
        embeddings: EmbeddingProvider,
        chunks: ChunkStore,
        overfetch: int = 3,
    ) -> None:
        """Wire the two collaborators, refusing a mismatched pair.

        See `Retriever.__init__` for why `overfetch` defaults to 3 and why
        the dimension check happens here, at construction, rather than after
        the query has been embedded.
        """
        if embeddings.dimension != chunks.dimension:
            raise DimensionMismatchError(expected=chunks.dimension, actual=embeddings.dimension)
        self._wire(embeddings, chunks, overfetch, RetrievalMode.HYBRID)

    @classmethod
    def lexical_only(cls, *, chunks: ChunkStore, overfetch: int = 3) -> ChunkRetriever:
        """A chunk retriever with no embedding provider.

        `Retriever.lexical_only`'s mirror, for the same reason and with the
        same shape: the lexical channel here is `lexical_candidates` plus
        `rank_chunks`, and neither touches a vector.

        Note what this does *not* change. A `HYBRID` query over a corpus whose
        rows carry no embedding still answers lexically and still does not
        raise -- "unembedded" is a per-row fact, as the class docstring says,
        and refusing it would mean refusing some rows mid-answer. A retriever
        with no provider at all is the different case: it is a configuration
        the caller stated, so `SEMANTIC` and `HYBRID` are refused outright.
        """
        self = cls.__new__(cls)
        self._wire(None, chunks, overfetch, RetrievalMode.LEXICAL)
        return self

    def _wire(
        self,
        embeddings: EmbeddingProvider | None,
        chunks: ChunkStore,
        overfetch: int,
        default_mode: RetrievalMode,
    ) -> None:
        """The guards both constructors share; see `Retriever._wire`."""
        if overfetch < 1:
            raise ValueError(f"overfetch must be at least 1, got {overfetch}")
        self._overfetch = overfetch
        self._embeddings = embeddings
        self._chunks = chunks
        self._default_mode = default_mode

    async def retrieve_chunks(
        self,
        query: str,
        tenant_id: TenantId,
        *,
        k: int = 10,
        mode: RetrievalMode | None = None,
    ) -> ChunkRetrievalResult:
        """The `k` best matching chunks for `query` in `tenant_id`, best first.

        `k=0` returns nothing; a negative `k` raises `ValueError`. A blank
        query raises rather than returning everything or nothing, matching
        `Retriever.retrieve`.

        `mode` defaults to the retriever's own default -- `HYBRID`, or
        `LEXICAL` for one built by `lexical_only`.
        """
        mode = self._default_mode if mode is None else mode
        if mode is not RetrievalMode.LEXICAL and self._embeddings is None:
            raise ValueError(
                f"this is a lexical-only retriever and cannot serve mode {mode.value!r}; "
                "construct ChunkRetriever(embeddings=..., chunks=...) for that"
            )
        if not query.strip():
            raise ValueError("query must not be blank")
        if k < 0:
            raise ValueError(f"k must not be negative, got {k}")
        if k == 0:
            return ChunkRetrievalResult(query=query, matches=[])

        # Each channel is asked for more than `k`; see `overfetch` on
        # `Retriever.__init__` for why fusion needs the candidates past each
        # cutoff.
        per_channel = k * self._overfetch

        semantic_scores: dict[ChunkId, float] = {}
        semantic_chunks: dict[ChunkId, StoredChunk] = {}
        if mode in (RetrievalMode.SEMANTIC, RetrievalMode.HYBRID):
            semantic_scores, semantic_chunks = await self._semantic(query, tenant_id, per_channel)

        lexical_scores: dict[ChunkId, float] = {}
        lexical_chunks: dict[ChunkId, StoredChunk] = {}
        if mode in (RetrievalMode.LEXICAL, RetrievalMode.HYBRID):
            lexical_scores, lexical_chunks = await self._lexical(query, tenant_id, per_channel)

        fused = reciprocal_rank_fusion([list(semantic_scores), list(lexical_scores)])[:k]

        known = {**semantic_chunks, **lexical_chunks}
        matches = [
            ScoredChunk(
                chunk=known[chunk_id],
                score=score,
                semantic=semantic_scores.get(chunk_id),
                lexical=lexical_scores.get(chunk_id),
            )
            for chunk_id, score in fused
            if chunk_id in known
        ]
        return ChunkRetrievalResult(query=query, matches=matches)

    async def _semantic(
        self,
        query: str,
        tenant_id: TenantId,
        k: int,
    ) -> tuple[dict[ChunkId, float], dict[ChunkId, StoredChunk]]:
        """Embed the query and search, best first.

        A corpus with no embedded chunks answers with an empty result here,
        never a raise -- see the class docstring.
        """
        embeddings = self._embeddings
        if embeddings is None:  # pragma: no cover -- `retrieve_chunks` refuses first
            raise ValueError("this is a lexical-only retriever")
        [vector] = await embeddings.embed_query([query])
        matches = await self._chunks.semantic_candidates(vector, tenant_id, k)
        return (
            {match.chunk.id: match.score for match in matches},
            {match.chunk.id: match.chunk for match in matches},
        )

    async def _lexical(
        self,
        query: str,
        tenant_id: TenantId,
        k: int,
    ) -> tuple[dict[ChunkId, float], dict[ChunkId, StoredChunk]]:
        """Tokenize the query, fetch candidates, and rank them, best first."""
        terms = tokenize(query)
        candidates = await self._chunks.lexical_candidates(terms, tenant_id, k)
        ranked = rank_chunks(terms, candidates, k)
        return (
            {result.chunk.id: result.score for result in ranked},
            {result.chunk.id: result.chunk for result in ranked},
        )
