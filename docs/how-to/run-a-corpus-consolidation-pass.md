# Run a corpus consolidation pass

`ConsolidationService.resolve_many` consolidates a whole corpus of subjects in
one call, instead of looping over `resolve` yourself. This page covers when to
reach for it, how `concurrency` behaves across the two phases it bounds, and
the one thing it does not do that a caller has to plan for: converge.

See `docs/adr/0041-the-consolidation-pass-is-decide-then-emit.md` for the
design this page assumes.

## When to run a pass

`resolve` handles one subject and is the right call when you already know
which entity just changed — for example, right after extracting a document
and wanting to fold its new entities into the graph before moving on.

`resolve_many` is for the case where you have a batch of candidate subjects
and no particular reason to process them one at a time: a scheduled sweep over
entities touched since the last pass, a backfill after lowering
`LOW_SIMILARITY`, or cleanup after a bulk import. It is not a substitute for
calling `resolve` from inside your ingestion path — the two solve different
problems, and `resolve_many`'s barrier phase (below) makes it a worse fit for
"consolidate this one entity right now" than `resolve` is.

```python
from redstring.consolidation.service import ConsolidationService

events = await service.resolve_many(
    subjects,
    finder=finder,
    adjudicator=adjudicator,
    concurrency=8,
)
```

`events` is the list of `EntitiesMerged` events actually emitted, in emit
order — shorter than `subjects` whenever a subject decided nothing or was
found stale before it emitted.

## `concurrency` bounds two different things

Unlike `build_graph`'s `concurrency`, which bounds one wavefront of chunk
calls, `resolve_many`'s `concurrency` only bounds phase 1 — phase 2 makes a
single call, not a wavefront of them:

- **Phase 1 (score and band):** how many subjects are scored against the
  store at once, in wavefronts of `concurrency`. This phase makes no model
  calls, so it is bounding connection-pool usage, not endpoint load.
- **Phase 2 (adjudicate):** one call to `adjudicator.adjudicate_many`, over
  the whole cross-subject batch, held under the `CallLimiter` built from
  `concurrency` (or passed explicitly — see below) for that call's entire
  duration. `concurrency` does not multiply how many adjudication requests
  are in flight; with the shipped `Adjudicator`, which awaits its own
  batches serially, exactly one model call is in flight at a time regardless
  of `concurrency`. The limiter only does work when it is shared across
  concurrent callers — see the next section — or when a different
  `MergeAdjudicator` fans its own batches out concurrently, in which case it
  is the only thing bounding them.

So `concurrency` governs phase 1's wavefront size and, indirectly, the
`CallLimiter`'s capacity if you let `resolve_many` build one for you — not
"how many model calls this pass makes at once."

**Raising `concurrency` past the number of subjects does nothing.** A
wavefront of size `concurrency` over fewer than `concurrency` subjects simply
processes all of them in one batch — the same arithmetic
`docs/how-to/tune-ingestion-throughput.md` documents for chunks:

```text
subjects in flight = min(concurrency, subjects remaining in the batch)
```

Consolidating ten subjects with `concurrency=50` runs identically to
`concurrency=10`. If raising `concurrency` did not shorten your run, check the
subject count before suspecting the server.

## Sharing one `CallLimiter` across callers

`resolve_many` builds its own `CallLimiter(concurrency)` when you do not pass
one. That is fine when a pass runs alone, but the ceiling only means what it
says when everything hitting one backend shares it — two independent
`CallLimiter` instances can together admit more callers than either was meant
to allow.

If you are running `resolve_many` against the same endpoint an extraction
pipeline is also using, construct one `CallLimiter` and pass it to both:

```python
from redstring import CallLimiter

limiter = CallLimiter(slots)

report = await build_graph(
    document,
    provider=provider,
    store=store,
    tenant_id=tenant_id,
    concurrency=slots,
    limiter=limiter,
)

events = await service.resolve_many(
    subjects, finder=finder, adjudicator=adjudicator, concurrency=slots, limiter=limiter
)
```

`CallLimiter` lives in `redstring.domain` precisely so both siblings can
import it without one importing the other — see ADR 0041.

## A pass is not a fixed point — plan for a second run

`resolve_many` scores every subject once, against the graph as it stood when
phase 1 read it. A subject found stale before it emits — because an earlier
subject in the same pass already absorbed it — is **skipped, not retried**.
That is deliberate: retrying would mean re-scoring against a graph the same
pass is still changing, which has no natural stopping point.

The consequence for callers: a chain of duplicates that this pass did not
fully resolve in one call — A and B merge, and the newly-merged entity turns
out to also duplicate C, which wasn't rescored after A+B's merge — is left for
the *next* call to `resolve_many` to find, the same way it would be left for
the next scheduled sweep. If your workload can produce chains like that (bulk
imports of near-duplicate data are the common case), run the pass, and run it
again against subjects touched by the events it emitted, until a pass returns
no events for those subjects.

## What raising `concurrency` trades away

Nothing, unlike chunk size in extraction. Consolidation subjects have no
notion of carryover between them, so there is no naming-drift cost to
widening the wavefront — `concurrency` here is purely a throughput knob,
bounded from above by subject count and from below by whatever your endpoint
can actually serve concurrently.
