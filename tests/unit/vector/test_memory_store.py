"""The in-memory `VectorStore` adapter against the shared compliance suite.

Everything asserted about behaviour lives in `tests.compliance.vector_store`.
This module supplies a store and adds only what is specific to *this*
adapter -- namely that it holds no state outside itself.
"""

from uuid import uuid4

import pytest

from kg_builder.domain.vector import VectorRecord
from kg_builder.ports.vector_store import VectorStore
from kg_builder.vector.adapters.memory import InMemoryVectorStore
from tests.compliance.vector_store import VectorStoreCompliance


class TestMemoryVectorStore(VectorStoreCompliance):
    async def new_store(self) -> VectorStore:
        return InMemoryVectorStore(dimension=self.DIMENSION)


class TestMemoryVectorStoreSpecifics:
    async def test_two_stores_share_nothing(self):
        one, two = InMemoryVectorStore(dimension=3), InMemoryVectorStore(dimension=3)
        entity_id, tenant = uuid4(), uuid4()
        await one.upsert(entity_id, [1.0, 0.0, 0.0], tenant)

        assert await two.get(entity_id, tenant) is None
        assert await two.search([1.0, 0.0, 0.0], tenant) == []

    async def test_dimension_must_be_positive(self):
        """A zero-dimension store accepts only the zero-length vector, which is
        also a zero vector, so nothing can ever be written to it."""
        for bad in (0, -1):
            with pytest.raises(ValueError, match="dimension"):
                InMemoryVectorStore(dimension=bad)

    async def test_upsert_many_is_not_a_loop_over_upsert_for_validation(self):
        """A bad element rejects the whole batch before anything is written.

        The port permits partial writes on failure for `GraphStore`; here the
        batch is validated up front, so a caller retrying after a
        `DimensionMismatchError` never sees a half-applied batch. The
        compliance suite pins that a rejected single write leaves no trace;
        this pins the batch form for the reference adapter specifically.
        """
        store = InMemoryVectorStore(dimension=3)
        tenant = uuid4()
        good = VectorRecord(entity_id=uuid4(), tenant_id=tenant, vector=[1.0, 0.0, 0.0])
        bad = VectorRecord(entity_id=uuid4(), tenant_id=tenant, vector=[1.0])

        with pytest.raises(Exception, match="dimension"):
            await store.upsert_many([good, bad])

        assert await store.get(good.entity_id, tenant) is None


class TestDimensionIsComparedByValue:
    """Two boundaries that a suite fixed at `DIMENSION = 8` cannot see.

    Both were found by cosmic-ray, and the first is the interned-small-int
    version of the trap `CLAUDE.md` tabulates: replacing `!=` with `is not` in
    the length check **survived every test**, because CPython caches integers
    up to 256 and the compliance suite's dimension is 8. At 768 -- the
    dimension of `nomic-embed-text`, the model this library is moving to --
    `len(vector) is not 768` is true for a vector of exactly the right length,
    so the store would reject every legitimate write. The test therefore uses
    a dimension above the cache.
    """

    async def test_a_correct_length_is_accepted_at_a_realistic_dimension(self):
        store = InMemoryVectorStore(dimension=768)
        entity_id, tenant = uuid4(), uuid4()
        vector = [0.0] * 767 + [1.0]

        await store.upsert(entity_id, vector, tenant)

        assert (await store.get(entity_id, tenant)).vector == vector

    async def test_a_dimension_of_one_is_legal(self):
        """Degenerate but permitted: the port says positive, not "more than
        one". Pinning the boundary stops it drifting to `<= 1` unnoticed."""
        assert InMemoryVectorStore(dimension=1).dimension == 1
