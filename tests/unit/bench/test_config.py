"""Config is the only YAML reader in the harness, so it is the only place a
malformed run can be refused before it costs twenty minutes of GPU time."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003

import pytest

from bench.config import BenchConfigError, SweepPoint, load_config

MINIMAL = """
endpoint: http://192.168.1.14:8080/v1/
models:
  extraction: muse-glimmer-30b
  embedding: nomic-embed-text
  embedding_dimensions: 768
corpus:
  graded: true
  long: [harry-potter-1]
sweep:
  chunk_size: [3000, 8000]
  concurrency: [1]
policy:
  repeats: 2
  stop_climbing_concurrency: true
  per_document_timeout_s: 1800
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return path


def test_the_scalar_fields_are_read(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, MINIMAL))

    assert config.endpoint == "http://192.168.1.14:8080/v1/"
    assert config.extraction_model == "muse-glimmer-30b"
    assert config.embedding_model == "nomic-embed-text"
    assert config.embedding_dimensions == 768
    assert config.graded is True
    assert config.long_documents == ("harry-potter-1",)
    assert config.repeats == 2
    assert config.stop_climbing_concurrency is True
    assert config.per_document_timeout_s == 1800


def test_the_sweep_is_the_full_cross_product_in_a_fixed_order(tmp_path: Path) -> None:
    """Two chunk sizes, one concurrency, two repeats, one document: four points.

    The order is asserted literally rather than as a set. A sweep that varies
    the document fastest re-loads a 100k-character document between every
    timing, and a sweep whose order changes between runs cannot be compared
    against an earlier results file point by point.
    """
    config = load_config(write(tmp_path, MINIMAL))

    assert config.sweep() == (
        SweepPoint(document_id="harry-potter-1", chunk_size=3000, concurrency=1, repeat=0),
        SweepPoint(document_id="harry-potter-1", chunk_size=3000, concurrency=1, repeat=1),
        SweepPoint(document_id="harry-potter-1", chunk_size=8000, concurrency=1, repeat=0),
        SweepPoint(document_id="harry-potter-1", chunk_size=8000, concurrency=1, repeat=1),
    )


def test_two_long_documents_each_get_the_whole_grid(tmp_path: Path) -> None:
    """A second document must not silently share the first one's points.

    With one document in the list, a implementation that ignores the document
    axis entirely produces the same four points as the correct one.
    """
    config = load_config(write(tmp_path, MINIMAL.replace("[harry-potter-1]", "[a, b]")))

    assert len(config.sweep()) == 8
    assert {p.document_id for p in config.sweep()} == {"a", "b"}
    assert len(set(config.sweep())) == 8


def test_a_concurrency_above_one_is_refused_by_name(tmp_path: Path) -> None:
    """The axis exists; the library does not yet. A silently ignored knob
    would make deliverable C look like it changed nothing."""
    with pytest.raises(BenchConfigError, match="deliverable C"):
        load_config(write(tmp_path, MINIMAL.replace("concurrency: [1]", "concurrency: [1, 4]")))


def test_a_missing_required_key_names_the_key(tmp_path: Path) -> None:
    broken = MINIMAL.replace("  embedding_dimensions: 768\n", "")

    with pytest.raises(BenchConfigError, match="embedding_dimensions"):
        load_config(write(tmp_path, broken))


def test_zero_repeats_is_refused(tmp_path: Path) -> None:
    """Zero repeats produces an empty sweep, which runs in no time and
    reports nothing -- the harness's own version of a zero-survivor run."""
    with pytest.raises(BenchConfigError, match="repeats"):
        load_config(write(tmp_path, MINIMAL.replace("repeats: 2", "repeats: 0")))


def test_the_raw_document_is_kept_for_the_results_file(tmp_path: Path) -> None:
    """A result must be able to say what produced it, including keys this
    version of the loader does not know about."""
    config = load_config(write(tmp_path, MINIMAL))

    assert config.raw["endpoint"] == "http://192.168.1.14:8080/v1/"
    assert config.raw["sweep"] == {"chunk_size": [3000, 8000], "concurrency": [1]}
