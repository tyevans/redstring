"""Chunks in bounded batches, and the two properties that make the knob safe.

`concurrency=1` must be byte-identical to the serial pipeline -- that is what
makes this a measurement rather than a rewrite. And no more than `concurrency`
calls may be in flight at once, because the operator's constraint is the
inference backend's queue depth, not this module's batch structure.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from redstring.domain.exceptions import LlmProviderError
from redstring.domain.source import SourceDocument
from redstring.extraction.chunkers import SlidingWindowChunker
from redstring.extraction.limiter import CallLimiter
from redstring.extraction.pipeline import ExtractionPipeline

if TYPE_CHECKING:
    from redstring.extraction.schema import Extraction

OBSERVED = datetime(2026, 2, 9, 11, 7, tzinfo=UTC)

#: Small enough that the four-paragraph document below really does split into
#: one chunk per paragraph.
_CHUNKER = SlidingWindowChunker(default_chunk_size=60, default_overlap=0, min_chunk_size=10)

#: Four short, distinct paragraphs -- one entity apiece, so each chunk's
#: answer differs and carryover has something to carry.
_PARAGRAPHS = [
    "Ada Lovelace wrote the first algorithm.",
    "Charles Babbage designed the engine.",
    "Grace Hopper wrote the first compiler.",
    "Alan Turing proved the halting problem.",
]


def _answer_for(text: str, schema: type[Extraction]) -> Extraction:
    """A different entity per chunk text, keyed by whichever name is in it."""
    names = ["Ada Lovelace", "Charles Babbage", "Grace Hopper", "Alan Turing"]
    for name in names:
        if name.split()[0] in text:
            return schema(entities=[{"name": name, "entity_type": "Person"}], relationships=[])
    return schema(entities=[], relationships=[])


class RecordingProvider:
    """Answers every chunk, recording prompts and peak concurrency."""

    def __init__(self, *, delay: float = 0.0) -> None:
        self.prompts: list[str | None] = []
        self.texts: list[str] = []
        self.in_flight = 0
        self.peak = 0
        self._delay = delay

    @property
    def model(self) -> str:
        return "recording-model"

    async def extract(self, text, schema, *, system_prompt=None):
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            self.texts.append(text)
            self.prompts.append(system_prompt)
            await asyncio.sleep(self._delay)
            return _answer_for(text, schema)
        finally:
            self.in_flight -= 1


async def _run(provider, *, concurrency: int, chunks: int = 3, gleanings: int = 0):
    text = "\n\n".join(_PARAGRAPHS[:chunks])
    pipeline = ExtractionPipeline(
        provider, chunker=_CHUNKER, concurrency=concurrency, gleanings=gleanings
    )
    return await pipeline.extract(
        SourceDocument(id="doc-1", text=text), uuid4(), observed_at=OBSERVED
    )


async def test_concurrency_one_makes_the_same_calls_in_the_same_order() -> None:
    """The regression gate for every existing caller -- against a literal
    oracle, not a second run of the same code.

    F2: comparing two runs of `_run` at K=1 checks only that the pipeline is
    deterministic against itself, which any implementation is, including one
    that folds carryover wrongly or batches even at K=1. The byte-identity
    claim this test's docstring makes is actually carried by the unmodified
    `test_pipeline.py` suite (no line of it changed on this branch); what this
    test adds is pinning the documented sequence directly: chunk 0 gets the
    bare `DEFAULT_SYSTEM_PROMPT`, chunk 1 gets it plus a block naming what
    chunk 0 found, and so on -- built here from `Carryover` directly rather
    than by running the pipeline twice, so a pipeline bug cannot cancel
    against itself.
    """
    from types import SimpleNamespace

    from redstring.extraction.carryover import Carryover
    from redstring.extraction.pipeline import DEFAULT_SYSTEM_PROMPT

    provider = RecordingProvider()

    await _run(provider, concurrency=1, chunks=4)

    carryover = Carryover()
    expected_prompts = []
    for name in ("Ada Lovelace", "Charles Babbage", "Grace Hopper", "Alan Turing"):
        expected_prompts.append(DEFAULT_SYSTEM_PROMPT + carryover.block())
        carryover.remember([SimpleNamespace(name=name, entity_type="Person")])

    assert provider.prompts == expected_prompts
    assert provider.peak == 1


async def test_no_more_than_k_calls_are_ever_in_flight() -> None:
    """The ceiling the operator actually sets.

    Four chunks at concurrency=2, not three chunks at concurrency=3: with
    `chunks <= concurrency` every chunk fits in one batch, so `peak <=
    concurrency` holds for *any* implementation -- including one that ignores
    `concurrency` entirely and gathers the whole document at once. Chunks
    strictly greater than concurrency is what makes an unbounded gather
    visibly exceed the ceiling this test is meant to catch.
    """
    provider = RecordingProvider(delay=0.01)

    await _run(provider, concurrency=2, chunks=4)

    assert provider.peak <= 2


async def test_a_wavefront_actually_overlaps() -> None:
    """The other half: a ceiling of 3 that never exceeds 1 is a serial
    pipeline passing the test above."""
    provider = RecordingProvider(delay=0.01)

    await _run(provider, concurrency=3)

    assert provider.peak > 1


class _OrderSensitiveProvider:
    """Delay depends on the chunk's text, not on its position in the batch.

    Every other provider in this module uses a uniform delay, so calls always
    *complete* in argument order and cannot separate chunk-index fold order
    from completion order -- `asyncio.gather` returns in argument order
    regardless of which call actually finished first. This one lets a later
    chunk finish before an earlier one within the same batch.
    """

    def __init__(self, delays: dict[str, float]) -> None:
        self.prompts: list[str | None] = []
        self._delays = delays

    @property
    def model(self) -> str:
        return "order-sensitive-model"

    async def extract(self, text, schema, *, system_prompt=None):
        self.prompts.append(system_prompt)
        for needle, delay in self._delays.items():
            if needle in text:
                await asyncio.sleep(delay)
                break
        return _answer_for(text, schema)


async def test_carryover_folds_in_chunk_order_not_completion_order() -> None:
    """The subtlest requirement in the batching design, pinned directly.

    Chunk 0 (Ada) is slower than chunk 1 (Charles), so within the first batch
    Charles's call *completes* first even though Ada is earlier in the
    document. `Carryover.remember` must still be called in chunk order --
    `redstring.extraction.carryover.Carryover` keeps insertion order, oldest
    first -- so the next batch's prompt must name Ada before Charles.

    This is not implied by the existing prompt-equality tests: folding on
    completion instead of chunk order, or folding inside the first
    classification pass, or iterating with `asyncio.as_completed` instead of
    `gather`, all produce a *self-consistent* pair of equal prompts within
    each batch -- they only disagree with the correct implementation about
    which name comes *first*, which is exactly what this test reads.
    """
    provider = _OrderSensitiveProvider({"Ada": 0.03, "Charles": 0.0})

    await _run(provider, concurrency=2, chunks=4)

    batch_two_prompt = provider.prompts[2]
    assert batch_two_prompt is not None
    ada_position = batch_two_prompt.index("Ada Lovelace")
    charles_position = batch_two_prompt.index("Charles Babbage")
    assert ada_position < charles_position


async def test_a_chunk_sees_what_earlier_batches_found_and_not_its_own_batch() -> None:
    """Carryover accumulates between batches, which is what bounds drift.

    With K=2 over four chunks, chunk 3's prompt must mention what chunks 1-2
    found and chunk 4's must not add anything chunk 3 found -- they are in one
    batch. A pipeline that updated carryover per completed call instead would
    make the prompt depend on which call finished first, so two runs of one
    document would differ.
    """
    provider = RecordingProvider()

    await _run(provider, concurrency=2, chunks=4)

    assert provider.prompts[2] == provider.prompts[3]
    assert provider.prompts[0] == provider.prompts[1]
    assert provider.prompts[2] != provider.prompts[0]


@pytest.mark.parametrize("bad", [0, -1])
async def test_a_concurrency_below_one_is_refused(bad: int) -> None:
    with pytest.raises(ValueError, match="concurrency"):
        ExtractionPipeline(RecordingProvider(), concurrency=bad)


class _CountingLimiter(CallLimiter):
    """A `CallLimiter` that also counts how many calls actually passed
    through it -- used to prove *which* calls a limiter gated, independent of
    timing."""

    def __init__(self, limit: int) -> None:
        super().__init__(limit)
        self.enters = 0

    async def __aenter__(self) -> None:
        self.enters += 1
        await super().__aenter__()


async def test_gleaning_calls_are_inside_the_ceiling_too() -> None:
    """Gleaning goes through the same limiter as extraction, not around it.

    A peak-based assertion cannot catch this: `_batches` already sizes every
    batch at `concurrency`, and each chunk's extraction and gleaning calls run
    *sequentially* within that chunk's own task, so no implementation --
    limiter or no limiter -- can ever have more than `concurrency` calls in
    flight at once here. Verified directly: stripping the limiter out of both
    `_extract_one` and `_glean` and rerunning `peak <= K` at concurrency 2 and
    3, chunk counts 3 and 4, and gleanings=1 still held (`peak == concurrency`
    every time) -- the batch structure alone already enforces it, so that
    assertion is vacuous for this specific regression.

    What actually distinguishes "gleaning bypasses the limiter" is a *count*:
    with `gleanings=1` and every chunk finding something, each of the four
    chunks makes exactly two calls (one extraction, one gleaning), so a
    limiter used by every call sees `4 * 2 == 8` entries. Skip wrapping the
    gleaning call and the count drops to 4, deterministically -- no timing
    involved.
    """
    provider = RecordingProvider()
    limiter = _CountingLimiter(2)
    pipeline = ExtractionPipeline(
        provider, chunker=_CHUNKER, concurrency=2, gleanings=1, limiter=limiter
    )

    await pipeline.extract(
        SourceDocument(id="doc-1", text="\n\n".join(_PARAGRAPHS)), uuid4(), observed_at=OBSERVED
    )

    assert limiter.enters == len(_PARAGRAPHS) * 2


class _FailingProvider:
    """Raises `LlmProviderError` for any chunk whose text contains a marker
    from `fails_on`, answers every other chunk normally.

    `FailOnSubstring` in `test_pipeline.py` does the same thing at
    concurrency=1, where a batch is one chunk and the failure loop's `continue`
    and `break` are indistinguishable. This is the K>1 version: with more than
    one chunk per batch, a failing chunk has siblings in the *same* `gather`
    call that must still be processed.
    """

    def __init__(self, fails_on: tuple[str, ...]) -> None:
        self._fails_on = fails_on

    @property
    def model(self) -> str:
        return "failing-model"

    async def extract(self, text, schema, *, system_prompt=None):
        if any(marker in text for marker in self._fails_on):
            raise LlmProviderError("the server said no", model=self.model)
        return _answer_for(text, schema)


async def test_a_failed_chunk_does_not_discard_its_batch_siblings() -> None:
    """The `continue`/`break` gap F1 names directly.

    K=2 over four chunks: chunk 0 (Ada) raises, chunk 1 (Charles) succeeds, in
    the *same* batch. `continue` counts the failure and keeps processing
    chunk 1; `break` would stop the whole `for chunk, result in zip(...)` loop
    at chunk 0 and silently drop chunk 1 from both `parts` and
    `found_by_index`, along with every later chunk in the batch. At
    concurrency=1 (every failure test in `test_pipeline.py`) a batch holds one
    chunk, so this loop shape is unreachable there -- it only exists at K>1.

    Swap `continue` for `break` in `pipeline.py` and this goes red: Charles
    Babbage disappears from `result.entities` and chunk 1's `entity_ids`
    comes back empty, because chunk 1 is never reached.
    """
    provider = _FailingProvider(fails_on=("Ada",))
    pipeline = ExtractionPipeline(
        provider, chunker=_CHUNKER, concurrency=2, skip_failed_chunks=True
    )

    result = await pipeline.extract(
        SourceDocument(id="doc-1", text="\n\n".join(_PARAGRAPHS[:4])), uuid4(), observed_at=OBSERVED
    )

    assert result.failed_chunks == 1
    assert [e.name for e in result.entities] == [
        "Charles Babbage",
        "Grace Hopper",
        "Alan Turing",
    ]
    # `result.chunks` is in first-seen chunking order (`stored_chunks`), and
    # the four paragraphs are distinct passages, so index 0 is Ada's chunk and
    # index 1 is Charles's.
    ada_chunk, charles_chunk = result.chunks[0], result.chunks[1]
    assert ada_chunk.entity_ids == []
    assert len(charles_chunk.entity_ids) == 1
    assert charles_chunk.entity_ids[0] in {e.id for e in result.entities}


async def test_a_failed_chunk_still_raises_when_skip_is_off() -> None:
    """The mirror of the test above: `skip_failed_chunks=False` still raises,
    even with a successful sibling in the same batch."""
    provider = _FailingProvider(fails_on=("Ada",))
    pipeline = ExtractionPipeline(provider, chunker=_CHUNKER, concurrency=2)

    with pytest.raises(LlmProviderError):
        await pipeline.extract(
            SourceDocument(id="doc-1", text="\n\n".join(_PARAGRAPHS[:4])),
            uuid4(),
            observed_at=OBSERVED,
        )


async def test_the_earliest_failing_chunk_in_a_batch_is_what_raises() -> None:
    """Two chunks in one batch fail; the raised error is chunk 0's, not
    chunk 1's -- unchanged failure semantics (F1's fix note, and the
    constraint the module docstring names: same object, same type, earliest
    chunk in document order).
    """
    provider = _FailingProvider(fails_on=("Ada", "Charles"))
    pipeline = ExtractionPipeline(provider, chunker=_CHUNKER, concurrency=2)

    with pytest.raises(LlmProviderError) as excinfo:
        await pipeline.extract(
            SourceDocument(id="doc-1", text="\n\n".join(_PARAGRAPHS[:4])),
            uuid4(),
            observed_at=OBSERVED,
        )

    # `zip(batch, results, strict=True)` iterates in chunk order, so the
    # first failing entry in document order is what raises regardless of
    # which call actually completed first.
    assert "server said no" in str(excinfo.value)
    assert "Ada" not in str(excinfo.value)


async def test_concurrency_and_limiter_bound_different_things() -> None:
    """F3: `concurrency` sets the batch size, `limiter` sets the ceiling, and
    an explicit limiter narrower than `concurrency` is legitimate --
    `ExtractionPipeline(provider, concurrency=4, limiter=CallLimiter(2))`
    batches four and admits two. More than four chunks, so a batch really
    does hold more calls than the limiter admits at once.
    """
    provider = RecordingProvider(delay=0.01)
    limiter = CallLimiter(2)
    pipeline = ExtractionPipeline(
        provider,
        chunker=_CHUNKER,
        concurrency=4,
        limiter=limiter,
        gleanings=0,
    )
    paragraphs = [*_PARAGRAPHS, "Marie Curie discovered radium."]

    await pipeline.extract(
        SourceDocument(id="doc-1", text="\n\n".join(paragraphs)), uuid4(), observed_at=OBSERVED
    )

    assert provider.peak <= 2
