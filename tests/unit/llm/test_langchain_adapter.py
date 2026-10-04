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

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel, Field

from redstring.llm.adapters.langchain import NO_THINKING, LangChainLlmProvider
from redstring.ports.llm_provider import LlmProvider

if TYPE_CHECKING:
    from collections.abc import Sequence


class Ent(BaseModel):
    name: str
    entity_type: str


class Bag(BaseModel):
    entities: list[Ent] = Field(default_factory=list)


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

    assert await provider.extract("Ada Lovelace.", Bag) == Bag(
        entities=[Ent(name="Ada", entity_type="Person")]
    )


async def test_a_completion_that_validates_to_nothing_is_an_answer_not_an_error():
    """The one case that must NOT raise, and the reason the others must.

    A document genuinely holding no entities is a real outcome. It is
    distinguishable from failure only because every failure raises, so this
    test and the empty-content test below are two halves of one claim.
    """
    assert await provider_replying('{"entities": []}').extract("Nothing here.", Bag) == Bag()


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

    assert await provider.extract("Ada.", Bag) == Bag(
        entities=[Ent(name="Ada", entity_type="Person")]
    )


async def test_the_system_prompt_reaches_the_model_and_the_text_is_a_separate_turn():
    """Both halves matter.

    Concatenated into one user turn, instructions become indistinguishable
    from the document -- a document containing the words "ignore the above"
    would then be read as instruction.
    """
    chat = ScriptedChatModel(reply=AIMessage(content="{}"))
    await LangChainLlmProvider(chat, model="test/v1").extract(
        "Ada Lovelace.", Bag, system_prompt="Find people."
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
    await LangChainLlmProvider(chat, model="test/v1").extract("Ada Lovelace.", Bag)

    [sent] = chat.seen_messages
    assert [(m.type, m.content) for m in sent] == [("human", "Ada Lovelace.")]


async def test_the_requested_schema_is_sent_as_a_json_schema_response_format():
    """Constraining the server beats parsing whatever prose comes back.

    Without this the malformed path is the *common* path rather than the
    exceptional one, and the adapter's error handling becomes the feature.
    """
    chat = ScriptedChatModel(reply=AIMessage(content="{}"))
    await LangChainLlmProvider(chat, model="test/v1").extract("Ada.", Bag)

    response_format = chat.seen_kwargs["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "Bag"
    assert response_format["json_schema"]["schema"] == Bag.model_json_schema()


class TestTheConvenienceConstructorsRequestBody:
    """What `openai_compatible` puts on the wire, short of putting it there.

    These read the constructed `ChatOpenAI`'s own configuration rather than
    intercepting a request, which is the most that can be pinned without a
    server. `tests/integration/llm/test_live_endpoint.py` is what proves a
    real server honours it; this is what proves we sent it, and neither is
    sufficient alone -- a default that never reaches the client and a server
    that ignores the field look identical from the outside.
    """

    def test_thinking_is_off_by_default(self):
        """The reversal of the server's own default, pinned.

        Extraction is not a reasoning task, and on the reference model the
        difference is 2789 completion tokens against 261. See `NO_THINKING`.
        """
        provider = LangChainLlmProvider.openai_compatible(
            base_url="http://localhost:8080/v1", model="qwen3.6-27b-mtp"
        )

        assert provider._chat.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}

    def test_asking_for_thinking_sends_nothing_rather_than_the_opposite_flag(self):
        """`thinking=True` restores the *server's* default, which is not the
        same as asserting `enable_thinking: true`.

        A backend with no chat template to pass kwargs to -- OpenAI's own API
        -- rejects the field whichever value it carries, so the escape hatch
        has to be an absent field rather than an inverted one. Sending
        `{"enable_thinking": True}` would pass a test that only checked the
        flag flipped, and would still 400.
        """
        provider = LangChainLlmProvider.openai_compatible(
            base_url="http://localhost:8080/v1", model="qwen3.6-27b-mtp", thinking=True
        )

        assert provider._chat.extra_body is None

    def test_the_module_constant_cannot_be_reconfigured_through_a_provider(self):
        """`NO_THINKING` is module-level and mutable, so aliasing it into a
        client would let one caller's edit reconfigure every provider in the
        process -- including ones built before the change.

        **Honest about what this pins.** `ChatOpenAI` is a pydantic model and
        validation copies `extra_body`, so this passes whether or not
        `openai_compatible` copies it as well -- removing the `dict(...)` was
        tried, and this test did not notice. It is kept because the invariant
        is real and depends on a *third-party* behaviour nothing else here
        states: it fails if pydantic stops copying, which is the case where
        the `dict(...)` starts being what saves us.
        """
        provider = LangChainLlmProvider.openai_compatible(
            base_url="http://localhost:8080/v1", model="a"
        )

        provider._chat.extra_body["chat_template_kwargs"] = {"enable_thinking": True}

        assert NO_THINKING == {"chat_template_kwargs": {"enable_thinking": False}}
        other = LangChainLlmProvider.openai_compatible(
            base_url="http://localhost:8080/v1", model="b"
        )
        assert other._chat.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}

    def test_building_the_chat_model_yourself_is_untouched(self):
        """`__init__` takes a caller's own chat model and must not edit it.

        The escape hatch the constructor's docstring points at has to stay
        exactly what the caller configured, or "build it yourself" stops being
        an answer to anything.
        """
        scripted = ScriptedChatModel(reply=AIMessage(content='{"entities": []}'))

        provider = LangChainLlmProvider(scripted, model="test/v1")

        assert provider._chat is scripted
