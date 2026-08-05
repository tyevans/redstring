"""In-memory `VectorStore`: the reference adapter.

This is a real implementation, not a stub. It enforces every contract the port
states -- dimension rejection, zero-vector rejection, filter-before-`k`, the
tie-break order -- because an adapter more permissive than its port is useless
as a reference: tests written against it would pass here and fail on pgvector.

**Search is exact brute force.** Every vector of the tenant is scored. That is
`O(n)` per query and deliberately so: a reference implementation is judged on
being obviously correct, and the port's exactness tier is defined by what this
adapter does. An approximate index here would leave nothing to compare an
approximate adapter against.

**Copy on write and on read**, as in `graph/adapters/memory.py`: handing out a
reference lets a caller mutate stored state by accident, and keeping the
caller's object lets a caller mutate it afterwards. Both directions are closed
with a deep copy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from redstring.domain.exceptions import DimensionMismatchError
from redstring.domain.vector import VectorMatch, VectorRecord, cosine_score, is_zero_vector
from redstring.ports.vector_store import entity_type_of

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redstring.domain.ids import EntityId, TenantId


class InMemoryVectorStore:
    """A `VectorStore` backed by plain dictionaries."""

    def __init__(self, *, dimension: int) -> None:
        if dimension <= 0:
            raise ValueError(f"dimension must be positive, not {dimension}")
        self._dimension = dimension
        self._records: dict[TenantId, dict[EntityId, VectorRecord]] = {}

    @property
    def dimension(self) -> int:
        return self._dimension

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def upsert(
        self,
        entity_id: EntityId,
        vector: Sequence[float],
        tenant_id: TenantId,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self.upsert_many(
            [
                VectorRecord(
                    entity_id=entity_id,
                    tenant_id=tenant_id,
                    vector=list(vector),
                    metadata=metadata or {},
                )
            ]
        )

    async def upsert_many(self, items: Sequence[VectorRecord]) -> None:
        # Every element is validated before any is written, so a rejected
        # batch leaves no trace. The pgvector adapter gets this for free from
        # sending one statement; doing it here too keeps the two adapters from
        # differing on an axis no test would otherwise cover.
        for record in items:
            self._check(record.vector)

        for record in items:
            tenant = self._records.setdefault(record.tenant_id, {})
            # Later element wins, matching last-write-wins across calls. The
            # key is the *pair*: `record.tenant_id` selects the mapping and
            # `record.entity_id` the slot, so `(x, y)` and `(y, x)` are
            # different rows.
            tenant[record.entity_id] = record.model_copy(deep=True)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, entity_id: EntityId, tenant_id: TenantId) -> VectorRecord | None:
        record = self._records.get(tenant_id, {}).get(entity_id)
        return None if record is None else record.model_copy(deep=True)

    async def search(
        self,
        vector: Sequence[float],
        tenant_id: TenantId,
        *,
        k: int = 10,
        entity_types: Sequence[str] | None = None,
        min_score: float | None = None,
    ) -> list[VectorMatch]:
        self._check(vector)
        if k < 0:
            raise ValueError("k must not be negative")

        # `None` means no filter; an empty sequence means nothing matches, so
        # the membership test must not be short-circuited by a truthiness
        # check on the sequence itself.
        wanted = None if entity_types is None else set(entity_types)

        scored = [
            VectorMatch(
                entity_id=record.entity_id,
                score=cosine_score(vector, record.vector),
                metadata=dict(record.metadata),
            )
            for record in self._records.get(tenant_id, {}).values()
            # `entity_type_of`, not `metadata.get(...)`: the stored value may
            # be any JSON, and comparing it raw against a `set` raises
            # `TypeError` for a list or a dict. The port owns that rule.
            if wanted is None or entity_type_of(record.metadata) in wanted
        ]
        # Filters are applied to the whole tenant *before* `k` is taken. Taking
        # `k` first and filtering after returns fewer than `k` while matching
        # records exist further down -- correct-looking and wrong.
        if min_score is not None:
            scored = [match for match in scored if match.score >= min_score]

        # Descending score, ties broken by ascending canonical id string, so
        # `k` cutting through a tie cuts the same way on every adapter.
        scored.sort(key=lambda match: (-match.score, str(match.entity_id)))
        # Deep-copied on the way out: `metadata` above is a shallow copy, which
        # a nested container would let a caller reach through.
        return [match.model_copy(deep=True) for match in scored[:k]]

    # ------------------------------------------------------------------
    # Deletion
    # ------------------------------------------------------------------

    async def delete(self, entity_id: EntityId, tenant_id: TenantId) -> bool:
        return self._records.get(tenant_id, {}).pop(entity_id, None) is not None

    async def delete_by_tenant(self, tenant_id: TenantId) -> int:
        return len(self._records.pop(tenant_id, {}))

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _check(self, vector: Sequence[float]) -> None:
        """Reject anything the port says is not a vector for this store."""
        if len(vector) != self._dimension:
            raise DimensionMismatchError(expected=self._dimension, actual=len(vector))
        if is_zero_vector(vector):
            raise ValueError("a zero vector has no direction; cosine is undefined for it")
