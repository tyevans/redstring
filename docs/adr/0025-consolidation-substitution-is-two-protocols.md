# ADR 0025: Consolidation's two substitution points are protocols, not classes

## Status

Accepted. Amends [`0015` consolidation gets a composed entry
point](0015-consolidation-gets-a-composed-entry-point.md), whose Decision
stands: `Consolidator` is still the composed entry point, `resolve` still
takes the same four keyword arguments with the same defaults and the same
banding. What changes is the *type* two of those arguments are declared
against.

**Why this is an ADR:** it changes the public surface (`__all__` gains two
names) and it adds plug-in protocols, both of which
`.claude/rules/definition-of-done.md` names.

## Context

`Consolidator.resolve` documents two of its parameters as substitution points:

> `finder`: Overrides the default built from this consolidator's stores.
> **Supply one to change the weights or the blocking.**
>
> `adjudicator`: Asked about the middle band, in batches.

Both were annotated against concrete classes — `CandidateFinder` and
`Adjudicator` — and both classes bind collaborators in their constructors.
`CandidateFinder.__init__` requires a `GraphStore` and optionally a
`VectorStore`, a `FeatureWeights` and a flag; `Adjudicator.__init__` requires
an `LlmProvider`.

So the two substitutions the docstring invites are exactly the two the
annotation obstructs. A caller whose blocking should come from a search index
they already run does not have a `GraphStore` to give the base class, and a
caller putting the ambiguous band to a human review queue does not have an
`LlmProvider`. Under `mypy --strict` — which is how this repo's own gate runs
and what its `py.typed` marker promises downstream — the only route is to
subclass a class, satisfy a constructor with collaborators that will never be
called, and override the one method actually wanted.

This was measured rather than argued. Assigning a duck-typed substitute to
each annotation:

    x: CandidateFinder = SearchIndexCandidates([])   # error: incompatible types
    x: CandidateSource = SearchIndexCandidates([])   # Success

Inviting a substitution and typing it against an implementation are different
promises, and the documentation was making the first while the code made the
second.

## Decision

**Declare `CandidateSource` and `MergeAdjudicator` as `runtime_checkable`
protocols in `redstring/consolidation/protocols.py`, annotate both `resolve`
methods against them, and export them.** `CandidateFinder` and `Adjudicator`
are unchanged, remain the defaults, and satisfy the protocols structurally.

Each protocol has exactly one method, which is what makes them cheap enough to
be worth having at all: `candidates` and `adjudicate`.

**They live in `consolidation/`, not in `ports/`.** Both traffic in
`ScoredCandidate`, a consolidation type, so a port would have to import
upward through the layer contract. That is the layering telling the truth
about what these are — not store or provider boundaries the whole library is
built on, but the two decisions *within* consolidation a caller might
reasonably own: which candidates to consider, and who settles the ambiguous
ones. The precedent is `extraction/protocols.py`, which holds `Chunker` for
the same reason.

**Two obligations are stated in the protocols rather than left to the
defaults' behaviour**, because they are properties a substitute can violate
without erroring:

- `candidates` returns results **best first under a total order**.
  `CandidateFinder` breaks score ties by ascending entity id as a string
  precisely so two runs over one graph agree. A substitute sorting on score
  alone leaves a cutoff inside a tie to be decided by whatever order its
  backend returned — which surfaces as an intermittently different merge, not
  as an error.
- `adjudicate` returns **exactly one verdict per candidate, positionally
  aligned**, with `None` where it has no answer. `None` is not a formality: a
  provider outage and a considered "not the same" are different facts, and
  collapsing them turns an outage into a corpus that appears to hold no
  duplicates.

## Consequences

**The docstring's offer becomes true**, and the two named substitutions are
now writable as ordinary classes holding ordinary state.

**The enforcement is the type checker, not the test suite**, and that is worth
knowing before reading `tests/unit/consolidation/test_substitution.py` as
stronger evidence than it is. Python does not enforce annotations, so a
duck-typed substitute always *ran*; what those tests add beyond the two
`isinstance` assertions is that a substitute holding none of the defaults'
collaborators genuinely drives a merge, is genuinely consulted about the band,
and that its `None` is not read as a yes.

**A third implementation of either protocol has no compliance suite.** The
store ports each have one under `tests/compliance/`, and
`.claude/rules/recurring-defects.md` §1 is about exactly what happens without
one: two implementations of a contract diverge and nothing fails, because each
one's tests assert its own behaviour. The two obligations above — a total
order, and positional alignment with `None` for no answer — are the shared
claims such a suite would hold, and they are currently prose in a protocol
docstring. This is filed as **B101** rather than built now: there is one
implementation of each, and a compliance suite written against a single
implementation is tuned to it rather than derived from the contract.

**`ScoredCandidate` is now load-bearing on the public surface** in a way it
was not. It was exported as part of 0015's closure, describing what the
default finder returned; it is now the *input* type a caller constructs when
supplying their own. Its shape is harder to change than it was.
