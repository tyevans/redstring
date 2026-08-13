"""A result that cannot say what produced it is an anecdote."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from bench.config import SweepPoint, load_config
from bench.metrics import RunMetrics
from bench.report import build_report, write_report

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = """
endpoint: http://192.168.1.14:8080/v1/
models:
  extraction: muse-glimmer-30b
  embedding: nomic-embed-text
  embedding_dimensions: 768
corpus:
  graded: true
  long: [hp1]
sweep:
  chunk_size: [3000]
  concurrency: [1]
policy:
  repeats: 2
  stop_climbing_concurrency: true
  per_document_timeout_s: 1800
"""

# Carries an unknown top-level key and an unknown key nested inside a section
# the loader does read from, to prove both survive into the report verbatim.
CONFIG_WITH_UNKNOWN_KEYS = """
endpoint: http://192.168.1.14:8080/v1/
notes: run by hand after the GPU firmware update
models:
  extraction: muse-glimmer-30b
  embedding: nomic-embed-text
  embedding_dimensions: 768
  driver: cuda
corpus:
  graded: true
  long: [hp1]
sweep:
  chunk_size: [3000]
  concurrency: [1]
policy:
  repeats: 2
  stop_climbing_concurrency: true
  per_document_timeout_s: 1800
"""


def config(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG)
    return load_config(path)


def run(repeat: int, *, names: tuple[str, ...], wall: float, chunk_size: int = 3000) -> RunMetrics:
    return RunMetrics(
        point=SweepPoint(document_id="hp1", chunk_size=chunk_size, concurrency=1, repeat=repeat),
        wall_clock_s=wall,
        time_to_first_entity_s=None,
        event_gaps_s=(1.0, 1.0, 7.0),
        model_calls=3,
        extract_s=wall - 1.0,
        consolidate_s=1.0,
        chunks=3,
        entities=len(names),
        relationships=2,
        failed_chunks=0,
        unresolved_relationships=1,
        entity_names=names,
    )


def two_runs() -> list[RunMetrics]:
    return [
        run(0, names=("ada lovelace", "charles babbage"), wall=30.0),
        run(1, names=("ada lovelace",), wall=32.0),
    ]


def runs_across_two_chunk_sizes() -> list[RunMetrics]:
    """Two configurations whose repeats disagree *differently*: 3000 chars
    splits (jaccard 0.5), 12000 chars agrees completely (jaccard 1.0). A
    grouping that folds the sweep together cannot reproduce both numbers."""
    return [
        run(0, names=("ada lovelace", "charles babbage"), wall=30.0, chunk_size=3000),
        run(1, names=("ada lovelace",), wall=32.0, chunk_size=3000),
        run(0, names=("ada lovelace", "charles babbage"), wall=55.0, chunk_size=12000),
        run(1, names=("ada lovelace", "charles babbage"), wall=57.0, chunk_size=12000),
    ]


def test_the_resolved_config_travels_with_the_result(tmp_path: Path) -> None:
    report = build_report(
        config(tmp_path),
        two_runs(),
        accuracy=None,
        started_at="2026-08-13T12-00-00Z",
        library_version="0.1.0",
        git_sha="abc1234",
    )

    assert report["config"]["endpoint"] == "http://192.168.1.14:8080/v1/"
    assert report["library_version"] == "0.1.0"
    assert report["git_sha"] == "abc1234"


def test_every_run_appears_with_its_point(tmp_path: Path) -> None:
    report = build_report(
        config(tmp_path),
        two_runs(),
        accuracy=None,
        started_at="t",
        library_version="v",
        git_sha="s",
    )

    assert [r["point"]["repeat"] for r in report["runs"]] == [0, 1]
    assert report["runs"][0]["wall_clock_s"] == 30.0


def test_the_gap_summary_is_written_alongside_the_raw_gaps(tmp_path: Path) -> None:
    """Both, deliberately: the summary is what a human reads and the list is
    what a later analysis re-summarises differently."""
    report = build_report(
        config(tmp_path),
        two_runs(),
        accuracy=None,
        started_at="t",
        library_version="v",
        git_sha="s",
    )

    assert report["runs"][0]["event_gaps_s"] == [1.0, 1.0, 7.0]
    assert report["runs"][0]["gaps"]["maximum"] == 7.0


def test_stability_is_computed_per_configuration_not_across_the_whole_sweep(tmp_path: Path) -> None:
    """Repeats of one configuration are comparable; a 3000-char run and a
    12000-char run disagreeing is a finding, not instability.

    Under a wrong implementation that folds the whole sweep into one group,
    there is a single group rather than two, so this fails loudly rather than
    merely landing on the wrong number."""
    report = build_report(
        config(tmp_path),
        runs_across_two_chunk_sizes(),
        accuracy=None,
        started_at="t",
        library_version="v",
        git_sha="s",
    )

    groups = {group["chunk_size"]: group for group in report["stability"]}
    assert len(report["stability"]) == 2
    assert groups[3000]["document_id"] == "hp1"
    assert groups[3000]["jaccard"] == 0.5
    assert groups[3000]["sometimes"] == 1
    assert groups[12000]["document_id"] == "hp1"
    assert groups[12000]["jaccard"] == 1.0
    assert groups[12000]["sometimes"] == 0


def test_unknown_config_keys_survive_into_the_report(tmp_path: Path) -> None:
    """The whole reason the raw parsed document is embedded rather than
    reconstructed from `BenchConfig`'s typed fields is that a results file
    must record what was asked, including keys this version of the loader
    knows nothing about. A top-level key and a key nested inside a section the
    loader does read from must both come through unchanged."""
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG_WITH_UNKNOWN_KEYS)
    loaded = load_config(path)

    report = build_report(
        loaded,
        two_runs(),
        accuracy=None,
        started_at="t",
        library_version="v",
        git_sha="s",
    )

    assert report["config"]["notes"] == "run by hand after the GPU firmware update"
    assert report["config"]["models"]["driver"] == "cuda"


def test_stability_and_accuracy_are_separate_keys(tmp_path: Path) -> None:
    """Named separately so no reader can take one for the other, and accuracy
    is explicitly null when the graded corpus did not run."""
    report = build_report(
        config(tmp_path),
        two_runs(),
        accuracy=None,
        started_at="t",
        library_version="v",
        git_sha="s",
    )

    assert report["accuracy"] is None
    assert "stability" in report


def test_a_timed_out_point_appears_under_its_own_key(tmp_path: Path) -> None:
    """A timed-out point contributes no `RunMetrics`, so `runs` alone cannot
    show it happened -- it has to be recorded separately."""
    point = SweepPoint(document_id="hp1", chunk_size=12000, concurrency=1, repeat=1)

    report = build_report(
        config(tmp_path),
        two_runs(),
        accuracy=None,
        started_at="t",
        library_version="v",
        git_sha="s",
        timed_out=[point],
    )

    assert report["timed_out"] == [
        {"document_id": "hp1", "chunk_size": 12000, "concurrency": 1, "repeat": 1}
    ]


def test_timed_out_is_present_and_empty_when_nothing_timed_out(tmp_path: Path) -> None:
    """An absent key would read as an older file format; an empty list reads
    as the decision it is -- nothing timed out."""
    report = build_report(
        config(tmp_path),
        two_runs(),
        accuracy=None,
        started_at="t",
        library_version="v",
        git_sha="s",
    )

    assert report["timed_out"] == []


def test_the_file_is_json_named_for_when_the_run_started(tmp_path: Path) -> None:
    report = build_report(
        config(tmp_path),
        two_runs(),
        accuracy=None,
        started_at="2026-08-13T12-00-00Z",
        library_version="v",
        git_sha="s",
    )

    path = write_report(report, directory=tmp_path / "results", started_at="2026-08-13T12-00-00Z")

    assert path.name == "2026-08-13T12-00-00Z.json"
    assert json.loads(path.read_text())["runs"][0]["chunks"] == 3
