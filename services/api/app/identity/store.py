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
import logging
import os
import time
from pathlib import Path
from typing import Any

from . import config
from .models import User, UserProfile
from ..core.atomic import atomic_write_text, file_lock

log = logging.getLogger(__name__)

from app.core import paths as _paths

_ACCOUNTS_FILE = _paths.bind_storage_path(
    __name__, "_ACCOUNTS_FILE", "users", "accounts.json")

# AUTH_MODE=1（生产）下引导管理员时拒绝的弱默认密码：照抄 .env.example
# 模板是最常见的部署错误，宁可启动失败也不留一个可预测的管理员凭证。
_WEAK_ADMIN_PASSWORDS = frozenset({
    "change-me", "changeme", "password", "admin", "administrator",
    "123456", "12345678", "admin123", "admin@123", "1234567890",
})


def _ensure_dir() -> None:
    config.USERS_DIR.mkdir(parents=True, exist_ok=True)
    # 账号文件含 bcrypt 哈希与邮箱：目录收紧到 0700（best-effort，兼容
    # 不支持 chmod 的文件系统）。
    try:
        os.chmod(config.USERS_DIR, 0o700)
    except OSError:
        pass


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
    # 含密码哈希的文件不给同机其他账号读（best-effort chmod）。
    try:
        os.chmod(_ACCOUNTS_FILE, 0o600)
    except OSError:
        pass


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
    with file_lock(_ACCOUNTS_FILE):
        user = get_by_id(user_id)
        if user:
            user.last_login_at = time.time()
            update_user(user)


def bump_token_version(user_id: str) -> bool:
    """吊销该账号全部已签发 JWT：token_version+1 后旧 token 验证即失败。

    供改密码/管理员重置凭证等"凭证变更"场景调用；删号不需要（get_by_id
    落空已令 token 失效）。在账号文件锁内 load-modify-save。"""
    with file_lock(_ACCOUNTS_FILE):
        user = get_by_id(user_id)
        if user is None:
            return False
        user.token_version += 1
        update_user(user)
        return True


def account_record_lock():
    """Share the preference-update lock with the final question registration."""
    return file_lock(_ACCOUNTS_FILE)


def update_profile_fields(user_id: str, fields: dict[str, Any],
                          prefs: dict[str, Any] | None = None) -> User:
    """Merge into the latest account while holding the account-file lock."""
    with file_lock(_ACCOUNTS_FILE):
        user = get_by_id(user_id)
        if user is None:
            raise ValueError("account_not_found")
        for name, value in fields.items():
            if name in {"name", "grade", "school", "subjects", "avatar"}:
                setattr(user.profile, name, value)
        if prefs is not None:
            user.profile.prefs.update(prefs)
        update_user(user)
        return user


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

    账号不存在 → 创建 role=admin；已存在但非 admin → 仅当 ADMIN_PASSWORD
    能通过该账号的 bcrypt 哈希校验（证明是运营者本人的账号）才提权。
    注册是开放的：无条件提权会让抢注 ADMIN_EMAIL 的攻击者在下一次重启时
    静默拿到 admin。未配置 env 则 no-op。

    生产（AUTH_MODE=1）下弱默认密码/过短密码直接拒绝启动（fail-fast，
    与 ensure_secret_safety 同款守卫）；开发模式仅告警。其余路径的异常
    仍然只记日志不抛（启动路径不容失败）。
    """
    try:
        email = (os.getenv("ADMIN_EMAIL") or "").strip().lower()
        password = os.getenv("ADMIN_PASSWORD") or ""
        if not email or not password:
            return
        production = os.getenv("AUTH_MODE", "0") == "1"
        weak = (password.lower() in _WEAK_ADMIN_PASSWORDS
                or len(password) < 10)
        if weak:
            message = (
                f"ADMIN_PASSWORD 是弱默认值或短于 10 字符"
                f"（{email}）。生产环境拒绝以此引导管理员账号："
                "请设置强密码（≥10 字符、非常见默认值）后重启。")
            if production:
                raise RuntimeError(message)
            log.warning("%s 当前 AUTH_MODE=0，仅告警继续。", message)
        from .security import hash_password, verify_password
        existing = get_by_email(email)
        if existing is None:
            create_user(email=email, username="管理员",
                        password_hash=hash_password(password), role="admin")
        elif existing.role != "admin":
            if verify_password(password, existing.password_hash):
                existing.role = "admin"
                update_user(existing)
            else:
                log.warning(
                    "ADMIN_EMAIL=%s 已被既有非管理员账号占用，且 ADMIN_PASSWORD"
                    " 校验失败：已跳过自动提权（防抢注）。如确为本人账号，请先把"
                    " .env 的 ADMIN_PASSWORD 改成该账号的正确密码再重启。",
                    email)
    except RuntimeError:
        raise
    except Exception:
        log.exception("admin bootstrap failed")
