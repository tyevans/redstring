#!/usr/bin/env python
"""Benchmark ingestion against a live endpoint, refusing a run worth nothing.

    uv run python scripts/benchmark.py
    uv run python scripts/benchmark.py --config bench/config.yaml --no-accuracy

Everything configurable lives in `bench/config.yaml`; this file wires the
pieces and owns no knobs of its own. The results land in `bench/results/` as
one JSON document per invocation, which is the artefact -- the console output
is a convenience.

**It refuses to start rather than warns**, for the reason in `bench/preflight.py`:
a broken benchmark run is fast, not slow, and reads as an improvement.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess  # nosec B404 -- see git_sha's docstring
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bench.config import BenchConfig, BenchConfigError, load_config  # noqa: E402
from bench.corpus import BenchCorpusError, load_document  # noqa: E402
from bench.preflight import PreflightError, Probes, preflight  # noqa: E402
from bench.report import build_report, write_report  # noqa: E402
from bench.runner import run_point  # noqa: E402
from bench.sweep import should_stop_climbing  # noqa: E402
from redstring import InMemoryGraphStore, SourceDocument, __version__, build_graph  # noqa: E402
from redstring.llm.adapters.langchain import LangChainLlmProvider  # noqa: E402
from redstring.llm.adapters.langchain_embedding import LangChainEmbeddingProvider  # noqa: E402

if TYPE_CHECKING:
    from tests.accuracy.runner import CorpusResult

    from bench.config import SweepPoint
    from bench.metrics import RunMetrics
    from redstring import LlmProvider

#: Short enough to cost seconds, rich enough that an extractor doing its job
#: cannot return nothing.
WARM_UP = (
    "Ada Lovelace was an English mathematician. She worked with Charles "
    "Babbage on the Analytical Engine."
)


def git_sha() -> str:
    """The short commit hash of the checkout being benchmarked, or "unknown".

    `# nosec B404 B603 B607` here and on the import above: the argv is a
    fixed list of literals (`["git", "rev-parse", "--short", "HEAD"]`) defined
    in this function, nothing reaches it from a caller, and `shell=True` is
    never used -- the same reasoning `scripts/coverage_ratchet.py` gives for
    its own `subprocess.run` calls. Any failure (a missing `git`, a checkout
    that is not a repo) is caught below and turned into `"unknown"` rather
    than propagating.
    """
    try:
        return subprocess.run(  # nosec B603 B607
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=ROOT,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def make_probes(
    config: BenchConfig, provider: LlmProvider, embedder: LangChainEmbeddingProvider
) -> Probes:
    """Build the four preflight probes against this invocation's config."""

    def list_models() -> list[str]:
        response = httpx.get(f"{config.endpoint.rstrip('/')}/models", timeout=30.0)
        response.raise_for_status()
        return [entry["id"] for entry in response.json()["data"]]

    def complete() -> str:
        response = httpx.post(
            f"{config.endpoint.rstrip('/')}/chat/completions",
            json={
                "model": config.extraction_model,
                "messages": [{"role": "user", "content": "Say the word OK and nothing else."}],
                # Generous: a reasoning model spends most of a short answer on
                # chain of thought, and a stingy probe skips a healthy server.
                "max_tokens": 2000,
            },
            timeout=180.0,
        )
        response.raise_for_status()
        return str(response.json()["choices"][0]["message"]["content"] or "")

    def embed() -> list[float]:
        return asyncio.run(embedder.embed(["probe"]))[0]

    def warm_up_entities() -> int:
        async def once() -> int:
            report = await build_graph(
                SourceDocument(id="warm-up", text=WARM_UP),
                provider=provider,
                store=InMemoryGraphStore(),
                tenant_id=uuid4(),
            )
            return report.entities

        return asyncio.run(once())

    return Probes(
        list_models=list_models, complete=complete, embed=embed, warm_up_entities=warm_up_entities
    )


async def run_sweep(
    config: BenchConfig, provider: LlmProvider
) -> tuple[list[RunMetrics], list[SweepPoint]]:
    """Run every point of the sweep, skipping a climb once it has reversed.

    A point that exceeds `policy.per_document_timeout_s` is recorded and
    skipped rather than allowed to propagate: a single long document must not
    discard every run that already completed, since those cost live GPU time
    and the operator would otherwise get nothing for twenty minutes of good
    measurements. A document that runs long at 12000 characters may complete
    fine at 3000, and that contrast is itself a result worth having.

    Returns the completed runs and the points that timed out, in that order.
    """
    runs: list[RunMetrics] = []
    timed_out: list[SweepPoint] = []
    documents = {doc_id: load_document(doc_id) for doc_id in config.long_documents}
    for point in config.sweep():
        if config.stop_climbing_concurrency and should_stop_climbing(runs, point):
            print(f"skipping {point}: a lower concurrency was already faster")
            continue
        print(f"running {point} ...", flush=True)
        try:
            result = await asyncio.wait_for(
                run_point(point, documents[point.document_id], provider=provider),
                timeout=config.per_document_timeout_s,
            )
        except TimeoutError:
            print(
                f"  timed out after {config.per_document_timeout_s}s; "
                f"skipping {point}, keeping runs already measured"
            )
            timed_out.append(point)
            continue
        print(
            f"  {result.wall_clock_s:.1f}s  {result.chunks} chunks  "
            f"{result.entities} entities  {result.model_calls} calls"
        )
        runs.append(result)
    return runs, timed_out


async def run_accuracy(provider: LlmProvider) -> CorpusResult:
    """Score the graded corpus. Imported locally: only needed on this path."""
    from tests.accuracy.corpus import load_corpus
    from tests.accuracy.runner import run_corpus

    return await run_corpus(load_corpus(), provider=provider)


def main() -> int:
    """Run the benchmark end to end.

    Exit codes name what went wrong, so a caller does not have to parse
    stderr to tell one refusal from another:

    - `0` -- ran to completion, nothing timed out.
    - `1` -- `PreflightError`: the endpoint cannot produce a measurement worth
      recording (bad model listing, empty completion, wrong embedding width,
      or a warm-up that extracted nothing).
    - `2` -- `BenchConfigError`: the config file is missing a required key or
      would produce a run that measures nothing.
    - `3` -- at least one sweep point timed out. A report is still written
      for the points that completed, but the caller must not treat this run
      as clean -- see `run_sweep`'s docstring.
    - `4` -- `BenchCorpusError`: a configured document is absent, blank, or
      missing the provenance metadata beside it. Distinct from `2` because a
      missing corpus file is a different problem from malformed YAML, and a
      caller scripting around this needs to tell them apart.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "bench" / "config.yaml")
    parser.add_argument("--results", type=Path, default=ROOT / "bench" / "results")
    parser.add_argument(
        "--no-accuracy",
        action="store_true",
        help="skip the graded corpus; timings only",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except BenchConfigError as error:
        print(f"config: {error}", file=sys.stderr)
        return 2

    provider = LangChainLlmProvider.openai_compatible(
        base_url=config.endpoint, model=config.extraction_model, api_key="local"
    )
    embedder = LangChainEmbeddingProvider.openai_compatible(
        base_url=config.endpoint,
        model=config.embedding_model,
        dimension=config.embedding_dimensions,
        api_key="local",
    )

    try:
        preflight(config, make_probes(config, provider, embedder))
    except PreflightError as error:
        print(f"preflight: {error}", file=sys.stderr)
        return 1

    started_at = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    try:
        runs, timed_out = asyncio.run(run_sweep(config, provider))
    except BenchCorpusError as error:
        print(f"corpus: {error}", file=sys.stderr)
        return 4

    # `--no-accuracy` is an escape hatch for a step `corpus.graded` already
    # gates: neither flag can force accuracy *on* by itself, so there is no
    # precedence question between them, only two independent ways to turn it
    # off.
    accuracy = (
        None if args.no_accuracy or not config.graded else asyncio.run(run_accuracy(provider))
    )

    path = write_report(
        build_report(
            config,
            runs,
            accuracy=accuracy,
            started_at=started_at,
            library_version=__version__,
            git_sha=git_sha(),
            timed_out=timed_out,
        ),
        directory=args.results,
        started_at=started_at,
    )
    print(f"\nwrote {path}")
    if timed_out:
        print(
            f"{len(timed_out)} point(s) timed out; report is partial -- see 'timed_out' in {path}",
            file=sys.stderr,
        )
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
