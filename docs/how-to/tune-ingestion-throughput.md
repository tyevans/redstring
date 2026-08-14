# Tune ingestion throughput

`build_graph` takes two knobs that decide how long a document takes to
ingest — `default_chunk_size` on the chunker and `concurrency` on the call
itself. They interact, so tuning either alone gives the wrong answer.

This page gives you the arithmetic (which holds everywhere), the shape of the
trade (which holds for any model), and one worked measurement (which holds for
nothing but the machine it ran on).

## Start here: the numbers on this page are not your numbers

Everything measured below used one model on one GPU against one document.
A different model, a different server, or a longer document gives different
answers — sometimes by 2×. **Do not copy the recommended values.** Copy the
method, and take ten minutes to measure your own; the last section says how.

What *does* transfer is the arithmetic in the next section, which is a
property of how the pipeline batches rather than of anything a model does.

## The arithmetic: why raising `concurrency` often changes nothing

Extraction runs a **wavefront**: chunks go out in batches of `concurrency`,
and the results fold back in chunk order before the next batch starts. So:

```text
calls in flight = min(concurrency, chunks remaining in the batch)
```

A 33,000-character document at `default_chunk_size=12000` is **4 chunks**.
Setting `concurrency=8` issues one batch of 4 — exactly what `concurrency=4`
does. The two are the same run, and measuring them will show you a difference
that is entirely noise.

**If raising `concurrency` did nothing, you are probably out of chunks.** Get
more concurrency by making chunks *smaller*, not by raising the ceiling.

```python
from redstring import SlidingWindowChunker, build_graph

chunker = SlidingWindowChunker(default_chunk_size=2000)

report = await build_graph(
    document,
    provider=provider,
    store=store,
    tenant_id=tenant_id,
    chunker=chunker,
    concurrency=8,
)
```

### Ask the chunker for the chunk count; do not estimate it

It is tempting to estimate chunks as `len(text) / chunk_size`. **That is wrong
by 28–47% in the ordinary case**, because the chunker advances by
`chunk_size - overlap` and backs each break up to a paragraph, sentence or
word boundary. On a 32,791-character document the formula gives 2.7 chunks at
`default_chunk_size=12000`; the chunker produces 4.

The error runs in the harmful direction: estimating low caps `concurrency`
below what the document could actually use. Ask instead — it is exact and
costs nothing:

```python
chunks = chunker.chunk(document.text).total_chunks
report = await build_graph(..., chunker=chunker, concurrency=min(slots, chunks))
```

Set `concurrency` to **your inference server's concurrent-request capacity**,
not to an arbitrary number. Asking for more than the server serves does not
fail — it queues, and a queue is hard to tell from a slow GPU from the client
side. The next section is what does and does not distinguish them.

## Diagnosing "concurrency didn't help"

Two different causes produce the same symptom, and one client-side measurement
separates them from the third:

1. **You are out of chunks** — see the arithmetic above. Check
   `chunker.chunk(text).total_chunks` against your `concurrency`. This is the
   most common cause and the easiest to rule out.
2. **Your server serves fewer concurrent requests than you asked for.** Your
   calls are queueing. Check the server's configuration directly — for
   llama.cpp, the parallel-slots setting.
3. **The GPU is genuinely saturated.** Nothing to do in the library.

**A word of warning about a metric that looks like it separates these and does
not.** It is natural to compute *overlap* — summed per-call duration divided
by wall clock — and read it as "how many calls were really in flight". It is
not that. Per-call duration measured client-side around the `await` includes
time the request spent **queued** on the server, so a single-slot server
serving eight queued requests still reports high overlap. In the measurements
below, a server known to be serving one request at a time scored 1.61× and
2.07×, and raising its slot count moved that figure by 0.09.

What overlap *is* good for is confirming the wavefront is issuing concurrent
calls at all: overlap pinned at ~1.0 with `concurrency > 1` means the batching
is not happening, which points at cause 1.

The signal that actually caught a misconfigured server was **per-call latency
rising in proportion to `concurrency`** — 38.4s serial becoming 80.4s at K=4,
i.e. four calls each taking four times as long, for no wall-clock gain.

## The trade: smaller chunks find more, until they start inventing

Chunk size moves extraction quality, and it does not move it monotonically.

Smaller chunks give the model less text per call, so it extracts more
thoroughly from each one — more entities, more relationships. But identity
here is derived from the **entity name** (see
`docs/adr/0009-the-extraction-fold-resolves-through-aliases.md`), and every
chunk boundary is a chance for the same person to be written differently in
adjacent windows. "Harry Potter" in one chunk and "Harry" in the next are two
entities, not one. Carryover passes recent names forward to reduce this; it
does not eliminate it.

So there is a floor, and **it is not a recall floor — it is an identity
floor.** Past it, entity counts keep climbing while an increasing share of
those "entities" are variants of each other.

The signal to watch is not entity count. Track the ratio of **relationships to
entities**: a graph whose entity count rises while its relationship-per-entity
ratio falls is fragmenting, not improving.

Going the other way — very large chunks — fails harder and faster. Handing a
model an entire document in one call, even one that fits its context window
comfortably, extracts dramatically less: attention spreads thin and no span
gets a second look. In the measurement below, one call over a whole document
found 10% of the relationships that 21 calls over the same text did.
**Chunking is doing real work, not working around a context limit.**

## One worked example

`muse-glimmer-30b` behind llama-swap, 8 concurrent slots, one 32,791-character
Wikipedia article. Full detail, including what could not be measured, is in
`bench/CONCURRENCY.md`; the baseline it is read against is `bench/BASELINE.md`.

| `default_chunk_size` | `concurrency` | chunks | wall clock | entities | relationships |
|---|---|---|---|---|---|
| 3,000 | 1 | 14 | 332.7s | 209 | 276 |
| 3,000 | 8 | 14 | 173.6s | 236 | 288 |
| **2,000** | **8** | 21 | **166.4s** | **329** | **384** |
| 1,500 | 8 | 28 | 260.6s | 504 | 497 |
| 40,000 | n/a (1 chunk) | 1 | 53.8s | 103 | 39 |

Reading it, against a **±12%** run-to-run noise floor measured from three
repeats of the baseline:

- **Concurrency was worth about 2×** (332.7s → 173.6s at the same chunk size).
  A serial run needs one slot, so that comparison survives the server's slot
  count changing between the two rows; a comparison between two *concurrent*
  rows would not.
- **2,000 was the best configuration**: tied with 3,000 on speed within noise,
  and richer.
- **1,500 was past the floor.** Naming variant pairs per entity rose from 0.38
  to 0.50, and it was *slower* — per-call latency had stopped falling while
  the extra chunks added another batch and half again as much consolidation
  work.
- **40,000 (one call, whole document) was the fastest and by far the worst.**
  103 entities joined by 39 relationships is a mostly disconnected graph.
  Entity count alone would have called this "a bit thinner" and been wrong,
  which is why relationship count is the number to watch.

**No accuracy figure on this page is attributable to a chunk size.** The
harness scores its graded corpus once per run at the default chunker, so
precision and recall cannot be compared across the rows above. The quality
column here is relationship count and variant pairs, both ungraded.

## Measuring your own

The repository's `bench/` harness does this end to end, and its configs are
worth reading as a worked example even if you re-implement the loop yourself —
`bench/probe-small.yaml` in particular states, before the run, what a *bad*
result would look like, which is what stops a surprising number from being
read as a good one.

The method is short:

1. Fix `concurrency` at your server's slot count, capped by
   `chunker.chunk(text).total_chunks`.
2. Time a full ingest at two or three chunk sizes — start at 2,000 and 4,000.
3. For each, record wall clock, entity count, **and relationship count**.
4. Take the smallest chunk size whose relationship-per-entity ratio has not
   started falling.
5. Repeat with a different `concurrency` only if step 1 was a guess.

Two things to be careful about, both of which produce confident wrong answers:

- **Run each configuration more than once.** Extraction against a real model
  is not deterministic even at temperature 0; run-to-run variation of ±12% in
  wall clock and ±15% in entity count is normal on the rig above. A single run
  per arm cannot distinguish a 10% improvement from weather.
- **Change one thing at a time, including on the server.** Restarting your
  inference server with a different slot count midway through invalidates
  every comparison across that point. This is not hypothetical — it happened
  while producing the table above, and it is why `bench/CONCURRENCY.md`
  declines to name an overall winner across two of its configurations.
