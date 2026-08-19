"""A deterministic `EmbeddingProvider` with no model behind it.

Exported, unlike most test doubles, for the reason `FakeLlmProvider` is: a
caller cannot write a test for their own pipeline without one, and the
alternative is every downstream project hand-rolling the same
hash-into-a-unit-vector.

## What it is for, and what it is not

It makes the vector half of the library exercisable — `build_graph` with an
embedding provider and a `VectorStore` runs end to end in the commit gate, with
no endpoint. That is worth a lot and is all it is worth.

**The vectors carry no semantics.** Two texts about the same subject are as far
apart as two unrelated ones, because the components come from a hash. Nothing
that depends on similarity *meaning* something — recall, ranking quality, a
consolidation threshold tuned against real embeddings — can be tested with
this. What can be tested is everything structural: that the right number of
vectors come back, in the right order, at the right width, wired to the right
store, for the right entities.

That distinction is the same one `tests/accuracy/` draws between correct and
accurate, and it is worth keeping in view: a green suite over this provider
says the plumbing works, never that the search is good.

## The prefixes are real, not ignored

`document_prefix` and `query_prefix` are prepended before hashing, so the fake
returns *different vectors* for the two sides exactly as an asymmetric model
does. A fake that accepted the arguments and dropped them would let a caller's
test pass against a pipeline that never applied a prefix, which is the one
thing this fake is being asked about here.

## Determinism is per-text, not per-call

The same text always gives the same vector, in this process and the next. That
makes a test that embeds, stores, and searches reproducible, and it means a
caller can assert on a specific vector without pinning a seed.
"""

from __future__ import annotations

import hashlib
import math
from typing import TYPE_CHECKING

from redstring.domain.exceptions import EmbeddingProviderError

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Matches `nomic-embed-text`, which is what this project's integration
#: suite points at. The default is realistic on purpose: CLAUDE.md records a
#: dimension check written with `is not` that passed at a test dimension of 8
#: and rejected every legitimate write at 768, because CPython caches small
#: integers. A fake whose default is 8 invites exactly that test.
DEFAULT_DIMENSION = 768


class FakeEmbeddingProvider:
    """Deterministic unit vectors derived from a hash of the text."""

    def __init__(
        self,
        *,
        model: str = "fake/hash-v1",
        dimension: int = DEFAULT_DIMENSION,
        fail_on: str | None = None,
        document_prefix: str = "",
        query_prefix: str = "",
    ) -> None:
        """Build a provider.

        Args:
            model: Reported as `model`, for provenance.
            dimension: Width of every vector returned. Must be positive.
            fail_on: A substring that makes `embed` raise when any input
                contains it. This exists so a caller can test *their* error
                path — the resilience wrappers over this port are the whole
                reason a provider needs to be able to fail on demand, and the
                alternative is a caller monkeypatching the adapter.
            document_prefix: Prepended to every text passed to `embed`.
            query_prefix: Prepended to every text passed to `embed_query`.
                Both default to empty, which is a symmetric model.
        """
        if dimension <= 0:
            raise ValueError(f"dimension must be positive, got {dimension}")
        self._model = model
        self._dimension = dimension
        self._fail_on = fail_on
        self._document_prefix = document_prefix
        self._query_prefix = query_prefix

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One unit vector per text, in order.

        Empty input returns empty output without doing any work, which is the
        port's stated contract and not merely an optimisation here: a caller
        that filtered a batch down to nothing must not be charged for it by a
        real adapter, so the fake must not hide the case.
        """
        return self._embed_all(texts, self._document_prefix)

    async def embed_query(self, texts: Sequence[str]) -> list[list[float]]:
        """One unit vector per query text, in order.

        Differs from `embed` only in which prefix is applied, which is the
        whole of the asymmetry a real model has.
        """
        return self._embed_all(texts, self._query_prefix)

    def _embed_all(self, texts: Sequence[str], prefix: str) -> list[list[float]]:
        """The shared body. `fail_on` matches the caller's text, not the
        prefixed one -- a caller asking to fail on `"boom"` should not have to
        know what this provider prepends."""
        if self._fail_on is not None and any(self._fail_on in t for t in texts):
            raise EmbeddingProviderError(f"asked to fail on {self._fail_on!r}", model=self._model)
        return [self._vector(prefix + text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        """A unit vector determined entirely by `text`.

        Built from a counter-mode digest rather than one hash, because a single
        `sha256` gives 32 bytes and the default width is 768. Normalised so
        that cosine similarity behaves — an un-normalised hash vector makes
        every score a function of length, and a caller comparing scores would
        get numbers that look meaningful and are not.
        """
        raw = bytearray()
        counter = 0
        while len(raw) < self._dimension:
            raw += hashlib.sha256(f"{text}\x00{counter}".encode()).digest()
            counter += 1

        # Centre on zero so vectors are spread over the sphere rather than
        # crowded into one octant, where every pair would look similar.
        components = [(b / 255.0) - 0.5 for b in raw[: self._dimension]]

        norm = math.sqrt(sum(c * c for c in components))
        if norm == 0.0:  # pragma: no cover -- needs a digest of all 0x80 bytes
            components[0] = 1.0
            return components
        return [c / norm for c in components]
