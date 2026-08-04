"""Domain-level errors.

These are raised by ports and adapters when a domain invariant cannot be
satisfied. They carry the identifiers involved so a caller can act on them
without parsing a message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kg_builder.domain.ids import EntityId, TenantId


class KgBuilderError(Exception):
    """Base class for every error this library raises deliberately."""


class MissingEntityError(KgBuilderError):
    """A referenced entity does not exist in the given tenant.

    Raised when writing a relationship whose endpoint is absent: dangling
    edges are not permitted in a `GraphStore`.
    """

    def __init__(self, *, entity_id: EntityId, tenant_id: TenantId) -> None:
        self.entity_id = entity_id
        self.tenant_id = tenant_id
        super().__init__(f"entity {entity_id} does not exist in tenant {tenant_id}")
