# Bounded Concurrency (Deliverable C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract a document's chunks in bounded concurrent batches, with a hard ceiling on in-flight requests the operator sets, and `concurrency=1` byte-identical to today.

**Architecture:** `ExtractionPipeline` gains `concurrency: int = 1`. Chunks run in wavefront batches of that size via `asyncio.gather`; carryover accumulates **between** batches, so naming drift is bounded by the batch size rather than unbounded. The ceiling is an `asyncio.Semaphore` threaded through every call the pipeline and `build_graph` make against the inference endpoint — extraction, gleaning and embedding — because the operator's constraint is in-flight requests at the backend, not batch structure.

**Tech Stack:** Python 3.13 `asyncio`, `uv`, pytest, hypothesis.

**Spec:** `docs/superpowers/specs/2026-08-13-ingestion-benchmark-design.md`, section C. Read it and the baseline it must be judged against: `bench/BASELINE.md`.

## Global Constraints

- **`concurrency=1` must be byte-identical to today** — same call sequence, same prompts, same result. This is what makes the knob a measurement rather than a rewrite, and it is what lets every sweep's `K=1` column serve as the baseline within its own grid.
- **The ceiling is on in-flight requests against the endpoint**, not on batch size. Extraction, gleaning and embedding all go through it. "K=4" must never mean six in flight because embeddings overlapped the next batch.
- **Failure semantics are unchanged.** `skip_failed_chunks` still governs; a batch containing a failure does not cancel its siblings (their results are already paid for); `record(allow_partial=...)` still refuses to record a partial extraction as a whole one.
- **The fold must stay order-independent.** `redstring.domain.preference` is a documented *total* order and `merge_extractions` takes parts "in any order" — deliverable C depends on that and must prove it rather than assume it. See ADR 0010.
- **`B-BENCH-3` lands first.** Entity count cannot detect naming drift: cross-configuration jaccard is 0.587 where within-configuration is 0.601–0.667. Measuring C with the current metrics would report nothing whatever C did to naming.
- Quality gates (ruff, mypy `--strict` over `src/redstring`, bandit, `lint-imports`) run on `git commit`. **The test suite does not** — run `uv run pytest` yourself before committing.
- Deferred work lands in `BACKLOG.md` in the same commit. When you close a backlog item, delete its entry in the same commit.
- New public parameters are an architectural decision: this plan adds one to `ExtractionPipeline` and one to `build_graph`, and Task 7 writes the ADR.

---

### Task 1: A drift metric the benchmark can actually see

Closes `B-BENCH-3`. Counts within-run name-variant pairs — the `dudley` / `dudley dursley` shape that `extraction.mapping.entity_id_for` cannot merge because identity is derived from the name.

**Files:**
- Create: `bench/drift.py`
- Modify: `bench/metrics.py` (add one field), `bench/runner.py` (populate it), `bench/report.py` (write it)
- Modify: `BACKLOG.md` (delete `B-BENCH-3`)
- Test: `tests/unit/bench/test_drift.py`, and additions to `tests/unit/bench/test_runner.py`

**Interfaces:**
- Produces: `variant_pairs(names: Iterable[str]) -> int`, `variant_pairs_detail(names: Iterable[str]) -> list[tuple[str, str]]`, and `RunMetrics.variant_pairs: int`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/bench/test_drift.py`:

```python
"""Naming drift, counted as pairs rather than inferred from a total.

Entity count cannot see drift: measured, changing the chunk size moves the
entity set no more than re-running the same configuration does (jaccard 0.587
against 0.601-0.667). This metric exists because deliverable C's whole risk is
drift, and the metric it would otherwise be judged by is blind to it.
"""

from __future__ import annotations

from bench.drift import variant_pairs, variant_pairs_detail


def test_a_first_name_beside_its_full_name_is_one_pair() -> None:
    """The exact shape the pipeline manufactures at a chunk boundary."""
    assert variant_pairs(["dudley", "dudley dursley"]) == 1


def test_unrelated_names_are_not_a_pair() -> None:
    assert variant_pairs(["harry potter", "albus dumbledore"]) == 0


def test_a_shared_word_is_not_enough() -> None:
    """`the philosopher's stone` and `the goblet of fire` share a token and
    are different things. Only a strict subset counts."""
    assert variant_pairs(["the philosophers stone", "the goblet of fire"]) == 0


def test_three_spellings_of_one_name_are_three_pairs() -> None:
    """Every pair is counted, not every cluster.

    A cluster count would report 1 here and 1 for a two-name cluster, hiding
    the difference between mild and severe drift on one entity.
    """
    assert variant_pairs(["harry", "harry potter", "harry james potter"]) == 3


def test_identical_names_are_not_a_pair() -> None:
    """A strict subset, not any subset -- a name is not a variant of itself,
    and a duplicated list entry must not manufacture drift."""
    assert variant_pairs(["harry potter", "harry potter"]) == 0


def test_possessives_and_hyphens_are_normalised_before_comparing() -> None:
    """`harry's` and `harry` are one name spelled two ways."""
    assert variant_pairs(["harrys wand", "harrys wand extra"]) == 1
    assert variant_pairs(["half-blood prince", "the half blood prince"]) == 1


def test_the_detail_lists_the_pairs_it_counted() -> None:
    """The count is for the report; the pairs are for the human deciding
    whether a rise is real drift or an artefact of the heuristic."""
    assert variant_pairs_detail(["dudley", "dudley dursley"]) == [("dudley", "dudley dursley")]


def test_the_order_of_the_input_does_not_change_the_count() -> None:
    names = ["harry", "harry potter", "albus dumbledore"]

    assert variant_pairs(names) == variant_pairs(list(reversed(names)))


def test_an_empty_run_has_no_pairs() -> None:
    assert variant_pairs([]) == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/bench/test_drift.py -v -p no:randomly`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench.drift'`

- [ ] **Step 3: Write the implementation**

Create `bench/drift.py`:

```python
"""Naming drift, counted directly rather than inferred from a total.

`extraction.mapping.entity_id_for` derives an entity's identity from its name,
so a chunk that says "Dudley" where another said "Dudley Dursley" does not
produce one entity with two mentions -- it produces two entities that no fold
can combine. That is the specific defect bounded concurrency risks, because a
wavefront gives a chunk less of what earlier chunks found.

**Entity count cannot see it.** Measured on one document: the entity-name sets
of a 3,000-character run and a 12,000-character run share a jaccard of 0.587,
while two repeats of one configuration share 0.601-0.667. A parameter that
moves everything else moves the total no more than noise does. Variant pairs
sat at 62 and 59 across the same comparison -- stable enough that a rise means
something.

**This is a floor on drift, not a count of it.** The heuristic sees
`dudley` / `dudley dursley`, where one name's tokens are a strict subset of
the other's. It cannot see `mum` / `mom`, or `dahl` / `roald dahl` where the
shorter is not a subset. Report it as a lower bound and never as a total.
"""

from __future__ import annotations

from itertools import combinations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


def _tokens(name: str) -> frozenset[str]:
    """Split a normalised name into comparable words.

    Possessives and hyphens are folded because they are spelling, not
    identity: `harry's wand` and `harry wand` are the same drift pair as
    `harry` and `harry potter`, and leaving them distinct would undercount.
    """
    return frozenset(name.replace("'s", "").replace("'", "").replace("-", " ").split())


def _is_variant(a: str, b: str) -> bool:
    """True when one name is a strict token-subset of the other.

    Strict: a name is not a variant of itself, so a list containing the same
    name twice reports no drift.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return ta < tb or tb < ta


def variant_pairs_detail(names: Iterable[str]) -> list[tuple[str, str]]:
    """Every pair of names in one run that look like one entity spelled twice."""
    ordered = sorted(set(names))
    return [(a, b) for a, b in combinations(ordered, 2) if _is_variant(a, b)]


def variant_pairs(names: Iterable[str]) -> int:
    """How many such pairs there are.

    Pairs rather than clusters: three spellings of one name is worse than two,
    and a cluster count reports both as 1.
    """
    return len(variant_pairs_detail(names))
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/bench/test_drift.py -v -p no:randomly`
Expected: PASS, 9 passed

- [ ] **Step 5: Thread it through the harness**

In `bench/metrics.py`, add to `RunMetrics` after `entity_names`:

```python
    #: Pairs of extracted names that look like one entity spelled two ways --
    #: see `bench.drift`. A **lower bound** on naming drift, and the metric
    #: deliverable C is judged by, because entity count cannot see drift at
    #: all (BACKLOG B-BENCH-3, closed).
    variant_pairs: int = 0
```

In `bench/runner.py`, import `variant_pairs` from `bench.drift` and pass `variant_pairs=variant_pairs(names)` when building the `RunMetrics`.

In `bench/report.py`'s `_run_json`, add `"variant_pairs": run.variant_pairs,`.

- [ ] **Step 6: Add the runner test that proves it is wired**

Append to `tests/unit/bench/test_runner.py`:

```python
async def test_the_run_reports_the_drift_pairs_its_entities_contain() -> None:
    """A counter with no test asserting it non-zero is the shape
    `recurring-defects.md` §3 is about -- and this one is wired through three
    modules, so a zero would look like "no drift" rather than "not measured".
    """
    clock = FakeClock()

    result = await run_point(point(), DOCUMENT, provider=DriftingProvider(clock), clock=clock)

    assert result.variant_pairs >= 1
```

Add the provider it needs beside the other fixtures in that file:

```python
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
```

Note `DriftingProvider.__init__` comes from `SteadyProvider`, which takes `(clock, *, takes)` — construct it as `DriftingProvider(clock, takes=0.0)` and adjust the call above to match.

- [ ] **Step 7: Close `B-BENCH-3`, but file what outlives it**

Remove the whole `### B-BENCH-3` section from `BACKLOG.md` — closing an item deletes its entry in the same commit.

**Its last paragraph must not die with it.** `B-BENCH-3` is closed by shipping the metric; the metric's *blind spots* are a separate, still-open problem, and deleting the entry would take the only written record of them with it. Add a successor entry in the same section, in the file's existing style:

```markdown
### B-BENCH-4. The drift metric is a floor, and its blind spots are the interesting half

`bench/drift.py` counts pairs where one name's tokens are a strict subset of
the other's — `dudley` inside `dudley dursley`. That catches the drift shape a
chunk boundary produces most often, and it is stable enough across
configurations to be worth reading (62 against 59 where entity count moved no
more than noise).

**It cannot see the cases where neither name contains the other**, and those
are not exotic:

- `mum` / `mom` — one referent, two spellings, no shared token. British and
  American editions of this corpus differ exactly here, and the document under
  test names both.
- `dahl` / `roald dahl` is caught, but `r. dahl` / `roald dahl` is not, because
  `r.` and `roald` are different tokens.
- Abbreviations and initialisms: `alarte ascendare` / `a. ascendare`,
  `american library association` / `ala`.
- Transposition: `dursley, dudley` / `dudley dursley` — same tokens, neither a
  strict subset, so the pair is invisible even though the names are equal as
  sets. This one is a genuine gap in the heuristic rather than a hard problem:
  equal token sets with different orderings should count.

So a **rise** in this number is evidence of drift, and a **flat** number is not
evidence of its absence. Anything reported from it says "at least".

Routes forward, cheapest first: count equal-token-set pairs too (fixes
transposition, a few lines); add edit distance over the whole name with a
threshold, which catches `mum`/`mom` and costs a tuning parameter nobody has
calibrated; or embed the names and cluster them, which is the only approach
that catches synonyms and which requires the embedding provider the harness
already configures. The third is the one to do if drift ever becomes the
number a decision rests on — until then, the floor is honest as long as every
report says it is one.
```

Then run the suite.

Run: `uv run pytest tests/unit/bench/ -q -p no:randomly`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add bench/drift.py bench/metrics.py bench/runner.py bench/report.py tests/unit/bench/ BACKLOG.md
git commit -m "Count naming drift directly, because entity count cannot see it


Changing the chunk size moves the entity set no more than re-running the same
configuration does -- jaccard 0.587 against 0.601-0.667 -- so a total cannot
carry a quality comparison. Variant pairs sat at 62 and 59 across that same
change, stable enough that a rise means something.

It is a floor rather than a count: the heuristic sees dudley/dudley dursley,
where one name's tokens are a strict subset of the other's, and cannot see
mum/mom. Reported as a lower bound wherever it appears.

Closes B-BENCH-3 and opens B-BENCH-4 in the same commit. The entry being
deleted carried the only written record of what the heuristic misses, and that
problem is not closed by shipping the heuristic -- a rise in this number is
evidence of drift, a flat one is not evidence of its absence."
```

---

### Task 2: Prove the fold is order-independent before depending on it

Deliverable C reorders when chunks are mapped. ADR 0010 says `preference` is a total order and `merge_extractions` takes parts "in any order" — this task turns that claim into a test, **before** any concurrency exists to blame.

**Files:**
- Test: `tests/unit/extraction/test_merging.py` (append)

**Interfaces:**
- Consumes: `redstring.extraction.merging.merge_extractions`.
- Produces: nothing; a gate.

- [ ] **Step 1: Read the existing module**

Read `tests/unit/extraction/test_merging.py` and `src/redstring/extraction/merging.py`. Match the file's existing fixture style; do not import a new builder if one is already there.

- [ ] **Step 2: Write the failing-if-broken test**

Append a property that permutes the parts:

```python
@given(st.integers(min_value=2, max_value=5), st.integers(min_value=0, max_value=120))
@settings(max_examples=25)
def test_the_fold_does_not_depend_on_the_order_of_its_parts(count: int, seed: int) -> None:
    """Bounded concurrency reorders when chunks are mapped, and this is the
    property that makes that safe.

    `merge_extractions` takes parts "in any order" and resolves collisions
    with `domain.preference`, a documented *total* order. Both halves matter:
    a partial order would fall through to "keep the one already there", which
    is order-dependent exactly where two mentions tie -- and two mentions of
    one entity tie whenever the model declined to score confidence, which is
    the common case. See ADR 0010.
    """
    parts = _colliding_parts(count, seed)
    forward = merge_extractions(parts)
    backward = merge_extractions(list(reversed(parts)))

    assert [e.id for e in forward.entities] == [e.id for e in backward.entities]
    assert {e.id: e for e in forward.entities} == {e.id: e for e in backward.entities}
    assert forward.dropped_entities == backward.dropped_entities
    assert forward.unresolved_relationships == backward.unresolved_relationships
```

Write `_colliding_parts(count, seed)` in the same file: it must build `count` `MappedExtraction`s that **collide** — the same entity id appearing in more than one part, with differing confidence *and* at least one pair where confidence is equal, so the tie-break beyond confidence is exercised. A generator whose parts share no ids proves nothing: with no collision, any fold gives the same answer and CLAUDE.md's table has this exact row.

- [ ] **Step 3: Break it on purpose**

Temporarily change `merge_extractions`'s comparison from `preference(entity) > preference(seen)` to `preference(entity)[0] > preference(seen)[0]` — confidence alone, which is the partial order ADR 0010 records replacing.

Run: `uv run pytest tests/unit/extraction/test_merging.py -v -p no:randomly`
Expected: **the new property FAILS.** If it passes, `_colliding_parts` is not producing ties — fix the generator, not the assertion. Restore the comparison and confirm green.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/extraction/test_merging.py
git commit -m "Pin the fold's order-independence before concurrency depends on it

Bounded concurrency reorders when chunks are mapped. merge_extractions
documents that it takes parts in any order and resolves collisions with a
total order, and ADR 0010 records what a partial one cost -- but nothing
asserted it, so the property C is about to rely on was a docstring.

Proved by reverting the comparison to confidence alone and watching the
property fail; parts are generated with deliberate ties, because parts that
share no ids agree under any fold."
```

---

### Task 3: The wavefront, with K=1 byte-identical

**Files:**
- Modify: `src/redstring/extraction/pipeline.py`
- Test: `tests/unit/extraction/test_pipeline_concurrency.py`

**Interfaces:**
- Produces: `ExtractionPipeline(..., concurrency: int = 1)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/extraction/test_pipeline_concurrency.py`. Read `tests/unit/extraction/test_pipeline.py` first and reuse its provider fakes and document builders rather than writing new ones.

```python
"""Chunks in bounded batches, and the two properties that make the knob safe.

`concurrency=1` must be byte-identical to the serial pipeline -- that is what
makes this a measurement rather than a rewrite. And no more than `concurrency`
calls may be in flight at once, because the operator's constraint is the
inference backend's queue depth, not this module's batch structure.
"""

from __future__ import annotations

import asyncio

import pytest

from redstring.extraction.pipeline import ExtractionPipeline


class RecordingProvider:
    """Answers every chunk, recording prompts and peak concurrency."""

    def __init__(self, *, delay: float = 0.0) -> None:
        self.prompts: list[str | None] = []
        self.texts: list[str] = []
        self.in_flight = 0
        self.peak = 0
        self._delay = delay

    @property
    def model(self) -> str:
        return "recording-model"

    async def extract(self, text, schema, *, system_prompt=None):
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            self.texts.append(text)
            self.prompts.append(system_prompt)
            await asyncio.sleep(self._delay)
            return _answer_for(text, schema)
        finally:
            self.in_flight -= 1


async def test_concurrency_one_makes_the_same_calls_in_the_same_order() -> None:
    """The regression gate for every existing caller.

    Not "produces the same entities" -- the same *calls*, in order, with the
    same prompts. Carryover means prompt N depends on chunks 1..N-1, so a
    pipeline that batched even at K=1 would produce different prompts while
    plausibly reaching the same entities.
    """
    serial = RecordingProvider()
    batched = RecordingProvider()

    await _run(serial, concurrency=1)
    await _run(batched, concurrency=1)

    assert serial.texts == batched.texts
    assert serial.prompts == batched.prompts
    assert serial.peak == 1


async def test_no_more_than_k_calls_are_ever_in_flight() -> None:
    """The ceiling the operator actually sets."""
    provider = RecordingProvider(delay=0.01)

    await _run(provider, concurrency=3)

    assert provider.peak <= 3


async def test_a_wavefront_actually_overlaps() -> None:
    """The other half: a ceiling of 3 that never exceeds 1 is a serial
    pipeline passing the test above."""
    provider = RecordingProvider(delay=0.01)

    await _run(provider, concurrency=3)

    assert provider.peak > 1


async def test_a_chunk_sees_what_earlier_batches_found_and_not_its_own_batch() -> None:
    """Carryover accumulates between batches, which is what bounds drift.

    With K=2 over four chunks, chunk 3's prompt must mention what chunks 1-2
    found and chunk 4's must not add anything chunk 3 found -- they are in one
    batch. A pipeline that updated carryover per completed call instead would
    make the prompt depend on which call finished first, so two runs of one
    document would differ.
    """
    provider = RecordingProvider()

    await _run(provider, concurrency=2, chunks=4)

    assert provider.prompts[2] == provider.prompts[3]
    assert provider.prompts[0] == provider.prompts[1]
    assert provider.prompts[2] != provider.prompts[0]


@pytest.mark.parametrize("bad", [0, -1])
async def test_a_concurrency_below_one_is_refused(bad: int) -> None:
    with pytest.raises(ValueError, match="concurrency"):
        ExtractionPipeline(RecordingProvider(), concurrency=bad)
```

Write `_run(provider, *, concurrency, chunks=3)` and `_answer_for(text, schema)` as helpers in the same file: `_run` builds an `ExtractionPipeline` with a chunker sized so the document splits into `chunks` pieces, and calls `extract` with a fixed `tenant_id` and `observed_at`. `_answer_for` must return a **different entity per chunk text**, so carryover has something to carry — an answer identical for every chunk makes the carryover assertions vacuous.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/extraction/test_pipeline_concurrency.py -v -p no:randomly`
Expected: FAIL — `ExtractionPipeline() got an unexpected keyword argument 'concurrency'`

- [ ] **Step 3: Implement the wavefront**

In `ExtractionPipeline.__init__`, add `concurrency: int = 1` after `skip_failed_chunks`, refuse `< 1` with a `ValueError` naming the parameter, and store it.

Replace the `for chunk in chunks:` loop body in `extract` with a batched form. The shape:

```python
for batch in _batches(chunks, self._concurrency):
    prompt = self._system_prompt + carryover.block()
    results = await asyncio.gather(
        *(self._extract_one(chunk, prompt, document, tenant_id, observed_at) for chunk in batch),
        return_exceptions=True,
    )
    for chunk, result in zip(batch, results, strict=True):
        if isinstance(result, LlmProviderError):
            if not self._skip_failed:
                raise result
            failed += 1
            continue
        if isinstance(result, BaseException):
            raise result
        mapped, passes, glean_failures = result
        gleaned += passes
        failed_gleanings += glean_failures
        parts.append(mapped)
        found_by_index[chunk.chunk_index] = [entity.id for entity in mapped.entities]
    for chunk, result in zip(batch, results, strict=True):
        if not isinstance(result, BaseException):
            carryover.remember(result[0].entities)
```

Three details are load-bearing and each has a test above:

- **One prompt per batch**, computed before the batch runs. Every chunk in a batch sees the same carryover.
- **Carryover is updated in chunk order after the batch**, in a second pass — not as each call returns. Updating on completion makes the prompt depend on which call finished first, and two runs of one document would differ.
- **`return_exceptions=True`**, so one chunk's failure does not cancel its siblings; their results are already paid for. A non-`LlmProviderError` is re-raised unchanged, because `skip_failed_chunks` is about model failures and nothing else.

Extract the per-chunk work into `_extract_one` returning `(mapped, gleaning_passes, gleaning_failures)`, moving the existing `map_extraction` and `_glean` calls into it unchanged.

Add `_batches`:

```python
def _batches(chunks: Sequence[Chunk], size: int) -> Iterator[Sequence[Chunk]]:
    """Consecutive groups of `size`, in order. `size=1` yields one chunk each."""
    for start in range(0, len(chunks), size):
        yield chunks[start : start + size]
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/extraction/test_pipeline_concurrency.py -v -p no:randomly`
Expected: PASS.

- [ ] **Step 5: Run the whole existing pipeline suite unchanged**

Run: `uv run pytest tests/unit/extraction/ -q -p no:randomly`
Expected: all pass, **with no edits to any existing test**. If an existing test needed changing, `concurrency=1` is not byte-identical and the implementation is wrong — fix the implementation, not the test.

- [ ] **Step 6: Commit**

```bash
git add src/redstring/extraction/pipeline.py tests/unit/extraction/test_pipeline_concurrency.py
git commit -m "Extract chunks in bounded wavefront batches

concurrency=1 makes the same calls with the same prompts in the same order as
the serial loop, which is what makes the knob a measurement rather than a
rewrite -- the whole existing pipeline suite passes unedited.

Carryover accumulates between batches, not within one, so drift is bounded by
the batch size. It is applied in chunk order after the batch rather than as
each call returns: updating on completion would make a chunk's prompt depend
on which sibling finished first, and two runs of one document would differ.

return_exceptions=True, so one chunk's failure does not cancel siblings whose
answers are already paid for. skip_failed_chunks still governs, and anything
that is not an LlmProviderError is re-raised unchanged."
```

---

### Task 4: One ceiling over every call, including embeddings

The batch bounds extraction. Gleaning and embedding also hit the endpoint, so without a shared limiter "K=4" becomes six in flight when they overlap.

**Files:**
- Create: `src/redstring/extraction/limiter.py`
- Modify: `src/redstring/extraction/pipeline.py`, `src/redstring/composition/build_graph.py`
- Test: `tests/unit/extraction/test_limiter.py`, additions to `tests/unit/extraction/test_pipeline_concurrency.py`

**Interfaces:**
- Produces: `class CallLimiter` with `async def __aenter__/__aexit__`, constructed as `CallLimiter(limit: int)`; `ExtractionPipeline(..., limiter: CallLimiter | None = None)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/extraction/test_limiter.py`:

```python
"""One ceiling over every call the library makes against the endpoint.

The operator's constraint is the backend's queue depth. Batch structure alone
bounds only the extraction calls, so gleaning or embedding overlapping the
next batch turns a stated ceiling of four into six in flight.
"""

from __future__ import annotations

import asyncio

import pytest

from redstring.extraction.limiter import CallLimiter


async def test_it_admits_no_more_than_its_limit_at_once() -> None:
    limiter = CallLimiter(2)
    in_flight = 0
    peak = 0

    async def call() -> None:
        nonlocal in_flight, peak
        async with limiter:
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1

    await asyncio.gather(*(call() for _ in range(6)))

    assert peak == 2


async def test_a_raising_call_releases_its_slot() -> None:
    """A slot leaked on failure turns a transient error into a deadlock that
    looks like a hung model."""
    limiter = CallLimiter(1)

    with pytest.raises(RuntimeError):
        async with limiter:
            raise RuntimeError("boom")

    async with asyncio.timeout(1):
        async with limiter:
            pass


@pytest.mark.parametrize("bad", [0, -1])
def test_a_limit_below_one_is_refused(bad: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        CallLimiter(bad)
```

Append to `tests/unit/extraction/test_pipeline_concurrency.py`:

```python
async def test_gleaning_calls_are_inside_the_ceiling_too() -> None:
    """K=2 with one gleaning pass is four calls per batch, not two.

    Without a shared limiter the batch bounds only the first call of each
    chunk, so this is the case that separates "bounded batches" from "bounded
    in flight".
    """
    provider = RecordingProvider(delay=0.01)

    await _run(provider, concurrency=2, chunks=4, gleanings=1)

    assert provider.peak <= 2
```

- [ ] **Step 2: Run to verify both fail**

Run: `uv run pytest tests/unit/extraction/test_limiter.py tests/unit/extraction/test_pipeline_concurrency.py -v -p no:randomly`
Expected: the limiter tests fail on the missing module; the gleaning test fails with `peak == 4`.

- [ ] **Step 3: Implement**

Create `src/redstring/extraction/limiter.py`:

```python
"""A ceiling on calls in flight against the inference endpoint.

`ExtractionPipeline`'s batch size bounds how many *chunks* are extracted at
once. It does not bound gleaning, which fires a further call per chunk, or
embedding, which `build_graph` runs after the extraction it does not own. The
operator's constraint is the backend's queue depth -- a single-GPU llama.cpp
server processes one request at a time and converts ten concurrent requests
into ten timeouts -- so the ceiling has to be one object every call passes
through, not a property of any one loop.

Deliberately thinner than `asyncio.Semaphore`: it refuses a limit below one,
and it is a named type so a caller can see what it is holding.
"""

from __future__ import annotations

import asyncio
from types import TracebackType


class CallLimiter:
    """Admits at most `limit` callers at once."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
        self._limit = limit
        self._semaphore = asyncio.Semaphore(limit)

    @property
    def limit(self) -> int:
        return self._limit

    async def __aenter__(self) -> None:
        await self._semaphore.acquire()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Release on the way out, whatever happened.

        A slot leaked on failure turns one transient error into a permanent
        deadlock that presents as a hung model.
        """
        self._semaphore.release()
```

In `ExtractionPipeline.__init__`, accept `limiter: CallLimiter | None = None` and store `self._limiter = limiter or CallLimiter(concurrency)`. Wrap **every** `self._provider.extract(...)` call in the module — the one in `_extract_one` and the one in `_glean` — with `async with self._limiter:`.

In `build_graph`, construct one `CallLimiter(concurrency)` and pass it to the pipeline, then wrap the embedding call in `_embed_entities` with the same limiter. Add `concurrency: int = 1` to `build_graph`'s signature, documented in its docstring alongside the existing parameters.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/unit/extraction/ tests/unit/composition/ -q -p no:randomly`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/redstring/extraction/limiter.py src/redstring/extraction/pipeline.py src/redstring/composition/build_graph.py tests/unit/extraction/
git commit -m "Bound calls in flight, not batches

A batch of K bounds K extraction calls and nothing else. Gleaning fires a
further call per chunk and build_graph embeds after an extraction it does not
own, so a stated ceiling of four became six whenever either overlapped the
next batch -- and the operator's constraint is the backend's queue depth, not
this library's loop structure.

One CallLimiter now covers extraction, gleaning and embedding. It releases in
__aexit__ whatever happened: a slot leaked on failure turns one transient
error into a deadlock that presents as a hung model."
```

---

### Task 5: Let the benchmark ask for concurrency

The harness currently refuses `concurrency > 1` in two places, both deliberately, both naming this deliverable.

**Files:**
- Modify: `bench/config.py`, `bench/runner.py`, `bench/config.yaml`
- Test: `tests/unit/bench/test_config.py`, `tests/unit/bench/test_runner.py`

- [ ] **Step 1: Update the tests that pin the refusals**

In `tests/unit/bench/test_config.py`, replace `test_a_concurrency_above_one_is_refused_by_name` with:

```python
def test_a_concurrency_above_one_is_accepted_now_that_the_library_supports_it(
    tmp_path: Path,
) -> None:
    """The refusal was correct while the library was serial: a knob a serial
    library silently discards makes the concurrency work look like it changed
    nothing. Deliverable C landed, so it is a real value now."""
    config = load_config(
        write(tmp_path, MINIMAL.replace("concurrency: [1]", "concurrency: [1, 4]"))
    )

    assert config.concurrencies == (1, 4)
    assert len(config.sweep()) == 8


@pytest.mark.parametrize("bad", ["[0]", "[-1]"])
def test_a_concurrency_below_one_is_still_refused(tmp_path: Path, bad: str) -> None:
    with pytest.raises(BenchConfigError, match="concurrency"):
        load_config(write(tmp_path, MINIMAL.replace("concurrency: [1]", f"concurrency: {bad}")))
```

In `tests/unit/bench/test_runner.py`, replace `test_a_concurrency_above_one_is_refused_here_too` with a test that `run_point` passes the value through — a `RecordingProvider` whose peak in-flight count is asserted `<= point.concurrency` and `> 1` for `concurrency=2` over a document of at least four chunks.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/bench/test_config.py tests/unit/bench/test_runner.py -v -p no:randomly`
Expected: FAIL — the old refusals still fire.

- [ ] **Step 3: Implement**

In `bench/config.py`, replace the `set(concurrencies) != {1}` refusal with a check that every value is `>= 1`, keeping a message that names `concurrency`. In `bench/runner.py`, delete the `point.concurrency != 1` `ValueError` and pass `concurrency=point.concurrency` to `build_graph`.

In `bench/config.yaml`, set `concurrency: [1, 2, 4, 8]` and replace the comment about deliverable C with one saying the ceiling is in-flight requests against the endpoint and that the sweep stops climbing on a reversal.

- [ ] **Step 4: Run the bench suite**

Run: `uv run pytest tests/unit/bench/ -q -p no:randomly`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bench/ tests/unit/bench/
git commit -m "Let the benchmark ask for concurrency

Both refusals named deliverable C and it has landed. What stays refused is a
value below 1, and what replaces the old test is a test that the value
actually reaches the pipeline -- a knob accepted and dropped is the failure
the original refusal existed to prevent, wearing the opposite sign."
```

---

### Task 6: Measure it

**Files:**
- Create: `bench/results/<timestamp>.json` (produced), `bench/CONCURRENCY.md`
- Modify: `bench/BASELINE.md` (link forward)

- [ ] **Step 1: Run the sweep**

Run: `uv run python scripts/benchmark.py --config bench/config.yaml`

Twelve points at three chunk sizes × four concurrencies × three repeats, minus whatever `stop_climbing_concurrency` skips. Do not run anything else against the endpoint while it runs — a benchmark sharing a GPU measures the other process.

- [ ] **Step 2: Write the reading**

Create `bench/CONCURRENCY.md` covering, from the results JSON:

- wall clock per `(chunk_size, concurrency)`, and where the curve turns over — the K at which the backend starts queueing is the operator's real answer
- **`variant_pairs` per configuration against the K=1 column**, which is the question this deliverable exists to answer: does a wavefront manufacture naming drift, and how much
- stability per configuration, read against the baseline's 0.601–0.667 — a change inside that band is not a finding
- `failed_chunks` and `timed_out`, which say whether the endpoint coped
- the recommended default, with the reasoning, and explicitly whether `12000 × K` beats `12000 × 1`

State plainly where the numbers cannot answer something. The drift metric is a lower bound; entity count still cannot carry a quality claim.

- [ ] **Step 3: Commit**

```bash
git add bench/results/ bench/CONCURRENCY.md bench/BASELINE.md
git commit -m "Record what bounded concurrency bought, and what it cost in drift"
```

---

### Task 7: The ADR

`.claude/rules/definition-of-done.md` requires an ADR for a change to a public contract. This adds a parameter to `ExtractionPipeline` and to `build_graph`, and it makes the fold's order-independence load-bearing.

**Files:**
- Create: `docs/adr/00NN-bounded-concurrency-over-chunks.md`
- Modify: `docs/adr/0010-one-total-order-for-preference.md` (Consequences), `mkdocs.yml` nav

- [ ] **Step 1: Allocate the number at merge time**

Run: `git ls-tree --name-only main docs/adr/ | sort | tail -1`

Use the next number after whatever that prints. **Re-run it immediately before merging** — parallel branches routinely draft the same next number, and the rule in `recurring-defects.md` §6 is that renumbering means the filename, the H1 **and** every inbound citation, in one commit.

- [ ] **Step 2: Write it**

Follow the structure of a neighbouring ADR — read `docs/adr/0007-composition-is-the-only-top-layer.md` for the house shape. Cover:

- **Decision:** chunks extract in wavefront batches of a caller-set size; carryover accumulates between batches; one `CallLimiter` bounds every call against the endpoint, including gleaning and embedding.
- **Forces:** the reference deployment is a single-GPU llama.cpp server, and the pipeline's own docstring recorded that firing ten concurrent requests at it converts a queue into ten timeouts. That argument was never wrong — it was an argument for a *bound*, and it had been implemented as the bound `1`. The baseline turned it into a number: 14 serial calls at ~24s each.
- **Why carryover survives:** identity is derived from the name, so a chunk that spells a name differently manufactures an entity the fold cannot combine. A batch bounds how much of the document a chunk is blind to; full concurrency would make every chunk blind to every other.
- **What made it safe:** `merge_extractions` is order-independent because `preference` is total — ADR 0010, now asserted by a property test rather than a docstring.
- **Consequences:** `concurrency=1` is byte-identical, so the default is unchanged for every existing caller; a caller raising it trades naming stability for wall clock, measured in `bench/CONCURRENCY.md`; the ceiling is in-flight calls, so it composes with a backend serving other tenants.
- **What it does not do:** no per-chunk progress (that is deliverable B), no cross-document concurrency, and no adaptive tuning of K.

Add an "Amended by" note to ADR 0010's Consequences: its total order is now depended on by concurrent extraction, not merely by the fold.

- [ ] **Step 3: Verify the docs build**

Run: `uv run mkdocs build --strict`
Expected: passes. A broken inbound link is what §6 says makes a half-finished renumber invisible.

- [ ] **Step 4: Commit**

```bash
git add docs/adr/ mkdocs.yml
git commit -m "Record bounded concurrency as a decision, and what made it safe"
```

---

## Self-Review

**Spec coverage (section C):**

| Spec requirement | Task |
|---|---|
| `ExtractionPipeline(concurrency: int = 1)`, wavefront batches | 3 |
| carryover accumulates between batches | 3 |
| `K=1` byte-identical, proved | 3 (existing suite unedited) |
| ceiling is a shared limiter over every call | 4 |
| failure semantics unchanged, siblings not cancelled | 3 |
| the sweep can ask for K > 1 | 5 |
| measured against the baseline | 6 |
| ADR 0010 read against this work | 2, 7 |

Added beyond the spec, with reasons: **Task 1** (the spec's metrics cannot detect C's stated risk — `B-BENCH-3`), **Task 2** (the spec asserts the fold is order-independent; nothing tested it), **Task 7** (required by `definition-of-done.md`, not by the spec).

**Type consistency:** `CallLimiter` is constructed as `CallLimiter(limit)` and used as an async context manager in Tasks 4 and 5; `concurrency` is the parameter name on `ExtractionPipeline`, `build_graph` and `SweepPoint` alike; `variant_pairs` is the function in Task 1 and the `RunMetrics` field in Tasks 1 and 6.

**Known soft spots, flagged rather than hidden:**
- Task 3's `_run`/`_answer_for` helpers and Task 5's `RecordingProvider` reuse are described rather than written out, because they must match fixtures in existing test modules the implementer will read. Both steps say to read those modules first.
- The exact line numbers in `pipeline.py` are not cited: the loop body moves, and a line reference in a plan decays faster than anything else in it.
- Task 6 cannot be planned in detail — it reports numbers that do not exist yet. Its value is the list of questions to ask of them.
