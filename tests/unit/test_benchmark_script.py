"""`scripts/benchmark.py`'s own control flow: the two failure paths a live
run must survive to be worth re-running.

Loaded the way `tests/unit/test_mutation_wrapper_refuses_a_bad_baseline.py`
loads `scripts/mutation.py` -- `scripts/` is not a package, so the module is
imported from its file path rather than by dotted name.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bench.config import BenchConfig, SweepPoint
from bench.corpus import BenchDocument

if TYPE_CHECKING:
    import pytest
    from pydantic import BaseModel

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "benchmark.py"
_spec = importlib.util.spec_from_file_location("_benchmark_script", _SCRIPT)
assert _spec is not None
assert _spec.loader is not None
benchmark = importlib.util.module_from_spec(_spec)
sys.modules["_benchmark_script"] = benchmark
_spec.loader.exec_module(benchmark)


def config(**overrides: Any) -> BenchConfig:
    fields: dict[str, Any] = {
        "endpoint": "http://example.invalid/v1/",
        "extraction_model": "model",
        "embedding_model": "embed-model",
        "embedding_dimensions": 8,
        "max_tokens": 16384,
        "graded": True,
        "long_documents": ("doc",),
        "chunk_sizes": (200,),
        "concurrencies": (1,),
        "repeats": 2,
        "stop_climbing_concurrency": True,
        "per_document_timeout_s": 60,
        "raw": {},
    }
    fields.update(overrides)
    return BenchConfig(**fields)


DOCUMENT = BenchDocument(
    id="doc",
    text="Ada Lovelace worked with Charles Babbage on the Analytical Engine. " * 10,
    source="https://example.com",
    retrieved="2026-08-13",
    licence="CC0",
)


class AlwaysFailingProvider:
    """Every call raises a bare exception -- not `LlmProviderError`, so
    `skip_failed_chunks` inside `build_graph` has no chance to absorb it and
    it reaches `run_point`'s caller as a document-level failure, the shape
    this fix is actually for (a transport blip `LlmProviderError` does not
    model, an unrecognised response shape, anything outside the chunk-retry
    path). A document that never once returns must not stop the sweep from
    reaching the point after it."""

    @property
    def model(self) -> str:
        return "flaky-model"

    async def extract(self, text: str, schema: Any, *, system_prompt: str | None = None) -> Any:
        raise RuntimeError("transport blip")


async def test_a_document_that_always_fails_is_recorded_and_the_sweep_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C1's second half: `run_sweep` widened its `except TimeoutError` to
    catch anything a point can raise, not just a timeout.

    Two repeats of one document/chunk_size/concurrency, both against a
    provider whose failure `skip_failed_chunks` cannot absorb. Before this
    fix the first failure would propagate out of `run_sweep` entirely and
    the second point would never run at all.
    """
    monkeypatch.setattr(benchmark, "load_document", lambda doc_id: DOCUMENT)

    runs, timed_out, failed = await benchmark.run_sweep(config(), AlwaysFailingProvider())

    assert runs == []
    assert timed_out == []
    assert len(failed) == 2, "both points must be recorded, not just the first"
    assert all(isinstance(point, SweepPoint) for point, _reason in failed)
    assert all(reason for _point, reason in failed)


async def test_a_point_that_succeeds_after_a_failing_one_is_still_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure path must not swallow a point that goes on to succeed --
    only the first repeat fails here, and the sweep must still report the
    second one as a completed run."""
    monkeypatch.setattr(benchmark, "load_document", lambda doc_id: DOCUMENT)

    class FailOnceProvider:
        def __init__(self) -> None:
            self._point = 0

        @property
        def model(self) -> str:
            return "flaky-model"

        async def extract(
            self, text: str, schema: type[BaseModel], *, system_prompt: str | None = None
        ) -> Any:
            self._point += 1
            if self._point == 1:
                raise RuntimeError("transport blip")
            return schema.model_validate({"entities": [], "relationships": []})

    runs, timed_out, failed = await benchmark.run_sweep(config(repeats=2), FailOnceProvider())

    assert timed_out == []
    assert len(failed) == 1
    assert len(runs) == 1


def test_a_failing_accuracy_pass_still_writes_a_report_with_accuracy_null(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """C2: the accuracy pass runs after the whole sweep and must not be able
    to discard it. `main` is exercised end to end with every network-facing
    seam stubbed, so what is under test is genuinely `main`'s own control
    flow rather than a live endpoint.
    """
    fake_config = config()
    monkeypatch.setattr(benchmark, "load_config", lambda path: fake_config)
    monkeypatch.setattr(benchmark, "build_provider", lambda cfg: object())
    monkeypatch.setattr(
        benchmark.LangChainEmbeddingProvider, "openai_compatible", lambda **kwargs: object()
    )
    monkeypatch.setattr(benchmark, "preflight", lambda cfg, probes: None)
    monkeypatch.setattr(benchmark, "make_probes", lambda cfg, provider, embedder: object())
    monkeypatch.setattr(benchmark, "load_document", lambda doc_id: DOCUMENT)

    async def fake_run_sweep(cfg: BenchConfig, provider: Any) -> tuple[list, list, list]:
        return [], [], []

    async def fake_run_accuracy(provider: Any) -> None:
        raise RuntimeError("the graded corpus blew up")

    monkeypatch.setattr(benchmark, "run_sweep", fake_run_sweep)
    monkeypatch.setattr(benchmark, "run_accuracy", fake_run_accuracy)
    monkeypatch.setattr(benchmark, "git_sha", lambda: "deadbeef")
    monkeypatch.setattr(
        sys, "argv", ["scripts/benchmark.py", "--config", "unused.yaml", "--results", str(tmp_path)]
    )

    exit_code = benchmark.main()

    assert exit_code == 3
    reports = list(tmp_path.glob("*.json"))
    assert len(reports) == 1
    written = json.loads(reports[0].read_text())
    assert written["accuracy"] is None
    assert written["runs"] == []


def test_accuracy_is_skipped_cleanly_when_the_config_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The non-failure case, for contrast: `graded: false` writes `accuracy:
    null` too, but exits 0 -- the two must be distinguishable by exit code
    alone, since the JSON shape is identical."""
    fake_config = config(graded=False)
    monkeypatch.setattr(benchmark, "load_config", lambda path: fake_config)
    monkeypatch.setattr(benchmark, "build_provider", lambda cfg: object())
    monkeypatch.setattr(
        benchmark.LangChainEmbeddingProvider, "openai_compatible", lambda **kwargs: object()
    )
    monkeypatch.setattr(benchmark, "preflight", lambda cfg, probes: None)
    monkeypatch.setattr(benchmark, "make_probes", lambda cfg, provider, embedder: object())
    monkeypatch.setattr(benchmark, "load_document", lambda doc_id: DOCUMENT)

    async def fake_run_sweep(cfg: BenchConfig, provider: Any) -> tuple[list, list, list]:
        return [], [], []

    async def fake_run_accuracy(provider: Any) -> None:
        raise AssertionError("must not be called when corpus.graded is false")

    monkeypatch.setattr(benchmark, "run_sweep", fake_run_sweep)
    monkeypatch.setattr(benchmark, "run_accuracy", fake_run_accuracy)
    monkeypatch.setattr(benchmark, "git_sha", lambda: "deadbeef")
    monkeypatch.setattr(
        sys, "argv", ["scripts/benchmark.py", "--config", "unused.yaml", "--results", str(tmp_path)]
    )

    exit_code = benchmark.main()

    assert exit_code == 0
    written = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert written["accuracy"] is None
