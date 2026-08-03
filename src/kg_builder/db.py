"""
Database infrastructure.

This module provides the async SQLAlchemy engine, session factory,
and declarative base for all database models. It includes connection
pooling configuration optimized for multi-tenant workloads.
"""

import logging
from typing import Any
from datetime import datetime, timezone

from sqlalchemy import DateTime, create_engine, event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session, sessionmaker
from sqlalchemy.pool import Pool

from kg_builder.config import settings

# Configure logger
logger = logging.getLogger(__name__)

# Convert DATABASE_URL to use asyncpg driver if needed
database_url = settings.DATABASE_URL
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

# Create async engine with connection pooling optimized for multi-tenant load
engine: AsyncEngine = create_async_engine(
    database_url,
    pool_size=20,  # Support 20 concurrent database operations
    max_overflow=10,  # Allow bursts up to 30 total connections
    pool_pre_ping=False,  # Disabled to avoid event loop issues in tests (safe in production with pool_recycle)
    pool_recycle=3600,  # Recycle connections after 1 hour to prevent long-lived issues
    echo=settings.DB_ECHO,  # Log SQL queries (controlled separately from DEBUG)
    future=True,  # Use SQLAlchemy 2.0 API style
    pool_reset_on_return="rollback",  # Reset connections on return to pool
)

# Create async session factory with explicit transaction control
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # Don't expire objects after commit (avoid extra queries)
    autocommit=False,  # Explicit transaction control for better error handling
    autoflush=False,  # Explicit flush control to optimize batch operations
)

# Synchronous database URL for Celery workers
sync_database_url = settings.DATABASE_URL
if sync_database_url.startswith("postgresql+asyncpg://"):
    sync_database_url = sync_database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

# Create synchronous engine for Celery workers
sync_engine = create_engine(
    sync_database_url,
    pool_size=10,  # Smaller pool for workers
    max_overflow=5,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=settings.DB_ECHO,
)

# Create synchronous session factory for Celery workers
SyncSessionLocal: sessionmaker[Session] = sessionmaker(
    sync_engine,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """
    Base class for all database models.

    Provides common columns that all models inherit:
    - id: Integer primary key with automatic indexing
    - created_at: Timezone-aware timestamp of record creation (UTC)
    - updated_at: Timezone-aware timestamp of last update (UTC)

    All timestamps use UTC to prevent timezone confusion in multi-tenant
    and multi-region deployments.
    """

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


# Connection pool event listeners for observability
@event.listens_for(Pool, "connect")
def receive_connect(dbapi_conn: Any, connection_record: Any) -> None:
    """
    Event listener for new database connections.

    Logs when a new connection is established to the database.
    Useful for monitoring connection creation rate and debugging
    connection issues.

    Args:
        dbapi_conn: The DBAPI connection object
        connection_record: The connection record
    """
    logger.debug("Database connection established")


@event.listens_for(Pool, "checkout")
def receive_checkout(dbapi_conn: Any, connection_record: Any, connection_proxy: Any) -> None:
    """
    Event listener for connection checkout from pool.

    Logs when a connection is checked out from the pool.
    Useful for monitoring pool usage patterns.

    Args:
        dbapi_conn: The DBAPI connection object
        connection_record: The connection record
        connection_proxy: The connection proxy
    """
    logger.debug("Database connection checked out from pool")


@event.listens_for(Pool, "checkin")
def receive_checkin(dbapi_conn: Any, connection_record: Any) -> None:
    """
    Event listener for connection checkin to pool.

    Logs when a connection is returned to the pool.
    Useful for detecting connection leaks (if checkouts > checkins).

    Args:
        dbapi_conn: The DBAPI connection object
        connection_record: The connection record
    """
    logger.debug("Database connection returned to pool")


async def init_db() -> None:
    """
    Initialize database connection on application startup.

    Verifies that the database is accessible by executing a simple query.
    This function should be called during application startup to fail fast
    if the database is not available.

    Raises:
        Exception: If database connection cannot be established

    Example:
        @app.on_event("startup")
        async def startup_event():
            await init_db()
    """
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text("SELECT 1"))
            assert result.scalar() == 1
        # Extract database info without credentials for logging
        db_info = database_url.split("@")[-1] if "@" in database_url else "unknown"
        logger.info(
            "Database connection established successfully",
            extra={
                "database_url": db_info,  # Log without credentials
                "pool_size": 20,
                "max_overflow": 10,
            },
        )
    except Exception as e:
        # Extract database info without credentials for logging
        db_info = database_url.split("@")[-1] if "@" in database_url else "unknown"
        logger.error(
            "Failed to connect to database",
            extra={"error": str(e), "database_url": db_info},
            exc_info=True,
        )
        raise


async def close_db() -> None:
    """
    Close all database connections on application shutdown.

    Properly disposes of the connection pool and closes all active connections.
    This function should be called during application shutdown to ensure clean
    resource cleanup.

    Example:
        @app.on_event("shutdown")
        async def shutdown_event():
            await close_db()
    """
    await engine.dispose()
    logger.info("Database connections closed and pool disposed")


async def get_db_health() -> dict[str, Any]:
    """
    Check database health status.

    Executes a simple query to verify database connectivity.
    Used by health check endpoints to monitor database availability.

    Returns:
        dict: Health status with 'status' and optional 'error' keys

    Example:
        health = await get_db_health()
        if health['status'] == 'healthy':
            print("Database is accessible")
    """
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        logger.error("Database health check failed", extra={"error": str(e)}, exc_info=True)
        return {"status": "unhealthy", "database": "disconnected", "error": str(e)}
