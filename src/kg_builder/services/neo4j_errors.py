"""
Neo4j error handling and recovery.

This module provides comprehensive error handling for Neo4j operations,
including error classification, retry logic support, and failure event creation.

Error Categories:
- Transient errors: Can be retried (service unavailable, session expired)
- Data errors: Indicate data issues (constraint violations, query errors)

Example:
    from kg_builder.services.neo4j_errors import Neo4jErrorHandler, Neo4jTransientError

    async def sync_entity(entity):
        try:
            result = await Neo4jErrorHandler.with_error_handling(
                lambda: neo4j_service.create_entity_node(...)
            )
            return result
        except Neo4jTransientError:
            # Schedule retry
            pass
        except Neo4jDataError:
            # Log and skip
            pass
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from neo4j.exceptions import (
    DatabaseError,
    ServiceUnavailable,
    SessionExpired,
    TransientError,
)

from kg_builder.events.scraping import Neo4jSyncFailed

logger = logging.getLogger(__name__)

T = TypeVar("T")


# =============================================================================
# Custom Exception Classes
# =============================================================================


class Neo4jSyncError(Exception):
    """Base exception for Neo4j sync errors.

    All Neo4j-related sync errors should inherit from this class
    to enable consistent error handling across the application.

    Attributes:
        message: Human-readable error description
        original_error: The original exception that caused this error
        entity_id: Optional ID of the entity being synced
        relationship_id: Optional ID of the relationship being synced
    """

    def __init__(
        self,
        message: str,
        original_error: Exception | None = None,
        entity_id: UUID | None = None,
        relationship_id: UUID | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.original_error = original_error
        self.entity_id = entity_id
        self.relationship_id = relationship_id

    def __str__(self) -> str:
        parts = [self.message]
        if self.entity_id:
            parts.append(f"entity_id={self.entity_id}")
        if self.relationship_id:
            parts.append(f"relationship_id={self.relationship_id}")
        if self.original_error:
            parts.append(f"original_error={type(self.original_error).__name__}")
        return " | ".join(parts)


class Neo4jTransientError(Neo4jSyncError):
    """Exception for transient Neo4j errors that can be retried.

    These errors indicate temporary conditions such as:
    - Service unavailable (Neo4j is down or unreachable)
    - Session expired (connection timed out)
    - Transient database errors (temporary resource constraints)

    Retry logic should be applied when catching this exception.
    """

    pass


class Neo4jDataError(Neo4jSyncError):
    """Exception for Neo4j data errors that should not be retried.

    These errors indicate issues with the data being synced:
    - Constraint violations (duplicate keys, missing required fields)
    - Invalid query syntax
    - Schema mismatches

    These errors typically require data correction rather than retry.
    """

    pass


# =============================================================================
# Error Handler Class
# =============================================================================


class Neo4jErrorHandler:
    """Handles Neo4j errors and recovery.

    This class provides static methods for:
    - Classifying errors as retryable or not
    - Creating failure events for event sourcing
    - Wrapping operations with consistent error handling

    The error classification is based on Neo4j's official exception hierarchy:
    - Transient errors: ServiceUnavailable, SessionExpired, TransientError
    - Data errors: DatabaseError (includes constraint violations)
    """

    # Errors that should trigger retry
    TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
        ServiceUnavailable,
        SessionExpired,
        TransientError,
    )

    # Errors that indicate data issues (should not retry)
    DATA_ERRORS: tuple[type[Exception], ...] = (DatabaseError,)

    @classmethod
    def is_retryable(cls, error: Exception) -> bool:
        """Check if error is retryable.

        Transient errors indicate temporary conditions that may resolve
        on their own, so retrying the operation may succeed.

        Args:
            error: The exception to classify

        Returns:
            True if the error is transient and should be retried,
            False otherwise

        Example:
            try:
                await sync_to_neo4j(entity)
            except Exception as e:
                if Neo4jErrorHandler.is_retryable(e):
                    await schedule_retry(entity)
                else:
                    await mark_as_failed(entity)
        """
        return isinstance(error, cls.TRANSIENT_ERRORS)

    @classmethod
    def is_data_error(cls, error: Exception) -> bool:
        """Check if error is a data error.

        Data errors indicate issues with the data itself, such as
        constraint violations or invalid data formats.

        Args:
            error: The exception to classify

        Returns:
            True if the error is a data error, False otherwise
        """
        return isinstance(error, cls.DATA_ERRORS)

    @classmethod
    def classify_error(cls, error: Exception) -> str:
        """Classify an error into a category.

        Args:
            error: The exception to classify

        Returns:
            String classification: "transient", "data", or "unknown"
        """
        if cls.is_retryable(error):
            return "transient"
        elif cls.is_data_error(error):
            return "data"
        else:
            return "unknown"

    @classmethod
    def handle_sync_error(
        cls,
        error: Exception,
        entity_id: UUID | None = None,
        relationship_id: UUID | None = None,
        tenant_id: UUID | None = None,
    ) -> Neo4jSyncFailed:
        """Create sync failure event.

        Creates a Neo4jSyncFailed event for tracking failed sync operations
        in the event store. This enables:
        - Audit trail of sync failures
        - Retry scheduling based on failure events
        - Monitoring and alerting on sync failure patterns

        Args:
            error: The exception that caused the failure
            entity_id: ID of the entity that failed to sync (if applicable)
            relationship_id: ID of the relationship that failed (if applicable)
            tenant_id: Tenant ID for the operation

        Returns:
            Neo4jSyncFailed event ready to be published

        Example:
            try:
                await sync_entity(entity)
            except Exception as e:
                event = Neo4jErrorHandler.handle_sync_error(
                    error=e,
                    entity_id=entity.id,
                    tenant_id=entity.tenant_id,
                )
                await event_store.publish(event)
        """
        # Determine aggregate_id - prefer entity_id, then relationship_id
        aggregate_id = entity_id or relationship_id or UUID(int=0)

        # Use provided tenant_id or default to zero UUID
        effective_tenant_id = tenant_id or UUID(int=0)

        # Log the failure for immediate visibility
        error_type = cls.classify_error(error)
        logger.warning(
            f"Neo4j sync failed: {error}",
            extra={
                "entity_id": str(entity_id) if entity_id else None,
                "relationship_id": str(relationship_id) if relationship_id else None,
                "tenant_id": str(effective_tenant_id),
                "error_type": error_type,
                "error_class": type(error).__name__,
            },
        )

        return Neo4jSyncFailed(
            aggregate_id=aggregate_id,
            tenant_id=effective_tenant_id,
            entity_id=entity_id,
            relationship_id=relationship_id,
            error_message=str(error),
            failed_at=datetime.now(UTC),
        )

    @classmethod
    async def with_error_handling(
        cls,
        operation: Callable[[], Awaitable[T]],
        entity_id: UUID | None = None,
        relationship_id: UUID | None = None,
    ) -> T:
        """Execute operation with error handling.

        Wraps an async operation with consistent error handling:
        - Transient errors are logged at WARNING level and re-raised as Neo4jTransientError
        - Data errors are logged at ERROR level and re-raised as Neo4jDataError
        - Unknown errors are logged at ERROR level and re-raised as Neo4jSyncError

        This wrapper enables consistent error classification and logging
        across all Neo4j operations.

        Args:
            operation: Async callable to execute
            entity_id: Optional entity ID for error context
            relationship_id: Optional relationship ID for error context

        Returns:
            The result of the operation if successful

        Raises:
            Neo4jTransientError: For retryable transient errors
            Neo4jDataError: For data-related errors
            Neo4jSyncError: For other unexpected errors

        Example:
            async def create_node():
                async with neo4j.session() as session:
                    return await session.run(query, params)

            try:
                result = await Neo4jErrorHandler.with_error_handling(
                    create_node,
                    entity_id=entity.id,
                )
            except Neo4jTransientError as e:
                # Schedule retry
                await retry_queue.add(entity.id)
            except Neo4jDataError as e:
                # Log and continue - data needs to be fixed
                logger.error(f"Data error for entity {entity.id}: {e}")
        """
        try:
            return await operation()
        except cls.TRANSIENT_ERRORS as e:
            logger.warning(
                f"Transient Neo4j error: {e}",
                extra={
                    "error_type": "transient",
                    "error_class": type(e).__name__,
                    "entity_id": str(entity_id) if entity_id else None,
                    "relationship_id": str(relationship_id) if relationship_id else None,
                },
            )
            raise Neo4jTransientError(
                message=f"Transient Neo4j error: {e}",
                original_error=e,
                entity_id=entity_id,
                relationship_id=relationship_id,
            ) from e
        except cls.DATA_ERRORS as e:
            logger.error(
                f"Neo4j data error: {e}",
                extra={
                    "error_type": "data",
                    "error_class": type(e).__name__,
                    "entity_id": str(entity_id) if entity_id else None,
                    "relationship_id": str(relationship_id) if relationship_id else None,
                },
            )
            raise Neo4jDataError(
                message=f"Neo4j data error: {e}",
                original_error=e,
                entity_id=entity_id,
                relationship_id=relationship_id,
            ) from e
        except Exception as e:
            logger.error(
                f"Unexpected Neo4j error: {e}",
                extra={
                    "error_type": "unknown",
                    "error_class": type(e).__name__,
                    "entity_id": str(entity_id) if entity_id else None,
                    "relationship_id": str(relationship_id) if relationship_id else None,
                },
            )
            raise Neo4jSyncError(
                message=f"Unexpected Neo4j error: {e}",
                original_error=e,
                entity_id=entity_id,
                relationship_id=relationship_id,
            ) from e
