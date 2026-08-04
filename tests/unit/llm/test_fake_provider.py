"""The fake provider is a real `LlmProvider`, and these tests say what that means.

A fake that hands back whatever the test asked for proves nothing about
extraction, so the two things that make this one honest are pinned here:
it takes **payload dicts** and validates them against the caller's schema
exactly as the LangChain adapter does, and it raises the same errors on the
same conditions. A test cannot smuggle a pre-built schema instance past
validation, which is what would let a malformed-output test pass vacuously.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from kg_builder.domain.exceptions import EmptyCompletionError, MalformedCompletionError
from kg_builder.llm.adapters.fake import EMPTY, FakeLlmProvider
from kg_builder.ports.llm_provider import LlmProvider


class _Thing(BaseModel):
    name: str
    weight: int


class _Bag(BaseModel):
    things: list[_Thing] = Field(default_factory=list)


def test_the_fake_satisfies_the_port():
    assert isinstance(FakeLlmProvider(script=[{}]), LlmProvider)


def test_it_reports_the_model_it_was_built_with():
    assert FakeLlmProvider(model="fake/v9", script=[{}]).model == "fake/v9"


async def test_a_scripted_payload_comes_back_as_a_validated_schema_instance():
    provider = FakeLlmProvider(script=[{"things": [{"name": "anvil", "weight": 200}]}])

    result = await provider.extract("anything", _Bag)

    assert result == _Bag(things=[_Thing(name="anvil", weight=200)])


async def test_a_payload_that_does_not_fit_the_schema_raises_rather_than_returning_partial():
    """The failure path the brief asks the fake to be able to exercise.

    `weight` is a string here, and the point is that the fake refuses -- a
    fake that returned `_Bag(things=[])` instead would make every
    malformed-output test in extraction pass without extraction handling
    anything.
    """
    provider = FakeLlmProvider(model="fake/v1", script=[{"things": [{"name": "anvil"}]}])

    with pytest.raises(MalformedCompletionError) as caught:
        await provider.extract("anything", _Bag)

    assert caught.value.model == "fake/v1"
    assert caught.value.schema == "_Bag"


async def test_the_empty_sentinel_raises_the_empty_error():
    provider = FakeLlmProvider(model="fake/v1", script=[EMPTY])

    with pytest.raises(EmptyCompletionError):
        await provider.extract("anything", _Bag)


async def test_a_script_is_consumed_in_order():
    provider = FakeLlmProvider(
        script=[
            {"things": [{"name": "first", "weight": 1}]},
            {"things": [{"name": "second", "weight": 2}]},
        ]
    )

    first = await provider.extract("a", _Bag)
    second = await provider.extract("b", _Bag)

    assert [t.name for t in first.things] == ["first"]
    assert [t.name for t in second.things] == ["second"]


async def test_exhausting_the_script_raises_instead_of_repeating_the_last_answer():
    """A test that calls more often than it scripted has a bug, not a default.

    Repeating the last entry would let a chunking test that produces three
    chunks pass while scripting two, and the assertion about the third
    chunk's entities would be about the second chunk's.
    """
    provider = FakeLlmProvider(script=[{}])
    await provider.extract("a", _Bag)

    with pytest.raises(AssertionError, match="script of 1"):
        await provider.extract("b", _Bag)


async def test_a_content_addressed_fake_answers_according_to_the_text_it_is_given():
    """Content-addressing is what makes an order-independence test mean anything.

    With a positional script, permuting the chunks permutes which answer each
    chunk gets, so "dedup is order-independent" would hold for a merge that
    simply ignored order-sensitive input. Keyed on text, each chunk keeps its
    own entities however the chunks are shuffled.
    """
    provider = FakeLlmProvider(
        by_substring={
            "anvil": {"things": [{"name": "anvil", "weight": 200}]},
            "feather": {"things": [{"name": "feather", "weight": 1}]},
        }
    )

    heavy = await provider.extract("a crate holding one anvil", _Bag)
    light = await provider.extract("a crate holding one feather", _Bag)

    assert [t.name for t in heavy.things] == ["anvil"]
    assert [t.name for t in light.things] == ["feather"]


async def test_unmatched_text_yields_the_default_which_is_a_valid_empty_result():
    """ "Nothing here" must be expressible: it is a legitimate extraction outcome.

    Distinct from `EMPTY`, which is the transport failing. This is the model
    successfully answering "no entities", and the two must not collapse.
    """
    provider = FakeLlmProvider(by_substring={"anvil": {"things": [{"name": "a", "weight": 1}]}})

    assert await provider.extract("a crate holding nothing at all", _Bag) == _Bag(things=[])


async def test_the_system_prompt_is_accepted_and_changes_no_answer():
    """The port takes a system prompt; the fake must not become prompt-sensitive.

    Asserting the prompt was *passed* would be a mock assertion. Asserting it
    does not perturb the answer is the property extraction tests rely on.
    """
    provider = FakeLlmProvider(by_substring={"anvil": {"things": [{"name": "anvil", "weight": 2}]}})

    without = await provider.extract("one anvil", _Bag)
    with_prompt = await provider.extract("one anvil", _Bag, system_prompt="Find only birds.")

    assert without == with_prompt


def test_a_fake_must_be_given_one_behaviour_and_not_both():
    with pytest.raises(ValueError, match="exactly one"):
        FakeLlmProvider(script=[{}], by_substring={"x": {}})


def test_a_fake_with_no_behaviour_at_all_is_refused():
    with pytest.raises(ValueError, match="exactly one"):
        FakeLlmProvider()
