"""M0 Identity & Account Infrastructure.

The bottom layer: answers "who is this user, whose data is this, how do we
access it safely?". Every M1-M9 module is keyed by a student_id that now flows
from the authenticated user's identity.

Design:
  - user_id == student_id. A registered user's id IS their student namespace
    key, so every existing store works unchanged (already parametrized by id).
  - AUTH_MODE toggle (env): 0 = anonymous/guest (DEFAULT_STUDENT_ID),
    1 = login required (identity resolved from JWT in Authorization header).
  - JWT (PyJWT) for stateless auth tokens, bcrypt for password hashing.
  - Data isolation: each user's M2-M9 data lives in students/<user_id>.*.
"""
from __future__ import annotations

import os

from app.agents.student_model.store import DEFAULT_STUDENT_ID


def is_auth_required() -> bool:
    """Whether login is enforced (AUTH_MODE=1). Default is guest mode (0)."""
    return os.getenv("AUTH_MODE", "0") == "1"


def fallback_student_id() -> str:
    """The student_id used when there is no authenticated user."""
    return DEFAULT_STUDENT_ID


__all__ = ["is_auth_required", "fallback_student_id"]
