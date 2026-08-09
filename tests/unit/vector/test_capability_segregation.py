"""One method is enough to drive `VectorProjection`, and the port says so now.

`VectorStore` is seven methods. `VectorProjection` calls `upsert_many`. One of
seven, and the same shape `docs/adr/0026-chunk-store-and-cache-are-capabilities-too.md`
recorded for `ChunkProjection` -- which was narrowed to `ChunkWriter` while
`VectorProjection`, its sibling in the same package doing the same job, was
left on the whole port.

`WriteOnlyVectorStore` below is the adapter that costs. It has `dimension`,
`upsert` and `upsert_many` and **nothing else** -- no `get`, no `search`, no
deletes -- and it folds an `EntitiesEmbedded` event to completion. Before the
split it could not have been annotated as anything the projection accepted.

`SearchOnlyVectorStore` is the read half, and it is what `Retriever` and
`CandidateFinder` are now typed against. Both of them read; neither can now
write or wipe a tenant, which is a fact about their signatures rather than
about their bodies.

**What these tests do not prove.** Every runtime assertion here would have
passed before the split, because `isinstance` against a `runtime_checkable`
Protocol is structural and the doubles have the methods they have either way.
What could not have been written before is the *annotation*: `mypy --strict`
over `src/redstring` is the standing gate, and these doubles are what make a
reverted narrowing fail something rather than merely read differently. The
`not isinstance(..., VectorStore)` assertions are the part with teeth at
runtime -- they fail the moment a double grows the methods it is supposed to
lack, which is how a segregation test quietly stops testing segregation.

Neither double subclasses anything, per ADR 0026: a double built by
subclassing `InMemoryVectorStore` would satisfy the whole port however the
protocols were declared, and could not tell you the split held.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from eventsource.adapters.memory import InMemoryCheckpointRepository, InMemoryDLQRepository

from redstring.domain.vector import VectorMatch, VectorRecord
from redstring.events.document import EntitiesEmbedded
from redstring.ports.vector_store import VectorPurge, VectorReader, VectorStore, VectorWriter
from redstring.projections.vector import VectorProjection
from redstring.testing.lifetime import NoOpLifetime
from redstring.vector.adapters.memory import InMemoryVectorStore

if TYPE_CHECKING:
    from redstring.domain.ids import EntityId, TenantId

DIMENSION = 4


class WriteOnlyVectorStore(NoOpLifetime):
    """`VectorWriter` and not one method more."""

    def __init__(self) -> None:
        self.rows: dict[tuple[TenantId, EntityId], VectorRecord] = {}

    @property
    def dimension(self) -> int:
        return DIMENSION

    async def upsert(
        self,
        entity_id: EntityId,
        vector: Any,
        tenant_id: TenantId,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.rows[(tenant_id, entity_id)] = VectorRecord(
            entity_id=entity_id,
            tenant_id=tenant_id,
            vector=list(vector),
            metadata=metadata or {},
        )

    async def upsert_many(self, items: Any) -> None:
        for record in items:
            await self.upsert(
                record.entity_id,
                record.vector,
                record.tenant_id,
                metadata=record.metadata,
            )


class SearchOnlyVectorStore(NoOpLifetime):
    """`VectorReader` and not one method more.

    `get` and `search` together, because that is the slice both read-side
    consumers ask for; see the capability's own docstring for why the port
    does not split them further.
    """

    def __init__(self, records: list[VectorRecord]) -> None:
        self.records = records

    @property
    def dimension(self) -> int:
        return DIMENSION

    async def get(self, entity_id: EntityId, tenant_id: TenantId) -> VectorRecord | None:
        for record in self.records:
            if record.entity_id == entity_id and record.tenant_id == tenant_id:
                return record
        return None

    async def search(
        self,
        vector: Any,
        tenant_id: TenantId,
        *,
        k: int = 10,
        entity_types: Any = None,
        min_score: float | None = None,
    ) -> list[VectorMatch]:
        return [
            VectorMatch(entity_id=record.entity_id, score=1.0, metadata=record.metadata)
            for record in self.records
            if record.tenant_id == tenant_id
        ][:k]


def record(tenant_id: TenantId, *, entity_id: EntityId | None = None) -> VectorRecord:
    return VectorRecord(
        entity_id=entity_id or uuid4(),
        tenant_id=tenant_id,
        vector=[1.0, 0.0, 0.0, 0.0],
        metadata={"entity_type": "person"},
    )


class TestTheProjectionNeedsOnlyTheWriter:
    def test_the_write_only_store_is_not_a_vector_store(self) -> None:
        # The whole point. If this ever became a `VectorStore`, the double had
        # grown the other four methods and the test below would be back to
        # exercising a full adapter.
        store = WriteOnlyVectorStore()

        assert isinstance(store, VectorWriter)
        assert not isinstance(store, VectorStore)
        assert not isinstance(store, VectorReader)
        assert not isinstance(store, VectorPurge)

    async def test_a_write_only_store_folds_an_embedding_event(self) -> None:
        tenant_id = uuid4()
        store = WriteOnlyVectorStore()
        embeddings = [record(tenant_id), record(tenant_id)]

        await VectorProjection(
            store,
            checkpoint_repo=InMemoryCheckpointRepository(),
            dlq_repo=InMemoryDLQRepository(),
        )._apply_embeddings(
            None,
            EntitiesEmbedded(
                tenant_id=tenant_id,
                aggregate_id=uuid4(),
                source_id="doc-1",
                embeddings=embeddings,
                embedding_model="fake",
            ),
        )

        # Both rows, keyed apart: a fold that wrote one would pass a bare
        # "the store is non-empty" assertion.
        assert set(store.rows) == {(tenant_id, item.entity_id) for item in embeddings}


class TestTheReadSideNeedsOnlyTheReader:
    def test_the_search_only_store_is_not_a_vector_store(self) -> None:
        store = SearchOnlyVectorStore([])

        assert isinstance(store, VectorReader)
        assert not isinstance(store, VectorStore)
        assert not isinstance(store, VectorWriter)
        assert not isinstance(store, VectorPurge)

    async def test_candidate_scoring_runs_on_a_reader_alone(self) -> None:
        """`CandidateFinder`'s embedding feature: `get` then `search`.

        Asserted through the finder rather than through the double, so a
        `VectorReader` that satisfied the protocol and was never consulted
        would still fail here.
        """
        from redstring.consolidation.candidates import CandidateFinder
        from redstring.domain.blocking import blocking_keys_for
        from redstring.graph.adapters.memory import InMemoryGraphStore
        from tests.unit.consolidation.conftest import entity

        tenant_id = uuid4()

        def blocked(name: str) -> Any:
            built = entity(tenant_id, name=name)
            return built.model_copy(update={"blocking_keys": blocking_keys_for(built)})

        subject = blocked("Ada Lovelace")
        other = blocked("Ada Lovelace")
        graph = InMemoryGraphStore()
        for known in (subject, other):
            await graph.upsert_entity(known)

        vectors = SearchOnlyVectorStore(
            [record(tenant_id, entity_id=subject.id), record(tenant_id, entity_id=other.id)]
        )
        found = await CandidateFinder(
            graph, vector_store=vectors, use_graph_signal=False
        ).candidates(subject)

        assert [scored.entity.id for scored in found] == [other.id]
        # Non-vacuous: the embedding feature must be *present*, or a finder
        # that never called the reader would produce the same candidate list.
        assert found[0].features.embedding == pytest.approx(1.0)


class TestTheComposedPortStillBindsEveryCapability:
    def test_the_real_adapter_satisfies_all_three(self) -> None:
        # Guards against the split becoming a fork: a capability the composed
        # port stopped naming would leave this assertion the only thing that
        # noticed.
        store = InMemoryVectorStore(dimension=DIMENSION)

        for capability in (VectorWriter, VectorReader, VectorPurge):
            assert isinstance(store, capability), capability.__name__
        assert isinstance(store, VectorStore)


class TestTheNarrowedAnnotationsAreWhatIsDeclared:
    """The half of this module that a revert cannot survive.

    Everything above is behavioural, and behaviour is exactly what a
    narrowing does not change -- `VectorProjection(WriteOnlyVectorStore())`
    runs identically whether its parameter says `VectorWriter` or
    `VectorStore`, because nothing checks a generic argument at runtime. That
    was measured rather than assumed: widening both annotations back left
    every other test in this file green and `uv run mypy` silent, because the
    configured gate covers `src/redstring` and not `tests/`.

    So the declarations are asserted directly. These are not a substitute for
    the type checker -- they cannot tell you a *caller* passes something too
    narrow -- but they are what makes reverting a narrowing fail something.
    """

    def test_the_projection_is_generic_over_the_writer(self) -> None:
        import typing

        [parameter] = typing.get_args(VectorProjection.__orig_bases__[0])  # type: ignore[attr-defined]
        assert parameter is VectorWriter

    def test_the_read_side_collaborators_ask_for_a_reader(self) -> None:
        import inspect

        from redstring.composition.retrieval import Retriever
        from redstring.consolidation.candidates import CandidateFinder

        # Raw strings: both modules use `from __future__ import annotations`
        # and import these under `if TYPE_CHECKING`, so there is nothing to
        # resolve them against at runtime -- which is also why the type
        # checker is the only thing that can enforce them properly.
        assert inspect.get_annotations(Retriever.__init__)["vectors"] == "VectorReader"
        assert (
            inspect.get_annotations(CandidateFinder.__init__)["vector_store"]
            == "VectorReader | None"
        )


class TestReachingAcrossTheSplitFails:
    @pytest.mark.parametrize(
        ("double", "absent"),
        [
            (WriteOnlyVectorStore(), "search"),
            (WriteOnlyVectorStore(), "delete_by_tenant"),
            (SearchOnlyVectorStore([]), "upsert_many"),
            (SearchOnlyVectorStore([]), "delete_by_tenant"),
        ],
    )
    def test_the_other_capabilities_are_genuinely_absent(self, double: object, absent: str) -> None:
        # Guards the guard. If a double quietly grew the method it is supposed
        # to lack, the tests above would pass while proving nothing about
        # segregation.
        assert not hasattr(double, absent)
