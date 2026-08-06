"""How the two aggregates are loaded and saved.

Both come wrapped in `TenantAwareRepository`, so a `save` outside a
`tenant_scope` raises rather than writing, and an event whose `tenant_id`
disagrees with the ambient scope raises rather than landing in the log.
Tenant isolation is the property this project treats as inviolable, and
having it enforced at write time by tested library code beats re-deriving it
at each call site.

`validate_on_save` is left at its default `True`; `require_tenant_context` is
not turned on, because it requires that a context exists without filtering
events by it -- see the library's own note. Loading is safe regardless: a
`Document` stream holds one document's events and its id is already derived
from the tenant, and a `ConsolidationLog` stream *is* a tenant.

The flag was called `enforce_on_load` until `eventsource-py` 0.12.0 (its ADR
0057) renamed it, and the rename is the reason this paragraph is shorter than
it was: the old name promised enforcement the code never performed, so a
downstream note had to spend a paragraph saying so. `require_tenant_context`
states the precondition it does impose, and there is no alias -- passing
`enforce_on_load=` now raises `TypeError`, which is the point.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from eventsource.application.aggregates.repository import AggregateRepository
from eventsource.application.aggregates.tenant_repository import TenantAwareRepository

from redstring.aggregates.consolidation_log import ConsolidationLog
from redstring.aggregates.document import Document

if TYPE_CHECKING:
    from eventsource.ports.snapshots import SnapshotStore
    from eventsource.ports.store import AggregateStore

#: Events between `ConsolidationLog` snapshots.
#:
#: The stream grows with a tenant's merge history and has no natural bound, so
#: rehydration without snapshots grows without bound too. 100 is a starting
#: point rather than a measured optimum: small enough that a rehydration reads
#: a bounded tail, large enough that a snapshot is not written on most saves.
#: Tune it when a tenant's merge volume is known -- nothing depends on the
#: number being 100.
CONSOLIDATION_SNAPSHOT_EVERY = 100


def document_repository(event_store: AggregateStore) -> TenantAwareRepository[Document]:
    """A repository for `Document` aggregates.

    No snapshot store: a document accumulates one event per model version,
    which is a handful over its whole life. A snapshot would cost a write to
    save a replay of three events.
    """
    return TenantAwareRepository(AggregateRepository(event_store, Document))


def consolidation_repository(
    event_store: AggregateStore,
    snapshot_store: SnapshotStore,
    *,
    snapshot_every: int = CONSOLIDATION_SNAPSHOT_EVERY,
) -> TenantAwareRepository[ConsolidationLog]:
    """A repository for `ConsolidationLog` aggregates, with snapshots.

    `snapshot_store` is required rather than optional. The unbounded stream is
    the known cost of serialising consolidation per tenant, and an optional
    parameter is one nobody passes -- the omission would surface as slow
    merges long after the code that omitted it was written.
    """
    return TenantAwareRepository(
        AggregateRepository(
            event_store,
            ConsolidationLog,
            snapshot_store=snapshot_store,
            snapshot_threshold=snapshot_every,
        )
    )
