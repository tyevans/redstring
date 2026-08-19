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


class TestPrefixedFakeEmbeddingProvider(EmbeddingProviderCompliance):
    """The contract again with both prefixes set and different. See the
    equivalent class in `test_langchain_embedding_provider.py` for why an
    unprefixed fixture cannot stand in for this one."""

    @pytest.fixture
    def provider(self) -> EmbeddingProvider:
        return FakeEmbeddingProvider(
            document_prefix="search_document: ",
            query_prefix="search_query: ",
        )


class TestFakeTaskPrefixes:
    """A fake that accepted the prefixes and ignored them would be worse than
    one that refused them: a caller's test of their own pipeline would pass
    against wiring that never applies a prefix, which is exactly the silent
    failure the port method exists to prevent.

    Both prefixes are non-empty *and different* in every case below, for the
    reason CLAUDE.md's failure-shape table gives: with them equal, an
    implementation that swapped them is the same function.
    """

    async def test_the_two_sides_give_different_vectors_for_one_text(self):
        provider = FakeEmbeddingProvider(
            dimension=64,
            document_prefix="search_document: ",
            query_prefix="search_query: ",
        )

        [document] = await provider.embed(["Ada Lovelace"])
        [query] = await provider.embed_query(["Ada Lovelace"])

        assert document != query

    async def test_a_prefix_actually_prefixes_rather_than_merely_varying(self):
        """Pinned against the *unprefixed* provider, which is an oracle
        independent of the code under test.

        Asserting only that the two sides differ would pass for a fake that
        salted each side with its method name and dropped the caller's prefix
        entirely — the input on which a correct implementation and a useless
        one agree. Embedding the already-concatenated string through a plain
        provider is the only thing that says the prefix is the *text* it was
        given.
        """
        plain = FakeEmbeddingProvider(dimension=64)
        prefixed = FakeEmbeddingProvider(dimension=64, query_prefix="search_query: ")

        [expected] = await plain.embed(["search_query: Ada Lovelace"])
        [actual] = await prefixed.embed_query(["Ada Lovelace"])

        assert actual == expected

    async def test_the_document_prefix_is_the_one_embed_uses(self):
        """The swap case. Fails if `embed` reaches for `query_prefix`."""
        plain = FakeEmbeddingProvider(dimension=64)
        prefixed = FakeEmbeddingProvider(
            dimension=64,
            document_prefix="search_document: ",
            query_prefix="search_query: ",
        )

        [expected] = await plain.embed(["search_document: Ada Lovelace"])
        [actual] = await prefixed.embed(["Ada Lovelace"])

        assert actual == expected

    async def test_the_prefixes_default_to_empty(self):
        """No behaviour change for the many existing callers of this fake: the
        two sides are one function until a prefix is asked for."""
        provider = FakeEmbeddingProvider(dimension=64)

        assert await provider.embed(["Ada"]) == await provider.embed_query(["Ada"])

    async def test_fail_on_matches_the_callers_text_not_the_prefixed_one(self):
        """A caller asking to fail on `"boom"` should not have to know what
        this provider prepends -- and a `fail_on` matched against the prefixed
        string would also fire on every text once someone set
        `fail_on="search"`."""
        provider = FakeEmbeddingProvider(fail_on="boom", query_prefix="search_query: ")

        with pytest.raises(EmbeddingProviderError, match="boom"):
            await provider.embed_query(["this one goes boom"])

        assert len(await provider.embed_query(["harmless"])) == 1
