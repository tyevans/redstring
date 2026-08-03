"""
MergeService for executing entity merges.

This module provides the core service for merging duplicate entities
in the knowledge graph. It handles:
- Merge validation and authorization
- Property merging with configurable strategies
- Relationship transfer to canonical entity
- EntityAlias record creation
- MergeHistory audit trail
- Domain event emission

This is Stage 5 of the consolidation pipeline, executing the actual
merge operations after candidates have been identified and scored.

Example:
    from kg_builder.services.consolidation import MergeService, PropertyMergeStrategy

    service = MergeService(session, event_bus)

    # Check if merge is valid
    if await service.is_mergeable(entity_a, entity_b, tenant_id):
        # Execute the merge
        result = await service.merge_entities(
            canonical_entity=entity_a,
            merged_entities=[entity_b],
            tenant_id=tenant_id,
            merge_reason="auto_high_confidence",
            similarity_scores=scores.to_dict(),
        )
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kg_builder.events.consolidation import (
    AliasCreated,
    EntitiesMerged,
    EntitySplit,
    MergeUndone,
)
from kg_builder.models.entity_alias import EntityAlias
from kg_builder.models.extracted_entity import (
    EntityRelationship,
    EntityType,
    ExtractedEntity,
    ExtractionMethod,
)
from kg_builder.models.merge_history import MergeEventType, MergeHistory

if TYPE_CHECKING:
    from eventsource import EventBus

logger = logging.getLogger(__name__)


class MergeError(Exception):
    """Error during entity merge operation."""

    pass


class MergeValidationError(MergeError):
    """Validation error preventing merge."""

    pass


class MergeAuthorizationError(MergeError):
    """Authorization error preventing merge."""

    pass


class MergeUndoError(MergeError):
    """Error when attempting to undo a merge."""

    pass


class EntitySplitError(MergeError):
    """Error when attempting to split an entity."""

    pass


class PropertyMergeStrategy(str, Enum):
    """Strategies for merging entity properties."""

    PREFER_CANONICAL = "prefer_canonical"  # Keep canonical entity's value
    PREFER_MERGED = "prefer_merged"  # Take merged entity's value
    UNION = "union"  # Union of arrays/sets
    LATEST = "latest"  # Take most recently updated value
    DEEP_MERGE = "deep_merge"  # Deep merge for nested dicts (extracted_data)


# Default strategies for common property types
DEFAULT_PROPERTY_STRATEGIES: dict[str, PropertyMergeStrategy] = {
    # Properties that should take the canonical value
    "name": PropertyMergeStrategy.PREFER_CANONICAL,
    "normalized_name": PropertyMergeStrategy.PREFER_CANONICAL,
    "entity_type": PropertyMergeStrategy.PREFER_CANONICAL,
    # Properties that should union
    "tags": PropertyMergeStrategy.UNION,
    "categories": PropertyMergeStrategy.UNION,
    "aliases": PropertyMergeStrategy.UNION,
    # Properties that should deep merge
    "extracted_data": PropertyMergeStrategy.DEEP_MERGE,
    "properties": PropertyMergeStrategy.DEEP_MERGE,
    "external_ids": PropertyMergeStrategy.DEEP_MERGE,
    # Properties that take latest
    "description": PropertyMergeStrategy.LATEST,
    "confidence_score": PropertyMergeStrategy.LATEST,
}


class MergeResult:
    """
    Result of a merge operation.

    Attributes:
        canonical_entity_id: ID of the canonical (surviving) entity
        merged_entity_ids: IDs of entities that were merged
        aliases_created: List of EntityAlias records created
        relationships_transferred: Count of relationships transferred
        properties_merged: Details of property merge operations
        merge_history_id: ID of the MergeHistory audit record
        event_id: ID of the EntitiesMerged domain event
    """

    def __init__(
        self,
        canonical_entity_id: UUID,
        merged_entity_ids: list[UUID],
        aliases_created: list[EntityAlias],
        relationships_transferred: int,
        properties_merged: dict[str, Any],
        merge_history_id: UUID,
        event_id: UUID,
    ):
        self.canonical_entity_id = canonical_entity_id
        self.merged_entity_ids = merged_entity_ids
        self.aliases_created = aliases_created
        self.relationships_transferred = relationships_transferred
        self.properties_merged = properties_merged
        self.merge_history_id = merge_history_id
        self.event_id = event_id

    def __repr__(self) -> str:
        return (
            f"<MergeResult canonical={self.canonical_entity_id} "
            f"merged={len(self.merged_entity_ids)} "
            f"aliases={len(self.aliases_created)} "
            f"relationships={self.relationships_transferred}>"
        )


class UndoResult:
    """
    Result of an undo operation.

    Attributes:
        original_merge_event_id: ID of the merge event that was undone
        canonical_entity_id: ID of the canonical entity (unchanged)
        restored_entity_ids: IDs of the newly created restored entities
        aliases_removed: Number of alias records removed
        relationships_restored: Number of relationships recreated
        undo_history_id: ID of the MergeHistory record for the undo
        event_id: ID of the MergeUndone domain event
    """

    def __init__(
        self,
        original_merge_event_id: UUID,
        canonical_entity_id: UUID,
        restored_entity_ids: list[UUID],
        aliases_removed: int,
        relationships_restored: int,
        undo_history_id: UUID,
        event_id: UUID,
    ):
        self.original_merge_event_id = original_merge_event_id
        self.canonical_entity_id = canonical_entity_id
        self.restored_entity_ids = restored_entity_ids
        self.aliases_removed = aliases_removed
        self.relationships_restored = relationships_restored
        self.undo_history_id = undo_history_id
        self.event_id = event_id

    def __repr__(self) -> str:
        return (
            f"<UndoResult original_merge={self.original_merge_event_id} "
            f"restored={len(self.restored_entity_ids)} entities "
            f"relationships={self.relationships_restored}>"
        )


class SplitResult:
    """
    Result of a split operation.

    Attributes:
        original_entity_id: ID of the entity that was split
        new_entity_ids: IDs of the newly created entities
        new_entities: The newly created ExtractedEntity objects
        relationships_redistributed: Number of relationships reassigned
        aliases_redistributed: Number of aliases reassigned
        split_history_id: ID of the MergeHistory record for the split
        event_id: ID of the EntitySplit domain event
    """

    def __init__(
        self,
        original_entity_id: UUID,
        new_entity_ids: list[UUID],
        new_entities: list[ExtractedEntity],
        relationships_redistributed: int,
        aliases_redistributed: int,
        split_history_id: UUID,
        event_id: UUID,
    ):
        self.original_entity_id = original_entity_id
        self.new_entity_ids = new_entity_ids
        self.new_entities = new_entities
        self.relationships_redistributed = relationships_redistributed
        self.aliases_redistributed = aliases_redistributed
        self.split_history_id = split_history_id
        self.event_id = event_id

    def __repr__(self) -> str:
        return (
            f"<SplitResult original={self.original_entity_id} "
            f"new_entities={len(self.new_entity_ids)} "
            f"relationships={self.relationships_redistributed}>"
        )


class MergeService:
    """
    Core service for executing entity merges.

    The MergeService orchestrates the complete merge process:
    1. Validates merge preconditions
    2. Merges entity properties using configured strategies
    3. Transfers relationships to canonical entity
    4. Creates EntityAlias records for merged entities
    5. Updates merged entities to point to canonical
    6. Creates MergeHistory audit record
    7. Emits domain events (EntitiesMerged, AliasCreated)

    The service ensures atomicity - either all operations succeed
    or none do. The caller is responsible for committing the session.

    Example:
        async with session.begin():
            service = MergeService(session)
            result = await service.merge_entities(
                canonical_entity=canonical,
                merged_entities=[duplicate1, duplicate2],
                tenant_id=tenant_id,
                merge_reason="user_approved",
            )
        # Commit happens automatically with context manager
    """

    def __init__(
        self,
        session: AsyncSession,
        event_bus: EventBus | None = None,
        property_strategies: dict[str, PropertyMergeStrategy] | None = None,
    ):
        """
        Initialize the merge service.

        Args:
            session: Async database session for operations
            event_bus: Optional event bus for emitting domain events.
                      If not provided, events are collected but not published.
            property_strategies: Custom property merge strategies.
                               Defaults to DEFAULT_PROPERTY_STRATEGIES.
        """
        self.session = session
        self.event_bus = event_bus
        self.property_strategies = property_strategies or DEFAULT_PROPERTY_STRATEGIES
        self._pending_events: list = []

    async def is_mergeable(
        self,
        entity_a: ExtractedEntity,
        entity_b: ExtractedEntity,
        tenant_id: UUID,
    ) -> bool:
        """
        Check if two entities can be merged.

        Validates:
        - Both entities exist and are accessible
        - Both entities belong to the same tenant
        - Both entities are canonical (not already aliases)
        - Entities are not the same
        - No circular reference would be created

        Args:
            entity_a: First entity
            entity_b: Second entity
            tenant_id: Tenant ID for isolation check

        Returns:
            True if entities can be merged, False otherwise
        """
        try:
            self._validate_merge_preconditions(
                canonical=entity_a,
                merged_entities=[entity_b],
                tenant_id=tenant_id,
            )
            return True
        except MergeValidationError:
            return False

    async def validate_merge(
        self,
        canonical_entity: ExtractedEntity,
        merged_entities: list[ExtractedEntity],
        tenant_id: UUID,
        user_id: UUID | None = None,
    ) -> list[str]:
        """
        Validate a proposed merge and return any issues.

        Performs comprehensive validation and returns a list of
        validation errors/warnings. Empty list means merge is valid.

        Args:
            canonical_entity: Entity to merge into (survives)
            merged_entities: Entities to merge (become aliases)
            tenant_id: Tenant ID for isolation check
            user_id: Optional user ID for authorization check

        Returns:
            List of validation error messages (empty if valid)
        """
        issues: list[str] = []

        # Check canonical entity
        if canonical_entity is None:
            issues.append("Canonical entity is required")
            return issues

        if canonical_entity.tenant_id != tenant_id:
            issues.append("Canonical entity does not belong to this tenant")

        if not canonical_entity.is_canonical:
            issues.append(
                f"Entity {canonical_entity.id} is already an alias "
                f"of {canonical_entity.is_alias_of}"
            )

        # Check merged entities
        if not merged_entities:
            issues.append("At least one entity to merge is required")
            return issues

        for entity in merged_entities:
            if entity.tenant_id != tenant_id:
                issues.append(f"Entity {entity.id} does not belong to this tenant")

            if not entity.is_canonical:
                issues.append(
                    f"Entity {entity.id} is already an alias of {entity.is_alias_of}"
                )

            if entity.id == canonical_entity.id:
                issues.append("Cannot merge an entity with itself")

            if entity.entity_type != canonical_entity.entity_type:
                issues.append(
                    f"Entity {entity.id} has different type "
                    f"({entity.entity_type.value}) than canonical "
                    f"({canonical_entity.entity_type.value})"
                )

        return issues

    async def merge_entities(
        self,
        canonical_entity: ExtractedEntity,
        merged_entities: list[ExtractedEntity],
        tenant_id: UUID,
        merge_reason: str,
        similarity_scores: dict | None = None,
        merged_by_user_id: UUID | None = None,
    ) -> MergeResult:
        """
        Execute a merge operation.

        Merges one or more entities into a canonical entity:
        1. Validates preconditions
        2. Merges properties using configured strategies
        3. Transfers relationships to canonical entity
        4. Creates EntityAlias records
        5. Marks merged entities as non-canonical
        6. Creates audit trail
        7. Emits domain events

        Args:
            canonical_entity: Entity to merge into (survives)
            merged_entities: Entities to be merged (become aliases)
            tenant_id: Tenant ID for isolation
            merge_reason: Reason for merge (auto_high_confidence,
                         user_approved, batch, manual)
            similarity_scores: Optional similarity scores dict for audit
            merged_by_user_id: Optional user who approved/triggered merge

        Returns:
            MergeResult with details of the merge operation

        Raises:
            MergeValidationError: If preconditions are not met
            MergeError: If merge operation fails
        """
        # Validate preconditions
        self._validate_merge_preconditions(
            canonical_entity, merged_entities, tenant_id
        )

        logger.info(
            f"Starting merge: canonical={canonical_entity.id}, "
            f"merged={[e.id for e in merged_entities]}, reason={merge_reason}"
        )

        # Generate IDs for tracking
        event_id = uuid.uuid4()
        merge_history_id = uuid.uuid4()
        now = datetime.now(UTC)

        # Track merge details for audit
        property_merge_details: dict[str, Any] = {}
        relationships_transferred = 0
        aliases_created: list[EntityAlias] = []

        try:
            # 1. Merge properties from each entity
            for entity in merged_entities:
                details = self._merge_properties(canonical_entity, entity)
                property_merge_details[str(entity.id)] = details

            # 2. Transfer relationships from merged entities
            for entity in merged_entities:
                count = await self._transfer_relationships(
                    canonical_entity, entity, tenant_id
                )
                relationships_transferred += count

            # 3. Create EntityAlias records with original properties for undo
            for entity in merged_entities:
                alias = EntityAlias(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    canonical_entity_id=canonical_entity.id,
                    alias_name=entity.name,
                    original_entity_id=entity.id,
                    source_page_id=entity.source_page_id,
                    merged_at=now,
                    merge_event_id=event_id,
                    merge_reason=merge_reason,
                    # Store original properties for undo support
                    original_entity_type=entity.entity_type.value if entity.entity_type else None,
                    original_normalized_name=entity.normalized_name,
                    original_description=entity.description,
                    original_properties=entity.properties or {},
                    original_external_ids=entity.external_ids or {},
                    original_confidence_score=entity.confidence_score or 1.0,
                    original_source_text=entity.source_text,
                )
                self.session.add(alias)
                aliases_created.append(alias)

                # Emit AliasCreated event
                alias_event = AliasCreated(
                    aggregate_id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    alias_id=alias.id,
                    canonical_entity_id=canonical_entity.id,
                    alias_name=entity.name,
                    original_entity_id=entity.id,
                    merge_event_id=event_id,
                )
                self._pending_events.append(alias_event)

            # 4. Mark merged entities as non-canonical (aliases)
            merged_entity_ids = [e.id for e in merged_entities]
            await self.session.execute(
                update(ExtractedEntity)
                .where(ExtractedEntity.id.in_(merged_entity_ids))
                .values(
                    is_canonical=False,
                    is_alias_of=canonical_entity.id,
                )
            )

            # 5. Create MergeHistory audit record
            history = MergeHistory(
                id=merge_history_id,
                tenant_id=tenant_id,
                event_id=event_id,
                event_type=MergeEventType.ENTITIES_MERGED,
                canonical_entity_id=canonical_entity.id,
                affected_entity_ids=[canonical_entity.id] + merged_entity_ids,
                merge_reason=merge_reason,
                similarity_scores=similarity_scores,
                details={
                    "property_merge_details": property_merge_details,
                    "relationships_transferred": relationships_transferred,
                    "aliases_created": [str(a.id) for a in aliases_created],
                },
                performed_by=merged_by_user_id,
                performed_at=now,
            )
            self.session.add(history)

            # 6. Emit EntitiesMerged event
            merge_event = EntitiesMerged(
                aggregate_id=event_id,
                tenant_id=tenant_id,
                canonical_entity_id=canonical_entity.id,
                merged_entity_ids=merged_entity_ids,
                merge_reason=merge_reason,
                similarity_scores=similarity_scores or {},
                property_merge_details=property_merge_details,
                relationship_transfer_count=relationships_transferred,
                merged_by_user_id=merged_by_user_id,
            )
            self._pending_events.append(merge_event)

            # Publish events if event bus is configured
            if self.event_bus:
                for event in self._pending_events:
                    await self.event_bus.publish(event)
            self._pending_events.clear()

            logger.info(
                f"Merge completed: canonical={canonical_entity.id}, "
                f"merged={len(merged_entities)}, "
                f"relationships_transferred={relationships_transferred}"
            )

            return MergeResult(
                canonical_entity_id=canonical_entity.id,
                merged_entity_ids=merged_entity_ids,
                aliases_created=aliases_created,
                relationships_transferred=relationships_transferred,
                properties_merged=property_merge_details,
                merge_history_id=merge_history_id,
                event_id=event_id,
            )

        except Exception as e:
            logger.error(f"Merge failed: {e}", exc_info=True)
            self._pending_events.clear()
            raise MergeError(f"Merge operation failed: {e}") from e

    def _validate_merge_preconditions(
        self,
        canonical: ExtractedEntity,
        merged_entities: list[ExtractedEntity],
        tenant_id: UUID,
    ) -> None:
        """
        Validate merge preconditions.

        Raises:
            MergeValidationError: If any precondition fails
        """
        if canonical is None:
            raise MergeValidationError("Canonical entity is required")

        if not merged_entities:
            raise MergeValidationError("At least one entity to merge is required")

        if canonical.tenant_id != tenant_id:
            raise MergeValidationError(
                f"Canonical entity {canonical.id} does not belong to tenant {tenant_id}"
            )

        if not canonical.is_canonical:
            raise MergeValidationError(
                f"Entity {canonical.id} is already an alias of {canonical.is_alias_of}"
            )

        for entity in merged_entities:
            if entity.tenant_id != tenant_id:
                raise MergeValidationError(
                    f"Entity {entity.id} does not belong to tenant {tenant_id}"
                )

            if entity.id == canonical.id:
                raise MergeValidationError("Cannot merge an entity with itself")

            if not entity.is_canonical:
                raise MergeValidationError(
                    f"Entity {entity.id} is already an alias of {entity.is_alias_of}"
                )

    def _merge_properties(
        self,
        canonical: ExtractedEntity,
        merged: ExtractedEntity,
    ) -> dict[str, Any]:
        """
        Merge properties from merged entity into canonical.

        Uses configured strategies for each property type.

        Args:
            canonical: Entity to merge into
            merged: Entity being merged

        Returns:
            Dictionary describing the merge operations performed
        """
        merge_details: dict[str, Any] = {}

        # Merge the properties JSONB field
        if merged.properties:
            strategy = self.property_strategies.get(
                "properties", PropertyMergeStrategy.DEEP_MERGE
            )
            merged_props, details = merge_property(
                canonical.properties or {},
                merged.properties,
                strategy,
            )
            canonical.properties = merged_props
            merge_details["properties"] = details

        # Merge external_ids JSONB field
        if merged.external_ids:
            strategy = self.property_strategies.get(
                "external_ids", PropertyMergeStrategy.DEEP_MERGE
            )
            merged_ids, details = merge_property(
                canonical.external_ids or {},
                merged.external_ids,
                strategy,
            )
            canonical.external_ids = merged_ids
            merge_details["external_ids"] = details

        # Update description if merged has one and canonical doesn't
        if merged.description and not canonical.description:
            canonical.description = merged.description
            merge_details["description"] = {
                "action": "adopted_from_merged",
                "value": merged.description[:100] + "..."
                if len(merged.description) > 100
                else merged.description,
            }

        # Take higher confidence score
        if merged.confidence_score > canonical.confidence_score:
            original = canonical.confidence_score
            canonical.confidence_score = merged.confidence_score
            merge_details["confidence_score"] = {
                "action": "updated",
                "from": original,
                "to": merged.confidence_score,
            }

        return merge_details

    async def _transfer_relationships(
        self,
        canonical: ExtractedEntity,
        merged: ExtractedEntity,
        tenant_id: UUID,
    ) -> int:
        """
        Transfer relationships from merged entity to canonical.

        Handles both outgoing and incoming relationships:
        - Outgoing: merged -> target becomes canonical -> target
        - Incoming: source -> merged becomes source -> canonical

        Avoids creating duplicate relationships by checking existing
        relationships on the canonical entity.

        Args:
            canonical: Entity to transfer relationships to
            merged: Entity to transfer relationships from
            tenant_id: Tenant ID for relationship queries

        Returns:
            Number of relationships transferred
        """
        transferred = 0

        # Get existing relationships on canonical to avoid duplicates
        existing_outgoing = await self._get_relationship_keys(
            canonical.id, "outgoing", tenant_id
        )
        existing_incoming = await self._get_relationship_keys(
            canonical.id, "incoming", tenant_id
        )

        # Transfer outgoing relationships (merged -> target)
        outgoing_query = select(EntityRelationship).where(
            EntityRelationship.source_entity_id == merged.id,
            EntityRelationship.tenant_id == tenant_id,
        )
        result = await self.session.execute(outgoing_query)
        outgoing = result.scalars().all()

        for rel in outgoing:
            # Skip if this would create self-loop
            if rel.target_entity_id == canonical.id:
                continue

            # Create key for duplicate check
            key = (rel.target_entity_id, rel.relationship_type)
            if key not in existing_outgoing:
                # Update to point from canonical
                rel.source_entity_id = canonical.id
                existing_outgoing.add(key)
                transferred += 1
            # If duplicate, the relationship will be cleaned up when
            # merged entity becomes alias

        # Transfer incoming relationships (source -> merged)
        incoming_query = select(EntityRelationship).where(
            EntityRelationship.target_entity_id == merged.id,
            EntityRelationship.tenant_id == tenant_id,
        )
        result = await self.session.execute(incoming_query)
        incoming = result.scalars().all()

        for rel in incoming:
            # Skip if this would create self-loop
            if rel.source_entity_id == canonical.id:
                continue

            # Create key for duplicate check
            key = (rel.source_entity_id, rel.relationship_type)
            if key not in existing_incoming:
                # Update to point to canonical
                rel.target_entity_id = canonical.id
                existing_incoming.add(key)
                transferred += 1

        logger.debug(
            f"Transferred {transferred} relationships from {merged.id} to {canonical.id}"
        )

        return transferred

    async def _get_relationship_keys(
        self,
        entity_id: UUID,
        direction: str,
        tenant_id: UUID,
    ) -> set[tuple[UUID, str]]:
        """
        Get set of relationship keys for duplicate detection.

        Args:
            entity_id: Entity ID
            direction: "outgoing" or "incoming"
            tenant_id: Tenant ID

        Returns:
            Set of (other_entity_id, relationship_type) tuples
        """
        if direction == "outgoing":
            query = select(
                EntityRelationship.target_entity_id,
                EntityRelationship.relationship_type,
            ).where(
                EntityRelationship.source_entity_id == entity_id,
                EntityRelationship.tenant_id == tenant_id,
            )
        else:
            query = select(
                EntityRelationship.source_entity_id,
                EntityRelationship.relationship_type,
            ).where(
                EntityRelationship.target_entity_id == entity_id,
                EntityRelationship.tenant_id == tenant_id,
            )

        result = await self.session.execute(query)
        return {(row[0], row[1]) for row in result}

    async def undo_merge(
        self,
        merge_event_id: UUID,
        reason: str,
        user_id: UUID | None = None,
        restore_entity_ids: list[UUID] | None = None,
    ) -> UndoResult:
        """
        Undo a previous merge operation.

        This method:
        1. Validates the merge can be undone
        2. Loads alias records for merged entities
        3. Creates new entities with original properties
        4. Restores relationships from snapshot (if available)
        5. Marks original merge as undone
        6. Deletes alias records
        7. Emits MergeUndone event

        Args:
            merge_event_id: ID of the original EntitiesMerged event
            user_id: ID of user performing the undo
            reason: Explanation for why merge is being undone
            restore_entity_ids: Optional subset of entities to restore.
                              If None, restores all merged entities.

        Returns:
            UndoResult with details of the undo operation

        Raises:
            MergeUndoError: If merge cannot be undone
        """
        logger.info(
            f"Starting undo for merge event {merge_event_id} by user {user_id}"
        )

        # Generate IDs for tracking
        undo_event_id = uuid.uuid4()
        undo_history_id = uuid.uuid4()
        now = datetime.now(UTC)

        try:
            # Step 1: Load and validate original merge
            merge_history = await self._load_merge_history(merge_event_id)

            if merge_history is None:
                raise MergeUndoError(f"Merge event {merge_event_id} not found")

            if merge_history.undone:
                raise MergeUndoError(
                    f"Merge {merge_event_id} was already undone at {merge_history.undone_at}"
                )

            canonical_id = merge_history.canonical_entity_id
            tenant_id = merge_history.tenant_id

            # Get merged entity IDs (excluding canonical)
            merged_entity_ids = [
                eid for eid in merge_history.affected_entity_ids
                if eid != canonical_id
            ]

            # Filter to requested entities if partial undo
            if restore_entity_ids:
                merged_entity_ids = [
                    eid for eid in merged_entity_ids
                    if eid in restore_entity_ids
                ]

            if not merged_entity_ids:
                raise MergeUndoError("No entities to restore")

            # Step 2: Load alias records for merged entities
            aliases = await self._load_aliases_for_entities(
                merged_entity_ids, canonical_id, tenant_id
            )

            if not aliases:
                raise MergeUndoError(
                    f"No alias records found for entities to restore. "
                    f"Expected {len(merged_entity_ids)} aliases."
                )

            if len(aliases) != len(merged_entity_ids):
                logger.warning(
                    f"Expected {len(merged_entity_ids)} aliases, found {len(aliases)}. "
                    "Some entities may have been re-merged."
                )

            # Step 3: Create new entities from alias records
            restored_entities = []
            for alias in aliases:
                new_entity = await self._restore_entity_from_alias(alias, tenant_id)
                restored_entities.append(new_entity)

            restored_entity_ids = [e.id for e in restored_entities]

            # Step 4: Restore relationships from snapshot (if available)
            relationships_restored = 0
            relationship_snapshot = merge_history.details.get(
                "relationship_snapshot", {}
            ) if merge_history.details else {}

            if relationship_snapshot:
                relationships_restored = await self._restore_relationships_from_snapshot(
                    relationship_snapshot,
                    aliases,
                    restored_entities,
                    canonical_id,
                    tenant_id,
                )

            # Step 5: Mark original merge as undone
            merge_history.undone = True
            merge_history.undone_at = now
            merge_history.undone_by = user_id
            merge_history.undo_reason = reason

            # Step 6: Delete alias records
            aliases_removed = len(aliases)
            for alias in aliases:
                await self.session.delete(alias)

            # Step 7: Create undo history record
            undo_history = MergeHistory(
                id=undo_history_id,
                tenant_id=tenant_id,
                event_id=undo_event_id,
                event_type=MergeEventType.MERGE_UNDONE,
                canonical_entity_id=canonical_id,
                affected_entity_ids=restored_entity_ids,
                merge_reason=reason,
                similarity_scores={"original_merge_id": str(merge_event_id)},
                details={
                    "restored_from_aliases": [str(a.id) for a in aliases],
                    "relationships_restored": relationships_restored,
                },
                performed_by=user_id,
                performed_at=now,
            )
            self.session.add(undo_history)

            # Step 8: Emit MergeUndone event
            # Note: We need the original entity IDs from aliases
            original_entity_ids = [alias.original_entity_id for alias in aliases]

            undo_event = MergeUndone(
                aggregate_id=undo_event_id,
                tenant_id=tenant_id,
                original_merge_event_id=merge_event_id,
                canonical_entity_id=canonical_id,
                restored_entity_ids=restored_entity_ids,
                original_entity_ids=original_entity_ids,
                undo_reason=reason,
                undone_by_user_id=user_id,
            )
            self._pending_events.append(undo_event)

            # Publish events if event bus is configured
            if self.event_bus:
                for event in self._pending_events:
                    await self.event_bus.publish(event)
            self._pending_events.clear()

            logger.info(
                f"Undo completed for merge {merge_event_id}: "
                f"restored {len(restored_entity_ids)} entities, "
                f"removed {aliases_removed} aliases"
            )

            return UndoResult(
                original_merge_event_id=merge_event_id,
                canonical_entity_id=canonical_id,
                restored_entity_ids=restored_entity_ids,
                aliases_removed=aliases_removed,
                relationships_restored=relationships_restored,
                undo_history_id=undo_history_id,
                event_id=undo_event_id,
            )

        except MergeUndoError:
            raise
        except Exception as e:
            logger.error(f"Undo failed: {e}", exc_info=True)
            self._pending_events.clear()
            raise MergeUndoError(f"Undo operation failed: {e}") from e

    async def _load_merge_history(self, merge_event_id: UUID) -> MergeHistory | None:
        """Load merge history record by event ID."""
        query = select(MergeHistory).where(
            MergeHistory.event_id == merge_event_id
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def _load_aliases_for_entities(
        self,
        entity_ids: list[UUID],
        canonical_id: UUID,
        tenant_id: UUID,
    ) -> list[EntityAlias]:
        """Load alias records for original entities that were merged."""
        query = select(EntityAlias).where(
            EntityAlias.tenant_id == tenant_id,
            EntityAlias.canonical_entity_id == canonical_id,
            EntityAlias.original_entity_id.in_(entity_ids),
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def _restore_entity_from_alias(
        self,
        alias: EntityAlias,
        tenant_id: UUID,
    ) -> ExtractedEntity:
        """
        Restore an entity from its alias record.

        Creates a new entity with a new UUID but original properties.
        """
        # Map entity type string back to enum
        entity_type = EntityType.CONCEPT  # Default
        if alias.original_entity_type:
            try:
                entity_type = EntityType(alias.original_entity_type)
            except ValueError:
                logger.warning(
                    f"Unknown entity type '{alias.original_entity_type}' for alias {alias.id}, "
                    f"using CONCEPT"
                )

        new_entity = ExtractedEntity(
            id=uuid.uuid4(),  # New UUID
            tenant_id=tenant_id,
            source_page_id=alias.source_page_id,
            entity_type=entity_type,
            name=alias.alias_name,
            normalized_name=alias.original_normalized_name or alias.alias_normalized_name,
            description=alias.original_description,
            properties=alias.original_properties or {},
            external_ids=alias.original_external_ids or {},
            confidence_score=alias.original_confidence_score or 1.0,
            source_text=alias.original_source_text,
            extraction_method=ExtractionMethod.HYBRID,  # Mark as restored
            is_canonical=True,
            is_alias_of=None,
            synced_to_neo4j=False,  # Needs sync
        )

        self.session.add(new_entity)

        logger.debug(
            f"Restored entity {new_entity.id} from alias "
            f"(original: {alias.original_entity_id}, name: {alias.alias_name})"
        )

        return new_entity

    async def _restore_relationships_from_snapshot(
        self,
        relationship_snapshot: dict,
        aliases: list[EntityAlias],
        restored_entities: list[ExtractedEntity],
        canonical_id: UUID,
        tenant_id: UUID,
    ) -> int:
        """
        Restore relationships from the merge snapshot.

        Maps old entity IDs to new restored entity IDs.
        Returns the number of relationships restored.
        """
        if not relationship_snapshot:
            logger.debug("No relationship snapshot to restore")
            return 0

        # Build mapping: original entity ID -> new restored entity ID
        id_mapping: dict[str, UUID] = {}
        for alias, restored in zip(aliases, restored_entities, strict=False):
            id_mapping[str(alias.original_entity_id)] = restored.id

        relationships_restored = 0

        # Iterate through snapshot entries
        for original_id_str, rel_data_list in relationship_snapshot.items():
            if original_id_str not in id_mapping:
                continue

            new_entity_id = id_mapping[original_id_str]

            for rel_data in rel_data_list:
                try:
                    # Determine source and target
                    source_id_str = rel_data.get("source_entity_id")
                    target_id_str = rel_data.get("target_entity_id")

                    if not source_id_str or not target_id_str:
                        continue

                    # Map IDs to new entities or keep existing
                    if source_id_str == original_id_str:
                        source_id = new_entity_id
                    elif source_id_str in id_mapping:
                        source_id = id_mapping[source_id_str]
                    else:
                        source_id = UUID(source_id_str)

                    if target_id_str == original_id_str:
                        target_id = new_entity_id
                    elif target_id_str in id_mapping:
                        target_id = id_mapping[target_id_str]
                    else:
                        target_id = UUID(target_id_str)

                    # Skip self-referential relationships
                    if source_id == target_id:
                        continue

                    # Create new relationship
                    new_rel = EntityRelationship(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        source_entity_id=source_id,
                        target_entity_id=target_id,
                        relationship_type=rel_data.get("relationship_type", "RELATED_TO"),
                        properties=rel_data.get("properties", {}),
                        confidence_score=rel_data.get("confidence_score", 1.0),
                        synced_to_neo4j=False,
                    )

                    self.session.add(new_rel)
                    relationships_restored += 1

                except (ValueError, KeyError) as e:
                    logger.warning(
                        f"Failed to restore relationship from snapshot: {e}"
                    )
                    continue

        logger.debug(f"Restored {relationships_restored} relationships from snapshot")
        return relationships_restored

    async def split_entity(
        self,
        entity_id: UUID,
        split_definitions: list[dict[str, Any]],
        relationship_assignments: dict[UUID, int],
        alias_assignments: dict[UUID, int] | None,
        reason: str,
        user_id: UUID | None = None,
    ) -> SplitResult:
        """
        Split a single entity into multiple new entities.

        This method is used when an entity was incorrectly merged or
        contains multiple distinct real-world concepts that should be
        separate entities.

        Args:
            entity_id: ID of the entity to split
            split_definitions: List of dicts defining new entities:
                [{"name": "Entity A", "entity_type": "person", "properties": {...}}, ...]
            relationship_assignments: Mapping of relationship_id -> new_entity_index
                (index into split_definitions list)
            alias_assignments: Mapping of alias_id -> new_entity_index
                (None to remove all aliases)
            user_id: ID of user performing the split
            reason: Explanation for why entity is being split

        Returns:
            SplitResult with details of the split operation

        Raises:
            EntitySplitError: If split cannot be performed
        """
        logger.info(
            f"Starting split for entity {entity_id} by user {user_id} "
            f"into {len(split_definitions)} entities"
        )

        # Generate IDs for tracking
        split_event_id = uuid.uuid4()
        split_history_id = uuid.uuid4()
        now = datetime.now(UTC)

        try:
            # Step 1: Load and validate original entity
            original_entity = await self._load_entity(entity_id)

            if original_entity is None:
                raise EntitySplitError(f"Entity {entity_id} not found")

            tenant_id = original_entity.tenant_id

            # Validate split definitions
            if len(split_definitions) < 2:
                raise EntitySplitError(
                    "Split requires at least 2 new entity definitions"
                )

            for i, defn in enumerate(split_definitions):
                if "name" not in defn:
                    raise EntitySplitError(
                        f"Split definition {i} missing required 'name' field"
                    )

            # Step 2: Create new entities from definitions
            new_entities = []
            for defn in split_definitions:
                entity_type_str = defn.get("entity_type", original_entity.entity_type.value)
                try:
                    entity_type = EntityType(entity_type_str)
                except ValueError:
                    entity_type = original_entity.entity_type

                new_entity = ExtractedEntity(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    source_page_id=original_entity.source_page_id,
                    entity_type=entity_type,
                    name=defn["name"],
                    normalized_name=ExtractedEntity._normalize_name(defn["name"]),
                    description=defn.get("description"),
                    properties=defn.get("properties", {}),
                    external_ids=defn.get("external_ids", {}),
                    confidence_score=defn.get("confidence_score", original_entity.confidence_score),
                    source_text=defn.get("source_text", original_entity.source_text),
                    extraction_method=ExtractionMethod.HYBRID,  # Mark as derived
                    is_canonical=True,
                    is_alias_of=None,
                    synced_to_neo4j=False,
                )

                self.session.add(new_entity)
                new_entities.append(new_entity)

            new_entity_ids = [e.id for e in new_entities]
            new_entity_names = [e.name for e in new_entities]

            # Step 3: Redistribute relationships
            relationships_redistributed = await self._redistribute_relationships(
                entity_id,
                new_entities,
                relationship_assignments,
                tenant_id,
            )

            # Step 4: Redistribute aliases
            aliases_redistributed = 0
            if alias_assignments:
                aliases_redistributed = await self._redistribute_aliases(
                    entity_id,
                    new_entities,
                    alias_assignments,
                    tenant_id,
                )

            # Step 5: Mark original entity as split (soft-delete approach)
            original_entity.is_canonical = False
            # Store reference to split in properties
            if not original_entity.properties:
                original_entity.properties = {}
            original_entity.properties["_split_into"] = [str(eid) for eid in new_entity_ids]
            original_entity.properties["_split_at"] = now.isoformat()
            original_entity.properties["_split_by"] = str(user_id)

            # Step 6: Create split history record
            # Convert relationship_assignments keys to strings for JSONB
            relationship_assignments_json = {
                str(k): v for k, v in relationship_assignments.items()
            }
            property_assignments_json: dict[str, str] = {}
            for i, defn in enumerate(split_definitions):
                for prop_key in defn.get("properties", {}).keys():
                    property_assignments_json[prop_key] = str(new_entity_ids[i])

            split_history = MergeHistory(
                id=split_history_id,
                tenant_id=tenant_id,
                event_id=split_event_id,
                event_type=MergeEventType.ENTITY_SPLIT,
                canonical_entity_id=entity_id,  # Original entity
                affected_entity_ids=new_entity_ids,
                merge_reason=reason,
                similarity_scores={},
                details={
                    "split_definitions": split_definitions,
                    "relationship_assignments": relationship_assignments_json,
                    "alias_assignments": {str(k): v for k, v in (alias_assignments or {}).items()},
                    "original_entity_properties": original_entity.properties,
                },
                performed_by=user_id,
                performed_at=now,
            )
            self.session.add(split_history)

            # Step 7: Emit EntitySplit event
            split_event = EntitySplit(
                aggregate_id=split_event_id,
                tenant_id=tenant_id,
                original_entity_id=entity_id,
                new_entity_ids=new_entity_ids,
                new_entity_names=new_entity_names,
                property_assignments=property_assignments_json,
                relationship_assignments={
                    str(k): str(new_entity_ids[v])
                    for k, v in relationship_assignments.items()
                },
                split_reason=reason,
                split_by_user_id=user_id,
            )
            self._pending_events.append(split_event)

            # Publish events if event bus is configured
            if self.event_bus:
                for event in self._pending_events:
                    await self.event_bus.publish(event)
            self._pending_events.clear()

            logger.info(
                f"Split completed for entity {entity_id}: "
                f"created {len(new_entity_ids)} entities, "
                f"redistributed {relationships_redistributed} relationships"
            )

            return SplitResult(
                original_entity_id=entity_id,
                new_entity_ids=new_entity_ids,
                new_entities=new_entities,
                relationships_redistributed=relationships_redistributed,
                aliases_redistributed=aliases_redistributed,
                split_history_id=split_history_id,
                event_id=split_event_id,
            )

        except EntitySplitError:
            raise
        except Exception as e:
            logger.error(f"Split failed: {e}", exc_info=True)
            self._pending_events.clear()
            raise EntitySplitError(f"Split operation failed: {e}") from e

    async def _load_entity(self, entity_id: UUID) -> ExtractedEntity | None:
        """Load entity by ID."""
        query = select(ExtractedEntity).where(
            ExtractedEntity.id == entity_id
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def _redistribute_relationships(
        self,
        original_entity_id: UUID,
        new_entities: list[ExtractedEntity],
        relationship_assignments: dict[UUID, int],
        tenant_id: UUID,
    ) -> int:
        """
        Redistribute relationships from original entity to new entities.

        For each relationship involving the original entity:
        - If assigned in relationship_assignments, move to that new entity
        - If not assigned, default to first new entity

        Returns the count of relationships redistributed.
        """
        if not relationship_assignments:
            # Default: assign all relationships to first new entity
            relationships_redistributed = 0

            # Get all outgoing relationships
            outgoing_query = select(EntityRelationship).where(
                EntityRelationship.source_entity_id == original_entity_id,
                EntityRelationship.tenant_id == tenant_id,
            )
            outgoing_result = await self.session.execute(outgoing_query)
            outgoing_rels = outgoing_result.scalars().all()

            for rel in outgoing_rels:
                rel.source_entity_id = new_entities[0].id
                relationships_redistributed += 1

            # Get all incoming relationships
            incoming_query = select(EntityRelationship).where(
                EntityRelationship.target_entity_id == original_entity_id,
                EntityRelationship.tenant_id == tenant_id,
            )
            incoming_result = await self.session.execute(incoming_query)
            incoming_rels = incoming_result.scalars().all()

            for rel in incoming_rels:
                rel.target_entity_id = new_entities[0].id
                relationships_redistributed += 1

            return relationships_redistributed

        # Handle explicit assignments
        relationships_redistributed = 0

        # Process all relationships
        all_rels_query = select(EntityRelationship).where(
            (
                (EntityRelationship.source_entity_id == original_entity_id)
                | (EntityRelationship.target_entity_id == original_entity_id)
            ),
            EntityRelationship.tenant_id == tenant_id,
        )
        all_rels_result = await self.session.execute(all_rels_query)
        all_rels = all_rels_result.scalars().all()

        for rel in all_rels:
            # Determine which new entity this relationship goes to
            new_entity_index = relationship_assignments.get(rel.id, 0)

            if new_entity_index < 0 or new_entity_index >= len(new_entities):
                new_entity_index = 0

            new_entity_id = new_entities[new_entity_index].id

            if rel.source_entity_id == original_entity_id:
                rel.source_entity_id = new_entity_id
            if rel.target_entity_id == original_entity_id:
                rel.target_entity_id = new_entity_id

            rel.synced_to_neo4j = False  # Mark for re-sync
            relationships_redistributed += 1

        return relationships_redistributed

    async def _redistribute_aliases(
        self,
        original_entity_id: UUID,
        new_entities: list[ExtractedEntity],
        alias_assignments: dict[UUID, int],
        tenant_id: UUID,
    ) -> int:
        """
        Redistribute aliases from original entity to new entities.

        Returns the count of aliases redistributed.
        """
        # Load all aliases pointing to the original entity
        aliases_query = select(EntityAlias).where(
            EntityAlias.canonical_entity_id == original_entity_id,
            EntityAlias.tenant_id == tenant_id,
        )
        aliases_result = await self.session.execute(aliases_query)
        aliases = aliases_result.scalars().all()

        aliases_redistributed = 0

        for alias in aliases:
            # Determine which new entity this alias goes to
            new_entity_index = alias_assignments.get(alias.id, 0)

            if new_entity_index < 0 or new_entity_index >= len(new_entities):
                new_entity_index = 0

            alias.canonical_entity_id = new_entities[new_entity_index].id
            aliases_redistributed += 1

        return aliases_redistributed


def merge_property(
    canonical_value: Any,
    merged_value: Any,
    strategy: PropertyMergeStrategy,
) -> tuple[Any, dict[str, Any]]:
    """
    Merge a single property value using the specified strategy.

    Args:
        canonical_value: Value from canonical entity
        merged_value: Value from merged entity
        strategy: Strategy to use for merging

    Returns:
        Tuple of (merged_value, merge_details_dict)
    """
    details: dict[str, Any] = {"strategy": strategy.value}

    if strategy == PropertyMergeStrategy.PREFER_CANONICAL:
        # Keep canonical value, but note what was discarded
        details["kept"] = "canonical"
        details["discarded"] = merged_value if merged_value != canonical_value else None
        return canonical_value, details

    elif strategy == PropertyMergeStrategy.PREFER_MERGED:
        # Take merged value
        details["kept"] = "merged"
        details["discarded"] = (
            canonical_value if canonical_value != merged_value else None
        )
        return merged_value, details

    elif strategy == PropertyMergeStrategy.UNION:
        # Union for lists/sets
        if isinstance(canonical_value, list) and isinstance(merged_value, list):
            result = list(set(canonical_value) | set(merged_value))
            details["union_count"] = len(result)
            details["added"] = len(set(merged_value) - set(canonical_value))
            return result, details
        elif isinstance(canonical_value, dict) and isinstance(merged_value, dict):
            # For dicts, union the keys (prefer canonical on conflicts)
            result = {**merged_value, **canonical_value}
            details["union_keys"] = list(result.keys())
            return result, details
        else:
            # Fallback to canonical for non-collection types
            return canonical_value, details

    elif strategy == PropertyMergeStrategy.DEEP_MERGE:
        # Deep merge for nested dicts
        if isinstance(canonical_value, dict) and isinstance(merged_value, dict):
            result = deep_merge_dicts(canonical_value, merged_value)
            details["merged_keys"] = list(
                set(canonical_value.keys()) | set(merged_value.keys())
            )
            return result, details
        elif isinstance(canonical_value, list) and isinstance(merged_value, list):
            # For lists in deep merge, concatenate and dedupe
            result = list(dict.fromkeys(canonical_value + merged_value))
            details["merged_count"] = len(result)
            return result, details
        else:
            # Fallback to canonical
            return canonical_value if canonical_value else merged_value, details

    elif strategy == PropertyMergeStrategy.LATEST:
        # Take most recent - requires timestamp comparison
        # Without timestamps, fall back to merged (assuming newer)
        details["kept"] = "merged"
        return merged_value if merged_value else canonical_value, details

    return canonical_value, details


def deep_merge_dicts(
    dict_a: dict[str, Any],
    dict_b: dict[str, Any],
) -> dict[str, Any]:
    """
    Deep merge two dictionaries.

    Values from dict_a take precedence for non-dict values.
    For dict values, recursively merge.

    Args:
        dict_a: First dictionary (takes precedence)
        dict_b: Second dictionary

    Returns:
        Merged dictionary
    """
    result = dict_b.copy()

    for key, value_a in dict_a.items():
        if key in result:
            value_b = result[key]
            if isinstance(value_a, dict) and isinstance(value_b, dict):
                result[key] = deep_merge_dicts(value_a, value_b)
            elif isinstance(value_a, list) and isinstance(value_b, list):
                # Concatenate and dedupe lists
                result[key] = list(dict.fromkeys(value_a + value_b))
            else:
                # dict_a takes precedence
                result[key] = value_a
        else:
            result[key] = value_a

    return result
