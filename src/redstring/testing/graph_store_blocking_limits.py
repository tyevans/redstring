"""Blocking-read cap cases, mixed into `GraphStoreCompliance`.

Kept apart from `graph_store.py` for length, as `graph_store_bounds.py` is.
`GraphStoreCompliance` inherits from this mixin, so every adapter that
subclasses the compliance suite runs these cases. The mixin needs the `store`
fixture, which `GraphStoreCompliance` defines. No class here matches `Test*`,
so it is not collected on its own.

ADR-0152 states the contract: `limit_per_key` caps each key's group on its
own, keeps the lowest ids, counts only the asked tenant, and refuses a
negative value.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest

from redstring.domain.entity import Entity
from redstring.domain.ids import EntityId, TenantId
from redstring.domain.provenance import ExtractionMethod, Provenance

if TYPE_CHECKING:
    from redstring.ports.graph_store import GraphStore

__all__ = ["GraphStoreBlockingReadLimits"]


def _pinned_id(n: int) -> EntityId:
    """An entity id whose sort position is `n`, for tests where order matters.

    The leading digit sets the sort order. The low bits are scrambled,
    because `UUID(int=n)` hashes as `n`, so a set of such ids iterates in sort
    order and an adapter that takes "the first rows" passes a test of "the
    lowest ids" by coincidence. That is what the first version of these
    tests did.
    """
    scrambled = (0x9E3779B97F4A7C15 * n) % (1 << 48)
    return EntityId(UUID(f"{n:01x}0000000-0000-4000-8000-{scrambled:012x}"))


def _entity(*, tenant: TenantId, **overrides: Any) -> Entity:  # noqa: ANN401
    """A minimal valid entity. Kept local so this module does not import `graph_store`."""
    fields: dict[str, Any] = {
        "id": EntityId(uuid4()),
        "tenant_id": tenant,
        "name": "Ada Lovelace",
        "normalized_name": "ada lovelace",
        "entity_type": "person",
        "provenance": Provenance(
            observed_at=datetime(2026, 3, 1, tzinfo=UTC),
            extraction_method=ExtractionMethod.MANUAL,
            confidence=1.0,
        ),
    }
    fields.update(overrides)
    return Entity(**fields)


class GraphStoreBlockingReadLimits:
    """`find_by_blocking_keys(..., limit_per_key=...)`, for every adapter."""

    async def test_find_by_blocking_keys_with_no_keys_is_empty(self, store: GraphStore) -> None:
        assert await store.find_by_blocking_keys([], TenantId(uuid4())) == {}

    async def test_find_by_blocking_keys_limit_keeps_the_lowest_ids_of_each_key(
        self, store: GraphStore
    ) -> None:
        """The cap is per key, and it keeps the lowest ids, not the first rows.

        The entities are written highest id first, so "the first rows the
        store holds" and "the lowest ids" disagree. Key `B` shares two
        members with `A`, so a cap on the total, or one taken from the union,
        gives `B` a different group than a cap per key does.
        """
        tenant = TenantId(uuid4())
        members = {1: {"A"}, 2: {"A"}, 3: {"A", "B"}, 4: {"A", "B"}, 5: {"B"}}
        await store.upsert_entities(
            [
                _entity(tenant=tenant, id=_pinned_id(n), blocking_keys=frozenset(members[n]))
                for n in sorted(members, reverse=True)
            ]
        )

        found = await store.find_by_blocking_keys(["A", "B"], tenant, limit_per_key=2)

        assert {e.id for e in found["A"]} == {_pinned_id(1), _pinned_id(2)}
        assert {e.id for e in found["B"]} == {_pinned_id(3), _pinned_id(4)}

    async def test_find_by_blocking_keys_limit_does_not_count_another_tenants_ids(
        self, store: GraphStore
    ) -> None:
        """Lower ids in another tenant under the same key take no slot."""
        tenant_a, tenant_b = TenantId(uuid4()), TenantId(uuid4())
        await store.upsert_entities(
            [
                _entity(tenant=tenant_b, id=_pinned_id(1), blocking_keys=frozenset({"A"})),
                _entity(tenant=tenant_b, id=_pinned_id(2), blocking_keys=frozenset({"A"})),
                _entity(tenant=tenant_a, id=_pinned_id(3), blocking_keys=frozenset({"A"})),
            ]
        )

        found = await store.find_by_blocking_keys(["A"], tenant_a, limit_per_key=1)

        assert [(e.tenant_id, e.id) for e in found["A"]] == [(tenant_a, _pinned_id(3))]

    async def test_find_by_blocking_keys_limit_zero_keeps_every_key_and_no_entity(
        self, store: GraphStore
    ) -> None:
        tenant = TenantId(uuid4())
        await store.upsert_entity(_entity(tenant=tenant, blocking_keys=frozenset({"A"})))

        assert await store.find_by_blocking_keys(["A", "absent"], tenant, limit_per_key=0) == {
            "A": [],
            "absent": [],
        }

    async def test_find_by_blocking_keys_limit_above_the_block_returns_the_whole_block(
        self, store: GraphStore
    ) -> None:
        tenant = TenantId(uuid4())
        await store.upsert_entities(
            [_entity(tenant=tenant, blocking_keys=frozenset({"A"})) for _ in range(3)]
        )

        capped = await store.find_by_blocking_keys(["A"], tenant, limit_per_key=1000)
        uncapped = await store.find_by_blocking_keys(["A"], tenant)

        assert len(capped["A"]) == 3
        assert {e.id for e in capped["A"]} == {e.id for e in uncapped["A"]}

    async def test_find_by_blocking_keys_refuses_a_negative_limit(self, store: GraphStore) -> None:
        with pytest.raises(ValueError, match="limit_per_key must be non-negative, got -1"):
            await store.find_by_blocking_keys(["A"], TenantId(uuid4()), limit_per_key=-1)
