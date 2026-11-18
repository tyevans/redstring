"""The OpenAI-compatible adapters pass an explicit timeout and retry count.

Checked on the client the adapter actually builds, not on the adapter's own
attributes: a value accepted by the constructor and dropped before the SDK
would pass an attribute check on the wrapper and still be wrong on the wire.
The stalled-endpoint test shows the timeout is enforced, not merely stored.
"""

from __future__ import annotations

import inspect
import socket
import threading
import time

import openai
import pytest
from pydantic import BaseModel

from redstring.llm.adapters.langchain import (
    DEFAULT_MAX_RETRIES_SDK,
    DEFAULT_REQUEST_TIMEOUT,
    LangChainLlmProvider,
)
from redstring.llm.adapters.langchain_embedding import (
    DEFAULT_EMBEDDING_MAX_RETRIES,
    DEFAULT_EMBEDDING_REQUEST_TIMEOUT,
    LangChainEmbeddingProvider,
)


class Answer(BaseModel):
    value: str


def chat_provider(**kwargs) -> LangChainLlmProvider:
    return LangChainLlmProvider.openai_compatible(
        base_url="http://127.0.0.1:9/v1", model="m", **kwargs
    )


def embedding_provider(**kwargs) -> LangChainEmbeddingProvider:
    return LangChainEmbeddingProvider.openai_compatible(
        base_url="http://127.0.0.1:9/v1", model="m", dimension=8, **kwargs
    )


class TestChatAdapter:
    def test_the_timeout_and_retry_parameters_have_documented_defaults(self):
        parameters = inspect.signature(LangChainLlmProvider.openai_compatible).parameters

        assert parameters["request_timeout"].default == DEFAULT_REQUEST_TIMEOUT == 300.0
        assert parameters["max_retries"].default == DEFAULT_MAX_RETRIES_SDK == 2

    def test_defaults_reach_the_chat_client(self):
        client = chat_provider()._chat.root_async_client

        assert client.timeout == 300.0
        assert client.max_retries == 2

    def test_explicit_values_reach_the_chat_client(self):
        client = chat_provider(request_timeout=7.5, max_retries=0)._chat.root_async_client

        assert client.timeout == 7.5
        assert client.max_retries == 0

    async def test_a_stalled_endpoint_is_abandoned_at_the_timeout(self):
        # A listener that accepts connections and never answers.
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
        held: list[socket.socket] = []

        def accept_and_hold() -> None:
            try:
                while True:
                    connection, _ = listener.accept()
                    held.append(connection)
            except OSError:
                return

        thread = threading.Thread(target=accept_and_hold, daemon=True)
        thread.start()
        port = listener.getsockname()[1]
        try:
            provider = LangChainLlmProvider.openai_compatible(
                base_url=f"http://127.0.0.1:{port}/v1",
                model="m",
                request_timeout=0.5,
                max_retries=0,
            )
            started = time.perf_counter()
            with pytest.raises(openai.APITimeoutError):
                await provider.extract("some text", Answer)
            assert time.perf_counter() - started < 10.0
        finally:
            listener.close()
            for connection in held:
                connection.close()


class TestEmbeddingAdapter:
    def test_the_timeout_and_retry_parameters_have_documented_defaults(self):
        parameters = inspect.signature(LangChainEmbeddingProvider.openai_compatible).parameters

        assert parameters["request_timeout"].default == DEFAULT_EMBEDDING_REQUEST_TIMEOUT == 60.0
        assert parameters["max_retries"].default == DEFAULT_EMBEDDING_MAX_RETRIES == 2

    def test_defaults_reach_the_embeddings_client(self):
        embeddings = embedding_provider()._embeddings

        assert embeddings.client._client.timeout == 60.0
        assert embeddings.client._client.max_retries == 2

    def test_explicit_values_reach_the_embeddings_client(self):
        embeddings = embedding_provider(request_timeout=3.0, max_retries=1)._embeddings

        assert embeddings.client._client.timeout == 3.0
        assert embeddings.client._client.max_retries == 1
