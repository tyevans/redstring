"""
Tenant-scoped Neo4j query utilities.

This module provides a wrapper around Neo4jService that enforces tenant isolation
for all graph database operations. All queries automatically include tenant_id
filtering to ensure data isolation between tenants.

Example:
    from kg_builder.services.neo4j_tenant import get_tenant_scoped_neo4j

    # In an async context with a known tenant_id
    tenant_service = await get_tenant_scoped_neo4j(tenant_id)

    # All operations are automatically scoped to the tenant
    entity = await tenant_service.get_entity(entity_id)
    related = await tenant_service.get_related_entities(entity_id, "USES")
    results = await tenant_service.search_entities("extract")
"""

import logging
from typing import Any
from uuid import UUID

from kg_builder.services.neo4j import Neo4jService, get_neo4j_service

logger = logging.getLogger(__name__)


class TenantScopedNeo4jService:
    """Neo4j service wrapper that enforces tenant isolation.

    All queries automatically include tenant_id filtering to ensure
    that operations only affect data belonging to the specified tenant.

    This wrapper provides a simplified interface focused on common
    read operations while maintaining strict tenant boundaries.

    Attributes:
        _service: The underlying Neo4jService instance
        _tenant_id: UUID of the tenant for all operations
    """

    def __init__(self, service: Neo4jService, tenant_id: UUID):
        """Initialize tenant-scoped Neo4j service.

        Args:
            service: The Neo4jService instance to wrap
            tenant_id: UUID of the tenant for isolation
        """
        self._service = service
        self._tenant_id = tenant_id
        logger.debug(f"Created TenantScopedNeo4jService for tenant {tenant_id}")

    @property
    def tenant_id(self) -> UUID:
        """Get the tenant ID for this scoped service."""
        return self._tenant_id

    @property
    def service(self) -> Neo4jService:
        """Get the underlying Neo4jService instance."""
        return self._service

    async def get_entity(self, entity_id: UUID) -> dict[str, Any] | None:
        """Get entity within tenant scope.

        Retrieves an entity by its ID, but only if it belongs to the
        configured tenant. Returns None if the entity doesn't exist
        or belongs to a different tenant.

        Args:
            entity_id: UUID of the entity to retrieve

        Returns:
            Entity properties dict with node_id, or None if not found
        """
        logger.debug(f"Getting entity {entity_id} for tenant {self._tenant_id}")
        return await self._service.get_entity_node(entity_id, self._tenant_id)

    async def get_entity_by_name(self, name: str) -> dict[str, Any] | None:
        """Find entity by name within tenant scope.

        Searches for an entity with the exact name within the tenant's
        data. Note that entity names are not required to be unique,
        so this returns the first match if multiple exist.

        Args:
            name: Exact name of the entity to find

        Returns:
            Entity properties dict with node_id, or None if not found
        """
        query = """
        MATCH (e:Entity {tenant_id: $tenant_id, name: $name})
        RETURN e {.*, node_id: elementId(e)} as entity
        LIMIT 1
        """

        logger.debug(f"Searching for entity by name '{name}' for tenant {self._tenant_id}")

        async with self._service.session() as session:
            result = await session.run(
                query,
                tenant_id=str(self._tenant_id),
                name=name,
            )
            record = await result.single()
            return record["entity"] if record else None

    async def get_related_entities(
        self,
        entity_id: UUID,
        relationship_type: str | None = None,
        direction: str = "both",
    ) -> list[dict[str, Any]]:
        """Get related entities within tenant scope.

        Retrieves all entities related to the specified entity through
        relationships, filtered by tenant to ensure isolation.

        Args:
            entity_id: UUID of the source entity
            relationship_type: Optional type to filter relationships (e.g., "USES")
            direction: Relationship direction - "outgoing", "incoming", or "both"

        Returns:
            List of related entity dicts with relationship type included
        """
        # Build relationship pattern based on type filter
        rel_filter = f":{relationship_type}" if relationship_type else ""

        # Build direction-specific pattern
        if direction == "outgoing":
            pattern = f"(e)-[r{rel_filter}]->(related)"
        elif direction == "incoming":
            pattern = f"(e)<-[r{rel_filter}]-(related)"
        else:  # both
            pattern = f"(e)-[r{rel_filter}]-(related)"

        query = f"""
        MATCH (e:Entity {{id: $entity_id, tenant_id: $tenant_id}})
        MATCH {pattern}
        WHERE related.tenant_id = $tenant_id
        RETURN related {{
            .*,
            node_id: elementId(related),
            relationship_type: type(r),
            relationship_direction: CASE
                WHEN startNode(r) = e THEN 'outgoing'
                ELSE 'incoming'
            END
        }} as entity
        """

        logger.debug(
            f"Getting related entities for {entity_id} "
            f"(type={relationship_type}, direction={direction}) "
            f"for tenant {self._tenant_id}"
        )

        async with self._service.session() as session:
            result = await session.run(
                query,
                entity_id=str(entity_id),
                tenant_id=str(self._tenant_id),
            )
            return [dict(record["entity"]) async for record in result]

    async def search_entities(
        self,
        query_text: str,
        entity_type: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search entities within tenant scope.

        Performs a case-insensitive search on entity names within the
        tenant's data. Optionally filters by entity type.

        Args:
            query_text: Text to search for in entity names
            entity_type: Optional type filter (e.g., "FUNCTION", "CLASS")
            limit: Maximum number of results to return (default 10)

        Returns:
            List of matching entity dicts ordered by name
        """
        type_filter = "AND e.type = $entity_type" if entity_type else ""

        query = f"""
        MATCH (e:Entity)
        WHERE e.tenant_id = $tenant_id
        AND toLower(e.name) CONTAINS toLower($query_text)
        {type_filter}
        RETURN e {{.*, node_id: elementId(e)}} as entity
        ORDER BY e.name
        LIMIT $limit
        """

        params: dict[str, Any] = {
            "tenant_id": str(self._tenant_id),
            "query_text": query_text,
            "limit": limit,
        }
        if entity_type:
            params["entity_type"] = entity_type

        logger.debug(
            f"Searching entities with query '{query_text}' "
            f"(type={entity_type}, limit={limit}) "
            f"for tenant {self._tenant_id}"
        )

        async with self._service.session() as session:
            result = await session.run(query, **params)
            return [dict(record["entity"]) async for record in result]

    async def get_entities_by_type(
        self,
        entity_type: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Get all entities of a specific type within tenant scope.

        Retrieves entities filtered by type with pagination support.

        Args:
            entity_type: Type of entities to retrieve (e.g., "FUNCTION")
            limit: Maximum number of results (default 100)
            offset: Number of results to skip for pagination

        Returns:
            List of entity dicts of the specified type
        """
        query = """
        MATCH (e:Entity {tenant_id: $tenant_id, type: $entity_type})
        RETURN e {.*, node_id: elementId(e)} as entity
        ORDER BY e.name
        SKIP $offset
        LIMIT $limit
        """

        logger.debug(
            f"Getting entities by type '{entity_type}' "
            f"(limit={limit}, offset={offset}) "
            f"for tenant {self._tenant_id}"
        )

        async with self._service.session() as session:
            result = await session.run(
                query,
                tenant_id=str(self._tenant_id),
                entity_type=entity_type,
                limit=limit,
                offset=offset,
            )
            return [dict(record["entity"]) async for record in result]

    async def count_entities(
        self,
        entity_type: str | None = None,
    ) -> int:
        """Count entities within tenant scope.

        Counts all entities belonging to the tenant, optionally filtered
        by entity type.

        Args:
            entity_type: Optional type filter

        Returns:
            Count of matching entities
        """
        if entity_type:
            query = """
            MATCH (e:Entity {tenant_id: $tenant_id, type: $entity_type})
            RETURN count(e) as count
            """
            params = {
                "tenant_id": str(self._tenant_id),
                "entity_type": entity_type,
            }
        else:
            query = """
            MATCH (e:Entity {tenant_id: $tenant_id})
            RETURN count(e) as count
            """
            params = {"tenant_id": str(self._tenant_id)}

        async with self._service.session() as session:
            result = await session.run(query, **params)
            record = await result.single()
            return record["count"] if record else 0

    async def get_entity_graph(
        self,
        entity_id: UUID,
        depth: int = 1,
    ) -> dict[str, Any]:
        """Get an entity and its relationship graph within tenant scope.

        Retrieves the specified entity along with related entities up to
        the specified depth, forming a subgraph.

        Args:
            entity_id: UUID of the root entity
            depth: How many relationship hops to include (default 1)

        Returns:
            Dict with 'root' entity and 'relationships' list
        """
        if depth < 1:
            depth = 1
        if depth > 3:
            # Limit depth to prevent performance issues
            depth = 3
            logger.warning(f"Graph depth limited to 3 for entity {entity_id}")

        # Build variable-length relationship pattern
        query = f"""
        MATCH (root:Entity {{id: $entity_id, tenant_id: $tenant_id}})
        OPTIONAL MATCH path = (root)-[r*1..{depth}]-(related:Entity)
        WHERE all(n IN nodes(path) WHERE n.tenant_id = $tenant_id)
        WITH root, collect(DISTINCT related {{.*, node_id: elementId(related)}}) as related_nodes,
             collect(DISTINCT {{
                 source: startNode(last(r)).id,
                 target: endNode(last(r)).id,
                 type: type(last(r)),
                 properties: last(r).properties
             }}) as relationships
        RETURN root {{.*, node_id: elementId(root)}} as root,
               related_nodes,
               relationships
        """

        logger.debug(
            f"Getting entity graph for {entity_id} (depth={depth}) for tenant {self._tenant_id}"
        )

        async with self._service.session() as session:
            result = await session.run(
                query,
                entity_id=str(entity_id),
                tenant_id=str(self._tenant_id),
            )
            record = await result.single()

            if not record:
                return {"root": None, "related_entities": [], "relationships": []}

            return {
                "root": dict(record["root"]) if record["root"] else None,
                "related_entities": [dict(node) for node in (record["related_nodes"] or [])],
                "relationships": [
                    dict(rel)
                    for rel in (record["relationships"] or [])
                    if rel.get("source")  # Filter out null relationships
                ],
            }


async def get_tenant_scoped_neo4j(tenant_id: UUID) -> TenantScopedNeo4jService:
    """Get a tenant-scoped Neo4j service.

    Factory function that creates a TenantScopedNeo4jService wrapping
    the global Neo4jService instance.

    Args:
        tenant_id: UUID of the tenant for isolation

    Returns:
        TenantScopedNeo4jService configured for the specified tenant

    Example:
        tenant_id = UUID("123e4567-e89b-12d3-a456-426614174000")
        service = await get_tenant_scoped_neo4j(tenant_id)
        entity = await service.get_entity(entity_id)
    """
    service = await get_neo4j_service()
    return TenantScopedNeo4jService(service, tenant_id)
