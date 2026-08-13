# Ingestion benchmark, progress port, and bounded concurrency

Date: 2026-08-13

## Why

A downstream caller (`research-team`) waited sixteen minutes for one Wikipedia
page and saw nothing during it. Three separate deficiencies produce that
experience, and they are not equally expensive to fix:

1. **`build_graph` is one opaque await.** It takes no callbacks and emits
   nothing until it returns. Downstream fabricates progress by wrapping
   `LlmProvider` and counting calls, which yields a numerator with no
   denominator — "17 calls" rather than "chunk 17 of 35" — because the chunk
   count exists only inside the pipeline.
2. **Chunks are extracted strictly one at a time.** Deliberate, documented in
   `extraction/pipeline.py`, and justified by a single-GPU reference
   deployment. The justification is an argument, not a measurement.
3. **Nothing measures any of this.** `tests/accuracy/` scores extraction
   quality against a graded corpus and says nothing about latency, so any
   change to (1) or (2) would be judged by feel.

(3) is the one to fix first, because it is what makes (1) and (2) decidable.

## Scope

Three deliverables, in order, each measured against the one before it:

- **A. Benchmark harness** — `scripts/benchmark.py`, `bench/`, no library
  change. Produces the baseline.
- **B. Progress port** — `ports/progress.py` + `domain/progress.py`; the
  pipeline reports per-chunk. Real time-to-first-entity, real denominator.
- **C. Bounded concurrency** — wavefront batches with a hard in-flight
  ceiling, `K=1` byte-identical to today.

Out of scope: fetching corpus documents (redstring never fetches; the
benchmark corpus is committed text), changing the chunkers, changing
consolidation policy, and any CI gate on benchmark numbers.

## A. Benchmark harness

```
bench/config.yaml          # endpoint, models, corpus, sweep matrix, run policy
bench/corpus/*.txt         # long documents, committed
bench/corpus/*.meta.yaml   # provenance: source URL, retrieval date, licence
bench/results/<ts>.json    # one file per invocation, machine-readable
scripts/benchmark.py       # the runner
```

### It refuses to start

`scripts/mutation.py` exists because an environment lying about the code is
undetectable from the output. A benchmark has the identical hazard with the
sign flipped: a dead or wrong endpoint does not report "dead", it reports a
suspiciously good number, and a run whose extraction returns nothing is the
*fastest* run in the grid.

Preflight, before any timing:

- `GET /v1/models` lists both configured model ids. Not "the endpoint answers"
  — the specific ids, because llama-swap serving one of the two is the failure
  that produces a plausible partial run.
- One completion against the extraction model and one embedding against the
  embedding model, both asserted non-empty and of the expected dimension
  (`nomic-embed-text` is 768; a dimension check written against a toy value is
  the identity-vs-equality trap in CLAUDE.md's table).
- One warm-up extraction over a short document, asserted to produce **at least
  one entity**. A zero-entity pipeline is this project's zero-survivor
  mutation run, and it must be refused rather than recorded.

Each refusal names which check failed and what it saw.

### Configuration

```yaml
endpoint: http://192.168.1.14:8080/v1/
models:
  extraction: muse-glimmer-30b
  embedding: nomic-embed-text
  embedding_dimensions: 768

corpus:
  graded: all                 # the five docs in tests/accuracy/corpus.yaml
  long: [harry-potter-1]      # bench/corpus/*.txt

sweep:
  chunk_size: [3000, 8000, 12000]
  concurrency: [1, 2, 4, 8]

policy:
  repeats: 3                  # for the stability metric and for variance
  stop_climbing_concurrency: true
  per_document_timeout_s: 1800
```

Every knob that changes a number lives here. Re-running a variant is an edit
to this file; the results JSON embeds the resolved config verbatim, so a
result can always say what produced it.

**`stop_climbing_concurrency`**: if `K=8` is slower than `K=4` on a document,
the backend is queueing and higher `K` is measuring the queue. The runner
records the reversal and skips the remaining higher values for that document
rather than spending twenty minutes confirming it.

### Metrics

Per (document, chunk_size, concurrency, repeat):

| Metric | Definition |
|---|---|
| `wall_clock_s` | `build_graph` entry to return |
| `time_to_first_entity_s` | entry to the first progress report carrying ≥1 entity |
| `event_gaps_s` | full sorted list of inter-report intervals |
| `phase_split_s` | classify / extract / consolidate / embed |
| `model_calls` | count, by phase |
| `chunks` | the denominator, from the progress port |
| `entities`, `relationships` | counts after merge |
| `adjudicator_calls` | consolidation model calls |
| `failed_chunks` | as reported by the pipeline |

`event_gaps_s` is stored as the **whole list**, and reported as p50/p95/max.
Perceived responsiveness is a distribution: `3,3,3` and `1,1,7` share a mean
and feel nothing alike, so a mean is the one summary that must not be the only
one stored.

Before B lands, `time_to_first_entity_s` and `chunks` are unavailable and are
recorded as `null` rather than estimated. The baseline is honest about what it
could not see; a wrapper-provider approximation recorded in the same field
would make B's improvement unreadable.

### Accuracy and stability are two metrics, not one

- **Accuracy**: the existing `tests/accuracy` scorer over the five graded
  documents, unchanged. Precision, recall, F1, entities and relationships
  separately. This is the correctness floor.
- **Stability**: over `repeats` runs of one long document, the Jaccard
  similarity of the extracted entity-name sets. Reported under the name
  *stability*, never *accuracy*.

The naming is load-bearing. A pipeline that drops half of every document
deterministically scores stability 1.0 — both sides of the comparison share
the implementation under test, which CLAUDE.md records as the shape that let
three broken handlers pass a replay-equivalence suite. Stability detects
*variance* introduced by concurrency and by carryover changes, which is the
only question it is being asked, and it is a real question: the whole risk of
C is naming drift, and drift shows up as instability.

### Testing the harness

The harness is code, and a benchmark that mismeasures is worse than none.

- Scoring, timing and reporting are unit-tested against a **scripted
  provider** with injected sleeps: a provider that stalls 2s then answers must
  produce `time_to_first_entity_s ≈ 2`, and the assertion is written as a
  literal, not in terms of the injected constant.
- The preflight refusals are each tested by breaking them on purpose — a
  models list missing one id, a zero-entity warm-up, a wrong embedding
  dimension. A gate whose happy path is "the file is there" is not believed
  until it has been watched failing.
- `event_gaps_s` is asserted on a run with **deliberately uneven** gaps, so a
  summariser that reports the mean alone fails.

## B. Progress port

### Port, not durable event

`events/document.py` holds aggregate events: tenant-scoped, durable,
replayable, folded by projections. A per-chunk progress signal is none of
those. It has no aggregate state, must not be replayed, and the pipeline's own
docstring states that "the chunk boundary is gone after the merge" — a durable
per-chunk event puts it back, and something will eventually fold it.

So:

```python
# ports/progress.py
class ProgressSink(Protocol):
    def report(self, event: ProgressEvent) -> None: ...

# domain/progress.py  (frozen, slots)
Chunked(total: int)
ChunkExtracted(index: int, total: int, entities: int, relationships: int, elapsed_s: float)
ChunkFailed(index: int, total: int, reason: str)
Consolidating(candidates: int)
Adjudicated(merged: int, kept: int)
Embedded(count: int)
```

`ExtractionPipeline.__init__` and `build_graph` take
`progress: ProgressSink | None = None`. `None` is a no-op, so every existing
caller is unaffected and the default path allocates nothing.

`report` is **synchronous and must not raise**. A sink is an observer; a
downstream UI whose queue is full must not fail an extraction that has already
been paid for. The pipeline wraps each `report` and swallows, and there is a
test that a raising sink does not fail a run.

Layer placement: `ports` sits below every sibling, `domain` below that.
Nothing in the contract changes, nothing gains a store reference, and
`extraction` still reaches only `ports`.

### `ChunkFailed` is not optional

`skip_failed_chunks` currently makes failures invisible until the summary.
Reporting them per chunk is the difference between "it is slow" and "chunk 12
of 35 failed and was skipped", which is the question downstream is actually
asking during those sixteen minutes.

### Testing

- A recording sink over a three-chunk document asserts the **exact sequence**,
  including `total` on every event — a `total` that is right only on the last
  event is the defect a summary-only assertion cannot see.
- A document whose second chunk fails asserts `ChunkFailed` then continued
  reporting for chunk three. Per CLAUDE.md's one-item-loop row: a bad chunk
  **followed by a good one**, never a bad chunk last.
- A raising sink does not fail the run.
- The no-sink path is asserted to make no calls (a `None` default that
  constructs a null object every chunk is a silent allocation regression).

## C. Bounded concurrency

### Wavefront

`ExtractionPipeline(concurrency: int = 1)`. Chunks are extracted in batches of
`K`; carryover accumulates **between** batches, so a chunk's prompt carries
every name found by every completed batch, and naming drift is bounded by `K`
rather than unbounded.

`K=1` must be **byte-identical to today**: same call sequence, same prompts,
same result. That is a property test comparing a scripted-provider run at
`K=1` against the recorded prompt sequence of the current implementation. It
is what makes the knob a measurement rather than a rewrite, and it is what
lets the sweep's `K=1` column serve as the baseline within every grid.

### The ceiling is a shared limiter

`K` bounds in-flight requests **against the inference backend**, which is the
constraint the operator actually has. Batch structure alone bounds only the
extraction calls; consolidation adjudication and embedding also hit the same
endpoint, so a single `asyncio.Semaphore(K)` is threaded through every call
site the pipeline owns. "K=4" must never mean six in flight because embeddings
overlapped the next batch.

There is a test for exactly this: an instrumented provider that records
maximum observed concurrency across a run including consolidation and
embedding, asserted `<= K`, with `K=2` on a document large enough that the
naive implementation exceeds it.

### Failure semantics are unchanged

`skip_failed_chunks` still governs. A batch containing a failure does not
cancel its siblings — their results are already paid for — and
`record(allow_partial=...)` still refuses to record a partial extraction as a
whole one. `asyncio.gather(return_exceptions=True)` per batch, failures
mapped to the same path the serial loop uses.

## Order, and what each step is judged by

1. **A** on unmodified code → baseline (`time_to_first_entity_s` null).
2. **B** → time-to-first-entity and the real denominator appear; wall clock
   must be unchanged within noise. A progress port that costs 5% is a finding.
3. **C** → the sweep grid. The decision the grid makes: which `(chunk_size,
   K)` to recommend as defaults, and whether increased adjudicator calls from
   naming drift eat the concurrency win.

Each step commits its results JSON, so the improvement is reviewable as data
rather than as a claim.

## Against the existing ADRs

Required by `.claude/rules/definition-of-done.md`: every related ADR is named,
with whether it stands.

| ADR | Verdict |
|---|---|
| `0008` the two non-store ports | **Amended by deliverable B.** It settles what `Cache` and `LlmProvider` promise; a third non-store port changes the set it describes. B writes a new ADR and adds an "Amended by" pointer to `0008`'s status. |
| `0007` composition is the only top layer | **Stands.** `ProgressSink` is a parameter threaded through `build_graph`, not a second module in `composition`. |
| `0001` event log schema and granularity | **Stands, and deliverable B is the reason it needs saying.** Per-chunk progress is deliberately *not* an event: it has no aggregate state and must not be replayed. Choosing the port is what leaves `0001` untouched, so the alternative belongs in B's ADR as the option rejected. |
| `0011` domain schemas prompt but do not constrain | **Stands.** Nothing here changes what a schema does. |
| `0006` the public surface is gated | **Engaged by B.** If `ProgressSink` or the progress value objects appear in `build_graph`'s signature, `__all__` must export them and their closure, or the signature gate fails. That is the gate working, not an obstacle. |
| `0010` one total order for preference | **Stands, and is the ADR deliverable C must be read against.** Concurrency changes which chunk observes an entity first; if any tie-break depends on extraction order, C changes results rather than only timings. C's plan proves order-independence or amends this. |

Deliverable A touches no ADR: it modifies no library code.

## Backlog and risks

- Long-document corpus is **ungraded**, so accuracy on it is unmeasurable by
  construction. Accepted deliberately: hand-grading a 100k-character document
  is hours of work, and CLAUDE.md's "omission is a claim" makes a partially
  graded document report a precision failure belonging to the grader. Filed in
  `BACKLOG.md` with this reasoning so it is a decision rather than a gap.
- Benchmark numbers depend on a machine that is not CI's. No gate, no ratchet;
  results are a committed record read by a human.
- `bench/corpus/` carries third-party text. Each document gets a `.meta.yaml`
  naming source, date and licence, and anything not redistributable is
  referenced by URL with a fetch script the operator runs — the library still
  never fetches.
