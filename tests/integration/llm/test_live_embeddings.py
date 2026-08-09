"""`LangChainEmbeddingProvider` against a real embeddings endpoint.

    KG_EMBED_BASE_URL=http://host:8080/v1 uv run pytest -m integration \\
        tests/integration/llm/test_live_embeddings.py

**This file exists because the stubbed suite was wrong and nothing else could
tell.** The compliance body was written asserting `==` for "the same text gives
the same vector" and for positional order, and it passed — against a hash and
against a stub, both of which are exactly reproducible. The first run against
llama.cpp failed both clauses.

Not because the adapter was broken. Embedding the same text alone and inside a
batch of four gives vectors differing by up to `4e-3` per component, because
floating-point accumulation depends on how the batch was packed. Short inputs
came back bit-identical; long ones did not. The contract was unsatisfiable by
any real backend, which is worse than a contract that is too loose — the
natural repair is to exempt the real adapter, and then the suite is a
description of the fake forever.

So the shared suite now compares by cosine, and this file is what keeps that
calibration honest. `recurring-defects.md` §1 is usually about an in-memory
reference being *more forgiving* than production; this is the same failure
inverted, and the same fix applies: weaken the shared claim exactly as far as
the backend forces, never with an exemption for one adapter.

## Why the probe embeds rather than lists models

Same rule as every other integration suite here, and this one has its own
reason on top: a chat endpoint and an embeddings endpoint are frequently the
same server with different models loaded, and asking for a model list tells you
nothing about whether the embedding weights will load. `BACKLOG.md` B12 is the
standing example of the weaker check costing eight failures instead of a skip.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from redstring.llm.adapters.langchain_embedding import LangChainEmbeddingProvider
from redstring.testing.embedding_provider import (
    DISTINCT_TEXTS,
    SAME_VECTOR_COSINE,
    EmbeddingProviderCompliance,
    _cosine,
)

pytestmark = [pytest.mark.integration, pytest.mark.live]

BASE_URL = os.environ.get("KG_EMBED_BASE_URL", "http://192.168.1.14:8080/v1")
MODEL = os.environ.get("KG_EMBED_MODEL", "nomic-embed-text")
DIMENSION = int(os.environ.get("KG_EMBED_DIMENSION", "768"))


def _provider() -> LangChainEmbeddingProvider:
    return LangChainEmbeddingProvider.openai_compatible(
        base_url=BASE_URL, model=MODEL, dimension=DIMENSION, provider="live"
    )


async def _serving() -> bool:
    """True when the endpoint returns a usable vector of the declared width."""
    try:
        result = await _provider().embed(["probe"])
    except Exception:
        return False
    return len(result) == 1 and len(result[0]) == DIMENSION


@pytest.fixture(scope="module")
def live() -> None:
    """Probe once per module, not once per test.

    `test_live_endpoint` has always done this and it is why that file skips in
    seconds. This one probed inside a *function*-scoped fixture, so a dead
    endpoint was paid for once per test rather than once per module -- and
    `openai_compatible` passes no timeout, so each probe inherits the openai
    client's 600s default with two retries. Against `KG_EMBED_BASE_URL`
    unset and the LAN default unreachable (every CI runner), that turned a
    15-test skip into the better part of an hour of a job doing nothing.

    Sync, and `asyncio.run` rather than an async module-scoped fixture:
    `asyncio_default_fixture_loop_scope` is `function`, so an async fixture
    at module scope would need its own loop scope to match, and the probe has
    no reason to share a loop with the tests. The provider it builds is
    discarded -- each test builds its own.

    Bounding the probe itself is the other half and is *not* done here; see
    BACKLOG B78. One unbounded probe is survivable, thirteen were not.
    """
    if not asyncio.run(_serving()):
        pytest.skip(
            f"no embedding model answered at {BASE_URL} (model {MODEL}, "
            f"dimension {DIMENSION}). Set KG_EMBED_BASE_URL, KG_EMBED_MODEL "
            f"and KG_EMBED_DIMENSION."
        )


class TestLiveEmbeddings(EmbeddingProviderCompliance):
    """The whole compliance suite, unchanged, against a real server.

    The fixture is defined here rather than at module level because
    `EmbeddingProviderCompliance` declares its own `provider` placeholder, and
    a fixture on the class shadows one in the module. A module-level fixture
    looks like it works and yields `NotImplementedError` from the base class --
    which is the base class doing its job, since a subclass that forgot to
    supply an adapter must not silently test nothing.
    """

    @pytest.fixture
    def provider(self, live: None) -> LangChainEmbeddingProvider:
        return _provider()


class TestWhatOnlyALiveServerShows:
    @pytest.fixture
    def provider(self, live: None) -> LangChainEmbeddingProvider:
        return _provider()

    async def test_batch_composition_perturbs_the_vector_but_not_its_direction(self, provider):
        """The measurement the contract is calibrated against.

        Asserted rather than described so the calibration is checkable: if a
        future backend perturbs vectors by *more* than `SAME_VECTOR_COSINE`
        allows, this fails and names the number, instead of the compliance
        suite failing somewhere less obvious with no explanation.

        The upper bound matters as much as the lower one. A backend whose
        batching changed a vector's direction materially would break every
        stored embedding's comparability, and "it still passes the tolerance"
        would be the wrong reading of that.
        """
        alone = (await provider.embed([DISTINCT_TEXTS[0]]))[0]
        in_batch = (await provider.embed(DISTINCT_TEXTS))[0]

        similarity = _cosine(alone, in_batch)
        assert similarity >= SAME_VECTOR_COSINE, (
            f"batching moved the vector further than the contract allows "
            f"(cosine {similarity:.6f} < {SAME_VECTOR_COSINE}); the tolerance "
            f"in redstring/testing/embedding_provider.py needs revisiting, not "
            f"an exemption for this adapter"
        )

    async def test_the_declared_dimension_matches_what_the_model_returns(self, provider):
        """The declared-not-probed trade, checked against reality once.

        `LangChainEmbeddingProvider` takes `dimension` rather than measuring it,
        to keep network I/O out of `__init__`. The cost is that a caller can
        state the wrong number; this is the test that says the number in this
        repository's own defaults is right for the model it names.
        """
        result = await provider.embed(["Ada Lovelace"])

        assert len(result[0]) == DIMENSION

    async def test_a_batch_of_one_and_a_batch_of_many_both_work(self, provider):
        """Both shapes reach a real server, which a stub cannot demonstrate.

        Some OpenAI-compatible servers handle a single-element `input` array
        differently from a multi-element one — returning a bare object rather
        than a list, historically. The client normalises that; this is what
        says so against the deployment in front of us.
        """
        one = await provider.embed(["Ada Lovelace"])
        many = await provider.embed(DISTINCT_TEXTS)

        assert len(one) == 1
        assert len(many) == len(DISTINCT_TEXTS)
