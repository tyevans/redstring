# ADR 0034: Neighbours are compared by name, because ids are namespaced by document

## Status

Accepted. **Amends
[`0015` consolidation gets a composed entry point](0015-consolidation-gets-a-composed-entry-point.md)**,
which fixed one half of this defect and stated the other half's defence too
broadly. 0015's decision — that an *absent* graph signal must not read as zero
— stands entirely and is not weakened here. What is amended is the sentence
that followed it: "a disjoint neighbourhood where at least one side *has*
structure still scores `0.0`, because that is a real finding about two
entities." That is true within one document and false across two, for a
reason 0015 did not have in view.

[`0009` the extraction fold resolves through aliases](0009-the-extraction-fold-resolves-through-aliases.md)
**stands**, and is the ADR that makes this one necessary: the per-document id
namespacing is deliberate, correct, and not up for revision.
[`0010` one total order for preference](0010-one-total-order-for-preference.md),
[`0004` consolidation emits events](0004-consolidation-emits-events.md) and
[`0016` `GraphStore` is five capabilities](0016-graph-store-is-five-capabilities.md)
all **stand**. No event payload, no port and no protocol changed.

## Context

`CandidateFinder` scores three signals. The graph signal was the Jaccard
overlap of two entities' neighbour sets, keyed on `EntityId`.

`extraction.mapping.entity_id_for` derives an entity's id as a `uuid5` chain
seeded with the tenant, then `source_id`, then the type, then the normalized
name. `source_id` is in that chain on purpose, and its docstring says why:
deciding that `doc-1`'s "Ada" and `doc-2`'s "Ada" are one person is
consolidation's judgement, recorded as an auditable and undoable
`EntitiesMerged`, rather than something extraction settles by choosing an id.
ADR 0009 depends on that property.

The consequence for the graph signal had not been drawn. **Every entity id is
namespaced by document, therefore so is every neighbour id, therefore two
extractions of one neighbour from two documents have different ids by
construction.** A Jaccard over ids has a structurally empty numerator for any
cross-document pair — not because the two entities disagree about their
surroundings, but because agreement was not expressible in the key being
compared.

So the feature reported maximum disagreement on precisely the pairs
consolidation exists to find, and its disagreement was an artefact of an id
scheme rather than a finding about the world. It was also circular: the
evidence that would have raised the score was a merge of the neighbours, which
the score was blocking.

Two consequences, both of which had been in the field:

- An identical-name cross-document pair with no embedding scored **0.7143**,
  below `LOW_SIMILARITY` (0.75), so it was **rejected outright rather than
  adjudicated** — the same number and the same silent rejection 0015 fixed for
  the no-neighbours case, reached this time by every cross-document duplicate
  with *any* edges at all. 0015 corrected the first-extraction case; this is
  the ordinary one.
- With the signal on, a cross-document pair could not reach `HIGH_SIMILARITY`
  (0.92) **at all**. A perfect name and a perfect embedding ceiling out at 0.8
  against a structural `graph=0.0`, so auto-merge across documents was
  unreachable regardless of the evidence. The feature was not merely weak for
  its main use case; it was close to inverted.

The reason the suite did not catch it is the reason 0015 gives for its own
blind spot, one layer along. Every consolidation test builds its entities
through a builder whose `source_id` defaults to `"doc-1"`, so the whole suite
— including the two tests 0015 added specifically about disjoint and empty
neighbourhoods — was single-document. The tests were not weak; the *fixture*
could not express the case. That is CLAUDE.md's standing question ("what else
would pass this?") applied to a default argument nobody was looking at.

It was found from outside: a downstream consumer hit it, worked around it with
a score floor, and reported the mechanism.

## Decision

**Neighbours are compared by normalized name, not by id.**

`CandidateFinder._neighbours` becomes `_neighbour_names`: it reads the
adjacent ids from `get_relationships` as before, then makes one batched
`get_entities` call and returns `normalize_name(entity.name)` for each.
`graph_similarity` is retyped from `Collection[EntityId]` to
`Collection[Hashable]` — it genuinely is set overlap, and what identifies a
neighbour is the caller's decision.

**Nothing branches on `source_id`.** The obvious alternative was to detect
that two entities come from different documents and return `None` there, on
the grounds that cross-document disjointness is absence rather than
disagreement. It was rejected: it adds a second absent-versus-zero rule to a
method that already carries one, it makes the feature's meaning depend on
provenance, and it still yields *no* positive signal across documents — the
ceiling problem above would survive it. Comparing names is not a special case
for cross-document pairs; it is simply a comparison the id namespacing cannot
defeat, and it collapses to the old behaviour within a document.

The discrimination 0015 protected is therefore kept intact and by the same
mechanism as before: two different neighbours have two different names, in one
document or across two.

## Consequences

**Merge outcomes change for callers on 0.4.0 with the graph signal on.**
Cross-document pairs that scored 0.7143 and were dropped before adjudication
now score up to 1.0. This is the intended correction and it is a behaviour
change, not a bug fix from the caller's side: a corpus consolidated before and
after this version can differ, in the direction of merging more. Unlike 0015,
this one ships against a released version, so it is a minor bump and the
change belongs in the release notes rather than in a footnote about nobody
being affected yet.

**A downstream score floor is not made redundant.** The consumer that reported
this admitted exact-name pairs to adjudication with a floor derived from
`FeatureWeights`. That remains defensible on its own terms — it is a statement
about which pairs deserve a model's attention, not a patch for this defect —
and it should not be removed as part of taking this fix.

**The graph feature can now be fooled by a name collision.** Two distinct
neighbours sharing a name read as agreement. This is the fallibility
`string_similarity` already documents reaching a second feature, bounded by
the graph weight, and it is the price of the only key available that survives
the namespacing. `BACKLOG.md` B123 carries what a sharper key would need; B124
carries the alias resolution this deliberately does not do, and why it is
priced rather than done.

**The signal costs two reads per side rather than one.** `get_relationships`
plus a batched `get_entities`. `use_graph_signal` exists for exactly this
trade and its docstring is updated; `GraphStore.get_entities` exists so that
consolidation is not a loop over `get_entity`, which is what makes the second
read one round trip rather than *n*.

**A dangling edge now contributes nothing rather than an unmatchable id.**
`get_entities` omits ids it cannot find, so an entity whose every edge points
at something this tenant does not hold has *no* neighbours instead of a set of
ids that can never match. A pair of those reaches the empty-versus-empty case
and scores absent rather than `0.0`, which is consistent with the rest of the
method: an id nothing can be learned about is not evidence of disagreement.

**The fixture, not the assertions, was the blind spot — and it still is one
everywhere else.** `tests/unit/consolidation/conftest.py::entity` defaults
`source_id="doc-1"`, so any consolidation test that does not pass it
explicitly is single-document and cannot see a cross-document defect of any
kind. Two tests here now pass it explicitly. The default is deliberate and
stays (`DocumentExtracted` requires an attribution), but it means the standing
question to ask of a consolidation test is not only "what other implementation
would pass this?" but "how many documents does this test have in it?"
