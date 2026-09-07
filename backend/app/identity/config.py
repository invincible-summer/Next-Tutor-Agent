"""Auth configuration read from environment / .env.

Mirrors core/config.py: single source of truth for auth settings. The JWT
secret is NEVER returned by any API endpoint -- it is used only internally.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_PROJECT_ROOT / ".env")

_DEFAULT_SECRET = "edu-agent-dev-secret-change-me-in-production-please"

AUTH_MODE = int(os.getenv("AUTH_MODE", "0"))  # 0=guest, 1=login required
AUTH_JWT_SECRET = os.getenv("AUTH_JWT_SECRET", _DEFAULT_SECRET)
AUTH_JWT_ALGORITHM = os.getenv("AUTH_JWT_ALGORITHM", "HS256")
AUTH_TOKEN_EXPIRE_DAYS = int(os.getenv("AUTH_TOKEN_EXPIRE_DAYS", "30"))
AUTH_BCRYPT_ROUNDS = int(os.getenv("AUTH_BCRYPT_ROUNDS", "12"))

USERS_DIR = _PROJECT_ROOT / "users"


def users_dir() -> Path:
    USERS_DIR.mkdir(parents=True, exist_ok=True)
    return USERS_DIR


def using_default_secret() -> bool:
    return AUTH_JWT_SECRET == _DEFAULT_SECRET


def ensure_secret_safety() -> None:
    """Startup guard: refuse to run with the dev default JWT secret when
    login is enforced (AUTH_MODE=1). In guest mode (AUTH_MODE=0) the default
    secret only earns a warning so local development keeps working.

    Called from the app factory. Reads AUTH_MODE live from the environment
    (same semantics as identity.is_auth_required), NOT the import-time
    module constant, so tests can toggle it per case.
    """
    if not using_default_secret():
        return
    if os.getenv("AUTH_MODE", "0") == "1":
        raise RuntimeError(
            "AUTH_JWT_SECRET 未配置（仍是开发默认值），AUTH_MODE=1 下拒绝启动。"
            "请生成随机密钥写入 .env 后重启：\n"
            "  python -c \"import secrets; "
            "print('AUTH_JWT_SECRET=' + secrets.token_urlsafe(48))\""
        )
    log.warning("AUTH_JWT_SECRET 仍为开发默认值，仅限本地开发使用；"
                "部署到公网前必须设置独立随机密钥。")
