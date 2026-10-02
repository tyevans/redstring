"""Coordinating tenant-scoped rebuild across projections."""

from __future__ import annotations

from typing import TYPE_CHECKING

from eventsource.application.projections import replay

if TYPE_CHECKING:
    from collections.abc import Sequence

    from eventsource.application.projections import EventSubscriber, ReplayReport
    from eventsource.ports import GlobalEventFeed

    from redstring.domain.ids import TenantId


async def rebuild_tenant(
    feed: GlobalEventFeed,
    projections: Sequence[EventSubscriber],
    *,
    tenant_id: TenantId,
    strict: bool = False,
    batch_size: int = 1000,
) -> ReplayReport:
    """Wipe `tenant_id` across projections supporting tenant wipe, then replay `feed`."""
    for projection in projections:
        wipe = getattr(projection, "wipe_tenant", None)
        if callable(wipe):
            await wipe(tenant_id)
    return await replay(
        feed,
        projections,
        tenant_id=tenant_id,
        strict=strict,
        batch_size=batch_size,
    )
