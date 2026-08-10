"""Finding the entities worth comparing, and scoring them.

Two steps, and keeping them apart is the point:

1. **Block.** `GraphStore.find_by_blocking_keys` returns everything sharing a
   key with the subject. Cheap, lossy, and the only thing standing between
   consolidation and a quadratic scan of the tenant.
2. **Score.** Each candidate gets a name score always, an embedding score when
   a `VectorStore` is supplied and both entities have vectors, and a graph
   score when the neighbours are asked for.

Nothing here writes, and nothing here decides. `CandidateFinder.candidates`
returns scored pairs; `redstring.consolidation.policy` decides what to do with
them and `ConsolidationService` emits the event. A finder that also merged
would make "what would this merge?" unanswerable without merging.

## "Never writes" is now a fact about the signature

`CandidateFinder` took a whole `GraphStore` while its docstring promised it
never writes -- so the promise was prose, and `EntityWriter` and `TenantPurge`
were both in reach of a class that wanted neither. It takes a
`ConsolidationGraph` -- the three capabilities it does call, composed, declared
in `redstring.consolidation.protocols` beside the other shapes this package
names for itself. The vector side is narrowed the same way, to `VectorReader`.

## Aliases are excluded, and that is not an optimisation

An entity already merged away is not a merge candidate: `ConsolidationLog`
would refuse it with `DoubleMergeError`, and proposing it would produce a
candidate list whose entries cannot be acted on. The subject itself is excluded
for the same reason -- `EntitiesMerged` refuses a self-merge outright.

Both exclusions use `GraphStore.resolve_entity_ids`, one call for the whole
block, so a chain `B -> A -> C` correctly proposes `C` and never `B`.

## The embedding score comes from the store, not from a provider

`VectorStore.search` already returns scores on `0..1` with the port's stated
scale. Re-deriving them here would mean holding an embedding provider, a second
scale, and a second opportunity to disagree with what the store ranked by.

The subject's own vector is read with `get`; when the tenant has no vector for
it, the embedding feature is simply absent -- `None`, not `0.0`, which
`combined_score` treats as "not computed" rather than as evidence of
disagreement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from redstring.domain.normalization import normalize_name
from redstring.domain.similarity import (
    FeatureWeights,
    SimilarityFeatures,
    combined_score,
    graph_similarity,
    string_similarity,
)

if TYPE_CHECKING:
    from redstring.consolidation.protocols import ConsolidationGraph
    from redstring.domain.entity import Entity
    from redstring.domain.ids import EntityId, TenantId
    from redstring.ports.vector_store import VectorReader

#: How many nearest vectors the embedding step asks for.
#:
#: The block, not the tenant, decides which candidates are scored -- this only
#: bounds how far down the ranking a candidate's embedding score is still
#: found. A candidate in the block but outside the top `k` gets no embedding
#: feature rather than a zero, which is the honest reading: the store was not
#: asked about it.
EMBEDDING_SEARCH_K = 50


@dataclass(frozen=True)
class ScoredCandidate:
    """One entity that might be the same thing as the subject.

    Carries the per-signal scores as well as the combined one, because a
    threshold decision that cannot be explained is a threshold nobody can
    tune -- and because "the name matched but nothing else did" and "everything
    matched a little" reach the same number by different routes.
    """

    entity: Entity
    features: SimilarityFeatures
    score: float


class CandidateFinder:
    """Blocks and scores. Never writes, never decides."""

    def __init__(
        self,
        graph_store: ConsolidationGraph,
        *,
        vector_store: VectorReader | None = None,
        weights: FeatureWeights | None = None,
        use_graph_signal: bool = True,
    ) -> None:
        """`vector_store` is optional and its absence is not an error.

        A deployment without embeddings still consolidates on names and graph
        structure; requiring the store would make the cheap configuration
        impossible rather than merely less accurate.

        `use_graph_signal` exists because the graph feature costs two reads
        per subject and two per candidate -- `get_relationships` for the edges
        and a batched `get_entities` for the neighbours they name -- which is
        the expensive part of scoring. Turning it off is a caller's trade, not
        a silent degradation -- the feature goes absent and `combined_score`
        renormalizes, so scores stay on the same scale.

        It was one read per side until neighbours started being compared by
        name; `_graph_feature` has the argument for why the cheaper comparison
        was answering a question nobody asked.
        """
        self._graph = graph_store
        self._vectors = vector_store
        self._weights = weights or FeatureWeights()
        self._use_graph_signal = use_graph_signal

    async def candidates(
        self, subject: Entity, *, minimum_score: float = 0.0
    ) -> list[ScoredCandidate]:
        """Everything blocked with `subject`, scored, best first.

        Ties in score are broken by ascending entity id as a string, so the
        order is total and two runs over one graph agree. Without that, `k`
        cutting through a tie -- or a caller taking `[0]` -- would depend on
        dictionary iteration.
        """
        blocked = await self._block(subject)
        if not blocked:
            return []

        embedding_scores = await self._embedding_scores(subject)
        subject_neighbours = await self._neighbour_names(subject.id, subject.tenant_id)

        scored = []
        for candidate in blocked:
            features = SimilarityFeatures(
                name=string_similarity(subject.name, candidate.name),
                embedding=embedding_scores.get(candidate.id),
                graph=await self._graph_feature(subject_neighbours, candidate),
            )
            score = combined_score(features, self._weights)
            if score >= minimum_score:
                scored.append(ScoredCandidate(candidate, features, score))

        return sorted(scored, key=lambda found: (-found.score, str(found.entity.id)))

    async def _block(self, subject: Entity) -> list[Entity]:
        """The subject's block, with itself and every alias removed."""
        keys = sorted(subject.blocking_keys or ())
        if not keys:
            return []

        grouped = await self._graph.find_by_blocking_keys(keys, subject.tenant_id)
        # Deduplicated by id: an entity carrying three of the keys appears
        # under each, and scoring it three times would be wasted work and a
        # candidate list a caller has to deduplicate itself.
        found = {
            entity.id: entity
            for entities in grouped.values()
            for entity in entities
            if entity.id != subject.id
        }
        if not found:
            return []

        # One resolution call for the whole block. An entity already merged
        # away cannot be merged again -- `ConsolidationLog` would refuse it --
        # so proposing it would produce a candidate nobody can act on.
        canonical = await self._graph.resolve_entity_ids(list(found), subject.tenant_id)
        # `==`, never `is`. Both adapters happen to hand back the *same* `UUID`
        # object for an id that is not an alias, so `is` would pass every test
        # in this repo -- and would then return an empty candidate list against
        # any adapter that rebuilt the id, which is consolidation finding no
        # duplicates in silence. A cosmic-ray mutant rewriting this as `is`
        # survived until `test_resolution_by_value_not_by_identity` pinned it.
        return [entity for entity_id, entity in found.items() if canonical[entity_id] == entity_id]

    async def _embedding_scores(self, subject: Entity) -> dict[EntityId, float]:
        """Nearest-vector scores by candidate id, or empty when unavailable.

        Empty rather than raising for every reason it can be empty -- no store
        configured, no vector for this subject -- because a missing embedding
        must weaken the evidence, not stop the run. `combined_score` reads an
        absent feature as "not computed" and renormalizes.
        """
        if self._vectors is None:
            return {}
        record = await self._vectors.get(subject.id, subject.tenant_id)
        if record is None:
            return {}
        matches = await self._vectors.search(record.vector, subject.tenant_id, k=EMBEDDING_SEARCH_K)
        # The subject's own vector is the nearest to itself and is in here.
        # It is *not* filtered out: `_block` never proposes the subject as its
        # own candidate, so nothing ever looks its score up, and a guard no
        # input reaches describes a situation that cannot arise. Removing it
        # was prompted by three cosmic-ray survivors on the comparison, which
        # is what an unreachable guard looks like from outside.
        return {match.entity_id: match.score for match in matches}

    async def _graph_feature(
        self, subject_neighbours: list[str] | None, candidate: Entity
    ) -> float | None:
        """The graph signal for one candidate, or `None` when there is none.

        Three cases, and both of the last two have been wrong.

        1. **The signal is off.** `subject_neighbours is None`; nobody asked.
        2. **At least one side has neighbours.** Jaccard overlap of the two
           neighbour *names*, including `0.0` when they are disjoint -- that
           is a real finding about two entities that both have structure and
           share none of it.
        3. **Neither side has any neighbours.** Previously this also scored
           `0.0`, and `graph_similarity` documents that choice deliberately:
           "nothing is known about either" must not read as "these agree
           perfectly", so two empty sets are not `1.0`. That reasoning is
           right and the conclusion did not follow, because there was a third
           option the module already uses everywhere else -- **absent**.

        Case 3 scoring `0.0` manufactures evidence out of an absence, which is
        the exact thing case 1 exists to avoid. And it is not a corner: two
        entities extracted from one document before any relationship is
        written have no neighbours, which is the *first* thing a new caller
        consolidates.

        Measured, on two entities named "Ada Lovelace" with no edges: the
        combined score was **0.7143** with the graph signal on and 1.0 with it
        off. `LOW_SIMILARITY` is 0.75, so an identical-name pair was not merged
        and not even adjudicated -- it was rejected, silently, by a feature
        that had nothing to say. Returning `None` lets `combined_score`
        renormalize over the features that do, which puts it back at 1.0.

        The reason no test caught it: every existing consolidation test builds
        its finder with `use_graph_signal=False`, so nothing in the suite ever
        reached this branch with two isolated entities.

        ## Case 2 compared ids, and so could not see agreement across documents

        Fixing case 3 left case 2 stating its defence too broadly. "Both have
        structure and share none of it" is a finding when the two neighbour
        sets *could* have overlapped, and `extraction.mapping.entity_id_for`
        guarantees they could not: it seeds its `uuid5` chain with `source_id`
        on purpose, so that deciding `doc-1`'s "Ada" and `doc-2`'s "Ada" are
        one person stays consolidation's judgement rather than something
        extraction settles by choosing an id.

        Every entity id is therefore namespaced by document, and so is every
        neighbour id. Two extractions of one neighbour from two documents have
        different ids **by construction**, which made the Jaccard numerator
        structurally empty for exactly the pairs consolidation exists to find.
        The result was the same 0.7143 as case 3 and the same silent rejection
        below `LOW_SIMILARITY` -- reached this time by every cross-document
        duplicate that has *any* edges at all, which is the normal case rather
        than the first-extraction one. It was also circular: the evidence that
        would have raised the score was the merge the score was blocking.

        Worse than the cutoff, and the part that shows the feature was close
        to inverted for its main use: with the signal on, a cross-document
        pair could not reach `HIGH_SIMILARITY` (0.92) at all. A perfect name
        and a perfect embedding ceiling out at 0.8 against `graph=0.0`, so
        auto-merge across documents was unreachable regardless of the
        evidence.

        So neighbours are compared by normalized name, which is the property
        two extractions of one neighbour actually share. Nothing branches on
        `source_id`: comparing names is simply a comparison the id namespacing
        cannot defeat, and it leaves the within-document discrimination case 2
        was written for exactly as it was -- two different neighbours have two
        different names whichever documents they came from.

        What it costs is that two *distinct* neighbours sharing a name now
        read as agreement. That is the same fallibility `string_similarity`
        already has ("fooled by two different people with the same name"), now
        reaching a second feature, and it is bounded by the graph weight --
        0.2 by default. `BACKLOG.md` B123 carries the sharper key that would
        not have it, and B124 the alias resolution this does not do.
        """
        if subject_neighbours is None:
            return None
        candidate_neighbours = await self._neighbour_names(candidate.id, candidate.tenant_id) or ()
        if not subject_neighbours and not candidate_neighbours:
            return None
        return graph_similarity(subject_neighbours, candidate_neighbours)

    async def _neighbour_names(self, entity_id: EntityId, tenant_id: TenantId) -> list[str] | None:
        """Normalized names adjacent to `entity_id`, or `None` when off.

        `None` and `[]` are different and both occur: the first means nobody
        asked, the second means the entity has no neighbours. `graph_similarity`
        would score two of the second at 0.0, which is right, and scoring two
        of the first at 0.0 would be evidence invented out of a configuration
        flag.

        Names rather than ids, and one batched read rather than a loop: see
        `_graph_feature` for why the ids cannot answer the question, and
        `GraphStore.get_entities`, which exists so that consolidation is not a
        loop over `get_entity`.

        An edge naming an entity this tenant does not hold contributes
        nothing, because `get_entities` omits ids it cannot find. That is a
        change from returning the dangling id: a subject whose every edge
        dangles now has *no* neighbours rather than a set of unmatchable ones,
        so a pair of them reaches case 3 and scores absent instead of `0.0`.
        Both readings are defensible and this one is consistent with the rest
        of the method -- an id nothing can be learned about is not evidence of
        disagreement.
        """
        if not self._use_graph_signal:
            return None
        edges = await self._graph.get_relationships(entity_id, tenant_id)
        neighbour_ids = {
            other
            for edge in edges
            for other in (edge.source_entity_id, edge.target_entity_id)
            if other != entity_id
        }
        if not neighbour_ids:
            return []
        neighbours = await self._graph.get_entities(sorted(neighbour_ids), tenant_id)
        return [normalize_name(neighbour.name) for neighbour in neighbours]
