"""The `EmbeddingProvider` contract, asserted identically for every adapter.

Not collected: no `test_*.py` name and no `Test*` class, so pytest walks past
it. An adapter opts in by subclassing `EmbeddingProviderCompliance` under a
`Test*` name and supplying a `provider` fixture.

## What this suite is for

The failure it exists to catch is **positional**: `embed` promises one vector
per input, in input order. An adapter that batches internally, retries a
partial failure, or deduplicates identical texts can return fewer vectors or
the same vectors in a different order — and a caller zipping the result onto
entities would then attach the wrong vector to the wrong entity and store it
without error. A corrupted graph, not an exception.

That shapes how the cases are written. **Every multi-text case uses inputs that
differ from each other**, because a suite that embeds `["a", "a", "a"]` cannot
observe a reordering: three identical inputs give three identical vectors and
every permutation passes. This is CLAUDE.md's failure-shape table applied
before the fact rather than after — the input has to make a correct
implementation and a scrambling one disagree.

## What it deliberately does not assert

Nothing about *meaning*. Whether two related texts land near each other is a
property of the model, not of the port, and an adapter backed by a hash (see
`FakeEmbeddingProvider`) satisfies every clause here while being semantically
useless. A suite that asserted similarity would either fail against the fake or
be tuned until it passed, and tuning a correctness test until it passes is how
a contract stops being one.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from redstring.ports.embedding_provider import EmbeddingProvider

#: Texts that are pairwise distinct *and* distinct in content, so that a
#: reordering changes the answer. Lengths differ too, which catches an adapter
#: that sorts by length to pack batches.
DISTINCT_TEXTS = [
    "Ada Lovelace wrote the first algorithm",
    "Geneva",
    "a retry budget bounds how long a caller waits before giving up entirely",
    "pgvector",
]


class EmbeddingProviderCompliance:
    """Subclass this and supply a `provider` fixture."""

    @pytest.fixture
    def provider(self) -> EmbeddingProvider:  # pragma: no cover -- overridden
        raise NotImplementedError("supply a `provider` fixture")

    async def test_one_vector_comes_back_per_text(self, provider):
        result = await provider.embed(DISTINCT_TEXTS)

        assert len(result) == len(DISTINCT_TEXTS)

    async def test_every_vector_has_the_declared_dimension(self, provider):
        """The port's central promise, and the one a store enforces later.

        Asserted with `!=` rather than `is not`, and stated here because the
        opposite bit this project once: CPython caches small integers, so
        `len(v) is not provider.dimension` passes at a dimension of 8 and
        rejects every legitimate vector at 768.
        """
        result = await provider.embed(DISTINCT_TEXTS)

        wrong = [len(v) for v in result if len(v) != provider.dimension]
        assert not wrong, f"expected width {provider.dimension}, got {wrong}"

    async def test_order_is_preserved(self, provider):
        """Embed the same texts in two different orders and compare.

        The discriminating case. Embedding a list and its reverse must give the
        reversed list of vectors; an adapter that returns results in completion
        order, or sorted, or deduplicated, fails here and passes everything
        else in this file.
        """
        forward = await provider.embed(DISTINCT_TEXTS)
        backward = await provider.embed(list(reversed(DISTINCT_TEXTS)))

        assert backward == list(reversed(forward)), (
            "embedding a reversed list did not give the reversed vectors, so "
            "results are not positional"
        )

    async def test_the_same_text_gives_the_same_vector(self, provider):
        """Determinism within a run.

        Not a property of embedding models in general — a provider free to
        return different vectors for one text would make every downstream
        assertion untestable, so the port requires it and this pins it.
        """
        first = await provider.embed(["Ada Lovelace"])
        second = await provider.embed(["Ada Lovelace"])

        assert first == second

    async def test_different_texts_give_different_vectors(self, provider):
        """Guards the guard.

        A provider returning one constant vector satisfies count, width, order
        and determinism above — every case in this file passes, and the port is
        useless. This is the vacuity check the rest of the suite needs.
        """
        result = await provider.embed(DISTINCT_TEXTS)

        distinct = {tuple(v) for v in result}
        assert len(distinct) == len(DISTINCT_TEXTS), (
            "the provider returned duplicate vectors for distinct texts"
        )

    async def test_an_empty_batch_returns_an_empty_list(self, provider):
        """Stated in the port because it must not become a round trip.

        A caller that filtered a batch down to nothing should not be charged
        for a request, and most providers reject an empty one. Pinned as an
        example rather than left to a property, per the boundary rule in
        `.claude/rules/testing.md`.
        """
        assert await provider.embed([]) == []

    async def test_a_single_text_batch_works(self, provider):
        """The other boundary, and the common case in practice."""
        result = await provider.embed(["Ada Lovelace"])

        assert len(result) == 1
        assert len(result[0]) == provider.dimension

    async def test_vectors_are_finite(self, provider):
        """No NaN, no infinity.

        A NaN component poisons every cosine computed against it and does so
        *quietly* — comparisons against NaN are false, so the vector simply
        never matches anything and a caller sees an entity that has no
        neighbours rather than an error.
        """
        result = await provider.embed(DISTINCT_TEXTS)

        bad = [c for v in result for c in v if not math.isfinite(c)]
        assert not bad, f"non-finite components: {bad[:5]}"

    async def test_the_model_name_is_not_blank(self, provider):
        """It is stored next to vectors as provenance, and an empty string
        there is indistinguishable from "nobody recorded it"."""
        assert provider.model.strip()

    async def test_the_dimension_is_positive_and_constant(self, provider):
        """Read twice: a provider computing it lazily from the last response
        would satisfy one read and drift over a run."""
        first = provider.dimension

        assert first > 0
        assert provider.dimension == first
