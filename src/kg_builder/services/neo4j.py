"""
Neo4j service layer for knowledge graph operations.

This module provides an async interface to Neo4j for storing
and querying extracted entities and relationships.
"""

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional
from uuid import UUID

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncSession

from kg_builder.config import settings

logger = logging.getLogger(__name__)


class Neo4jService:
    """Async service for Neo4j graph database operations.

    This service provides:
    - Connection lifecycle management
    - Entity node CRUD operations
    - Relationship CRUD operations
    - Tenant isolation utilities
    - Health checking

    Example:
        service = Neo4jService()
        await service.connect()

        try:
            node_id = await service.create_entity_node(
                entity_id=uuid4(),
                tenant_id=uuid4(),
                entity_type="FUNCTION",
                name="extract_entities",
                properties={"signature": "def extract_entities(...)"},
            )
        finally:
            await service.close()
    """

    def __init__(
        self,
        uri: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        database: Optional[str] = None,
    ):
        """Initialize Neo4j service.

        Args:
            uri: Neo4j URI (defaults to settings)
            user: Neo4j username (defaults to settings)
            password: Neo4j password (defaults to settings)
            database: Neo4j database (defaults to settings)
        """
        self._uri = uri or settings.NEO4J_URI
        self._user = user or settings.NEO4J_USER
        self._password = password or settings.NEO4J_PASSWORD
        self._database = database or settings.NEO4J_DATABASE
        self._driver: Optional[AsyncDriver] = None

    async def connect(self) -> None:
        """Establish connection to Neo4j.

        Creates an async driver with connection pooling configured
        according to application settings.
        """
        if self._driver is not None:
            logger.warning("Neo4j driver already connected")
            return

        self._driver = AsyncGraphDatabase.driver(
            self._uri,
            auth=(self._user, self._password),
            max_connection_pool_size=settings.NEO4J_MAX_CONNECTION_POOL_SIZE,
            connection_timeout=settings.NEO4J_CONNECTION_TIMEOUT,
        )

        # Verify connectivity
        await self._driver.verify_connectivity()
        logger.info(f"Connected to Neo4j at {self._uri}")

    async def close(self) -> None:
        """Close Neo4j connection and release resources."""
        if self._driver is not None:
            await self._driver.close()
            self._driver = None
            logger.info("Neo4j connection closed")

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Get a Neo4j session.

        Yields:
            AsyncSession: Neo4j async session

        Raises:
            RuntimeError: If driver not connected
        """
        if self._driver is None:
            raise RuntimeError("Neo4j driver not connected. Call connect() first.")

        session = self._driver.session(database=self._database)
        try:
            yield session
        finally:
            await session.close()

    async def health_check(self) -> dict[str, Any]:
        """Check Neo4j connectivity and return status.

        Returns:
            dict with status, latency, and database info
        """
        import time

        try:
            start = time.time()
            async with self.session() as session:
                result = await session.run("RETURN 1 as health")
                await result.consume()
            latency_ms = (time.time() - start) * 1000

            return {
                "status": "healthy",
                "latency_ms": round(latency_ms, 2),
                "uri": self._uri,
                "database": self._database,
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
                "uri": self._uri,
                "database": self._database,
            }

    # =========================================================================
    # Entity Node Operations
    # =========================================================================

    async def create_entity_node(
        self,
        entity_id: UUID,
        tenant_id: UUID,
        entity_type: str,
        name: str,
        properties: dict[str, Any],
        description: Optional[str] = None,
    ) -> str:
        """Create or merge an entity node in the graph.

        Uses MERGE to ensure idempotency - can be called multiple times
        for the same entity without creating duplicates.

        Args:
            entity_id: Unique entity identifier
            tenant_id: Tenant for isolation
            entity_type: Type of entity (FUNCTION, CLASS, etc.)
            name: Entity name
            properties: Type-specific properties
            description: Optional description

        Returns:
            Neo4j element ID of the created/updated node
        """
        query = """
        MERGE (e:Entity {id: $id})
        SET e.tenant_id = $tenant_id,
            e.type = $type,
            e.name = $name,
            e.description = $description,
            e.properties = $properties,
            e.updated_at = datetime()
        ON CREATE SET e.created_at = datetime()
        RETURN elementId(e) as node_id
        """

        async with self.session() as session:
            result = await session.run(
                query,
                id=str(entity_id),
                tenant_id=str(tenant_id),
                type=entity_type,
                name=name,
                description=description,
                properties=properties,
            )
            record = await result.single()
            return record["node_id"]

    async def get_entity_node(
        self,
        entity_id: UUID,
        tenant_id: UUID,
    ) -> Optional[dict[str, Any]]:
        """Get an entity node by ID.

        Args:
            entity_id: Entity identifier
            tenant_id: Tenant for isolation

        Returns:
            Entity properties or None if not found
        """
        query = """
        MATCH (e:Entity {id: $id, tenant_id: $tenant_id})
        RETURN e {.*, node_id: elementId(e)} as entity
        """

        async with self.session() as session:
            result = await session.run(
                query,
                id=str(entity_id),
                tenant_id=str(tenant_id),
            )
            record = await result.single()
            return record["entity"] if record else None

    async def delete_entity_node(
        self,
        entity_id: UUID,
        tenant_id: UUID,
    ) -> bool:
        """Delete an entity node and its relationships.

        Args:
            entity_id: Entity identifier
            tenant_id: Tenant for isolation

        Returns:
            True if deleted, False if not found
        """
        query = """
        MATCH (e:Entity {id: $id, tenant_id: $tenant_id})
        DETACH DELETE e
        RETURN count(e) as deleted
        """

        async with self.session() as session:
            result = await session.run(
                query,
                id=str(entity_id),
                tenant_id=str(tenant_id),
            )
            record = await result.single()
            return record["deleted"] > 0

    # =========================================================================
    # Relationship Operations
    # =========================================================================

    async def create_relationship(
        self,
        relationship_id: UUID,
        tenant_id: UUID,
        source_entity_id: UUID,
        target_entity_id: UUID,
        relationship_type: str,
        properties: dict[str, Any],
        confidence_score: float = 1.0,
    ) -> Optional[str]:
        """Create a relationship between two entities.

        Args:
            relationship_id: Unique relationship identifier
            tenant_id: Tenant for isolation
            source_entity_id: Source entity ID
            target_entity_id: Target entity ID
            relationship_type: Type (USES, IMPLEMENTS, etc.)
            properties: Relationship properties
            confidence_score: Confidence in the relationship

        Returns:
            Neo4j element ID of relationship, or None if entities not found
        """
        # Normalize relationship type to Neo4j convention (uppercase, underscores)
        rel_type = relationship_type.upper().replace("-", "_")

        query = f"""
        MATCH (s:Entity {{id: $source_id, tenant_id: $tenant_id}})
        MATCH (t:Entity {{id: $target_id, tenant_id: $tenant_id}})
        MERGE (s)-[r:{rel_type} {{id: $rel_id}}]->(t)
        SET r.confidence = $confidence,
            r.properties = $properties,
            r.updated_at = datetime()
        ON CREATE SET r.created_at = datetime()
        RETURN elementId(r) as rel_id
        """

        async with self.session() as session:
            result = await session.run(
                query,
                source_id=str(source_entity_id),
                target_id=str(target_entity_id),
                tenant_id=str(tenant_id),
                rel_id=str(relationship_id),
                confidence=confidence_score,
                properties=properties,
            )
            record = await result.single()
            return record["rel_id"] if record else None

    async def get_entity_relationships(
        self,
        entity_id: UUID,
        tenant_id: UUID,
        direction: str = "both",
    ) -> list[dict[str, Any]]:
        """Get all relationships for an entity.

        Args:
            entity_id: Entity identifier
            tenant_id: Tenant for isolation
            direction: "outgoing", "incoming", or "both"

        Returns:
            List of relationship dictionaries
        """
        if direction == "outgoing":
            query = """
            MATCH (e:Entity {id: $id, tenant_id: $tenant_id})-[r]->(t:Entity)
            RETURN type(r) as type, r.properties as properties,
                   r.confidence as confidence, t.id as target_id, t.name as target_name
            """
        elif direction == "incoming":
            query = """
            MATCH (s:Entity)-[r]->(e:Entity {id: $id, tenant_id: $tenant_id})
            RETURN type(r) as type, r.properties as properties,
                   r.confidence as confidence, s.id as source_id, s.name as source_name
            """
        else:
            query = """
            MATCH (e:Entity {id: $id, tenant_id: $tenant_id})-[r]-(other:Entity)
            RETURN type(r) as type, r.properties as properties,
                   r.confidence as confidence, other.id as other_id, other.name as other_name,
                   CASE WHEN startNode(r) = e THEN 'outgoing' ELSE 'incoming' END as direction
            """

        async with self.session() as session:
            result = await session.run(
                query,
                id=str(entity_id),
                tenant_id=str(tenant_id),
            )
            return [dict(record) async for record in result]

    # =========================================================================
    # Tenant Utilities
    # =========================================================================

    async def count_entities_for_tenant(self, tenant_id: UUID) -> int:
        """Count all entities for a tenant.

        Args:
            tenant_id: Tenant identifier

        Returns:
            Count of entities
        """
        query = """
        MATCH (e:Entity {tenant_id: $tenant_id})
        RETURN count(e) as count
        """

        async with self.session() as session:
            result = await session.run(query, tenant_id=str(tenant_id))
            record = await result.single()
            return record["count"]

    async def delete_tenant_data(self, tenant_id: UUID) -> int:
        """Delete all data for a tenant.

        WARNING: This is destructive and cannot be undone.

        Args:
            tenant_id: Tenant identifier

        Returns:
            Count of deleted nodes
        """
        query = """
        MATCH (e:Entity {tenant_id: $tenant_id})
        DETACH DELETE e
        RETURN count(e) as deleted
        """

        async with self.session() as session:
            result = await session.run(query, tenant_id=str(tenant_id))
            record = await result.single()
            return record["deleted"]


# =========================================================================
# Global Service Instance
# =========================================================================

_neo4j_service: Optional[Neo4jService] = None


async def get_neo4j_service() -> Neo4jService:
    """Get the global Neo4j service instance.

    Creates and connects the service on first call.

    Returns:
        Connected Neo4jService instance
    """
    global _neo4j_service

    if _neo4j_service is None:
        _neo4j_service = Neo4jService()
        await _neo4j_service.connect()

    return _neo4j_service


async def close_neo4j_service() -> None:
    """Close the global Neo4j service."""
    global _neo4j_service

    if _neo4j_service is not None:
        await _neo4j_service.close()
        _neo4j_service = None
