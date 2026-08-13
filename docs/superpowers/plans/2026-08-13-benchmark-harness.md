# Benchmark Harness (Deliverable A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reconfigurable benchmark that measures redstring ingestion speed, accuracy and stability against a live OpenAI-compatible endpoint, on **unmodified library code**, and writes a machine-readable baseline.

**Architecture:** A `bench/` package holds the logic (config, preflight, metrics, runner, report) and `scripts/benchmark.py` is a thin CLI over it. Every knob lives in `bench/config.yaml`; every run writes one JSON file to `bench/results/` with the resolved config embedded. The harness refuses to start against an endpoint it cannot prove is serving both models and producing entities, because a broken run reports the fastest number in the grid.

**Tech Stack:** Python 3.13, `uv`, `pyyaml` (already a dependency — `tests/accuracy/corpus.py` imports it), `httpx` (already used by `tests/integration/llm/test_live_endpoint.py`), `pytest` for the harness's own unit tests.

**Spec:** `docs/superpowers/specs/2026-08-13-ingestion-benchmark-design.md`

## Global Constraints

- **The library is not modified in this plan.** No file under `src/redstring/` is created, edited or deleted. Deliverables B (progress port) and C (bounded concurrency) get their own plans; this one produces the baseline they will be judged against.
- **Endpoint**: `http://192.168.1.14:8080/v1/`. Extraction model `muse-glimmer-30b`. Embedding model `nomic-embed-text`, dimension **768**.
- **Coverage ratchet is unaffected**: `[tool.coverage.run] source = ["src/redstring"]`, so `bench/` is not measured. Do not add it.
- **Quality gates run on `git commit`.** Do not run `ruff`, `bandit`, `lint-imports` or `pytest` as separate pre-commit steps. Write, then commit; re-`git add` and re-commit when the hook fixes files in place.
- **Deferred work goes in `BACKLOG.md` in the same commit that defers it.** Not a TODO comment, not a commit message.
- **`concurrency` may only be `1` in this deliverable.** The axis exists in config so the sweep shape is settled, but the library is serial; any other value must be refused with a message naming deliverable C. A silently-ignored knob is worse than an absent one.
- **`time_to_first_entity_s` is recorded as `null`** in this deliverable. It is unmeasurable without the progress port. Do not approximate it from provider call counts.
- Every unit test in this plan runs in the default commit gate (no marker, no network). The live path is exercised only by `scripts/benchmark.py`.

---

### Task 1: Config loading and sweep expansion

`bench/config.py` turns `bench/config.yaml` into a frozen `BenchConfig` and expands the sweep matrix into an ordered list of `SweepPoint`. Everything downstream takes a `SweepPoint`, so no other module parses YAML.

**Files:**
- Create: `bench/__init__.py`
- Create: `bench/config.py`
- Test: `tests/unit/bench/__init__.py`, `tests/unit/bench/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `@dataclass(frozen=True, slots=True) SweepPoint(document_id: str, chunk_size: int, concurrency: int, repeat: int)`
  - `@dataclass(frozen=True, slots=True) BenchConfig` with fields `endpoint: str`, `extraction_model: str`, `embedding_model: str`, `embedding_dimensions: int`, `graded: bool`, `long_documents: tuple[str, ...]`, `chunk_sizes: tuple[int, ...]`, `concurrencies: tuple[int, ...]`, `repeats: int`, `stop_climbing_concurrency: bool`, `per_document_timeout_s: float`, `raw: dict[str, object]`
  - `BenchConfig.sweep() -> tuple[SweepPoint, ...]`
  - `load_config(path: Path) -> BenchConfig`
  - `class BenchConfigError(Exception)`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/bench/__init__.py` as an empty file, then `tests/unit/bench/test_config.py`:

```python
"""Config is the only YAML reader in the harness, so it is the only place a
malformed run can be refused before it costs twenty minutes of GPU time."""

from __future__ import annotations

from pathlib import Path

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
    assert len({p for p in config.sweep()}) == 8


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/bench/test_config.py -v -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench'`

- [ ] **Step 3: Write the implementation**

Create `bench/__init__.py`:

```python
"""The ingestion benchmark: speed, accuracy and stability against a live model.

Not a test suite and not a gate. It runs on demand against a machine that is
not CI's, and its output is a committed JSON record read by a human. See
`docs/superpowers/specs/2026-08-13-ingestion-benchmark-design.md`.
"""
```

Create `bench/config.py`:

```python
"""The only YAML reader in the harness.

Everything downstream takes a `SweepPoint` or a `BenchConfig`, so a malformed
run is refused here -- before the first model call rather than after twenty
minutes of them. `scripts/mutation.py` takes the same posture for the same
reason: the expensive failure is the one that produces a plausible number.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any

import yaml


class BenchConfigError(Exception):
    """The config cannot produce a run worth making."""


@dataclass(frozen=True, slots=True)
class SweepPoint:
    """One timed run: a document at a chunk size and concurrency, once."""

    document_id: str
    chunk_size: int
    concurrency: int
    repeat: int


@dataclass(frozen=True, slots=True)
class BenchConfig:
    """A whole invocation's worth of knobs, resolved."""

    endpoint: str
    extraction_model: str
    embedding_model: str
    embedding_dimensions: int
    graded: bool
    long_documents: tuple[str, ...]
    chunk_sizes: tuple[int, ...]
    concurrencies: tuple[int, ...]
    repeats: int
    stop_climbing_concurrency: bool
    per_document_timeout_s: float
    #: The parsed YAML exactly as written, embedded verbatim in the results
    #: file. A result that cannot say what produced it is an anecdote, and
    #: reconstructing the document from the fields above would silently drop
    #: any key a later version of the file adds.
    raw: dict[str, Any]

    def sweep(self) -> tuple[SweepPoint, ...]:
        """Every timed run, document-slowest and repeat-fastest.

        The order is part of the contract. Repeats of one configuration run
        together so a stability comparison is not separated by a re-chunk of a
        100k-character document, and the document varies slowest so the whole
        grid for one document is contiguous in the results file.
        """
        return tuple(
            SweepPoint(
                document_id=document, chunk_size=chunk_size, concurrency=concurrency, repeat=repeat
            )
            for document, chunk_size, concurrency, repeat in product(
                self.long_documents, self.chunk_sizes, self.concurrencies, range(self.repeats)
            )
        )


def _require(document: dict[str, Any], *path: str) -> Any:
    """Fetch a nested key, naming the full path when it is absent."""
    cursor: Any = document
    for key in path:
        if not isinstance(cursor, dict) or key not in cursor:
            raise BenchConfigError(f"config is missing {'.'.join(path)}")
        cursor = cursor[key]
    return cursor


def load_config(path: Path) -> BenchConfig:
    """Read and validate a benchmark config.

    Raises:
        BenchConfigError: A required key is absent, or a value would produce a
            run that measures nothing.
    """
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise BenchConfigError(f"{path} is not a YAML mapping")

    concurrencies = tuple(_require(raw, "sweep", "concurrency"))
    if set(concurrencies) != {1}:
        raise BenchConfigError(
            f"concurrency {sorted(concurrencies)} needs deliverable C; the library "
            "extracts chunks serially, so any value but 1 would be ignored"
        )

    repeats = int(_require(raw, "policy", "repeats"))
    if repeats < 1:
        raise BenchConfigError(
            f"policy.repeats is {repeats}; a sweep of zero runs measures nothing"
        )

    return BenchConfig(
        endpoint=str(_require(raw, "endpoint")),
        extraction_model=str(_require(raw, "models", "extraction")),
        embedding_model=str(_require(raw, "models", "embedding")),
        embedding_dimensions=int(_require(raw, "models", "embedding_dimensions")),
        graded=bool(_require(raw, "corpus", "graded")),
        long_documents=tuple(str(d) for d in _require(raw, "corpus", "long")),
        chunk_sizes=tuple(int(c) for c in _require(raw, "sweep", "chunk_size")),
        concurrencies=tuple(int(c) for c in concurrencies),
        repeats=repeats,
        stop_climbing_concurrency=bool(_require(raw, "policy", "stop_climbing_concurrency")),
        per_document_timeout_s=float(_require(raw, "policy", "per_document_timeout_s")),
        raw=raw,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/bench/test_config.py -v -p no:randomly`
Expected: PASS, 7 passed

- [ ] **Step 5: Commit**

```bash
git add bench/__init__.py bench/config.py tests/unit/bench/
git commit -m "Read the benchmark config in one place, and refuse a sweep that measures nothing

Every knob lands here so no other module parses YAML. Two refusals are the
point of the module rather than validation ceremony: a concurrency above 1
would be silently ignored by a serial library and make deliverable C look
like it changed nothing, and zero repeats produces an empty sweep that runs
instantly and reports success."
```

---

### Task 2: Gap summary, and why the mean is not enough

`bench/metrics.py` holds the per-run record and the one piece of arithmetic in the harness: turning a list of inter-event intervals into the summary a human reads.

**Files:**
- Create: `bench/metrics.py`
- Test: `tests/unit/bench/test_metrics.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `@dataclass(frozen=True, slots=True) GapSummary(p50: float, p95: float, maximum: float, count: int)`
  - `summarise_gaps(gaps: Sequence[float]) -> GapSummary | None` — `None` for fewer than two gaps
  - `@dataclass(frozen=True, slots=True) RunMetrics` with fields `point: SweepPoint`, `wall_clock_s: float`, `time_to_first_entity_s: float | None`, `event_gaps_s: tuple[float, ...]`, `model_calls: int`, `extract_s: float`, `consolidate_s: float`, `chunks: int`, `entities: int`, `relationships: int`, `failed_chunks: int`, `unresolved_relationships: int`, `entity_names: tuple[str, ...]`
  - `RunMetrics.gaps` property returning `GapSummary | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/bench/test_metrics.py`:

```python
"""Perceived responsiveness is a distribution, so the summariser is tested
against a run whose mean hides its worst gap."""

from __future__ import annotations

import pytest

from bench.config import SweepPoint
from bench.metrics import RunMetrics, summarise_gaps

POINT = SweepPoint(document_id="d", chunk_size=3000, concurrency=1, repeat=0)


def metrics(**overrides: object) -> RunMetrics:
    """A run with every field set, so a test can vary exactly one."""
    fields: dict[str, object] = {
        "point": POINT,
        "wall_clock_s": 10.0,
        "time_to_first_entity_s": None,
        "event_gaps_s": (),
        "model_calls": 3,
        "extract_s": 8.0,
        "consolidate_s": 2.0,
        "chunks": 3,
        "entities": 12,
        "relationships": 7,
        "failed_chunks": 0,
        "unresolved_relationships": 1,
        "entity_names": ("ada lovelace",),
    }
    fields.update(overrides)
    return RunMetrics(**fields)  # type: ignore[arg-type]


def test_an_even_run_and_a_spiky_run_share_a_mean_and_differ_at_p95() -> None:
    """The whole reason the gap list is stored rather than averaged.

    3,3,3 and 1,1,7 both average 3. One of them stalls for seven seconds and
    the other never stalls, and a caller watching a progress bar can tell the
    difference immediately. A summary that reports only the mean cannot.
    """
    even = summarise_gaps([3.0, 3.0, 3.0])
    spiky = summarise_gaps([1.0, 1.0, 7.0])

    assert even is not None
    assert spiky is not None
    assert even.maximum == 3.0
    assert spiky.maximum == 7.0
    assert spiky.p95 > even.p95


def test_the_percentiles_are_the_values_they_name() -> None:
    """Literal expectations, not expectations phrased in terms of the input.

    Nearest-rank on ten sorted values: p50 is the 5th, p95 is the 10th.
    Written as literals so that an implementation using a different
    interpolation is a failure rather than a redefinition.
    """
    summary = summarise_gaps([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])

    assert summary is not None
    assert summary.p50 == 5.0
    assert summary.p95 == 10.0
    assert summary.maximum == 10.0
    assert summary.count == 10


def test_the_input_order_does_not_change_the_summary() -> None:
    """A summariser that forgets to sort passes every already-sorted case."""
    assert summarise_gaps([10.0, 1.0, 5.0, 2.0]) == summarise_gaps([1.0, 2.0, 5.0, 10.0])


@pytest.mark.parametrize("gaps", [[], [4.0]])
def test_fewer_than_two_gaps_is_no_summary_rather_than_zeroes(gaps: list[float]) -> None:
    """A single gap describes no rhythm, and reporting p95=4.0 from one
    sample invites reading it as one."""
    assert summarise_gaps(gaps) is None


def test_the_run_exposes_its_own_summary() -> None:
    assert metrics(event_gaps_s=(1.0, 1.0, 7.0)).gaps == summarise_gaps([1.0, 1.0, 7.0])


def test_a_baseline_run_carries_no_time_to_first_entity() -> None:
    """Recorded as absent, never approximated. See the plan's constraints."""
    assert metrics().time_to_first_entity_s is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/bench/test_metrics.py -v -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench.metrics'`

- [ ] **Step 3: Write the implementation**

Create `bench/metrics.py`:

```python
"""What one timed run produced, and the one piece of arithmetic in the harness.

`event_gaps_s` is stored as the whole list and summarised on the way out.
Perceived responsiveness is a distribution: a run whose reports arrive at
3s, 3s, 3s and one that arrives at 1s, 1s, 7s share a mean and feel nothing
alike, so the mean is the one summary that must not be the only one kept.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil

from bench.config import SweepPoint


@dataclass(frozen=True, slots=True)
class GapSummary:
    """Nearest-rank percentiles over the intervals between progress reports."""

    p50: float
    p95: float
    maximum: float
    count: int


def _nearest_rank(ordered: Sequence[float], percentile: float) -> float:
    """The value at the nearest rank, with no interpolation.

    Interpolating between two samples invents a gap that no report actually
    took, which is the wrong shape for a measurement whose whole purpose is
    "how long did a human wait".
    """
    rank = max(1, ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def summarise_gaps(gaps: Sequence[float]) -> GapSummary | None:
    """Summarise inter-report intervals, or `None` when there is no rhythm.

    Fewer than two gaps is reported as absent rather than as zeroes: one
    interval describes no cadence, and a `p95` derived from a single sample
    reads exactly like one derived from a hundred.
    """
    if len(gaps) < 2:
        return None
    ordered = sorted(gaps)
    return GapSummary(
        p50=_nearest_rank(ordered, 0.50),
        p95=_nearest_rank(ordered, 0.95),
        maximum=ordered[-1],
        count=len(ordered),
    )


@dataclass(frozen=True, slots=True)
class RunMetrics:
    """One timed run of one document at one point in the sweep."""

    point: SweepPoint
    wall_clock_s: float
    #: `None` until the progress port lands (deliverable B). Unmeasurable from
    #: outside `build_graph`, and deliberately not approximated from provider
    #: call counts -- an estimate recorded here would make B's improvement
    #: unreadable against it.
    time_to_first_entity_s: float | None
    event_gaps_s: tuple[float, ...]
    model_calls: int
    extract_s: float
    consolidate_s: float
    chunks: int
    entities: int
    relationships: int
    failed_chunks: int
    unresolved_relationships: int
    #: Normalised entity names, kept for the stability comparison across
    #: repeats. Sorted by the runner so two runs are comparable directly.
    entity_names: tuple[str, ...]

    @property
    def gaps(self) -> GapSummary | None:
        return summarise_gaps(self.event_gaps_s)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/bench/test_metrics.py -v -p no:randomly`
Expected: PASS, 7 passed

- [ ] **Step 5: Commit**

```bash
git add bench/metrics.py tests/unit/bench/test_metrics.py
git commit -m "Keep the whole gap list, and summarise it at p50, p95 and max

A run reporting at 3s/3s/3s and one reporting at 1s/1s/7s share a mean and
feel nothing alike, so the mean is the one summary that must not be the only
one stored. Percentiles are nearest-rank: interpolating invents an interval
no report took, which is the wrong shape for 'how long did a human wait'.

Fewer than two gaps summarises to None rather than to zeroes, because a p95
from one sample reads exactly like a p95 from a hundred."
```

---

### Task 3: Stability as a set comparison, named stability

`bench/stability.py` compares the entity names two repeats produced. It is deliberately a separate module from accuracy so the two numbers cannot be printed under one heading by accident.

**Files:**
- Create: `bench/stability.py`
- Test: `tests/unit/bench/test_stability.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `@dataclass(frozen=True, slots=True) Stability(jaccard: float, always: int, sometimes: int, runs: int)`
  - `stability_of(runs: Sequence[Sequence[str]]) -> Stability | None` — `None` for fewer than two runs

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/bench/test_stability.py`:

```python
"""Stability is agreement between repeats. It is not accuracy, and the test
that says so loudest is the one where a pipeline extracting one wrong entity
every time scores a perfect 1.0."""

from __future__ import annotations

from bench.stability import stability_of


def test_identical_runs_agree_completely() -> None:
    assert stability_of([["a", "b"], ["a", "b"]]).jaccard == 1.0


def test_a_consistently_wrong_pipeline_also_scores_one() -> None:
    """The metric's own limit, pinned so nobody reads it as correctness.

    A pipeline that finds one entity in a document naming forty, every single
    time, is perfectly stable. That is why the field is called stability and
    why accuracy is scored separately against the graded corpus.
    """
    assert stability_of([["wrong"], ["wrong"], ["wrong"]]).jaccard == 1.0


def test_disjoint_runs_agree_not_at_all() -> None:
    assert stability_of([["a"], ["b"]]).jaccard == 0.0


def test_partial_agreement_is_intersection_over_union() -> None:
    """Three runs: 'a' in all three, 'b' in two, 'c' in one.

    Union is 3, intersection is 1, so 1/3. Written as a literal rather than
    as a formula over the inputs.
    """
    result = stability_of([["a", "b"], ["a", "b", "c"], ["a"]])

    assert result.jaccard == 1 / 3
    assert result.always == 1
    assert result.sometimes == 2
    assert result.runs == 3


def test_a_repeated_name_within_one_run_does_not_inflate_agreement() -> None:
    """Runs are compared as sets. A run listing 'a' twice agrees with a run
    listing it once, and an implementation counting occurrences does not."""
    assert stability_of([["a", "a"], ["a"]]).jaccard == 1.0


def test_one_run_is_no_stability_rather_than_perfect_stability() -> None:
    """A single run agrees with itself trivially, and reporting 1.0 for it
    would make a misconfigured `repeats: 1` sweep look maximally stable."""
    assert stability_of([["a", "b"]]) is None


def test_no_runs_is_no_stability() -> None:
    assert stability_of([]) is None


def test_two_empty_runs_are_no_stability_rather_than_perfect_agreement() -> None:
    """Two runs that extracted nothing agree on nothing, and 0/0 must not be
    reported as 1.0 -- that is the exact number a dead endpoint produces."""
    assert stability_of([[], []]) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/bench/test_stability.py -v -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench.stability'`

- [ ] **Step 3: Write the implementation**

Create `bench/stability.py`:

```python
"""Agreement between repeats of the same run. Never called accuracy.

Both sides of this comparison are produced by the code under test, so it
cannot distinguish a correct pipeline from a consistently incomplete one --
CLAUDE.md records the same shape letting three broken handlers pass a
replay-equivalence suite. What it can see is *variance*, which is the only
question it is asked: the risk in bounded concurrency (deliverable C) is
naming drift at chunk boundaries, and drift shows up here as instability.

Correctness is scored separately, against the graded corpus, by
`tests.accuracy`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Stability:
    """How much two or more repeats agreed about which entities exist."""

    #: Intersection over union of the entity-name sets.
    jaccard: float
    #: Names every run found.
    always: int
    #: Names some run found and some run missed. The number to look at when
    #: `jaccard` drops: it counts the entities whose presence is a coin flip.
    sometimes: int
    runs: int


def stability_of(runs: Sequence[Sequence[str]]) -> Stability | None:
    """Compare the entity names of repeated runs.

    Returns `None` when there is nothing to compare -- fewer than two runs, or
    two runs that both extracted nothing. The empty case matters: 0/0 defined
    as 1.0 would report a dead endpoint as maximally stable, which is the
    failure this harness exists to refuse.
    """
    if len(runs) < 2:
        return None

    sets = [set(run) for run in runs]
    union = set[str]().union(*sets)
    if not union:
        return None

    intersection = set(sets[0]).intersection(*sets[1:])
    return Stability(
        jaccard=len(intersection) / len(union),
        always=len(intersection),
        sometimes=len(union) - len(intersection),
        runs=len(runs),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/bench/test_stability.py -v -p no:randomly`
Expected: PASS, 8 passed

- [ ] **Step 5: Commit**

```bash
git add bench/stability.py tests/unit/bench/test_stability.py
git commit -m "Score agreement between repeats, and pin what it cannot see

Both sides of a self-consistency comparison come from the code under test, so
a pipeline dropping half of every document deterministically scores 1.0. That
limit is a test rather than a caveat in a docstring, alongside the two cases
where the metric must decline to answer: one run, and two runs that both
extracted nothing. 0/0 as 1.0 would report a dead endpoint as maximally
stable."
```

---

### Task 4: The long-document corpus, with provenance required

`bench/corpus.py` loads `bench/corpus/<id>.txt` and refuses one without a `.meta.yaml` beside it. redstring never fetches; the operator commits the text, and the metadata is what makes that defensible.

**Files:**
- Create: `bench/corpus.py`
- Test: `tests/unit/bench/test_corpus.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `@dataclass(frozen=True, slots=True) BenchDocument(id: str, text: str, source: str, retrieved: str, licence: str)`
  - `load_document(document_id: str, *, root: Path) -> BenchDocument`
  - `class BenchCorpusError(Exception)`
  - `CORPUS_ROOT: Final[Path]` — `Path(__file__).parent / "corpus"`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/bench/test_corpus.py`:

```python
"""A benchmark document is third-party text living in the repository. The
metadata beside it is what makes that a decision rather than an accident."""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.corpus import BenchCorpusError, load_document

META = """
source: https://en.wikipedia.org/wiki/Harry_Potter_and_the_Philosopher's_Stone
retrieved: 2026-08-13
licence: CC BY-SA 4.0
"""


def seed(root: Path, document_id: str = "hp1", *, text: str = "Harry met Hagrid.") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{document_id}.txt").write_text(text)
    (root / f"{document_id}.meta.yaml").write_text(META)
    return root


def test_the_text_and_its_provenance_load_together(tmp_path: Path) -> None:
    document = load_document("hp1", root=seed(tmp_path))

    assert document.id == "hp1"
    assert document.text == "Harry met Hagrid."
    assert document.retrieved == "2026-08-13"
    assert document.licence == "CC BY-SA 4.0"
    assert document.source.startswith("https://en.wikipedia.org/")


def test_text_without_metadata_is_refused(tmp_path: Path) -> None:
    """Committed third-party text whose origin nobody recorded."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "orphan.txt").write_text("some text")

    with pytest.raises(BenchCorpusError, match="orphan.meta.yaml"):
        load_document("orphan", root=tmp_path)


def test_metadata_missing_a_field_names_the_field(tmp_path: Path) -> None:
    root = seed(tmp_path)
    (root / "hp1.meta.yaml").write_text("source: https://example.com\nretrieved: 2026-08-13\n")

    with pytest.raises(BenchCorpusError, match="licence"):
        load_document("hp1", root=root)


def test_an_absent_document_names_the_path_it_looked_for(tmp_path: Path) -> None:
    with pytest.raises(BenchCorpusError, match="missing.txt"):
        load_document("missing", root=tmp_path)


def test_an_empty_document_is_refused(tmp_path: Path) -> None:
    """An empty document extracts nothing in no time -- the fastest run in
    any grid, and a benchmark's version of a zero-survivor mutation run."""
    with pytest.raises(BenchCorpusError, match="empty"):
        load_document("hp1", root=seed(tmp_path, text="   \n  "))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/bench/test_corpus.py -v -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench.corpus'`

- [ ] **Step 3: Write the implementation**

Create `bench/corpus.py`:

```python
"""The long documents, and the provenance that makes committing them a decision.

redstring never fetches, and the benchmark does not either: the operator puts
the text in `bench/corpus/` and records where it came from beside it. A
document without a `.meta.yaml` is refused rather than defaulted, because a
default here is third-party text in a repository with nobody's name on the
decision to put it there.

These documents are **ungraded**, so nothing scored against them is accuracy.
See BACKLOG B-BENCH-1 for why grading a 100k-character document was deferred
rather than skipped.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml

CORPUS_ROOT: Final[Path] = Path(__file__).parent / "corpus"

_REQUIRED_META: Final[tuple[str, ...]] = ("source", "retrieved", "licence")


class BenchCorpusError(Exception):
    """A benchmark document is absent, empty, or unattributed."""


@dataclass(frozen=True, slots=True)
class BenchDocument:
    """One long document and where it came from."""

    id: str
    text: str
    source: str
    retrieved: str
    licence: str


def load_document(document_id: str, *, root: Path = CORPUS_ROOT) -> BenchDocument:
    """Load a benchmark document and its provenance.

    Raises:
        BenchCorpusError: The text is missing, blank, or has no metadata
            beside it naming source, retrieval date and licence.
    """
    text_path = root / f"{document_id}.txt"
    meta_path = root / f"{document_id}.meta.yaml"

    if not text_path.is_file():
        raise BenchCorpusError(f"no benchmark document at {text_path}")
    if not meta_path.is_file():
        raise BenchCorpusError(
            f"{text_path.name} has no provenance; write {meta_path.name} naming "
            f"{', '.join(_REQUIRED_META)}"
        )

    text = text_path.read_text()
    if not text.strip():
        raise BenchCorpusError(
            f"{text_path} is empty; an empty document is the fastest run there is"
        )

    meta = yaml.safe_load(meta_path.read_text()) or {}
    missing = [key for key in _REQUIRED_META if not meta.get(key)]
    if missing:
        raise BenchCorpusError(f"{meta_path.name} is missing {', '.join(missing)}")

    return BenchDocument(
        id=document_id,
        text=text,
        source=str(meta["source"]),
        retrieved=str(meta["retrieved"]),
        licence=str(meta["licence"]),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/bench/test_corpus.py -v -p no:randomly`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add bench/corpus.py tests/unit/bench/test_corpus.py
git commit -m "Load benchmark documents, refusing text nobody attributed

redstring never fetches and neither does the benchmark: the operator commits
the text and records where it came from beside it. A missing .meta.yaml is a
refusal rather than a default, because the default is third-party text in a
repository with nobody's name on the decision.

An empty document is refused for the same reason a zero-entity warm-up will
be: it extracts nothing in no time and wins every grid."
```

---

### Task 5: The timing provider, and the clock it reads

`bench/instruments.py` wraps an `LlmProvider` to count calls and split extraction time from consolidation time, and takes its clock as an argument so the tests assert literal durations.

**Files:**
- Create: `bench/instruments.py`
- Test: `tests/unit/bench/test_instruments.py`

**Interfaces:**
- Consumes: `redstring.LlmProvider` (the port, unmodified).
- Produces:
  - `class TimingProvider` — `TimingProvider(inner: LlmProvider, *, clock: Callable[[], float])`, satisfies `LlmProvider`, exposes `calls: int`, `elapsed_s: float`, `phase: str` (settable), `elapsed_in(phase: str) -> float`, `call_starts: tuple[float, ...]`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/bench/test_instruments.py`:

```python
"""The wrapper is the only thing that can see inside `build_graph` before the
progress port exists, so what it measures has to be pinned exactly."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from bench.instruments import TimingProvider


class Answer(BaseModel):
    value: str = "x"


class FakeClock:
    """A clock that advances only when a test says so."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class ScriptedProvider:
    """An `LlmProvider` that advances a clock instead of doing work."""

    def __init__(self, clock: FakeClock, *, takes: float) -> None:
        self._clock = clock
        self._takes = takes
        self.seen: list[str] = []

    @property
    def model(self) -> str:
        return "scripted-model"

    async def extract(self, text: str, schema: Any, *, system_prompt: str | None = None) -> Any:
        self.seen.append(text)
        self._clock.now += self._takes
        return schema()


async def test_it_counts_the_calls_it_forwards() -> None:
    clock = FakeClock()
    provider = TimingProvider(ScriptedProvider(clock, takes=1.0), clock=clock)

    await provider.extract("a", Answer)
    await provider.extract("b", Answer)

    assert provider.calls == 2


async def test_it_reports_the_time_the_calls_took() -> None:
    """Two calls of 1.5s each is 3.0s. A literal, not `2 * takes`."""
    clock = FakeClock()
    provider = TimingProvider(ScriptedProvider(clock, takes=1.5), clock=clock)

    await provider.extract("a", Answer)
    await provider.extract("b", Answer)

    assert provider.elapsed_s == 3.0


async def test_time_between_calls_is_not_counted_as_model_time() -> None:
    """The wrapper measures the model, not the run.

    An implementation timing from the first call to the last would report
    5.0s here and be indistinguishable from the correct one on any test where
    the calls are back to back.
    """
    clock = FakeClock()
    provider = TimingProvider(ScriptedProvider(clock, takes=1.0), clock=clock)

    await provider.extract("a", Answer)
    clock.now += 3.0  # merging, mapping, whatever the pipeline does between calls
    await provider.extract("b", Answer)

    assert provider.elapsed_s == 2.0


async def test_calls_are_attributed_to_the_phase_that_was_running() -> None:
    """Extraction and consolidation both call the same provider. Without the
    phase label the sixteen minutes cannot be split, which is the first
    question to ask of a slow run."""
    clock = FakeClock()
    provider = TimingProvider(ScriptedProvider(clock, takes=2.0), clock=clock)

    provider.phase = "extract"
    await provider.extract("a", Answer)
    provider.phase = "consolidate"
    await provider.extract("b", Answer)

    assert provider.elapsed_in("extract") == 2.0
    assert provider.elapsed_in("consolidate") == 2.0
    assert provider.elapsed_in("embed") == 0.0


async def test_a_failing_call_is_still_counted_and_still_timed() -> None:
    """A run that fails halfway must not report that the model was idle.

    A wrapper recording after the await never sees a failure, and every test
    with a healthy provider passes against it.
    """

    class Failing:
        @property
        def model(self) -> str:
            return "failing"

        async def extract(self, text: str, schema: Any, *, system_prompt: str | None = None) -> Any:
            clock.now += 4.0
            raise RuntimeError("boom")

    clock = FakeClock()
    provider = TimingProvider(Failing(), clock=clock)

    with pytest.raises(RuntimeError):
        await provider.extract("a", Answer)

    assert provider.calls == 1
    assert provider.elapsed_s == 4.0


async def test_the_wrapped_model_name_shows_through() -> None:
    """`Entity.provenance.model` comes from here. A wrapper reporting its own
    name would stamp every benchmarked entity with the wrong provenance."""
    clock = FakeClock()

    assert TimingProvider(ScriptedProvider(clock, takes=0.0), clock=clock).model == "scripted-model"


async def test_the_arguments_reach_the_inner_provider_unchanged() -> None:
    clock = FakeClock()
    inner = ScriptedProvider(clock, takes=0.0)

    await TimingProvider(inner, clock=clock).extract("the text", Answer, system_prompt="sys")

    assert inner.seen == ["the text"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/bench/test_instruments.py -v -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench.instruments'`

- [ ] **Step 3: Write the implementation**

Create `bench/instruments.py`:

```python
"""The only seam into `build_graph` before the progress port exists.

`LlmProvider` is a single-method protocol, so wrapping it is how the harness
learns anything about a run in progress. That buys a call count and a
per-phase split of model time; it does **not** buy time-to-first-entity,
because a returned completion is not a mapped entity and the wrapper cannot
see the merge. That field stays `None` until deliverable B rather than being
estimated here -- an estimate recorded in the field B will fill makes B's
improvement unreadable.

The clock is a parameter so the tests assert literal durations against a
clock they advance by hand, rather than sleeping and asserting a tolerance.
"""

from __future__ import annotations

from collections import defaultdict
from time import perf_counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

    from redstring import LlmProvider


class TimingProvider:
    """Count and time the calls an extraction makes, attributing each to a phase.

    Satisfies `LlmProvider` structurally; the pipeline cannot tell it apart
    from the adapter it wraps, which is the point -- an instrument that
    changes the run measures itself.
    """

    def __init__(self, inner: LlmProvider, *, clock: Callable[[], float] = perf_counter) -> None:
        self._inner = inner
        self._clock = clock
        self._elapsed: defaultdict[str, float] = defaultdict(float)
        self.calls = 0
        #: Which phase the caller believes is running. The runner sets it;
        #: nothing in the library knows about it.
        self.phase = "extract"
        self.call_starts: list[float] = []

    @property
    def model(self) -> str:
        """The wrapped provider's model, so provenance is unaffected."""
        return self._inner.model

    @property
    def elapsed_s(self) -> float:
        """Total time inside model calls, across every phase."""
        return sum(self._elapsed.values())

    def elapsed_in(self, phase: str) -> float:
        """Time inside model calls made while `phase` was set."""
        return self._elapsed[phase]

    async def extract[S: BaseModel](
        self, text: str, schema: type[S], *, system_prompt: str | None = None
    ) -> S:
        """Forward the call, recording what it cost even when it raises.

        The bookkeeping is in a `finally` deliberately: a failed call is model
        time that was spent, and a wrapper that records only successes reports
        an idle model for the run that most needs explaining.
        """
        started = self._clock()
        self.calls += 1
        self.call_starts.append(started)
        current = self.phase
        try:
            return await self._inner.extract(text, schema, system_prompt=system_prompt)
        finally:
            self._elapsed[current] += self._clock() - started

    def __getattr__(self, name: str) -> Any:
        """Defer anything else to the wrapped provider."""
        return getattr(self._inner, name)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/bench/test_instruments.py -v -p no:randomly`
Expected: PASS, 7 passed

- [ ] **Step 5: Commit**

```bash
git add bench/instruments.py tests/unit/bench/test_instruments.py
git commit -m "Wrap the provider to count and time calls, per phase

LlmProvider is a single-method protocol, so wrapping it is the only seam into
build_graph until the progress port lands. Two details are the whole module:
the bookkeeping is in a finally, because a failed call is model time that was
spent and a wrapper recording only successes reports an idle model for the
run that most needs explaining; and the clock is a parameter, so the tests
assert literal durations against a clock they advance rather than sleeping
and asserting a tolerance.

Time between calls is deliberately not counted -- an implementation timing
first-call to last-call agrees with this one on every back-to-back run."
```

---

### Task 6: Preflight, broken on purpose

`bench/preflight.py` refuses to start. Every probe is a function taking its dependencies as arguments so each refusal is unit-tested without a network.

**Files:**
- Create: `bench/preflight.py`
- Test: `tests/unit/bench/test_preflight.py`

**Interfaces:**
- Consumes: `bench.config.BenchConfig`.
- Produces:
  - `class PreflightError(Exception)`
  - `@dataclass(frozen=True, slots=True) Probes` with fields `list_models: Callable[[], Sequence[str]]`, `complete: Callable[[], str]`, `embed: Callable[[], Sequence[float]]`, `warm_up_entities: Callable[[], int]`
  - `def preflight(config: BenchConfig, probes: Probes) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/bench/test_preflight.py`:

```python
"""Every refusal here is broken on purpose, because a gate whose happy path
is 'the endpoint answered' is indistinguishable from no gate.

The failure this module exists for has a specific shape: a broken run is not
slow, it is *fast*. A pipeline extracting nothing from a 100k-character
document finishes in seconds and wins every grid it appears in.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from bench.config import load_config
from bench.preflight import PreflightError, Probes, preflight

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
    with pytest.raises(PreflightError, match="384.*768|768.*384"):
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

    with pytest.raises(PreflightError, match="192.168.1.14"):
        preflight(config, replace(healthy, list_models=boom))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/bench/test_preflight.py -v -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench.preflight'`

- [ ] **Step 3: Write the implementation**

Create `bench/preflight.py`:

```python
"""Refuse to start, rather than produce a plausible number.

`scripts/mutation.py` exists because an environment lying about the code is
undetectable from the output. A benchmark has the same hazard with the sign
flipped: a broken run is not slow, it is *fast*. A pipeline that extracts
nothing from a 100k-character document finishes in seconds and wins every
grid it appears in, and the reading is "the new chunk size is a huge win".

Four checks, each of which the three others cannot see:

- **Both model ids are listed.** Not "the endpoint answers": llama-swap lists
  every model it is configured for, and serving one of two produces a run
  that half works.
- **A real completion comes back non-empty.** A listed model whose weights
  will not load answers with nothing. BACKLOG B12 is this repository's
  standing example of trusting a model listing.
- **An embedding is the configured width.** A different embedding model
  behind the same id is a silent dimension change.
- **A warm-up extraction produces at least one entity.** Everything above can
  pass while extraction returns an empty graph.

Probes are injected so each refusal is unit-tested without a network. A gate
whose happy path is "the endpoint answered" has to be watched failing before
it is believed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from bench.config import BenchConfig


class PreflightError(Exception):
    """The endpoint cannot produce a measurement worth recording."""


@dataclass(frozen=True, slots=True)
class Probes:
    """The four questions asked of the endpoint before anything is timed."""

    list_models: Callable[[], Sequence[str]]
    complete: Callable[[], str]
    embed: Callable[[], Sequence[float]]
    warm_up_entities: Callable[[], int]


def _attempt[T](what: str, endpoint: str, probe: Callable[[], T]) -> T:
    try:
        return probe()
    except PreflightError:
        raise
    except Exception as error:  # noqa: BLE001 -- every failure is the same answer
        raise PreflightError(f"{what} failed against {endpoint}: {error!r}") from error


def preflight(config: BenchConfig, probes: Probes) -> None:
    """Check the endpoint can produce a measurement, or raise saying why.

    Raises:
        PreflightError: Naming which check failed and what it saw.
    """
    served = list(_attempt("model listing", config.endpoint, probes.list_models))
    for model in (config.extraction_model, config.embedding_model):
        if model not in served:
            raise PreflightError(
                f"{config.endpoint} does not serve {model}; it lists {sorted(served)}"
            )

    completion = _attempt("completion probe", config.endpoint, probes.complete)
    if not completion.strip():
        raise PreflightError(
            f"{config.extraction_model} is listed but returned an empty completion; "
            "a listed model whose weights will not load looks exactly like this"
        )

    vector = _attempt("embedding probe", config.endpoint, probes.embed)
    if len(vector) != config.embedding_dimensions:
        raise PreflightError(
            f"{config.embedding_model} returned {len(vector)} dimensions, "
            f"config expects {config.embedding_dimensions}"
        )

    if _attempt("warm-up extraction", config.endpoint, probes.warm_up_entities) < 1:
        raise PreflightError(
            "the warm-up extraction produced no entities; every timing below would "
            "measure a pipeline that extracts nothing, which is the fastest run there is"
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/bench/test_preflight.py -v -p no:randomly`
Expected: PASS, 7 passed

- [ ] **Step 5: Commit**

```bash
git add bench/preflight.py tests/unit/bench/test_preflight.py
git commit -m "Refuse to benchmark an endpoint that cannot produce a measurement

Four checks, each blind to the others' failure: both model ids listed, a
non-empty completion, an embedding of the configured width, and a warm-up
extraction producing at least one entity. The last is the expensive one and
the one that matters, because the three before it can all pass while
extraction returns an empty graph.

The shape being refused is specific: a broken benchmark run is not slow, it
is fast. A pipeline extracting nothing from a 100k-character document
finishes in seconds and reads as a huge win.

Probes are injected so every refusal is broken on purpose in the unit tests
-- a gate whose happy path is 'the endpoint answered' is indistinguishable
from no gate until it has been watched failing."
```

---

### Task 7: Running one sweep point

`bench/runner.py` executes a single `SweepPoint` against `build_graph` and returns `RunMetrics`. Tested entirely against a scripted provider and a hand-advanced clock — no network in the commit gate.

**Files:**
- Create: `bench/runner.py`
- Test: `tests/unit/bench/test_runner.py`

**Interfaces:**
- Consumes: `bench.config.SweepPoint`, `bench.corpus.BenchDocument`, `bench.metrics.RunMetrics`, `bench.instruments.TimingProvider`, and `redstring.build_graph` / `InMemoryGraphStore` / `SourceDocument`.
- Produces:
  - `async def run_point(point: SweepPoint, document: BenchDocument, *, provider: LlmProvider, clock: Callable[[], float] = perf_counter) -> RunMetrics`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/bench/test_runner.py`:

```python
"""One sweep point against a scripted provider, so the harness's own
arithmetic is proved before any live number is believed."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from bench.config import SweepPoint
from bench.corpus import BenchDocument
from bench.runner import run_point

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
    rest to defaults.
    """
    return schema.model_validate(
        {
            "entities": [
                {"name": "Ada Lovelace", "entity_type": "person"},
                {"name": "Charles Babbage", "entity_type": "person"},
            ],
            "relationships": [
                {
                    "source": "Ada Lovelace",
                    "target": "Charles Babbage",
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/bench/test_runner.py -v -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench.runner'`

- [ ] **Step 3: Write the implementation**

Create `bench/runner.py`:

```python
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

from redstring import InMemoryGraphStore, SourceDocument, build_graph
from redstring.extraction.chunkers import SlidingWindowChunker

from bench.instruments import TimingProvider
from bench.metrics import RunMetrics

if TYPE_CHECKING:
    from collections.abc import Callable

    from redstring import LlmProvider

    from bench.config import SweepPoint
    from bench.corpus import BenchDocument


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
        chunker=SlidingWindowChunker(default_chunk_size=point.chunk_size),
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/bench/test_runner.py -v -p no:randomly`
Expected: PASS, 7 passed

If `_two_entities` fails to validate, the extraction schema's field names differ from the assumption. Read them from `redstring.extraction.schema` and correct the fixture — do not weaken the assertion to accommodate an empty extraction, since a run producing no entities is what every other check in this harness exists to refuse.

- [ ] **Step 5: Commit**

```bash
git add bench/runner.py tests/unit/bench/test_runner.py
git commit -m "Run one sweep point and report what it cost

The chunk count comes off GraphBuildReport rather than from dividing the
document length by the chunk size: the default chunker respects sentence and
paragraph boundaries, so the arithmetic agrees with the pipeline only on text
that has neither, and a test asserts a smaller chunk size actually produces
more calls.

A fresh store and tenant per run, for the reason the accuracy runner gives --
a shared store makes the second repeat report double the entities and perfect
stability, which is exactly backwards."
```

---

### Task 8: The results file

`bench/report.py` turns a sweep's runs into one JSON document with the resolved config embedded.

**Files:**
- Create: `bench/report.py`
- Test: `tests/unit/bench/test_report.py`

**Interfaces:**
- Consumes: `bench.config.BenchConfig`, `bench.metrics.RunMetrics`, `bench.stability.Stability`, and `tests.accuracy.runner.CorpusResult | None`.
- Produces:
  - `def build_report(config: BenchConfig, runs: Sequence[RunMetrics], *, accuracy: CorpusResult | None, started_at: str, library_version: str, git_sha: str) -> dict[str, Any]`
  - `def write_report(report: dict[str, Any], *, directory: Path, started_at: str) -> Path`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/bench/test_report.py`:

```python
"""A result that cannot say what produced it is an anecdote."""

from __future__ import annotations

import json
from pathlib import Path

from bench.config import SweepPoint, load_config
from bench.metrics import RunMetrics
from bench.report import build_report, write_report

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


def config(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG)
    return load_config(path)


def run(repeat: int, *, names: tuple[str, ...], wall: float) -> RunMetrics:
    return RunMetrics(
        point=SweepPoint(document_id="hp1", chunk_size=3000, concurrency=1, repeat=repeat),
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
    """Repeats of one configuration are comparable; a 3000-char run and an
    8000-char run disagreeing is a finding, not instability."""
    report = build_report(
        config(tmp_path),
        two_runs(),
        accuracy=None,
        started_at="t",
        library_version="v",
        git_sha="s",
    )

    (group,) = report["stability"]
    assert group["chunk_size"] == 3000
    assert group["document_id"] == "hp1"
    assert group["jaccard"] == 0.5
    assert group["sometimes"] == 1


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/bench/test_report.py -v -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench.report'`

- [ ] **Step 3: Write the implementation**

Create `bench/report.py`:

```python
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

    from bench.config import BenchConfig
    from bench.metrics import RunMetrics
    from tests.accuracy.runner import CorpusResult


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
        "gaps": asdict(summary) if summary else None,
        "model_calls": run.model_calls,
        "extract_s": run.extract_s,
        "consolidate_s": run.consolidate_s,
        "chunks": run.chunks,
        "entities": run.entities,
        "relationships": run.relationships,
        "failed_chunks": run.failed_chunks,
        "unresolved_relationships": run.unresolved_relationships,
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
) -> dict[str, Any]:
    """Assemble one invocation's results.

    `accuracy` is `None` when the graded corpus did not run, and is written as
    a null rather than omitted -- an absent key reads as an older file format,
    a null reads as a decision.
    """
    return {
        "started_at": started_at,
        "library_version": library_version,
        "git_sha": git_sha,
        "config": config.raw,
        "runs": [_run_json(run) for run in runs],
        "stability": _stability_json(runs),
        "accuracy": _accuracy_json(accuracy) if accuracy is not None else None,
    }


def write_report(report: dict[str, Any], *, directory: Path, started_at: str) -> Path:
    """Write the report, named for when the run started."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{started_at}.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n")
    return path
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/bench/test_report.py -v -p no:randomly`
Expected: PASS, 6 passed

- [ ] **Step 5: Commit**

```bash
git add bench/report.py tests/unit/bench/test_report.py
git commit -m "Write one results file per run, carrying what produced it

The resolved config, the library version and the git sha travel with the
numbers: the endpoint is a machine that is not CI's and the model behind an
id can change without the id changing, so the config is the only record of
what was asked.

Stability is grouped per configuration rather than across the sweep. A
3000-character run disagreeing with an 8000-character run is a finding about
chunk size; folding them together would report it as instability."
```

---

### Task 9: The CLI

`scripts/benchmark.py` wires config, preflight, corpus, runner and report against the live endpoint. Its own logic is thin, and the one piece worth testing — the concurrency climb-stop — is a pure function.

**Files:**
- Create: `scripts/benchmark.py`
- Create: `bench/sweep.py`
- Test: `tests/unit/bench/test_sweep.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `def should_stop_climbing(runs: Sequence[RunMetrics], point: SweepPoint) -> bool` in `bench/sweep.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/bench/test_sweep.py`:

```python
"""The climb-stop is arithmetic over completed runs, so it is a pure
function rather than a branch buried in the CLI."""

from __future__ import annotations

from bench.config import SweepPoint
from bench.metrics import RunMetrics
from bench.sweep import should_stop_climbing


def run(*, concurrency: int, wall: float, document: str = "hp1", chunk: int = 3000) -> RunMetrics:
    return RunMetrics(
        point=SweepPoint(document_id=document, chunk_size=chunk, concurrency=concurrency, repeat=0),
        wall_clock_s=wall,
        time_to_first_entity_s=None,
        event_gaps_s=(),
        model_calls=3,
        extract_s=wall,
        consolidate_s=0.0,
        chunks=3,
        entities=2,
        relationships=1,
        failed_chunks=0,
        unresolved_relationships=0,
        entity_names=("a", "b"),
    )


def point(concurrency: int, *, document: str = "hp1", chunk: int = 3000) -> SweepPoint:
    return SweepPoint(document_id=document, chunk_size=chunk, concurrency=concurrency, repeat=0)


def test_a_slower_higher_concurrency_stops_the_climb() -> None:
    """K=4 slower than K=2 is the backend queueing. K=8 would measure the
    queue for twenty minutes and report it as a benchmark."""
    completed = [run(concurrency=2, wall=100.0), run(concurrency=4, wall=140.0)]

    assert should_stop_climbing(completed, point(8)) is True


def test_a_faster_higher_concurrency_keeps_climbing() -> None:
    completed = [run(concurrency=2, wall=100.0), run(concurrency=4, wall=60.0)]

    assert should_stop_climbing(completed, point(8)) is False


def test_a_regression_at_another_chunk_size_does_not_stop_this_one() -> None:
    """The curve is per configuration. A backend that queues at K=4 with
    12000-character chunks may not at 3000."""
    completed = [
        run(concurrency=2, wall=100.0, chunk=12000),
        run(concurrency=4, wall=140.0, chunk=12000),
    ]

    assert should_stop_climbing(completed, point(8, chunk=3000)) is False


def test_a_regression_on_another_document_does_not_stop_this_one() -> None:
    completed = [
        run(concurrency=2, wall=100.0, document="other"),
        run(concurrency=4, wall=140.0, document="other"),
    ]

    assert should_stop_climbing(completed, point(8)) is False


def test_fewer_than_two_concurrencies_cannot_show_a_reversal() -> None:
    assert should_stop_climbing([run(concurrency=1, wall=100.0)], point(2)) is False
    assert should_stop_climbing([], point(2)) is False


def test_the_lowest_concurrency_is_never_skipped() -> None:
    """K=1 is the baseline column of every grid, so it runs whatever the
    completed runs say."""
    completed = [run(concurrency=2, wall=100.0), run(concurrency=4, wall=140.0)]

    assert should_stop_climbing(completed, point(1)) is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/bench/test_sweep.py -v -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench.sweep'`

- [ ] **Step 3: Write the implementation**

Create `bench/sweep.py`:

```python
"""When to stop climbing concurrency.

If K=4 is slower than K=2, the backend is queueing and K=8 measures the queue.
That is worth recording once and not worth twenty more minutes, so the sweep
skips the remaining higher values -- for that document at that chunk size, and
nowhere else. The curve is per configuration: a backend that queues at K=4
with 12000-character chunks may not at 3000.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bench.config import SweepPoint
    from bench.metrics import RunMetrics


def should_stop_climbing(runs: Sequence[RunMetrics], point: SweepPoint) -> bool:
    """True when a completed run already showed higher concurrency is slower.

    `point.concurrency` at or below every completed concurrency is never
    skipped: the lowest column is the baseline the rest of the grid is read
    against.
    """
    comparable = sorted(
        (
            run
            for run in runs
            if run.point.document_id == point.document_id
            and run.point.chunk_size == point.chunk_size
            and run.point.concurrency < point.concurrency
        ),
        key=lambda run: run.point.concurrency,
    )
    if len(comparable) < 2:
        return False
    return comparable[-1].wall_clock_s > comparable[-2].wall_clock_s
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/bench/test_sweep.py -v -p no:randomly`
Expected: PASS, 6 passed

- [ ] **Step 5: Write the CLI**

Create `scripts/benchmark.py`:

```python
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
import subprocess  # noqa: S404 -- one fixed argv, for the git sha in the result
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bench.config import BenchConfigError, load_config  # noqa: E402
from bench.corpus import CORPUS_ROOT, BenchCorpusError, load_document  # noqa: E402
from bench.preflight import PreflightError, Probes, preflight  # noqa: E402
from bench.report import build_report, write_report  # noqa: E402
from bench.runner import run_point  # noqa: E402
from bench.sweep import should_stop_climbing  # noqa: E402
from redstring import SourceDocument, __version__, build_graph  # noqa: E402
from redstring.graph.adapters.memory import InMemoryGraphStore  # noqa: E402
from redstring.llm.adapters.langchain import LangChainLlmProvider  # noqa: E402
from redstring.llm.adapters.langchain_embedding import LangChainEmbeddingProvider  # noqa: E402

#: Short enough to cost seconds, rich enough that an extractor doing its job
#: cannot return nothing.
WARM_UP = (
    "Ada Lovelace was an English mathematician. She worked with Charles "
    "Babbage on the Analytical Engine."
)


def git_sha() -> str:
    try:
        return subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            cwd=ROOT,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def make_probes(config, provider, embedder) -> Probes:
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
        return response.json()["choices"][0]["message"]["content"] or ""

    def embed() -> list[float]:
        return asyncio.run(embedder.embed(["probe"]))[0]

    def warm_up_entities() -> int:
        async def once() -> int:
            report = await build_graph(
                SourceDocument(id="warm-up", text=WARM_UP),
                provider=provider,
                store=InMemoryGraphStore(),
                tenant_id=__import__("uuid").uuid4(),
            )
            return report.entities

        return asyncio.run(once())

    return Probes(
        list_models=list_models, complete=complete, embed=embed, warm_up_entities=warm_up_entities
    )


async def run_sweep(config, provider) -> list:
    runs = []
    documents = {doc_id: load_document(doc_id) for doc_id in config.long_documents}
    for point in config.sweep():
        if config.stop_climbing_concurrency and should_stop_climbing(runs, point):
            print(f"skipping {point}: a lower concurrency was already faster")
            continue
        print(f"running {point} ...", flush=True)
        result = await asyncio.wait_for(
            run_point(point, documents[point.document_id], provider=provider),
            timeout=config.per_document_timeout_s,
        )
        print(
            f"  {result.wall_clock_s:.1f}s  {result.chunks} chunks  "
            f"{result.entities} entities  {result.model_calls} calls"
        )
        runs.append(result)
    return runs


async def run_accuracy(provider):
    from tests.accuracy.corpus import load_corpus
    from tests.accuracy.runner import run_corpus

    return await run_corpus(load_corpus(), provider=provider)


def main() -> int:
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
        runs = asyncio.run(run_sweep(config, provider))
    except BenchCorpusError as error:
        print(f"corpus: {error}", file=sys.stderr)
        return 2
    except TimeoutError:
        print(
            f"a run exceeded policy.per_document_timeout_s "
            f"({config.per_document_timeout_s}s); no results written",
            file=sys.stderr,
        )
        return 1

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
        ),
        directory=args.results,
        started_at=started_at,
    )
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Check the CLI's imports resolve**

Run: `uv run python scripts/benchmark.py --help`
Expected: the argparse help text, no `ImportError`.

If `LangChainEmbeddingProvider.openai_compatible` has a different signature, read it from `src/redstring/llm/adapters/langchain_embedding.py:66` and correct the call. If `redstring.__version__` is not exported, use `importlib.metadata.version("redstring")`.

- [ ] **Step 7: Commit**

```bash
git add scripts/benchmark.py bench/sweep.py tests/unit/bench/test_sweep.py
git commit -m "Wire the benchmark CLI, and stop climbing concurrency on a reversal

The CLI owns no knobs: everything configurable is in bench/config.yaml and
this wires the pieces. The one piece of logic worth testing is extracted --
if K=4 is slower than K=2 the backend is queueing, and K=8 would spend twenty
minutes measuring the queue. The stop is per document and per chunk size,
because a backend that queues at K=4 with 12000-character chunks may not at
3000."
```

---

### Task 10: The config, a corpus document, and the deferred grading

The harness is code until there is something to run it on. This task adds the real config, the first long document, and the backlog entry the spec promised.

**Files:**
- Create: `bench/config.yaml`
- Create: `bench/corpus/README.md`
- Create: `bench/corpus/<id>.txt` and `bench/corpus/<id>.meta.yaml` (operator-supplied)
- Create: `bench/results/.gitkeep`
- Modify: `BACKLOG.md`
- Modify: `.gitignore` (only if `bench/results/` would otherwise be ignored)

**Interfaces:**
- Consumes: everything above.
- Produces: a runnable benchmark.

- [ ] **Step 1: Write the config**

Create `bench/config.yaml`:

```yaml
# Every knob that changes a benchmark number. Re-running a variant is an edit
# here; the resolved contents are embedded verbatim in each results file.
endpoint: http://192.168.1.14:8080/v1/

models:
  extraction: muse-glimmer-30b
  embedding: nomic-embed-text
  # nomic-embed-text. Checked in preflight, because a different embedding
  # model behind the same id is a silent dimension change.
  embedding_dimensions: 768

corpus:
  # The five hand-graded documents in tests/accuracy/corpus.yaml. This is the
  # only accuracy number the harness produces; the long documents below are
  # ungraded and score stability, which is a different claim.
  graded: true
  long: [harry-potter-1]

sweep:
  chunk_size: [3000, 8000, 12000]
  # Only 1 until deliverable C lands. Any other value is refused rather than
  # ignored -- a knob a serial library silently discards would make the
  # concurrency work look like it changed nothing.
  concurrency: [1]

policy:
  repeats: 3
  stop_climbing_concurrency: true
  per_document_timeout_s: 1800
```

- [ ] **Step 2: Write the corpus README and add the first document**

Create `bench/corpus/README.md`:

```markdown
# Benchmark corpus

Long documents, timed rather than graded.

**redstring never fetches, and neither does the benchmark.** Put the text here
yourself as `<id>.txt`, with an `<id>.meta.yaml` beside it:

```yaml
source: https://en.wikipedia.org/wiki/...
retrieved: 2026-08-13
licence: CC BY-SA 4.0
```

All three fields are required — `bench/corpus.py` refuses a document without
them. The metadata is what makes committing third-party text a decision rather
than an accident, and anything not redistributable should be left out and
fetched by the operator into an ignored path instead.

**These documents are ungraded.** Nothing scored against them is accuracy;
they produce timings and a stability comparison across repeats. Accuracy comes
from `tests/accuracy/corpus.yaml`. See BACKLOG B-BENCH-1.
```

Add the document the sixteen-minute report was about:

```bash
mkdir -p bench/corpus bench/results
touch bench/results/.gitkeep
# Save the plain text of the article to bench/corpus/harry-potter-1.txt
```

Create `bench/corpus/harry-potter-1.meta.yaml`:

```yaml
source: https://en.wikipedia.org/wiki/Harry_Potter_and_the_Philosopher%27s_Stone
retrieved: 2026-08-13
licence: CC BY-SA 4.0
```

- [ ] **Step 3: File the deferred grading in BACKLOG.md**

Append to `BACKLOG.md`:

```markdown
### B-BENCH-1 — The long benchmark documents are ungraded, so nothing scored against them is accuracy

`bench/corpus/*.txt` produce timings and a stability score (`bench/stability.py`),
never an accuracy score. Stability is Jaccard agreement between repeats, and
both sides of that comparison come from the code under test: a pipeline that
deterministically drops half of every document scores 1.0. It detects variance
— which is the live risk in deliverable C, where concurrency may cause naming
drift at chunk boundaries — and nothing else.

Deferred rather than skipped, and what was learned deciding it:

- Hand-grading a 100k-character document is hours of work, and CLAUDE.md's
  grading convention makes a *partial* grading actively misleading: "omission
  is a claim", so every ungraded entity the model correctly finds is scored as
  a false positive. A half-graded long document reports a precision failure
  belonging to the grader.
- The grading convention that makes the short corpus trustworthy — grade what
  the text states, not what is true — is hardest exactly where a model knows
  the subject. A Harry Potter article is the worst case: an extractor that
  supplies Hermione's house from its own training rather than from the text
  is wrong, and a grader who knows the books will not notice.

Options, cheapest first: grade a *bounded excerpt* (the first 5k characters)
and score only entities whose mentions fall inside it; or grade a long
document in an unfamiliar domain where the grader has no prior knowledge to
leak. Neither is free, and both are better than the third option of quietly
renaming stability to accuracy.

Until then: `bench/report.py` writes `stability` and `accuracy` as separate
keys, and `accuracy` is `null` whenever the graded corpus did not run.
```

- [ ] **Step 4: Verify the harness refuses before it runs**

Run: `uv run python scripts/benchmark.py --config bench/config.yaml`

Expected, with the endpoint up: preflight passes, the sweep prints one line per point, and a JSON file is written to `bench/results/`.

Then **break it on purpose and watch each refusal**, per CLAUDE.md's standing instruction that a gate whose happy path is silent is not yet evidence:

1. Edit `bench/config.yaml` to `extraction: no-such-model`. Expected: `preflight: ... does not serve no-such-model; it lists [...]`, exit 1, **no results file written**.
2. Restore it, set `embedding_dimensions: 384`. Expected: `preflight: nomic-embed-text returned 768 dimensions, config expects 384`, exit 1.
3. Restore it, set `concurrency: [1, 4]`. Expected: `config: concurrency [1, 4] needs deliverable C...`, exit 2.
4. Restore it, and confirm a results file appears only on the run that passes all three.

Record what each refusal printed in the commit message. A refusal nobody has seen fire is indistinguishable from a refusal that never fires.

- [ ] **Step 5: Commit**

```bash
git add bench/config.yaml bench/corpus/ bench/results/.gitkeep BACKLOG.md
git commit -m "Add the benchmark config, the first long document, and the deferred grading

The config names the endpoint and both models; concurrency stays [1] until
deliverable C, refused rather than ignored. The corpus document is the
Wikipedia page the sixteen-minute report was about, with the provenance
bench/corpus.py requires.

B-BENCH-1 records why long-document accuracy was deferred rather than
skipped: 'omission is a claim' makes a partial grading report a precision
failure belonging to the grader, and a Harry Potter article is the worst case
for the convention that keeps the short corpus honest -- an extractor
supplying facts from training rather than from the text is wrong, and a
grader who knows the books will not notice.

Each preflight refusal was broken on purpose and watched failing:
<paste what each printed>"
```

---

### Task 11: Record the baseline

The harness exists to produce one artefact. This task produces it.

**Files:**
- Create: `bench/results/<timestamp>.json`
- Create: `bench/BASELINE.md`

- [ ] **Step 1: Run the full sweep**

Run: `uv run python scripts/benchmark.py`

This takes a while — three chunk sizes × three repeats over a ~100k-character document, plus the graded corpus. Do not run it concurrently with anything else against the endpoint; a benchmark sharing a GPU measures the other process.

- [ ] **Step 2: Write the reading of it**

Create `bench/BASELINE.md` summarising, from the results JSON:

- wall clock per chunk size, and whether larger chunks won
- the extract/consolidate split — the spec's open question is whether extraction is the whole sixteen minutes
- model calls per chunk size
- stability per chunk size, and whether larger chunks destabilised naming
- accuracy from the graded corpus, with the caveat that five short documents move on noise
- `time_to_first_entity_s: null` everywhere, and that deliverable B is what fills it

State the numbers and what they imply for B and C. If larger chunks are a large win, the cheapest improvement is a config default rather than either deliverable, and that finding belongs at the top.

- [ ] **Step 3: Commit**

```bash
git add bench/results/ bench/BASELINE.md
git commit -m "Record the ingestion baseline

<one line naming the headline number, e.g. 'chunk_size 12000 cuts the wall
clock from Xs to Ys and consolidation is Z% of it'>

Every number here comes from one machine on one day against one model id.
The results file carries the resolved config, the library version and the git
sha so a later run can say whether it is comparing like with like."
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| `bench/` layout, config, results JSON | 1, 8, 10 |
| Preflight refusals (models listed, completion, dimension, warm-up) | 6 |
| Sweep matrix, `stop_climbing_concurrency` | 1, 9 |
| Metrics table (wall clock, gaps, phase split, calls, chunks, counts) | 2, 5, 7 |
| `time_to_first_entity_s` null until B | 2, 7 (constraint, and an assertion in each) |
| `event_gaps_s` stored whole, summarised p50/p95/max | 2, 8 |
| Accuracy from graded corpus, stability separate and named | 3, 8 |
| Harness tested with a scripted provider and injected clock | 5, 7 |
| Refusals broken on purpose | 6 (unit), 10 step 4 (end to end) |
| Corpus provenance `.meta.yaml` | 4, 10 |
| Ungraded long documents filed in BACKLOG | 10 |

Deliverables B and C are deliberately absent — they get their own plans, written after Task 11's numbers exist.

**Type consistency:** `SweepPoint`, `BenchConfig`, `RunMetrics`, `GapSummary`, `Stability`, `BenchDocument`, `Probes` are each defined once and used with the same field names in every later task. `run_point` returns `RunMetrics`; `build_report` consumes `Sequence[RunMetrics]`; `should_stop_climbing` reads `run.point.concurrency` and `run.wall_clock_s`, both defined in Task 2.

**Known soft spots, flagged rather than hidden:**
- Task 7's `_two_entities` assumes the extraction schema's field names. Step 4 says to read the real ones rather than weaken the assertion.
- Task 9's CLI calls `LangChainEmbeddingProvider.openai_compatible` and `redstring.__version__` from signatures not read in full while planning. Step 6 is the check, with the fix named.
- `bench` importing `tests.accuracy` inverts the usual direction. It is deliberate: the graded corpus and scorer exist and are trusted, duplicating them would give two corpora that drift, and neither package ships in the wheel (src-layout). If it becomes awkward, the move is `tests/accuracy/scoring.py` into `bench/` with the tests importing it from there — not a second copy.
