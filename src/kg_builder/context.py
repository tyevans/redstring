"""
Application context variables for async-safe state management.

Re-exports from eventsource.multitenancy for tenant context propagation.
This module provides backward-compatible aliases for existing code.
"""

from uuid import UUID

from eventsource.multitenancy import (
    clear_tenant_context,
    get_current_tenant,
    get_required_tenant,
    set_current_tenant as _set_current_tenant,
    tenant_context as current_tenant_id,
    tenant_scope,
    tenant_scope_sync,
)

__all__ = [
    "current_tenant_id",
    "get_current_tenant",
    "get_required_tenant",
    "set_current_tenant",
    "clear_current_tenant",
    "tenant_scope",
    "tenant_scope_sync",
]


def set_current_tenant(tenant_id: UUID) -> None:
    """
    Set current tenant ID in context.

    Args:
        tenant_id: UUID of tenant to set as current

    Note:
        This is a backward-compatible wrapper that ignores the token return value.
        For code that needs to restore context, use tenant_scope() or tenant_scope_sync().
    """
    _set_current_tenant(tenant_id)


def clear_current_tenant() -> None:
    """
    Clear current tenant ID from context.

    Alias for clear_tenant_context() for backward compatibility.
    """
    clear_tenant_context()
