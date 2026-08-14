# Tune ingestion throughput

`build_graph` takes two knobs that decide how long a document takes to
ingest — `chunk_size` on the chunker and `concurrency` on the call itself.
They interact, so tuning either alone gives the wrong answer.

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
chunks          ≈ len(document.text) / chunk_size
```

A 33,000-character document at `chunk_size=12000` is **4 chunks**. Setting
`concurrency=8` issues one batch of 4 — exactly what `concurrency=4` does. The
two are the same run, and measuring them will show you a difference that is
entirely noise.

**If raising `concurrency` did nothing, you are probably out of chunks.** Get
more concurrency by making chunks *smaller*, not by raising the ceiling.

```python
from redstring import SlidingWindowChunker, build_graph

report = await build_graph(
    document,
    provider=provider,
    store=store,
    tenant_id=tenant_id,
    chunker=SlidingWindowChunker(chunk_size=2000),
    concurrency=8,
)
```

Set `concurrency` to **your inference server's concurrent-request capacity**,
not to an arbitrary number. Asking for more than the server serves does not
fail — it queues, and a queue looks exactly like a slow GPU from the client
side. The next section is how to tell those apart.

## Is it your server or your settings? Measure overlap

The one diagnostic worth computing is **overlap**: total time spent inside
provider calls, divided by wall clock. It says how many calls were genuinely
in flight on average.

```text
overlap = (sum of per-call durations) / wall clock
```

- **overlap ≈ 1.0** with `concurrency > 1` — nothing is running in parallel.
  Either your server is configured for one request at a time, or you are out
  of chunks (see the arithmetic above).
- **overlap ≈ concurrency** — ideal, and you will not see it.
- **overlap between 60% and 75% of `concurrency`** — normal. The calls do
  overlap, but they share one GPU, so each one slows down as siblings join it.

This is worth computing because the first case is *common* and is invisible in
wall clock alone. A run against a single-slot server and a run against a
saturated GPU both look like "concurrency didn't help".

Today you have to time the calls yourself, by wrapping your `LlmProvider`:

```python
class TimingProvider:
    def __init__(self, inner):
        self._inner, self.total_s = inner, 0.0

    async def extract(self, text, schema, *, system_prompt=None):
        start = time.monotonic()
        try:
            return await self._inner.extract(text, schema, system_prompt=system_prompt)
        finally:
            self.total_s += time.monotonic() - start
```

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

`muse-glimmer-30b` behind llama-swap, 8 concurrent slots, one 32,819-character
Wikipedia article. Full detail, including what could not be measured, is in
`bench/CONCURRENCY.md`; the baseline it is read against is `bench/BASELINE.md`.

| `chunk_size` | `concurrency` | chunks | wall clock | entities | relationships |
|---|---|---|---|---|---|
| 3,000 | 1 | 14 | 332.7s | 209 | 276 |
| 3,000 | 8 | 14 | 173.6s | 236 | 288 |
| **2,000** | **8** | 21 | **166.4s** | **329** | **384** |
| 1,500 | 8 | 28 | 260.6s | 504 | 497 |
| 40,000 | 8 | 1 | 53.8s | 103 | 39 |

Reading it:

- **Concurrency was worth about 2×** (332.7s → 173.6s at the same chunk size).
- **2,000 was the best configuration**: fastest measured, and richer than the
  larger sizes.
- **1,500 was past the floor.** Naming variants per entity rose from 0.38 to
  0.50 — roughly half its "entities" were variants of another — and it was
  *slower*, since per-call latency had stopped falling while the extra chunks
  added another batch and twice the consolidation work.
- **40,000 (one call, whole document) was the fastest and by far the worst.**
  103 entities joined by 39 relationships is a mostly disconnected graph.
  Entity count alone would have called this "a bit thinner" and been wrong,
  which is why relationship count is the number to watch.

## Measuring your own

The repository's `bench/` harness does this end to end, and its configs are
worth reading as a worked example even if you re-implement the loop yourself —
`bench/probe-small.yaml` in particular states, before the run, what a *bad*
result would look like, which is what stops a surprising number from being
read as a good one.

The method is short:

1. Fix `concurrency` at your server's slot count.
2. Time a full ingest at two or three chunk sizes — start at 2,000 and 4,000.
3. For each, record wall clock, entity count, **and relationship count**.
4. Take the smallest chunk size whose relationship-per-entity ratio has not
   started falling.
5. Repeat with a different `concurrency` only if step 1 was a guess.

Two things to be careful about, both of which produce confident wrong answers:

- **Run each configuration more than once.** Extraction against a real model
  is not deterministic even at temperature 0; run-to-run variation of ±10% in
  wall clock and ±15% in entity count is normal. A single run per arm cannot
  distinguish a 10% improvement from weather.
- **Change one thing at a time, including on the server.** Restarting your
  inference server with a different slot count midway through invalidates
  every comparison across that point. This is not hypothetical — it happened
  while producing the table above, and it is why `bench/CONCURRENCY.md`
  declines to name an overall winner across two of its configurations.
