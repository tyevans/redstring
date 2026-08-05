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

from redstring.domain.similarity import (
    FeatureWeights,
    SimilarityFeatures,
    combined_score,
    graph_similarity,
    string_similarity,
)

if TYPE_CHECKING:
    from redstring.domain.entity import Entity
    from redstring.domain.ids import EntityId, TenantId
    from redstring.ports.graph_store import GraphStore
    from redstring.ports.vector_store import VectorStore

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
        graph_store: GraphStore,
        *,
        vector_store: VectorStore | None = None,
        weights: FeatureWeights | None = None,
        use_graph_signal: bool = True,
    ) -> None:
        """`vector_store` is optional and its absence is not an error.

        A deployment without embeddings still consolidates on names and graph
        structure; requiring the store would make the cheap configuration
        impossible rather than merely less accurate.

        `use_graph_signal` exists because the graph feature costs one
        `get_relationships_for` per subject and one per candidate, which is the
        expensive part of scoring. Turning it off is a caller's trade, not a
        silent degradation -- the feature goes absent and `combined_score`
        renormalizes, so scores stay on the same scale.
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
        subject_neighbours = await self._neighbours(subject.id, subject.tenant_id)

        scored = []
        for candidate in blocked:
            features = SimilarityFeatures(
                name=string_similarity(subject.name, candidate.name),
                embedding=embedding_scores.get(candidate.id),
                graph=(
                    None
                    if subject_neighbours is None
                    else graph_similarity(
                        subject_neighbours,
                        await self._neighbours(candidate.id, candidate.tenant_id) or (),
                    )
                ),
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

    async def _neighbours(self, entity_id: EntityId, tenant_id: TenantId) -> list[EntityId] | None:
        """Ids adjacent to `entity_id`, or `None` when the signal is off.

        `None` and `[]` are different and both occur: the first means nobody
        asked, the second means the entity has no neighbours. `graph_similarity`
        would score two of the second at 0.0, which is right, and scoring two
        of the first at 0.0 would be evidence invented out of a configuration
        flag.
        """
        if not self._use_graph_signal:
            return None
        edges = await self._graph.get_relationships(entity_id, tenant_id)
        return [
            other
            for edge in edges
            for other in (edge.source_entity_id, edge.target_entity_id)
            if other != entity_id
        ]
