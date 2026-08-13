"""One sweep point against a scripted provider, so the harness's own
arithmetic is proved before any live number is believed."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from bench.config import SweepPoint
from bench.corpus import BenchDocument
from bench.runner import run_point

if TYPE_CHECKING:
    from pydantic import BaseModel

DOCUMENT = BenchDocument(
    id="doc",
    # Long enough to chunk at 200 characters and not at 100_000.
    text=("Ada Lovelace worked with Charles Babbage on the Analytical Engine. " * 40),
    source="https://example.com",
    retrieved="2026-08-13",
    licence="CC0",
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class SteadyProvider:
    """Returns two entities and one relationship per chunk, taking `takes`."""

    def __init__(self, clock: FakeClock, *, takes: float) -> None:
        self._clock = clock
        self._takes = takes
        self.prompts: list[str | None] = []

    @property
    def model(self) -> str:
        return "scripted-model"

    async def extract(self, text: str, schema: Any, *, system_prompt: str | None = None) -> Any:
        self.prompts.append(system_prompt)
        self._clock.now += self._takes
        return _two_entities(schema)


def _two_entities(schema: type[BaseModel]) -> BaseModel:
    """Build whatever extraction schema the pipeline asked for, populated.

    The pipeline chooses the schema; the harness must not assume its shape, so
    this fills the entity and relationship lists by field name and leaves the
    rest to defaults. The real schema
    (`redstring.extraction.schema.ExtractedRelationship`) names its endpoints
    `source_name` / `target_name`, not `source` / `target`.
    """
    return schema.model_validate(
        {
            "entities": [
                {"name": "Ada Lovelace", "entity_type": "person"},
                {"name": "Charles Babbage", "entity_type": "person"},
            ],
            "relationships": [
                {
                    "source_name": "Ada Lovelace",
                    "target_name": "Charles Babbage",
                    "relationship_type": "worked_with",
                }
            ],
        }
    )


def point(**overrides: object) -> SweepPoint:
    fields: dict[str, object] = {
        "document_id": "doc",
        "chunk_size": 200,
        "concurrency": 1,
        "repeat": 0,
    }
    fields.update(overrides)
    return SweepPoint(**fields)  # type: ignore[arg-type]


async def test_it_reports_the_chunk_count_the_pipeline_actually_used() -> None:
    """`total_chunks` comes off the report rather than being recomputed.

    A harness dividing the document length by the chunk size agrees with the
    pipeline on a uniform document and disagrees the moment the chunker
    respects a paragraph boundary -- which is its default.
    """
    clock = FakeClock()
    result = await run_point(
        point(), DOCUMENT, provider=SteadyProvider(clock, takes=1.0), clock=clock
    )

    assert result.chunks > 1
    assert result.model_calls == result.chunks


async def test_a_smaller_chunk_size_produces_more_chunks_and_more_calls() -> None:
    """The knob under test does something. Without this, every chunk_size in
    the sweep could be ignored and the grid would still look plausible."""
    clock = FakeClock()

    small = await run_point(
        point(chunk_size=200), DOCUMENT, provider=SteadyProvider(clock, takes=0.0), clock=clock
    )
    large = await run_point(
        point(chunk_size=2000), DOCUMENT, provider=SteadyProvider(clock, takes=0.0), clock=clock
    )

    assert small.chunks > large.chunks
    assert small.model_calls > large.model_calls


async def test_wall_clock_is_the_whole_call_not_the_model_time() -> None:
    """Three chunks at 2.0s each is 6.0s of model time; the run also spends
    time chunking and merging, which the wall clock must include."""
    clock = FakeClock()
    provider = SteadyProvider(clock, takes=2.0)

    result = await run_point(point(), DOCUMENT, provider=provider, clock=clock)

    assert result.wall_clock_s == pytest.approx(2.0 * result.model_calls)
    assert result.extract_s == pytest.approx(2.0 * result.model_calls)


async def test_time_to_first_entity_is_absent_in_this_deliverable() -> None:
    """Not approximated from the first call's return. See the plan."""
    clock = FakeClock()

    result = await run_point(
        point(), DOCUMENT, provider=SteadyProvider(clock, takes=1.0), clock=clock
    )

    assert result.time_to_first_entity_s is None
    assert result.event_gaps_s == ()


async def test_the_entities_come_back_normalised_and_sorted_for_comparison() -> None:
    """Stability compares these across repeats, so two runs finding the same
    entities in a different order must produce identical tuples."""
    clock = FakeClock()

    result = await run_point(
        point(), DOCUMENT, provider=SteadyProvider(clock, takes=0.0), clock=clock
    )

    assert result.entity_names == tuple(sorted(result.entity_names))
    assert "ada lovelace" in result.entity_names
    assert result.entities == len(result.entity_names)


async def test_each_run_starts_from_an_empty_store() -> None:
    """Two runs of the same document must not accumulate.

    A shared store makes the second repeat report double the entities and
    perfect stability, which is exactly backwards.
    """
    clock = FakeClock()

    first = await run_point(
        point(), DOCUMENT, provider=SteadyProvider(clock, takes=0.0), clock=clock
    )
    second = await run_point(
        point(), DOCUMENT, provider=SteadyProvider(clock, takes=0.0), clock=clock
    )

    assert first.entities == second.entities
    assert first.entity_names == second.entity_names


async def test_a_concurrency_above_one_is_refused_here_too() -> None:
    """Config refuses it, and so does the runner: a caller building a
    SweepPoint directly must not silently get serial behaviour labelled 4."""
    clock = FakeClock()

    with pytest.raises(ValueError, match="deliverable C"):
        await run_point(
            point(concurrency=4), DOCUMENT, provider=SteadyProvider(clock, takes=0.0), clock=clock
        )
