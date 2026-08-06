# Consolidate duplicate entities

Two entities in the same tenant can stand for the same real thing — `doc-1`'s
"Ada" and `doc-2`'s "Ada" are separate entities by construction, because
`extraction.mapping.entity_id_for` namespaces ids per document. This guide
takes you from a populated graph to a merge you can audit and undo:

1. build a `CandidateFinder` to block and score the entities worth comparing,
2. build an `Adjudicator` so a model settles the ambiguous band,
3. call `ConsolidationService.resolve` on a subject entity,
4. fold the returned `EntitiesMerged` through `GraphProjection` to make it real,
5. and reverse it with `ConsolidationService.undo` when it was wrong.

Two things about the shape of this that will otherwise surprise you. First,
**the service never writes to your stores.** It reads the graph to work out
what a merge would do, records that as an event, and stops; nothing in your
graph changes until you apply the event (Step 6). See
[ADR 0004](../adr/0004-consolidation-emits-events.md) for why. Second,
**`resolve` returning `None` is the normal outcome** — it means nothing was
confirmed worth merging, not that anything failed.

If you have not appended events or run a projection before, read
[Use the write model](use-the-write-model.md) first; this guide assumes you
can already get an `AggregateStore` and a `SnapshotStore` and fold events
through a projection.

## Before you start

### What you need in place: an `AggregateStore`, a `SnapshotStore`, a populated `GraphStore`, and entities that carry `blocking_keys`

Four things, and the last one is the one that silently produces no candidates:

- **An `AggregateStore` and a `SnapshotStore`.** `ConsolidationService`
  takes both as keyword arguments and wraps them in
  `consolidation_repository`, which loads and appends the tenant's
  `ConsolidationLog` aggregate — see
  [aggregates](../reference/aggregates.md). The merge history lives here, not
  in the graph, which is what makes `undo` able to name a merge that happened.
  The snapshot store is *required*, not optional: a tenant's merge stream has
  no natural bound, so rehydrating it without snapshots grows without bound
  too.
- **A populated `GraphStore`, passed as `graph_store`.** Consolidation reads
  it and never writes to it. Three reads, between the service and the finder:
  `get_relationships_for` (what a merge would redirect),
  `find_by_blocking_keys` (the block), and `resolve_entity_ids` (dropping
  candidates that are already aliases). An empty graph is not an error; it
  just has nothing to consolidate.
- **A `LlmProvider`, if you want the ambiguous band settled.** Optional —
  Step 2 explains what happens without one. A `VectorStore` is optional in the
  same way, and Step 1 covers what its absence costs.
- **Entities that carry `blocking_keys`.** Blocking is the only thing between
  consolidation and a quadratic scan of the tenant, so a candidate is only
  ever found through a shared key. `Entity.blocking_keys` defaults to `None`,
  and `None` is silent in *both* directions:

  - the **subject** you pass to `resolve` with no keys never reaches the
    store at all — `CandidateFinder` returns an empty list before querying;
  - a **candidate** stored with no keys is never grouped under any key, so
    `find_by_blocking_keys` cannot return it.

  Either way there is no error and no warning: `resolve` returns `None`,
  which looks exactly like "nothing worth merging".

  Anything that went through extraction already has them:
  `extraction.mapping` fills the field with `domain.blocking.blocking_keys_for`
  before the entity reaches an event. If you construct `Entity` objects
  yourself and write them to a store directly, call `blocking_keys_for`
  yourself and set the field:

  ```python
  from redstring.domain.blocking import blocking_keys_for

  entity = entity.model_copy(update={"blocking_keys": blocking_keys_for(entity)})
  ```

  The default strategies always produce at least an entity-type key, so a
  well-formed entity never ends up with an empty set by accident. See
  [ADR 0003](../adr/0003-blocking-keys-as-nodes.md) for why the entity carries
  the keys and the store only groups by them.

Merging also changes what later extraction writes: once the aliases exist, the
extraction fold resolves endpoints through them
([ADR 0009](../adr/0009-the-extraction-fold-resolves-through-aliases.md)),
so a merge is not a one-off edit to the graph you have — it is a fact that
keeps applying.

### Why this is imported by path and not from `redstring` (and what that means for upgrades)

Every class in this guide is imported by a dotted path:

```python
from redstring.consolidation.candidates import CandidateFinder
from redstring.consolidation.policy import Adjudicator
from redstring.domain.similarity import FeatureWeights
from redstring.consolidation.service import ConsolidationService
from redstring.events.merge import EntitiesMerged, MergeUndone
from redstring.domain.exceptions import (
    DoubleMergeError,
    MergeIntoAliasError,
    UnknownMergeError,
)
```

None of those names are in `redstring.__all__`, and that is a deliberate
statement about stability rather than an oversight. `redstring`'s docstring
puts it plainly: **everything named in `__all__` is supported and nothing
else**, and anything reached through a dotted path may change without notice,
including in a patch release. Consolidation is on the "deliberately not here"
list for one stated reason — it has no composed entry point yet, so exporting
these classes would publish an API whose shape is still being decided by
callers it does not have.

The surface is *closed*, not merely documented: every exported name's
signature mentions only exported types, and every `RedstringError` subclass is
either exported or recorded against the capability whose export would bring it
([ADR 0006](../adr/0006-the-public-surface-is-gated.md)).
`MergeIntoAliasError`, `DoubleMergeError` and `UnknownMergeError` are recorded
there today with the reason "`redstring.consolidation` is not exported yet",
which is the same decision seen from the exception side.

What that means when you upgrade:

- **Pin an exact version, and read the changelog rather than trusting
  semver.** Signatures, keyword names, and module locations under
  `redstring.consolidation` may all move in a patch release. The stability
  promise that covers `build_graph` does not extend to
  `ConsolidationService`.
- **Import from the module you actually depend on, not from a re-export.**
  `redstring.events` re-exports `EntitiesMerged` and `MergeUndone`, but the
  defining module is `redstring.events.merge`. Either path is internal;
  neither is guaranteed.
- **The half of this guide that touches exported names is the stable half.**
  `GraphProjection`, `replay`, `Entity`, `Alias`, `Relationship`,
  `MissingEntityError` and `AliasCycleError` are all exported. So Step 6 —
  folding a merge event through the projection — rests on the supported
  surface even though Step 3 does not.
- **The events outlive the API that produced them.** `EntitiesMerged` and
  `MergeUndone` are durable log entries governed by the log's compatibility
  rules ([events](../reference/events.md),
  [ADR 0004](../adr/0004-consolidation-emits-events.md)), not by this module's.
  Code that folds a stored event is more durable than code that calls the
  service, which is a good reason to keep the two steps separate in your own
  application rather than wrapping them in one helper.

When consolidation is exported it will arrive with its own closure of types —
exporting one name obliges every type its signature mentions — and this
section will say so instead of warning you off.

## Step 1: Build a `CandidateFinder`

`CandidateFinder` does two things and decides nothing: it **blocks** (asks the
graph store for everything sharing a blocking key with your subject) and it
**scores** each of those candidates on `0..1`. It never writes, and it never
merges — `ConsolidationService` does that in Step 4, with a finder you hand it.

The minimum construction takes a `GraphStore` and nothing else:

```python
from redstring.consolidation.candidates import CandidateFinder

finder = CandidateFinder(graph_store)
```

You can call it directly, which is the fastest way to see whether your data
blocks at all before any model is involved:

```python
for candidate in await finder.candidates(subject, minimum_score=0.75):
    print(candidate.entity.name, candidate.score, candidate.features)
```

Each result is a `ScoredCandidate` carrying three things: the `entity`, the
combined `score`, and the per-signal `features` (`name`, `embedding`, `graph`,
each a float or `None`). Keep the features when you are tuning — "the name
matched and nothing else did" and "everything matched a little" reach the same
combined number by different routes, and only `features` tells them apart.

Two behaviours of `candidates` to rely on:

- **Results are best first, and the order is total.** Ties in score break by
  ascending entity id as a string, so two runs over one graph agree and taking
  `[0]` is deterministic. Duplicates tie on every signal constantly, so this
  is the common case, not an edge one.
- **`minimum_score` drops candidates before you see them,** defaulting to
  `0.0`. Step 4 passes `low` here, which is why `resolve` never has to reject
  anything itself.

What is *not* in the list: the subject itself, and any candidate that has
already been merged away. Both are excluded through one
`GraphStore.resolve_entity_ids` call over the whole block, so a chain
`B -> A -> C` proposes `C` and never `B`. Neither exclusion is an
optimisation — `ConsolidationLog` refuses to merge an entity twice, so an
aliased candidate is one nobody could act on.

If `candidates` returns `[]` for a subject you expected duplicates for, check
`blocking_keys` before anything else (see "Before you start"): a subject with
none returns `[]` without ever querying the store.

### With a `VectorStore`, and without one

Pass one as a keyword argument to add the embedding signal:

```python
finder = CandidateFinder(graph_store, vector_store=vector_store)
```

You supply the store already populated; consolidation only reads it (`get` and
`search`), and never embeds or upserts anything.

The score comes straight from `VectorStore.search` — the finder holds no
embedding provider and computes no distances itself. It reads the subject's own
vector with `get`, asks for the 50 nearest (`EMBEDDING_SEARCH_K`, with no
`entity_types` filter and no `min_score`), and looks each blocked candidate up
in that result by id.

**Read the scale before you tune anything.** The port defines `score` as cosine
mapped by `(1 + cosine) / 2`: `1.0` identical direction, **`0.5` orthogonal**,
`0.0` opposite. So an unrelated pair contributes about `0.5` to the embedding
feature, not `0.0`, and with the default weights that floor propagates into the
combined score. A threshold you calibrated against a raw-cosine store means
something different here.

The absence of a vector store is not an error, and neither is a missing
vector. In every case where the embedding cannot be computed the feature comes
back **absent (`None`), never `0.0`**:

- no `vector_store` passed;
- the tenant holds no vector for the subject;
- the candidate is in the block but outside the top 50 by vector rank — the
  store was not asked about it, so scoring it `0.0` would invent a
  disagreement.

That last case is the one to watch in a large tenant. The 50 is ranked over
**the whole tenant**, not over your block, so a subject in a crowded
neighbourhood can have blocked candidates that fall outside it and are then
scored on name and graph alone. `EMBEDDING_SEARCH_K` is a module constant, not
a constructor argument: if you need it larger, that is a change to
`redstring.consolidation.candidates`, not a knob.

That distinction is the whole reason a deployment without embeddings still
works. `combined_score` renormalizes over the features actually present, so a
name-and-graph score lands on the same `0..1` scale rather than being scaled
down by a missing third. If absent read as `0.0`, "the embedding provider was
down" and "these two entities are unalike" would produce the same number, and
your thresholds would move under you whenever the vector store did.

What it costs to run without one: the name signal is Jaro-Winkler over
normalized names, which is fooled by two different people with the same name,
and the graph signal only helps for entities that already have edges. Freshly
extracted entities with no relationships yet get neither — for those, the
embedding is the only signal that distinguishes them, so a no-vector
deployment should expect more traffic in the adjudication band (Step 5).

### Turning off the graph signal (`use_graph_signal=False`) and what it costs

```python
finder = CandidateFinder(graph_store, use_graph_signal=False)
```

The graph feature is Jaccard overlap of two neighbour sets, derived from
`GraphStore.get_relationships` — one call for the subject, plus **one more per
candidate**, awaited one at a time inside the scoring loop. That is the
expensive part of scoring: a subject blocked against 200 candidates makes 201
sequential round trips before a single score exists. Neither the embedding
step (one `get` and one `search`, whatever the block size) nor the name step
(pure computation, no I/O) scales with the block that way. `use_graph_signal`
is therefore the lever that matters for a large sweep.

Turning it off makes the feature **absent (`None`), not `0.0`** — the same
distinction the embedding signal makes, for the same reason. `_neighbours`
returns `None` when the signal is off and `[]` when the entity genuinely has
no neighbours, and `combined_score` renormalizes over the features actually
present. So a name-and-embedding score still lands on `0..1`, and the `high`
and `low` thresholds of Step 5 keep their meaning instead of drifting down by
the weight of the missing third. Setting `FeatureWeights(graph=0.0)` gets you
the identical arithmetic — a zero weight leaves the numerator and the divisor
together — but it still pays for every round trip, so it is the wrong way to
buy the speed.

What you give up is the one signal the other two cannot supply: two records
that barely look alike but sit in the same part of the graph. `"Augusta King"`
and `"Ada Lovelace"` score near zero on name and may have no vectors at all,
yet if both point at the same three papers the graph overlap is what finds
them. Turn it off when your duplicates differ mainly in surface form —
typos, casing, inflections — and the name and embedding signals already
separate them. Leave it on when they do not.

Two cautions:

- **With no `vector_store` *and* `use_graph_signal=False`, the score is the
  name score and nothing else.** That is a legal configuration and it will
  cheerfully run, but Jaro-Winkler alone cannot tell two different people with
  the same name apart. Expect a much heavier adjudication band (Step 5), or
  raise `high` so the model sees more of it.
- **Re-tune `high` and `low` after flipping this.** Renormalization keeps the
  scale, not the distribution: dropping a signal that usually disagreed pushes
  scores up, and dropping one that usually agreed pushes them down. `0.92`
  means something different against two features than against three.

One asymmetry worth knowing when you read `features.graph` with the signal
left **on**: **two entities with no neighbours at all score `0.0`, not the
conventional Jaccard `1.0`.** That convention is deliberately rejected in
`domain/similarity.py` — "nothing is known about either" must not read as
"these agree perfectly", or every pair of freshly extracted entities would
arrive at the top of your candidate list on the strength of knowing nothing.
So on a graph you have only just populated, the graph signal costs you its
round trips and returns `0.0` for almost everything; that is the case where
turning it off is closest to free.

### Overriding `FeatureWeights`

```python
from redstring.domain.similarity import FeatureWeights

finder = CandidateFinder(
    graph_store,
    vector_store=vector_store,
    weights=FeatureWeights(name=0.5, embedding=0.3, graph=0.2),
)
```

Those are the defaults, spelled out — passing no `weights` at all constructs
exactly that. Five things to know before changing them:

- **The values need not sum to 1.** `combined_score` divides by the sum of the
  weights of the features *actually present*, so `FeatureWeights(name=10.0,
  embedding=1.0, graph=1.0)` is a perfectly good way of saying "the name counts
  ten times as much as either of the others". Ratios are what matter; the
  absolute magnitudes never reach the score.
- **A weight of zero is exactly equivalent to the feature being absent.** The
  term leaves the numerator and the weight leaves the divisor together, so
  `embedding=0.0` yields the same number as passing no `vector_store` — it does
  not drag scores down towards zero. This falls out of the arithmetic rather
  than being arranged by a filter, which is deliberate: an earlier version
  filtered zero-weight features out explicitly, and a mutant removing that
  filter survived every test, because there was nothing for it to change.
- **A zero weight buys you nothing in I/O.** `use_graph_signal=False` and
  `FeatureWeights(graph=0.0)` produce identical scores, but only the first
  skips the per-candidate `get_relationships` calls. If your reason for
  dropping a signal is cost, use the flag; use a zero weight only when you want
  the arithmetic without restructuring the finder.
- **Weights are bounded below at zero, and all-zero is rejected.** Each field
  is `ge=0.0`, so a negative weight — an attempt to score *disagreement* as
  evidence — fails validation at construction. All-zero fails too, with
  `ValueError: at least one feature weight must be positive`: every pair would
  score identically, which looks exactly like a corpus containing no duplicates
  — a plausible answer, and therefore not one anybody investigates.
- **`FeatureWeights` is frozen.** Build a new one rather than mutating an
  existing instance. A weight vector that changed between two comparisons makes
  the scores incomparable, and there is nothing in a score that would show it.

One case the weights cannot rescue: when *no* feature was computed at all —
no vector store, `use_graph_signal=False`, and, say, a subject whose name
scored nothing because you supplied none — `combined_score` returns `0.0`
whatever the weights say. That is the "no evidence" answer, not a similarity
of zero, and with a `minimum_score` above `0.0` the candidate simply never
reaches you.

Re-tune `high` and `low` (Step 5) after changing weights. The thresholds are
calibrated against a scoring function, and re-weighting changes what `0.92`
means — usually by shifting the whole distribution rather than by reordering
it, so the symptom is a suddenly empty or suddenly overflowing adjudication
band rather than obviously wrong merges.

## Step 2: Build an `Adjudicator` over your `LlmProvider`

`Adjudicator` settles the band. Give it anything satisfying the `LlmProvider`
port and nothing else:

```python
from redstring.consolidation.policy import Adjudicator

adjudicator = Adjudicator(provider)
```

`provider` is whatever you already use for extraction — the port is one
method, `extract(text, schema, *, system_prompt)`, and the shipped adapter is
`redstring.llm.adapters.langchain.LangChainLlmProvider(chat, model=...)`. The
adjudicator holds no prompt of yours, no model name, and no configuration: it
sends its own system prompt and asks for an `AdjudicationBatch` back.

You can call it directly, which is worth doing once before wiring it into
Step 4:

```python
verdicts = await adjudicator.adjudicate(subject, candidates)
for candidate, verdict in zip(candidates, verdicts, strict=True):
    print(candidate.entity.name, verdict)
```

`candidates` is a sequence of `ScoredCandidate` — normally the ones Step 1
scored into the band. The result is **one entry per candidate, positionally
aligned**, each either an `AdjudicationVerdict` or `None`. `zip(...,
strict=True)` is the right way to read it; the service uses exactly that.

An `AdjudicationVerdict` carries three fields, and only two of them do
anything today:

- **`same: bool`** — the answer. `resolve` merges a candidate when, and only
  when, its verdict is not `None` and `same` is true.
- **`reason: str`** — required, free-form, and load-bearing. It is joined into
  `EntitiesMerged.merge_reason`, so it is the only surviving record of *why*
  a judgement call went the way it did. Merges above `high` get the generated
  reason `"score >= 0.92"` instead; a reason that reads like a sentence is
  therefore a merge a model made.
- **`confidence: float`** (`0.0..1.0`) — validated, returned, and **not
  consulted by `resolve`**. A low-confidence `same: true` merges exactly like
  a high-confidence one. If you want a confidence floor, call `adjudicate`
  yourself and pass the survivors to `merge` (see "Merging a known group
  directly"); there is no threshold argument for it.

The prompt tells the model to answer `same: false` when unsure, on the
grounds that a wrong merge is harder to notice than a missed one. That bias is
fixed in `_SYSTEM_PROMPT` and is not a constructor argument.

### Choosing a `batch_size`

```python
adjudicator = Adjudicator(provider, batch_size=4)
```

The default is `ADJUDICATION_BATCH_SIZE = 10`, a module constant in
`redstring.consolidation.policy`. `adjudicate` walks the candidates in
consecutive slices of that size and makes **one `LlmProvider.extract` call per
slice**, so `batch_size` is directly the number of model calls a band of *n*
pairs costs: `ceil(n / batch_size)`. The returned list is still one verdict
per candidate in the original order, whatever the batching — the batches are
an implementation of the call, not of the result.

`batch_size < 1` raises `ValueError` at construction, and `batch_size=1` is
legal. Zero is refused rather than clamped because the slicing loop would
produce no calls and no verdicts, and an empty verdict list reads downstream
exactly like a model that answered "not the same" to everything.

Batching is what keeps the band affordable, but a batch re-pairs **by
position**. The prompt goes out numbered (`Pair 1`, `Pair 2`, …) and the model
returns an `AdjudicationBatch` — a bare list of verdicts, with no ids in it.
Ids are kept out deliberately, on the grounds that ids are the graph's
business and putting them in a prompt invites a model to invent one; the
consequence is that position is the only alignment there is. That sets the
ceiling on `batch_size`: a long batch is where a model starts losing track of
which answer belongs to which pair, and a mis-aligned verdict is a wrong merge
with a plausible-sounding reason attached to it.

The misalignment that *can* be detected is handled hard, and the blast radius
is the batch:

- **A batch whose verdict count disagrees with its pair count yields `None`
  for every pair in that batch** — not just for the tail, and in both
  directions: too few verdicts and too many are treated the same way. A
  disagreeing count means the alignment is unknown, so the verdicts that did
  arrive cannot be trusted to belong to the pairs they line up against.
  Silently taking the prefix is how a model's answer about pair 3 gets
  recorded against pair 1.
- **A provider error yields `None` for that batch and no others.**
  `adjudicate` catches `LlmProviderError`, so an empty, refused or malformed
  completion costs you that batch; the remaining batches are still sent.
- **A `None` is not a "no".** Every pair in a discarded batch is left
  unanswered, which `resolve` treats as "not confirmed" — the pair is neither
  merged nor recorded as rejected. See the next section; the reasoning is the
  same one.

So a smaller `batch_size` costs more calls and buys two things: less room for
a model to lose the ordering, and a smaller blast radius when a batch is
thrown away. At `batch_size=1` both failures above become per-pair and a
mis-ordered answer is impossible, at exactly the price the batching exists to
avoid. Raise it above `10` only if you have measured that your model holds its
ordering there — nothing in this library can tell you it has not, because a
mis-ordered batch of the *right length* parses cleanly and merges the wrong
entities.

### Running without an `Adjudicator`: the band is rejected, not merged

`adjudicator` is a keyword argument on `resolve` and its default is `None`:

```python
event = await service.resolve(subject, finder=finder)  # no adjudicator
```

With no adjudicator, **everything between `low` and `high` is rejected.** Only
candidates scoring `>= high` reach the merge group. The band is not merged, not
deferred, and not queued for review — those candidates simply do not appear in
the group, and if nothing else was confirmed `resolve` returns `None`, which is
indistinguishable from having found no candidates at all.

The direction is deliberate, and it is the same rule the whole path is built
on: **an unanswered question is never a yes.** The band exists precisely
because the score does not settle those pairs, so treating "nobody was asked"
as agreement would merge exactly the pairs a model was there to protect.
`resolve` confirms a candidate only when a verdict came back and `same` is
true, so no adjudicator and a `None` verdict from a discarded batch (previous
section) behave identically for the pairs they cover.

What still happens without one:

- **Candidates below `low` were never in play anyway.** `resolve` passes
  `minimum_score=low` to the finder, so the low threshold is applied by the
  scoring pass rather than by any decision after it. Dropping the adjudicator
  changes what happens to the *middle* band only.
- **Merges above `high` are unaffected**, and their `merge_reason` is the
  generated string `"score >= 0.92"` (the value of `high` you passed). A
  `merge_reason` that reads like a sentence is therefore always a model's
  words; a no-adjudicator deployment's audit trail is scores and nothing else.
- **The aggregate's refusals are unchanged.** `MergeIntoAliasError` and
  `DoubleMergeError` are checked against the replayed log, not against the
  policy, so an aliased subject still raises rather than returning `None`.

Two consequences worth planning around:

- **Nothing tells you how much you are leaving on the floor.** Rejected
  candidates are not recorded anywhere (BACKLOG B44), so a band holding half
  your duplicates looks exactly like an empty one. If you intend to run this
  way, call `finder.candidates(subject, minimum_score=low)` yourself
  periodically and look at the score distribution between `low` and `high`
  — that is the only view of what you are discarding.
- **The signals you dropped in Step 1 land here.** A finder with no
  `vector_store`, or with `use_graph_signal=False`, moves pairs *down* out of
  the merge band and into the ambiguous one — which is the band now being
  discarded. Running without embeddings *and* without an adjudicator is the
  configuration that quietly merges least, and it will look like a corpus with
  no duplicates in it.

If what you actually want is the band merged without a model, a missing
adjudicator is not how to ask for it. Lower `high` instead (Step 5): it says
the same thing explicitly, keeps `merge_reason` honest about the basis for the
merge, and leaves every merge undoable by its `event_id` (Step 7) either way.

## Step 3: Construct the `ConsolidationService`

Three stores, all keyword-only, and nothing else:

```python
from redstring.consolidation.service import ConsolidationService

service = ConsolidationService(
    event_store=event_store,
    snapshot_store=snapshot_store,
    graph_store=graph_store,
)
```

Note the keyword name: **`event_store`**, not `aggregate_store`. It takes an
`AggregateStore` — the write-model port from
[Use the write model](use-the-write-model.md) — and the parameter is named for
the role it plays here.

The finder and the adjudicator you built in Steps 1 and 2 are *not*
constructor arguments. They are passed per call to `resolve` (Step 4), so one
service can serve a cheap name-only sweep and a careful embedding-and-model
pass over the same tenant without being rebuilt.

What each store is for:

- **`event_store` + `snapshot_store`** are wrapped together, at construction,
  into a repository over the tenant's `ConsolidationLog`
  ([aggregates](../reference/aggregates.md)). Every merge and every undo is
  loaded and appended through it. This pair is the merge history — the graph
  holds the *result* of a merge, and only the log holds the fact that one
  happened, which is what makes `undo` (Step 7) able to name it.
- **`graph_store` is read and never written.** The service reads it to work
  out what a merge would do — `get_relationships_for` over the whole group,
  before anything touches the aggregate — and hands it to nothing else. The
  finder you pass to `resolve` holds its own reference; they are usually the
  same object, but nothing enforces that.

Three things about this construction that are decisions rather than
conveniences:

- **`snapshot_store` is required, not optional.** A tenant's merge stream has
  no natural bound — one stream per tenant, forever — so rehydrating it
  without snapshots gets slower for the tenant's whole life. An optional
  parameter is one nobody passes, and the omission would surface as slow
  merges long after the code that omitted it was written. The repository
  snapshots every `CONSOLIDATION_SNAPSHOT_EVERY = 100` events
  (`redstring.aggregates.repositories`), which is not a constructor argument
  here.
- **The stream is the tenant.** `consolidation_stream` uses the tenant id
  itself as the aggregate id: there is exactly one consolidation log per
  tenant. That is what gives you optimistic concurrency across the tenant's
  merges — two concurrent `resolve` calls in one tenant contend, and one of
  them retries rather than both appending against different histories. It also
  means merges in one tenant serialise against each other, which is the known
  cost of the guarantee.
- **The service is stateless between calls and safe to keep.** It holds the
  graph store and the repository and no per-merge state, so construct one per
  tenant-agnostic process, not one per merge. `tenant_id` arrives with each
  call — from `subject.tenant_id` in `resolve`, explicitly in `merge` and
  `undo` — and every store access happens inside a `tenant_scope` for it.

A worked construction against the in-memory adapters, which is what the tests
use and the shortest way to see the whole thing run:

```python
from redstring.consolidation.candidates import CandidateFinder
from redstring.consolidation.service import ConsolidationService
from redstring.graph.adapters.memory import InMemoryGraphStore

graph_store = InMemoryGraphStore()
service = ConsolidationService(
    event_store=event_store,
    snapshot_store=snapshot_store,
    graph_store=graph_store,
)
finder = CandidateFinder(graph_store, vector_store=vector_store)
```

Nothing has read or written anything yet — construction opens no connection
and touches no stream. The first store access happens in Step 4.

## Step 4: Call `resolve` for one subject entity

One call does the whole pipeline — block, score, band, ask the model about the
band, and emit **one** `EntitiesMerged` for everything confirmed:

```python
event = await service.resolve(
    subject,
    finder=finder,
    adjudicator=adjudicator,
)
```

`subject` is an `Entity`, not an id — the finder needs its `name`,
`entity_type` and `blocking_keys` to block and score, and `resolve` takes
`tenant_id` from it. Everything after it is keyword-only: `finder` is
required, `adjudicator` defaults to `None` (Step 2), and `high` and `low`
default to `HIGH_SIMILARITY` and `LOW_SIMILARITY` (Step 5).

**The subject becomes the canonical entity.** Choosing which of two duplicates
to pass is choosing which one survives and which becomes an alias; there is no
scoring pass that picks a winner for you. The candidates are absorbed into the
subject, and their edges are redirected onto it.

### What the call does, in order

1. `finder.candidates(subject, minimum_score=low)` — one blocked, scored list,
   best first. If it is empty, `resolve` returns `None` without loading the
   aggregate or touching a stream.
2. Each candidate is banded by `decide(score, high=high, low=low)`. Because
   `low` was already applied as `minimum_score`, nothing in this list can band
   as a rejection — the low threshold is enforced by the scoring pass, not by
   a decision after it.
3. Candidates at or above `high` are confirmed immediately, with the generated
   reason `"score >= 0.92"` (whatever `high` you passed).
4. The band between them goes to the adjudicator in one `adjudicate` call, and
   the verdicts are re-paired with `zip(..., strict=True)`. A candidate is
   confirmed only when a verdict came back **and** `same` is true; a `None`
   verdict is not a yes. With no adjudicator the whole band is dropped.
5. If nothing was confirmed, `resolve` returns `None`. Otherwise it calls
   `merge` once for the entire group.

### One event for the group, not one per pair

Everything confirmed lands in a single `EntitiesMerged`: `canonical_entity_id`
is the subject, `merged_entity_ids` is every confirmed candidate, and
`merge_reason` is the individual reasons joined with `"; "` — so a mixed run
reads like `"score >= 0.92; both refer to the 1843 translator's notes"`, and
you can tell a threshold merge from a model's judgement by whether the clause
reads as a sentence.

That is a correctness property, not tidiness. `ConsolidationLog` refuses to
merge an entity twice, so two separate merges into one canonical entity would
each compute their `RelationshipRedirection` set against a different graph —
the second against a graph the first had already changed. Do not loop `resolve`
per candidate to get finer-grained events; use `merge` (below) if you need to
control the group.

### The graph read happens before the aggregate is loaded

`resolve` delegates to `merge`, which reads `get_relationships_for` over the
whole group *first*, plans the redirections, and only then opens the
`tenant_scope`, loads the log and appends. Keeping the read outside the
optimistic-concurrency window makes that window as short as the write.

The cost is that the graph can change between the read and the append. What
protects you is the aggregate, not the graph: `MergeIntoAliasError` and
`DoubleMergeError` are checked against the replayed log. The residual gap is a
stale read leaving a parallel edge — BACKLOG **B43**, and "Known limitations"
below.

### Errors you can get from this call

Two refusals come from the aggregate, before anything is written, so there is
never a half-applied merge to clean up:

- **`MergeIntoAliasError`** — the subject has itself been merged away. Note
  the asymmetry with candidates, which are silently excluded when they are
  aliases: an aliased candidate is one of many and dropping it costs nothing,
  while an aliased subject means you asked to consolidate around an entity
  that no longer stands for itself, and answering `None` would be
  indistinguishable from "no duplicates found".
- **`DoubleMergeError`** — a candidate in the group has already been absorbed
  by an earlier merge.

If you are sweeping a whole tenant, resolve your ids before calling:
`find_entities` returns absorbed entities too, because a merge is not a delete.

### Nothing has changed in your graph yet

`resolve` returning an event means the merge is *recorded*, not applied. The
service reads `GraphStore` and never writes to it
([ADR 0004](../adr/0004-consolidation-emits-events.md)); the aliases and the
redirected relationships appear only when you fold the event through
`GraphProjection`. That is Step 6, and skipping it is the most common way to
conclude that consolidation "did nothing".

### Reading the return value: an `EntitiesMerged`, or `None` when nothing was confirmed

The return type is `EntitiesMerged | None`, and the `None` is the ordinary
answer, not an error path:

```python
event = await service.resolve(subject, finder=finder, adjudicator=adjudicator)
if event is None:
    return  # nothing was confirmed worth merging; the graph is untouched
```

**`None` means "nothing was confirmed", and it collapses several distinct
situations into one value.** All of these return it, and none of them is
distinguishable from the others at the call site:

- the subject carries no `blocking_keys`, so the finder never queried the
  store;
- the block was empty, or held only the subject and entities already merged
  away;
- every candidate scored below `low`;
- the whole band went to the adjudicator and every verdict came back `same:
  false`;
- the whole band went to the adjudicator and every verdict came back `None`
  — a discarded batch, a provider error;
- there was no adjudicator and nothing reached `high`.

The first of those is a configuration mistake and the rest are answers, which
is why the first thing to check on an unexpected `None` is `blocking_keys`
(see "Before you start"). Nothing is recorded about the rejected candidates
either way — that is BACKLOG **B44**, and "Known limitations" below. If you
need to tell the cases apart, call `finder.candidates(subject,
minimum_score=low)` yourself and look at what came back before deciding it was
a quiet corpus.

`None` also means **nothing was appended.** `resolve` returns before opening
the tenant's stream when the candidate list is empty, and before calling
`merge` when nothing was confirmed, so a `None` leaves no event, no aggregate
version bump, and nothing for a later `undo` to name. A sweep that returns
`None` for every subject is indistinguishable in the log from a sweep that
never ran.

### What the event tells you

When it is not `None` you get exactly one `EntitiesMerged` for the whole
group, whatever the group's size — never one per pair. Four fields are worth
reading:

- **`event_id`** — a `UUID`, and the *only* handle on this merge. Step 7's
  `undo` takes it, and the aggregate has no other way to name a merge, so if
  you may ever want to reverse this you must persist `event.event_id`
  somewhere you can find it later. It is returned rather than looked up
  afterwards for a reason worth taking seriously: an undo naming the *wrong*
  merge is the one mistake `UnknownMergeError` cannot catch, because both ids
  name merges that happened.
- **`canonical_entity_id`** — always the subject you passed. The event
  validator rejects a canonical id that also appears in `merged_entity_ids`,
  so this is never one of the absorbed entities.
- **`merged_entity_ids`** — every confirmed candidate, in candidate order
  (best first, ties broken by ascending entity id as a string), with at least
  one element and no duplicates. Both are enforced by the event: `min_length=1`
  means an empty merge cannot be recorded at all, which is what makes `None`
  the *only* way `resolve` says "nothing happened".
- **`merge_reason`** — the individual reasons joined with `"; "`, one clause
  per merged entity in the same order. A clause reading `"score >= 0.92"` is a
  threshold merge; a clause reading like a sentence is an adjudicator's
  `verdict.reason`. That join is the entire audit trail for *why* each entity
  was absorbed, so a mixed run reads as
  `"score >= 0.92; both refer to the 1843 translator's notes"` and you can tell
  the two apart by eye.

A fifth field, **`redirections`**, is the plan rather than the reason: one
`RelationshipRedirection` per edge the merge touches, each carrying the whole
`before` relationship and an `after` that is the same edge moved onto the
canonical entity — or `None`, which means **the edge was dropped**, not that
nothing happened. An edge with both endpoints absorbed by this merge would
become a self-loop, which `Relationship` refuses, so the merge deletes it. You
rarely need to read this; the projection does (Step 6), and undo restores from
`before` (Step 7). An empty list is normal — it means no entity in the group
had any edges.

### Do not treat the return value as "the merge succeeded"

It means the merge was *recorded*, and only in memory until you do something
with it. Two separate things still have to happen, and the return value tells
you about neither:

- **The graph is unchanged.** The aliases and the redirected relationships
  exist only once the event is folded through `GraphProjection` — Step 6, and
  [ADR 0004](../adr/0004-consolidation-emits-events.md) for why the service
  will not do it for you.
- **Keep `event_id` before you fold.** Folding does not return it and the
  graph does not record it; the id lives in the log
  ([events](../reference/events.md)) and in the value you are holding right
  now.

The refusals are the other half of this. `resolve` never returns `None` to
signal a refusal — `MergeIntoAliasError` and `DoubleMergeError` are raised
from the aggregate before anything is appended, so the three outcomes are
genuinely three: an event (recorded), `None` (nothing to record), or an
exception (refused, with nothing half-applied).

## Step 5: Choose `high` and `low`

`high` and `low` are keyword arguments on `resolve`, and they are the only
tuning it has:

```python
event = await service.resolve(
    subject,
    finder=finder,
    adjudicator=adjudicator,
    high=0.95,
    low=0.80,
)
```

They are thresholds on the *combined* score from Step 1, so they only mean
anything relative to the scoring function that produced it. Change the
weights, drop the vector store, or turn off the graph signal, and these two
numbers have to be re-chosen.

`merge` (below) takes neither: it merges the group you name, without scoring.

### The defaults (`HIGH_SIMILARITY = 0.92`, `LOW_SIMILARITY = 0.75`) and what each band does

Both live in `redstring.consolidation.policy` and are exposed by `decide`,
which is the whole of the banding logic:

```python
from redstring.consolidation.policy import (
    HIGH_SIMILARITY,
    LOW_SIMILARITY,
    MergeDecision,
    decide,
)

decide(0.99)  # MergeDecision.MERGE
decide(0.80)  # MergeDecision.ADJUDICATE
decide(0.10)  # MergeDecision.REJECT
```

Three bands, and the two bounds are **inclusive from below**: `score == high`
merges, `score == low` adjudicates.

| Band | Decision | What `resolve` does |
|---|---|---|
| `score >= high` | `MERGE` | Confirmed with no model call. `merge_reason` clause is the generated `"score >= 0.92"`. |
| `low <= score < high` | `ADJUDICATE` | Sent to the adjudicator; merged only on a returned verdict with `same` true. No adjudicator, no verdict, or a `None` verdict all mean not merged. |
| `score < low` | `REJECT` | Never reaches `resolve` at all — see below. |

That inclusivity is stated in the source rather than left to be discovered,
because a pair landing *exactly* on a threshold is not rare: an exact name
match with no other signal produces a round number every time.

**`low` is enforced by the scoring pass, not by a decision after it.**
`resolve` calls `finder.candidates(subject, minimum_score=low)`, so the list it
bands can never contain a `REJECT`. Two consequences worth holding on to:

- lowering `low` widens what the *store query and scoring pass* hand back, so
  it is the threshold with the I/O cost attached (Step 1's per-candidate
  `get_relationships` calls);
- rejected candidates are dropped before anything sees them, which is why
  nothing records them (BACKLOG **B44**).

**Why the band exists at all:** sending every blocked pair to a model is
quadratic in model calls, which is the cost that makes LLM-assisted resolution
impractical. Sending only the band makes it proportional to the genuinely
ambiguous pairs, which is a small fraction of a block. Widening it is
therefore a spend decision — which is why the two values are named constants
rather than literals buried in a condition.

The defaults are inherited from a previously tuned threshold pair
(`MERGER_HIGH_SIMILARITY_THRESHOLD` and its partner, from the pre-event
mergers recorded as deleted in BACKLOG **B40**), not derived from your corpus.
Treat them as a starting point: run `finder.candidates` directly over a sample
and look at where your true duplicates actually score before moving either.

### Overriding one without the other collapses the band

`high` and `low` are separate keyword arguments with separate defaults, so
passing one leaves the other at its constant. That is the way the band quietly
disappears:

```python
# Band is now 0.90..0.92 — almost nothing will ever be adjudicated,
# and everything from 0.92 up still merges unasked.
await service.resolve(subject, finder=finder, adjudicator=adjudicator, low=0.90)

# Band is now 0.75..0.76 — everything above 0.76 merges without a model call.
await service.resolve(subject, finder=finder, adjudicator=adjudicator, high=0.76)
```

Neither raises, because neither is wrong: both are legal bands, and `decide`
refuses only the *inverted* pair (next section). What makes them a trap is that
the number you passed is not the thing that changed — the **gap** is, and the
gap is set by the argument you left alone.

The two moves fail in opposite directions, and only one of them is loud:

- **Raising `low` towards `high`** starves the adjudicator. Fewer model calls,
  a smaller bill, and no signal at all that anything changed: the pairs that
  would have been asked about are now dropped before the banding, and rejected
  candidates are recorded nowhere (BACKLOG **B44**). A tenant that stops
  producing merges looks exactly like a tenant that ran out of duplicates.
- **Lowering `high` towards `low`** does the same to the band, but the pairs
  go the other way — into unasked merges. Those are visible, at least: each
  arrives with `merge_reason` reading `"score >= 0.76"` rather than a
  sentence, so a spot-check of `EntitiesMerged.merge_reason` shows what
  happened (Step 4).

`HIGH_SIMILARITY` and `LOW_SIMILARITY` are named constants in
`redstring.consolidation.policy` rather than literals buried in a condition
for exactly this reason — the module says so in as many words. If you mean to
move one, import the other and pass it explicitly, so the band you chose is
visible in the call rather than inferred from a default:

```python
from redstring.consolidation.policy import HIGH_SIMILARITY

await service.resolve(
    subject, finder=finder, adjudicator=adjudicator, high=HIGH_SIMILARITY, low=0.90
)
```

Better still, hold the pair together in your own configuration and pass both
every time. A band is one decision with two numbers in it, and code that can
express half of it will eventually express half of it.

The degenerate case is `high == low`, which is legal and empties the band
completely: `decide(0.5, high=0.5, low=0.5)` is `MERGE` (both bounds are
inclusive from below, and `MERGE` is tested first) and `decide(0.4, high=0.5,
low=0.5)` is `REJECT`. `ADJUDICATE` becomes unreachable, so the adjudicator you
built in Step 2 is constructed, passed, and never called — no error, no warning,
and no model calls on the bill to notice. That is a real choice — "never ask a
model, merge everything above this line" — but make it deliberately, by writing
both numbers, rather than by moving one threshold onto the other.

### `low > high` raises ValueError

An *inverted* pair is refused rather than quietly accepted:

```python
await service.resolve(subject, finder=finder, adjudicator=adjudicator, high=0.2, low=0.8)
# ValueError: low (0.8) must not exceed high (0.2); an inverted band silently
# disables adjudication rather than failing
```

The check is in `decide`, so you get the same refusal calling the policy
directly:

```python
from redstring.consolidation.policy import decide

decide(0.5, high=0.2, low=0.8)  # ValueError
decide(0.5, high=0.5, low=0.5)  # MergeDecision.MERGE — legal, band is empty
```

**Why this is an error when `high == low` is not.** Both empty the band, and
the previous section says an empty band is a legal thing to choose. The
difference is what an inverted pair does to the *shape* of the decision rather
than to its width: `decide` tests `score >= high` first, so with `low` above
`high` every score at or over `high` merges and everything else rejects, and
the `low` you passed never affects a single decision. Nothing fails, nothing
is slower, and the adjudicator you built in Step 2 is simply never called.
The symptom is a run that reads as "the model was never needed" — a plausible
answer, and therefore not one anybody investigates. It is refused for the same
reason `FeatureWeights` refuses all-zero weights: a misconfiguration that
produces a believable result is worse than one that raises.

Two practical notes on *where* the error surfaces, both of which matter if you
were planning to catch it with a smoke run:

- **It is raised per candidate, after the finder has already run.** `resolve`
  bands the scored list by calling `decide` once per candidate, so an inverted
  band over a subject with **no candidates returns `None` and never raises** —
  `resolve` returns early on an empty list, before any banding. A validation
  pass over a subject that happens to have no duplicates will therefore
  certify thresholds that are broken.
- **The store query and the scoring have already happened by then.** `low` is
  passed to the finder as `minimum_score` before any `decide` call, so an
  inverted band pays for the block and the per-candidate scoring round trips
  and *then* raises. Worse, a high `low` used as `minimum_score` filters
  aggressively, which makes the empty-candidate case above more likely, not
  less: the more inverted the pair, the better the odds it fails silently.

So do not rely on `resolve` to tell you the band is wrong. Validate `high` and
`low` where you read them from configuration — `decide(high, high=high,
low=low)` on startup is enough, costs nothing, and raises in the one place the
mistake was actually made.

### Widening the band costs model calls; narrowing it costs review

The band is the whole design of this policy, and its width is a spend
decision. Every pair that lands between `low` and `high` is a pair put to a
model; every pair outside it is settled by arithmetic. So `high - low` is
directly what adjudication costs you, and the two edges of it fail in
different ways when you get them wrong.

| Move | Immediate effect | What it costs |
|---|---|---|
| **Lower `high`** | Band shrinks from the top; those pairs merge unasked | Fewer model calls. The merges arrive with `merge_reason` reading `"score >= 0.90"` and nothing else — no sentence to read back later. Wrong merges are cheap to spot and cheap to undo (Step 7), but you have to look. |
| **Raise `high`** | Band grows upwards; pairs that used to merge now get asked about | More model calls, and merges whose `merge_reason` is a sentence a person can audit. |
| **Lower `low`** | Band grows downwards *and* the scored candidate list grows | Both model calls **and** graph round trips: `low` is passed to the finder as `minimum_score`, so lowering it widens what the block scores, and with the graph signal on that is one `get_relationships_for` per extra candidate (Step 1). |
| **Raise `low`** | Band shrinks from the bottom; those pairs are dropped before anything sees them | Cheapest move available, and the one with no feedback at all. |

That last row is the one to be careful with. Rejected candidates are recorded
nowhere — BACKLOG **B44**, and "Known limitations" below — so raising `low`
buys you silence rather than information. A tenant whose duplicates all sit
just under `low` produces exactly the same output as a tenant with no
duplicates: `resolve` returns `None`, no event, nothing in the log. **Narrowing
the band does not reduce work, it moves work to a review nobody is prompted to
do.**

Widening it is visible in the bill instead, which is the better failure mode of
the two, but it is not smooth. At the default `batch_size = 10`, a band of *n*
pairs costs `ceil(n / 10)` calls (Step 2), so cost is a step function of band
width, and the step is per subject rather than per sweep — a hundred subjects
each gaining one banded candidate can cost a hundred extra calls, not ten.

### Measure the band before you move a threshold

You can price a change before paying for it, because the finder is callable on
its own and decides nothing:

```python
candidates = await finder.candidates(subject, minimum_score=proposed_low)
banded = [c for c in candidates if c.score < proposed_high]
print(len(candidates), len(banded), [round(c.score, 3) for c in banded])
```

`len(banded)` is the number of pairs a model would be asked about for that
subject under the proposed pair, and `ceil(len(banded) / batch_size)` is the
call count. Run it over a sample of subjects rather than one — block sizes are
extremely uneven, and the subject with a common name is where the calls
actually go.

Look at the scores themselves too, not only the count. The useful question is
not "how wide is the band" but "where do my true duplicates score", and
`c.features` tells you *why* each one landed where it did (Step 1). A band
crowded at its lower edge means `low` is doing the deciding; a band that is
empty while duplicates still merge means `high` is.

### The thresholds are relative to your scoring function

`0.92` is a number about a specific combination of signals. Anything that
changes the scoring changes what it means, and none of these raise or warn:

- adding or removing a `vector_store`;
- flipping `use_graph_signal`;
- changing `FeatureWeights`;
- a vector store whose `score` scale differs from the port's
  `(1 + cosine) / 2` — on that scale an unrelated pair contributes about `0.5`
  to the embedding feature, not `0.0` (Step 1).

`combined_score` renormalizes over the features actually present, so the scale
stays `0..1` in every one of those cases. What moves is the **distribution**,
and a distribution shift shows up as a suddenly empty or suddenly overflowing
band rather than as obviously wrong merges. Re-measure with the snippet above
after any of them.

### The asymmetry that should decide which way you err

Both errors are recoverable, but not equally:

- **Too many merges** (band too narrow at the top, `high` too low) leaves an
  `EntitiesMerged` with an `event_id` for every one of them, and `undo` takes
  that id (Step 7). The mistake is recorded, attributable, and reversible —
  *provided you kept the `event_id`* (Step 4).
- **Too few merges** (band too narrow at the bottom, `low` too high, or the
  band discarded for want of an adjudicator) leaves nothing at all. There is
  no event, no rejection record, and no id to name. The only way to find it is
  to re-run the finder over a sample and look.

So the recoverable direction is the expensive one and the silent direction is
the cheap one, which is the opposite of the pressure a bill applies. The
model's own prompt already leans the safe way — it is told to answer
`same: false` when unsure, because a wrong merge is harder to notice than a
missed one — and that only holds for pairs that reach it. Keep the band wide
enough that the ambiguous pairs get there, and let the cost show up somewhere
you will see it.

## Step 6: Apply the event — nothing has changed the graph yet

`resolve` returned an `EntitiesMerged`, and your graph is exactly as it was.
No alias exists, no edge has moved, and `resolve_entity_ids` still maps every
absorbed entity to itself. The merge becomes real when the event is folded
through `GraphProjection`:

```python
from redstring import GraphProjection

projection = GraphProjection(graph_store)
await projection.handle(event)
```

That is the whole of it. Everything below is why it is a separate step, what
the fold writes, and how to do it from a store instead of from the value in
your hand.

### Why `resolve` returned without writing: the service reads the graph and never writes to it

`ConsolidationService` touches `GraphStore` three ways — `get_relationships_for`
to plan the redirections, and the finder's `find_by_blocking_keys` and
`resolve_entity_ids` — and all three are reads. The only write it makes is
appending to the tenant's `ConsolidationLog`
([aggregates](../reference/aggregates.md)).

This is deliberate and it is the rule the whole library is built on:
extraction and consolidation *emit events*, and projections do the writing
([ADR 0004](../adr/0004-consolidation-emits-events.md), and the same split in
[README](https://github.com/tyevans/redstring/blob/main/README.md)). Two things fall out of it that matter to you here:

- **The merge is durable before it is visible.** Once `resolve` returns, the
  decision is in the log with an `event_id` (Step 7's handle). A crash between
  the append and the fold loses no judgement — replay the log and the read
  model catches up.
- **Failing to fold is the most common reason consolidation "does nothing".**
  There is no error, no warning, and no partial state: `resolve` reports a
  successful merge and the graph disagrees with it indefinitely. If a merge
  looks like it did not happen, check that something folded the event before
  you look at thresholds.

### Fold `EntitiesMerged` through `GraphProjection` to create the aliases and redirect relationships

`GraphProjection._apply_merge` does two things, in this order.

**One `Alias` row per absorbed entity.** It reads the absorbed entities back
with `get_entities` and writes an `Alias` carrying
`canonical_entity_id`, `alias_entity_id`, the absorbed entity's `name` and
`normalized_name`, the event's `occurred_at` as `merged_at`, and the whole
`merge_reason`. Three consequences worth knowing:

- **The absorbed entities are not deleted.** `GraphStore` has no
  `delete_entity` at all; an entity merged away survives as itself *plus* an
  alias. `get_entity` on an absorbed id still returns it, so resolve ids
  through `resolve_entity_ids` rather than assuming a lookup failure means
  "merged".
- **The alias id is derived, not random.** It is a `uuid5` over
  `(tenant_id, alias_entity_id)`, so folding the same log twice — or rebuilding
  from scratch — produces the same row rather than a duplicate. The merge's
  `event_id` is deliberately *not* in that hash: an entity has at most one
  canonical parent, so the pair is the row's identity.
- **`alias_name` and `alias_normalized_name` are `None` when the absorbed
  entity is not in the store.** The fold does not fail on it; the alias is
  still written, because the redirection is the part that must not be lost.

**Then the redirections.** For each `RelationshipRedirection` on the event, the
handler upserts `after` — the same edge id, moved onto the canonical entity —
or, when `after` is `None`, deletes `before.id`. A `None` means the merge
*dropped* that edge: both its endpoints were absorbed into one entity, and
`Relationship` refuses a self-loop. See [events](../reference/events.md) for
the payload's shape, and note that `redirections` is recorded on the event
rather than recomputed here, because recomputing it needs the pre-merge graph
that applying the event destroys.

After the fold:

```python
canonical = await graph_store.resolve_entity_ids(event.merged_entity_ids, tenant_id)
# every absorbed id now maps to event.canonical_entity_id
```

And the resolution is transitive — `B -> A` then `A -> C` resolves `B` to `C`,
because merging a canonical entity away is legal even though merging *into* an
alias is not (Step 7's `MergeIntoAliasError`).

The alias table is not only for this merge. Every later `DocumentExtracted`
fold resolves its edge endpoints through it
([ADR 0009](../adr/0009-the-extraction-fold-resolves-through-aliases.md)), so
re-extracting a document after a merge writes the edges onto the canonical
entity instead of quietly undoing the merge. Skipping the fold therefore costs
more than one merge: it leaves the resolution table that protects every future
extraction unwritten.

### Doing this with an event store and `replay`, versus folding the returned event directly

Both are supported and they differ in what happens when something goes wrong.

**Fold the value you are holding** when the merge and its application are one
operation in one process:

```python
await GraphProjection(graph_store).handle(event)
```

Simple, synchronous, and no feed involved — this is what `build_graph` does for
extraction. Its weakness is that an exception here leaves the log ahead of the
read model with nothing tracking the gap, so you have to re-derive the position
yourself.

**Drive it from the store** when you have a projection catching up over the
log anyway:

```python
from eventsource import replay

from redstring import GraphProjection

projection = GraphProjection(graph_store, checkpoint_repo=checkpoints, dlq_repo=dlq)
report = await replay(event_store, [projection])
assert report.failed == 0
```

`replay` reads the global feed from a position, folds every event into every
projection, and returns a `ReplayReport` of `applied`, `failed` and
`last_position`. It is the shape this project's own consolidation tests use,
and it buys three things the direct call does not:

- **A checkpoint**, so the next call resumes rather than refolding, and a crash
  between the append and the fold is recoverable without bookkeeping of yours.
- **A DLQ.** A failing event is recorded and `replay` carries on, so one
  poison event does not deny the projection every event after it.
  `report.failed` is a *count* rather than a flag, precisely so "some failed"
  cannot be read as "none did" by a truthiness check — assert on it.
- **Ordering with everything else.** A merge folded out of band can land before
  a `DocumentExtracted` that the log puts first; through the feed it cannot.

Two practical notes:

- **Keep the two steps separate in your own code** rather than wrapping
  `resolve` and the fold in one helper. Folding a stored event is the durable
  half — the events outlive the API that produced them (Step 0's upgrade
  note), and a helper that couples them makes an application that cannot
  recover by replay.
- **Rebuilds are per tenant.** `GraphProjection` deliberately raises
  `NotImplementedError` from `_truncate_read_models`, because `GraphStore` has
  no cross-tenant delete; wipe with `delete_by_tenant(tenant_id)` for each
  tenant you are rebuilding. See
  [Rebuild a projection](rebuild-a-projection.md) and
  [Drive projections from an event store](drive-projections-from-an-event-store.md).

Whichever you choose, the fold is idempotent: every write it makes is an
upsert or an idempotent delete, so applying an `EntitiesMerged` twice leaves
the same graph as applying it once. Retry freely.
