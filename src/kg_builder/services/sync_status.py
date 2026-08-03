"""
Neo4j sync status tracking and retry utilities.

This module provides the SyncStatusService for tracking the synchronization
status of entities and relationships between PostgreSQL and Neo4j.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kg_builder.models.extracted_entity import EntityRelationship, ExtractedEntity


class SyncStatusService:
    """
    Track and manage Neo4j sync status for entities and relationships.

    This service provides methods to:
    - Query entities and relationships that need synchronization
    - Calculate sync statistics for monitoring
    - Track and retry failed sync operations

    Attributes:
        _session: AsyncSession for database operations
    """

    def __init__(self, session: AsyncSession) -> None:
        """
        Initialize the sync status service.

        Args:
            session: SQLAlchemy async session for database operations
        """
        self._session = session

    async def get_unsynced_entities(
        self,
        tenant_id: UUID | None = None,
        limit: int = 100,
    ) -> list[ExtractedEntity]:
        """
        Get entities not yet synced to Neo4j.

        Args:
            tenant_id: Optional tenant ID to filter by. If None, returns
                entities from all tenants (use with caution).
            limit: Maximum number of entities to return. Defaults to 100.

        Returns:
            List of ExtractedEntity objects that have not been synced to Neo4j.
        """
        stmt = select(ExtractedEntity).where(
            ExtractedEntity.synced_to_neo4j == False  # noqa: E712
        )

        if tenant_id is not None:
            stmt = stmt.where(ExtractedEntity.tenant_id == tenant_id)

        # Order by creation time to process oldest first
        stmt = stmt.order_by(ExtractedEntity.id).limit(limit)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_unsynced_relationships(
        self,
        tenant_id: UUID | None = None,
        limit: int = 100,
    ) -> list[EntityRelationship]:
        """
        Get relationships not yet synced to Neo4j.

        Args:
            tenant_id: Optional tenant ID to filter by. If None, returns
                relationships from all tenants (use with caution).
            limit: Maximum number of relationships to return. Defaults to 100.

        Returns:
            List of EntityRelationship objects that have not been synced to Neo4j.
        """
        stmt = select(EntityRelationship).where(
            EntityRelationship.synced_to_neo4j == False  # noqa: E712
        )

        if tenant_id is not None:
            stmt = stmt.where(EntityRelationship.tenant_id == tenant_id)

        # Order by ID for consistent ordering
        stmt = stmt.order_by(EntityRelationship.id).limit(limit)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_sync_stats(
        self,
        tenant_id: UUID | None = None,
    ) -> dict:
        """
        Get sync statistics for entities and relationships.

        Args:
            tenant_id: Optional tenant ID to filter by. If None, returns
                aggregate stats across all tenants.

        Returns:
            Dictionary containing:
            - total_entities: Total number of entities
            - synced_entities: Number of entities synced to Neo4j
            - pending_entities: Number of entities not yet synced
            - sync_percentage_entities: Percentage of entities synced
            - total_relationships: Total number of relationships
            - synced_relationships: Number of relationships synced
            - pending_relationships: Number of relationships not yet synced
            - sync_percentage_relationships: Percentage of relationships synced
        """
        # Entity stats query
        entity_stmt = select(
            func.count().label("total"),
            func.sum(
                case(
                    (ExtractedEntity.synced_to_neo4j == True, 1),  # noqa: E712
                    else_=0,
                )
            ).label("synced"),
        ).select_from(ExtractedEntity)

        if tenant_id is not None:
            entity_stmt = entity_stmt.where(ExtractedEntity.tenant_id == tenant_id)

        entity_result = await self._session.execute(entity_stmt)
        entity_row = entity_result.one()

        total_entities = entity_row.total or 0
        synced_entities = int(entity_row.synced or 0)
        pending_entities = total_entities - synced_entities

        # Relationship stats query
        rel_stmt = select(
            func.count().label("total"),
            func.sum(
                case(
                    (EntityRelationship.synced_to_neo4j == True, 1),  # noqa: E712
                    else_=0,
                )
            ).label("synced"),
        ).select_from(EntityRelationship)

        if tenant_id is not None:
            rel_stmt = rel_stmt.where(EntityRelationship.tenant_id == tenant_id)

        rel_result = await self._session.execute(rel_stmt)
        rel_row = rel_result.one()

        total_relationships = rel_row.total or 0
        synced_relationships = int(rel_row.synced or 0)
        pending_relationships = total_relationships - synced_relationships

        return {
            "total_entities": total_entities,
            "synced_entities": synced_entities,
            "pending_entities": pending_entities,
            "sync_percentage_entities": (
                round(synced_entities / total_entities * 100, 2) if total_entities > 0 else 100.0
            ),
            "total_relationships": total_relationships,
            "synced_relationships": synced_relationships,
            "pending_relationships": pending_relationships,
            "sync_percentage_relationships": (
                round(synced_relationships / total_relationships * 100, 2)
                if total_relationships > 0
                else 100.0
            ),
        }

    async def mark_entity_synced(
        self,
        entity_id: UUID,
        neo4j_node_id: str,
    ) -> bool:
        """
        Mark an entity as successfully synced to Neo4j.

        Args:
            entity_id: UUID of the entity to mark as synced
            neo4j_node_id: The Neo4j node element ID

        Returns:
            True if the entity was updated, False if not found
        """
        stmt = (
            update(ExtractedEntity)
            .where(ExtractedEntity.id == entity_id)
            .values(
                synced_to_neo4j=True,
                neo4j_node_id=neo4j_node_id,
                synced_at=datetime.now(UTC),
            )
        )

        result = await self._session.execute(stmt)
        await self._session.commit()
        return result.rowcount > 0

    async def mark_relationship_synced(
        self,
        relationship_id: UUID,
        neo4j_relationship_id: str,
    ) -> bool:
        """
        Mark a relationship as successfully synced to Neo4j.

        Args:
            relationship_id: UUID of the relationship to mark as synced
            neo4j_relationship_id: The Neo4j relationship element ID

        Returns:
            True if the relationship was updated, False if not found
        """
        stmt = (
            update(EntityRelationship)
            .where(EntityRelationship.id == relationship_id)
            .values(
                synced_to_neo4j=True,
                neo4j_relationship_id=neo4j_relationship_id,
            )
        )

        result = await self._session.execute(stmt)
        await self._session.commit()
        return result.rowcount > 0

    async def mark_sync_failed(
        self,
        entity_id: UUID,
        error_message: str,
    ) -> None:
        """
        Mark an entity sync as failed.

        Note: This method currently logs the failure but does not persist
        the error to the database. A future enhancement could add a
        sync_error or sync_retry_count column to the ExtractedEntity model.

        Args:
            entity_id: UUID of the entity that failed to sync
            error_message: Description of the sync failure
        """
        # For now, this is a placeholder that could be extended to:
        # 1. Store error in a sync_error column
        # 2. Increment a retry counter
        # 3. Set a next_retry_at timestamp
        # 4. Emit a sync failure event
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(
            "Sync failed for entity %s: %s",
            entity_id,
            error_message,
            extra={"entity_id": str(entity_id), "error": error_message},
        )

    async def retry_failed_syncs(
        self,
        batch_size: int = 50,
        tenant_id: UUID | None = None,
    ) -> list[ExtractedEntity]:
        """
        Get entities that need sync retry.

        This returns unsynced entities ordered by ID, which can be used
        to retry synchronization. The caller is responsible for actually
        performing the sync operation.

        Args:
            batch_size: Maximum number of entities to return. Defaults to 50.
            tenant_id: Optional tenant ID to filter by.

        Returns:
            List of ExtractedEntity objects that need synchronization.
        """
        return await self.get_unsynced_entities(
            tenant_id=tenant_id,
            limit=batch_size,
        )

    async def reset_sync_status(
        self,
        entity_id: UUID,
    ) -> bool:
        """
        Reset the sync status of an entity for re-synchronization.

        This clears the Neo4j node ID and marks the entity as unsynced,
        allowing it to be picked up by the sync process again.

        Args:
            entity_id: UUID of the entity to reset

        Returns:
            True if the entity was updated, False if not found
        """
        stmt = (
            update(ExtractedEntity)
            .where(ExtractedEntity.id == entity_id)
            .values(
                synced_to_neo4j=False,
                neo4j_node_id=None,
                synced_at=None,
            )
        )

        result = await self._session.execute(stmt)
        await self._session.commit()
        return result.rowcount > 0


async def get_sync_status_service(session: AsyncSession) -> SyncStatusService:
    """
    Factory function to create a SyncStatusService instance.

    Args:
        session: SQLAlchemy async session

    Returns:
        Configured SyncStatusService instance
    """
    return SyncStatusService(session)
