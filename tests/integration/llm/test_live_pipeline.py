"""The whole pipeline against the real model: chunk, extract, merge, emit.

The unit tests run every path in `ExtractionPipeline` against
`FakeLlmProvider`, whose answers are canned. What they cannot show is that a
real model, given `Extraction`'s JSON schema and the default system prompt,
returns something the mapper can turn into a connected graph. That is what
this file is for, and it is why the assertions here are about *structure*
rather than about which entities a model chose -- the latter changes between
model versions and is the accuracy suite's concern.

Skipped unless a probe gets a real non-empty completion; see
`test_live_endpoint.py` for why a model listing is not enough.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from redstring.aggregates.document import Document
from redstring.domain.entity import ExtractionMethod
from redstring.domain.source import SourceDocument
from redstring.events import DocumentExtracted
from redstring.events.streams import document_stream
from redstring.extraction.chunkers import SlidingWindowChunker
from redstring.extraction.pipeline import ExtractionPipeline
from redstring.llm.adapters.langchain import LangChainLlmProvider
from tests.integration.llm.test_live_endpoint import BASE_URL, MODEL, serving

pytestmark = [pytest.mark.integration, pytest.mark.live]

PASSAGE = (
    "Ada Lovelace was an English mathematician. She worked with Charles "
    "Babbage on the Analytical Engine, a mechanical general-purpose "
    "computer he designed. Lovelace wrote the first algorithm intended to "
    "be carried out by such a machine."
)


@pytest.fixture(scope="module")
def live() -> None:
    if not serving():
        pytest.skip(f"no model serving at {BASE_URL} ({MODEL})")


@pytest.fixture
def pipeline(live: None) -> ExtractionPipeline:
    return ExtractionPipeline(
        LangChainLlmProvider.openai_compatible(base_url=BASE_URL, model=MODEL, api_key="local")
    )


@pytest.fixture
def tenant_id():
    return uuid4()


async def test_a_real_passage_yields_the_people_it_names(pipeline, tenant_id):
    """Weak on taxonomy, strict on presence.

    Which `entity_type` a model assigns to Ada Lovelace is its taste and
    changes between versions. That she is in the output at all is the claim
    the pipeline makes.
    """
    result = await pipeline.extract(SourceDocument(id="ada", text=PASSAGE), tenant_id)

    found = {e.normalized_name for e in result.entities}
    assert "ada lovelace" in found
    assert "charles babbage" in found


async def test_the_entities_are_domain_objects_with_real_provenance(pipeline, tenant_id):
    result = await pipeline.extract(SourceDocument(id="ada", text=PASSAGE), tenant_id)

    for extracted in result.entities:
        assert extracted.tenant_id == tenant_id
        assert extracted.source_id == "ada"
        assert extracted.extraction_method is ExtractionMethod.LLM
        assert extracted.model == f"openai-compatible/{MODEL}"


async def test_a_real_model_produces_edges_whose_endpoints_actually_resolve(pipeline, tenant_id):
    """The assertion this whole file exists for.

    The default system prompt tells the model that every endpoint must be an
    entity it also listed, because unresolved endpoints are the largest
    source of dropped edges. If a real model ignores that instruction, every
    relationship lands in `unresolved_relationships` and the graph is a pile
    of disconnected nodes -- which no unit test can detect, because the fake
    answers with whatever the test wrote.
    """
    result = await pipeline.extract(SourceDocument(id="ada", text=PASSAGE), tenant_id)
    present = {e.id for e in result.entities}

    assert result.relationships, (
        f"a real model produced no usable edges: "
        f"{result.unresolved_relationships} unresolved, {result.self_loops} self-loops"
    )
    for edge in result.relationships:
        assert edge.source_entity_id in present
        assert edge.target_entity_id in present


async def test_a_chunked_document_merges_back_into_one_set_of_entities(live, tenant_id):
    """Chunk overlap really does report the same entity twice, and it survives once.

    Run against a chunker small enough to split this passage, so the
    deduplication is exercised by the model's real, inconsistent output rather
    than by canned identical answers.
    """
    chunker = SlidingWindowChunker(default_chunk_size=150, default_overlap=50)
    pipeline = ExtractionPipeline(
        LangChainLlmProvider.openai_compatible(base_url=BASE_URL, model=MODEL, api_key="local"),
        chunker=chunker,
    )

    # Guards the test: on one chunk there is no deduplication to observe and
    # the assertion below would hold trivially.
    assert chunker.chunk(PASSAGE).total_chunks > 1

    result = await pipeline.extract(SourceDocument(id="ada", text=PASSAGE), tenant_id)

    ids = [e.id for e in result.entities]
    assert len(ids) == len(set(ids))


async def test_recording_produces_one_event_the_domain_accepts(pipeline, tenant_id):
    """`DocumentExtracted` validates tenants and source attribution on construction.

    So this passes only if every entity the real model produced was mapped
    with the right tenant and the right `source_id` -- the validator is the
    assertion.
    """
    aggregate = Document(document_stream(tenant_id=tenant_id, source_id="ada").aggregate_id)

    event = await pipeline.record(aggregate, SourceDocument(id="ada", text=PASSAGE), tenant_id)

    assert isinstance(event, DocumentExtracted)
    assert event.model_version == f"openai-compatible/{MODEL}"
    assert event.entities
