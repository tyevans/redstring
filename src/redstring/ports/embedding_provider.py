"""Turning text into vectors, as a port.

`VectorStore` existed for six slices with two adapters and a compliance suite,
and **nothing in this library could put a vector in it** — every write path
took vectors the caller had computed elsewhere. This is the port that closes
that, and it is deliberately the narrowest thing that does.

See `docs/adr/0017-the-embedding-provider-port.md` for why it is a separate
port from `LlmProvider` rather than a method on it, and why the dimension is
declared on both sides.

## `embed` is batch, and the order is the contract

Every embedding API charges and rate-limits per *request*, not per input, so a
one-at-a-time port would put the adapter's most important optimisation out of
reach and push a worse version of it up into the caller.

The return is positional: **one vector per input text, in the same order**.
That is what lets a caller zip results back onto the entities they came from,
and it is the thing an adapter is most likely to break — by batching
internally, by retrying a partial failure, or by deduplicating identical texts
and forgetting to re-expand. The compliance suite checks it with inputs that
differ from each other, because a suite that embeds `["a", "a", "a"]` cannot
see a reordering.

## Documents and queries are two sides, because modern models are asymmetric

`embed` is the *corpus* side and `embed_query` is the *query* side, and the
split is here rather than in a caller because the model asks for it.
`nomic-embed-text-v1.5` wants `search_document: ` on stored text and
`search_query: ` on a query; the BGE family wants an instruction on the query
and nothing on the document. A port with one method cannot express that at
all, so every call site has to remember to prepend a string — and a rule that
holds only because nobody has broken it is `recurring-defects.md` §3.

The two methods have the identical contract otherwise: batch, positional,
one vector per input in order, empty input makes no request.

**Getting it wrong is silent.** A corpus embedded without its prefix produces
well-formed vectors that cluster sensibly and score plausibly; the only symptom
is retrieval quality below what the model can do, which reads as "this model is
mediocre". Nothing raises. That is the whole reason this is a port method a
compliance suite can enforce rather than a wrapper a caller composes twice.

See `docs/adr/0043-a-query-is-embedded-differently-from-a-document.md`, which
also records the consequence for storage: **a corpus embedded with a prefix and
the same corpus embedded without it are not comparable vectors**, so the prefix
is part of the model's identity in ADR 0017's sense and changing it means a new
store.

## Vectors are reproducible in direction, not bit-for-bit

The same text embedded twice gives the *same* vector in the sense that matters
-- cosine above 0.99 -- and not an identical one. Measured against llama.cpp
behind `nomic-embed-text`: embedding a string alone and again inside a batch of
four differs by up to `4e-3` per component, because floating-point accumulation
depends on how the batch was packed. Short inputs came back bit-identical and
long ones did not.

So a caller must not compare vectors with `==`, and must not store a hash of
one as an identity. The compliance suite states the contract this way for the
same reason: an earlier version asserted equality, passed against a hash and a
stub, and failed on its first contact with a real server.

## Dimension is declared, not discovered

`dimension` is a property here and on `VectorStore`, and the composition point
refuses to wire a provider to a store whose numbers disagree — before any text
is embedded, rather than after the API call has been paid for and pgvector has
rejected the insert.

The store is not the place to adapt: its dimension is fixed at DDL time, and
ADR 0002 records that changing embedding model means a **new store**, because
two models' vectors are not comparable even at equal dimensions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Text to vectors, in batches, with a fixed dimension."""

    @property
    def model(self) -> str:
        """Which model produced the vectors, for provenance.

        Mirrors `LlmProvider.model`. Worth recording next to a stored vector:
        two models' embeddings are not comparable, and the model name is the
        only thing that says which collection a vector belongs to.
        """
        ...

    @property
    def dimension(self) -> int:
        """Component count of every vector this provider returns.

        Constant for the life of the provider. A provider that would return
        different widths for different inputs cannot be matched against a
        `VectorStore`, which fixes its width at construction.
        """
        ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed each text, in order.

        Args:
            texts: The strings to embed. May be empty, in which case the
                result is empty and **no request is made** — a caller filtering
                a batch down to nothing should not pay for a round trip, and
                an adapter that sends an empty request gets an error from most
                providers.

        Returns:
            One vector per input, in the same order, each of length
            `dimension`. `len(result) == len(texts)` always.

        Raises:
            EmbeddingProviderError: The provider returned nothing usable, or
                returned a number of vectors that does not match the number of
                texts. The second is worth its own mention because it is
                silent otherwise: a caller zipping results onto entities would
                attach the wrong vector to the wrong entity rather than fail.
        """
        ...

    async def embed_query(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed each text as a *query*, in order.

        Same contract as `embed` in every respect — batch, positional, empty
        input makes no request, `len(result) == len(texts)`, each of length
        `dimension` — and a different side of an asymmetric model. See the
        module docstring for why the port carries both rather than leaving the
        distinction to the caller.

        Batch, and not single-string, even though a query is usually one text:
        a caller expanding a question into several paraphrases, or scoring a
        batch of evaluation queries, pays per *request*. Keeping the two
        signatures identical also means a decorator over this port — a cache, a
        retry budget, a call limiter — wraps both the same way.

        For a symmetric model, or one used without prefixes, this is `embed`.

        Raises:
            EmbeddingProviderError: As `embed`.
        """
        ...
