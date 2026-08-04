"""Domain-level errors.

These are raised by ports and adapters when a domain invariant cannot be
satisfied. They carry the identifiers involved so a caller can act on them
without parsing a message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

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


class DimensionMismatchError(KgBuilderError):
    """A vector's length does not match the store's configured dimension.

    A store is built for one embedding model and one dimension. Accepting a
    vector of a different length is a silent correctness catastrophe: it does
    not surface as an exception but as mediocre search results, which read as
    a mediocre embedding model rather than as a bug. Changing model therefore
    means a new store, not an in-place write of differently-shaped vectors.
    """

    def __init__(self, *, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"expected a vector of dimension {expected}, got {actual}")


class ConsolidationInvariantError(KgBuilderError):
    """A merge or undo would violate a rule the consolidation log enforces.

    These three rules used to be enforced by nothing, and each one corrupts a
    graph quietly rather than loudly: a merge into an alias leaves a chain
    nothing resolves, a double merge gives one entity two canonical parents,
    and an undo of a merge that never happened restores edges that were never
    displaced.
    """


class MergeIntoAliasError(ConsolidationInvariantError):
    """The proposed canonical entity has itself been merged into another."""

    def __init__(self, *, alias_entity_id: EntityId, canonical_entity_id: EntityId) -> None:
        self.alias_entity_id = alias_entity_id
        self.canonical_entity_id = canonical_entity_id
        super().__init__(
            f"cannot merge into {alias_entity_id}: it is already an alias of {canonical_entity_id}"
        )


class DoubleMergeError(ConsolidationInvariantError):
    """An entity in this merge has already been merged into another."""

    def __init__(self, *, entity_id: EntityId, canonical_entity_id: EntityId) -> None:
        self.entity_id = entity_id
        self.canonical_entity_id = canonical_entity_id
        super().__init__(f"entity {entity_id} has already been merged into {canonical_entity_id}")


class UnknownMergeError(ConsolidationInvariantError):
    """No merge in effect matches the event id an undo refers to.

    Covers both "never happened" and "already undone". The two are one case
    from the caller's point of view -- there is no merge to reverse -- and
    distinguishing them in the type would invite handling only one.
    """

    def __init__(self, *, merge_event_id: UUID) -> None:
        self.merge_event_id = merge_event_id
        super().__init__(f"no merge in effect with event id {merge_event_id}")
