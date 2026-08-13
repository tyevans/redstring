# Ingestion baseline, 2026-08-13

Read from `bench/results/2026-08-13T21-54-30Z.json`. One machine, one day, one
model id: `muse-glimmer-30b` behind llama-swap at `192.168.1.14:8080`,
redstring 0.7.0 at `e2f84f8`. Document: the plain-text Wikipedia article for
*Harry Potter and the Philosopher's Stone*, 32,790 characters. Three repeats
per configuration, serial extraction throughout — `concurrency` cannot be
anything but 1 until deliverable C.

## The headline

**Chunk size is the cheapest large win available, and it needs no library
change at all.** At 12,000 characters the same document extracts in **153s
against 333s** at the 3,000-character default — 2.2× faster, from a config
value the caller already controls.

| `chunk_size` | median wall clock | chunks | per chunk | entities (mean) | relationships (mean) |
|---|---|---|---|---|---|
| 3,000 (default) | 332.7s | 14 | 23.8s | 209.3 | 276.0 |
| 8,000 | 177.5s | 6 | 29.6s | 163.7 | 169.7 |
| 12,000 | 153.4s | 4 | 38.4s | 166.0 | 140.7 |

The reason it works is in the fourth column: **a 4× larger chunk costs only
1.6× more per call.** Per-call latency is dominated by something that is not
input length — model load, sampling overhead, the reasoning preamble — so
fewer, larger calls beat more, smaller ones by a wide margin.

## The number that is not a win, and cannot be read as one

Entity count falls 21% between 3,000 and 12,000 (209 → 166). **This baseline
cannot tell you whether that is worse.**

Both readings fit the data. A larger chunk may genuinely miss entities a
smaller one caught. *Or* a smaller chunk may be manufacturing duplicates:
14 chunks means 13 boundaries, each an opportunity for "Harry Potter" in one
window and "Harry" in the next, and `extraction.mapping` derives identity from
the name — so naming drift at a boundary does not merge, it creates a second
entity. Relationship count moves the same way and further (276 → 141), which
is consistent with either story.

Nothing here distinguishes them, because these documents are **ungraded**.
That is BACKLOG `B-BENCH-1`, and this is precisely the question it was filed
for. Do not quote the 21% as a quality loss, and do not quote the 2.2× as free.

## Stability is low, and it changes how every later comparison must be run

Jaccard agreement between repeats of an identical configuration:

| `chunk_size` | jaccard | always | sometimes |
|---|---|---|---|
| 3,000 | 0.667 | 166 | 83 |
| 8,000 | 0.768 | 139 | 42 |
| 12,000 | 0.601 | 128 | 85 |

**A third of the entities found at 3,000 characters appear in some runs and
not others**, on identical input against a temperature-0 provider. The
practical consequence is a methodology rule rather than a bug report: a single
run's entity count is worth roughly ±15%, so **no A/B on extraction quality
can be read from one run per arm.** Deliverable C's whole risk is naming drift
at chunk boundaries, and drift is measured in exactly this number — against
this spread, a change would have to move stability a long way before it was
distinguishable from noise.

That 8,000 sits highest, with 3,000 and 12,000 both lower, is not something to
build on: three points, three repeats each, and the metric's own limit is that
both sides of it come from the code under test.

## Accuracy, on the graded corpus

Five hand-graded documents, scored against `tests/accuracy/corpus.yaml`:

| | true positives | false positives | false negatives |
|---|---|---|---|
| entities | 12 | 5 | 0 |
| relationships | 6 | 7 | 0 |

Recall is **1.0** on both — everything the corpus asserts was found. Precision
is 0.71 for entities and 0.46 for relationships, i.e. the model states more
than the text does. Read this as "extraction is working", not as a benchmark:
five short documents, and `tests/accuracy/corpus.py` says at the top why a
change in these figures is noise until it is large.

## What could not be measured, and why that is deliberate

- **`time_to_first_entity_s` is `null` on every run.** `build_graph` is one
  opaque await; a returned completion is not a mapped entity, and nothing
  outside the call can see the merge. This is the field deliverable B exists to
  fill, and it is left empty rather than estimated so that B's improvement is
  readable against it.
- **`consolidate_s` is `null`.** Not an oversight: consolidation happens inside
  `build_graph`, which per ADR 0015 makes no adjudicator calls at all, so the
  extract/consolidate split the spec asked for is unobservable from here. A
  zero would have read as "consolidation used no model time", which is a claim
  rather than an absence.
- **`event_gaps_s` is empty**, for the same reason as the first: there are no
  progress events to measure gaps between. Perceived event rate is
  0 events per run today. That is the finding, and it is the one the downstream
  report was actually complaining about.

`failed_chunks` was 0 everywhere and `timed_out` is empty, so none of the
above is a partial run.

## What this says about deliverables B and C

1. **Ship the chunk-size finding first.** 2.2× for a config default, available
   today, no library change. It should be qualified by the entity-count
   question above rather than adopted blind — but a caller ingesting long
   documents at 3,000 characters is paying double for reasons nobody chose.
2. **Concurrency (C) is still the larger lever, and now has a number.** At
   3,000 characters this document is 14 strictly serial calls at ~24s each. A
   bounded wavefront at K=4 has roughly 4× of wall clock available to it — more
   than chunk size gave — and unlike chunk size it does not trade away chunk
   boundaries. The two compose: 12,000 characters at K=4 is the configuration
   to measure first.
3. **The progress port (B) is worth it on this evidence alone.** The best case
   here is 38s to the first sign of life and 153s to any output at all; the
   default is 333s. A caller currently waits five and a half minutes with no
   signal, and the harness itself can only report `null` for the metric that
   describes it.
4. **Sixteen minutes was never this document.** The report that started this
   work described 16 minutes for this page; the measured figure at the default
   chunk size is 5.5. Whatever cost that caller sixteen minutes — a larger
   input, a different model, cross-document consolidation calls — is not
   reproduced here, and finding it is a separate question from making this
   faster.
