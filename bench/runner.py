"""Run one point of the sweep and report what it cost.

The library is not modified by this deliverable, so everything here goes
through `build_graph`'s public signature. Two consequences worth stating:

- **The chunk count comes off `GraphBuildReport.total_chunks`**, never from
  dividing the document length by the chunk size. The default chunker
  respects sentence and paragraph boundaries, so the arithmetic agrees with
  the pipeline only on text that has neither.
- **`time_to_first_entity_s` is `None`.** A returned completion is not a
  mapped entity, and nothing outside `build_graph` can see the merge.

A fresh tenant and a fresh `InMemoryGraphStore` per run, for the reason
`tests/accuracy/runner.py` gives: nothing a previous run extracted may be
counted for this one.
"""

from __future__ import annotations

from time import perf_counter
from typing import TYPE_CHECKING
from uuid import uuid4

from bench.instruments import TimingProvider
from bench.metrics import RunMetrics
from redstring import InMemoryGraphStore, SourceDocument, build_graph
from redstring.extraction.chunkers import SlidingWindowChunker

if TYPE_CHECKING:
    from collections.abc import Callable

    from bench.config import SweepPoint
    from bench.corpus import BenchDocument
    from redstring import LlmProvider


async def run_point(
    point: SweepPoint,
    document: BenchDocument,
    *,
    provider: LlmProvider,
    clock: Callable[[], float] = perf_counter,
) -> RunMetrics:
    """Extract one document at one sweep point, timing what it took.

    Raises:
        ValueError: `point.concurrency` is not 1. The library extracts chunks
            serially; recording a run as concurrency 4 when it was serial
            would make deliverable C's measurement meaningless.
    """
    if point.concurrency != 1:
        raise ValueError(
            f"concurrency {point.concurrency} needs deliverable C; this run would be "
            "serial and recorded as concurrent"
        )

    timed = TimingProvider(provider, clock=clock)
    store = InMemoryGraphStore()
    tenant_id = uuid4()

    started = clock()
    report = await build_graph(
        SourceDocument(id=document.id, text=document.text),
        provider=timed,
        store=store,
        tenant_id=tenant_id,
        chunker=SlidingWindowChunker(
            default_chunk_size=point.chunk_size,
            default_overlap=min(200, point.chunk_size // 2),
        ),
    )
    wall_clock = clock() - started

    entities = await store.find_entities(tenant_id)
    names = tuple(sorted(entity.normalized_name for entity in entities))

    return RunMetrics(
        point=point,
        wall_clock_s=wall_clock,
        time_to_first_entity_s=None,
        event_gaps_s=(),
        model_calls=timed.calls,
        extract_s=timed.elapsed_in("extract"),
        consolidate_s=timed.elapsed_in("consolidate"),
        chunks=report.total_chunks,
        entities=report.entities,
        relationships=report.relationships,
        failed_chunks=report.failed_chunks,
        unresolved_relationships=report.unresolved_relationships,
        entity_names=names,
    )
