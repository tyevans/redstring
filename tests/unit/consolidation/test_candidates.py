"""Blocking and scoring: what gets compared, and how it ranks.

Real stores throughout -- `InMemoryGraphStore`, `InMemoryVectorStore`. A mocked
store would let `_block` return whatever the test wanted and prove nothing
about which query was made.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from redstring.consolidation.candidates import CandidateFinder
from redstring.domain.alias import Alias
from redstring.domain.blocking import blocking_keys_for
from redstring.domain.similarity import FeatureWeights
from redstring.graph.adapters.memory import InMemoryGraphStore
from redstring.vector.adapters.memory import InMemoryVectorStore

from .conftest import edge, entity

DIMENSION = 4


def keyed(tenant_id, name, **overrides):
    """An entity carrying the blocking keys extraction would have given it."""
    built = entity(tenant_id, name=name, **overrides)
    return built.model_copy(update={"blocking_keys": blocking_keys_for(built)})


async def _store_with(*entities):
    store = InMemoryGraphStore()
    await store.upsert_entities(list(entities))
    return store


class TestBlocking:
    async def test_only_entities_sharing_a_key_are_returned(self):
        tenant = uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        near = keyed(tenant, "Ada Lovelac")
        far = keyed(tenant, "Zebra Crossing", entity_type="animal")
        store = await _store_with(subject, near, far)

        found = await CandidateFinder(store).candidates(subject)

        assert [c.entity.id for c in found] == [near.id]

    async def test_the_subject_is_never_its_own_candidate(self):
        """`EntitiesMerged` refuses a self-merge, so proposing one produces a
        candidate nobody can act on."""
        tenant = uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        store = await _store_with(subject)

        assert await CandidateFinder(store).candidates(subject) == []

    async def test_an_entity_already_merged_away_is_excluded(self):
        """`ConsolidationLog` would refuse it with `DoubleMergeError`."""
        tenant = uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        absorbed = keyed(tenant, "Ada Lovelac")
        canonical = keyed(tenant, "Ada Lovelacx")
        store = await _store_with(subject, absorbed, canonical)
        await store.upsert_alias(
            Alias(
                id=uuid4(),
                tenant_id=tenant,
                canonical_entity_id=canonical.id,
                alias_entity_id=absorbed.id,
                merged_at=datetime.now(UTC),
            )
        )

        found = await CandidateFinder(store).candidates(subject)

        assert [c.entity.id for c in found] == [canonical.id]

    async def test_an_entity_with_no_keys_has_no_candidates(self):
        """Not an error. An entity extraction never keyed cannot be blocked,
        and scanning the tenant instead is the quadratic this avoids."""
        tenant = uuid4()
        subject = entity(tenant, name="Ada Lovelace")
        other = keyed(tenant, "Ada Lovelac")
        store = await _store_with(subject, other)

        assert await CandidateFinder(store).candidates(subject) == []

    async def test_a_candidate_carrying_several_keys_appears_once(self):
        """It is returned under each key it carries; scoring it three times
        would be wasted work and a list the caller has to deduplicate."""
        tenant = uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        twin = keyed(tenant, "Ada Lovelace")
        assert len(subject.blocking_keys & twin.blocking_keys) > 1
        store = await _store_with(subject, twin)

        found = await CandidateFinder(store).candidates(subject)

        assert [c.entity.id for c in found] == [twin.id]

    async def test_candidates_never_cross_tenants(self):
        tenant, other = uuid4(), uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        elsewhere = keyed(other, "Ada Lovelace")
        store = await _store_with(subject, elsewhere)

        assert await CandidateFinder(store).candidates(subject) == []


class TestResolutionIsByValue:
    """A candidate is kept when its resolved id *equals* its own, not when it
    is the same object.

    Both adapters in this repo hand back the identical `UUID` for an id that is
    not an alias, so `is` passes every other test here -- and would return an
    empty candidate list against any adapter that rebuilt the id from a row.
    That failure is silent: no candidates is exactly what "no duplicates" also
    looks like.

    The store below is a real `GraphStore`, not a mock. It differs from
    `InMemoryGraphStore` in one respect the port explicitly permits -- it
    returns equal-but-distinct ids -- which is the whole point: a port contract
    two adapters satisfy by accident is not a contract.
    """

    class RebuildingStore(InMemoryGraphStore):
        async def resolve_entity_ids(self, entity_ids, tenant_id):
            resolved = await super().resolve_entity_ids(entity_ids, tenant_id)
            return {UUID(str(key)): UUID(str(value)) for key, value in resolved.items()}

    async def test_resolution_by_value_not_by_identity(self):
        tenant = uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        candidate = keyed(tenant, "Ada Lovelac")
        store = self.RebuildingStore()
        await store.upsert_entities([subject, candidate])

        found = await CandidateFinder(store).candidates(subject)

        assert [c.entity.id for c in found] == [candidate.id]

    async def test_the_rebuilding_store_really_does_rebuild(self):
        """Guards the guard: if it returned the same objects, the test above
        would pass against an `is` comparison and prove nothing."""
        tenant = uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        store = self.RebuildingStore()
        await store.upsert_entity(subject)

        resolved = await store.resolve_entity_ids([subject.id], tenant)

        [(key, value)] = resolved.items()
        assert key == subject.id
        assert value == subject.id
        assert value is not subject.id


class TestScoring:
    async def test_a_closer_name_scores_higher(self):
        tenant = uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        close = keyed(tenant, "Ada Lovelace")
        distant = keyed(tenant, "Ada Lovecraft")
        store = await _store_with(subject, close, distant)

        found = await CandidateFinder(store, use_graph_signal=False).candidates(subject)

        assert [c.entity.id for c in found] == [close.id, distant.id]
        assert found[0].score > found[1].score

    async def test_the_per_signal_scores_are_reported(self):
        """A threshold decision that cannot be explained is one nobody can
        tune -- and "the name matched but nothing else did" reaches the same
        number as "everything matched a little"."""
        tenant = uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        other = keyed(tenant, "Ada Lovelace")
        store = await _store_with(subject, other)

        [found] = await CandidateFinder(store).candidates(subject)

        assert found.features.name == 1.0
        assert found.features.graph is not None
        assert found.features.embedding is None

    async def test_the_graph_signal_can_be_turned_off(self):
        """And when it is, the feature is absent rather than zero -- a zero
        would be evidence invented out of a configuration flag."""
        tenant = uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        other = keyed(tenant, "Ada Lovelace")
        store = await _store_with(subject, other)

        [found] = await CandidateFinder(store, use_graph_signal=False).candidates(subject)

        assert found.features.graph is None

    async def test_shared_neighbours_raise_the_score(self):
        """The signal the other two cannot supply: two records that barely
        look alike but sit in the same part of the graph."""
        tenant = uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        connected = keyed(tenant, "Ada Lovelacx")
        isolated = keyed(tenant, "Ada Lovelacx")
        shared = keyed(tenant, "Analytical Engine", entity_type="machine")
        store = await _store_with(subject, connected, isolated, shared)
        await store.upsert_relationships(
            [
                edge(tenant, source=subject.id, target=shared.id, kind="worked_on"),
                edge(tenant, source=connected.id, target=shared.id, kind="worked_on"),
            ]
        )

        found = {c.entity.id: c for c in await CandidateFinder(store).candidates(subject)}

        assert found[connected.id].features.graph == 1.0
        assert found[isolated.id].features.graph == 0.0
        assert found[connected.id].score > found[isolated.id].score

    async def test_the_embedding_signal_is_used_when_a_vector_store_is_given(self):
        tenant = uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        near = keyed(tenant, "Ada Lovelacx")
        vectors = InMemoryVectorStore(dimension=DIMENSION)
        await vectors.upsert(subject.id, [1.0, 0.0, 0.0, 0.0], tenant)
        await vectors.upsert(near.id, [1.0, 0.0, 0.0, 0.0], tenant)
        store = await _store_with(subject, near)

        [found] = await CandidateFinder(store, vector_store=vectors).candidates(subject)

        assert found.features.embedding == pytest.approx(1.0)

    async def test_a_subject_with_no_vector_simply_has_no_embedding_score(self):
        """A missing embedding must weaken the evidence, not stop the run."""
        tenant = uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        near = keyed(tenant, "Ada Lovelacx")
        vectors = InMemoryVectorStore(dimension=DIMENSION)
        await vectors.upsert(near.id, [1.0, 0.0, 0.0, 0.0], tenant)
        store = await _store_with(subject, near)

        [found] = await CandidateFinder(store, vector_store=vectors).candidates(subject)

        assert found.features.embedding is None
        assert found.score > 0.0

    async def test_weights_change_the_ranking(self):
        """Otherwise `FeatureWeights` is decoration.

        The two candidates disagree in opposite directions and by a *wide*
        margin on both signals. A near-miss name would not do: Jaro-Winkler
        scores "Ada Lovelace" against "Ada Lovelaxx" at 0.93, so a tenfold
        weight on the name is not enough to overcome a graph score of 1.0
        against 0.0 -- the first version of this test asserted the ranking
        flipped and it did not. The names are now unrelated, which is what
        makes the weights decisive rather than merely present.
        """
        tenant = uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        better_name = keyed(tenant, "Ada Lovelace")
        better_graph = keyed(tenant, "Zebedee Quill")
        shared = keyed(tenant, "Analytical Engine", entity_type="machine")
        store = await _store_with(subject, better_name, better_graph, shared)
        await store.upsert_relationships(
            [
                edge(tenant, source=subject.id, target=shared.id, kind="worked_on"),
                edge(tenant, source=better_graph.id, target=shared.id, kind="worked_on"),
            ]
        )

        by_name = await CandidateFinder(
            store, weights=FeatureWeights(name=10.0, embedding=0.0, graph=1.0)
        ).candidates(subject)
        by_graph = await CandidateFinder(
            store, weights=FeatureWeights(name=1.0, embedding=0.0, graph=10.0)
        ).candidates(subject)

        assert by_name[0].entity.id == better_name.id
        assert by_graph[0].entity.id == better_graph.id


class TestOrderingAndFiltering:
    async def test_ties_are_broken_by_entity_id_not_by_iteration(self):
        """Two identical names tie on every signal, which is the common case
        for duplicates. Without a tie-break, "the best candidate" would depend
        on how the store's dictionary happened to iterate."""
        tenant = uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        low = keyed(tenant, "Augusta King", entity_id=UUID("00000000-0000-4000-8000-00000000000a"))
        high = keyed(tenant, "Augusta King", entity_id=UUID("ffffffff-0000-4000-8000-00000000000f"))
        # Same type, so they block together with the subject.
        store = await _store_with(subject, low, high)

        found = await CandidateFinder(store, use_graph_signal=False).candidates(subject)

        assert [c.entity.id for c in found] == [low.id, high.id]

    async def test_a_minimum_score_filters(self):
        tenant = uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        close = keyed(tenant, "Ada Lovelace")
        distant = keyed(tenant, "Zebedee Quill")
        store = await _store_with(subject, close, distant)

        found = await CandidateFinder(store, use_graph_signal=False).candidates(
            subject, minimum_score=0.9
        )

        assert [c.entity.id for c in found] == [close.id]

    async def test_the_filter_is_inclusive_at_the_boundary(self):
        """A candidate scoring exactly the minimum is kept. Boundaries are
        where an off-by-one hides, and an exact name match with the graph
        signal off scores a round 1.0 every time."""
        tenant = uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        twin = keyed(tenant, "Ada Lovelace")
        store = await _store_with(subject, twin)

        found = await CandidateFinder(store, use_graph_signal=False).candidates(
            subject, minimum_score=1.0
        )

        assert [c.entity.id for c in found] == [twin.id]

    async def test_an_empty_tenant_yields_nothing(self):
        tenant = uuid4()
        subject = keyed(tenant, "Ada Lovelace")

        assert await CandidateFinder(InMemoryGraphStore()).candidates(subject) == []


class TestTheFinderNeverWrites:
    async def test_scoring_leaves_the_graph_exactly_as_it_was(self):
        """A finder that also merged would make "what would this merge?"
        unanswerable without merging."""
        tenant = uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        other = keyed(tenant, "Ada Lovelacx")
        store = await _store_with(subject, other)
        await store.upsert_relationship(
            edge(tenant, source=subject.id, target=other.id, kind="same_as")
        )
        before = sorted(
            (e.model_dump(mode="json") for e in await store.find_entities(tenant)), key=str
        )

        await CandidateFinder(store).candidates(subject)

        after = sorted(
            (e.model_dump(mode="json") for e in await store.find_entities(tenant)), key=str
        )
        assert after == before
        assert await store.find_aliases(subject.id, tenant) == []
