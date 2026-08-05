"""The `LlmProvider` port: structured extraction from text, in domain terms.

One method, one job: hand it text and a pydantic schema, get back an instance
of that schema. Everything a chat API makes you think about -- messages,
roles, tool calls, token budgets, response formats, streaming -- is the
adapter's business and stops at this boundary.

## Why the port is this narrow

The library calls a model for exactly one purpose: turning prose into
structured data it can validate. A port shaped like a chat API would put
`AIMessage` and friends into every caller's signature, and LangChain's
interfaces move fast enough that a breaking change would then touch every
one. Shaped like this, it touches `redstring.llm.adapters` and nothing else.

**No `langchain*` type may appear in `domain/`, `ports/`, or any signature
outside `redstring/llm/adapters/`.** That is the whole point of the port,
and it is checked by `tests/unit/llm/test_port_does_not_leak.py`.

## `model` is provenance, not configuration

`Entity.model` records which artifact produced an entity, and that value
lands in a durable event log where "re-extract everything the old model
touched" has to stay answerable. The provider is the only thing that knows
its own identity, so it exposes it rather than making each caller pass a
string it might get wrong. Convention is provider-qualified and versioned --
`"ollama/qwen3.6-27b-mtp"`, not `"qwen"`.

## Empty output is an error, never an empty result

An extraction that returns nothing and an extraction that failed are
indistinguishable downstream, and the first is a legitimate answer ("this
document held no entities") while the second is a bug that would silently
erode a knowledge graph. So a provider that gets an empty or unparseable
completion raises -- `EmptyCompletionError` or `MalformedCompletionError` --
and only a *successfully parsed* schema instance with no entities in it means
"nothing here".

This is not hypothetical. The reference deployment serves a reasoning model
that emits its chain of thought into a separate `reasoning_content` field and
its answer into `content`; with too small a token budget the budget is spent
before `content` starts, and the server returns HTTP 200 with `content` empty.
See `redstring.llm.adapters.langchain`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pydantic import BaseModel


@runtime_checkable
class LlmProvider(Protocol):
    """Structured extraction from text against a caller-supplied schema."""

    @property
    def model(self) -> str:
        """Which model this provider speaks to, for `Entity.model` provenance."""
        ...

    async def extract[S: BaseModel](
        self,
        text: str,
        schema: type[S],
        *,
        system_prompt: str | None = None,
    ) -> S:
        """Extract an instance of `schema` from `text`.

        Args:
            text: The content to extract from.
            schema: The pydantic model the completion must validate against.
            system_prompt: Instructions for the model. A provider supplies no
                default of its own: prompts are extraction's business, and a
                provider quietly substituting one would make two callers
                passing the same text get different answers for reasons
                neither could see.

        Returns:
            An instance of `schema`. Never `None`, and never a partially
            populated object -- validation either succeeded or this raised.

        Raises:
            EmptyCompletionError: The model returned no usable content.
            MalformedCompletionError: Content came back but did not validate
                against `schema`.
        """
        ...
