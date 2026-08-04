"""`LlmProvider` over a LangChain `BaseChatModel`.

## The whole LangChain surface, deliberately

Constructing a chat model, and `await chat.ainvoke(messages, response_format=)`.
That is it. `with_structured_output` would have been the idiomatic choice and
was rejected: it decides the parsing strategy, swallows the raw message, and
turns a truncated completion into an `openai.LengthFinishReasonError` raised
from inside the openai SDK -- three behaviours that would each have to be
re-learned on every LangChain minor. Assembling the `response_format` and
validating the content here costs a dozen lines and leaves the failure modes
ours to name.

## What `qwen3.6-27b-mtp` actually returns, and why the empty check exists

Verified against the reference deployment at the time of writing:

```
{"choices":[{"finish_reason":"length","index":0,
  "message":{"role":"assistant","content":"",
             "reasoning_content":"Here's a thinking process: ..."}}]}
```

It is a reasoning model. The chain of thought goes to `reasoning_content` and
the answer to `content`, so a token budget that runs out during the reasoning
returns **HTTP 200 with `content` empty** -- roughly 150 completion tokens
were needed for a one-word answer. Given room, `content` is populated
normally and `response_format: json_schema` is honoured exactly.

So `content` is the right field to read, but reading it is not enough: an
adapter that mapped empty content onto an empty extraction would report "this
document contained nothing" for every document whose extraction merely ran out
of budget, and nothing downstream could tell the two apart. Hence
`EmptyCompletionError`, carrying `finish_reason` because `"length"` and
`"stop"` call for different fixes.

`DEFAULT_MAX_TOKENS` is generous for the same reason: the cost of a too-small
budget is a failed run, and the cost of a too-large one is nothing at all
unless the model actually uses it.

## Truncation never reaches this module, and has to be caught anyway

Found by `tests/integration/llm/test_live_endpoint.py` against the real
server, and not findable any other way. Asking for `response_format` of type
`json_schema` routes `langchain-openai` through the openai SDK's *parsing*
code path, and that path raises `openai.LengthFinishReasonError` for
`finish_reason == "length"` before returning a message at all. So the empty
check below sees only the `"stop"`-with-empty-content case, and truncation --
the more common of the two, and the one the reference model reaches first --
would otherwise escape as a vendor exception from three frames down.

That path raises a second exception too, `ContentFilterFinishReasonError`,
and it is translated to `RefusedCompletionError` rather than to the same
error. A truncation is a configuration problem a larger budget fixes; a
refusal is a permanent property of the content, and retrying it only spends
tokens. Both are `LlmProviderError`, so a caller wanting one `except` still
has one -- what is preserved is the ability to tell them apart when it
matters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError

from kg_builder.domain.exceptions import (
    EmptyCompletionError,
    MalformedCompletionError,
    RefusedCompletionError,
)

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import BaseMessage
    from pydantic import BaseModel

#: Room for a reasoning model to think and *then* answer. See the module docstring.
DEFAULT_MAX_TOKENS: Final = 8192


class _NeverRaised(Exception):
    """Stands in for a vendor exception class that is not installed.

    `except _NeverRaised` is a no-op, so the translation below degrades to
    "this transport cannot truncate that way" rather than to an `ImportError`
    at module import. A provider wrapping a non-OpenAI chat model must not
    need `openai` installed to be constructed.
    """


try:  # pragma: no cover -- the except branch needs langchain-openai absent
    from openai import ContentFilterFinishReasonError as _RefusedError
    from openai import LengthFinishReasonError as _TruncatedError
except ImportError:  # pragma: no cover
    _TruncatedError = _NeverRaised  # type: ignore[assignment, misc]
    _RefusedError = _NeverRaised  # type: ignore[assignment, misc]


def _text_of(content: str | list[str | dict[str, Any]]) -> str:
    """Flatten LangChain message content to the text a schema can be parsed from.

    Content is a `str` for most models and a list of blocks for multimodal and
    reasoning ones. Non-text blocks -- above all `reasoning` -- contribute
    nothing: they are what the model thought, not what it answered, and
    concatenating them would turn a valid completion into unparseable prose.
    """
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif block.get("type") == "text" and isinstance(text := block.get("text"), str):
            parts.append(text)
    return "".join(parts)


class LangChainLlmProvider:
    """Structured extraction through any LangChain chat model."""

    def __init__(self, chat: BaseChatModel, *, model: str) -> None:
        """Wrap an already-configured chat model.

        Args:
            chat: The LangChain model. Constructed by the caller rather than
                here, so that a deployment's own retry, callback and tracing
                configuration is not something this class has to mirror.
            model: The provenance string recorded on every entity extracted
                through this provider. Passed in rather than read off `chat`
                because the convention is provider-qualified and versioned
                (`"ollama/qwen3.6-27b-mtp"`) and no chat model knows which
                provider is in front of it. See `openai_compatible`.
        """
        self._chat = chat
        self._model = model

    @classmethod
    def openai_compatible(
        cls,
        *,
        base_url: str,
        model: str,
        api_key: str = "not-needed",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.0,
        provider: str = "openai-compatible",
    ) -> LangChainLlmProvider:
        """Build a provider against any OpenAI-compatible server.

        Covers llama.cpp, llama-swap, vLLM, Ollama's OpenAI shim and OpenAI
        itself -- one adapter, because they agree on the wire format that
        matters here.

        Args:
            base_url: The `/v1` root, e.g. `"http://192.168.1.14:8080/v1"`.
            model: The server's model id, e.g. `"qwen3.6-27b-mtp"`.
            api_key: Sent as a bearer token. Local servers ignore it but the
                OpenAI client refuses to start without one, so it defaults to
                a value that is visibly not a secret rather than to `""`.
            max_tokens: See `DEFAULT_MAX_TOKENS` -- too small is a failed run.
            temperature: Zero by default. Extraction wants the same entities
                from the same document twice; sampling variety is a cost here
                with no compensating benefit.
            provider: Prefix for the provenance string, giving
                `f"{provider}/{model}"`.

        Note:
            Deliberately not a passthrough for arbitrary `ChatOpenAI` kwargs.
            A caller needing callbacks, proxies or a custom `http_client` can
            build the chat model itself and use `__init__`, which keeps this
            convenience constructor from slowly becoming a second, worse copy
            of `ChatOpenAI`'s signature.

        Raises:
            ImportError: `langchain-openai` is not installed. Raised with the
                extra to install, because the alternative is a bare
                `ModuleNotFoundError` from three frames down.
        """
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as error:  # pragma: no cover -- needs the extra absent
            raise ImportError(
                "LangChainLlmProvider.openai_compatible needs langchain-openai; "
                "install kg-builder[llm]"
            ) from error

        # `model`, `base_url` and `api_key` are pydantic *aliases* on
        # ChatOpenAI (for `model_name`, `openai_api_base`, `openai_api_key`),
        # and they are the names LangChain's own documentation uses. The
        # pydantic mypy plugin runs with `init_forbid_extra`, which cannot see
        # `populate_by_name` aliases and so reads all three as extra kwargs.
        # Spelling the private names instead would type-check and would be
        # the wrong code: they are undocumented and have already been renamed
        # once.
        chat = ChatOpenAI(  # type: ignore[call-arg]
            model=model,
            base_url=base_url,
            api_key=api_key,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return cls(chat, model=f"{provider}/{model}")

    @property
    def model(self) -> str:
        return self._model

    async def extract[S: BaseModel](
        self,
        text: str,
        schema: type[S],
        *,
        system_prompt: str | None = None,
    ) -> S:
        """See `kg_builder.ports.llm_provider.LlmProvider.extract`."""
        if not text.strip():
            raise ValueError("text must not be blank")

        messages: list[tuple[str, str]] = []
        if system_prompt is not None:
            messages.append(("system", system_prompt))
        messages.append(("human", text))

        try:
            reply = await self._chat.ainvoke(messages, response_format=_response_format(schema))
        except _TruncatedError as error:
            # The openai SDK's parsing path refuses a truncated completion
            # before returning a message, so `_parse` never sees this one.
            raise EmptyCompletionError(model=self._model, finish_reason="length") from error
        except _RefusedError as error:
            # The same path, the other exception it raises. Distinct from the
            # truncation because the fixes are opposite: a bigger budget vs.
            # not sending this content again.
            raise RefusedCompletionError(model=self._model) from error
        return self._parse(reply, schema)

    def _parse[S: BaseModel](self, reply: BaseMessage, schema: type[S]) -> S:
        content = _text_of(reply.content)
        if not content.strip():
            raise EmptyCompletionError(
                model=self._model,
                finish_reason=reply.response_metadata.get("finish_reason"),
            )
        try:
            return schema.model_validate_json(content)
        except ValidationError as error:
            raise MalformedCompletionError(
                model=self._model, schema=schema.__name__, cause=str(error)
            ) from error


def _response_format(schema: type[BaseModel]) -> dict[str, Any]:
    """The OpenAI structured-output request for `schema`.

    `strict` asks the server to constrain decoding to the grammar rather than
    merely prompt for it, which is the difference between the malformed path
    being exceptional and being routine. Servers that do not support it ignore
    the key; the validation in `_parse` is the backstop either way.
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema.__name__,
            "strict": True,
            "schema": schema.model_json_schema(),
        },
    }
