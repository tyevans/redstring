"""
Neo4j schema setup and constraints.

This module provides functions to set up, verify, and manage the Neo4j schema
for the knowledge graph, including constraints and indexes for efficient querying.

Schema Overview:
---------------
Node Labels:
- Entity: All extracted entities (with type property for differentiation)

Node Properties (Entity):
- id: UUID (unique) - Primary identifier
- tenant_id: UUID (required) - For multi-tenant isolation
- type: String - Entity type (function, class, module, concept, etc.)
- name: String - Human-readable name
- description: String (optional) - Entity description
- properties: Map - Type-specific additional properties
- created_at: DateTime - Creation timestamp
- updated_at: DateTime - Last update timestamp

Relationship Types:
- USES: Entity uses another entity
- IMPLEMENTS: Implements interface/pattern
- EXTENDS: Class inheritance
- CALLS: Function calls another
- RETURNS: Function returns type
- DOCUMENTED_IN: Entity documented on page
- DEPENDS_ON: Module dependency
- EXAMPLE_OF: Example demonstrates concept
- PART_OF: Entity is part of another
- RELATED_TO: General relationship

Relationship Properties:
- id: UUID (optional) - Relationship identifier
- confidence: Float - Confidence score
- properties: Map - Additional properties
- created_at: DateTime - Creation timestamp
"""

import logging
from typing import Any

from kg_builder.services.neo4j import Neo4jService, get_neo4j_service

logger = logging.getLogger(__name__)


# =============================================================================
# Schema Constants
# =============================================================================

# Constraint definitions
CONSTRAINTS = {
    "entity_id_unique": """
        CREATE CONSTRAINT entity_id_unique IF NOT EXISTS
        FOR (e:Entity) REQUIRE e.id IS UNIQUE
    """,
}

# Index definitions
INDEXES = {
    "entity_tenant_idx": """
        CREATE INDEX entity_tenant_idx IF NOT EXISTS
        FOR (e:Entity) ON (e.tenant_id)
    """,
    "entity_type_idx": """
        CREATE INDEX entity_type_idx IF NOT EXISTS
        FOR (e:Entity) ON (e.type)
    """,
    "entity_name_idx": """
        CREATE INDEX entity_name_idx IF NOT EXISTS
        FOR (e:Entity) ON (e.name)
    """,
    "entity_tenant_type_idx": """
        CREATE INDEX entity_tenant_type_idx IF NOT EXISTS
        FOR (e:Entity) ON (e.tenant_id, e.type)
    """,
}


# =============================================================================
# Schema Setup Functions
# =============================================================================


async def setup_neo4j_schema(service: Neo4jService | None = None) -> dict[str, Any]:
    """Set up Neo4j schema with constraints and indexes.

    Creates:
    - Uniqueness constraint on Entity.id
    - Indexes for common query patterns (tenant_id, type, name, composite)

    Args:
        service: Optional Neo4jService instance. If not provided, gets the
                 global service instance.

    Returns:
        Dict with created constraints and indexes information.

    Raises:
        RuntimeError: If Neo4j connection is not available.

    Example:
        >>> service = Neo4jService()
        >>> await service.connect()
        >>> result = await setup_neo4j_schema(service)
        >>> print(result["constraints_created"])
        ['entity_id_unique']
    """
    svc = service or await get_neo4j_service()

    constraints_created = []
    indexes_created = []

    async with svc.session() as session:
        # =====================================================================
        # Create Constraints
        # =====================================================================
        logger.info("Setting up Neo4j constraints...")

        for constraint_name, constraint_query in CONSTRAINTS.items():
            try:
                await session.run(constraint_query)
                constraints_created.append(constraint_name)
                logger.debug(f"Created constraint: {constraint_name}")
            except Exception as e:
                logger.warning(f"Constraint {constraint_name} may already exist: {e}")
                constraints_created.append(constraint_name)

        # =====================================================================
        # Create Indexes
        # =====================================================================
        logger.info("Setting up Neo4j indexes...")

        for index_name, index_query in INDEXES.items():
            try:
                await session.run(index_query)
                indexes_created.append(index_name)
                logger.debug(f"Created index: {index_name}")
            except Exception as e:
                logger.warning(f"Index {index_name} may already exist: {e}")
                indexes_created.append(index_name)

    logger.info(
        f"Neo4j schema setup completed. "
        f"Constraints: {len(constraints_created)}, Indexes: {len(indexes_created)}"
    )

    return {
        "constraints_created": constraints_created,
        "indexes_created": indexes_created,
    }


async def verify_schema(service: Neo4jService | None = None) -> dict[str, Any]:
    """Verify Neo4j schema is properly configured.

    Checks that all expected constraints and indexes exist in the database.

    Args:
        service: Optional Neo4jService instance. If not provided, gets the
                 global service instance.

    Returns:
        Dict with schema verification results including:
        - constraints_count: Total number of constraints
        - indexes_count: Total number of indexes
        - constraints: List of constraint names
        - indexes: List of index names
        - expected_constraints: List of expected constraint names
        - expected_indexes: List of expected index names
        - missing_constraints: Constraints that should exist but don't
        - missing_indexes: Indexes that should exist but don't
        - is_valid: Boolean indicating if all expected schema elements exist

    Example:
        >>> result = await verify_schema(service)
        >>> print(result["is_valid"])
        True
        >>> print(result["constraints"])
        ['entity_id_unique']
    """
    svc = service or await get_neo4j_service()

    async with svc.session() as session:
        # Check constraints
        result = await session.run("SHOW CONSTRAINTS")
        constraints = []
        async for record in result:
            constraint_name = record.get("name")
            if constraint_name:
                constraints.append(constraint_name)

        # Check indexes
        result = await session.run("SHOW INDEXES")
        indexes = []
        async for record in result:
            index_name = record.get("name")
            if index_name:
                indexes.append(index_name)

    # Calculate expected vs actual
    expected_constraints = list(CONSTRAINTS.keys())
    expected_indexes = list(INDEXES.keys())

    missing_constraints = [c for c in expected_constraints if c not in constraints]
    missing_indexes = [i for i in expected_indexes if i not in indexes]

    is_valid = len(missing_constraints) == 0 and len(missing_indexes) == 0

    return {
        "constraints_count": len(constraints),
        "indexes_count": len(indexes),
        "constraints": constraints,
        "indexes": indexes,
        "expected_constraints": expected_constraints,
        "expected_indexes": expected_indexes,
        "missing_constraints": missing_constraints,
        "missing_indexes": missing_indexes,
        "is_valid": is_valid,
    }


async def drop_schema(service: Neo4jService | None = None) -> dict[str, Any]:
    """Drop Neo4j schema constraints and indexes.

    WARNING: This function is intended for testing purposes only.
    It will remove all constraints and indexes defined in this module.

    Args:
        service: Optional Neo4jService instance. If not provided, gets the
                 global service instance.

    Returns:
        Dict with dropped constraints and indexes information.

    Example:
        >>> # For testing only
        >>> result = await drop_schema(service)
        >>> print(result["constraints_dropped"])
        ['entity_id_unique']
    """
    svc = service or await get_neo4j_service()

    constraints_dropped = []
    indexes_dropped = []

    async with svc.session() as session:
        # =====================================================================
        # Drop Indexes (must be done before constraints)
        # =====================================================================
        logger.info("Dropping Neo4j indexes...")

        for index_name in INDEXES.keys():
            try:
                await session.run(f"DROP INDEX {index_name} IF EXISTS")
                indexes_dropped.append(index_name)
                logger.debug(f"Dropped index: {index_name}")
            except Exception as e:
                logger.warning(f"Could not drop index {index_name}: {e}")

        # =====================================================================
        # Drop Constraints
        # =====================================================================
        logger.info("Dropping Neo4j constraints...")

        for constraint_name in CONSTRAINTS.keys():
            try:
                await session.run(f"DROP CONSTRAINT {constraint_name} IF EXISTS")
                constraints_dropped.append(constraint_name)
                logger.debug(f"Dropped constraint: {constraint_name}")
            except Exception as e:
                logger.warning(f"Could not drop constraint {constraint_name}: {e}")

    logger.info(
        f"Neo4j schema dropped. "
        f"Constraints: {len(constraints_dropped)}, Indexes: {len(indexes_dropped)}"
    )

    return {
        "constraints_dropped": constraints_dropped,
        "indexes_dropped": indexes_dropped,
    }


async def get_schema_info(service: Neo4jService | None = None) -> dict[str, Any]:
    """Get detailed information about the current Neo4j schema.

    Provides comprehensive information about all constraints and indexes,
    including their properties and state.

    Args:
        service: Optional Neo4jService instance. If not provided, gets the
                 global service instance.

    Returns:
        Dict with detailed schema information.
    """
    svc = service or await get_neo4j_service()

    async with svc.session() as session:
        # Get constraint details
        result = await session.run("SHOW CONSTRAINTS")
        constraints = []
        async for record in result:
            constraints.append(dict(record))

        # Get index details
        result = await session.run("SHOW INDEXES")
        indexes = []
        async for record in result:
            indexes.append(dict(record))

    return {
        "constraints": constraints,
        "indexes": indexes,
        "constraint_count": len(constraints),
        "index_count": len(indexes),
    }
