"""`EmbeddingProvider` over any LangChain `Embeddings` implementation.

The second adapter behind this port, and the reason there is a compliance
suite at all: **a contract one implementation satisfies is not a contract.**
`FakeEmbeddingProvider` hashes text and could satisfy any reasonable set of
assertions by construction, so until something with a network behind it ran
the same body, the suite was a description of the fake.

Same division as `LangChainLlmProvider`, for the same reasons:

- the caller constructs the LangChain object, so a deployment's own retry,
  callback and tracing configuration is not something this class must mirror;
- `model` is passed in rather than read off the client, because the convention
  here is provider-qualified and versioned (`"ollama/nomic-embed-text"`) and no
  embeddings client knows which provider is in front of it;
- this module and `langchain.py` are the only places in `src/` permitted to
  import `langchain*`, enforced by `tests/unit/llm/test_port_does_not_leak.py`
  rather than by the import contract, which sees first-party imports only.

## `dimension` is declared, not probed

The constructor takes it. The alternative — embed a throwaway string at
construction and measure the result — makes building a provider do network
I/O, turns a configuration error into a connection error, and would make
`__init__` async or blocking. Neither is acceptable for something a caller
constructs at import time.

The cost is that a caller can state the wrong number. That is caught the first
time a vector is produced, by `embed` itself, rather than left to corrupt a
store: a provider that promises 768 and returns 1536 raises
`EmbeddingProviderError` naming both.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from redstring.domain.exceptions import EmbeddingProviderError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from langchain_core.embeddings import Embeddings


class LangChainEmbeddingProvider:
    """Embeddings through any LangChain `Embeddings` client."""

    def __init__(self, embeddings: Embeddings, *, model: str, dimension: int) -> None:
        """Wrap an already-configured embeddings client.

        Args:
            embeddings: The LangChain client. Built by the caller.
            model: Provenance recorded alongside the vectors.
            dimension: The width this model produces. Declared rather than
                probed -- see the module docstring -- and checked against
                reality on the first `embed`.
        """
        if dimension <= 0:
            raise ValueError(f"dimension must be positive, got {dimension}")
        self._embeddings = embeddings
        self._model = model
        self._dimension = dimension

    @classmethod
    def openai_compatible(
        cls,
        *,
        base_url: str,
        model: str,
        dimension: int,
        api_key: str = "not-needed",
        provider: str = "openai-compatible",
    ) -> LangChainEmbeddingProvider:
        """Build a provider against any OpenAI-compatible embeddings endpoint.

        Covers Ollama, llama.cpp, vLLM, LM Studio and OpenAI itself. The
        `api_key` default exists because most local servers require the field
        and ignore its value.

        Raises:
            ImportError: `langchain-openai` is not installed; install
                `redstring[llm]`.
        """
        try:
            from langchain_openai import OpenAIEmbeddings
        except ImportError as error:  # pragma: no cover -- needs the extra absent
            raise ImportError(
                "LangChainEmbeddingProvider.openai_compatible needs langchain-openai: "
                "install `redstring[llm]`"
            ) from error

        # `model`, `base_url` and `api_key` are pydantic *aliases*, and the
        # pydantic mypy plugin runs with `init_forbid_extra`, which cannot see
        # `populate_by_name` aliases and reads them as extra kwargs. Same
        # situation and same reasoning as `langchain.py`'s `ChatOpenAI` call --
        # spelling the private names would type-check and would be the wrong
        # code, since they are undocumented and have been renamed before.
        #
        # `check_embedding_ctx_length=False` because the default path chunks
        # and re-averages long inputs client-side, which would silently break
        # this port's positional contract: one input could become several
        # requests whose results are combined. Entity names are short; the
        # behaviour is unwanted either way.
        client: Any = OpenAIEmbeddings(  # type: ignore[call-arg]
            model=model,
            base_url=base_url,
            api_key=api_key,
            check_embedding_ctx_length=False,
        )
        return cls(client, model=f"{provider}/{model}", dimension=dimension)

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed each text, in order.

        The empty case short-circuits before touching the client. That is the
        port's contract and it is also self-defence: OpenAI-compatible servers
        reject an empty `input` array, so a caller who filtered a batch down to
        nothing would otherwise get a 400 from someone else's server.
        """
        if not texts:
            return []

        try:
            vectors = await self._embeddings.aembed_documents(list(texts))
        except Exception as error:
            raise EmbeddingProviderError(
                f"the embeddings client raised {type(error).__name__}: {error}",
                model=self._model,
            ) from error

        # Both checks are about the same thing -- a result that cannot be
        # trusted positionally -- and both are silent if unchecked. A short
        # list zipped onto entities attaches vectors to the wrong ones; a wrong
        # width is rejected later by the store, one row at a time, with a
        # message about a column.
        if len(vectors) != len(texts):
            raise EmbeddingProviderError(
                f"asked for {len(texts)} embeddings and got {len(vectors)}; "
                f"results are positional and cannot be matched up",
                model=self._model,
            )

        wrong = {len(v) for v in vectors if len(v) != self._dimension}
        if wrong:
            raise EmbeddingProviderError(
                f"declared dimension {self._dimension} but the model returned "
                f"{sorted(wrong)}; the provider's dimension does not match the model",
                model=self._model,
            )

        return [list(v) for v in vectors]
