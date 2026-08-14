# ADR 0039: Bounded concurrency over chunks

**Status:** accepted. Amended by [`0041` the consolidation pass is
decide-then-emit](0041-the-consolidation-pass-is-decide-then-emit.md), which
moves `CallLimiter` from `extraction` to `domain` so consolidation can share
one ceiling with the pipeline. The Decision below stands unchanged.

**Decision:** `ExtractionPipeline` and `build_graph` take a `concurrency`
parameter (default `1`). Chunks are extracted in consecutive wavefront
batches of that size — every chunk in a batch fires with the same prompt,
computed once before the batch runs, and the batch is awaited together.
Carryover accumulates *between* batches, from each batch's results in chunk
order, never as an individual call returns. A single `CallLimiter` is passed
alongside `concurrency` and admits at most that many callers at once against
every call the pipeline makes to the endpoint — gleaning's extra call per
chunk and `build_graph`'s embedding call included, not only the chunk
extraction loop.

## Context

The module docstring for `src/redstring/extraction/pipeline.py` had already
argued, before this decision, that the pipeline should not fire chunks
concurrently: the reference deployment is a single-GPU llama.cpp server, and
firing ten requests at once "converts a queue into ten timeouts". That
argument was never wrong. It was an argument for a *bound* on how many calls
may be in flight at once, and until now it had been implemented as the
tightest possible bound — the constant `1`, wired into a plain sequential
loop with no parameter naming it as a choice.

`bench/BASELINE.md` turned the argument into a number: a 32,790-character
document, chunked at the library's default size, is 14 strictly serial calls
at roughly 24 seconds each — on the order of six minutes for one document,
entirely a function of how many calls the backend can be asked to do at once,
not of anything the model itself is slow at per call. That is the forcing
measurement behind this ADR; the reasoning behind the bound was already
correct, and what changed is that "one" stopped being the only value anyone
could argue for.

### Why carryover survives concurrency, and why this is a wavefront rather than a `gather`

Entity identity in this library is derived from the name —
`extraction.mapping.entity_id_for` hashes `(tenant, source, entity type,
normalized name)` — so a chunk that spells a name differently from an earlier
chunk does not get deduplicated by the fold; it manufactures a second entity,
which `consolidation` then pays a model call to resolve. The pipeline's
carryover mechanism exists to suppress exactly that: each chunk after the
first is told, in its system prompt, a bounded list of `(name, entity_type)`
pairs the chunks before it found, so a later mention has something to spell
consistently against.

Firing every chunk in the document at once would delete that signal
entirely — every chunk would be blind to every other, and the naming drift
carryover exists to prevent would recur at the rate a fully concurrent
extraction produces boundaries, which is every chunk boundary in the
document rather than only the ones inside one wavefront. A batch of size
`concurrency` bounds how much of the document a chunk is blind to: it sees
carryover from every batch before it, and only chunks in its own batch are
invisible to each other. That is why the pipeline groups chunks into
wavefronts and folds carryover in *between* them rather than reaching for
`asyncio.gather` over the whole chunk list — the two are the same amount of
code and very different amounts of naming drift.

### What made it safe

Nothing about extraction's correctness depends on the order chunks are
processed in, provided the merge that combines their results is
order-independent — and that was already a decision this project had made
and documented, not a new one made for this change.
[`0010` one total order for preference](0010-one-total-order-for-preference.md)
establishes that `domain.preference` is a total order over one id bucket, and
that totality is what makes `merge_extractions` insensitive to the sequence
in which chunk results arrive. Before this change that property was
asserted as a fact about a serial pipeline where the sequence never varied
in practice; concurrency makes it load-bearing in a way it was not before,
because a bounded wavefront genuinely can complete its members in more than
one order depending on which one the backend answers first. What backs the
decision is not new reasoning but an old one now being exercised: the
property is asserted directly by
`tests/unit/extraction/test_merging.py::test_the_fold_does_not_depend_on_the_order_of_its_parts`,
which generates chunk results and checks the fold agrees across permutations
of the order they are combined in — the same shape as the two totality
properties ADR 0010 already required of `preference` and
`relationship_preference`, aimed at the fold that consumes them rather than
at the order itself.

### The ceiling is on calls in flight, not on batch size

`concurrency` sets the batch size for chunk extraction. It is not the whole
story of what may be talking to the endpoint at once: gleaning
(`redstring.extraction.gleaning`) fires a second call per chunk when enabled,
and `build_graph` runs an embedding call after an extraction it does not
own. A batch-size parameter alone would leave those two paths unbounded,
which defeats the argument that motivated the bound in the first place — the
backend's queue depth does not care which code path issued the call. So the
bound is expressed as a `CallLimiter`, a single object constructed from
`concurrency` and threaded through every call site that reaches the
endpoint, admitting at most `concurrency` callers through it at once
regardless of which of those call sites they came from.

## Consequences

**`concurrency=1` is byte-identical to the pipeline before this parameter
existed.** A batch of size one is the same calls, the same prompts, in the
same order, so every existing caller is unaffected and the default did not
move.

**A caller raising `concurrency` trades naming stability for wall clock —
and the measurement says the trade is not the one that binds.** Widening the
wavefront shortens the run at the cost of more chunks being mutually blind to
each other's carryover within a batch. `bench/CONCURRENCY.md` now records the
sweep, and it does not show that: naming drift did not track `concurrency` in
any readable way. The drift that *is* measurable tracks **chunk size**
instead, rising as chunks shrink — because a smaller chunk means more
boundaries for a name to drift across, not because a wider batch does. The
measurements are in that file rather than here, per
`.claude/rules/recurring-defects.md` §5.

The consequence that replaced it is arithmetic, and callers hit it first:
**effective concurrency is `min(K, chunks in the batch)`**, so raising `K`
past a document's chunk count does nothing at all. The two knobs cannot be
tuned independently, which `docs/how-to/tune-ingestion-throughput.md` covers
for callers.

**The ceiling composes with a backend serving other tenants.** Because the
bound is calls in flight rather than a property of one pipeline run, a
caller who knows their endpoint's real capacity can set `concurrency` to
share it correctly across concurrent callers of the library, rather than
each caller needing to reason about every other caller's batch size.

**`CallLimiter` widens the public surface by one name**, per ADR 0006's gate:
`ExtractionPipeline.__init__` takes `limiter: CallLimiter | None`, so the type
had to be exported or the signature would reference something a caller cannot
construct. Exporting it is also the thing that makes the previous paragraph
possible for a caller to act on directly — sharing one ceiling across several
`build_graph` calls against one backend needs the type in hand, not only the
parameter name.

## What this does not do

No per-chunk progress reporting — the pipeline still returns from one
opaque `await`, and giving a caller visibility into a run in flight is a
separate deliverable. No concurrency across documents — the bound is scoped
to one pipeline's calls, and running multiple documents through one
`CallLimiter` is a composition question left to the caller. No adaptive
tuning of the bound — `concurrency` is a number the caller sets and the
pipeline does not infer or adjust it from observed latency or failures.
