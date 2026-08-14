"""The shapes consolidation names for itself, rather than borrowing whole ports.

Three protocols, two kinds. `CandidateSource` and `MergeAdjudicator` are the
substitution points -- the decisions a caller might own. `ConsolidationGraph`
is not a substitution point at all: it is a *narrowing*, the subset of
`GraphStore` that blocking-and-scoring actually calls, named so a signature can
withhold what it does not need.

What puts all three in one module is that each is a statement about what
consolidation needs, not about what a store offers.

## The two substitution points

`Consolidator.resolve` has always invited substitution -- "supply one to
change the weights or the blocking" -- but its parameters were annotated
against the *concrete* `CandidateFinder` and `Adjudicator`. Inviting a
substitution and typing it against an implementation are different promises,
and the gap was paid by the caller: replacing the blocking with your own
search index meant subclassing a class whose `__init__` demands a
`GraphStore`, an optional `VectorStore`, a `FeatureWeights` and a flag, then
overriding the one method you actually wanted. A human review queue standing
in for the model meant subclassing a class that requires an `LlmProvider` to
construct and never calling it.

Both are single-method interfaces, which is what makes the protocols cheap
enough to be worth having. The concrete classes remain the defaults and are
unchanged; they satisfy these structurally, with no base class and no
registration.

## Why these live here and not in `ports/`

For the substitution pair the layering settles it: `ports/` sits below the
sibling band, and both traffic in `ScoredCandidate`, which is a consolidation
type. A port for either would have to import upward. That is the layering
telling the truth about what these are -- not store or provider boundaries the
whole library is built on, but the two decisions *within* consolidation that a
caller might reasonably own.

`ConsolidationGraph` is here for a different reason, and it is worth being
plain that the layering does **not** decide it: it names only capabilities
`ports/graph_store.py` already declares, so it could sit there and compile.
What rules that out is what it would make the port into. The port describes
the store; a composition describes one *consumer's* subset of it, and a
`ports/` module carrying those accumulates one protocol per caller. ADR 0016
rejected exactly that under "consumer-owned protocols everywhere" -- it scales
with the number of callers rather than the number of capabilities -- while
leaving room for "a slice these five cannot express". A consumer-shaped slice
belongs beside the consumer that shaped it, which is here.

## What a substitute owes

`CandidateSource.candidates` must return its results **best first, under a
total order**. `CandidateFinder` breaks score ties by ascending entity id as
a string precisely so that two runs over one graph agree; a substitute that
sorts by score alone leaves `resolve`'s banding to depend on whatever order
its own backend happened to return, which is a difference that shows up as
an intermittently different merge rather than as an error.

`MergeAdjudicator.adjudicate` must return **exactly one verdict per candidate,
positionally aligned**, and `None` where it has no answer. `None` is not a
formality: a provider outage and a considered "not the same" are different
facts, and collapsing them turns an outage into a corpus that appears to hold
no duplicates. A substitute that cannot answer for a pair says `None` rather
than `False`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from redstring.ports.graph_store import AliasStore, EntityReader, RelationshipStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redstring.consolidation.candidates import ScoredCandidate
    from redstring.consolidation.policy import AdjudicationVerdict
    from redstring.domain.entity import Entity


@runtime_checkable
class ConsolidationGraph(EntityReader, AliasStore, RelationshipStore, Protocol):
    """The three graph capabilities blocking-and-scoring needs, and no more.

    Not a fourth store capability -- it adds no method and regroups nothing.
    It is a *composition* of three of `GraphStore`'s five, named so that a
    signature can say "reads entities, resolves aliases, reads edges" without
    also saying "and may wipe a tenant".

    `docs/adr/0016-graph-store-is-five-capabilities.md` left `CandidateFinder`
    on the whole port, reasoning that a collaborator spanning three
    capabilities is honestly typed by the composed one. Three of five is not
    five, and the two it does not span are the two that matter most: it holds
    `EntityWriter` while its own docstring promises it never writes, and it
    holds `TenantPurge`, whose whole stated purpose is to make "this
    collaborator can wipe a tenant" a visible fact about a signature. A
    capability that is load-bearing only when it is *absent* cannot be granted
    by default without retiring it.

    0016 also declined a bespoke three-method protocol here and said to
    revisit "if a caller ever needs a slice these five cannot express". This
    is that slice, and the form matters: naming a caller's *combination* of
    capabilities keeps the port describing the store, where inventing a
    three-method interface would have started describing its callers.

    It is declared here rather than in `ports/graph_store.py` because it is a
    statement about a consumer -- see this module's docstring for why that is
    the deciding fact and the layering is not.
    """


@runtime_checkable
class CandidateSource(Protocol):
    """Supplies the entities that might be duplicates of a subject, scored."""

    async def candidates(
        self, subject: Entity, *, minimum_score: float = 0.0
    ) -> list[ScoredCandidate]:
        """Everything worth considering as a duplicate of `subject`, best first.

        Args:
            subject: The entity being consolidated around.
            minimum_score: Drop anything scoring below this. `resolve` passes
                its `low` threshold here, so a candidate under it is never
                built rather than being built and then discarded.

        Returns:
            Scored candidates in descending score. Ties must be broken by a
            further total order -- `CandidateFinder` uses ascending entity id
            as a string -- so that a cutoff falling inside a tie is decided
            the same way on every run.
        """
        ...


@runtime_checkable
class MergeAdjudicator(Protocol):
    """Settles the candidates whose score does not settle them."""

    async def adjudicate(
        self, subject: Entity, candidates: Sequence[ScoredCandidate]
    ) -> list[AdjudicationVerdict | None]:
        """One verdict per candidate, positionally aligned, `None` for no answer.

        Returning fewer verdicts than there are candidates is not permitted:
        the alignment between question and answer is the whole contract, and a
        short list silently records an answer about one pair against another.
        An implementation that cannot answer for some pairs returns `None` in
        those positions and keeps the length.
        """
        ...

    async def adjudicate_many(
        self, work: Sequence[tuple[Entity, Sequence[ScoredCandidate]]]
    ) -> list[list[AdjudicationVerdict | None]]:
        """One verdict list per entry in `work`, same order, same length.

        `resolve_many` calls this rather than `adjudicate` in a loop: the
        whole reason it exists is to batch across subjects, and a caller
        that fanned `adjudicate` out itself would be back to one model call
        per subject regardless of what this method could do. A subject with
        no candidates gets `[]` and keeps its slot -- the caller re-pairs by
        position.

        **Required, not optional -- `resolve_many` calls only this method,
        never `adjudicate`.** An optional method with a per-subject fallback
        inside `resolve_many` would be a branch nothing in this tree ever
        exercises, which is exactly the inert-code shape
        `.claude/rules/recurring-defects.md` #3 warns about; better a
        required method with a one-line migration than a silent dead path.

        **Migration for an existing implementation that predates this
        method** (added alongside `resolve_many`, so any substitute written
        against the single-subject `adjudicate` needs this to keep
        satisfying `MergeAdjudicator`): delegate per subject and accept that
        you lose the cross-subject batching `resolve_many` exists to get --

        ```python
        async def adjudicate_many(self, work):
            return [await self.adjudicate(subject, candidates) for subject, candidates in work]
        ```

        `tests/unit/consolidation/test_substitution.py`'s `ReviewQueue` is
        exactly this: a foreign implementation with no notion of a
        cross-subject batch, satisfying the protocol by delegating.
        """
        ...
