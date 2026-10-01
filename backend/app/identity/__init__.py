"""M0 Identity & Account Infrastructure.

The bottom layer: answers "who is this user, whose data is this, how do we
access it safely?". Every M1-M9 module is keyed by a student_id that now flows
from the authenticated user's identity.

Design:
  - user_id == student_id. A registered user's id IS their student namespace
    key, so every existing store works unchanged (already parametrized by id).
  - Admin guest policy: default deny; enabled guests use isolated RAM state.
  - JWT (PyJWT) for stateless auth tokens, bcrypt for password hashing.
  - Data isolation: each user's M2-M9 data lives in students/<user_id>.*.
"""
from __future__ import annotations

from app.agents.student_model.store import DEFAULT_STUDENT_ID


def is_auth_required() -> bool:
    """Whether guest learning is disabled by the live administrator policy."""
    from app.core.guest_policy import guests_allowed
    return not guests_allowed()


def fallback_student_id() -> str:
    """Legacy namespace retained for old storage helpers and data cleanup."""
    return DEFAULT_STUDENT_ID


__all__ = ["is_auth_required", "fallback_student_id"]
