# ADR 0040: Overlap-aware name similarity

**Status:** accepted.

**Decision:** `domain.similarity.string_similarity` is the maximum of
Jaro-Winkler and a token overlap coefficient capped at `CONTAINMENT_CEILING`.
`CONTAINMENT_CEILING` sits at or above `LOW_SIMILARITY` and strictly below
`HIGH_SIMILARITY`.

## Context

Jaro-Winkler rewards a shared prefix, which is exactly backwards for the most
common alias shape in prose: a name qualified by a leading title or epithet.
"Dr. Grant" against "Grant" shares no prefix at all, because the qualifier
sits in front of the name it is supposed to be modifying. The pair still
blocks correctly on the entity-type key — blocking does not look at the name
— and scoring is what rejects it, so the adjudication band the pipeline pays
a model call to reach never sees the pair.

Widening `LOW_SIMILARITY` to catch these pairs is not the alternative it looks
like. The band exists to avoid asking the model about every pair a type-key
block produces; pulling the floor down far enough to catch a title-qualified
name catches most of that block along with it; and the quadratic cost the
band exists to avoid is the cost it exists to avoid regardless of which
feature produced the low score.

### Why not a fourth feature

`combined_score` renormalizes over whichever features are present, so adding
a fourth moves every score in the corpus, not only the pairs the new feature
has an opinion about. Strengthening the name feature instead moves only the
pairs the name feature was already wrong about — everything else is
unaffected.

### Why the overlap coefficient rather than Jaccard

Jaccard divides by the union of the two token sets; the overlap coefficient
divides by the smaller set. A title added to a name is not evidence against
the match, so a qualifier lengthening one side should not be able to drag the
score down the way it would under a union-sized divisor. The overlap
coefficient scores a strict subset relationship at 1.0 regardless of how many
extra tokens the superset carries.

### Why not `domain.tokenize.tokenize`

That tokenizer drops stopwords for BM25 ranking. Reusing it for name
similarity would couple a merge decision to a retrieval tokenizer's
vocabulary — a stopword list tuned for search relevance has no reason to
agree with what belongs in a person's name, and a change made for retrieval
would silently move merge behavior.

## Consequences

**Containment buys a model call, never a merge.** This is the two-sided
relation `LOW_SIMILARITY <= CONTAINMENT_CEILING < HIGH_SIMILARITY`: the
ceiling is high enough to lift a strict-subset pair out of automatic
rejection, and strictly below `HIGH_SIMILARITY` so it can never on its own
trigger an automatic merge. `{smith}` is a subset of `{john, smith}`, so the
strictly-below half is load-bearing — without it, every shared surname would
merge automatically. Re-check this relation if either threshold ever moves.

**Adjudication volume rises for callers with an adjudicator wired.** This is
the intended trade, not a side effect: pairs that used to be silently
rejected on a prefix-penalized score now reach the model. The trade is bounded
by construction — containment only fires on a strict token subset, which is a
narrow slice of any type-key block.

**`string_similarity` keeps both properties it documented before this
change**: it is symmetric, and it returns `1.0` if and only if the two names
are equal.

## What this does not do

It does not help a *partial* token overlap — two names sharing one token out
of two, neither a subset of the other, score identically whether the pair is
one person's alias or two different people. See `BACKLOG.md` B-ALIAS-1.

## Verdicts on existing ADRs

[`0010` one total order for preference](0010-one-total-order-for-preference.md)
stands: this changes a score that feeds into blocking and adjudication, not
the total order that decides which mapping of a thing survives.

[`0015` consolidation gets a composed entry point](0015-consolidation-gets-a-composed-entry-point.md)
stands: nothing about the entry point or its treatment of an absent graph
signal changes here.

[`0006` the public surface is gated](0006-the-public-surface-is-gated.md)
stands: `string_similarity`'s signature is unchanged and no export moves.
