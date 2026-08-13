"""Every refusal here is broken on purpose, because a gate whose happy path
is 'the endpoint answered' is indistinguishable from no gate.

The failure this module exists for has a specific shape: a broken run is not
slow, it is *fast*. A pipeline extracting nothing from a 100k-character
document finishes in seconds and wins every grid it appears in.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from bench.config import load_config
from bench.preflight import PreflightError, Probes, preflight

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
  repeats: 1
  stop_climbing_concurrency: true
  per_document_timeout_s: 1800
"""


@pytest.fixture
def config(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG)
    return load_config(path)


@pytest.fixture
def healthy() -> Probes:
    return Probes(
        list_models=lambda: ["muse-glimmer-30b", "nomic-embed-text"],
        complete=lambda: "OK",
        embed=lambda: [0.1] * 768,
        warm_up_entities=lambda: 3,
    )


def test_a_healthy_endpoint_passes(config, healthy: Probes) -> None:
    preflight(config, healthy)


def test_the_extraction_model_missing_from_the_listing_is_named(config, healthy: Probes) -> None:
    """llama-swap lists every model it is configured for. One of the two
    absent is the partial-run failure this check exists for."""
    probes = replace(healthy, list_models=lambda: ["nomic-embed-text"])

    with pytest.raises(PreflightError, match="muse-glimmer-30b"):
        preflight(config, probes)


def test_the_embedding_model_missing_from_the_listing_is_named(config, healthy: Probes) -> None:
    """Checked separately from extraction: a check that passes when *either*
    model is present agrees with the correct one on every healthy endpoint."""
    probes = replace(healthy, list_models=lambda: ["muse-glimmer-30b"])

    with pytest.raises(PreflightError, match="nomic-embed-text"):
        preflight(config, probes)


def test_an_empty_completion_is_refused(config, healthy: Probes) -> None:
    """A listed model whose weights will not load answers with nothing.
    BACKLOG B12 is the standing example of trusting the listing alone."""
    with pytest.raises(PreflightError, match="completion"):
        preflight(config, replace(healthy, complete=lambda: "   "))


def test_a_wrong_embedding_dimension_is_refused_with_both_numbers(config, healthy: Probes) -> None:
    """768 is configured; llama-swap serving a different embedding model
    under the same id returns something else."""
    with pytest.raises(PreflightError, match=r"384.*768|768.*384"):
        preflight(config, replace(healthy, embed=lambda: [0.1] * 384))


def test_a_warm_up_extracting_nothing_is_refused(config, healthy: Probes) -> None:
    """The check that costs the most and matters the most.

    Both models can be listed, answering, and correctly dimensioned while
    extraction returns an empty graph -- a schema the model will not fill, a
    prompt it ignores. That run is the fastest in the grid.
    """
    with pytest.raises(PreflightError, match="no entities"):
        preflight(config, replace(healthy, warm_up_entities=lambda: 0))


def test_a_probe_that_raises_becomes_a_refusal_naming_the_endpoint(config, healthy: Probes) -> None:
    """A connection error must refuse rather than propagate a traceback the
    operator has to read to learn the endpoint is down."""

    def boom() -> list[str]:
        raise OSError("connection refused")

    with pytest.raises(PreflightError, match=re.escape("192.168.1.14")):
        preflight(config, replace(healthy, list_models=boom))
