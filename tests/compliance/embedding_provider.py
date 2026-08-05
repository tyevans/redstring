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

## Equality is by cosine, not by `==`

**A real embedding server does not return bit-identical vectors for the same
text.** Measured against llama.cpp behind `nomic-embed-text`: embedding
`"Ada Lovelace wrote the first algorithm"` alone and again inside a batch of
four gives vectors differing by up to `4e-3` per component, because
floating-point accumulation depends on how the batch was packed. Short inputs
came back bit-identical and long ones did not, which is the same effect seen
from the other side.

The first version of this suite asserted `==` and **passed only because the
one adapter behind it was a hash.** The first live run failed two clauses. That
is `recurring-defects.md` §1 arriving in reverse: not an in-memory reference
that is more forgiving than production, but a contract no real backend can
satisfy — which is worse, because it would have been "fixed" by exempting the
real adapter.

So the shared claim is weakened exactly as far as the backend forces and no
further, the same move `tests/compliance/vector_store.py` makes for float32
storage: **cosine similarity above `SAME_VECTOR_COSINE`**, with an explicit
check that *mismatched* pairs fall far below it. Tolerance alone would not be
a test — every vector from a poorly-centred model is somewhat similar to every
other, so a scrambling adapter could pass a loose threshold. The pair is what
discriminates: 0.9996 for the right pairing against 0.27 for the wrong one, on
the live run that prompted this.

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

#: Two vectors of the same text agree above this, and two vectors of different
#: texts must not. The observed values on a live model were 0.9996 for a
#: matched pair and 0.27 for a mismatched one, so the threshold sits far from
#: both -- close enough that batch-dependent float noise passes, distant
#: enough that a swapped result cannot.
SAME_VECTOR_COSINE = 0.99

#: Texts that are pairwise distinct *and* distinct in content, so that a
#: reordering changes the answer. Lengths differ too, which catches an adapter
#: that sorts by length to pack batches -- and, as the live run showed, length
#: is also what makes batch-dependent float noise visible at all.
DISTINCT_TEXTS = [
    "Ada Lovelace wrote the first algorithm",
    "Geneva",
    "a retry budget bounds how long a caller waits before giving up entirely",
    "pgvector",
]


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity, used instead of `==` throughout. See the docstring."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return 0.0 if norm == 0.0 else dot / norm


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
        """Embed the same texts in two orders; the results must correspond.

        The discriminating case in this file. An adapter that returns results
        in completion order, or sorted, or deduplicated, fails here and passes
        everything else.

        Compared by cosine rather than `==` because batch composition changes
        the low bits on a real server -- see the module docstring. The second
        assertion is what keeps the first honest: with a tolerance alone, an
        adapter returning similar-but-wrong vectors could pass, so the wrong
        pairing is required to be *dissimilar*.
        """
        forward = await provider.embed(DISTINCT_TEXTS)
        backward = await provider.embed(list(reversed(DISTINCT_TEXTS)))
        realigned = list(reversed(backward))

        for text, a, b in zip(DISTINCT_TEXTS, forward, realigned, strict=True):
            assert _cosine(a, b) >= SAME_VECTOR_COSINE, (
                f"{text!r} embedded differently depending on its position in "
                f"the batch, so results are not positional"
            )

        assert _cosine(forward[0], realigned[1]) < SAME_VECTOR_COSINE, (
            "two different texts embed to nearly the same vector, so the check "
            "above cannot tell a correct pairing from a scrambled one"
        )

    async def test_the_same_text_gives_the_same_vector(self, provider):
        """Determinism within a run, to cosine tolerance.

        A provider free to return genuinely different vectors for one text
        would make every downstream assertion untestable. But "the same" here
        cannot mean bit-identical: the same text in a batch of one and in a
        batch of four differs by ~4e-3 per component on a real server, and that
        is a property of the arithmetic rather than of the adapter.
        """
        first = await provider.embed(["Ada Lovelace"])
        second = await provider.embed(["Ada Lovelace"])

        assert _cosine(first[0], second[0]) >= SAME_VECTOR_COSINE

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
