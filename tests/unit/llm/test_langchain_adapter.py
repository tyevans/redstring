"""What the LangChain adapter does with the four shapes a chat model comes back in.

**Division of labour, stated because it is the risk in this file.** The double
below is a `BaseChatModel` implementing `_generate`, which is LangChain's own
extension point -- so these tests run real LangChain message plumbing over a
scripted transport. What they cannot pin is that a real OpenAI-compatible
server produces those four shapes; that is
`tests/integration/llm/test_live_endpoint.py`, which talks to the real model
and is skipped when it is unreachable. Neither file is sufficient alone: this
one would pass against an adapter that handles shapes no server emits, and
that one cannot conjure a malformed completion on demand.

Doubling `BaseChatModel` rather than `LlmProvider` is deliberate. `LlmProvider`
is ours, and Global Constraint 4 forbids mocking what we own -- the fake at
`redstring.llm.adapters.fake` is the real implementation used everywhere
else. LangChain is not ours, and the adapter exists precisely to be the one
place that knows it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel, Field

from redstring.domain.exceptions import (
    EmptyCompletionError,
    LlmProviderError,
    MalformedCompletionError,
    RefusedCompletionError,
)
from redstring.llm.adapters.langchain import LangChainLlmProvider
from redstring.ports.llm_provider import LlmProvider

if TYPE_CHECKING:
    from collections.abc import Sequence


class _Ent(BaseModel):
    name: str
    entity_type: str


class _Bag(BaseModel):
    entities: list[_Ent] = Field(default_factory=list)


class ScriptedChatModel(BaseChatModel):
    """A `BaseChatModel` that returns a prepared `AIMessage`.

    Records the messages it was handed on `seen_messages` so the *prompt
    assembly* tests can read what the adapter built -- that is inspecting the
    request the adapter constructs, which is the adapter's observable output
    at this boundary, not an assertion about call counts.
    """

    reply: AIMessage
    seen_messages: list[list[BaseMessage]] = Field(default_factory=list)
    seen_kwargs: dict[str, Any] = Field(default_factory=dict)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.seen_messages.append(list(messages))
        self.seen_kwargs.update(kwargs)
        return ChatResult(generations=[ChatGeneration(message=self.reply)])


def provider_replying(
    content: str | Sequence[Any],
    *,
    finish_reason: str | None = None,
    model: str = "test/scripted-v1",
) -> LangChainLlmProvider:
    metadata = {} if finish_reason is None else {"finish_reason": finish_reason}
    reply = AIMessage(content=content, response_metadata=metadata)
    return LangChainLlmProvider(ScriptedChatModel(reply=reply), model=model)


def test_the_adapter_satisfies_the_port():
    assert isinstance(provider_replying("{}"), LlmProvider)


def test_it_reports_the_model_it_was_built_with():
    assert provider_replying("{}", model="ollama/qwen3.6-27b-mtp").model == "ollama/qwen3.6-27b-mtp"


async def test_well_formed_json_validates_into_the_requested_schema():
    provider = provider_replying('{"entities": [{"name": "Ada", "entity_type": "Person"}]}')

    assert await provider.extract("Ada Lovelace.", _Bag) == _Bag(
        entities=[_Ent(name="Ada", entity_type="Person")]
    )


async def test_a_completion_that_validates_to_nothing_is_an_answer_not_an_error():
    """The one case that must NOT raise, and the reason the others must.

    A document genuinely holding no entities is a real outcome. It is
    distinguishable from failure only because every failure raises, so this
    test and the empty-content test below are two halves of one claim.
    """
    assert await provider_replying('{"entities": []}').extract("Nothing here.", _Bag) == _Bag()


async def test_empty_content_raises_rather_than_reporting_an_empty_extraction():
    """The verified failure of `qwen3.6-27b-mtp`: HTTP 200, `content` empty.

    Returning `_Bag()` here would be indistinguishable from the test above,
    and a knowledge graph built on that silently loses every document whose
    extraction failed this way.
    """
    with pytest.raises(EmptyCompletionError) as caught:
        await provider_replying("", finish_reason="length").extract("Ada Lovelace.", _Bag)

    assert caught.value.finish_reason == "length"
    assert caught.value.model == "test/scripted-v1"


async def test_a_truncation_the_vendor_sdk_refuses_becomes_the_same_empty_error():
    """Found against the live server, and not findable from the shapes above.

    Requesting `response_format: json_schema` routes langchain-openai through
    the openai SDK's parsing path, which raises `LengthFinishReasonError` for
    `finish_reason == "length"` *instead of* returning a message. So the more
    common half of the empty case never reaches `_parse` at all, and without
    this translation it escapes as a vendor exception -- making `openai` part
    of this library's public failure contract by accident.
    """
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
        await provider.extract("Ada.", _Bag)

    assert caught.value.finish_reason == "length"


async def test_a_content_filter_refusal_becomes_a_distinct_domain_error():
    """The same openai parsing path, the other exception it can raise.

    `ContentFilterFinishReasonError` escaped untranslated, making `openai`
    part of this library's public failure contract in exactly the way the
    module docstring says translation prevents.

    Its own error rather than `EmptyCompletionError`, because the two call
    for opposite responses: a truncation is a configuration problem a larger
    budget fixes, while a refusal is a permanent property of the content and
    retrying it just spends tokens. A caller extracting from clinical or
    legal text needs to tell "this chunk was refused" from "this run was
    misconfigured", and one error type cannot say both.
    """
    from openai import ContentFilterFinishReasonError

    class RefusingChatModel(ScriptedChatModel):
        def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
            raise ContentFilterFinishReasonError

    provider = LangChainLlmProvider(
        RefusingChatModel(reply=AIMessage(content="{}")), model="test/scripted-v1"
    )

    with pytest.raises(RefusedCompletionError) as caught:
        await provider.extract("Ada.", _Bag)

    assert caught.value.model == "test/scripted-v1"


async def test_a_refusal_is_still_one_of_the_catchable_family():
    """A caller wrapping extraction keeps one `except`, and the pipeline's
    `skip_failed_chunks` keeps working without knowing the new type exists."""
    from openai import ContentFilterFinishReasonError

    class RefusingChatModel(ScriptedChatModel):
        def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
            raise ContentFilterFinishReasonError

    provider = LangChainLlmProvider(RefusingChatModel(reply=AIMessage(content="{}")), model="m")

    with pytest.raises(LlmProviderError):
        await provider.extract("Ada.", _Bag)


async def test_a_refusal_is_not_reported_as_an_empty_completion():
    """Guards the distinction: a shared base would make this pass vacuously."""
    from openai import ContentFilterFinishReasonError

    class RefusingChatModel(ScriptedChatModel):
        def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
            raise ContentFilterFinishReasonError

    provider = LangChainLlmProvider(RefusingChatModel(reply=AIMessage(content="{}")), model="m")

    with pytest.raises(RefusedCompletionError):
        await provider.extract("Ada.", _Bag)
    assert not issubclass(RefusedCompletionError, EmptyCompletionError)


async def test_whitespace_only_content_counts_as_empty():
    with pytest.raises(EmptyCompletionError):
        await provider_replying("  \n\t ").extract("Ada Lovelace.", _Bag)


async def test_content_that_is_not_json_raises_malformed_and_names_the_schema():
    with pytest.raises(MalformedCompletionError) as caught:
        await provider_replying("I'm sorry, I can't help with that.").extract("Ada.", _Bag)

    assert caught.value.schema == "_Bag"


async def test_json_of_the_wrong_shape_raises_malformed_and_not_a_partial_object():
    """Valid JSON, wrong schema. The failure most likely to pass silently.

    `entity_type` is missing, so a lenient adapter could produce
    `_Bag(entities=[])` -- valid, plausible, and wrong. Pinning the exception
    is what stops the "the document held nothing" reading.
    """
    with pytest.raises(MalformedCompletionError) as caught:
        await provider_replying('{"entities": [{"name": "Ada"}]}').extract("Ada.", _Bag)

    assert "entity_type" in caught.value.cause


async def test_both_failure_types_are_one_catchable_family():
    """A caller wrapping extraction needs one `except`, not a growing tuple."""
    for provider in (provider_replying(""), provider_replying("garbage")):
        with pytest.raises(LlmProviderError):
            await provider.extract("Ada.", _Bag)


async def test_content_delivered_as_text_blocks_is_joined_rather_than_rejected():
    """LangChain hands multimodal models a list of blocks instead of a string.

    An adapter that assumed `str` would raise `MalformedCompletionError` on a
    perfectly good completion, which reads as a model problem and is not one.
    """
    provider = provider_replying(
        [
            {"type": "text", "text": '{"entities": [{"name": "Ada",'},
            {"type": "text", "text": ' "entity_type": "Person"}]}'},
        ]
    )

    assert await provider.extract("Ada.", _Bag) == _Bag(
        entities=[_Ent(name="Ada", entity_type="Person")]
    )


async def test_a_block_list_holding_no_text_is_empty_not_malformed():
    """Reasoning-only output arrives this way, and it is the empty case.

    Calling it malformed would send someone to look at the prompt when the
    fix is a larger token budget.
    """
    with pytest.raises(EmptyCompletionError):
        await provider_replying([{"type": "reasoning", "reasoning": "thinking..."}]).extract(
            "Ada.", _Bag
        )


async def test_the_system_prompt_reaches_the_model_and_the_text_is_a_separate_turn():
    """Both halves matter.

    Concatenated into one user turn, instructions become indistinguishable
    from the document -- a document containing the words "ignore the above"
    would then be read as instruction.
    """
    chat = ScriptedChatModel(reply=AIMessage(content="{}"))
    await LangChainLlmProvider(chat, model="test/v1").extract(
        "Ada Lovelace.", _Bag, system_prompt="Find people."
    )

    [sent] = chat.seen_messages
    assert [(m.type, m.content) for m in sent] == [
        ("system", "Find people."),
        ("human", "Ada Lovelace."),
    ]


async def test_without_a_system_prompt_only_the_text_is_sent():
    """No default prompt is substituted: prompts are extraction's business.

    A provider inventing one would make two callers passing identical text
    get different answers for a reason neither could see.
    """
    chat = ScriptedChatModel(reply=AIMessage(content="{}"))
    await LangChainLlmProvider(chat, model="test/v1").extract("Ada Lovelace.", _Bag)

    [sent] = chat.seen_messages
    assert [(m.type, m.content) for m in sent] == [("human", "Ada Lovelace.")]


async def test_the_requested_schema_is_sent_as_a_json_schema_response_format():
    """Constraining the server beats parsing whatever prose comes back.

    Without this the malformed path is the *common* path rather than the
    exceptional one, and the adapter's error handling becomes the feature.
    """
    chat = ScriptedChatModel(reply=AIMessage(content="{}"))
    await LangChainLlmProvider(chat, model="test/v1").extract("Ada.", _Bag)

    response_format = chat.seen_kwargs["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "_Bag"
    assert response_format["json_schema"]["schema"] == _Bag.model_json_schema()


async def test_blank_text_is_refused_before_a_model_is_called():
    """Cheap, and it stops a chunker bug from being billed as a model failure.

    A blank prompt gets an unpredictable answer, which surfaces as an
    intermittent `MalformedCompletionError` far from the empty chunk.
    """
    chat = ScriptedChatModel(reply=AIMessage(content="{}"))
    provider = LangChainLlmProvider(chat, model="test/v1")

    with pytest.raises(ValueError, match="text must not be blank"):
        await provider.extract("   ", _Bag)

    assert chat.seen_messages == []
