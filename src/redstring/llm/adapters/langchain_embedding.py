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
  import `langchain*`, enforced by `tests/unit/test_dependencies_stay_confined.py`
  rather than by the import contract, which sees first-party imports only.

## Both sides go through `aembed_documents`

`embed_query` does **not** call LangChain's `aembed_query`, and that is
deliberate rather than an oversight. `aembed_query` takes a single string and
returns a single vector; routing this port's batch method through it would mean
one HTTP request per query text, which is precisely the optimisation
`EmbeddingProvider` exists to keep reachable. `aembed_documents` is the batch
entry point on the same client and hits the same endpoint. The asymmetry a
modern embedding model wants lives in the *prefix*, not in which LangChain
method is called.

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

    def __init__(
        self,
        embeddings: Embeddings,
        *,
        model: str,
        dimension: int,
        document_prefix: str = "",
        query_prefix: str = "",
    ) -> None:
        """Wrap an already-configured embeddings client.

        Args:
            embeddings: The LangChain client. Built by the caller.
            model: Provenance recorded alongside the vectors.
            dimension: The width this model produces. Declared rather than
                probed -- see the module docstring -- and checked against
                reality on the first `embed`.
            document_prefix: Prepended to every text passed to `embed`.
                `"search_document: "` for `nomic-embed-text-v1.5`; empty for
                BGE, which asks for nothing on the corpus side.
            query_prefix: Prepended to every text passed to `embed_query`.
                `"search_query: "` for nomic; an instruction line for BGE.

        Both prefixes default to empty, so an existing caller's behaviour is
        unchanged. They are *not* folded into `model`: see
        `docs/adr/0043-a-query-is-embedded-differently-from-a-document.md`.
        """
        if dimension <= 0:
            raise ValueError(f"dimension must be positive, got {dimension}")
        self._embeddings = embeddings
        self._model = model
        self._dimension = dimension
        self._document_prefix = document_prefix
        self._query_prefix = query_prefix

    @classmethod
    def openai_compatible(
        cls,
        *,
        base_url: str,
        model: str,
        dimension: int,
        api_key: str = "not-needed",
        provider: str = "openai-compatible",
        document_prefix: str = "",
        query_prefix: str = "",
    ) -> LangChainEmbeddingProvider:
        """Build a provider against any OpenAI-compatible embeddings endpoint.

        Covers Ollama, llama.cpp, vLLM, LM Studio and OpenAI itself. The
        `api_key` default exists because most local servers require the field
        and ignore its value.

        `document_prefix` and `query_prefix` are the asymmetric-model task
        prefixes, threaded through unchanged -- most callers reach a model
        through this factory rather than through `__init__`, so a prefix
        available only on the constructor would be available to nobody.

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
        return cls(
            client,
            model=f"{provider}/{model}",
            dimension=dimension,
            document_prefix=document_prefix,
            query_prefix=query_prefix,
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed each text as a document, in order.

        The empty case short-circuits before touching the client. That is the
        port's contract and it is also self-defence: OpenAI-compatible servers
        reject an empty `input` array, so a caller who filtered a batch down to
        nothing would otherwise get a 400 from someone else's server.
        """
        return await self._embed_all(texts, self._document_prefix)

    async def embed_query(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed each text as a query, in order.

        Routed through `aembed_documents`, not LangChain's `aembed_query`, and
        the next reader will wonder: `aembed_query` takes one string and would
        turn a batch into one request per text, breaking this port's batch
        contract. Same client, same endpoint, different prefix -- see the
        module docstring.
        """
        return await self._embed_all(texts, self._query_prefix)

    async def _embed_all(self, texts: Sequence[str], prefix: str) -> list[list[float]]:
        """The shared body of both sides.

        One place applies a prefix, so it cannot be applied twice, and the
        empty short-circuit is above it, so it is never applied to nothing.
        """
        if not texts:
            return []

        try:
            vectors = await self._embeddings.aembed_documents([prefix + text for text in texts])
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
