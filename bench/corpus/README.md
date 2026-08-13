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
