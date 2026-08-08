"""Blocking and scoring reads the graph; the signature now says only that.

`ports/graph_store.py` states, as a claim about this codebase, that
collaborators should not depend on eighteen methods to call three and should
"narrow the annotation to the capability actually used".
`docs/adr/0016-graph-store-is-five-capabilities.md` then left `CandidateFinder`
on the whole port, reasoning that a collaborator spanning three capabilities is
honestly typed by the composed one.

Three of five is not five, and the two it did not span are the two whose
absence is the point. `CandidateFinder`'s own docstring is "Blocks and scores.
Never writes, never decides" -- while it held `EntityWriter`. And it held
`TenantPurge`, whose docstring says its whole purpose is making "this
collaborator can wipe a tenant" a visible fact about a signature. A capability
that is load-bearing only when absent cannot be granted by default without
retiring it.

`BlockingGraph` below is the adapter that shows the cost: `ConsolidationGraph`
and **nothing else** -- no `upsert_entity`, no `delete_by_tenant` -- driving a
real `CandidateFinder` to a real candidate list.

**What these tests do not prove.** Every runtime assertion here would have
passed before the narrowing: `isinstance` against a `runtime_checkable`
Protocol is structural, so the double satisfies what it satisfies either way.
What could not have been written before is the *annotation* -- `uv run mypy`
over `src/redstring` is the standing gate, and this double is what makes a
reverted narrowing fail rather than merely read differently. The
`not isinstance(...)` assertions are the half with runtime teeth: they fail the
moment the double grows a method it is supposed to lack, which is how a
segregation test quietly stops testing segregation.

The double subclasses nothing, per ADR 0026. Subclassing `InMemoryGraphStore`
would satisfy every capability however the protocols were declared.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from redstring.consolidation.candidates import CandidateFinder, ConsolidationGraph
from redstring.domain.blocking import blocking_keys_for
from redstring.graph.adapters.memory import InMemoryGraphStore
from redstring.ports.graph_store import (
    AliasStore,
    EntityReader,
    EntityWriter,
    GraphStore,
    RelationshipStore,
    TenantPurge,
)
from tests.unit.consolidation.conftest import edge, entity

if TYPE_CHECKING:
    from redstring.domain.entity import Entity
    from redstring.domain.ids import EntityId, TenantId


def blocked(tenant_id: TenantId, name: str) -> Entity:
    built = entity(tenant_id, name=name)
    return built.model_copy(update={"blocking_keys": blocking_keys_for(built)})


class BlockingGraph:
    """`ConsolidationGraph` and not one method more.

    Eleven methods rather than three: the composition names every method of
    `EntityReader`, `AliasStore` and `RelationshipStore`, which is the honest
    price of composing capabilities instead of inventing a caller-shaped
    three-method interface. It is still seven fewer than the whole port, and
    the seven it omits are every write and the tenant purge.
    """

    def __init__(self, entities: list[Entity], edges: list[Any] | None = None) -> None:
        self.entities = entities
        self.edges = edges or []

    # -- EntityReader ---------------------------------------------------

    async def get_entity(self, entity_id: EntityId, tenant_id: TenantId) -> Entity | None:
        for found in self.entities:
            if found.id == entity_id and found.tenant_id == tenant_id:
                return found
        return None

    async def get_entities(self, entity_ids: Any, tenant_id: TenantId) -> list[Entity]:
        wanted = set(entity_ids)
        return [e for e in self.entities if e.id in wanted and e.tenant_id == tenant_id]

    async def find_entities(self, tenant_id: TenantId, **_kwargs: Any) -> list[Entity]:
        return [e for e in self.entities if e.tenant_id == tenant_id]

    async def find_by_blocking_key(self, key: str, tenant_id: TenantId) -> list[Entity]:
        return [
            e for e in self.entities if e.tenant_id == tenant_id and key in (e.blocking_keys or ())
        ]

    async def find_by_blocking_keys(
        self, keys: Any, tenant_id: TenantId
    ) -> dict[str, list[Entity]]:
        return {key: await self.find_by_blocking_key(key, tenant_id) for key in keys}

    # -- AliasStore -----------------------------------------------------

    async def upsert_alias(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError("consolidation writes aliases through the log, not the store")

    async def remove_alias(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError("consolidation writes aliases through the log, not the store")

    async def find_aliases(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        raise NotImplementedError("the finder resolves ids; it never lists aliases")

    async def resolve_entity_ids(
        self, entity_ids: Any, _tenant_id: TenantId
    ) -> dict[EntityId, EntityId]:
        # No aliases in this rig: every id is its own canonical form.
        return {entity_id: entity_id for entity_id in entity_ids}

    # -- RelationshipStore ----------------------------------------------

    async def get_relationships(self, entity_id: EntityId, tenant_id: TenantId) -> list[Any]:
        return [
            held
            for held in self.edges
            if held.tenant_id == tenant_id
            and entity_id in (held.source_entity_id, held.target_entity_id)
        ]

    async def get_relationships_for(self, *_args: Any, **_kwargs: Any) -> dict[Any, list[Any]]:
        raise NotImplementedError("the batch form is ConsolidationService's, not the finder's")

    async def neighbors(self, *_args: Any, **_kwargs: Any) -> list[Entity]:
        raise NotImplementedError("the finder reads edges, never traverses them")

    async def upsert_relationship(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError("the finder never writes")

    async def upsert_relationships(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError("the finder never writes")

    async def delete_relationship(self, *_args: Any, **_kwargs: Any) -> bool:
        raise NotImplementedError("the finder never writes")


class TestTheDoubleIsExactlyTheCapabilitiesTheFinderCalls:
    def test_it_satisfies_the_composition_and_not_the_port(self) -> None:
        # The whole point. If this ever became a `GraphStore`, the double had
        # grown the writes and the purge, and every test below would be back
        # to exercising a full adapter.
        graph = BlockingGraph([])

        assert isinstance(graph, ConsolidationGraph)
        assert not isinstance(graph, GraphStore)

    def test_it_satisfies_each_composed_capability(self) -> None:
        graph = BlockingGraph([])

        for capability in (EntityReader, AliasStore, RelationshipStore):
            assert isinstance(graph, capability), capability.__name__

    def test_the_two_capabilities_it_declines_are_genuinely_absent(self) -> None:
        # `TenantPurge` is the one this module exists for: its own docstring
        # says its purpose is making "this collaborator can wipe a tenant" a
        # visible fact, and the finder held it for as long as it held the port.
        graph = BlockingGraph([])

        assert not isinstance(graph, EntityWriter)
        assert not isinstance(graph, TenantPurge)
        assert not hasattr(graph, "upsert_entity")
        assert not hasattr(graph, "delete_by_tenant")


class TestTheFinderRunsOnTheCompositionAlone:
    async def test_it_blocks_and_scores_without_a_writer_or_a_purge(self) -> None:
        tenant_id = uuid4()
        subject = blocked(tenant_id, "Ada Lovelace")
        twin = blocked(tenant_id, "Ada Lovelace")
        stranger = blocked(tenant_id, "Charles Babbage")

        found = await CandidateFinder(
            BlockingGraph([subject, twin, stranger]), use_graph_signal=False
        ).candidates(subject)

        # The stranger is *in* the block -- `t:person` is a blocking key and
        # all three share it -- so this asserts the ranking rather than the
        # recall. A finder returning everything unscored would pass a bare
        # membership check; it cannot pass an ordering plus two distinct
        # scores.
        assert [scored.entity.id for scored in found] == [twin.id, stranger.id]
        assert found[0].score == pytest.approx(1.0)
        assert found[1].score < found[0].score

    async def test_the_graph_signal_reads_edges_through_the_composition(self) -> None:
        """The `RelationshipStore` third of it, exercised rather than assumed.

        Without this, `BlockingGraph` could omit `get_relationships` entirely
        and every other test here would still pass -- which is the shape where
        a capability is in the composition because someone wrote it down.
        """
        tenant_id = uuid4()
        subject = blocked(tenant_id, "Ada Lovelace")
        twin = blocked(tenant_id, "Ada Lovelace")
        shared = blocked(tenant_id, "Analytical Engine")

        graph = BlockingGraph(
            [subject, twin, shared],
            [
                edge(tenant_id, source=subject.id, target=shared.id),
                edge(tenant_id, source=twin.id, target=shared.id),
            ],
        )
        found = await CandidateFinder(graph, use_graph_signal=True).candidates(subject)

        [scored] = [item for item in found if item.entity.id == twin.id]
        # A neighbour set of exactly `{shared}` on both sides: 1.0 rather than
        # merely "not None", so a feature wired to the wrong operand shows.
        assert scored.features.graph == pytest.approx(1.0)


class TestTheNarrowedAnnotationsAreWhatIsDeclared:
    """The half of this module that a revert cannot survive.

    Measured rather than assumed: widening `graph_store` back to `GraphStore`
    leaves every behavioural test above green -- `BlockingGraph` still has the
    eleven methods the finder calls, and nothing checks an annotation at
    runtime -- and `uv run mypy` silent, because the configured gate covers
    `src/redstring` and not `tests/`. A narrowing whose only evidence is a
    behavioural test has no evidence.
    """

    def test_the_finder_asks_for_the_composition_and_not_the_port(self) -> None:
        import inspect

        assert (
            inspect.get_annotations(CandidateFinder.__init__)["graph_store"] == "ConsolidationGraph"
        )

    def test_the_retriever_asks_for_an_entity_reader(self) -> None:
        import inspect

        from redstring.composition.retrieval import Retriever

        assert inspect.get_annotations(Retriever.__init__)["graph"] == "EntityReader"


class TestTheRealAdapterStillSatisfiesTheComposition:
    def test_an_in_memory_graph_store_is_a_consolidation_graph(self) -> None:
        # Guards against the composition drifting away from the port: a
        # method renamed on `GraphStore` and not here would leave the real
        # adapter unable to serve the finder, and nothing else would notice.
        store = InMemoryGraphStore()

        assert isinstance(store, ConsolidationGraph)
        assert isinstance(store, GraphStore)
