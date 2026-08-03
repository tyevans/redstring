"""
Graph-based similarity computation for entity consolidation.

This module computes structural similarity between entities based on
their neighborhoods in the knowledge graph (Neo4j).

Graph similarity examines:
- Shared neighbors (Jaccard similarity of neighbor sets)
- Relationship patterns (similar relationship types)
- Graph structure (how entities connect to others)

This is part of Stage 3 of the consolidation pipeline, providing
graph-based similarity scores to complement embedding similarity.

Example usage:
    >>> service = GraphSimilarityService(neo4j_driver)
    >>> similarity = await service.compute_similarity(entity_a_id, entity_b_id)
    >>> print(f"Graph similarity: {similarity:.3f}")
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

from kg_builder.schemas.similarity import (
    GraphSimilarityScores,
    SimilarityScore,
    SimilarityType,
)

if TYPE_CHECKING:
    from neo4j import AsyncDriver

logger = logging.getLogger(__name__)


@dataclass
class GraphNeighborhood:
    """
    Represents an entity's neighborhood in the graph.

    Contains information about the entity's immediate connections
    including both incoming and outgoing relationships.

    Attributes:
        entity_id: The entity whose neighborhood this represents
        outgoing_neighbors: Set of entity IDs this entity points to
        incoming_neighbors: Set of entity IDs that point to this entity
        relationship_types: Mapping of neighbor_id to relationship type
    """

    entity_id: UUID
    outgoing_neighbors: set[UUID] = field(default_factory=set)
    incoming_neighbors: set[UUID] = field(default_factory=set)
    relationship_types: dict[UUID, str] = field(default_factory=dict)

    @property
    def all_neighbors(self) -> set[UUID]:
        """Get all neighbors (incoming and outgoing combined)."""
        return self.outgoing_neighbors | self.incoming_neighbors

    @property
    def neighbor_count(self) -> int:
        """Total number of unique neighbors."""
        return len(self.all_neighbors)

    @property
    def outgoing_count(self) -> int:
        """Number of outgoing relationships."""
        return len(self.outgoing_neighbors)

    @property
    def incoming_count(self) -> int:
        """Number of incoming relationships."""
        return len(self.incoming_neighbors)

    @property
    def relationship_type_set(self) -> set[str]:
        """Set of unique relationship types."""
        return set(self.relationship_types.values())


class GraphSimilarityService:
    """
    Service for computing graph-based similarity between entities.

    Uses neighborhood overlap and relationship patterns to measure
    structural similarity in the knowledge graph.

    Attributes:
        neo4j_driver: Async Neo4j driver for graph queries
    """

    def __init__(self, neo4j_driver: AsyncDriver):
        """
        Initialize graph similarity service.

        Args:
            neo4j_driver: Async Neo4j driver
        """
        self._driver = neo4j_driver

    async def get_neighborhood(
        self,
        entity_id: UUID,
        max_neighbors: int = 100,
    ) -> GraphNeighborhood:
        """
        Get entity's neighborhood from Neo4j.

        Retrieves both outgoing and incoming neighbors with their
        relationship types. Limits results for performance.

        Args:
            entity_id: Entity ID to query
            max_neighbors: Maximum neighbors to retrieve per direction

        Returns:
            GraphNeighborhood with neighbor sets and relationship types
        """
        async with self._driver.session() as session:
            # Get outgoing relationships
            outgoing_result = await session.run(
                """
                MATCH (e:Entity {id: $entity_id})-[r]->(neighbor:Entity)
                RETURN neighbor.id AS neighbor_id, type(r) AS rel_type
                LIMIT $limit
                """,
                entity_id=str(entity_id),
                limit=max_neighbors,
            )
            outgoing_records = await outgoing_result.data()

            # Get incoming relationships
            incoming_result = await session.run(
                """
                MATCH (neighbor:Entity)-[r]->(e:Entity {id: $entity_id})
                RETURN neighbor.id AS neighbor_id, type(r) AS rel_type
                LIMIT $limit
                """,
                entity_id=str(entity_id),
                limit=max_neighbors,
            )
            incoming_records = await incoming_result.data()

        outgoing_neighbors: set[UUID] = set()
        incoming_neighbors: set[UUID] = set()
        relationship_types: dict[UUID, str] = {}

        for record in outgoing_records:
            try:
                neighbor_id = UUID(record["neighbor_id"])
                outgoing_neighbors.add(neighbor_id)
                relationship_types[neighbor_id] = record["rel_type"]
            except (ValueError, TypeError):
                continue

        for record in incoming_records:
            try:
                neighbor_id = UUID(record["neighbor_id"])
                incoming_neighbors.add(neighbor_id)
                if neighbor_id not in relationship_types:
                    relationship_types[neighbor_id] = record["rel_type"]
            except (ValueError, TypeError):
                continue

        return GraphNeighborhood(
            entity_id=entity_id,
            outgoing_neighbors=outgoing_neighbors,
            incoming_neighbors=incoming_neighbors,
            relationship_types=relationship_types,
        )

    def compute_jaccard_similarity(
        self,
        neighborhood_a: GraphNeighborhood,
        neighborhood_b: GraphNeighborhood,
    ) -> float:
        """
        Compute Jaccard similarity of neighbor sets.

        Jaccard similarity measures the overlap between two sets:
        J(A, B) = |A intersection B| / |A union B|

        Args:
            neighborhood_a: First entity's neighborhood
            neighborhood_b: Second entity's neighborhood

        Returns:
            Jaccard similarity in range [0, 1]
        """
        neighbors_a = neighborhood_a.all_neighbors
        neighbors_b = neighborhood_b.all_neighbors

        # Both have no neighbors - consider neutral (0.5)
        # This prevents entities with no relationships from having 0 similarity
        if not neighbors_a and not neighbors_b:
            return 0.5

        intersection = neighbors_a & neighbors_b
        union = neighbors_a | neighbors_b

        if not union:
            return 0.0

        jaccard = len(intersection) / len(union)

        logger.debug(
            f"Jaccard similarity: |A|={len(neighbors_a)}, |B|={len(neighbors_b)}, "
            f"|A intersection B|={len(intersection)}, J={jaccard:.3f}"
        )

        return jaccard

    def compute_relationship_type_similarity(
        self,
        neighborhood_a: GraphNeighborhood,
        neighborhood_b: GraphNeighborhood,
    ) -> float:
        """
        Compute similarity based on shared relationship types.

        Entities with similar relationship patterns (e.g., both
        IMPLEMENTS something, both EXTENDS something) are more likely
        to be similar concepts.

        Args:
            neighborhood_a: First entity's neighborhood
            neighborhood_b: Second entity's neighborhood

        Returns:
            Relationship type similarity in range [0, 1]
        """
        types_a = neighborhood_a.relationship_type_set
        types_b = neighborhood_b.relationship_type_set

        # Both have no relationships - consider neutral
        if not types_a and not types_b:
            return 0.5

        intersection = types_a & types_b
        union = types_a | types_b

        if not union:
            return 0.0

        return len(intersection) / len(union)

    async def compute_similarity(
        self,
        entity_a_id: UUID,
        entity_b_id: UUID,
        max_neighbors: int = 100,
    ) -> float:
        """
        Compute overall graph similarity between two entities.

        Combines Jaccard neighbor similarity with relationship type
        similarity using weighted combination.

        Args:
            entity_a_id: First entity ID
            entity_b_id: Second entity ID
            max_neighbors: Maximum neighbors to consider

        Returns:
            Combined graph similarity in range [0, 1]
        """
        # Get neighborhoods
        neighborhood_a = await self.get_neighborhood(entity_a_id, max_neighbors)
        neighborhood_b = await self.get_neighborhood(entity_b_id, max_neighbors)

        # Compute similarities
        jaccard = self.compute_jaccard_similarity(neighborhood_a, neighborhood_b)
        rel_type_sim = self.compute_relationship_type_similarity(
            neighborhood_a, neighborhood_b
        )

        # Weighted combination
        # Jaccard (actual shared neighbors) is more important
        combined = (jaccard * 0.7) + (rel_type_sim * 0.3)

        logger.debug(
            f"Graph similarity {entity_a_id} <-> {entity_b_id}: "
            f"jaccard={jaccard:.3f}, rel_type={rel_type_sim:.3f}, combined={combined:.3f}"
        )

        return combined

    async def compute_similarity_scores(
        self,
        entity_a_id: UUID,
        entity_b_id: UUID,
        max_neighbors: int = 100,
    ) -> GraphSimilarityScores:
        """
        Compute full graph similarity scores for entity pair.

        Returns GraphSimilarityScores with neighborhood similarity.

        Args:
            entity_a_id: First entity ID
            entity_b_id: Second entity ID
            max_neighbors: Maximum neighbors to consider

        Returns:
            GraphSimilarityScores with neighborhood and co-occurrence scores
        """
        start_time = time.perf_counter()

        # Get neighborhoods
        neighborhood_a = await self.get_neighborhood(entity_a_id, max_neighbors)
        neighborhood_b = await self.get_neighborhood(entity_b_id, max_neighbors)

        # Compute Jaccard similarity
        jaccard = self.compute_jaccard_similarity(neighborhood_a, neighborhood_b)

        computation_time_ms = (time.perf_counter() - start_time) * 1000

        scores = GraphSimilarityScores(
            neighborhood=SimilarityScore(
                similarity_type=SimilarityType.GRAPH_NEIGHBORHOOD,
                raw_score=jaccard,
                computation_time_ms=computation_time_ms,
            ),
            # Co-occurrence could be computed separately if needed
            co_occurrence=None,
        )

        logger.debug(
            f"Graph scores computed for ({entity_a_id}, {entity_b_id}): "
            f"neighborhood={jaccard:.3f}, time={computation_time_ms:.2f}ms"
        )

        return scores

    async def compute_similarity_direct(
        self,
        entity_a_id: UUID,
        entity_b_id: UUID,
    ) -> float:
        """
        Compute graph similarity using single Cypher query.

        More efficient than separate neighborhood queries for single pair.

        Args:
            entity_a_id: First entity ID
            entity_b_id: Second entity ID

        Returns:
            Graph similarity in range [0, 1]
        """
        async with self._driver.session() as session:
            result = await session.run(
                """
                // Get neighbors of entity A
                MATCH (a:Entity {id: $entity_a_id})
                OPTIONAL MATCH (a)-[r1]-(neighbor1:Entity)
                WITH a, collect(DISTINCT neighbor1.id) AS neighbors_a

                // Get neighbors of entity B
                MATCH (b:Entity {id: $entity_b_id})
                OPTIONAL MATCH (b)-[r2]-(neighbor2:Entity)
                WITH neighbors_a, collect(DISTINCT neighbor2.id) AS neighbors_b

                // Compute Jaccard
                WITH neighbors_a, neighbors_b,
                     [x IN neighbors_a WHERE x IN neighbors_b] AS intersection,
                     neighbors_a + [x IN neighbors_b WHERE NOT x IN neighbors_a] AS union_set

                RETURN
                    size(intersection) AS intersection_size,
                    size(union_set) AS union_size,
                    CASE WHEN size(union_set) = 0 THEN 0.5
                         ELSE toFloat(size(intersection)) / size(union_set)
                    END AS jaccard_similarity
                """,
                entity_a_id=str(entity_a_id),
                entity_b_id=str(entity_b_id),
            )

            record = await result.single()

            if record:
                similarity = record["jaccard_similarity"]
                logger.debug(
                    f"Direct graph similarity: {entity_a_id} <-> {entity_b_id} = {similarity:.3f}"
                )
                return similarity

            # No data - neutral score
            return 0.5

    async def compute_similarities_batch(
        self,
        entity_id: UUID,
        candidate_ids: list[UUID],
    ) -> dict[UUID, float]:
        """
        Compute graph similarity between entity and multiple candidates.

        Optimized batch query for efficiency.

        Args:
            entity_id: Source entity ID
            candidate_ids: List of candidate entity IDs

        Returns:
            Dict mapping candidate_id to similarity score
        """
        if not candidate_ids:
            return {}

        start_time = time.perf_counter()

        async with self._driver.session() as session:
            result = await session.run(
                """
                // Get source entity neighbors
                MATCH (source:Entity {id: $source_id})
                OPTIONAL MATCH (source)-[]-(neighbor:Entity)
                WITH source, collect(DISTINCT neighbor.id) AS source_neighbors

                // For each candidate, compute Jaccard
                UNWIND $candidate_ids AS candidate_id
                MATCH (candidate:Entity {id: candidate_id})
                OPTIONAL MATCH (candidate)-[]-(neighbor:Entity)
                WITH candidate_id, source_neighbors,
                     collect(DISTINCT neighbor.id) AS candidate_neighbors

                WITH candidate_id, source_neighbors, candidate_neighbors,
                     [x IN source_neighbors WHERE x IN candidate_neighbors] AS intersection

                RETURN
                    candidate_id,
                    CASE
                        WHEN size(source_neighbors) = 0 AND size(candidate_neighbors) = 0 THEN 0.5
                        WHEN size(source_neighbors) + size(candidate_neighbors) - size(intersection) = 0 THEN 0.0
                        ELSE toFloat(size(intersection)) /
                             (size(source_neighbors) + size(candidate_neighbors) - size(intersection))
                    END AS similarity
                """,
                source_id=str(entity_id),
                candidate_ids=[str(cid) for cid in candidate_ids],
            )

            records = await result.data()

            similarities: dict[UUID, float] = {}
            for record in records:
                try:
                    cid = UUID(record["candidate_id"])
                    similarities[cid] = record["similarity"]
                except (ValueError, TypeError):
                    continue

            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.debug(
                f"Batch graph similarity for {entity_id}: "
                f"computed {len(similarities)} candidates, "
                f"time={elapsed_ms:.2f}ms"
            )

            return similarities

    async def compute_batch_scores(
        self,
        entity_id: UUID,
        candidate_ids: list[UUID],
    ) -> dict[UUID, GraphSimilarityScores]:
        """
        Compute full graph similarity scores for entity vs multiple candidates.

        Args:
            entity_id: Source entity ID
            candidate_ids: List of candidate entity IDs

        Returns:
            Dict mapping candidate_id to GraphSimilarityScores
        """
        if not candidate_ids:
            return {}

        start_time = time.perf_counter()

        # Get batch similarities
        similarities = await self.compute_similarities_batch(entity_id, candidate_ids)

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        per_candidate_ms = elapsed_ms / len(candidate_ids) if candidate_ids else 0.0

        scores: dict[UUID, GraphSimilarityScores] = {}
        for cid, similarity in similarities.items():
            scores[cid] = GraphSimilarityScores(
                neighborhood=SimilarityScore(
                    similarity_type=SimilarityType.GRAPH_NEIGHBORHOOD,
                    raw_score=similarity,
                    computation_time_ms=per_candidate_ms,
                ),
                co_occurrence=None,
            )

        return scores


async def get_graph_similarity_service() -> GraphSimilarityService | None:
    """
    Get graph similarity service with Neo4j driver.

    Creates the service with the application's Neo4j async driver.

    Returns:
        GraphSimilarityService or None if Neo4j unavailable
    """
    try:
        from kg_builder.graph.client import get_neo4j_client

        client = get_neo4j_client()
        if client is None:
            logger.warning("Neo4j client unavailable, graph similarity disabled")
            return None

        # Get the async driver from the client
        driver = client.get_async_driver()
        if driver is None:
            logger.warning("Neo4j async driver unavailable, graph similarity disabled")
            return None

        return GraphSimilarityService(neo4j_driver=driver)
    except Exception as e:
        logger.error(f"Failed to create graph similarity service: {e}")
        return None
