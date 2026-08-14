# What bounded concurrency bought, 2026-08-14

Read against `bench/BASELINE.md`, which measured the same document serially
the day before. Same machine, same model id (`muse-glimmer-30b` behind
llama-swap at `192.168.1.14:8080`), same document: the plain-text Wikipedia
article for *Harry Potter and the Philosopher's Stone*, 32,791 characters.
redstring 0.7.0 at `0ccf533`.

## Read the server configuration column before reading anything else

**The inference server's slot count changed three times during this session,
and it is not a property of redstring.** llama.cpp serves a fixed number of
concurrent requests; a client asking for more than that gets a queue. Every
row below is tagged with the configuration it ran on, and **rows in different
configurations are not comparable on wall clock.**

| tag | what it was |
|---|---|
| `1-slot` | one request at a time — every concurrent call queued |
| `multi` | restarted with more than one slot; **the exact count was not recorded** |
| `8-slot` | restarted with 8 slots |

**The one exception, stated rather than assumed: a `concurrency=1` row is
comparable across all three.** A serial run issues one call at a time and
needs exactly one slot, so raising the server's slot count cannot change it.
That is what licenses the headline's comparison of a `1-slot` serial baseline
against `8-slot` concurrent runs. No such defence exists for comparing two
*concurrent* rows across configurations, which is why the 12,000 series is
excluded from the recommendation — see "The comparison this run cannot make".

## The headline

**At a fixed 8-slot server, concurrency is worth about 2× and smaller chunks
compound with it.** The best configuration measured is **2,000 characters at
K=8: 166.4s**, against the serial 3,000-character default's 332.7s — and it
finds *more* than the default did, not less.

| config | server | chunks | wall clock | entities | relationships | variant pairs |
|---|---|---|---|---|---|---|
| 3,000 × K=1 (baseline) † | 1-slot | 14 | 332.7s | 209 | 276 | — |
| 3,000 × K=8 | 8-slot | 14 | 173.6s | 236 | 288 | 69 |
| **2,000 × K=8** | 8-slot | 21 | **166.4s** | **329** | **384** | 124 |
| 1,500 × K=8 | 8-slot | 28 | 260.6s | 504 | 497 | 250 |

† The baseline row is three repeats, following `BASELINE.md`'s convention:
**median** wall clock (of 328.5/350.0/332.7; the mean is 337.0s) and **mean**
entity and relationship counts. Every other row in this document is a single
run. The 2.0× claim below holds against either the median or the mean.

## Concurrency scales with work available, not with K

The number that made every result here legible is **overlap** — summed
per-call time divided by wall clock.

| config | server | wall | summed calls | overlap | per-call |
|---|---|---|---|---|---|
| 12,000 × K=2 | 1-slot | 138.1s | 222.9s | 1.61× | 55.7s |
| 12,000 × K=4 | 1-slot | 155.5s | 321.5s | 2.07× | 80.4s |
| 12,000 × K=2 | multi | 153.2s | 259.8s | 1.70× | 64.9s |
| 12,000 × K=4 | multi | 116.9s | 345.2s | 2.95× | 86.3s |
| 12,000 × K=8 | multi | 129.3s | 383.6s | 2.97× | 95.9s |
| 3,000 × K=8 | 8-slot | 173.6s | 861.2s | 4.96× | 61.5s |
| 2,000 × K=8 | 8-slot | 166.4s | 1002.3s | **6.02×** | 47.7s |
| 1,500 × K=8 | 8-slot | 260.6s | 1452.9s | 5.57× | 51.9s |

### What overlap does *not* measure, learned from these rows

An earlier draft of this document described overlap as "how many calls were
genuinely in flight". **That is wrong, and this table disproves it.** The two
`1-slot` rows — a server serving exactly one request at a time — score 1.61×
and 2.07×. Nothing was in flight concurrently; the calls were *queued*.
`extract_s` is measured client-side around each `await`, so queue time is
counted as call time, and overlap cannot tell a queue from parallelism.

Sharper still: `12,000 × K=2` scored 1.61× on `1-slot` and 1.70× on `multi`.
**Overlap did not detect the slot count changing.** It is a measure of
*client-side call time compression*, useful for spotting that the wavefront is
issuing calls at all, and useless for diagnosing the server. What actually
identified the single-slot server was the wall clock failing to improve while
per-call latency rose almost exactly in proportion — 38.4s serial to 80.4s at
K=4, i.e. four calls taking four times as long each.

Two things still fall out of the table, and the second is the actionable one.

**Overlap never reaches K.** At 4× requests the best was 2.95×; at 8×, 6.02×.
Some of that shortfall is real GPU sharing and some is queueing; these runs
cannot separate the two.

**Overlap is capped by chunk count, not by K.** `12,000 × K=8` and
`12,000 × K=4` produced 2.97× and 2.95× — because a 32,791-character document
splits into **4 chunks** at that size, so a ceiling of 8 still issues a single
`gather` of 4. They are the same workload, and their 12.4s wall-clock
difference is a lower-bound noise estimate on identical work.

That is the general rule, and it is arithmetic rather than a finding:

    calls in flight = min(K, chunks remaining in the batch)

**Raising K past a document's chunk count does nothing at all.** A caller
wanting more concurrency from a fixed document has to make chunks smaller,
which is why the two knobs cannot be tuned independently. Note that chunk
count is **not** `len(text) / chunk_size` — see "Do not estimate chunk count"
below.

### How much noise to read every margin against

Use **±12%**, not the 10% the single identical-workload pair above suggests.
`BASELINE.md` ran three repeats per point and paid for a better estimate: at
12,000 characters those were **139.5 / 153.5 / 177.5s** — 27% peak-to-peak,
±12% about the mean. Against that figure:

- 2,000 (166.4s) and 3,000 (173.6s) are **tied on speed**, and separated only
  on what they extract.
- 1,500's 260.6s is **outside** it — a real regression.
- The 2× concurrency gain is far outside it.

## Do not estimate chunk count from document length

An earlier draft of this document, and of `build_graph`'s docstring, said
chunks ≈ `len(text) / chunk_size`. **That understates by 28–47% at every size
run here, and by 119% at 24,000**, because the chunker advances by
`chunk_size - overlap` and backs each break up to a paragraph, sentence or
word boundary:

| `chunk_size` | `len / chunk_size` | actual |
|---|---|---|
| 1,500 | 21.9 | 28 |
| 2,000 | 16.4 | 21 |
| 3,000 | 10.9 | 14 |
| 8,000 | 4.1 | 6 |
| 12,000 | 2.7 | 4 |
| 24,000 | 1.4 | 3 |

The error runs in the harmful direction for the advice above: a caller
applying the formula at `chunk_size=12000` concludes 2 or 3 chunks and caps K
there, when 4 are issued. **Ask the chunker instead** —
`chunker.chunk(text).total_chunks` is exact, free, and public.

## Smaller chunks pay twice, until they stop

Below the 3,000-character default — which nothing had ever measured below, and
which was a floor only because it was the smallest value in the original sweep
config — quality and speed improve together for one step and then part company.

| chunk | chunks | wall | entities | rels | variant pairs | rel/entity | **vp/entity** |
|---|---|---|---|---|---|---|---|
| 3,000 | 14 | 173.6s | 236 | 288 | 69 | 1.22 | 0.29 |
| 2,000 | 21 | 166.4s | 329 | 384 | 124 | 1.17 | 0.38 |
| 1,500 | 28 | 260.6s | 504 | 497 | 250 | 0.99 | **0.50** |

**1,500 is past the floor, and the failure mode was not the predicted one.**
`bench/probe-small.yaml` was written before the run with an explicit
prediction: a recall cliff would show as entity count rising while
relationships collapsed. That prediction was wrong. Relationships rose the
whole way down. What degrades instead is **identity**: variant pairs per
entity go 0.29 → 0.38 → 0.50. `extraction.mapping` derives identity from the
name, so `harry` in one window and `harry potter` in the next are two
entities, and 28 chunks give drift 27 boundaries to happen at. Carryover
carries names forward; it does not carry the clause that named them.

Read that as a *ratio*, not as a percentage of entities. 0.50 variant pairs
per entity does not mean half the entities are variants — one name in three
subset relationships contributes three pairs. The direction is the finding;
the metric cannot support a share-of-entities claim.

1,500 is also **57% slower** than 2,000 despite having more chunks. Per-call
latency did not fall — it rose slightly, 47.7s → 51.9s — while 28 chunks
pushed the run from three batches to four, over **half again** as many
entities to consolidate (504 against 329).

This is the first result the drift metric decided. An hour earlier the same
number was uninterpretable, because no configuration had a comparable column
to read it against.

## Fewer, larger calls lose badly — and it is not a context limit

The model serves 131k tokens of context and the whole document is ~8,200
tokens, so context was never the binding constraint at any chunk size this
harness has run.

| chunk | K | chunks | wall | entities | relationships |
|---|---|---|---|---|---|
| 2,000 | 8 | 21 | 166.4s | 329 | 384 |
| 24,000 | 2 | 3 | 126.7s | 163 | 121 |
| **40,000** | n/a (1 chunk) | **1** | **53.8s** | **103** | **39** |

**3.1× faster than the best configuration, for 31% of the entities and 10% of
the relationships.** Relationships collapse ~10× while entities fall ~3×,
which is the sharper signal: 103 nodes joined by 39 edges is mostly
disconnected — a list, not a graph. Entity count alone would have called this
"somewhat thinner" and been badly wrong, which is why it should gate nothing.

The 24,000 row ran at K=2 rather than K=8, so it is not comparable on wall
clock with the rows above it; it is here for its quality columns, which is
what the section is about.

The 16,384-token output ceiling may also bind on a single whole-document call,
and a truncated response arrives as a shorter answer rather than an error — so
part of that drop may be truncation. It cannot explain a 10× relationship loss
on its own. **Chunking is doing real work, not working around a context
window.**

## The comparison this run cannot make

**`12,000 × K=4` at 116.9s is the fastest non-degenerate number here, and it
is not comparable to `2,000 × K=8` at 166.4s.** They ran on different server
configurations — `multi` and `8-slot` — and the slot count for `multi` was
never recorded. A 40s gap across an unknown change in server parallelism is
not a finding. Within the 8-slot configuration, where every row *is*
comparable, 2,000 × K=8 wins. Re-running the 12,000 series at 8 slots is
`B-BENCH-8`.

Two further limits, stated rather than buried:

- **One repeat per point** outside the baseline, against a ±12% noise floor.
- **No accuracy measurement here is attributable to a chunk size.** The
  harness runs the graded corpus **once per invocation**, at `build_graph`'s
  default chunker, after the sweep — `run_accuracy` takes no chunk size at
  all. `BASELINE.md`'s precision figures (0.71 entities, 0.46 relationships)
  are therefore one run's entity and relationship precision, **not** a
  comparison between chunk sizes, and an earlier draft of this document
  misread them as one. Every quality claim below 3,000 characters rests on
  relationship count and variant pairs, both ungraded. That is `B-BENCH-9`.
- **`variant_pairs` is a lower bound.** It counts token-subset pairs, so it
  sees `harry` beside `harry potter` and misses `mum` beside `mom`.

## Recommended default

**2,000 characters at a concurrency matching the server's slot count**, which
here was 8.

- It is the fastest configuration measured on the 8-slot server — tied with
  3,000 within noise — and simultaneously the richest that stays on the right
  side of the drift floor: 329 entities and 384 relationships against the
  serial default's 209 and 276, in half the wall clock.
- Against the serial 3,000 default that is **2.0× faster and ~1.4× more
  extracted**, which is the outcome worth having: it makes the fine-grained
  configuration affordable rather than making the coarse one marginally
  cheaper.
- `concurrency=1` remains the library default and reproduces the serial
  pipeline exactly.

**Does `12,000 × K` beat `12,000 × 1`?** Yes, on the `multi` server: 116.9s
against a 139.5–177.5s serial spread. But 12,000 is not the chunk size to run
either way — it caps concurrency at 4 chunks, and 2,000 beats it on every
quality number available here.

`failed_chunks` was 0 and `timed_out` empty on every run in this document.

## What still cannot be measured

`time_to_first_entity_s`, `event_gaps_s` and perceived event rate are `null`
or empty here exactly as they were in `BASELINE.md`: `build_graph` is one
opaque await. Concurrency changed how long the wait is, not whether a caller
can see inside it. That is deliverable B.
