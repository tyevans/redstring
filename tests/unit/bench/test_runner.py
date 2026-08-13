"""One sweep point against a scripted provider, so the harness's own
arithmetic is proved before any live number is believed."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from bench.config import SweepPoint
from bench.corpus import BenchDocument
from bench.runner import run_point
from redstring.domain.exceptions import EmptyCompletionError

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


async def test_wall_clock_brackets_every_model_call() -> None:
    """The wall clock starts before the first model call and stops after the
    last, so it never reports less than the model time it contains.

    The fake clock advances only inside provider calls, so wall clock and
    total model time come out equal here by construction -- this cannot show
    that chunking or merging time is included, since nothing in this harness
    makes that cost simulated time. What it does catch is a timer started
    after the first call or stopped before the last, either of which would
    make wall clock read less than the model time it is supposed to contain.
    """
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


async def test_consolidate_s_is_absent_rather_than_zero() -> None:
    """`None`, not 0.0: consolidation happens inside `build_graph` and the
    runner has no seam that could set a phase for it. Reporting 0.0 would
    read as "consolidation used no model time" rather than "not measured"."""
    clock = FakeClock()

    result = await run_point(
        point(), DOCUMENT, provider=SteadyProvider(clock, takes=1.0), clock=clock
    )

    assert result.consolidate_s is None


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


async def test_two_runs_of_one_document_report_the_same_counts() -> None:
    """Two runs of one document agree, so nothing accumulates between them.

    This does **not** prove the store is fresh per run, and the distinction
    matters because the name it used to have said it did. `run_point` mints a
    new tenant for every call and `InMemoryGraphStore` is tenant-scoped, so a
    store hoisted to module level and shared across calls would land in
    disjoint partitions and pass this exactly as written. Proving freshness
    needs two runs under one tenant id, which `run_point` has no parameter
    for. See BACKLOG B-BENCH-2.
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


async def test_a_failing_chunk_is_skipped_rather_than_aborting_the_point() -> None:
    """C1: a single chunk raising must not discard the whole point.

    Without `skip_failed_chunks=True` this call would raise out of
    `run_point` and the caller would get nothing back for a document that
    otherwise extracted fine.
    """
    clock = FakeClock()

    class FlakyProvider:
        def __init__(self) -> None:
            self.calls = 0

        @property
        def model(self) -> str:
            return "flaky-model"

        async def extract(self, text: str, schema: Any, *, system_prompt: str | None = None) -> Any:
            self.calls += 1
            if self.calls == 1:
                raise EmptyCompletionError(model="flaky-model")
            return _two_entities(schema)

    result = await run_point(point(), DOCUMENT, provider=FlakyProvider(), clock=clock)

    assert result.failed_chunks == 1
    assert result.chunks > result.failed_chunks


async def test_a_concurrency_above_one_is_refused_here_too() -> None:
    """Config refuses it, and so does the runner: a caller building a
    SweepPoint directly must not silently get serial behaviour labelled 4."""
    clock = FakeClock()

    with pytest.raises(ValueError, match="deliverable C"):
        await run_point(
            point(concurrency=4), DOCUMENT, provider=SteadyProvider(clock, takes=0.0), clock=clock
        )


class DriftingProvider(SteadyProvider):
    """Names one entity two ways, the way a chunk boundary does."""

    async def extract(self, text: str, schema: Any, *, system_prompt: str | None = None) -> Any:
        self.prompts.append(system_prompt)
        self._clock.now += self._takes
        return schema.model_validate(
            {
                "entities": [
                    {"name": "Ada Lovelace", "entity_type": "person"},
                    {"name": "Lovelace", "entity_type": "person"},
                ],
                "relationships": [],
            }
        )


async def test_the_run_reports_the_drift_pairs_its_entities_contain() -> None:
    """A counter with no test asserting it non-zero is the shape
    `recurring-defects.md` §3 is about -- and this one is wired through three
    modules, so a zero would look like "no drift" rather than "not measured".
    """
    clock = FakeClock()

    result = await run_point(
        point(), DOCUMENT, provider=DriftingProvider(clock, takes=0.0), clock=clock
    )

    assert result.variant_pairs >= 1
