"""What the two projections share: a store, and the parent's full constructor.

The constructor is spelled out rather than forwarded through `**kwargs`
because a subclass that narrows its parent's constructor is a real hazard --
eventsource 0.10 widened its own projection constructors for exactly that
reason. Spelling the parameters out keeps `retry_policy`, `tenant_filter` and
the tracing switches reachable, and keeps them typed, which `**kwargs: Any`
would not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from eventsource.application.projections import DeclarativeProjection

if TYPE_CHECKING:
    from eventsource.application.projections.base import TenantFilter
    from eventsource.application.projections.retry import RetryPolicy
    from eventsource.observability import Tracer
    from eventsource.ports.checkpoints import ProjectionCheckpoints
    from eventsource.ports.dlq import DLQRepository


class StoreProjection[TStore](DeclarativeProjection):
    """A projection that folds the log into one store."""

    def __init__(
        self,
        store: TStore,
        checkpoint_repo: ProjectionCheckpoints | None = None,
        dlq_repo: DLQRepository | None = None,
        enable_tracing: bool = False,
        *,
        retry_policy: RetryPolicy | None = None,
        tracer: Tracer | None = None,
        tenant_filter: TenantFilter = None,
    ) -> None:
        self._store = store
        super().__init__(
            checkpoint_repo=checkpoint_repo,
            dlq_repo=dlq_repo,
            enable_tracing=enable_tracing,
            retry_policy=retry_policy,
            tracer=tracer,
            tenant_filter=tenant_filter,
        )
