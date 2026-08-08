"""The path from a domain schema to what the model is actually told.

BACKLOG B55 recorded the gap this closes: `domains/`, `prompt_generator` and
`classifier` were a real capability with no route into `ExtractionPipeline`.
The route turns out to need no new type at all -- the pipeline has taken
`system_prompt` since slice 6, and its docstring already said "a constructor
argument because domain schemas supply their own". `domain_system_prompt` is
the missing one-line join.

## Why the assertions land on the provider rather than on the pipeline

`ExtractionPipeline.system_prompt` is a readable property, so the cheap test
is `pipeline.system_prompt == domain_system_prompt("literature_fiction")`. A
pipeline that stored the argument and then sent `DEFAULT_SYSTEM_PROMPT` to the
model would pass it, and that is precisely the defect worth catching -- the
prompt is only worth generating if it reaches the model. So the provider here
records what it was told, and the assertion is on the recording.

`RecordingProvider` is a real `LlmProvider`, not a mock: it validates its
canned payload against the caller's schema exactly as `FakeLlmProvider` does,
by delegating to one.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

import redstring
from redstring import RedstringError, UnknownDomainError
from redstring.domain.source import SourceDocument
from redstring.extraction import (
    DEFAULT_SYSTEM_PROMPT,
    ExtractionPipeline,
    domain_system_prompt,
)
from redstring.extraction.domains import get_domain_schema
from redstring.llm.adapters.fake import FakeLlmProvider

TENANT_ID = uuid4()


class RecordingProvider:
    """A `FakeLlmProvider` that also remembers every system prompt it got."""

    def __init__(self) -> None:
        self._inner = FakeLlmProvider(by_substring={})
        self.system_prompts: list[str | None] = []

    @property
    def model(self) -> str:
        return self._inner.model

    async def extract(self, text, schema, *, system_prompt=None):
        self.system_prompts.append(system_prompt)
        return await self._inner.extract(text, schema, system_prompt=system_prompt)


class TestDomainSystemPrompt:
    def test_fills_in_the_schema_s_entity_and_relationship_descriptions(self) -> None:
        schema = get_domain_schema("literature_fiction")
        prompt = domain_system_prompt("literature_fiction")

        assert "{entity_descriptions}" not in prompt
        assert "{relationship_descriptions}" not in prompt
        for entity_type in schema.entity_types:
            assert entity_type.id in prompt
            assert entity_type.description in prompt

    def test_two_domains_do_not_get_the_same_prompt(self) -> None:
        # A `domain_system_prompt` that ignored its argument and returned the
        # template, or the default, would pass every assertion above.
        assert domain_system_prompt("literature_fiction") != domain_system_prompt(
            "academic_research"
        )
        assert domain_system_prompt("literature_fiction") != DEFAULT_SYSTEM_PROMPT

    def test_accepts_a_schema_object_as_well_as_an_id(self) -> None:
        schema = get_domain_schema("news_journalism")
        assert domain_system_prompt(schema) == domain_system_prompt("news_journalism")

    def test_an_unknown_domain_names_the_ones_that_exist(self) -> None:
        with pytest.raises(UnknownDomainError, match="literature_fiction"):
            domain_system_prompt("underwater_basket_weaving")

    def test_an_unknown_domain_is_catchable_as_a_redstring_error(self) -> None:
        # `RedstringError` is documented as the base of every error this
        # library raises deliberately, and `domain_system_prompt` is public.
        # A bare `KeyError` from the internal registry escaping through it
        # would make that promise false for the one public function most
        # likely to be handed a typo.
        with pytest.raises(RedstringError):
            domain_system_prompt("underwater_basket_weaving")


class TestTheDomainPromptReachesTheModel:
    async def test_every_chunk_is_asked_with_the_domain_prompt(self) -> None:
        provider = RecordingProvider()
        pipeline = ExtractionPipeline(
            provider, system_prompt=domain_system_prompt("literature_fiction")
        )

        await pipeline.extract(SourceDocument(id="doc-1", text="Hamlet met Ophelia."), TENANT_ID)

        assert provider.system_prompts, "the pipeline made no model call at all"
        assert set(provider.system_prompts) == {domain_system_prompt("literature_fiction")}

    async def test_without_a_domain_the_pipeline_still_sends_its_default(self) -> None:
        provider = RecordingProvider()
        pipeline = ExtractionPipeline(provider)

        await pipeline.extract(SourceDocument(id="doc-1", text="Hamlet met Ophelia."), TENANT_ID)

        assert set(provider.system_prompts) == {DEFAULT_SYSTEM_PROMPT}


class TestTheBundledDomainsAreDiscoverable:
    """`list_available_domains` must agree with what the prompt accepts.

    Exporting a listing is only worth anything if it is the *same* set the
    function it serves will take. Two independent sources for "which domains
    exist" is `.claude/rules/recurring-defects.md` §2 -- one silently wins and
    the loser looks authoritative -- so these assert the listing against both
    doors into the registry: the success path and the error path.

    Before this was exported the supported way to discover a domain id was to
    pass a wrong one and read `UnknownDomainError.available`, which is why
    that attribute is the oracle here rather than a hand-written list of six
    names. A list written here would agree with the code by construction and
    could not fail.
    """

    def test_every_listed_domain_is_one_the_prompt_accepts(self) -> None:
        listed = redstring.list_available_domains()

        assert listed, "the listing is empty, so nothing below can fail"
        for summary in listed:
            assert domain_system_prompt(summary.domain_id)

    def test_the_listing_is_exactly_what_the_error_offers(self) -> None:
        with pytest.raises(UnknownDomainError) as caught:
            domain_system_prompt("underwater_basket_weaving")

        assert {s.domain_id for s in redstring.list_available_domains()} == set(
            caught.value.available
        )

    def test_a_summary_says_what_the_domain_is_for(self) -> None:
        # A listing of bare ids would satisfy the two tests above while
        # leaving a caller no way to choose between six of them.
        summary = redstring.list_available_domains()[0]

        assert summary.display_name
        assert summary.description
        assert summary.entity_type_count == len(summary.entity_types)
        assert summary.relationship_type_count == len(summary.relationship_types)
