"""Tenant-aware request principal.

``resolve_student_id()`` remains the one trusted student identity. The
principal adds the tenant/membership/session facts enterprise routes need.
File mode (no tenants yet) resolves with tenant fields empty — the shape is
identical so callers never branch on mode.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class RequestPrincipal:
    user_id: str
    platform_role: str = "student"
    tenant_id: str | None = None
    membership_id: str | None = None
    tenant_role: str | None = None
    auth_session_id: str | None = None
