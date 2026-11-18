"""Traversal-depth contract cases, mixed into `GraphStoreCompliance`.

Kept apart from `graph_store.py` for length. The cases still run against every
adapter, because `GraphStoreCompliance` inherits from this mixin, so an adapter
that subclasses the compliance suite inherits them too.

The mixin needs the `store` fixture, which `GraphStoreCompliance` defines. It is
not collected on its own: no class here matches `Test*`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from redstring.domain.entity import Entity
from redstring.domain.ids import EntityId, RelationshipId, TenantId
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.relationship import Relationship
from redstring.ports.graph_store import MAX_TRAVERSAL_DEPTH

if TYPE_CHECKING:
    from redstring.ports.graph_store import GraphStore

__all__ = ["GraphStoreTraversalBounds"]


def _entity(tenant: TenantId) -> Entity:
    """A minimal valid entity. Kept local so this module does not import `graph_store`."""
    return Entity(
        id=EntityId(uuid4()),
        tenant_id=tenant,
        name="Ada Lovelace",
        normalized_name="ada lovelace",
        entity_type="person",
        provenance=Provenance(
            observed_at=datetime(2026, 3, 1, tzinfo=UTC),
            extraction_method=ExtractionMethod.MANUAL,
            confidence=1.0,
        ),
    )


def _edge(tenant: TenantId, source: EntityId, target: EntityId) -> Relationship:
    return Relationship(
        id=RelationshipId(uuid4()),
        tenant_id=tenant,
        source_entity_id=source,
        target_entity_id=target,
        relationship_type="knows",
        confidence=1.0,
    )


class GraphStoreTraversalBounds:
    """Both sides of `MAX_TRAVERSAL_DEPTH`. Every adapter must pass these."""

    async def test_depth_at_the_maximum_is_accepted(self, store: GraphStore) -> None:
        """The cap is inclusive: a request at the cap still answers.

        A two-node chain makes the answer checkable. An adapter that raised
        at the cap would fail here rather than pass on an empty result.
        """
        tenant = TenantId(uuid4())
        a, b = _entity(tenant), _entity(tenant)
        await store.upsert_entities([a, b])
        await store.upsert_relationship(_edge(tenant, a.id, b.id))

        reached = await store.neighbors(a.id, tenant, depth=MAX_TRAVERSAL_DEPTH)
        assert [e.id for e in reached] == [b.id]

    async def test_depth_above_the_maximum_is_rejected(self, store: GraphStore) -> None:
        """One past the cap raises, even for an entity that exists.

        The entity is real on purpose. The rejection must not depend on the
        origin being absent, or an adapter that checked depth only after the
        lookup would pass with an unknown id.
        """
        tenant = TenantId(uuid4())
        a = _entity(tenant)
        await store.upsert_entity(a)

        with pytest.raises(ValueError, match="depth"):
            await store.neighbors(a.id, tenant, depth=MAX_TRAVERSAL_DEPTH + 1)
