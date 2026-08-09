"""`FakeEmbeddingProvider` against the shared `EmbeddingProvider` contract.

The subclass is the whole opt-in. Everything below it is true of *this*
adapter and no other — which is the split `.claude/rules/testing.md` requires,
and the reason a case about hashing lives here rather than in the suite.
"""

from __future__ import annotations

import math

import pytest

from redstring.domain.exceptions import EmbeddingProviderError
from redstring.llm.adapters.fake_embedding import DEFAULT_DIMENSION, FakeEmbeddingProvider
from redstring.ports.embedding_provider import EmbeddingProvider
from redstring.testing.embedding_provider import EmbeddingProviderCompliance


class TestFakeEmbeddingProvider(EmbeddingProviderCompliance):
    @pytest.fixture
    def provider(self) -> EmbeddingProvider:
        return FakeEmbeddingProvider()


class TestFakeEmbeddingProviderSpecifics:
    def test_it_satisfies_the_port(self):
        assert isinstance(FakeEmbeddingProvider(), EmbeddingProvider)

    def test_the_default_dimension_is_realistic(self):
        """768, matching `nomic-embed-text`, and deliberately not 8.

        A fake defaulting to a small width invites a dimension check written
        with `is not`, which passes for every value CPython caches and rejects
        every real vector. CLAUDE.md records that exact defect; the default
        here is the cheapest way to stop it recurring.
        """
        assert DEFAULT_DIMENSION == 768
        assert FakeEmbeddingProvider().dimension == 768

    @pytest.mark.parametrize("dimension", [1, 3, 8, 384, 768, 1536])
    async def test_any_positive_dimension_is_honoured(self, dimension: int):
        result = await FakeEmbeddingProvider(dimension=dimension).embed(["Ada"])

        assert len(result[0]) == dimension

    @pytest.mark.parametrize("dimension", [0, -1])
    def test_a_non_positive_dimension_is_rejected(self, dimension: int):
        with pytest.raises(ValueError, match="dimension must be positive"):
            FakeEmbeddingProvider(dimension=dimension)

    async def test_vectors_are_unit_length(self):
        """Normalisation is what makes cosine scores mean anything.

        Without it every score is partly a function of vector magnitude, which
        for a hash is arbitrary — a caller comparing two scores would be
        reading noise that looks like signal.
        """
        result = await FakeEmbeddingProvider(dimension=64).embed(["Ada", "Babbage"])

        for vector in result:
            assert math.isclose(math.sqrt(sum(c * c for c in vector)), 1.0, rel_tol=1e-9)

    async def test_components_are_spread_around_zero(self):
        """Centred, not crowded into one octant.

        An uncentred hash gives all-positive components, so every pair of
        vectors has a high cosine and *everything* looks similar. A test using
        this fake to check "the right neighbour came back" would then pass
        regardless of which neighbour it was.
        """
        vector = (await FakeEmbeddingProvider(dimension=256).embed(["Ada"]))[0]

        assert any(c < 0 for c in vector), "no negative components"
        assert any(c > 0 for c in vector), "no positive components"

    async def test_determinism_holds_across_instances(self):
        """Same text, different provider object, same vector.

        Per-instance determinism would be enough for one test and useless
        across a suite — two tests building their own provider must agree, or
        a stored vector cannot be asserted against a freshly computed one.
        """
        first = await FakeEmbeddingProvider().embed(["Ada Lovelace"])
        second = await FakeEmbeddingProvider().embed(["Ada Lovelace"])

        assert first == second

    async def test_dimension_changes_the_vector_not_just_its_length(self):
        """A shorter vector is not a truncation of a longer one.

        Stated because the obvious implementation — hash once, slice to width
        — would make a 8-dimensional vector a prefix of a 768-dimensional one,
        and the two would be renormalised copies of the same direction. Nothing
        depends on this today; it is asserted so that the property is a
        decision rather than an accident of the current code.
        """
        short = (await FakeEmbeddingProvider(dimension=8).embed(["Ada"]))[0]
        long = (await FakeEmbeddingProvider(dimension=768).embed(["Ada"]))[0]

        assert short != long[:8]

    async def test_fail_on_raises_for_a_matching_text(self):
        """The hook exists so a caller can exercise their own error path."""
        provider = FakeEmbeddingProvider(fail_on="boom")

        with pytest.raises(EmbeddingProviderError, match="boom"):
            await provider.embed(["harmless", "this one goes boom"])

    async def test_fail_on_is_inert_when_nothing_matches(self):
        provider = FakeEmbeddingProvider(fail_on="boom")

        assert len(await provider.embed(["harmless", "also fine"])) == 2

    async def test_the_error_names_the_model(self):
        """`EmbeddingProviderError` carries `model` for the same reason
        `LlmProviderError` does: the failure is nearly always about which
        endpoint was reached."""
        provider = FakeEmbeddingProvider(model="fake/named", fail_on="x")

        with pytest.raises(EmbeddingProviderError) as caught:
            await provider.embed(["x"])

        assert caught.value.model == "fake/named"
        assert "fake/named" in str(caught.value)
