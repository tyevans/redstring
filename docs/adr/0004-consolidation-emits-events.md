# ADR 0004: Consolidation emits events rather than writing

**Status:** accepted, slice 7 of the ring migration. Closed `BACKLOG` B40.

**Why this is an ADR:** the previous implementation resolved duplicates by
writing to the store directly, and it was deleted rather than ported. Someone
will eventually notice that `ConsolidationService.merge` does a lot of work to
produce an event that a projection then applies, and propose collapsing the
two. This records what that would cost.

## Context

The deleted `SimpleMerger`/`LLMMerger` (recoverable from
`ff36ec7:src/redstring/services/consolidation/`) decided that "Ada" and "Ada
Lovelace" were the same entity **inside extraction**, and wrote the merged
result. Two consequences followed from that placement, and neither was a
performance concern:

- **The merge was unauditable.** No event recorded that a judgement had been
  made, what it scored, or what a model said about it. The graph simply had one
  entity where the documents had two.
- **The merge was un-undoable.** Nothing held the pre-merge state, so a wrong
  merge was permanent — and the merges most worth undoing are exactly the ones
  a fuzzy-similarity threshold gets wrong.

## Decision

`ConsolidationService` does **not** write to `GraphStore` or `VectorStore`. It
reads the graph to work out what a merge would do, records that as an
`EntitiesMerged` on the `ConsolidationLog` aggregate, and stops. A projection
applies it. Every method is the same shape: **read, plan, emit.**

Undo is a compensating event (`MergeUndone`), not a rollback. Its payload is
derived from the log, not from the caller — the caller names a merge, and the
aggregate replays what that merge did.

The invariants live on the aggregate, checked against replayed log state rather
than against the graph: no merging into an alias, no double-merge, and undo
must reference a real merge. Nothing enforced any of the three before.

## Evidence that this holds

The slice's headline test is that **merge → undo reproduces the pre-merge graph
exactly**, over a *diamond* graph containing all four cases at once — an edge
that moves, a self-loop collapse, a duplicate collapse, and an uninvolved edge
that must not move.

Three properties of that test are what make it evidence rather than
decoration, and all three were added because a weaker version passed against
broken code:

- **An independent oracle.** The expected graph is recorded separately, not
  produced by the fold. An equivalence property whose two sides both run the
  fold cannot distinguish a correct fold from one that does too little — slice
  5b's replay-equivalence suite passed against a handler that never applied an
  undo, one that never deleted a dropped edge, and one that never wrote
  relationships at all.
- **Control assertions that fail a do-nothing merge.** Without these, a merge
  that did nothing round-trips perfectly.
- **A diamond rather than a chain.** On simpler shapes several wrong
  implementations agree with the right one.

Eight hand-applied mutants died against it. The ninth survived and was the
useful one: a comment in `_apply_undo` claimed an ordering was load-bearing and
it was not — a comment asserting a constraint that does not exist is how a
later reader comes to believe the fold resolves everywhere.

## The cost, stated

The read happens **before** the aggregate is loaded, so the edge set a merge
plans against can be stale by the time the append happens. This is deliberate:
the read model is a projection and lags the log by construction, so no ordering
of the two steps makes the graph authoritative, and doing the read inside the
aggregate's window would widen the window without making it correct.

Staleness has three consequences and only the third matters. A redirection for
an edge that has since gone is harmless (both writes are idempotent). An edge
that appeared after the read is self-healing (the extraction fold resolves it
on the next `DocumentExtracted`). But if the canonical entity already carries
the same claim, **that resolution creates a permanent parallel edge rather than
fixing one**, and nothing repairs it — re-extraction is what produces the
duplicate.

That is `BACKLOG` B43, open, pinned in
`tests/unit/consolidation/test_known_gaps.py`, which asserts the wrong answer
on purpose. Only re-plan-on-version-conflict addresses it, because
`plan_redirections` is the only code that deduplicates and by definition it
never saw the late edge.

## Consequences

- A caller who wants consolidation applied must run the projection. `build_graph`
  does both halves for a caller who has no event store.
- Rejected candidates are discarded, not recorded (`BACKLOG` B44) — which is
  precisely the data needed to tune the two similarity thresholds, currently
  inherited numbers with no measurement behind them on any real corpus.
- Blocking stayed a pure function in `domain/`, which is what let `extraction`
  and `consolidation` remain sibling layers that never import each other. The
  cost is that nothing but a test spanning both can show they agree about what
  a key is — and slice 7 found that they did not: `mapping.py` never set
  `blocking_keys`, so every entity reached the store with `None` and
  consolidation would have found no candidates in production. Silently, because
  an empty candidate list is what "no duplicates" also looks like.
