"""Domain Schema Registry for adaptive extraction.

This module provides a singleton registry for loading and managing
domain schemas from YAML files. The registry is designed to be
initialized once at application startup and reused throughout.

Thread Safety:
    The registry uses double-check locking pattern to ensure thread-safe
    singleton instantiation. Schema access after loading is read-only
    and therefore thread-safe.

Usage:
    # Get the singleton instance
    registry = DomainSchemaRegistry.get_instance()

    # Load schemas (typically at startup)
    registry.load_schemas()

    # Access schemas
    schema = registry.get_schema("literature_fiction")
    domains = registry.list_domains()

    # Or use convenience functions
    from kg_builder.extraction.domains.registry import get_domain_schema, list_available_domains
    schema = get_domain_schema("literature_fiction")
    domains = list_available_domains()

    # For FastAPI dependency injection
    from kg_builder.extraction.domains.registry import get_registry_dependency

    @router.get("/domains")
    async def list_domains(registry: DomainSchemaRegistry = Depends(get_registry_dependency)):
        return registry.list_domains()
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING

from kg_builder.extraction.domains.loader import (
    SchemaLoadError,
    get_schema_directory,
    load_all_schemas,
)
from kg_builder.extraction.domains.models import DomainSchema, DomainSummary

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

# Default schema directory
DEFAULT_SCHEMA_DIR = get_schema_directory()

# Environment variable to enable hot reload in development
HOT_RELOAD_ENV_VAR = "DOMAIN_SCHEMA_HOT_RELOAD"


class DomainSchemaRegistry:
    """Singleton registry for domain schemas.

    The registry loads domain schemas from YAML files and provides
    thread-safe access to schema data. It is designed to be initialized
    once and cached for the lifetime of the application.

    Attributes:
        schema_dir: Path to the directory containing schema files
        hot_reload: Whether to reload schemas on each access (development only)

    Example:
        >>> registry = DomainSchemaRegistry.get_instance()
        >>> registry.load_schemas()
        >>> schema = registry.get_schema("literature_fiction")
        >>> print(schema.display_name)
        Literature & Fiction
    """

    _instance: DomainSchemaRegistry | None = None
    _lock: Lock = Lock()

    def __init__(
        self,
        schema_dir: Path | None = None,
        *,
        hot_reload: bool | None = None,
    ) -> None:
        """Initialize the registry.

        Note:
            Use `get_instance()` instead of direct instantiation
            to ensure singleton behavior.

        Args:
            schema_dir: Directory containing YAML schema files.
                       Defaults to the built-in schemas directory.
            hot_reload: Enable hot reload for development. Defaults to
                       the value of DOMAIN_SCHEMA_HOT_RELOAD env var.
        """
        self._schema_dir = schema_dir or DEFAULT_SCHEMA_DIR
        self._schemas: dict[str, DomainSchema] = {}
        self._loaded = False
        self._load_lock = Lock()

        # Determine hot reload setting
        if hot_reload is not None:
            self._hot_reload = hot_reload
        else:
            self._hot_reload = os.getenv(HOT_RELOAD_ENV_VAR, "").lower() in (
                "true",
                "1",
                "yes",
            )

        if self._hot_reload:
            logger.warning(
                "Domain schema hot reload is enabled. This should only be used in development."
            )

    @classmethod
    def get_instance(
        cls,
        schema_dir: Path | None = None,
        *,
        force_new: bool = False,
        hot_reload: bool | None = None,
    ) -> DomainSchemaRegistry:
        """Get the singleton registry instance.

        Args:
            schema_dir: Optional schema directory for first initialization.
            force_new: If True, create a new instance (for testing).
            hot_reload: Enable hot reload for development.

        Returns:
            The singleton DomainSchemaRegistry instance.
        """
        if force_new:
            # For testing - bypass singleton
            return cls(schema_dir, hot_reload=hot_reload)

        if cls._instance is None:
            with cls._lock:
                # Double-check locking pattern for thread safety
                if cls._instance is None:
                    cls._instance = cls(schema_dir, hot_reload=hot_reload)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance.

        This is primarily for testing purposes. In production,
        the singleton should persist for the application lifetime.
        """
        with cls._lock:
            cls._instance = None

    @property
    def schema_dir(self) -> Path:
        """Get the schema directory path."""
        return self._schema_dir

    @property
    def is_loaded(self) -> bool:
        """Check if schemas have been loaded."""
        return self._loaded

    @property
    def hot_reload(self) -> bool:
        """Check if hot reload is enabled."""
        return self._hot_reload

    def load_schemas(self, *, force: bool = False) -> int:
        """Load all domain schemas from YAML files.

        Args:
            force: Force reload even if already loaded.

        Returns:
            Number of schemas loaded.

        Raises:
            FileNotFoundError: If schema directory doesn't exist.
            SchemaLoadError: If any schema file is invalid.
        """
        if self._loaded and not force:
            logger.debug("Schemas already loaded, skipping reload")
            return len(self._schemas)

        with self._load_lock:
            # Double-check after acquiring lock
            if self._loaded and not force:
                return len(self._schemas)

            if not self._schema_dir.exists():
                raise FileNotFoundError(f"Schema directory not found: {self._schema_dir}")

            logger.info("Loading domain schemas from %s", self._schema_dir)

            try:
                # Use existing loader module
                self._schemas = load_all_schemas(self._schema_dir)
                self._loaded = True

                logger.info(
                    "Loaded %d domain schema(s): %s",
                    len(self._schemas),
                    ", ".join(sorted(self._schemas.keys())),
                )

                return len(self._schemas)

            except SchemaLoadError as e:
                logger.error("Failed to load schemas: %s", e)
                raise

    def reload_schemas(self) -> int:
        """Force reload all domain schemas.

        Returns:
            Number of schemas loaded.

        Raises:
            FileNotFoundError: If schema directory doesn't exist.
            SchemaLoadError: If any schema file is invalid.
        """
        return self.load_schemas(force=True)

    def ensure_loaded(self) -> None:
        """Ensure schemas are loaded, loading them if necessary.

        This is a convenience method for lazy initialization.
        If hot_reload is enabled, this will reload schemas on each call.

        Raises:
            FileNotFoundError: If schema directory doesn't exist.
            SchemaLoadError: If any schema file is invalid.
        """
        if self._hot_reload:
            self.reload_schemas()
        elif not self._loaded:
            self.load_schemas()

    def get_schema(self, domain_id: str) -> DomainSchema:
        """Get a domain schema by ID.

        Domain IDs are case-insensitive and whitespace-trimmed.

        Args:
            domain_id: The domain identifier (e.g., "literature_fiction").

        Returns:
            The DomainSchema for the specified domain.

        Raises:
            KeyError: If the domain is not found.
        """
        self.ensure_loaded()

        normalized_id = domain_id.lower().strip()
        if normalized_id not in self._schemas:
            available = ", ".join(sorted(self._schemas.keys()))
            raise KeyError(
                f"Unknown domain: '{domain_id}'. Available domains: {available or 'none'}"
            )

        return self._schemas[normalized_id]

    def get_schema_or_none(self, domain_id: str) -> DomainSchema | None:
        """Get a domain schema by ID, or None if not found.

        Args:
            domain_id: The domain identifier.

        Returns:
            The DomainSchema if found, None otherwise.
        """
        try:
            return self.get_schema(domain_id)
        except KeyError:
            return None

    def get_default_schema(self) -> DomainSchema | None:
        """Get the default/fallback domain schema.

        Currently returns the 'encyclopedia_wiki' schema as the most
        general-purpose domain. Returns None if no schemas are loaded.

        Returns:
            The default DomainSchema, or None if not available.
        """
        self.ensure_loaded()

        # Try encyclopedia_wiki as the default (most general)
        default_domain_id = "encyclopedia_wiki"
        if default_domain_id in self._schemas:
            return self._schemas[default_domain_id]

        # Fallback to first available schema if any
        if self._schemas:
            return next(iter(self._schemas.values()))

        return None

    def list_domains(self) -> list[DomainSummary]:
        """List all available domains.

        Returns:
            List of DomainSummary objects for all loaded domains,
            sorted alphabetically by display_name.
        """
        self.ensure_loaded()

        return [
            DomainSummary.from_schema(schema)
            for schema in sorted(
                self._schemas.values(),
                key=lambda s: s.display_name,
            )
        ]

    def list_domain_ids(self) -> list[str]:
        """List all available domain IDs.

        Returns:
            Sorted list of domain ID strings.
        """
        self.ensure_loaded()
        return sorted(self._schemas.keys())

    def has_domain(self, domain_id: str) -> bool:
        """Check if a domain exists.

        Args:
            domain_id: The domain identifier.

        Returns:
            True if the domain exists, False otherwise.
        """
        self.ensure_loaded()
        return domain_id.lower().strip() in self._schemas

    def get_schemas_for_entity_type(self, entity_type: str) -> list[DomainSchema]:
        """Find all schemas that support a given entity type.

        Args:
            entity_type: The entity type ID to search for.

        Returns:
            List of DomainSchemas that include the entity type.
        """
        self.ensure_loaded()

        normalized = entity_type.lower().strip()
        return [
            schema
            for schema in self._schemas.values()
            if normalized in schema.get_entity_type_ids()
        ]

    def __len__(self) -> int:
        """Return the number of loaded schemas."""
        self.ensure_loaded()
        return len(self._schemas)

    def __iter__(self) -> Iterator[DomainSchema]:
        """Iterate over all loaded schemas."""
        self.ensure_loaded()
        return iter(self._schemas.values())

    def __contains__(self, domain_id: str) -> bool:
        """Check if a domain exists using 'in' operator."""
        return self.has_domain(domain_id)

    def __repr__(self) -> str:
        """Return string representation of the registry."""
        return (
            f"DomainSchemaRegistry("
            f"schema_dir={self._schema_dir!r}, "
            f"loaded={self._loaded}, "
            f"count={len(self._schemas) if self._loaded else '?'}"
            f")"
        )


@lru_cache(maxsize=1)
def get_domain_registry() -> DomainSchemaRegistry:
    """Get the domain schema registry with lazy initialization.

    This function provides a convenient way to access the registry
    from dependency injection or other contexts. The registry is
    cached and schemas are loaded on first access.

    Returns:
        The singleton DomainSchemaRegistry instance with schemas loaded.

    Raises:
        FileNotFoundError: If schema directory doesn't exist.
        SchemaLoadError: If any schema file is invalid.
    """
    registry = DomainSchemaRegistry.get_instance()
    registry.ensure_loaded()
    return registry


def get_registry_dependency() -> DomainSchemaRegistry:
    """FastAPI dependency for injecting the domain schema registry.

    Usage:
        from fastapi import Depends
        from kg_builder.extraction.domains.registry import (
            DomainSchemaRegistry,
            get_registry_dependency,
        )

        @router.get("/domains")
        async def list_domains(
            registry: DomainSchemaRegistry = Depends(get_registry_dependency)
        ):
            return registry.list_domains()

    Returns:
        The singleton DomainSchemaRegistry instance with schemas loaded.
    """
    return get_domain_registry()


def reset_registry_cache() -> None:
    """Reset the registry cache.

    This clears both the lru_cache and the singleton instance.
    Primarily for testing purposes.
    """
    get_domain_registry.cache_clear()
    DomainSchemaRegistry.reset_instance()


# Convenience functions for common operations


def get_domain_schema(domain_id: str) -> DomainSchema:
    """Get a domain schema by ID.

    Convenience function that uses the singleton registry.

    Args:
        domain_id: The domain identifier.

    Returns:
        The DomainSchema for the specified domain.

    Raises:
        KeyError: If the domain is not found.
    """
    return get_domain_registry().get_schema(domain_id)


def list_available_domains() -> list[DomainSummary]:
    """List all available domains.

    Convenience function that uses the singleton registry.

    Returns:
        List of DomainSummary objects for all loaded domains.
    """
    return get_domain_registry().list_domains()


def is_valid_domain(domain_id: str) -> bool:
    """Check if a domain ID is valid.

    Convenience function that uses the singleton registry.

    Args:
        domain_id: The domain identifier.

    Returns:
        True if the domain exists, False otherwise.
    """
    return get_domain_registry().has_domain(domain_id)


def get_default_domain_schema() -> DomainSchema | None:
    """Get the default/fallback domain schema.

    Convenience function that uses the singleton registry.

    Returns:
        The default DomainSchema, or None if not available.
    """
    return get_domain_registry().get_default_schema()
