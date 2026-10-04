"""Domain schema catalog and query operations.

Provides read-only lookup, search, and filtering over loaded domain schemas.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from redstring.extraction.domains.models import DomainSchema, DomainSummary

if TYPE_CHECKING:
    from collections.abc import Iterator


class DomainCatalog:
    """Read-only container and query interface for domain schemas.

    Encapsulates schema lookup, domain filtering, and entity-type resolution.
    """

    def __init__(self, schemas: dict[str, DomainSchema] | None = None) -> None:
        self._schemas: dict[str, DomainSchema] = schemas or {}

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
        normalized_id = domain_id.lower().strip()
        if normalized_id not in self._schemas:
            available = ", ".join(sorted(self._schemas.keys()))
            raise KeyError(
                f"Unknown domain: '{domain_id}'. Available domains: {available or 'none'}"
            )

        return self._schemas[normalized_id]

    def get_schema_or_none(self, domain_id: str) -> DomainSchema | None:
        """Get a domain schema by ID, or None if not found."""
        try:
            return self.get_schema(domain_id)
        except KeyError:
            return None

    def get_default_schema(self) -> DomainSchema | None:
        """Get the default/fallback domain schema.

        Currently returns the 'encyclopedia_wiki' schema as the most
        general-purpose domain. Returns None if no schemas are loaded.
        """
        default_domain_id = "encyclopedia_wiki"
        if default_domain_id in self._schemas:
            return self._schemas[default_domain_id]

        if self._schemas:
            return next(iter(self._schemas.values()))

        return None

    def list_domains(self) -> list[DomainSummary]:
        """List all available domains sorted by display_name."""
        return [
            DomainSummary.from_schema(schema)
            for schema in sorted(
                self._schemas.values(),
                key=lambda s: s.display_name,
            )
        ]

    def list_domain_ids(self) -> list[str]:
        """List all available domain IDs sorted alphabetically."""
        return sorted(self._schemas.keys())

    def has_domain(self, domain_id: str) -> bool:
        """Check if a domain exists."""
        return domain_id.lower().strip() in self._schemas

    def get_schemas_for_entity_type(self, entity_type: str) -> list[DomainSchema]:
        """Find all schemas that support a given entity type."""
        normalized = entity_type.lower().strip()
        return [
            schema
            for schema in self._schemas.values()
            if normalized in schema.get_entity_type_ids()
        ]

    def __len__(self) -> int:
        return len(self._schemas)

    def __iter__(self) -> Iterator[DomainSchema]:
        return iter(self._schemas.values())

    def __contains__(self, domain_id: str) -> bool:
        return self.has_domain(domain_id)
