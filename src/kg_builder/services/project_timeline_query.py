"""
Project Timeline Query Service for aggregating temporal data across all jobs in a project.

This service extends the timeline query patterns to work at the project level,
aggregating events from all scraping jobs within a project.

See ADR-025 for design decisions regarding the Timeline and Chronology
Extraction feature.
"""

import logging
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from kg_builder.models.extracted_entity import EntityRelationship, ExtractedEntity
from kg_builder.models.scraped_page import ScrapedPage
from kg_builder.models.scraping_job import ScrapingJob
from kg_builder.schemas.timeline import (
    DatePrecision,
    TemporalRelationship,
    TimelineEvent,
    TimelineFilters,
    TimelineResponse,
    TimelineSummaryResponse,
    TimeRange,
    UncertaintyMarker,
)
from kg_builder.services.temporal_relationship_inference import (
    TemporalEvent,
    TemporalRelationshipInferenceService,
    get_temporal_relationship_inference_service,
)

logger = logging.getLogger(__name__)


class ProjectTimelineQueryService:
    """
    Service for querying timeline events aggregated across all jobs in a project.

    This service provides methods to:
    - Retrieve timeline events from ALL jobs within a project
    - Include source attribution (which job contributed each event)
    - Retrieve temporal relationships between events
    - Generate project-level timeline summary statistics
    - Verify project access and tenant isolation
    """

    # Temporal relationship types for filtering
    TEMPORAL_RELATIONSHIP_TYPES = {
        "precedes",
        "follows",
        "during",
        "overlaps",
        "causes",
        "concurrent",
    }

    def __init__(
        self,
        db: AsyncSession,
        inference_service: TemporalRelationshipInferenceService | None = None,
    ):
        """
        Initialize the project timeline query service.

        Args:
            db: Async database session with tenant context set
            inference_service: Optional inference service for relationship inference
        """
        self.db = db
        self._inference_service = inference_service or get_temporal_relationship_inference_service()

    async def verify_project_access(
        self,
        project_id: UUID,
        tenant_id: UUID,
    ) -> bool:
        """
        Verify that a project exists and belongs to the given tenant.

        Args:
            project_id: UUID of the project
            tenant_id: UUID of the tenant

        Returns:
            True if project exists and accessible, False otherwise
        """
        result = await self.db.execute(
            text("""
                SELECT id FROM projects
                WHERE id = :project_id AND tenant_id = :tenant_id
            """),
            {"project_id": project_id, "tenant_id": tenant_id},
        )
        return result.scalar_one_or_none() is not None

    async def get_project_timeline_events(
        self,
        project_id: UUID,
        filters: TimelineFilters,
        tenant_id: UUID,
        limit: int = 100,
        offset: int = 0,
        *,
        include_relationships: bool = True,
        include_inferred_relationships: bool = True,
    ) -> TimelineResponse:
        """
        Query entities with temporal data from ALL jobs in a project.

        Args:
            project_id: UUID of the project to query
            filters: TimelineFilters with date range, entity types, precision, etc.
            tenant_id: UUID of the tenant (for authorization)
            limit: Maximum number of events to return (max 1000)
            offset: Number of events to skip for pagination
            include_relationships: Whether to include temporal relationships
            include_inferred_relationships: Whether to include inferred relationships

        Returns:
            TimelineResponse containing events, relationships, counts, and pagination info

        Raises:
            ValueError: If project not found or unauthorized
        """
        # Enforce maximum limit
        limit = min(limit, 1000)

        logger.info(
            "Querying project timeline events",
            extra={
                "project_id": str(project_id),
                "tenant_id": str(tenant_id),
                "filters": {
                    "start_date": str(filters.start_date) if filters.start_date else None,
                    "end_date": str(filters.end_date) if filters.end_date else None,
                    "entity_types": filters.entity_types,
                    "include_undated": filters.include_undated,
                    "sort_by": filters.sort_by,
                },
                "limit": limit,
                "offset": offset,
            },
        )

        # Base query: get entities from pages belonging to jobs in this project
        # Only include canonical entities (not merged aliases)
        query = (
            select(
                ExtractedEntity, ScrapingJob.id.label("job_id"), ScrapingJob.name.label("job_name")
            )
            .join(ScrapedPage, ExtractedEntity.source_page_id == ScrapedPage.id)
            .join(ScrapingJob, ScrapedPage.job_id == ScrapingJob.id)
            .where(
                ScrapingJob.project_id == project_id,
                ExtractedEntity.tenant_id == tenant_id,
                ExtractedEntity.is_canonical == True,  # noqa: E712
            )
            .options(joinedload(ExtractedEntity.source_page))
        )

        # Filter for temporal entities
        if filters.include_undated:
            # Include entities with either start_date or sequence_position
            query = query.where(
                (ExtractedEntity.start_date.is_not(None))
                | (ExtractedEntity.sequence_position.is_not(None))
            )
        else:
            # Only include entities with start_date (excludes sequence-only)
            query = query.where(ExtractedEntity.start_date.is_not(None))

        # Apply date range filters
        if filters.start_date:
            query = query.where(
                (ExtractedEntity.start_date >= filters.start_date)
                | (ExtractedEntity.end_date >= filters.start_date)
                | (ExtractedEntity.start_date.is_(None))  # Include undated if allowed
            )
        if filters.end_date:
            query = query.where(
                (ExtractedEntity.start_date <= filters.end_date)
                | (ExtractedEntity.start_date.is_(None))  # Include undated if allowed
            )

        # Filter by entity types
        if filters.entity_types:
            normalized_types = [t.lower() for t in filters.entity_types]
            query = query.where(ExtractedEntity.entity_type.in_(normalized_types))

        # Apply search filter
        if filters.search:
            search_pattern = f"%{filters.search}%"
            query = query.where(
                (ExtractedEntity.name.ilike(search_pattern))
                | (ExtractedEntity.description.ilike(search_pattern))
            )

        # Count total matching events (before pagination)
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await self.db.execute(count_query)
        total_count = total_result.scalar() or 0

        # Apply sorting
        if filters.sort_by == "sequence":
            # Sort by sequence_position first, then start_date
            query = query.order_by(
                ExtractedEntity.sequence_position.asc().nullslast(),
                ExtractedEntity.start_date.asc().nullslast(),
            )
        else:
            # Default: sort by start_date, then sequence_position
            query = query.order_by(
                ExtractedEntity.start_date.asc().nullslast(),
                ExtractedEntity.sequence_position.asc().nullslast(),
            )

        # Apply pagination
        query = query.offset(offset).limit(limit)

        # Execute query
        result = await self.db.execute(query)
        rows = result.unique().all()

        # Convert to TimelineEvent objects with source attribution
        events = []
        dated_events = []
        undated_count = 0
        sequence_only_count = 0

        for row in rows:
            entity = row[0]  # ExtractedEntity
            source_job_id = row[1]  # job_id
            source_job_name = row[2]  # job_name

            event = self._entity_to_timeline_event(
                entity,
                source_job_id=source_job_id,
                source_job_name=source_job_name,
            )
            events.append(event)

            if entity.start_date:
                dated_events.append(entity)
            else:
                undated_count += 1
                if entity.sequence_position is not None:
                    sequence_only_count += 1

        # Calculate time range from dated events
        time_range = None
        if dated_events:
            min_date = min(e.start_date for e in dated_events if e.start_date)
            max_date = max((e.end_date or e.start_date) for e in dated_events if e.start_date)
            time_range = TimeRange(start=min_date, end=max_date)

        has_more = (offset + len(events)) < total_count

        # Fetch temporal relationships if requested
        relationships: list[TemporalRelationship] = []
        if include_relationships and events:
            event_ids = [event.id for event in events]
            relationships = await self.get_temporal_relationships(
                project_id=project_id,
                tenant_id=tenant_id,
                event_ids=event_ids,
                include_inferred=include_inferred_relationships,
            )

        logger.info(
            "Project timeline query completed",
            extra={
                "project_id": str(project_id),
                "total_count": total_count,
                "returned_count": len(events),
                "dated_count": len(dated_events),
                "undated_count": undated_count,
                "has_more": has_more,
                "relationship_count": len(relationships),
            },
        )

        return TimelineResponse(
            events=events,
            relationships=relationships,
            total_count=total_count,
            time_range=time_range,
            has_more=has_more,
            undated_count=undated_count,
            sequence_only_count=sequence_only_count,
            relationship_count=len(relationships),
        )

    async def get_project_timeline_summary(
        self,
        project_id: UUID,
        tenant_id: UUID,
    ) -> TimelineSummaryResponse:
        """
        Get summary statistics for a project's timeline.

        Args:
            project_id: UUID of the project
            tenant_id: UUID of the tenant

        Returns:
            TimelineSummaryResponse with counts, date ranges, and distributions

        Raises:
            ValueError: If project not found or unauthorized
        """
        logger.info(
            "Generating project timeline summary",
            extra={
                "project_id": str(project_id),
                "tenant_id": str(tenant_id),
            },
        )

        # Base query: get temporal entities from pages belonging to jobs in this project
        base_query = (
            select(ExtractedEntity)
            .join(ScrapedPage, ExtractedEntity.source_page_id == ScrapedPage.id)
            .join(ScrapingJob, ScrapedPage.job_id == ScrapingJob.id)
            .where(
                ScrapingJob.project_id == project_id,
                ExtractedEntity.tenant_id == tenant_id,
                ExtractedEntity.is_canonical == True,  # noqa: E712
                (ExtractedEntity.start_date.is_not(None))
                | (ExtractedEntity.sequence_position.is_not(None)),
            )
        )

        result = await self.db.execute(base_query)
        entities = result.scalars().all()

        # Calculate statistics
        total_events = len(entities)
        dated_events = 0
        undated_events = 0
        sequence_only_events = 0
        entity_type_counts: dict[str, int] = {}
        precision_distribution: dict[str, int] = {}
        uncertainty_distribution: dict[str, int] = {}
        dated_entities: list[ExtractedEntity] = []

        for entity in entities:
            # Count by dated/undated
            if entity.start_date:
                dated_events += 1
                dated_entities.append(entity)
            else:
                undated_events += 1
                if entity.sequence_position is not None:
                    sequence_only_events += 1

            # Count by entity type
            entity_type = entity.entity_type.lower()
            entity_type_counts[entity_type] = entity_type_counts.get(entity_type, 0) + 1

            # Count by precision
            if entity.date_precision:
                precision = entity.date_precision.lower()
                precision_distribution[precision] = precision_distribution.get(precision, 0) + 1

            # Count by uncertainty
            if entity.uncertainty_marker:
                uncertainty = entity.uncertainty_marker.lower()
                uncertainty_distribution[uncertainty] = (
                    uncertainty_distribution.get(uncertainty, 0) + 1
                )

        # Calculate overall time range
        time_range = None
        if dated_entities:
            min_date = min(e.start_date for e in dated_entities if e.start_date)
            max_date = max((e.end_date or e.start_date) for e in dated_entities if e.start_date)
            time_range = TimeRange(start=min_date, end=max_date)

        logger.info(
            "Project timeline summary generated",
            extra={
                "project_id": str(project_id),
                "total_events": total_events,
                "dated_events": dated_events,
                "undated_events": undated_events,
                "entity_types": list(entity_type_counts.keys()),
            },
        )

        return TimelineSummaryResponse(
            total_events=total_events,
            dated_events=dated_events,
            undated_events=undated_events,
            sequence_only_events=sequence_only_events,
            time_range=time_range,
            entity_type_counts=entity_type_counts,
            precision_distribution=precision_distribution,
            uncertainty_distribution=uncertainty_distribution,
        )

    async def get_temporal_relationships(
        self,
        project_id: UUID,
        tenant_id: UUID,
        event_ids: list[UUID],
        *,
        include_inferred: bool = True,
    ) -> list[TemporalRelationship]:
        """
        Get temporal relationships between timeline events.

        Fetches both explicitly extracted temporal relationships and
        optionally inferred relationships based on event dates.

        Args:
            project_id: UUID of the project
            tenant_id: UUID of the tenant
            event_ids: List of event/entity IDs to get relationships for
            include_inferred: Whether to include date-inferred relationships

        Returns:
            List of TemporalRelationship objects
        """
        if not event_ids:
            return []

        logger.info(
            "Fetching temporal relationships for project",
            extra={
                "project_id": str(project_id),
                "tenant_id": str(tenant_id),
                "event_count": len(event_ids),
                "include_inferred": include_inferred,
            },
        )

        # Fetch explicitly extracted temporal relationships
        explicit_relationships = await self._fetch_explicit_temporal_relationships(
            tenant_id=tenant_id,
            event_ids=event_ids,
        )

        # Track existing relationship pairs to avoid duplicates
        existing_pairs: set[tuple[UUID, UUID]] = set()
        for rel in explicit_relationships:
            existing_pairs.add((rel.source_event_id, rel.target_event_id))

        # Optionally infer relationships from dates
        inferred_relationships: list[TemporalRelationship] = []
        if include_inferred:
            inferred_relationships = await self._infer_temporal_relationships(
                tenant_id=tenant_id,
                event_ids=event_ids,
                existing_pairs=existing_pairs,
            )

        all_relationships = explicit_relationships + inferred_relationships

        logger.info(
            "Temporal relationships fetched for project",
            extra={
                "project_id": str(project_id),
                "explicit_count": len(explicit_relationships),
                "inferred_count": len(inferred_relationships),
                "total_count": len(all_relationships),
            },
        )

        return all_relationships

    async def _fetch_explicit_temporal_relationships(
        self,
        tenant_id: UUID,
        event_ids: list[UUID],
    ) -> list[TemporalRelationship]:
        """
        Fetch explicitly extracted temporal relationships from the database.

        Args:
            tenant_id: UUID of the tenant
            event_ids: List of event IDs to get relationships for

        Returns:
            List of TemporalRelationship objects for explicitly extracted relationships
        """
        # Query relationships where both source and target are in our event list
        # and the relationship type is a temporal type
        query = (
            select(EntityRelationship)
            .where(
                EntityRelationship.tenant_id == tenant_id,
                EntityRelationship.source_entity_id.in_(event_ids),
                EntityRelationship.target_entity_id.in_(event_ids),
                EntityRelationship.relationship_type.in_(self.TEMPORAL_RELATIONSHIP_TYPES),
            )
            .options(
                joinedload(EntityRelationship.source_entity),
                joinedload(EntityRelationship.target_entity),
            )
        )

        result = await self.db.execute(query)
        db_relationships = result.unique().scalars().all()

        # Convert to TemporalRelationship schema
        relationships = []
        for db_rel in db_relationships:
            # Get context/evidence from properties if available
            evidence = db_rel.properties.get("context") if db_rel.properties else None

            relationships.append(
                TemporalRelationship(
                    id=db_rel.id,
                    source_event_id=db_rel.source_entity_id,
                    target_event_id=db_rel.target_entity_id,
                    source_event_name=(
                        db_rel.source_entity.name if db_rel.source_entity else "Unknown"
                    ),
                    target_event_name=(
                        db_rel.target_entity.name if db_rel.target_entity else "Unknown"
                    ),
                    relationship_type=db_rel.relationship_type,  # type: ignore
                    confidence=db_rel.confidence_score,
                    evidence=evidence,
                    is_inferred=False,
                )
            )

        return relationships

    async def _infer_temporal_relationships(
        self,
        tenant_id: UUID,
        event_ids: list[UUID],
        existing_pairs: set[tuple[UUID, UUID]],
    ) -> list[TemporalRelationship]:
        """
        Infer temporal relationships from event dates.

        Args:
            tenant_id: UUID of the tenant
            event_ids: List of event IDs to infer relationships for
            existing_pairs: Set of (source_id, target_id) pairs that already have relationships

        Returns:
            List of TemporalRelationship objects for inferred relationships
        """
        # Fetch events with temporal data
        query = select(ExtractedEntity).where(
            ExtractedEntity.tenant_id == tenant_id,
            ExtractedEntity.id.in_(event_ids),
            ExtractedEntity.start_date.is_not(None),
        )

        result = await self.db.execute(query)
        entities = result.scalars().all()

        if len(entities) < 2:
            return []

        # Convert to TemporalEvent for inference
        temporal_events = [
            TemporalEvent(
                id=e.id,
                name=e.name,
                start_date=e.start_date,
                end_date=e.end_date,
            )
            for e in entities
        ]

        # Run inference
        inferred = self._inference_service.infer_relationships(
            events=temporal_events,
            existing_relationship_pairs=existing_pairs,
        )

        # Convert to TemporalRelationship schema
        return [
            TemporalRelationship(
                id=r.id,
                source_event_id=r.source_event_id,
                target_event_id=r.target_event_id,
                source_event_name=r.source_event_name,
                target_event_name=r.target_event_name,
                relationship_type=r.relationship_type,  # type: ignore
                confidence=r.confidence,
                evidence=r.evidence,
                is_inferred=True,
            )
            for r in inferred
        ]

    def _entity_to_timeline_event(
        self,
        entity: ExtractedEntity,
        source_job_id: UUID | None = None,
        source_job_name: str | None = None,
    ) -> TimelineEvent:
        """
        Convert an ExtractedEntity to a TimelineEvent with source attribution.

        Args:
            entity: ExtractedEntity with temporal data
            source_job_id: UUID of the job that extracted this entity
            source_job_name: Name of the source job

        Returns:
            TimelineEvent representing the entity with source info
        """
        # Parse precision enum if present
        precision = None
        if entity.date_precision:
            try:
                precision = DatePrecision(entity.date_precision.lower())
            except ValueError:
                logger.warning(
                    "Invalid date precision value",
                    extra={
                        "entity_id": str(entity.id),
                        "precision": entity.date_precision,
                    },
                )

        # Parse uncertainty enum if present
        uncertainty = None
        if entity.uncertainty_marker:
            try:
                uncertainty = UncertaintyMarker(entity.uncertainty_marker.lower())
            except ValueError:
                logger.warning(
                    "Invalid uncertainty marker value",
                    extra={
                        "entity_id": str(entity.id),
                        "uncertainty": entity.uncertainty_marker,
                    },
                )

        # Get source page info
        source_page = entity.source_page
        source_url = source_page.url if source_page else ""

        return TimelineEvent(
            id=entity.id,
            name=entity.name,
            description=entity.description,
            entity_type=entity.entity_type,
            start_date=entity.start_date,
            end_date=entity.end_date,
            precision=precision,
            uncertainty=uncertainty,
            original_text=entity.original_temporal_text,
            sequence_position=entity.sequence_position,
            source_page_id=entity.source_page_id,
            source_url=source_url,
            involved_entities=[],
            source_job_id=source_job_id,
            source_job_name=source_job_name,
        )


def get_project_timeline_query_service(db: AsyncSession) -> ProjectTimelineQueryService:
    """
    Factory function to create a ProjectTimelineQueryService instance.

    Args:
        db: Async database session with tenant context set

    Returns:
        ProjectTimelineQueryService instance
    """
    return ProjectTimelineQueryService(db)
