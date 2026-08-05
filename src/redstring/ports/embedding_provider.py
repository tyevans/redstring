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
