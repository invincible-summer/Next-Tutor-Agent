"""Request-scoped tenant context for domain document access.

Enterprise sessions pin the tenant the token was issued for; the API
boundary copies that claim here (``TenantContextMiddleware``) so every
repository call resolves its tenant without threading a parameter
through each domain store. ``bridge.call`` propagates this context onto
the worker loop's task, so both the adapter code and the coroutine body
see the same tenant.

The empty string is the legacy pre-tenant scope — the default outside
requests (importers, background sweeps in single-tenant deployments,
tests without tenants). Background sweeps that must span tenants
enumerate ``(tenant_id, owner_id)`` scopes and re-enter each with
:func:`tenant_scope`.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token

_tenant: ContextVar[str] = ContextVar("domain_document_tenant", default="")


def current_tenant() -> str:
    """The tenant scope domain documents resolve against right now."""
    return _tenant.get()


def set_tenant(tenant_id: str | None) -> Token:
    return _tenant.set(str(tenant_id or ""))


def reset_tenant(token: Token) -> None:
    _tenant.reset(token)


@contextmanager
def tenant_scope(tenant_id: str | None):
    """Run a block against one tenant's document scope."""
    token = set_tenant(tenant_id)
    try:
        yield
    finally:
        reset_tenant(token)
