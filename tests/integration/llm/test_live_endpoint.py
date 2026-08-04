"""The LangChain adapter against a real OpenAI-compatible server.

The unit tests script a `BaseChatModel` and so pin what the adapter does with
each response *shape*. Only this file can say those shapes are the ones a real
server produces. Run it deliberately::

    KG_LLM_BASE_URL=http://192.168.1.14:8080/v1 uv run pytest -m integration \\
        tests/integration/llm/

`-m integration` is required: `addopts` excludes the marker so the commit gate
needs no infrastructure.

## Why the skip probe asks for a completion

BACKLOG B12 is the standing example of the weaker check: the accuracy suite
probed Ollama's *model list*, the model was listed, it would not load, and
eight tests failed rather than skipping. A model list here is worse still --
this deployment is `llama-swap`, which lists every model it is configured for
whether or not the weights load. So the probe asks for a real completion and
requires non-empty content back, which is exactly the condition these tests
depend on.

The probe is deliberately given a generous token budget. A reasoning model
answers a one-word question in about 150 completion tokens, nearly all of them
chain of thought, and a stingy probe would skip the suite on a healthy server
-- reporting "no LLM here" for a model that is running perfectly.
"""

from __future__ import annotations

import os

import httpx
import pytest
from pydantic import BaseModel, Field

from kg_builder.domain.exceptions import EmptyCompletionError
from kg_builder.llm.adapters.langchain import LangChainLlmProvider

pytestmark = pytest.mark.integration

BASE_URL = os.environ.get("KG_LLM_BASE_URL", "http://192.168.1.14:8080/v1")
MODEL = os.environ.get("KG_LLM_MODEL", "qwen3.6-27b-mtp")

#: Enough for the chain of thought plus the answer. See the module docstring.
PROBE_MAX_TOKENS = 2000


class Person(BaseModel):
    name: str
    role: str | None = None


class People(BaseModel):
    people: list[Person] = Field(default_factory=list)


def serving() -> bool:
    """True when the endpoint answers a real completion with real content."""
    try:
        response = httpx.post(
            f"{BASE_URL}/chat/completions",
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": "Say the word OK and nothing else."}],
                "max_tokens": PROBE_MAX_TOKENS,
            },
            timeout=180.0,
        )
    except httpx.HTTPError:
        return False
    if response.status_code != httpx.codes.OK:
        return False
    choices = response.json().get("choices") or [{}]
    return bool((choices[0].get("message") or {}).get("content", "").strip())


@pytest.fixture(scope="module")
def live() -> None:
    if not serving():
        pytest.skip(f"no model serving at {BASE_URL} ({MODEL}); set KG_LLM_BASE_URL / KG_LLM_MODEL")


@pytest.fixture
def provider(live: None) -> LangChainLlmProvider:
    return LangChainLlmProvider.openai_compatible(base_url=BASE_URL, model=MODEL, api_key="local")


async def test_it_extracts_the_people_a_sentence_names(provider: LangChainLlmProvider):
    """Deliberately weak on *how* the model classifies, strict on the plumbing.

    Asserting a `role` string would be asserting the model's taste, which
    changes between versions and is the accuracy suite's job. What must hold
    here is that a real server, given a real schema, returns something that
    validates -- and that the names in it are the names in the sentence.
    """
    result = await provider.extract(
        "Ada Lovelace worked with Charles Babbage on the Analytical Engine.",
        People,
        system_prompt="Extract every person named in the text. Output only the schema.",
    )

    found = {person.name.casefold() for person in result.people}
    assert {"ada lovelace", "charles babbage"} <= found


async def test_the_provenance_string_names_the_real_model(provider: LangChainLlmProvider):
    assert provider.model == f"openai-compatible/{MODEL}"


async def test_a_starved_token_budget_raises_rather_than_extracting_nothing(live: None):
    """The verified `qwen3.6-27b-mtp` failure, reproduced against the server.

    This is the test the whole empty-content path exists for, and it is the
    one shape the unit tests cannot prove is real. With 16 tokens the model
    spends the budget inside `reasoning_content` and the server answers HTTP
    200 with `content` empty. The adapter must raise: a knowledge graph fed by
    an adapter that returned `People()` here loses every document whose
    extraction merely ran short, and reports nothing.
    """
    starved = LangChainLlmProvider.openai_compatible(
        base_url=BASE_URL, model=MODEL, api_key="local", max_tokens=16
    )

    with pytest.raises(EmptyCompletionError):
        await starved.extract(
            "Ada Lovelace worked with Charles Babbage.",
            People,
            system_prompt="Extract every person named in the text.",
        )
