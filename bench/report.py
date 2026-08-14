"""One JSON document per invocation, carrying enough to be re-read in a year.

Three things travel with the numbers, because a result that cannot say what
produced it is an anecdote: the resolved config verbatim, the library version,
and the git sha. The endpoint is a machine that is not CI's and the model
behind an id can change without the id changing, so the config is the only
record of what was asked.

Stability is grouped **per configuration**, not across the sweep. Repeats of
one chunk size are comparable; a 3000-character run disagreeing with an
8000-character run is a finding about chunk size, and folding the two together
would report it as instability.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from bench.stability import stability_of

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from tests.accuracy.runner import CorpusResult

    from bench.config import BenchConfig, SweepPoint
    from bench.corpus import BenchDocument
    from bench.metrics import RunMetrics


def _provenance_json(documents: Sequence[BenchDocument]) -> dict[str, dict[str, str]]:
    """Source, retrieval date and licence for every long document benchmarked.

    `BenchDocument` carries this and nothing wrote it down before -- the
    text is loaded, timed, and the provenance that made committing it a
    decision (see `bench/corpus.py`) was dropped on the floor. A results
    file that carries "enough to be re-read in a year" ought to say what was
    licensed to be benchmarked, not just what it measured.
    """
    return {
        document.id: {
            "source": document.source,
            "retrieved": document.retrieved,
            "licence": document.licence,
        }
        for document in documents
    }


def _run_json(run: RunMetrics) -> dict[str, Any]:
    summary = run.gaps
    return {
        "point": asdict(run.point),
        "wall_clock_s": run.wall_clock_s,
        "time_to_first_entity_s": run.time_to_first_entity_s,
        # Both the raw list and the summary: the summary is what a human
        # reads, and the list is what a later analysis re-summarises its own
        # way without re-running the benchmark.
        "event_gaps_s": list(run.event_gaps_s),
        "gaps": asdict(summary) if summary is not None else None,
        "model_calls": run.model_calls,
        "extract_s": run.extract_s,
        "consolidate_s": run.consolidate_s,
        "chunks": run.chunks,
        "entities": run.entities,
        "relationships": run.relationships,
        "failed_chunks": run.failed_chunks,
        "unresolved_relationships": run.unresolved_relationships,
        # A lower bound on naming drift, not a total -- see `bench/drift.py`'s
        # module docstring for what the heuristic cannot see, and divide by
        # "entities" above before comparing this across two runs with
        # different entity counts.
        "variant_pairs": run.variant_pairs,
    }


def _accuracy_json(accuracy: CorpusResult) -> dict[str, Any]:
    return {
        "entities": asdict(accuracy.entities),
        "relationships": asdict(accuracy.relationships),
        "documents": [
            {
                "document_id": document.document_id,
                "entities": asdict(document.entities),
                "relationships": asdict(document.relationships),
            }
            for document in accuracy.documents
        ],
    }


def _stability_json(runs: Sequence[RunMetrics]) -> list[dict[str, Any]]:
    grouped: defaultdict[tuple[str, int, int], list[tuple[str, ...]]] = defaultdict(list)
    for run in runs:
        key = (run.point.document_id, run.point.chunk_size, run.point.concurrency)
        grouped[key].append(run.entity_names)

    groups = []
    for (document_id, chunk_size, concurrency), names in sorted(grouped.items()):
        stability = stability_of(names)
        if stability is None:
            continue
        groups.append(
            {
                "document_id": document_id,
                "chunk_size": chunk_size,
                "concurrency": concurrency,
                **asdict(stability),
            }
        )
    return groups


def build_report(
    config: BenchConfig,
    runs: Sequence[RunMetrics],
    *,
    accuracy: CorpusResult | None,
    started_at: str,
    library_version: str,
    git_sha: str,
    timed_out: Sequence[SweepPoint] = (),
    failed: Sequence[tuple[SweepPoint, str]] = (),
    documents: Sequence[BenchDocument] = (),
) -> dict[str, Any]:
    """Assemble one invocation's results.

    `accuracy` is `None` when the graded corpus did not run, and is written as
    a null rather than omitted -- an absent key reads as an older file format,
    a null reads as a decision.

    `timed_out` is every point the sweep gave up on, always present as a list
    -- empty when nothing timed out, for the same reason: an absent key would
    read as an older file format rather than as "nothing timed out". A point
    that timed out contributes no `RunMetrics` and so does not appear in
    `runs`; the reader has to check both to know what happened to a point.

    `failed` is the same idea for a point that raised for a reason other than
    a timeout -- an `EmptyCompletionError`, a transport blip anywhere
    `skip_failed_chunks` could not absorb it. Each entry carries the reason
    `str(exception)` gave, because "why" is the whole point of recording it
    separately from a timeout.

    `documents` supplies the provenance of every long document actually
    benchmarked; see `_provenance_json`.
    """
    return {
        "started_at": started_at,
        "library_version": library_version,
        "git_sha": git_sha,
        "config": config.raw,
        "runs": [_run_json(run) for run in runs],
        "stability": _stability_json(runs),
        "accuracy": _accuracy_json(accuracy) if accuracy is not None else None,
        "timed_out": [asdict(point) for point in timed_out],
        "failed": [{"point": asdict(point), "reason": reason} for point, reason in failed],
        "corpus_provenance": _provenance_json(documents),
    }


def write_report(report: dict[str, Any], *, directory: Path, started_at: str) -> Path:
    """Write the report, named for when the run started."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{started_at}.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n")
    return path
