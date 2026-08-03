"""
Base event classes for Knowledge Mapper.

Re-exports TenantDomainEvent from eventsource.multitenancy.
All domain events in the application should inherit from TenantDomainEvent
to ensure proper multi-tenancy support.
"""

from eventsource.multitenancy import TenantDomainEvent

__all__ = ["TenantDomainEvent"]
