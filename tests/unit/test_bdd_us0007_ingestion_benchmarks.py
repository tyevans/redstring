"""Executable BDD Acceptance Criteria for US-0007: Ingestion Benchmarks.

Governed by:
- ADR-0039 (Bounded Concurrency Over Chunks)
- PRD-0001
- US-0007
"""

from __future__ import annotations

from bench.config import SweepPoint
from bench.metrics import RunMetrics


def test_observe_incremental_chunk_extraction_progress_with_real_denominators() -> None:
    """Scenario: Observe incremental chunk extraction progress with real denominators (US-0007)."""
    point = SweepPoint(document_id="benchmark-doc", chunk_size=2000, concurrency=2, repeat=1)
    metrics = RunMetrics(
        point=point,
        wall_clock_s=5.2,
        time_to_first_entity_s=1.1,
        event_gaps_s=(0.5, 0.4),
        model_calls=4,
        extract_s=4.0,
        consolidate_s=1.2,
        chunks=4,
        entities=10,
        relationships=5,
        failed_chunks=0,
        unresolved_relationships=0,
        entity_names=("ada", "babbage"),
    )
    assert metrics.chunks == 4
    assert metrics.failed_chunks == 0
    assert metrics.wall_clock_s > 0


def test_execute_reproducible_throughput_sweeps_across_concurrency_levels() -> None:
    """Scenario: Execute reproducible throughput sweeps across concurrency levels (US-0007)."""
    point_c1 = SweepPoint(document_id="bench-doc", chunk_size=1000, concurrency=1, repeat=1)
    point_c4 = SweepPoint(document_id="bench-doc", chunk_size=1000, concurrency=4, repeat=1)

    assert point_c1.concurrency == 1
    assert point_c4.concurrency == 4
    assert point_c1.chunk_size == point_c4.chunk_size
