# What bounded concurrency bought, 2026-08-14

Read against `bench/BASELINE.md`, which measured the same document serially
the day before. Same machine, same model id (`muse-glimmer-30b` behind
llama-swap at `192.168.1.14:8080`), same document: the plain-text Wikipedia
article for *Harry Potter and the Philosopher's Stone*, 32,819 characters.
redstring 0.7.0 at `0ccf533`.

## Read the server configuration column before reading anything else

**The inference server's slot count changed three times during this session,
and it is not a property of redstring.** llama.cpp serves a fixed number of
concurrent requests; a client asking for more than that gets a queue, and a
queue is indistinguishable from a slow GPU if you only look at wall clock.
Every row below is tagged with the configuration it ran on, and **rows in
different configurations are not comparable to each other.**

| tag | what it was |
|---|---|
| `1-slot` | one request at a time — every concurrent call queued |
| `multi` | restarted with more than one slot; **the exact count was not recorded** |
| `8-slot` | restarted with 8 slots |

The `multi` rows are the 12,000-character series, and they are the reason
this document does not name a single overall winner. See "The comparison this
run cannot make" below.

## The headline

**At a fixed 8-slot server, concurrency is worth about 2× and smaller chunks
compound with it.** The best configuration measured is **2,000 characters at
K=8: 166.4s**, against the serial 3,000-character default's 332.7s — and it
finds *more* than the default did, not less.

| config | server | chunks | wall clock | entities | relationships | variant pairs |
|---|---|---|---|---|---|---|
| 3,000 × K=1 (baseline) | 1-slot | 14 | 332.7s | 209 | 276 | — |
| 3,000 × K=8 | 8-slot | 14 | 173.6s | 236 | 288 | 69 |
| **2,000 × K=8** | 8-slot | 21 | **166.4s** | **329** | **384** | 124 |
| 1,500 × K=8 | 8-slot | 28 | 260.6s | 504 | 497 | 250 |

## Concurrency scales with work available, not with K

The number that made every result here legible is **overlap** — summed
per-call time divided by wall clock. It says how many calls were genuinely in
flight on average, and unlike wall clock it does not move with GPU weather.

| config | server | wall | summed calls | overlap | per-call |
|---|---|---|---|---|---|
| 12,000 × K=2 | 1-slot | 138.1s | 222.9s | 1.61× | 55.7s |
| 12,000 × K=4 | 1-slot | 155.5s | 321.5s | 2.07× | 80.4s |
| 12,000 × K=2 | multi | 153.2s | 259.8s | 1.70× | 64.9s |
| 12,000 × K=4 | multi | 116.9s | 345.2s | 2.95× | 86.3s |
| 12,000 × K=8 | multi | 129.3s | 383.6s | 2.97× | 95.9s |
| 3,000 × K=8 | 8-slot | 173.6s | 861.2s | 4.96× | 61.5s |
| 2,000 × K=8 | 8-slot | 166.4s | 1001.5s | **6.02×** | 47.7s |
| 1,500 × K=8 | 8-slot | 260.6s | 1452.7s | 5.57× | 51.9s |

Two things fall out, and the second is the actionable one.

**Overlap never reaches K, and the shortfall is the server's.** At 4× requests
the best overlap measured was 2.95×; at 8× it was 6.02×. Roughly 62–75%
efficiency, spent on per-call latency: one call over a 12,000-character chunk
takes 38.4s alone and 86.3s with three siblings. This is a shared GPU, not
four independent workers, and no library change moves it.

**Overlap is capped by chunk count, not by K.** `12,000 × K=8` and
`12,000 × K=4` produced 2.97× and 2.95× — because a 32,819-character document
splits into **4 chunks** at that size, so a ceiling of 8 still issues a single
`gather` of 4. They are the same workload. Their 12.4s wall-clock difference
is therefore a direct measurement of this rig's noise floor on identical work:
**about 10%**, measured rather than assumed, and every margin in this document
should be read against it.

That is the general rule, and it is arithmetic rather than a finding:

    effective concurrency = min(K, chunks remaining in the batch)
    chunks               ≈ len(document) / chunk_size

**Raising K past a document's chunk count does nothing at all.** A caller who
wants more concurrency out of a fixed document has to make chunks smaller,
which is why the two knobs cannot be tuned independently.

## Smaller chunks pay twice, until they stop

Below the 3,000-character default — which nothing had ever measured below,
and which was a floor only because it was the smallest value in the original
sweep config — quality and speed improve together for one step and then part
company.

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
entity go 0.29 → 0.38 → 0.50, so at 1,500 characters roughly half of the
"entities" are a naming variant of another entity. `extraction.mapping`
derives identity from the name, so `harry` in one window and `harry potter`
in the next are two entities, and 28 chunks give drift 27 boundaries to
happen at. Carryover carries names forward; it does not carry the clause that
named them.

1,500 is also **57% slower** than 2,000 despite having more chunks — per-call
latency barely fell (47.7s → 51.9s) while 28 chunks pushed the run from three
batches to four, over twice as many entities to consolidate.

This is the first result the drift metric decided. It is worth recording that
an hour earlier the same number was uninterpretable, because no configuration
had a comparable column to read it against.

## Fewer, larger calls lose badly — and it is not a context limit

The model serves 131k tokens of context and the whole document is ~8,200
tokens, so context was never the binding constraint at any chunk size this
harness has run. Asking for the whole document in one call is therefore
possible, fast, and by a wide margin the worst result measured.

| chunk | chunks | wall | entities | relationships |
|---|---|---|---|---|
| 2,000 | 21 | 166.4s | 329 | 384 |
| 24,000 | 3 | 126.7s | 163 | 121 |
| **40,000 (whole document)** | **1** | **53.8s** | **103** | **39** |

**3.2× faster than the best configuration, for 31% of the entities and 10% of
the relationships.** Relationships collapse faster than entities do, which is
the sharper signal: 103 nodes joined by 39 edges is mostly disconnected —
a list, not a graph. Entity count alone would have called this "somewhat
thinner" and been badly wrong, which is the same warning `BASELINE.md` gives
about entity count and the reason it should gate nothing.

The 16,384-token output ceiling may also bind on a single whole-document call,
and a truncated response arrives as a shorter answer rather than an error —
so part of that drop may be truncation. It cannot explain a 7× relationship
loss on its own.

**Chunking is doing real work, not working around a context window.**

## The comparison this run cannot make

**`12,000 × K=4` at 116.9s is the fastest non-degenerate number here, and it
is not comparable to `2,000 × K=8` at 166.4s.** They ran on different server
configurations — `multi` and `8-slot` — and the slot count for `multi` was
never recorded. A 40s gap across an unknown change in server parallelism is
not a finding.

Within the 8-slot configuration, where every row *is* comparable, 2,000 × K=8
wins. That is the recommendation below, and it is the one the data supports.
Re-running the 12,000 series at 8 slots is `B-BENCH-8`.

Three further limits, stated rather than buried:

- **One repeat per point**, against a measured ~10% noise floor. The
  166.4/173.6 gap between 2,000 and 3,000 does *not* survive that; the
  166.4/260.6 gap to 1,500 does. Read 2,000 and 3,000 as tied on speed and
  separated on yield.
- **Accuracy was never graded below 3,000 characters.** The five-document
  graded corpus ran at 3,000/8,000/12,000 only (precision 0.71 entities /
  0.46 relationships at 3,000). 2,000's quality claim rests on relationship
  count and variant pairs, not on a graded score. That is `B-BENCH-9`.
- **`variant_pairs` is a lower bound.** It counts token-subset pairs, so it
  sees `harry` beside `harry potter` and misses `mum` beside `mom`. Real
  drift is higher than every number in this document.

## Recommended default

**2,000 characters at K=8, for a server with 8 slots**, with K set to the
server's slot count rather than to 8 as such.

- It is the fastest configuration measured on the 8-slot server, and it is
  simultaneously the richest that stays on the right side of the drift floor —
  329 entities and 384 relationships against the serial default's 209 and 276,
  in half the wall clock.
- Against the original serial 3,000 default, that is **2.0× faster and ~1.4×
  more extracted**, which is the outcome worth having: it makes the fine-grained
  configuration affordable rather than making the coarse one marginally cheaper.
- `concurrency=1` remains the library default and reproduces the serial
  pipeline exactly. Nothing here changes what redstring does out of the box;
  it changes what a caller can ask for.

**Does `12,000 × K` beat `12,000 × 1`?** Yes, on the `multi` server: 116.9s
against a 139.5–177.5s serial spread, and the overlap column shows why. But
12,000 is not the chunk size to run either way — it scored the worst graded
precision (0.46 relationships) of any size measured, it caps concurrency at 4
chunks, and 2,000 beats it on every quality number available.

`failed_chunks` was 0 and `timed_out` empty on every run in this document, so
none of it is a partial result.

## What still cannot be measured

`time_to_first_entity_s`, `event_gaps_s` and perceived event rate are `null`
or empty here exactly as they were in `BASELINE.md`, and for the same reason:
`build_graph` is one opaque await. Concurrency changed how long the wait is,
not whether a caller can see inside it. That is deliverable B.
