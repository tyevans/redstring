"""The composed retrieval surface.

Every store here is a **real in-memory adapter**, never a `MagicMock`. A
`MagicMock` answers any attribute, which is how this repo shipped 583 lines of
a router keyed on a deleted model with a fully green suite.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest

from redstring.composition.retrieval import Retriever
from redstring.domain.blocking import (
    blocking_keys_for,
    prefix_key_for_name,
    query_blocking_keys,
    soundex_key_for_name,
)
from redstring.domain.entity import Entity, ExtractionMethod
from redstring.domain.exceptions import DimensionMismatchError
from redstring.domain.normalization import normalize_name
from redstring.domain.retrieval import RetrievalMode
from redstring.graph.adapters.memory import InMemoryGraphStore
from redstring.llm.adapters.fake_embedding import FakeEmbeddingProvider
from redstring.vector.adapters.memory import InMemoryVectorStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redstring.domain.alias import Alias
    from redstring.domain.ids import EntityId, TenantId

#: The width `nomic-embed-text` produces, and `FakeEmbeddingProvider`'s default.
#: Realistic on purpose: CLAUDE.md records a dimension check written with
#: `is not` that passed at a test dimension of 8 and rejected every legitimate
#: vector at 768, because CPython caches small integers.
DIMENSION = 768


def _entity(
    name: str,
    tenant_id: TenantId,
    *,
    entity_type: str = "person",
    entity_id: EntityId | None = None,
    properties: dict[str, Any] | None = None,
    blocking_keys: frozenset[str] | None = None,
) -> Entity:
    """An entity carrying its blocking keys, as the extractor would write it.

    `blocking_keys` is overridable because two tests need a *specific*
    arrangement of which entity is reachable under which key, and the store
    computes nothing -- it only groups by what the entity carries.
    """
    entity = Entity(
        id=entity_id or uuid4(),
        tenant_id=tenant_id,
        name=name,
        normalized_name=normalize_name(name),
        entity_type=entity_type,
        properties=properties or {},
        extraction_method=ExtractionMethod.MANUAL,
        confidence=1.0,
    )
    keys = blocking_keys if blocking_keys is not None else blocking_keys_for(entity)
    return entity.model_copy(update={"blocking_keys": keys})


def _retriever(
    graph: InMemoryGraphStore | Any,
    vectors: InMemoryVectorStore | None = None,
    embeddings: FakeEmbeddingProvider | None = None,
) -> Retriever:
    return Retriever(
        embeddings=embeddings or FakeEmbeddingProvider(dimension=DIMENSION),
        vectors=vectors or InMemoryVectorStore(dimension=DIMENSION),
        graph=graph,
    )


async def _store_vector(
    vectors: InMemoryVectorStore,
    embeddings: FakeEmbeddingProvider,
    entity: Entity,
) -> None:
    [vector] = await embeddings.embed([entity.name])
    await vectors.upsert(
        entity.id, vector, entity.tenant_id, metadata={"entity_type": entity.entity_type}
    )


# ----------------------------------------------------------------------
# The happy path
# ----------------------------------------------------------------------


async def test_an_exact_name_is_retrieved() -> None:
    tenant = uuid4()
    graph = InMemoryGraphStore()
    ada = _entity("Ada Lovelace", tenant)
    await graph.upsert_entity(ada)

    result = await _retriever(graph).retrieve("Ada Lovelace", tenant)

    assert result.query == "Ada Lovelace"
    assert [match.entity.id for match in result.matches] == [ada.id]


async def test_two_tenants_holding_the_same_entity_id_never_cross() -> None:
    """The composite-key case, forced rather than hoped for.

    Ids come from `uuid4()` everywhere else in this repo and never collide, so
    a `(tenant_id, id)` key compared on `id` alone survives every natural
    test. CLAUDE.md records this firing anyway, in a fix round that cited the
    rule. Both tenants get the *same* `EntityId` with different names; each
    retrieve must see only its own.
    """
    left, right = uuid4(), uuid4()
    shared_id = uuid4()
    graph = InMemoryGraphStore()
    await graph.upsert_entity(_entity("Ada Lovelace", left, entity_id=shared_id))
    await graph.upsert_entity(_entity("Ada Lovelace", right, entity_id=shared_id))

    retriever = _retriever(graph)
    from_left = await retriever.retrieve("Ada Lovelace", left)
    from_right = await retriever.retrieve("Ada Lovelace", right)

    assert [match.entity.tenant_id for match in from_left.matches] == [left]
    assert [match.entity.tenant_id for match in from_right.matches] == [right]


# ----------------------------------------------------------------------
# The two stores lag independently
# ----------------------------------------------------------------------


async def test_a_vector_match_whose_entity_the_graph_lacks_is_skipped() -> None:
    """The two stores are independent projections and lag independently.

    The vector is written and the entity is not. The result must omit it and
    must not raise -- raising would make retrieval fail during replay.
    """
    tenant = uuid4()
    graph = InMemoryGraphStore()
    vectors = InMemoryVectorStore(dimension=DIMENSION)
    embeddings = FakeEmbeddingProvider(dimension=DIMENSION)
    dangling = _entity("Ada Lovelace", tenant)
    await _store_vector(vectors, embeddings, dangling)

    result = await _retriever(graph, vectors, embeddings).retrieve(
        "Ada Lovelace", tenant, mode=RetrievalMode.SEMANTIC
    )

    assert result.matches == []


async def test_a_skipped_dangling_match_is_not_backfilled() -> None:
    """Distinct from the test above: this one asserts the *count*.

    Backfilling is only reachable when the fused list is **longer than `k`**,
    which a single-channel retrieval never produces -- each channel is asked
    for exactly `k`, so in `SEMANTIC` mode there is nothing below the cut to
    top up *from*, and a backfilling implementation passes. It took planting
    the defect to find that out; the arrangement below is what makes it fire.

    So: `HYBRID` with `k=1` and one candidate per channel, which fuses to a
    two-element list truncated to one. Both sit at rank 0 and therefore score
    identically, so the tie-break on ascending id string decides -- and the ids
    are fixed, not `uuid4()`, because a random pair would put the intended
    winner first only about half the time.

    `dangling` wins the tie and has no entity in the graph, so the answer is
    **nothing**. A version that topped up to `k` returns `named` instead.
    """
    tenant = uuid4()
    graph = InMemoryGraphStore()
    vectors = InMemoryVectorStore(dimension=DIMENSION)
    embeddings = FakeEmbeddingProvider(dimension=DIMENSION)

    # Sorts before `named` as a string, so it survives the truncation to k=1.
    dangling_id = UUID(int=1)
    [vector] = await embeddings.embed(["Ada Lovelace"])
    await vectors.upsert(dangling_id, vector, tenant)

    # The lexical channel's only candidate, and the one a backfill would reach.
    named = _entity("Ada Lovelace", tenant, entity_id=UUID(int=2))
    await graph.upsert_entity(named)

    retriever = _retriever(graph, vectors, embeddings)
    assert str(dangling_id) < str(named.id), "the tie-break must favour the dangling id"

    result = await retriever.retrieve("Ada Lovelace", tenant, k=1)

    assert result.matches == []


# ----------------------------------------------------------------------
# The lexical channel
# ----------------------------------------------------------------------


async def test_the_lexical_channel_scores_a_candidate_after_a_zero_scoring_one() -> None:
    """A bad row followed by a good one.

    On a one-element remainder `break` and `continue` are the same function,
    so the arrangement has to put something *before* the answer that the loop
    skips. `find_by_blocking_keys` returns one group per requested key, in the
    order asked, and an entity carrying several keys appears under each -- so
    the weak candidate carries both keys and is therefore seen a second time,
    as a duplicate, immediately before the strong candidate in the same group.
    A loop that stopped at that skip would drop the answer.
    """
    tenant = uuid4()
    prefix, soundex = prefix_key_for_name("Ada Lovelace"), soundex_key_for_name("Ada Lovelace")
    assert soundex is not None
    assert query_blocking_keys("Ada Lovelace") == [prefix, soundex]

    weak = _entity("Adalbert Zyzzyva", tenant, blocking_keys=frozenset({prefix, soundex}))
    strong = _entity("Ada Lovelace", tenant, blocking_keys=frozenset({soundex}))
    graph = InMemoryGraphStore()
    await graph.upsert_entity(weak)
    await graph.upsert_entity(strong)

    result = await _retriever(graph).retrieve("Ada Lovelace", tenant, mode=RetrievalMode.LEXICAL)

    assert [match.entity.id for match in result.matches] == [strong.id, weak.id]


async def test_entity_types_filters_the_lexical_channel_before_k_is_applied() -> None:
    """Filter-before-k, the defect `ports/vector_store.py` calls out by name.

    `k=1` with one non-matching candidate ranked *above* one matching
    candidate. Truncating first and filtering after returns nothing while a
    match exists.
    """
    tenant = uuid4()
    graph = InMemoryGraphStore()
    # Scores 1.0 and so ranks first, but is the wrong type.
    await graph.upsert_entity(_entity("Ada Lovelace", tenant, entity_type="concept"))
    wanted = _entity("Ada Lovelac", tenant, entity_type="person")
    await graph.upsert_entity(wanted)

    result = await _retriever(graph).retrieve(
        "Ada Lovelace", tenant, k=1, entity_types=["person"], mode=RetrievalMode.LEXICAL
    )

    assert [match.entity.id for match in result.matches] == [wanted.id]


async def test_empty_entity_types_matches_nothing() -> None:
    """`[]` means nothing matches; `None` means no filter. Same as the port."""
    tenant = uuid4()
    graph = InMemoryGraphStore()
    await graph.upsert_entity(_entity("Ada Lovelace", tenant))
    retriever = _retriever(graph)

    filtered = await retriever.retrieve("Ada Lovelace", tenant, entity_types=[])
    unfiltered = await retriever.retrieve("Ada Lovelace", tenant, entity_types=None)

    assert filtered.matches == []
    assert [match.entity.id for match in unfiltered.matches] != []


# ----------------------------------------------------------------------
# What the modes cost and what they report
# ----------------------------------------------------------------------


async def test_a_result_reports_both_component_scores_when_both_channels_ranked() -> None:
    tenant = uuid4()
    graph = InMemoryGraphStore()
    vectors = InMemoryVectorStore(dimension=DIMENSION)
    embeddings = FakeEmbeddingProvider(dimension=DIMENSION)
    ada = _entity("Ada Lovelace", tenant)
    await graph.upsert_entity(ada)
    await _store_vector(vectors, embeddings, ada)

    result = await _retriever(graph, vectors, embeddings).retrieve("Ada Lovelace", tenant)

    [match] = result.matches
    assert match.semantic is not None
    assert match.lexical == pytest.approx(1.0)
    assert match.score > 0.0


async def test_a_semantic_only_mode_leaves_lexical_none() -> None:
    """`None` is the claim that the channel did not rank it -- see the type."""
    tenant = uuid4()
    graph = InMemoryGraphStore()
    vectors = InMemoryVectorStore(dimension=DIMENSION)
    embeddings = FakeEmbeddingProvider(dimension=DIMENSION)
    ada = _entity("Ada Lovelace", tenant)
    await graph.upsert_entity(ada)
    await _store_vector(vectors, embeddings, ada)

    result = await _retriever(graph, vectors, embeddings).retrieve(
        "Ada Lovelace", tenant, mode=RetrievalMode.SEMANTIC
    )

    [match] = result.matches
    assert match.lexical is None
    assert match.semantic is not None


async def test_a_lexical_only_mode_makes_no_embedding_call() -> None:
    """A mode that embedded anyway would be correct in its output.

    It would also cost a paid round trip per query, which is invisible to
    every assertion about results.
    """

    class CountingProvider(FakeEmbeddingProvider):
        def __init__(self) -> None:
            super().__init__(dimension=DIMENSION)
            self.calls = 0

        async def embed(self, texts: Sequence[str]) -> list[list[float]]:
            self.calls += 1
            return await super().embed(texts)

    tenant = uuid4()
    graph = InMemoryGraphStore()
    await graph.upsert_entity(_entity("Ada Lovelace", tenant))
    embeddings = CountingProvider()

    result = await _retriever(graph, embeddings=embeddings).retrieve(
        "Ada Lovelace", tenant, mode=RetrievalMode.LEXICAL
    )

    assert result.matches != []
    assert embeddings.calls == 0


# ----------------------------------------------------------------------
# Equality, isolation, and the argument bounds
# ----------------------------------------------------------------------


async def test_entities_are_compared_by_equality_not_identity() -> None:
    """Both shipped adapters hand back the object they were given.

    So `is` where `==` was meant passes against both, and a contract two
    implementations satisfy by accident is not a contract. This wrapper
    returns equal-but-distinct entities, which no port forbids.
    """

    class RebuildingGraphStore:
        def __init__(self, inner: InMemoryGraphStore) -> None:
            self._inner = inner

        @staticmethod
        def _rebuild(entity: Entity) -> Entity:
            return Entity.model_validate(entity.model_dump())

        async def get_entities(
            self, entity_ids: Sequence[EntityId], tenant_id: TenantId
        ) -> list[Entity]:
            found = await self._inner.get_entities(entity_ids, tenant_id)
            return [self._rebuild(entity) for entity in found]

        async def find_by_blocking_keys(
            self, keys: Sequence[str], tenant_id: TenantId
        ) -> dict[str, list[Entity]]:
            groups = await self._inner.find_by_blocking_keys(keys, tenant_id)
            return {key: [self._rebuild(e) for e in found] for key, found in groups.items()}

        async def find_aliases(
            self, canonical_entity_id: EntityId, tenant_id: TenantId
        ) -> list[Alias]:
            return await self._inner.find_aliases(canonical_entity_id, tenant_id)

    tenant = uuid4()
    inner = InMemoryGraphStore()
    ada = _entity("Ada Lovelace", tenant)
    await inner.upsert_entity(ada)

    result = await _retriever(RebuildingGraphStore(inner)).retrieve("Ada Lovelace", tenant)

    [match] = result.matches
    assert match.entity == ada
    assert match.entity is not ada


async def test_mutating_a_result_cannot_change_what_a_later_retrieve_returns() -> None:
    tenant = uuid4()
    graph = InMemoryGraphStore()
    await graph.upsert_entity(_entity("Ada Lovelace", tenant, properties={"note": "original"}))
    retriever = _retriever(graph)

    first = await retriever.retrieve("Ada Lovelace", tenant)
    first.matches[0].entity.properties["note"] = "tampered"
    first.matches.clear()

    second = await retriever.retrieve("Ada Lovelace", tenant)

    assert [match.entity.properties["note"] for match in second.matches] == ["original"]


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
async def test_a_blank_query_raises(blank: str) -> None:
    graph = InMemoryGraphStore()
    with pytest.raises(ValueError, match="query"):
        await _retriever(graph).retrieve(blank, uuid4())


async def test_k_zero_returns_nothing_and_a_negative_k_raises() -> None:
    """Both pinned as literals.

    `VectorStore.search` says the same, and a property sampling `k` from a
    range makes boundary coverage depend on the sampler and on the lowered
    example count under mutation.
    """
    tenant = uuid4()
    graph = InMemoryGraphStore()
    await graph.upsert_entity(_entity("Ada Lovelace", tenant))
    retriever = _retriever(graph)

    empty = await retriever.retrieve("Ada Lovelace", tenant, k=0)
    assert empty.matches == []
    assert empty.query == "Ada Lovelace"

    with pytest.raises(ValueError, match="k"):
        await retriever.retrieve("Ada Lovelace", tenant, k=-1)


async def test_more_results_than_k_are_truncated() -> None:
    tenant = uuid4()
    graph = InMemoryGraphStore()
    for n in range(5):
        await graph.upsert_entity(_entity(f"Ada Lovelace {n}", tenant))

    result = await _retriever(graph).retrieve("Ada Lovelace", tenant, k=2)

    assert len(result.matches) == 2


async def test_a_provider_and_store_of_different_dimensions_are_refused() -> None:
    """At construction, before any text is embedded -- `build_graph`'s rule."""
    with pytest.raises(DimensionMismatchError):
        Retriever(
            embeddings=FakeEmbeddingProvider(dimension=8),
            vectors=InMemoryVectorStore(dimension=16),
            graph=InMemoryGraphStore(),
        )
