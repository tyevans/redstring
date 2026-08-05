"""`LangChainEmbeddingProvider.openai_compatible`, which costs an import.

Marked `integration` for **cost, not infrastructure** — the same reason
`test_wheel_contents.py` is. It needs no server: `OpenAIEmbeddings` does no I/O
at construction. What it needs is `langchain_openai`, whose first import takes
about fourteen seconds, and no other unit test pays that. Adding it to the
commit gate would lengthen a ~60s suite by a quarter for four assertions.

That is also how `LangChainLlmProvider.openai_compatible` is handled, and the
consistency is deliberate: both factories are thin, both are exercised for real
by the live-endpoint suites, and neither is worth a fifth of the commit gate.

**What it does catch** is worth stating, because "constructs an object" reads
as a weak test. Both factories pass pydantic *aliases* (`model`, `base_url`,
`api_key`) that the mypy plugin cannot verify, so each carries a
`# type: ignore[call-arg]`. That ignore is exactly the kind that goes stale
silently after a LangChain upgrade renames a field — the type checker stays
quiet because it was told to, and the failure appears at a caller's first
construction. This runs the constructor for real.

    uv run pytest -m integration tests/integration/llm/test_langchain_embedding_factory.py
"""

from __future__ import annotations

import pytest

from redstring.llm.adapters.langchain_embedding import LangChainEmbeddingProvider
from redstring.ports.embedding_provider import EmbeddingProvider

pytestmark = pytest.mark.integration


class TestOpenAiCompatibleFactory:
    """The convenience constructor, exercised without a network.

    `OpenAIEmbeddings` does no I/O at construction, so this is a real test of
    the factory rather than a mocked one: if the pydantic aliases it passes
    were wrong, or the `# type: ignore` were hiding a genuine signature change
    after a LangChain upgrade, this raises.
    """

    def test_it_builds_a_provider_with_a_qualified_model_name(self):
        provider = LangChainEmbeddingProvider.openai_compatible(
            base_url="http://localhost:11434/v1",
            model="nomic-embed-text",
            dimension=768,
        )

        assert provider.dimension == 768
        assert provider.model == "openai-compatible/nomic-embed-text"

    def test_the_provider_prefix_is_configurable(self):
        """`model` is provenance, and "openai-compatible/x" is a poor record
        when the server is Ollama. The prefix names what is actually in front
        of the endpoint."""
        provider = LangChainEmbeddingProvider.openai_compatible(
            base_url="http://localhost:11434/v1",
            model="nomic-embed-text",
            dimension=768,
            provider="ollama",
        )

        assert provider.model == "ollama/nomic-embed-text"

    def test_it_satisfies_the_port(self):
        provider = LangChainEmbeddingProvider.openai_compatible(
            base_url="http://localhost:11434/v1", model="nomic-embed-text", dimension=768
        )

        assert isinstance(provider, EmbeddingProvider)

    def test_a_bad_dimension_is_rejected_here_too(self):
        """The factory must not be a way around the constructor's guard."""
        with pytest.raises(ValueError, match="dimension must be positive"):
            LangChainEmbeddingProvider.openai_compatible(
                base_url="http://localhost:11434/v1", model="nomic-embed-text", dimension=0
            )
