"""Coordinating tenant-scoped rebuild across projections."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from eventsource.application.projections import replay

if TYPE_CHECKING:
    from eventsource.application.projections import EventSubscriber, ReplayReport
    from eventsource.domain.event import DomainEvent
    from eventsource.ports import GlobalEventFeed

    from redstring.domain.ids import TenantId


__all__ = ["rebuild_tenant"]


async def rebuild_tenant(
    feed: GlobalEventFeed,
    projections: Iterable[EventSubscriber] | EventSubscriber,
    *,
    tenant_id: TenantId,
    event_types: Iterable[str | type[DomainEvent]] | None = None,
    strict: bool = False,
    batch_size: int = 1000,
) -> ReplayReport:
    """Wipe `tenant_id` across projections supporting tenant wipe, then replay `feed`."""
    subscribers = list(projections) if isinstance(projections, Iterable) else [projections]
    for projection in subscribers:
        wipe = getattr(projection, "wipe_tenant", None)
        if callable(wipe):
            await wipe(tenant_id)
    return await replay(
        feed,
        subscribers,
        tenant_id=tenant_id,
        event_types=event_types,
        strict=strict,
        batch_size=batch_size,
    )
