"""JSON-based user account store.

Mirrors core/session.py and student_model/store.py: a single JSON working file
at the project root under users/, with path-traversal guards. All password
hashes live here; the file is gitignored.

Account index layout (users/accounts.json):
    {"users": {email_lower: {full User.to_dict()}}, "by_id": {user_id: email}}

The dual index lets us look up by email (login) or by id (token verification)
in O(1) without scanning. Writes are atomic (write-then-rename).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import config
from .models import User, UserProfile
from ..core.atomic import atomic_write_text, file_lock

_ACCOUNTS_FILE = config.USERS_DIR / "accounts.json"


def _ensure_dir() -> None:
    config.USERS_DIR.mkdir(parents=True, exist_ok=True)


def _load_raw() -> dict[str, Any]:
    """Load the account index. Missing/corrupt -> empty structure."""
    if not _ACCOUNTS_FILE.exists():
        return {"users": {}, "by_id": {}}
    try:
        data = json.loads(_ACCOUNTS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {"users": data.get("users") or {},
                    "by_id": data.get("by_id") or {}}
    except (json.JSONDecodeError, OSError):
        pass
    return {"users": {}, "by_id": {}}


def _save_raw(data: dict[str, Any]) -> None:
    """Atomic write (temp file + fsync + rename) to avoid partial-write corruption."""
    _ensure_dir()
    atomic_write_text(_ACCOUNTS_FILE, json.dumps(data, ensure_ascii=False, indent=2))


# --- lookups ----------------------------------------------------------------

def get_by_email(email: str) -> User | None:
    """Find a user by email (case-insensitive)."""
    raw = _load_raw()
    entry = (raw.get("users") or {}).get(email.strip().lower())
    return User.from_dict(entry) if entry else None


def get_by_id(user_id: str) -> User | None:
    """Find a user by id (from a JWT sub claim)."""
    raw = _load_raw()
    email = (raw.get("by_id") or {}).get(user_id)
    if not email:
        return None
    entry = (raw.get("users") or {}).get(email)
    return User.from_dict(entry) if entry else None


def email_exists(email: str) -> bool:
    return email.strip().lower() in (_load_raw().get("users") or {})


# --- mutations --------------------------------------------------------------

def create_user(email: str, username: str, password_hash: str,
                role: str = "student", profile: UserProfile | None = None,
                user_id: str | None = None) -> User:
    """Insert a new user. Raises ValueError if the email is already taken."""
    with file_lock(_ACCOUNTS_FILE):
        raw = _load_raw()
        key = email.strip().lower()
        if key in (raw.get("users") or {}):
            raise ValueError("email_already_registered")
        import uuid
        uid = user_id or f"usr_{uuid.uuid4().hex[:10]}"
        user = User(
            id=uid, email=key, username=username.strip() or email.split("@")[0],
            password_hash=password_hash, role=role, created_at=time.time(),
            profile=profile or UserProfile(),
        )
        users = raw.setdefault("users", {})
        by_id = raw.setdefault("by_id", {})
        users[key] = user.to_dict()
        by_id[uid] = key
        _save_raw(raw)
    return user


def update_user(user: User) -> None:
    """Persist field changes (profile, last_login_at, etc.)."""
    with file_lock(_ACCOUNTS_FILE):
        raw = _load_raw()
        key = user.email.strip().lower()
        users = raw.setdefault("users", {})
        by_id = raw.setdefault("by_id", {})
        users[key] = user.to_dict()
        by_id[user.id] = key
        _save_raw(raw)


def touch_login(user_id: str) -> None:
    """Update last_login_at without reloading the full object."""
    user = get_by_id(user_id)
    if user:
        user.last_login_at = time.time()
        update_user(user)


def delete_user(user_id: str) -> bool:
    """Remove a user account. Does NOT touch students/ data (kept for audit)."""
    with file_lock(_ACCOUNTS_FILE):
        raw = _load_raw()
        by_id = raw.get("by_id") or {}
        users = raw.get("users") or {}
        email = by_id.get(user_id)
        if not email:
            return False
        users.pop(email, None)
        by_id.pop(user_id, None)
        _save_raw(raw)
    return True


def list_users() -> list[User]:
    """All registered users (admin console). Oldest first."""
    raw = _load_raw()
    out = [User.from_dict(e) for e in (raw.get("users") or {}).values()]
    out.sort(key=lambda u: u.created_at)
    return out


def ensure_admin_account() -> None:
    """P6-B1：从 ADMIN_EMAIL/ADMIN_PASSWORD 引导管理员账号（启动时调用一次）。

    账号不存在 → 创建 role=admin；已存在但非 admin → 提升。未配置 env 则 no-op。
    永不抛出（启动路径不容失败）；密码用既有 bcrypt 哈希。
    """
    try:
        import os
        email = (os.getenv("ADMIN_EMAIL") or "").strip().lower()
        password = os.getenv("ADMIN_PASSWORD") or ""
        if not email or not password:
            return
        from .security import hash_password
        existing = get_by_email(email)
        if existing is None:
            create_user(email=email, username="管理员",
                        password_hash=hash_password(password), role="admin")
        elif existing.role != "admin":
            existing.role = "admin"
            update_user(existing)
    except Exception:
        pass
