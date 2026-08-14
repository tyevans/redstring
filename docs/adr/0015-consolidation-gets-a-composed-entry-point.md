# ADR 0015: Consolidation gets a composed entry point, and an absent graph signal stops meaning zero

**Status:** accepted. Amended by [`0025` consolidation's substitution points
are protocols](0025-consolidation-substitution-is-two-protocols.md), which
retypes `resolve`'s `finder` and `adjudicator` against one-method protocols.
The Decision below stands unchanged: same entry point, same arguments, same
defaults, same banding.

Also amended by [`0034` neighbours are compared by
name](0034-neighbours-are-compared-by-name.md). The decision this ADR is
named for — an *absent* graph signal must not read as zero — stands entirely.
What 0034 corrects is the clause immediately after it, that a disjoint
neighbourhood with structure on either side "still scores `0.0`, because that
is a real finding about two entities". Ids are namespaced per document by
`entity_id_for`, so across two documents that disjointness was an artefact
rather than a finding, and it scored the same 0.7143 rejection this ADR was
written to remove.

Also amended by [`0041` the consolidation pass is
decide-then-emit](0041-the-consolidation-pass-is-decide-then-emit.md), which
gives the composed entry point a corpus-level method alongside the
per-subject one this ADR describes. The Decision below is otherwise
unchanged.

**Why this is an ADR:** it changes the public surface, and it changes what a
merge decision is. Either alone would qualify under
`.claude/rules/definition-of-done.md`; they are one ADR because the second was
found by doing the first, and neither is fully motivated without the other.

## Context

This library states a three-part thesis: extraction, **consolidation**, and
storage that can be rebuilt. The README argues the middle one hardest —
without it a graph accumulates one node per *mention*, which looks like a
knowledge graph and answers every question wrong, because each entity's edges
are split across its aliases.

Consolidation was real, tested, and **not on the public surface**. Reaching it
meant `redstring.consolidation.service.ConsolidationService`, a dotted path
the documentation simultaneously described as liable to move without notice.
So the library told its users that the step was essential and then declined to
ship it as API.

The stated reason was that its shape was "still being decided by the callers
it does not have", and that was honest rather than evasive. But the deciding
factor turned out to be assembly cost, which is measurable: constructing a
working consolidation required **six objects from three packages**, two of
them eventsource internals (`AggregateStore`, `SnapshotStore`) that no
`redstring` documentation mentions. `build_graph` had already solved the same
problem for extraction and nobody had done it here.

## Decision

### 1. `Consolidator` is the composed entry point, and it is exported

It stands to `ConsolidationService` exactly as `build_graph` stands to
`ExtractionPipeline`: the service decides and emits, `GraphProjection` writes,
and this holds both so a caller does not have to.

```python
consolidator = Consolidator(store)
report = await consolidator.resolve(subject)
```

Three methods — `merge` (the decision is already made), `resolve` (block,
score, band, adjudicate, merge), `undo` — each returning a
`ConsolidationReport` describing what happened to the graph.

**It never writes to the store directly.** Every change arrives through a
projection applying an event, so a store maintained by this class and a store
rebuilt by replay end up identical. That is the property
[ADR 0004](0004-consolidation-emits-events.md) exists to protect, and a
composed entry point is exactly where it would have been convenient to break
it.

### 2. The event store defaults to in-memory, and the cost is named

`undo` takes a merge's event id and **nothing describing what to restore** —
the aggregate rehydrates its own history, because a caller supplying the
restoration would be a caller able to restore something that never happened.
That history lives in an event store.

Requiring one would reintroduce the assembly cost this ADR exists to remove.
Defaulting to in-memory means merge, resolve and undo all work, the graph is
always correct, and **the history dies with the object**: a new `Consolidator`
cannot undo an earlier one's merge, and raises `UnknownMergeError` — which is
also what it raises for a merge that never happened.

This is the same trade `build_graph` makes ("there is no log to rebuild
from"). It is accepted on the same grounds and, unlike there, it is also
*asserted*: `test_a_fresh_consolidator_cannot_undo_an_in_memory_merge` pins
the limitation so it cannot be discovered after a restart, and
`remembers_merges_across_restarts` reports which arrangement is in use.

### 3. An empty-vs-empty neighbour comparison is absent, not zero

`CandidateFinder` scored the graph feature by Jaccard overlap of two neighbour
sets. `graph_similarity` returns `0.0` for two empty sets, and documents that
choice deliberately: "nothing is known about either" must not read as "these
agree perfectly", so it is not `1.0`.

That reasoning is right and the conclusion did not follow, because there was a
third option the module already uses everywhere else — **absent**. `None` is
what the finder returns when the signal is switched off, precisely so a
configuration flag cannot invent evidence. Two empty sets are the same
situation arrived at differently.

Scoring them `0.0` says the entities positively disagree. Measured, on two
entities named "Ada Lovelace" with no edges: combined score **0.7143** with
the graph signal on, **1.0** with it off. `LOW_SIMILARITY` is `0.75`, so an
identical-name pair was **rejected outright — not merged, and not even
adjudicated** — by a feature that had nothing to say.

The finder now returns `None` for that case. A disjoint neighbourhood where at
least one side *has* structure still scores `0.0`, because that is a real
finding about two entities.

**That last sentence is true of one document and was not true across two; see
[`0034`](0034-neighbours-are-compared-by-name.md).** Neighbours were compared
by id, and ids are namespaced by `source_id`, so two extractions of one
neighbour could not overlap however completely they agreed. The reasoning here
is unchanged — disagreement is still disagreement — but the comparison could
not tell disagreement from an id scheme.

## Consequences

**The public surface grows by fifteen names, and that is the mechanism
working.** [ADR 0006](0006-the-public-surface-is-gated.md) says exporting one
name pulls its closure, and the gate enumerated it rather than leaving it to
judgement: the two events a report carries, the two collaborators `resolve`
names, the four value types those name in turn, the four consolidation errors,
and `AggregateStore`/`SnapshotStore` recorded as foreign. The closure
terminated on its own; nothing had to be argued about.

The four consolidation errors left
`UNEXPORTED_BECAUSE_THEIR_RAISER_IS` in the same commit, which is the pairing
that dict was built for.

**Merge outcomes change for existing callers.** Anyone running a
`CandidateFinder` with the graph signal on will now see pairs merge that
previously scored below `LOW_SIMILARITY`. This is the intended correction, but
it is a behaviour change and not a bug fix from the caller's side: a corpus
consolidated before and after this version can differ. Nothing is released
yet, so no one is affected in practice.

**The consolidation suite had a blind spot and it should be assumed to have
others.** Every existing test built its finder with `use_graph_signal=False`,
so no test in the package had ever reached the branch this ADR corrects. The
one test that did assert on the graph feature used two isolated entities and
asserted `graph is not None` — encoding the defect as the specification, which
is `recurring-defects.md` §4. It now uses a fixture where the feature has
something to report, and two new tests cover the empty-vs-empty and
disjoint cases separately.

**Temporal inference is still not exported**, and the argument for it is now
weaker rather than stronger. What changed here was not a discovery that the
shape was obvious; it was doing the composition work. `redstring.temporal`
deserves the same treatment and has not had it.

## Alternatives rejected

**Export `ConsolidationService` directly.** It would have satisfied the letter
of the complaint and left the assembly cost intact, which was the actual
barrier.

**Require an event store.** Honest, and it puts eventsource's two ports in the
first line of every consolidation example — reintroducing the friction that
kept this unexported for four slices.

**Default `use_graph_signal=False` on `Consolidator`.** This was the tempting
fix when identical entities failed to merge, and it would have worked. It also
would have buried a defect in `CandidateFinder` under a default, leaving the
next caller who turns the signal on to rediscover it — and leaving
`use_graph_signal=True`, which is `CandidateFinder`'s own default, as a
setting that silently degrades the most common case.
