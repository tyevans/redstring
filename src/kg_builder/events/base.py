"""
Base event classes for Knowledge Mapper.

Re-exports `TenantDomainEvent` from `eventsource.domain.tenant_events`.

**Legacy.** The events in this package that still inherit from it -- the
`consolidation` and `scraping` modules -- are ORM-shaped, have never been
emitted, and die with their last consumers in slices 7 and 9. The event
schema this library actually writes is `kg_builder.events.document` and
`kg_builder.events.merge`; those import `TenantDomainEvent` directly.
"""

from eventsource.domain.tenant_events import TenantDomainEvent

__all__ = ["TenantDomainEvent"]
