"""Extraction accuracy against a live model, over the graded corpus.

    KG_LLM_BASE_URL=http://host:8080/v1 uv run pytest -m accuracy tests/accuracy/

`-m accuracy` is required: `addopts` deselects the marker, so nothing here runs
on commit.

## What a number from this suite means, and what it does not

Five hand-graded documents. That is enough to answer "is extraction working"
and not enough to answer "how well" — see the header of `corpus.yaml` for the
grading rules and the honest limits. **Do not quote an F1 from this suite as a
benchmark figure.** The floors asserted below are deliberately low: they are
set where a real regression trips them, not where a good model sits, because a
floor tuned to the current model becomes a test of the model rather than of the
library the first time anyone changes the endpoint.

## Why the probe asks for a completion

This suite is the origin of that rule. Its predecessor probed Ollama's *model
list*, the model was listed, the weights would not load, and eight tests failed
instead of skipping (`BACKLOG.md` B12). A model list is worse than useless
against a swapping proxy, which lists every model it is configured for whether
or not it can serve one. So the probe runs a real extraction through the real
provider and requires a real entity back.

## Why the metric is not tested here

`tests/unit/accuracy/` holds the tests for the scorer and for the harness, and
both run in the commit gate against `FakeLlmProvider`. That split is what makes
this module's output believable: if the harness could silently measure nothing,
its F1 of 0.0 would read as a bad model, and if it could silently compare the
corpus against itself, its 1.0 would read as a good one. Those two possibilities
are excluded before this file runs.
"""

from __future__ import annotations

import os

import pytest

from redstring.llm.adapters.langchain import LangChainLlmProvider
from tests.accuracy.corpus import load_corpus
from tests.accuracy.runner import run_corpus

pytestmark = pytest.mark.accuracy

BASE_URL = os.environ.get("KG_LLM_BASE_URL", "http://192.168.1.14:8080/v1")
MODEL = os.environ.get("KG_LLM_MODEL", "qwen3.6-27b-mtp")

#: Floors, not targets. Set where a regression trips them, not where a good
#: model sits. Raising these to track a particular endpoint turns the suite
#: into a test of that endpoint.
MIN_ENTITY_RECALL = 0.5
MIN_ENTITY_PRECISION = 0.3
MIN_RELATIONSHIP_RECALL = 0.2


def _provider() -> LangChainLlmProvider:
    return LangChainLlmProvider(model=MODEL, base_url=BASE_URL, api_key="not-needed")


async def _serving() -> bool:
    """True when the endpoint completes a real extraction.

    Runs the corpus's own first document rather than a synthetic ping, so the
    probe exercises the same call shape the suite depends on — a server that
    answers chat but fails structured output skips rather than failing every
    test with the same traceback.
    """
    try:
        result = await run_corpus(load_corpus()[:1], provider=_provider())
    except Exception:
        return False
    entities = result.entities
    return entities.true_positives + entities.false_positives > 0


@pytest.fixture(scope="module")
async def corpus_result():
    """Run the whole corpus once and share the result across assertions.

    Module-scoped because each document is a model call and the assertions
    below are readings of one run, not independent experiments. Sharing state
    across tests is normally a bug in this repo; here the state is immutable
    and the alternative is running the corpus once per assertion.
    """
    if not await _serving():
        pytest.skip(
            f"no model completed an extraction at {BASE_URL} (model {MODEL}). "
            f"Set KG_LLM_BASE_URL and KG_LLM_MODEL."
        )
    result = await run_corpus(load_corpus(), provider=_provider())
    print("\n" + result.report())
    return result


async def test_entity_recall_is_above_the_floor(corpus_result):
    assert corpus_result.entities.recall >= MIN_ENTITY_RECALL, (
        f"entity recall regressed:\n{corpus_result.report()}"
    )


async def test_entity_precision_is_above_the_floor(corpus_result):
    assert corpus_result.entities.precision >= MIN_ENTITY_PRECISION, (
        f"entity precision regressed:\n{corpus_result.report()}"
    )


async def test_relationship_recall_is_above_the_floor(corpus_result):
    """Lower than the entity floor, deliberately.

    Relationship extraction is strictly harder — it needs both endpoints found
    *and* linked under the right type — and holding it to the entity floor
    would make the suite fail for a reason it is not measuring.
    """
    assert corpus_result.relationships.recall >= MIN_RELATIONSHIP_RECALL, (
        f"relationship recall regressed:\n{corpus_result.report()}"
    )


async def test_the_negative_document_extracts_nothing(corpus_result):
    """The one document that can detect hallucination rather than reward recall.

    `empty-negative` states nothing extractable, so anything returned for it is
    invented. Asserted separately from corpus precision because it would
    otherwise be four false positives diluted across a corpus that is mostly
    rewarding recall — visible in the total only as a slightly lower number,
    and not attributable to anything.
    """
    negative = next(d for d in corpus_result.documents if d.document_id == "empty-negative")

    assert negative.entities.false_positives == 0, (
        f"entities invented for a document that states none: {negative.entities}"
    )
