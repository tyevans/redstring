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

from redstring.domain.source import SourceDocument
from redstring.extraction.chunkers import SlidingWindowChunker
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


async def _run(provider, *, concurrency: int, chunks: int = 3):
    text = "\n\n".join(_PARAGRAPHS[:chunks])
    pipeline = ExtractionPipeline(provider, chunker=_CHUNKER, concurrency=concurrency)
    return await pipeline.extract(
        SourceDocument(id="doc-1", text=text), uuid4(), observed_at=OBSERVED
    )


async def test_concurrency_one_makes_the_same_calls_in_the_same_order() -> None:
    """The regression gate for every existing caller.

    Not "produces the same entities" -- the same *calls*, in order, with the
    same prompts. Carryover means prompt N depends on chunks 1..N-1, so a
    pipeline that batched even at K=1 would produce different prompts while
    plausibly reaching the same entities.
    """
    serial = RecordingProvider()
    batched = RecordingProvider()

    await _run(serial, concurrency=1)
    await _run(batched, concurrency=1)

    assert serial.texts == batched.texts
    assert serial.prompts == batched.prompts
    assert serial.peak == 1


async def test_no_more_than_k_calls_are_ever_in_flight() -> None:
    """The ceiling the operator actually sets."""
    provider = RecordingProvider(delay=0.01)

    await _run(provider, concurrency=3)

    assert provider.peak <= 3


async def test_a_wavefront_actually_overlaps() -> None:
    """The other half: a ceiling of 3 that never exceeds 1 is a serial
    pipeline passing the test above."""
    provider = RecordingProvider(delay=0.01)

    await _run(provider, concurrency=3)

    assert provider.peak > 1


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
