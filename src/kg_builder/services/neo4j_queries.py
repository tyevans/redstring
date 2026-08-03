"""
Graph query utilities for knowledge graph exploration.

This module provides a GraphQueryService class that wraps Neo4jService
and offers specialized query methods for path finding, neighborhood
exploration, similarity detection, and graph statistics.

All queries enforce tenant isolation to ensure data security in a
multi-tenant environment.

Example:
    from kg_builder.services.neo4j_queries import GraphQueryService
    from kg_builder.services.neo4j import get_neo4j_service

    service = await get_neo4j_service()
    query_service = GraphQueryService(service)

    # Find shortest path between two entities
    path = await query_service.find_path(
        source_id=source_uuid,
        target_id=target_uuid,
        tenant_id=tenant_uuid,
        max_hops=5,
    )

    # Get entity neighborhood
    neighborhood = await query_service.get_neighborhood(
        entity_id=entity_uuid,
        tenant_id=tenant_uuid,
        depth=2,
    )
"""

import logging
from typing import Any
from uuid import UUID

from kg_builder.services.neo4j import Neo4jService, get_neo4j_service

logger = logging.getLogger(__name__)


class GraphQueryService:
    """Utilities for querying the knowledge graph.

    This service provides specialized graph query operations including:
    - Path finding between entities
    - Neighborhood exploration
    - Entity similarity based on shared connections
    - Graph statistics

    All operations enforce tenant isolation to maintain data security.

    Attributes:
        _service: The underlying Neo4jService instance
    """

    def __init__(self, service: Neo4jService):
        """Initialize the graph query service.

        Args:
            service: The Neo4jService instance to use for queries
        """
        self._service = service
        logger.debug("GraphQueryService initialized")

    @property
    def service(self) -> Neo4jService:
        """Get the underlying Neo4jService instance."""
        return self._service

    async def find_path(
        self,
        source_id: UUID,
        target_id: UUID,
        tenant_id: UUID,
        max_hops: int = 5,
    ) -> dict[str, Any] | None:
        """Find shortest path between two entities.

        Uses Neo4j's shortestPath algorithm to find the optimal path
        between a source and target entity within the specified tenant's
        data.

        Args:
            source_id: UUID of the source entity
            target_id: UUID of the target entity
            tenant_id: UUID of the tenant for isolation
            max_hops: Maximum number of relationship hops to traverse
                     (default 5, capped at 10 for performance)

        Returns:
            Dictionary containing:
            - nodes: List of entities in the path with id, name, type
            - relationships: List of relationship types along the path
            Returns None if no path exists between the entities.

        Example:
            path = await service.find_path(source_id, target_id, tenant_id)
            if path:
                print(f"Path has {len(path['nodes'])} nodes")
                print(f"Relationship types: {path['relationships']}")
        """
        # Cap max_hops for performance
        if max_hops < 1:
            max_hops = 1
        elif max_hops > 10:
            max_hops = 10
            logger.warning("max_hops capped at 10 for performance")

        query = f"""
        MATCH (source:Entity {{id: $source_id, tenant_id: $tenant_id}})
        MATCH (target:Entity {{id: $target_id, tenant_id: $tenant_id}})
        MATCH path = shortestPath((source)-[*..{max_hops}]-(target))
        WHERE all(n IN nodes(path) WHERE n.tenant_id = $tenant_id)
        RETURN [n IN nodes(path) | {{id: n.id, name: n.name, type: n.type}}] as nodes,
               [r IN relationships(path) | type(r)] as relationships
        """

        logger.debug(
            f"Finding path from {source_id} to {target_id} "
            f"(max_hops={max_hops}) for tenant {tenant_id}"
        )

        async with self._service.session() as session:
            result = await session.run(
                query,
                source_id=str(source_id),
                target_id=str(target_id),
                tenant_id=str(tenant_id),
            )
            record = await result.single()

            if record:
                return {
                    "nodes": list(record["nodes"]),
                    "relationships": list(record["relationships"]),
                }
            return None

    async def get_neighborhood(
        self,
        entity_id: UUID,
        tenant_id: UUID,
        depth: int = 1,
    ) -> dict[str, Any] | None:
        """Get entity neighborhood up to N hops.

        Retrieves the specified entity and all entities connected to it
        within the specified depth, forming a local subgraph.

        Args:
            entity_id: UUID of the center entity
            tenant_id: UUID of the tenant for isolation
            depth: Number of relationship hops to include (default 1, capped at 3)

        Returns:
            Dictionary containing:
            - center: The center entity with id, name, type
            - neighbors: List of neighboring entities with id, name, type
            Returns None if the center entity doesn't exist.

        Example:
            neighborhood = await service.get_neighborhood(entity_id, tenant_id)
            if neighborhood:
                print(f"Center: {neighborhood['center']['name']}")
                print(f"Found {len(neighborhood['neighbors'])} neighbors")
        """
        # Cap depth for performance
        if depth < 1:
            depth = 1
        elif depth > 3:
            depth = 3
            logger.warning("Neighborhood depth capped at 3 for performance")

        query = f"""
        MATCH (center:Entity {{id: $entity_id, tenant_id: $tenant_id}})
        OPTIONAL MATCH (center)-[r*1..{depth}]-(neighbor:Entity {{tenant_id: $tenant_id}})
        WHERE neighbor.id <> center.id
        WITH center, collect(DISTINCT {{
            id: neighbor.id,
            name: neighbor.name,
            type: neighbor.type
        }}) as neighbors
        RETURN center {{.id, .name, .type}} as center, neighbors
        """

        logger.debug(
            f"Getting neighborhood for entity {entity_id} "
            f"(depth={depth}) for tenant {tenant_id}"
        )

        async with self._service.session() as session:
            result = await session.run(
                query,
                entity_id=str(entity_id),
                tenant_id=str(tenant_id),
            )
            record = await result.single()

            if not record or not record["center"]:
                return None

            # Filter out null entries from neighbors (happens when no neighbors exist)
            neighbors = [
                n for n in record["neighbors"]
                if n and n.get("id") is not None
            ]

            return {
                "center": dict(record["center"]),
                "neighbors": neighbors,
            }

    async def find_similar_entities(
        self,
        entity_id: UUID,
        tenant_id: UUID,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Find entities similar based on shared connections.

        Identifies entities that share the most connections with the
        specified entity, ranked by the number of shared relationships.

        Args:
            entity_id: UUID of the entity to find similarities for
            tenant_id: UUID of the tenant for isolation
            limit: Maximum number of similar entities to return (default 10)

        Returns:
            List of entity dicts, each containing:
            - id: Entity UUID
            - name: Entity name
            - type: Entity type
            - shared_connections: Count of shared connected entities
            Empty list if no similar entities found.

        Example:
            similar = await service.find_similar_entities(entity_id, tenant_id)
            for entity in similar:
                print(f"{entity['name']}: {entity['shared_connections']} shared")
        """
        query = """
        MATCH (e:Entity {id: $entity_id, tenant_id: $tenant_id})-[r]-(shared:Entity)
        WHERE shared.tenant_id = $tenant_id
        MATCH (similar:Entity {tenant_id: $tenant_id})-[r2]-(shared)
        WHERE similar.id <> e.id
        WITH similar, count(DISTINCT shared) as shared_count
        ORDER BY shared_count DESC
        LIMIT $limit
        RETURN similar {.id, .name, .type, shared_connections: shared_count} as similar
        """

        logger.debug(
            f"Finding entities similar to {entity_id} "
            f"(limit={limit}) for tenant {tenant_id}"
        )

        async with self._service.session() as session:
            result = await session.run(
                query,
                entity_id=str(entity_id),
                tenant_id=str(tenant_id),
                limit=limit,
            )
            return [dict(record["similar"]) async for record in result]

    async def get_entity_stats(self, tenant_id: UUID) -> dict[str, Any]:
        """Get statistics for tenant's knowledge graph.

        Computes aggregate statistics about the tenant's knowledge graph
        including total entity count and breakdown by entity type.

        Args:
            tenant_id: UUID of the tenant

        Returns:
            Dictionary containing:
            - total_entities: Total count of entities
            - total_relationships: Total count of relationships
            - by_type: Dict mapping entity type to count

        Example:
            stats = await service.get_entity_stats(tenant_id)
            print(f"Total entities: {stats['total_entities']}")
            for type_name, count in stats['by_type'].items():
                print(f"  {type_name}: {count}")
        """
        # First get entity counts by type
        entity_query = """
        MATCH (e:Entity {tenant_id: $tenant_id})
        WITH count(e) as total_entities, e.type as type
        RETURN total_entities, type, count(*) as count
        ORDER BY count DESC
        """

        # Get relationship count
        relationship_query = """
        MATCH (e:Entity {tenant_id: $tenant_id})-[r]->(:Entity {tenant_id: $tenant_id})
        RETURN count(r) as total_relationships
        """

        logger.debug(f"Getting entity stats for tenant {tenant_id}")

        async with self._service.session() as session:
            # Get entity stats
            entity_result = await session.run(
                entity_query,
                tenant_id=str(tenant_id),
            )
            entity_records = [record async for record in entity_result]

            if not entity_records:
                return {
                    "total_entities": 0,
                    "total_relationships": 0,
                    "by_type": {},
                }

            # Get relationship count
            rel_result = await session.run(
                relationship_query,
                tenant_id=str(tenant_id),
            )
            rel_record = await rel_result.single()
            total_relationships = rel_record["total_relationships"] if rel_record else 0

            # Build by_type dict, filtering out null types
            by_type = {}
            for record in entity_records:
                entity_type = record["type"]
                if entity_type:
                    by_type[entity_type] = record["count"]

            return {
                "total_entities": entity_records[0]["total_entities"] if entity_records else 0,
                "total_relationships": total_relationships,
                "by_type": by_type,
            }


async def get_graph_query_service() -> GraphQueryService:
    """Get a GraphQueryService instance using the global Neo4j service.

    Factory function that creates a GraphQueryService wrapping the global
    Neo4jService instance.

    Returns:
        GraphQueryService configured with the global Neo4j service

    Example:
        query_service = await get_graph_query_service()
        stats = await query_service.get_entity_stats(tenant_id)
    """
    service = await get_neo4j_service()
    return GraphQueryService(service)
