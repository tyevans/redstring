# ADR 0041: The consolidation pass is decide-then-emit

**Status:** accepted.

**Why this is an ADR:** it adds a corpus-level entry point to
`ConsolidationService`, widens `MergeAdjudicator`'s required surface, and
moves `CallLimiter` to a shared layer. Each of those is independently
architecturally significant under `.claude/rules/definition-of-done.md`; they
are one ADR because the three decisions were made together and none is fully
motivated without the others.

## Context

`ConsolidationService.resolve` handles one subject: read, score, band,
adjudicate, emit. A caller with a corpus of subjects to consolidate has to
call it in a loop, which is correct but serial — the loop's only concurrency
opportunity is however many subjects the caller chooses to fire at once, and
nothing in the service helps them reason about doing that safely.

[`0039` bounded concurrency over chunks](0039-bounded-concurrency-over-chunks.md)
already solved a version of this problem for extraction, and the instinct is
to copy its shape directly: wavefronts of `concurrency`, a shared
`CallLimiter`. That shape does not transfer whole, because chunks and
consolidation subjects differ in the one property the wavefront pattern
depends on.

**The fan-out cannot be over `resolve`.** Two things forbid firing `resolve`
concurrently over a subject list, and neither is incidental:

1. **Each merge changes the graph the next subject reads.** Candidate finding
   resolves through aliases, so a merge emitted for one subject changes what
   candidate a later subject blocks against. Chunks carried no such
   dependency on each other's *results* — 0039's wavefronts exist to bound
   calls in flight and preserve carryover ordering, not because one chunk's
   extraction could invalidate another's.
2. **`ConsolidationLog` uses optimistic concurrency on the tenant stream.**
   Two concurrent merges within one tenant collide by construction — the
   stream is the tenant, deliberately, so nothing about widening a batch size
   changes that.

Chunks were genuinely independent of each other once carryover was folded in
between wavefronts; consolidation subjects are not independent at all, because
resolving one can retarget what another subject means.

## Decision

`ConsolidationService.resolve_many` consolidates a whole corpus in one call as
three phases, split exactly where the two constraints above bite:

**Phase 1, concurrent — score and band.** Every subject is scored against the
graph in wavefronts of `concurrency`. This phase reads the store only; it
makes no model calls, so what `concurrency` bounds here is the adapters'
connection pools, not the endpoint.

**Phase 2, a barrier — adjudicate.** The scored subjects are adjudicated in
batches that span subject boundaries, behind a `CallLimiter`. This is the
only phase that talks to the model endpoint. The barrier is real: no emit
starts until every subject has been scored. It is accepted rather than
engineered around, because the alternative — filling an adjudication batch
only from subjects that happen to have finished scoring — is exactly the
per-subject batching this phase exists to replace with something better.

**Phase 3, serial — emit.** Subjects emit one at a time, in an order derived
from `str(subject.id)` rather than from the order the caller supplied or the
order phase 1 happened to complete in, so two runs over the same graph agree
regardless of scheduling. Before each merge, the subject and its confirmed
candidates are re-resolved for staleness; a subject that has itself become an
alias since phase 1 read it is skipped, not retried.

### The in-pass alias map is part of the decision

`GraphStore` is a read model, written only by a projection replaying the log.
`resolve_many` runs no projection between phases, so `resolve_entity_ids`
**structurally cannot see a merge this same call already emitted** — the
graph will not reflect it until something replays the event, which this pass
does not do. Phase 3 therefore keeps its own map, in memory, of ids absorbed
by merges emitted earlier in this call, and consults it alongside the graph's
own staleness check on every subsequent merge.

This is not a convenience; without it the documented staleness behaviour is
false. A mutual pair — subject A's neighbourhood confirms B, and B's
confirms A, which is the ordinary shape of a genuine duplicate pair when both
members are in the subject list — would otherwise merge twice: once when A
absorbs B, and again when B's own (now stale) decision tries to execute,
naming an id that is already an alias. That is precisely the shape
`MergeIntoAliasError` exists to reject, and without the in-pass map it would
surface as an uncaught error on the ordinary case rather than the exceptional
one.

Replaying a projection between phases 2 and 3 was considered and rejected: it
would make `resolve_many` write to the graph indirectly, which is the
boundary [`0004`](0004-consolidation-emits-events.md) exists to hold.

### Why skip, not retry

A subject found stale in phase 3 is dropped from this pass's output, not
re-scored. Retrying would mean re-scoring against a graph that this same
pass's other, not-yet-emitted decisions are still going to change — a
fixed-point computation wearing a single pass's clothes, with no defined
point at which it is allowed to stop. The duplicates that subject would have
found now belong to whatever absorbed it, and the next call to `resolve_many`
finds them there, under whatever the graph looks like by then. One pass is
one pass.

### `CallLimiter` moves to `domain`

`CallLimiter` was introduced by 0039 under `extraction`. `consolidation` and
`extraction` are siblings and forbidden from importing each other, so a
second `CallLimiter` in `consolidation` was the only way to give phase 2 the
same primitive — and two limiters is two ceilings, which defeats the point of
either: the backend does not care which caller's limiter admitted a request,
so two independent objects each admitting `concurrency` callers can together
exceed the bound either one was meant to enforce. Moving the type to `domain`,
below both siblings, lets a caller share one `CallLimiter` instance across an
extraction pipeline and a consolidation pass against the same backend, which
is the only way the bound means what it says.

## Consequences

**`concurrency=1` is equivalent to a serial loop over `resolve`.** A
wavefront of size one processes subjects one at a time in phase 1, and phase
2's batch is then a single subject-sized unit — no output differs from
calling `resolve` in a loop over the same subjects, aside from the derived
emit order.

**The staleness window is wider than `resolve`'s, and re-resolution before
each emit is what makes that acceptable.** `resolve` documents a window
between its own read and its own append; `resolve_many` opens that window at
the end of phase 1 for every subject in the batch and does not close it until
phase 3 gets to that subject. Widening the window is only safe because
phase 3 re-checks before acting on it rather than trusting phase 1's
decision unconditionally.

**BACKLOG B43 is unchanged by this ADR, not worsened.** It concerns a
parallel edge write from the extraction fold, not merge ordering within a
consolidation pass, and nothing here touches it.

**`MergeAdjudicator` gained a required `adjudicate_many` method, which is a
breaking change for any external implementation.** An adjudicator written
against the previous, single-subject-only Protocol will not satisfy it until
it adds the delegation the Protocol's own docstring describes. This is
recorded as a breaking change deliberately, because there is currently no
compliance suite for `MergeAdjudicator` (BACKLOG **B101**) and so no
mechanism here would otherwise have caught it — the blast radius on external
implementers is unknown, and that is worth stating plainly rather than
discovering on upgrade. See BACKLOG **B139**.

## What this does not do

No cross-tenant concurrency — the bound is scoped to the calls one
`resolve_many` invocation makes, and running the pass for several tenants
concurrently is a composition question left to the caller, exactly as 0039
leaves it for documents. No adaptive tuning of `concurrency`. No progress
reporting mid-pass. No transitive closure within one pass — a chain of
duplicates that phase 1 did not fully see resolved in one call is left for a
second call to `resolve_many`, the same way any other stale-and-skipped
subject is.

## Verdicts on existing ADRs

**[`0004`](0004-consolidation-emits-events.md) stands.** Phase 3 emits, a
projection writes; splitting the pass into phases makes that separation more
load-bearing than it was for a single-subject call, not less, since the whole
in-pass alias map exists to compensate for the projection this design
deliberately does not run.

**[`0010`](0010-one-total-order-for-preference.md) stands, and becomes
load-bearing the way 0039 made it for extraction.** Phase 1's completion
order varies with scheduling; nothing downstream may depend on it, which is
exactly the property `0010` established for the extraction fold and this
pass now also requires.

**[`0015`](0015-consolidation-gets-a-composed-entry-point.md) is amended.**
The composed entry point gains a corpus-level method beside the per-subject
one.

**[`0039`](0039-bounded-concurrency-over-chunks.md) is amended.** Its
`CallLimiter` becomes a shared primitive rather than an `extraction`-only
one — the second caller its own ceiling argument predicted.

**[`0006`](0006-the-public-surface-is-gated.md) stands, unexercised.**
`redstring.__all__` is unchanged by this work: every type the new signatures
name — `MergeAdjudicator`, `CandidateSource`, `EntitiesMerged`, `CallLimiter`
— was already exported before this pass existed.
