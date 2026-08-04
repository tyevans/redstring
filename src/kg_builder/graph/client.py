"""
Neo4j client for knowledge graph operations.

Provides a tenant-aware interface to Neo4j for:
- Entity node operations
- Relationship operations
- Graph queries
"""

import logging
from typing import Optional
from uuid import UUID

from neo4j import AsyncGraphDatabase, GraphDatabase
from neo4j.exceptions import ServiceUnavailable

from kg_builder.config import settings
from kg_builder.models.extracted_entity import EntityRelationship, ExtractedEntity

logger = logging.getLogger(__name__)


# Global client instance
_neo4j_client: Optional["Neo4jClient"] = None


def get_neo4j_client() -> "Neo4jClient":
    """Get or create the Neo4j client singleton."""
    global _neo4j_client
    if _neo4j_client is None:
        _neo4j_client = Neo4jClient(
            uri=settings.NEO4J_URI,
            user=settings.NEO4J_USER,
            password=settings.NEO4J_PASSWORD,
        )
    return _neo4j_client


class Neo4jClient:
    """
    Neo4j client with tenant-aware operations.

    All operations include tenant_id for multi-tenant isolation.
    Entities and relationships are stored as nodes and edges
    with tenant_id properties for filtering.
    """

    def __init__(self, uri: str, user: str, password: str):
        """
        Initialize Neo4j client.

        Args:
            uri: Neo4j connection URI
            user: Neo4j username
            password: Neo4j password
        """
        self.uri = uri
        self.user = user
        self.password = password

        # Create sync driver for Celery tasks
        self._sync_driver = GraphDatabase.driver(
            uri,
            auth=(user, password),
            max_connection_lifetime=3600,
            max_connection_pool_size=50,
        )

        # Create async driver for API operations
        self._async_driver = AsyncGraphDatabase.driver(
            uri,
            auth=(user, password),
            max_connection_lifetime=3600,
            max_connection_pool_size=50,
        )

        logger.info(f"Neo4j client initialized: {uri}")

    def close(self) -> None:
        """Close all database connections."""
        self._sync_driver.close()
        logger.info("Neo4j sync driver closed")

    async def close_async(self) -> None:
        """Close async database connection."""
        await self._async_driver.close()
        logger.info("Neo4j async driver closed")

    def verify_connectivity(self) -> bool:
        """Verify database connectivity."""
        try:
            self._sync_driver.verify_connectivity()
            return True
        except ServiceUnavailable as e:
            logger.error(f"Neo4j connectivity check failed: {e}")
            return False

    # =========================================================================
    # Entity Operations (Sync - for Celery)
    # =========================================================================

    def sync_entity(self, entity: ExtractedEntity) -> str:
        """
        Create or update an entity node in Neo4j.

        Args:
            entity: ExtractedEntity from PostgreSQL

        Returns:
            Neo4j element ID of the node
        """
        with self._sync_driver.session() as session:
            result = session.run(
                """
                MERGE (e:Entity {id: $id})
                SET e.tenant_id = $tenant_id,
                    e.name = $name,
                    e.normalized_name = $normalized_name,
                    e.type = $type,
                    e.description = $description,
                    e.confidence_score = $confidence,
                    e.extraction_method = $method,
                    e.properties = $properties,
                    e.updated_at = datetime()
                WITH e
                CALL apoc.create.addLabels(e, [$type_label]) YIELD node
                RETURN elementId(node) as node_id
                """,
                id=str(entity.id),
                tenant_id=str(entity.tenant_id),
                name=entity.name,
                normalized_name=entity.normalized_name,
                type=entity.entity_type,
                type_label=entity.entity_type.capitalize(),
                description=entity.description,
                confidence=entity.confidence_score,
                method=entity.extraction_method.value,
                properties=_serialize_properties(entity.properties),
            )
            record = result.single()
            return record["node_id"] if record else None

    def sync_relationship(
        self,
        relationship: EntityRelationship,
        source_node_id: str,
        target_node_id: str,
    ) -> str:
        """
        Create or update a relationship in Neo4j.

        Args:
            relationship: EntityRelationship from PostgreSQL
            source_node_id: Neo4j element ID of source node
            target_node_id: Neo4j element ID of target node

        Returns:
            Neo4j element ID of the relationship
        """
        with self._sync_driver.session() as session:
            # Create relationship with dynamic type
            rel_type = relationship.relationship_type.upper().replace(" ", "_")

            result = session.run(
                f"""
                MATCH (source:Entity {{id: $source_id}})
                MATCH (target:Entity {{id: $target_id}})
                MERGE (source)-[r:{rel_type} {{id: $rel_id}}]->(target)
                SET r.tenant_id = $tenant_id,
                    r.confidence_score = $confidence,
                    r.properties = $properties,
                    r.updated_at = datetime()
                RETURN elementId(r) as rel_id
                """,
                source_id=str(relationship.source_entity_id),
                target_id=str(relationship.target_entity_id),
                rel_id=str(relationship.id),
                tenant_id=str(relationship.tenant_id),
                confidence=relationship.confidence_score,
                properties=_serialize_properties(relationship.properties),
            )
            record = result.single()
            return record["rel_id"] if record else None

    def delete_entity(self, entity_id: UUID, tenant_id: UUID) -> bool:
        """
        Delete an entity node and its relationships.

        Args:
            entity_id: Entity UUID
            tenant_id: Tenant UUID

        Returns:
            True if deleted, False otherwise
        """
        with self._sync_driver.session() as session:
            result = session.run(
                """
                MATCH (e:Entity {id: $id, tenant_id: $tenant_id})
                DETACH DELETE e
                RETURN count(e) as deleted
                """,
                id=str(entity_id),
                tenant_id=str(tenant_id),
            )
            record = result.single()
            return record["deleted"] > 0 if record else False

    # =========================================================================
    # Query Operations (Async - for API)
    # =========================================================================

    async def query_entity_graph(
        self,
        entity_id: UUID,
        tenant_id: UUID,
        depth: int = 2,
        limit: int = 100,
    ) -> dict:
        """
        Query the graph around an entity.

        Args:
            entity_id: Central entity UUID
            tenant_id: Tenant UUID for isolation
            depth: How many relationship hops to traverse
            limit: Maximum nodes to return

        Returns:
            dict with 'nodes' and 'edges' lists
        """
        async with self._async_driver.session() as session:
            result = await session.run(
                """
                MATCH (center:Entity {id: $id, tenant_id: $tenant_id})
                CALL apoc.path.subgraphAll(center, {
                    maxLevel: $depth,
                    relationshipFilter: null,
                    labelFilter: null,
                    limit: $limit
                }) YIELD nodes, relationships
                RETURN nodes, relationships
                """,
                id=str(entity_id),
                tenant_id=str(tenant_id),
                depth=depth,
                limit=limit,
            )

            record = await result.single()
            if not record:
                return {"nodes": [], "edges": []}

            nodes = []
            for node in record["nodes"]:
                # Filter by tenant
                if node.get("tenant_id") != str(tenant_id):
                    continue
                nodes.append(
                    {
                        "id": node.get("id"),
                        "name": node.get("name"),
                        "type": node.get("type"),
                        "properties": node.get("properties", {}),
                    }
                )

            edges = []
            for rel in record["relationships"]:
                edges.append(
                    {
                        "source": rel.start_node.get("id"),
                        "target": rel.end_node.get("id"),
                        "type": rel.type,
                        "confidence": rel.get("confidence_score", 1.0),
                    }
                )

            return {"nodes": nodes, "edges": edges}

    async def search_entities(
        self,
        tenant_id: UUID,
        query: str,
        entity_types: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """
        Search entities by name.

        Args:
            tenant_id: Tenant UUID
            query: Search query string
            entity_types: Optional list of entity types to filter
            limit: Maximum results

        Returns:
            List of matching entity dictionaries
        """
        type_filter = ""
        if entity_types:
            labels = " OR ".join(f"e:{t.capitalize()}" for t in entity_types)
            type_filter = f"AND ({labels})"

        async with self._async_driver.session() as session:
            result = await session.run(
                f"""
                MATCH (e:Entity)
                WHERE e.tenant_id = $tenant_id
                AND (e.name CONTAINS $query OR e.normalized_name CONTAINS $query_lower)
                {type_filter}
                RETURN e
                ORDER BY e.confidence_score DESC
                LIMIT $limit
                """,
                tenant_id=str(tenant_id),
                query=query,
                query_lower=query.lower(),
                limit=limit,
            )

            entities = []
            async for record in result:
                node = record["e"]
                entities.append(
                    {
                        "id": node.get("id"),
                        "name": node.get("name"),
                        "type": node.get("type"),
                        "description": node.get("description"),
                        "confidence": node.get("confidence_score"),
                    }
                )

            return entities

    async def get_entity_relationships(
        self,
        entity_id: UUID,
        tenant_id: UUID,
        direction: str = "both",
    ) -> list[dict]:
        """
        Get relationships for an entity.

        Args:
            entity_id: Entity UUID
            tenant_id: Tenant UUID
            direction: 'outgoing', 'incoming', or 'both'

        Returns:
            List of relationship dictionaries
        """
        if direction == "outgoing":
            pattern = "(e)-[r]->(other)"
        elif direction == "incoming":
            pattern = "(e)<-[r]-(other)"
        else:
            pattern = "(e)-[r]-(other)"

        async with self._async_driver.session() as session:
            result = await session.run(
                f"""
                MATCH (e:Entity {{id: $id, tenant_id: $tenant_id}})
                MATCH {pattern}
                WHERE other.tenant_id = $tenant_id
                RETURN r, other
                """,
                id=str(entity_id),
                tenant_id=str(tenant_id),
            )

            relationships = []
            async for record in result:
                rel = record["r"]
                other = record["other"]
                relationships.append(
                    {
                        "id": rel.get("id"),
                        "type": rel.type,
                        "confidence": rel.get("confidence_score", 1.0),
                        "related_entity": {
                            "id": other.get("id"),
                            "name": other.get("name"),
                            "type": other.get("type"),
                        },
                    }
                )

            return relationships

    # =========================================================================
    # Schema Management
    # =========================================================================

    def create_indexes(self) -> None:
        """Create indexes for performance."""
        indexes = [
            "CREATE INDEX IF NOT EXISTS FOR (e:Entity) ON (e.id)",
            "CREATE INDEX IF NOT EXISTS FOR (e:Entity) ON (e.tenant_id)",
            "CREATE INDEX IF NOT EXISTS FOR (e:Entity) ON (e.name)",
            "CREATE INDEX IF NOT EXISTS FOR (e:Entity) ON (e.normalized_name)",
            "CREATE INDEX IF NOT EXISTS FOR (e:Entity) ON (e.type)",
            # Fulltext index for search
            """
            CREATE FULLTEXT INDEX entity_name_search IF NOT EXISTS
            FOR (e:Entity) ON EACH [e.name, e.description]
            """,
        ]

        with self._sync_driver.session() as session:
            for index_query in indexes:
                try:
                    session.run(index_query)
                except Exception as e:
                    logger.warning(f"Index creation warning: {e}")

        logger.info("Neo4j indexes created")

    def create_constraints(self) -> None:
        """Create uniqueness constraints."""
        constraints = [
            """
            CREATE CONSTRAINT entity_id_unique IF NOT EXISTS
            FOR (e:Entity) REQUIRE e.id IS UNIQUE
            """,
        ]

        with self._sync_driver.session() as session:
            for constraint_query in constraints:
                try:
                    session.run(constraint_query)
                except Exception as e:
                    logger.warning(f"Constraint creation warning: {e}")

        logger.info("Neo4j constraints created")


def _serialize_properties(props: dict) -> str:
    """Serialize properties dict to JSON string for Neo4j."""
    import json

    return json.dumps(props) if props else "{}"
