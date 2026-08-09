"""`LangChainEmbeddingProvider` against the shared contract, with a stub client.

**This is the adapter that makes `EmbeddingProviderCompliance` a contract.**
With only `FakeEmbeddingProvider` behind it the suite described the fake: a
hash satisfies count, order, width and determinism by construction, so nothing
could have failed. Running the same body against something that goes through a
client — even a stubbed one — is what turns those assertions into promises the
port makes rather than properties one implementation happens to have.

The stub returns vectors that *differ per text*, deliberately. A stub returning
a constant would pass every case except the vacuity check, and would make the
order test meaningless — the failure mode CLAUDE.md catalogues as an input on
which the right and wrong implementations agree.

The live endpoint is exercised separately under `-m integration`.
"""

from __future__ import annotations

import hashlib

import pytest

from redstring.domain.exceptions import EmbeddingProviderError
from redstring.llm.adapters.langchain_embedding import LangChainEmbeddingProvider
from redstring.ports.embedding_provider import EmbeddingProvider
from redstring.testing.embedding_provider import EmbeddingProviderCompliance

DIMENSION = 768


class StubEmbeddings:
    """A LangChain-shaped client: `aembed_documents`, nothing else.

    Deliberately not a `MagicMock`. `recurring-defects.md` records a 583-line
    router whose entire test file used mocks and stayed green after the type it
    was built around was deleted — a mock answers any attribute, so it cannot
    fail the way a real collaborator does. This object has exactly the method
    the adapter calls, so removing that call breaks the test.
    """

    def __init__(self, *, dimension: int = DIMENSION, count: int | None = None) -> None:
        self.dimension = dimension
        self.count = count
        self.calls: list[list[str]] = []

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        vectors = [self._vector(t) for t in texts]
        return vectors if self.count is None else vectors[: self.count]

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        return [digest[i % len(digest)] / 255.0 for i in range(self.dimension)]


class TestLangChainEmbeddingProvider(EmbeddingProviderCompliance):
    @pytest.fixture
    def provider(self) -> EmbeddingProvider:
        return LangChainEmbeddingProvider(
            StubEmbeddings(),  # type: ignore[arg-type]
            model="stub/embed-v1",
            dimension=DIMENSION,
        )


class TestLangChainEmbeddingProviderSpecifics:
    def test_it_satisfies_the_port(self):
        provider = LangChainEmbeddingProvider(
            StubEmbeddings(),  # type: ignore[arg-type]
            model="stub/embed-v1",
            dimension=DIMENSION,
        )

        assert isinstance(provider, EmbeddingProvider)

    async def test_an_empty_batch_never_reaches_the_client(self):
        """Not just an optimisation: OpenAI-compatible servers reject an empty
        `input`, so a caller who filtered a batch to nothing would otherwise
        get a 400 from someone else's server."""
        client = StubEmbeddings()
        provider = LangChainEmbeddingProvider(
            client,  # type: ignore[arg-type]
            model="stub/embed-v1",
            dimension=DIMENSION,
        )

        assert await provider.embed([]) == []
        assert client.calls == [], "an empty batch was sent to the client"

    async def test_a_short_result_raises_rather_than_being_zipped(self):
        """The failure that corrupts a graph instead of stopping a run.

        A client returning three vectors for four texts — because it batched,
        retried, or deduplicated — would have its results attached to the wrong
        entities by any caller zipping them. `strict=True` in the composition
        root catches the same thing one layer up; this catches it at the
        adapter, which is where the fault actually is.
        """
        provider = LangChainEmbeddingProvider(
            StubEmbeddings(count=1),  # type: ignore[arg-type]
            model="stub/embed-v1",
            dimension=DIMENSION,
        )

        with pytest.raises(EmbeddingProviderError, match="asked for 2 embeddings and got 1"):
            await provider.embed(["Ada", "Babbage"])

    async def test_a_wrong_width_raises_naming_both_numbers(self):
        """The declared dimension is checked against reality on first use.

        Declaring it avoids network I/O in `__init__`; the cost is that a
        caller can state the wrong number, and this is where that is caught —
        before a store rejects it one row at a time with a message about a
        column type.
        """
        provider = LangChainEmbeddingProvider(
            StubEmbeddings(dimension=1536),  # type: ignore[arg-type]
            model="stub/embed-v1",
            dimension=768,
        )

        with pytest.raises(EmbeddingProviderError, match="declared dimension 768") as caught:
            await provider.embed(["Ada"])

        assert "1536" in str(caught.value)

    async def test_a_client_failure_is_wrapped_with_the_model_name(self):
        """A raw transport exception says nothing about which endpoint failed."""

        class Failing:
            async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
                raise ConnectionError("no route to host")

        provider = LangChainEmbeddingProvider(
            Failing(),  # type: ignore[arg-type]
            model="ollama/nomic-embed-text",
            dimension=DIMENSION,
        )

        with pytest.raises(EmbeddingProviderError) as caught:
            await provider.embed(["Ada"])

        assert caught.value.model == "ollama/nomic-embed-text"
        assert "ConnectionError" in str(caught.value)
        assert isinstance(caught.value.__cause__, ConnectionError)

    @pytest.mark.parametrize("dimension", [0, -1])
    def test_a_non_positive_dimension_is_rejected(self, dimension: int):
        with pytest.raises(ValueError, match="dimension must be positive"):
            LangChainEmbeddingProvider(
                StubEmbeddings(),  # type: ignore[arg-type]
                model="stub/embed-v1",
                dimension=dimension,
            )

    async def test_the_client_receives_a_list_not_the_caller_s_sequence(self):
        """`aembed_documents` is typed for `list[str]`, and a tuple is a legal
        `Sequence`. Passing one through would work against this stub and fail
        against a client that indexes or serialises it differently."""
        client = StubEmbeddings()
        provider = LangChainEmbeddingProvider(
            client,  # type: ignore[arg-type]
            model="stub/embed-v1",
            dimension=DIMENSION,
        )

        await provider.embed(("Ada", "Babbage"))

        assert client.calls == [["Ada", "Babbage"]]
