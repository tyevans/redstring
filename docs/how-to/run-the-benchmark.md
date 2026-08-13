# Run the ingestion benchmark

`scripts/benchmark.py` measures `build_graph` against a live OpenAI-compatible
endpoint: wall-clock time per document at a range of chunk sizes, model call
counts, entity/relationship counts, run-to-run stability, and — optionally —
extraction accuracy against the graded corpus. Nothing under `src/redstring/`
is touched to run it; everything the harness needs lives in `bench/`.

```
uv run python scripts/benchmark.py
uv run python scripts/benchmark.py --config bench/config.yaml --no-accuracy
```

Run it after a change that could move ingestion latency or extraction
quality, and before quoting either number in a plan or an ADR. It is **not**
part of the commit gate or CI — it needs a live model and can run for the
better part of an hour, which is exactly why `bench/preflight.py` refuses
loudly rather than producing a plausible number from a broken endpoint (see
below).

## What it needs

- **An OpenAI-compatible endpoint** serving both the extraction model and the
  embedding model named in `bench/config.yaml`. There is no container for
  this — point `endpoint:` at whatever you are running, the way
  [the live integration suite](run-integration-and-mutation-suites.md#what-each-subdirectory-needs-the-llm-subset-needs-kg_llm_base_url-not-docker)
  does.
- **Both models loaded**, not merely listed. `llama-swap` and similar proxies
  list every model they are configured for whether or not the weights load —
  the same trap `tests/accuracy/test_extraction_accuracy.py` and the live
  integration suite are both written around (BACKLOG B12). Preflight below
  is what catches it here.
- **The long-document corpus on disk**, `bench/corpus/*.txt` plus a
  `.meta.yaml` beside each naming `source`, `retrieved` and `licence` —
  the library never fetches, and neither does this harness, so the operator
  supplies the text (`bench/corpus/README.md`).

Nothing else. `uv sync --all-extras` covers the `llm` extra this needs; see
[quality gates](../reference/quality-gates.md).

## Every knob lives in `bench/config.yaml`

`scripts/benchmark.py` wires the pieces and owns no knobs of its own —
re-running a variant is an edit to the config file, never a flag:

```yaml
endpoint: http://192.168.1.14:8080/v1/
models:
  extraction: muse-glimmer-30b
  embedding: nomic-embed-text
  embedding_dimensions: 768
  max_tokens: 16384
corpus:
  graded: true
  long: [harry-potter-1]
sweep:
  chunk_size: [3000, 8000, 12000]
  concurrency: [1]
policy:
  repeats: 3
  stop_climbing_concurrency: true
  per_document_timeout_s: 1800
```

`models.max_tokens` is not a library default — it exists because
`tests/accuracy/test_extraction_accuracy.py` documents that the library's own
default (`DEFAULT_MAX_TOKENS`, 8192) is too small for this corpus against a
reasoning model, which spends most of a short answer on chain of thought
before `content` begins. Left unset here, the accuracy pass would silently
take the smaller budget and every document could fail with
`EmptyCompletionError(finish_reason='length')` rather than one scoring badly.

`sweep.concurrency` accepts only `[1]` today and is refused, not silently
ignored, at any other value — the library extracts chunks serially, so a
config claiming concurrency 4 would report a number that means nothing until
deliverable C lands.

The whole resolved file is embedded verbatim in every results file
(`config` key), including any key this version of the loader does not read —
a result that cannot say what produced it is an anecdote.

## What it refuses, and why fast is the dangerous outcome here

`bench/preflight.py`'s docstring states the design directly: **a broken
benchmark run is fast, not slow, and reads as an improvement.** A pipeline
that silently extracts nothing from a 100k-character document finishes in
seconds and wins every grid it appears in. So the harness checks four things
before timing anything, each of which the other three cannot see — both
model ids are actually listed, a real completion comes back non-empty, an
embedding is the configured width, and a warm-up extraction produces at
least one entity — and refuses outright rather than warning.

`bench/config.py` refuses just as hard on the config itself: `repeats: 0`,
an empty `chunk_size` list, or an empty `long` document list all produce a
sweep that measures nothing, and each is refused by name rather than
silently running zero points.

## Reading the exit code

`main`'s own docstring is the source of truth; it is repeated here because
it is the one place a caller scripting around this is most likely to look
first, and the one place in the source least likely to be read:

| Code | Meaning |
|---|---|
| `0` | Ran to completion. Nothing timed out, nothing failed, the accuracy pass (if it ran) succeeded. |
| `1` | `PreflightError` — the endpoint cannot produce a measurement worth recording. The message names which of the four checks failed. |
| `2` | `BenchConfigError` — the config file is missing a required key, or would produce a run that measures nothing. |
| `3` | At least one sweep point timed out, at least one point failed for another reason, or the accuracy pass itself raised. A report is still written for everything that completed — see below — but the run is not clean. |
| `4` | `BenchCorpusError` — a configured long document is absent, blank, or missing its provenance metadata. Distinct from `2` because a missing corpus file is a different problem from malformed YAML. |

**Exit code `3` still writes a report**, and that is deliberate rather than a
partial failure to work around. A single chunk failure, a document that
exceeds `policy.per_document_timeout_s`, or a graded-corpus document that
raises must not discard every measurement already paid for on a live GPU —
those all cost real time against a real endpoint, and the alternative is a
traceback and nothing to show for the hour. The report names exactly what
was lost:

- `timed_out` — every sweep point that exceeded the timeout, as a list of
  points. Empty, not absent, when nothing timed out.
- `failed` — every sweep point that raised for any other reason, each paired
  with the exception's message. `run_point` already passes
  `skip_failed_chunks=True` to `build_graph`, so an ordinary single-chunk
  failure is absorbed and shows up as `RunMetrics.failed_chunks` on a
  completed run instead — an entry here means the *document* failed, not one
  chunk of it.
- `accuracy: null` — the graded corpus ran and raised, rather than not
  running at all (`--no-accuracy`, or `corpus.graded: false`, both of which
  also write `null`). The two cases are distinguishable by the exit code:
  `0` or `3` with `--no-accuracy` set means the operator chose to skip it;
  `3` with accuracy configured on means it tried and failed.

A run interrupted before `write_report` — killed by an operator, a crash the
harness itself could not catch — writes nothing at all, which is the one
outcome none of the above can help with. Consider that report missing rather
than partial.

## Running only part of it

```
uv run python scripts/benchmark.py --no-accuracy
```

Skips the graded-corpus accuracy pass; the sweep still runs. `--no-accuracy`
is an escape hatch for a step `corpus.graded` already gates in the config —
neither can force accuracy *on* by itself, so there is no precedence
question between the flag and the config key, only two independent ways to
turn the pass off.

```
uv run python scripts/benchmark.py --config bench/config-narrow.yaml
```

Point at a different config file to run a narrower sweep — one chunk size,
one repeat — while iterating, rather than editing `bench/config.yaml` and
reverting it.

## What lands in `bench/results/`

One JSON document per invocation, named for when the run started
(`{started_at}.json`). Alongside `runs`, `stability` and `accuracy`, every
long document actually benchmarked has its `source`, `retrieved` and
`licence` recorded under `corpus_provenance` — the harness benchmarks
third-party text, and a results file that outlives the corpus on disk should
still be able to say where that text came from.

## Related

- [Run the integration and mutation suites](run-integration-and-mutation-suites.md) —
  the other deliberately-outside-the-commit-gate runs, and the same
  `--all-extras` sync trap.
- `bench/corpus/README.md` — the operator-supplied corpus and its provenance
  requirement.
- `docs/reference/quality-gates.md` — what runs on commit and what does not.
