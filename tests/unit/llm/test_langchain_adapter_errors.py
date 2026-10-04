"""Completion error translation, refusals, and malformed outputs in LangChain adapter.

Tests the domain exception translation layer that turns vendor-specific or malformed
LLM responses into typed Redstring domain exceptions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from langchain_core.messages import AIMessage

from redstring.domain.exceptions import (
    EmptyCompletionError,
    LlmProviderError,
    MalformedCompletionError,
    RefusedCompletionError,
    UnstructuredCompletionError,
)
from redstring.llm.adapters.langchain import LangChainLlmProvider

from .test_langchain_adapter import (
    Bag,
    ScriptedChatModel,
    provider_replying,
)

if TYPE_CHECKING:
    from langchain_core.outputs import ChatResult


async def test_empty_content_raises_rather_than_reporting_an_empty_extraction() -> None:
    """The verified failure of `qwen3.6-27b-mtp`: HTTP 200, `content` empty."""
    with pytest.raises(EmptyCompletionError) as caught:
        await provider_replying("", finish_reason="length").extract("Ada Lovelace.", Bag)

    assert caught.value.finish_reason == "length"
    assert caught.value.model == "test/scripted-v1"


async def test_a_truncation_the_vendor_sdk_refuses_becomes_the_same_empty_error() -> None:
    """Requesting json_schema routes through openai LengthFinishReasonError."""
    from openai import LengthFinishReasonError
    from openai.types.chat import ChatCompletion

    truncated = ChatCompletion(
        id="x",
        created=0,
        model="scripted",
        object="chat.completion",
        choices=[
            {
                "finish_reason": "length",
                "index": 0,
                "message": {"role": "assistant", "content": ""},
            }
        ],
    )

    class TruncatingChatModel(ScriptedChatModel):
        def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
            raise LengthFinishReasonError(completion=truncated)

    provider = LangChainLlmProvider(
        TruncatingChatModel(reply=AIMessage(content="{}")), model="test/scripted-v1"
    )

    with pytest.raises(EmptyCompletionError) as caught:
        await provider.extract("Ada.", Bag)

    assert caught.value.finish_reason == "length"


async def test_a_content_filter_refusal_becomes_a_distinct_domain_error() -> None:
    """OpenAI ContentFilterFinishReasonError maps to RefusedCompletionError."""
    from openai import ContentFilterFinishReasonError

    class RefusingChatModel(ScriptedChatModel):
        def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
            raise ContentFilterFinishReasonError

    provider = LangChainLlmProvider(
        RefusingChatModel(reply=AIMessage(content="{}")), model="test/scripted-v1"
    )

    with pytest.raises(RefusedCompletionError) as caught:
        await provider.extract("Ada.", Bag)

    assert caught.value.model == "test/scripted-v1"


async def test_a_refusal_is_still_one_of_the_catchable_family() -> None:
    """A caller wrapping extraction keeps one except for LlmProviderError."""
    from openai import ContentFilterFinishReasonError

    class RefusingChatModel(ScriptedChatModel):
        def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
            raise ContentFilterFinishReasonError

    provider = LangChainLlmProvider(RefusingChatModel(reply=AIMessage(content="{}")), model="m")

    with pytest.raises(LlmProviderError):
        await provider.extract("Ada.", Bag)


async def test_a_refusal_is_not_reported_as_an_empty_completion() -> None:
    """Guards the distinction: refusal is not empty completion."""
    from openai import ContentFilterFinishReasonError

    class RefusingChatModel(ScriptedChatModel):
        def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
            raise ContentFilterFinishReasonError

    provider = LangChainLlmProvider(RefusingChatModel(reply=AIMessage(content="{}")), model="m")

    with pytest.raises(RefusedCompletionError):
        await provider.extract("Ada.", Bag)
    assert not issubclass(RefusedCompletionError, EmptyCompletionError)


async def test_whitespace_only_content_counts_as_empty() -> None:
    with pytest.raises(EmptyCompletionError):
        await provider_replying("  \n\t ").extract("Ada Lovelace.", Bag)


async def test_content_that_is_not_json_raises_malformed_and_names_the_schema() -> None:
    with pytest.raises(MalformedCompletionError) as caught:
        await provider_replying("I'm sorry, I can't help with that.").extract("Ada.", Bag)

    assert caught.value.schema == "Bag"


async def test_content_that_is_not_json_at_all_is_unstructured_not_merely_malformed() -> None:
    """Fluent prose instead of JSON raises UnstructuredCompletionError."""
    markdown = (
        "## Extraction results\n\n"
        "I found the following entities in the text:\n\n"
        "- **Ada Lovelace** -- a mathematician\n"
        "- **Charles Babbage** -- an inventor\n\n"
        "They worked together on the Analytical Engine."
    )

    with pytest.raises(UnstructuredCompletionError) as caught:
        await provider_replying(markdown).extract("Ada and Charles.", Bag)

    assert isinstance(caught.value, MalformedCompletionError)
    assert "response_format" in caught.value.cause
    assert "grammar" in caught.value.cause


async def test_a_json_array_is_unstructured_too_not_a_validation_failure() -> None:
    with pytest.raises(UnstructuredCompletionError):
        await provider_replying('["Ada", "Charles"]').extract("Ada.", Bag)


async def test_json_of_the_wrong_shape_raises_malformed_and_not_a_partial_object() -> None:
    """Valid JSON, wrong schema raises MalformedCompletionError."""
    with pytest.raises(MalformedCompletionError) as caught:
        await provider_replying('{"entities": [{"name": "Ada"}]}').extract("Ada.", Bag)

    assert "entity_type" in caught.value.cause


async def test_both_failure_types_are_one_catchable_family() -> None:
    """A caller wrapping extraction needs one except, not a growing tuple."""
    for provider in (provider_replying(""), provider_replying("garbage")):
        with pytest.raises(LlmProviderError):
            await provider.extract("Ada.", Bag)


async def test_a_block_list_holding_no_text_is_empty_not_malformed() -> None:
    with pytest.raises(EmptyCompletionError):
        await provider_replying([{"type": "reasoning", "reasoning": "thinking..."}]).extract(
            "Ada.", Bag
        )


async def test_blank_text_is_refused_before_a_model_is_called() -> None:
    chat = ScriptedChatModel(reply=AIMessage(content="{}"))
    provider = LangChainLlmProvider(chat, model="test/v1")

    with pytest.raises(ValueError, match="text must not be blank"):
        await provider.extract("   ", Bag)

    assert chat.seen_messages == []
