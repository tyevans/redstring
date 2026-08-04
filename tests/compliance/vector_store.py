"""Shared compliance suite for the `VectorStore` port.

**Every `VectorStore` adapter must pass this suite unchanged.** It is the
executable definition of the port; the prose in
`kg_builder.ports.vector_store` describes what these tests enforce.

## Consistency contract

Adapters are **read-your-writes**, exactly as `GraphStore` adapters are: once
an `upsert` has returned, its effect is visible to the next read on the same
store. There is no "eventually" inside a store. Lag belongs between the event
log and the projection, never here.

## Exactness contract, and how this suite stays honest about it

This is the hard part of testing a vector store, and it is handled here
deliberately rather than discovered later.

The in-memory adapter does exact brute-force kNN. A store over an
**approximate** index (ivfflat, hnsw, or any managed ANN service) does not:
it may omit a true neighbour, and the omission is a legitimate implementation
choice, not a bug. A suite that asserted exact ordering everywhere would pass
in-memory and be flaky-to-wrong against a real index, which is the worst
possible outcome -- an adapter that "passes compliance" while quietly
returning the wrong neighbours.

So the contract is stated in two tiers, and every test belongs to exactly one:

1. **Exact behaviour, on small datasets.** Tens of vectors, where every
   sensible backend falls back to a sequential scan and *is* exact. These
   assert exact membership, exact ordering and exact scores. `k` respected,
   filters applied before `k`, tie-break order, self-similarity: all here.
2. **Recall, on a larger dataset.** The honest weaker claim: the single true
   nearest neighbour appears somewhere in the returned top-k. Not its rank,
   not the rest of the list.

There is deliberately **no `is_approximate` capability flag.** A flag that
lets an adapter opt out of correctness tests is how adapters quietly stop
being interchangeable: the flag gets set once, for a good reason, and from
then on the suite is silent about the thing it was written to check. An
adapter that cannot pass tier 1 on ten vectors is not a `VectorStore`.

**Float precision.** `vector` in pgvector is float4, so the suite generates
float32-representable components (see `strategies.vectors`) and compares
scores with a tolerance rather than `==`. Exact equality on the *stored
vector* is still asserted, because float32-representable values survive a
float32 column unchanged.

## How an adapter opts in

Subclass and supply `new_store`::

    class TestMemoryVectorStore(VectorStoreCompliance):
        async def new_store(self) -> VectorStore:
            return InMemoryVectorStore(dimension=self.DIMENSION)

`new_store` must return an **empty** store of dimension `self.DIMENSION`, and
each call must return one isolated from every other. The property tests call
it once per generated example, because hypothesis reuses the surrounding
fixture across examples and a shared store would let state from example *n*
decide example *n+1*.

## If you add a read method to the port, add its isolation test here

Every method handing back an object a caller can mutate needs a test that
mutates the result and asserts a later read is unaffected -- in the same edit
that adds the method. `tests/unit/vector/test_compliance_coverage.py` enforces
it by introspecting the Protocol, so this is a gate rather than advice. Slice
3 learned this four times over: behavioural tests cannot catch it, because
handing back the stored object is correct on every read and wrong only
afterwards.
"""

from __future__ import annotations

import math
import os
import random
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from kg_builder.domain.exceptions import DimensionMismatchError
from kg_builder.domain.vector import VectorRecord, cosine_score
from kg_builder.ports.vector_store import VectorStore
from tests.compliance import strategies as gen

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from kg_builder.domain.vector import VectorMatch

#: Hypothesis examples per property test. Per *run*, via the environment --
#: not per adapter subclass; see BACKLOG B10h for why that is not achievable
#: as the shared `settings()` is written, and what would have to change.
#:
#:     KG_COMPLIANCE_MAX_EXAMPLES=10 uv run pytest -m integration
DEFAULT_MAX_EXAMPLES = int(os.environ.get("KG_COMPLIANCE_MAX_EXAMPLES", "50"))

compliance_settings = settings(
    deadline=None,
    max_examples=DEFAULT_MAX_EXAMPLES,
    suppress_health_check=[HealthCheck.too_slow],
)

#: Score comparisons run through float32 storage and, in a database adapter,
#: float32 arithmetic too. Relative error there is around 1e-7, so this is
#: roughly an order of magnitude of headroom and still far tighter than any
#: ranking mistake.
SCORE_TOLERANCE = 1e-5


def _mutate_record(record: VectorRecord) -> None:
    """Mutate a record in place, reaching into its nested containers."""
    record.vector[0] = record.vector[0] + 12345.0
    record.vector.append(999.0)
    record.metadata["__tampered__"] = True
    for value in record.metadata.values():
        if isinstance(value, dict):
            value["__nested_tamper__"] = True
        elif isinstance(value, list):
            value.append("__nested_tamper__")


def _mutate_match(match: VectorMatch) -> None:
    match.metadata["__tampered__"] = True
    for value in match.metadata.values():
        if isinstance(value, dict):
            value["__nested_tamper__"] = True
        elif isinstance(value, list):
            value.append("__nested_tamper__")


class VectorStoreCompliance:
    """Tests every `VectorStore` implementation must pass."""

    #: Small on purpose. The properties are about the store, not about the
    #: embedding model, and a small dimension keeps a 200-vector recall test
    #: cheap enough to run against a real database per hypothesis example.
    DIMENSION = 8

    async def new_store(self) -> VectorStore:
        """Return a fresh, empty store of `self.DIMENSION`. Adapters override."""
        raise NotImplementedError

    async def dispose(self, store: VectorStore) -> None:
        """Release whatever `new_store` acquired. No-op by default."""

    @asynccontextmanager
    async def _store(self) -> AsyncIterator[VectorStore]:
        """A store for the duration of one example, disposed afterwards."""
        store = await self.new_store()
        try:
            yield store
        finally:
            await self.dispose(store)

    @pytest.fixture
    async def store(self) -> AsyncIterator[VectorStore]:
        async with self._store() as store:
            yield store

    def _vectors(self) -> st.SearchStrategy[list[float]]:
        return gen.vectors(self.DIMENSION)

    # ------------------------------------------------------------------
    # The port itself
    # ------------------------------------------------------------------

    async def test_satisfies_the_vector_store_protocol(self, store: VectorStore) -> None:
        assert isinstance(store, VectorStore)

    async def test_reports_the_dimension_it_was_built_with(self, store: VectorStore) -> None:
        assert store.dimension == self.DIMENSION

    # ------------------------------------------------------------------
    # Property 1 -- round-trip
    # ------------------------------------------------------------------

    @compliance_settings
    @given(data=st.data())
    async def test_upsert_then_get_round_trips(self, data: st.DataObject) -> None:
        async with self._store() as store:
            entity_id, tenant_id = data.draw(st.uuids()), data.draw(st.uuids())
            vector = data.draw(self._vectors())
            metadata = data.draw(gen.property_dicts)

            await store.upsert(entity_id, vector, tenant_id, metadata=metadata)

            found = await store.get(entity_id, tenant_id)
            assert found == VectorRecord(
                entity_id=entity_id, tenant_id=tenant_id, vector=vector, metadata=metadata
            )

    @compliance_settings
    @given(data=st.data())
    async def test_upsert_many_round_trips(self, data: st.DataObject) -> None:
        async with self._store() as store:
            record = VectorRecord(
                entity_id=data.draw(st.uuids()),
                tenant_id=data.draw(st.uuids()),
                vector=data.draw(self._vectors()),
                metadata=data.draw(gen.property_dicts),
            )

            await store.upsert_many([record])

            assert await store.get(record.entity_id, record.tenant_id) == record

    async def test_upsert_defaults_metadata_to_empty(self, store: VectorStore) -> None:
        entity_id, tenant = uuid4(), uuid4()
        await store.upsert(entity_id, self._unit(0), tenant)

        found = await store.get(entity_id, tenant)
        assert found is not None
        assert found.metadata == {}

    async def test_upsert_many_with_no_items_is_not_an_error(self, store: VectorStore) -> None:
        await store.upsert_many([])

    async def test_get_returns_none_for_an_unknown_id(self, store: VectorStore) -> None:
        assert await store.get(uuid4(), uuid4()) is None

    # ------------------------------------------------------------------
    # Property 2 -- idempotency and last-write-wins
    # ------------------------------------------------------------------

    @compliance_settings
    @given(data=st.data())
    async def test_upserting_twice_leaves_one_record_holding_the_later_value(
        self, data: st.DataObject
    ) -> None:
        async with self._store() as store:
            entity_id, tenant = data.draw(st.uuids()), data.draw(st.uuids())
            first, second = data.draw(self._vectors()), data.draw(self._vectors())
            metadata = data.draw(gen.property_dicts)

            await store.upsert(entity_id, first, tenant, metadata={"first": True})
            await store.upsert(entity_id, second, tenant, metadata=metadata)

            found = await store.get(entity_id, tenant)
            assert found is not None
            assert found.vector == second
            # Wholesale replacement, not a merge: a key removed by a later
            # event must not survive, or replay stops being deterministic.
            assert found.metadata == metadata
            assert len(await store.search(second, tenant, k=10)) == 1

    async def test_a_repeated_key_within_one_batch_takes_the_later_value(
        self, store: VectorStore
    ) -> None:
        """`upsert_many` sends one statement, so the batch must be deduplicated.

        A database upsert cannot touch the same row twice in one statement --
        Postgres raises outright -- so the adapter has to collapse duplicates
        itself, and the rule must be the same last-write-wins one that applies
        across calls.
        """
        entity_id, tenant = uuid4(), uuid4()
        await store.upsert_many(
            [
                VectorRecord(
                    entity_id=entity_id, tenant_id=tenant, vector=self._unit(0), metadata={"n": 1}
                ),
                VectorRecord(
                    entity_id=entity_id, tenant_id=tenant, vector=self._unit(1), metadata={"n": 2}
                ),
            ]
        )

        found = await store.get(entity_id, tenant)
        assert found is not None
        assert found.vector == self._unit(1)
        assert found.metadata == {"n": 2}

    async def test_the_key_is_the_pair_and_neither_component_alone(
        self, store: VectorStore
    ) -> None:
        """`(tenant_id, entity_id)` is one key, and the pair is **ordered**.

        Three arrangements, all in one batch so the adapter's deduplication
        sees them together:

        - one entity id under two tenants,
        - two entity ids under one tenant,
        - and the pair `(x, y)` alongside the pair `(y, x)` -- the same two
          UUIDs swapped between the components.

        The third is the one that costs something to get wrong and nothing to
        get right by accident. An adapter keyed on an unordered pair, on a set
        of both values, or on `hash(a) ^ hash(b)` answers every ordinary test
        correctly and collapses these two records into one. When a key is a
        tuple, the test has to make its components collide.
        """
        x, y = uuid4(), uuid4()
        shared_entity, other_entity = uuid4(), uuid4()
        tenant = uuid4()
        records = [
            VectorRecord(entity_id=shared_entity, tenant_id=x, vector=self._unit(0)),
            VectorRecord(entity_id=shared_entity, tenant_id=y, vector=self._unit(1)),
            VectorRecord(entity_id=other_entity, tenant_id=tenant, vector=self._unit(2)),
            VectorRecord(entity_id=shared_entity, tenant_id=tenant, vector=self._unit(3)),
            # The components swapped: (x, y) and (y, x) are different rows.
            VectorRecord(entity_id=y, tenant_id=x, vector=self._unit(4)),
            VectorRecord(entity_id=x, tenant_id=y, vector=self._unit(5)),
        ]

        await store.upsert_many(records)

        for record in records:
            found = await store.get(record.entity_id, record.tenant_id)
            assert found is not None, f"lost {record.entity_id} under tenant {record.tenant_id}"
            assert found.vector == record.vector

    # ------------------------------------------------------------------
    # Property 3 -- tenant isolation (the one that matters most)
    # ------------------------------------------------------------------

    @compliance_settings
    @given(tenants=gen.distinct_tenant_pairs, data=st.data())
    async def test_get_never_crosses_tenants(
        self, tenants: tuple[UUID, UUID], data: st.DataObject
    ) -> None:
        """The **same entity ids** under two tenants, so a leak cannot hide
        behind ids that differ anyway."""
        tenant_a, tenant_b = tenants
        async with self._store() as store:
            entity_id = data.draw(st.uuids())
            a_vector, b_vector = data.draw(self._vectors()), data.draw(self._vectors())
            await store.upsert(entity_id, a_vector, tenant_a, metadata={"tenant": "a"})
            await store.upsert(entity_id, b_vector, tenant_b, metadata={"tenant": "b"})

            under_a = await store.get(entity_id, tenant_a)
            under_b = await store.get(entity_id, tenant_b)
            assert under_a is not None
            assert under_b is not None
            assert under_a.vector == a_vector
            assert under_a.metadata == {"tenant": "a"}
            assert under_b.vector == b_vector
            assert under_b.metadata == {"tenant": "b"}

    @compliance_settings
    @given(tenants=gen.distinct_tenant_pairs, data=st.data())
    async def test_search_never_crosses_tenants(
        self, tenants: tuple[UUID, UUID], data: st.DataObject
    ) -> None:
        tenant_a, tenant_b = tenants
        async with self._store() as store:
            entity_id = data.draw(st.uuids())
            vector = data.draw(self._vectors())
            await store.upsert(entity_id, vector, tenant_a, metadata={"tenant": "a"})

            # Searching B with A's own vector is the strongest form of the
            # question: a leaking store returns a perfect match.
            assert await store.search(vector, tenant_b, k=10) == []

            b_only = data.draw(st.uuids())
            await store.upsert(b_only, data.draw(self._vectors()), tenant_b)
            found = await store.search(vector, tenant_b, k=10)
            assert [match.entity_id for match in found] == [b_only]

    async def test_delete_never_crosses_tenants(self, store: VectorStore) -> None:
        entity_id, tenant, other = uuid4(), uuid4(), uuid4()
        await store.upsert(entity_id, self._unit(0), tenant)

        assert await store.delete(entity_id, other) is False
        assert await store.get(entity_id, tenant) is not None

    # ------------------------------------------------------------------
    # Property 4 -- dimension rejection
    # ------------------------------------------------------------------

    @compliance_settings
    @given(length=st.integers(min_value=0, max_value=32))
    async def test_upsert_rejects_a_vector_of_the_wrong_length(self, length: int) -> None:
        # `assume` rather than a strategy filter, because the value to exclude
        # is `self.DIMENSION` and a decorator on the class body cannot see it.
        assume(length != self.DIMENSION)
        async with self._store() as store:
            wrong = [1.0] * length

            with pytest.raises(DimensionMismatchError) as raised:
                await store.upsert(uuid4(), wrong, uuid4())
            assert raised.value.expected == self.DIMENSION
            assert raised.value.actual == length

    @compliance_settings
    @given(length=st.integers(min_value=0, max_value=32))
    async def test_search_rejects_a_vector_of_the_wrong_length(self, length: int) -> None:
        assume(length != self.DIMENSION)
        async with self._store() as store:
            with pytest.raises(DimensionMismatchError):
                await store.search([1.0] * length, uuid4())

    async def test_upsert_many_rejects_a_vector_of_the_wrong_length(
        self, store: VectorStore
    ) -> None:
        good = VectorRecord(entity_id=uuid4(), tenant_id=uuid4(), vector=self._unit(0))
        bad = VectorRecord(entity_id=uuid4(), tenant_id=uuid4(), vector=[1.0, 2.0])

        with pytest.raises(DimensionMismatchError):
            await store.upsert_many([good, bad])

    async def test_a_rejected_write_leaves_no_trace(self, store: VectorStore) -> None:
        """Validation happens before the write, not alongside it."""
        entity_id, tenant = uuid4(), uuid4()

        with pytest.raises(DimensionMismatchError):
            await store.upsert(entity_id, [1.0, 2.0], tenant)

        assert await store.get(entity_id, tenant) is None

    async def test_zero_vectors_are_rejected(self, store: VectorStore) -> None:
        """Cosine is undefined at the origin; every backend disagrees on how."""
        zeroes = [0.0] * self.DIMENSION

        with pytest.raises(ValueError, match="zero"):
            await store.upsert(uuid4(), zeroes, uuid4())
        with pytest.raises(ValueError, match="zero"):
            await store.search(zeroes, uuid4())
        with pytest.raises(ValueError, match="zero"):
            await store.upsert_many(
                [VectorRecord(entity_id=uuid4(), tenant_id=uuid4(), vector=zeroes)]
            )

    # ------------------------------------------------------------------
    # Property 5 -- delete and delete_by_tenant are exact
    # ------------------------------------------------------------------

    async def test_delete_reports_whether_it_removed_anything(self, store: VectorStore) -> None:
        entity_id, tenant = uuid4(), uuid4()
        await store.upsert(entity_id, self._unit(0), tenant)

        assert await store.delete(entity_id, tenant) is True
        assert await store.get(entity_id, tenant) is None
        # Idempotent: replaying a delete removes nothing and is not an error.
        assert await store.delete(entity_id, tenant) is False
        assert await store.delete(uuid4(), tenant) is False

    async def test_delete_removes_only_that_record(self, store: VectorStore) -> None:
        tenant = uuid4()
        doomed, spared = uuid4(), uuid4()
        await store.upsert(doomed, self._unit(0), tenant)
        await store.upsert(spared, self._unit(1), tenant)

        await store.delete(doomed, tenant)

        assert await store.get(spared, tenant) is not None
        assert [m.entity_id for m in await store.search(self._unit(0), tenant, k=10)] == [spared]

    @compliance_settings
    @given(tenants=gen.distinct_tenant_pairs, data=st.data())
    async def test_delete_by_tenant_removes_exactly_that_tenant(
        self, tenants: tuple[UUID, UUID], data: st.DataObject
    ) -> None:
        doomed, spared = tenants
        async with self._store() as store:
            doomed_ids = data.draw(st.lists(st.uuids(), min_size=1, max_size=4, unique=True))
            spared_ids = data.draw(st.lists(st.uuids(), min_size=1, max_size=4, unique=True))
            for entity_id in doomed_ids:
                await store.upsert(entity_id, data.draw(self._vectors()), doomed)
            for entity_id in spared_ids:
                await store.upsert(entity_id, data.draw(self._vectors()), spared)

            removed = await store.delete_by_tenant(doomed)

            assert removed == len(doomed_ids)
            for entity_id in doomed_ids:
                assert await store.get(entity_id, doomed) is None
            assert await store.search(self._unit(0), doomed, k=100) == []
            for entity_id in spared_ids:
                assert await store.get(entity_id, spared) is not None
            assert len(await store.search(self._unit(0), spared, k=100)) == len(spared_ids)

    @compliance_settings
    @given(tenant=st.uuids())
    async def test_delete_by_tenant_on_an_unknown_tenant_removes_nothing(
        self, tenant: UUID
    ) -> None:
        async with self._store() as store:
            assert await store.delete_by_tenant(tenant) == 0

    # ------------------------------------------------------------------
    # Property 6 -- self-similarity
    #
    # The cheapest test that catches a distance/similarity inversion: an
    # adapter reporting a distance ranks the *least* similar record first and
    # scores an identical vector at 0.0 rather than 1.0.
    # ------------------------------------------------------------------

    @compliance_settings
    @given(data=st.data())
    async def test_a_vector_matches_itself_best_and_at_one(self, data: st.DataObject) -> None:
        async with self._store() as store:
            tenant = data.draw(st.uuids())
            entity_id = data.draw(st.uuids())
            vector = data.draw(self._vectors())
            await store.upsert(entity_id, vector, tenant)
            # The exact opposite direction: the worst possible score, 0.0. Its
            # presence is what makes "highest" mean something -- with one
            # record in the store, first place is unearned.
            await store.upsert(data.draw(st.uuids()), [-v for v in vector], tenant)

            found = await store.search(vector, tenant, k=2)

            assert found[0].entity_id == entity_id
            assert found[0].score == pytest.approx(1.0, abs=SCORE_TOLERANCE)
            assert found[1].score == pytest.approx(0.0, abs=SCORE_TOLERANCE)

    async def test_the_score_of_an_identical_vector_does_not_exceed_one(
        self, store: VectorStore
    ) -> None:
        """The boundary, pinned.

        Accumulated rounding makes a float vector's dot product with itself
        land marginally above its squared norm, so the unclamped score for an
        identical pair can exceed 1.0 -- which `VectorMatch` rejects outright.
        Slice 0 hit this in the previous `cosine_similarity`. The components
        below are deliberately values whose squares do not sum exactly.
        """
        tenant = uuid4()
        awkward = [0.1, 0.2, 0.3, 0.7, 0.9, 1.1, 1.3, 1.7][: self.DIMENSION]
        await store.upsert(uuid4(), awkward, tenant)

        (match,) = await store.search(awkward, tenant, k=1)
        assert match.score <= 1.0
        assert match.score == pytest.approx(1.0, abs=SCORE_TOLERANCE)

    async def test_scores_agree_with_the_domain_score_function(self, store: VectorStore) -> None:
        """Every adapter reports the *same number*, not merely the same order.

        Ranking is invariant under any monotone transform of cosine, so an
        adapter reporting raw cosine, or squared euclidean distance on
        normalised vectors, orders results identically and passes every
        ordering test here. Only comparing against the pinned scale catches
        it -- and it has to be caught, because `min_score` is a number the
        caller carries between adapters.
        """
        tenant = uuid4()
        query = self._unit(0)
        others = {
            uuid4(): self._unit(0),  # identical      -> 1.0
            uuid4(): self._unit(1),  # orthogonal     -> 0.5
            uuid4(): [-v for v in self._unit(0)],  # opposite -> 0.0
            uuid4(): [1.0, 1.0, *([0.0] * (self.DIMENSION - 2))],  # 45 degrees
        }
        for entity_id, vector in others.items():
            await store.upsert(entity_id, vector, tenant)

        for match in await store.search(query, tenant, k=10):
            assert match.score == pytest.approx(
                cosine_score(query, others[match.entity_id]), abs=SCORE_TOLERANCE
            )

    # ------------------------------------------------------------------
    # Property 7 -- k is respected
    # ------------------------------------------------------------------

    @compliance_settings
    @given(k=st.integers(min_value=0, max_value=12), data=st.data())
    async def test_search_never_returns_more_than_k(self, k: int, data: st.DataObject) -> None:
        async with self._store() as store:
            tenant = data.draw(st.uuids())
            held = data.draw(st.integers(min_value=0, max_value=8))
            for _ in range(held):
                await store.upsert(uuid4(), data.draw(self._vectors()), tenant)

            found = await store.search(data.draw(self._vectors()), tenant, k=k)

            assert len(found) == min(k, held)

    async def test_search_defaults_to_ten_results(self, store: VectorStore) -> None:
        """The default is pinned, not merely "some small number"."""
        tenant = uuid4()
        for index in range(12):
            await store.upsert(uuid4(), self._spread(index), tenant)

        assert len(await store.search(self._unit(0), tenant)) == 10

    async def test_a_negative_k_is_rejected(self, store: VectorStore) -> None:
        with pytest.raises(ValueError, match="k"):
            await store.search(self._unit(0), uuid4(), k=-1)

    async def test_search_on_an_empty_tenant_is_empty(self, store: VectorStore) -> None:
        assert await store.search(self._unit(0), uuid4()) == []

    # ------------------------------------------------------------------
    # Property 8 -- mutation isolation
    # ------------------------------------------------------------------

    @compliance_settings
    @given(data=st.data())
    async def test_get_returns_copies(self, data: st.DataObject) -> None:
        async with self._store() as store:
            entity_id, tenant = data.draw(st.uuids()), data.draw(st.uuids())
            vector = data.draw(self._vectors())
            metadata = data.draw(gen.property_dicts)
            await store.upsert(entity_id, vector, tenant, metadata=metadata)
            pristine = VectorRecord(
                entity_id=entity_id, tenant_id=tenant, vector=vector, metadata=metadata
            ).model_copy(deep=True)

            first = await store.get(entity_id, tenant)
            assert first is not None
            _mutate_record(first)

            assert await store.get(entity_id, tenant) == pristine

    @compliance_settings
    @given(data=st.data())
    async def test_search_returns_copies(self, data: st.DataObject) -> None:
        async with self._store() as store:
            entity_id, tenant = data.draw(st.uuids()), data.draw(st.uuids())
            vector = data.draw(self._vectors())
            metadata = data.draw(gen.property_dicts)
            await store.upsert(entity_id, vector, tenant, metadata=metadata)
            pristine = dict(metadata)

            for match in await store.search(vector, tenant, k=1):
                _mutate_match(match)

            found = await store.get(entity_id, tenant)
            assert found is not None
            assert found.metadata == pristine
            assert (await store.search(vector, tenant, k=1))[0].metadata == pristine

    async def test_mutating_the_argument_after_a_write_does_not_change_the_store(
        self, store: VectorStore
    ) -> None:
        """The other direction: the store must not keep the caller's objects."""
        entity_id, tenant = uuid4(), uuid4()
        vector = self._unit(0)
        metadata: dict[str, Any] = {"nested": {"k": "v"}}
        await store.upsert(entity_id, vector, tenant, metadata=metadata)

        vector[0] = 999.0
        metadata["nested"]["k"] = "tampered"
        metadata["added"] = True

        found = await store.get(entity_id, tenant)
        assert found is not None
        assert found.vector == self._unit(0)
        assert found.metadata == {"nested": {"k": "v"}}

    async def test_mutating_a_batch_argument_after_a_write_does_not_change_the_store(
        self, store: VectorStore
    ) -> None:
        record = VectorRecord(
            entity_id=uuid4(),
            tenant_id=uuid4(),
            vector=self._unit(0),
            metadata={"nested": {"k": "v"}},
        )
        await store.upsert_many([record])
        pristine = record.model_copy(deep=True)

        _mutate_record(record)

        assert await store.get(pristine.entity_id, pristine.tenant_id) == pristine

    # ------------------------------------------------------------------
    # Tier 1 -- exact behaviour on a small dataset
    #
    # Small enough that every backend scans sequentially and is exact, so
    # these may assert exact membership, ordering and scores. See the module
    # docstring on the two tiers.
    # ------------------------------------------------------------------

    async def test_search_returns_the_exact_top_k_in_order(self, store: VectorStore) -> None:
        """Ten well-separated vectors; the answer is not a matter of opinion.

        The angles are chosen so consecutive scores differ by far more than
        `SCORE_TOLERANCE`, which is what makes exact ordering a fair thing to
        demand of an adapter storing float32.
        """
        tenant = uuid4()
        query = [1.0, 0.0, *([0.0] * (self.DIMENSION - 2))]
        # Increasing angle from the query, so `ids[i]` is the i-th nearest.
        ids = [uuid4() for _ in range(10)]
        for rank, entity_id in enumerate(ids):
            angle = rank * (math.pi / 12)
            await store.upsert(
                entity_id,
                [math.cos(angle), math.sin(angle), *([0.0] * (self.DIMENSION - 2))],
                tenant,
            )

        found = await store.search(query, tenant, k=4)

        assert [match.entity_id for match in found] == ids[:4]
        assert [match.score for match in found] == sorted(
            (match.score for match in found), reverse=True
        )

    async def test_search_orders_ties_by_entity_id(self, store: VectorStore) -> None:
        """`k` cutting through a tie must cut the same way on every adapter.

        All four vectors are the *same*, so score decides nothing and the
        tie-break is the only thing that can. Without a defined one, two
        adapters return different members and no test above would notice.
        """
        tenant = uuid4()
        ids = sorted((uuid4() for _ in range(4)), key=str)
        for entity_id in ids:
            await store.upsert(entity_id, self._unit(0), tenant)

        found = await store.search(self._unit(0), tenant, k=2)

        assert [match.entity_id for match in found] == ids[:2]

    async def test_entity_types_filters_by_metadata(self, store: VectorStore) -> None:
        tenant = uuid4()
        person, place, untyped = uuid4(), uuid4(), uuid4()
        await store.upsert(person, self._unit(0), tenant, metadata={"entity_type": "person"})
        await store.upsert(place, self._unit(0), tenant, metadata={"entity_type": "place"})
        await store.upsert(untyped, self._unit(0), tenant, metadata={"other": "person"})

        by_type = await store.search(self._unit(0), tenant, k=10, entity_types=["person"])
        assert [match.entity_id for match in by_type] == [person]

        # `[]` matches nothing; `None` filters not at all. A store treating an
        # empty sequence as "no filter" -- the natural `if entity_types:` bug --
        # returns all three here.
        assert await store.search(self._unit(0), tenant, k=10, entity_types=[]) == []
        assert len(await store.search(self._unit(0), tenant, k=10, entity_types=None)) == 3

        both = await store.search(self._unit(0), tenant, k=10, entity_types=["person", "place"])
        assert {match.entity_id for match in both} == {person, place}

    async def test_entity_types_compares_by_value(self, store: VectorStore) -> None:
        """A runtime-built string, not a literal.

        Every other test here passes literals, which CPython interns, so an
        adapter comparing with `is` passes them all and returns nothing for a
        caller whose type name came out of a config file.
        """
        tenant, entity_id = uuid4(), uuid4()
        await store.upsert(entity_id, self._unit(0), tenant, metadata={"entity_type": "plot_point"})

        built = "_".join(["plot", "point"])
        interned = "plot_point"
        assert built == interned
        assert built is not interned  # equal in value, distinct as an object
        found = await store.search(self._unit(0), tenant, k=10, entity_types=[built])
        assert [match.entity_id for match in found] == [entity_id]

    async def test_min_score_drops_results_below_it(self, store: VectorStore) -> None:
        tenant = uuid4()
        identical, orthogonal, opposite = uuid4(), uuid4(), uuid4()
        query = self._unit(0)
        await store.upsert(identical, query, tenant)  # score 1.0
        await store.upsert(orthogonal, self._unit(1), tenant)  # score 0.5
        await store.upsert(opposite, [-v for v in query], tenant)  # score 0.0

        assert {m.entity_id for m in await store.search(query, tenant, k=10, min_score=0.25)} == {
            identical,
            orthogonal,
        }
        # Inclusive at the boundary: "strictly below" is dropped, so a record
        # scoring exactly `min_score` survives.
        assert {m.entity_id for m in await store.search(query, tenant, k=10, min_score=0.5)} == {
            identical,
            orthogonal,
        }
        assert [m.entity_id for m in await store.search(query, tenant, k=10, min_score=0.75)] == [
            identical
        ]
        # 0.0 is not a no-op: it is a real threshold that keeps everything.
        assert len(await store.search(query, tenant, k=10, min_score=0.0)) == 3

    async def test_filters_are_applied_before_k(self, store: VectorStore) -> None:
        """The failure mode that looks like a small corpus and is a bug.

        Six records are nearer to the query than the two that match the
        filter. A store that takes the `k` nearest and filters *afterwards*
        returns nothing at all here, with no error and no clue -- which is
        indistinguishable from a tenant that genuinely holds no `person`.

        The same shape is tested for `min_score`, because the two filters are
        usually implemented in different places.
        """
        tenant = uuid4()
        query = [1.0, 0.0, *([0.0] * (self.DIMENSION - 2))]
        for rank in range(6):
            angle = (rank + 1) * (math.pi / 40)
            await store.upsert(
                uuid4(),
                [math.cos(angle), math.sin(angle), *([0.0] * (self.DIMENSION - 2))],
                tenant,
                metadata={"entity_type": "place"},
            )
        wanted = [uuid4(), uuid4()]
        for offset, entity_id in enumerate(wanted):
            angle = (offset + 10) * (math.pi / 40)
            await store.upsert(
                entity_id,
                [math.cos(angle), math.sin(angle), *([0.0] * (self.DIMENSION - 2))],
                tenant,
                metadata={"entity_type": "person"},
            )

        by_type = await store.search(query, tenant, k=2, entity_types=["person"])
        assert [match.entity_id for match in by_type] == wanted

        # `min_score` deliberately has no equivalent test, and the reason is
        # worth writing down rather than rediscovering: it is monotone in the
        # score, so it can only ever remove a *suffix* of the ranking. "Filter
        # then take k" and "take k then filter" therefore agree on it, and any
        # test claiming to tell them apart would be testing nothing. The
        # ordering hazard belongs to filters like `entity_types`, which cut
        # anywhere in the list.

    async def test_search_returns_the_stored_metadata(self, store: VectorStore) -> None:
        tenant, entity_id = uuid4(), uuid4()
        metadata: dict[str, Any] = {"entity_type": "person", "nested": {"k": [1, "two", None]}}
        await store.upsert(entity_id, self._unit(0), tenant, metadata=metadata)

        (match,) = await store.search(self._unit(0), tenant, k=1)
        assert match.metadata == metadata

    # ------------------------------------------------------------------
    # Tier 2 -- recall on a larger dataset
    #
    # The honest weaker contract. An approximate index may reorder the middle
    # of the list or drop a mid-ranked neighbour; what it may not do is lose
    # the *true* nearest one.
    # ------------------------------------------------------------------

    async def test_the_true_nearest_neighbour_is_within_the_top_k(self, store: VectorStore) -> None:
        """200 vectors -- past the point where an index would engage.

        Deterministically seeded rather than generated by hypothesis: the
        claim is about recall over a realistic corpus, and a shrinking search
        over 200-vector corpora costs a great deal to learn nothing. A fixed
        seed also means a failure is reproducible without a counterexample
        database, which matters most for the adapter that needs a container.
        """
        tenant = uuid4()
        rng = random.Random(20260803)
        corpus = {
            uuid4(): [rng.uniform(-1.0, 1.0) for _ in range(self.DIMENSION)] for _ in range(200)
        }
        query = [rng.uniform(-1.0, 1.0) for _ in range(self.DIMENSION)]
        await store.upsert_many(
            [
                VectorRecord(entity_id=entity_id, tenant_id=tenant, vector=vector)
                for entity_id, vector in corpus.items()
            ]
        )

        expected = max(corpus, key=lambda entity_id: cosine_score(query, corpus[entity_id]))

        found = await store.search(query, tenant, k=10)

        assert len(found) == 10
        assert expected in {match.entity_id for match in found}, (
            "the true nearest neighbour is missing from the top 10: recall has "
            "fallen below what the port promises"
        )
        assert [m.score for m in found] == sorted((m.score for m in found), reverse=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _unit(self, axis: int) -> list[float]:
        """The basis vector along `axis`. Exactly representable in float32."""
        return [1.0 if index == axis % self.DIMENSION else 0.0 for index in range(self.DIMENSION)]

    def _spread(self, index: int) -> list[float]:
        """A distinct non-zero vector per `index`, all pairwise non-parallel."""
        vector = self._unit(0)
        vector[1 + index % (self.DIMENSION - 1)] = float(index + 1)
        return vector
