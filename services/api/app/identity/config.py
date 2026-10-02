"""Auth configuration read from environment / .env.

Mirrors core/config.py: single source of truth for auth settings. The JWT
secret is NEVER returned by any API endpoint -- it is used only internally.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger(__name__)

from app.core import paths
# Test runs force the keyless CI environment (tests/__init__.py sets the
# flag and scrubs the variables); never load real credentials there.
# `unittest in sys.modules` 与 core/config.py 同理：覆盖 discover 导入顺序
# 下 app 先于 tests 包清洗被导入的场景。
if os.environ.get("EDU_TEST_KEYLESS") != "1" and "unittest" not in sys.modules:
    load_dotenv(paths.repo_root() / ".env")

_DEFAULT_SECRET = "edu-agent-dev-secret-change-me-in-production-please"
# 生成文件所在目录（0700）：与 pid/日志同级的运行时状态，天然不入库。
_GENERATED_SECRET_FILE = paths.bind_storage_path(
    __name__, "_GENERATED_SECRET_FILE", "auth_secret")
# 测试运行在 keyless CI 环境（tests/__init__.py 置位并清洗变量）：跳过生成，
# 保持确定性默认值，避免并行测试进程触碰（甚至竞争写）生成文件。
_IN_TEST_RUNNER = (os.environ.get("EDU_TEST_KEYLESS") == "1"
                   or "unittest" in sys.modules)


def _load_or_generate_secret() -> str:
    """未显式配置 AUTH_JWT_SECRET 时，为本安装生成并持久化随机开发密钥。

    旧行为（直接用上面的公共默认值签名）等于把签名密钥公开在整个仓库里：
    任何读过源码的人都能对已知 user_id（演示账号 id 在仓库内公开）伪造
    30 天有效期的 token。.runtime 不可写属于部署异常（只读文件系统/
    错误属主），此时回退公共默认值等于把签名密钥交给所有读过仓库的人：
    fail-closed，直接启动失败并给出修复指引，而不是降级继续跑。
    """
    try:
        if _GENERATED_SECRET_FILE.is_file():
            value = _GENERATED_SECRET_FILE.read_text(encoding="utf-8").strip()
            if value:
                return value
        import secrets
        value = secrets.token_urlsafe(48)
        _GENERATED_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        # 原子替换（临时文件 + os.replace）：并发进程同时首启时不会读到半写值。
        tmp = _GENERATED_SECRET_FILE.with_name(
            f"auth_jwt_secret.tmp.{os.getpid()}")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(value)
        os.replace(tmp, _GENERATED_SECRET_FILE)
        return value
    except OSError as exc:
        raise RuntimeError(
            f"无法生成/读取本机 JWT 密钥文件（{exc}）。"
            f"请修复 {_GENERATED_SECRET_FILE.parent} 的可写权限，"
            "或在 .env 显式配置 AUTH_JWT_SECRET 后重启。") from exc


AUTH_MODE = int(os.getenv("AUTH_MODE", "0"))  # deployment secret guard; guest policy is separate
AUTH_JWT_SECRET = (os.getenv("AUTH_JWT_SECRET")
                   or (_DEFAULT_SECRET if _IN_TEST_RUNNER
                       else _load_or_generate_secret()))
AUTH_JWT_ALGORITHM = os.getenv("AUTH_JWT_ALGORITHM", "HS256")
AUTH_TOKEN_EXPIRE_DAYS = int(os.getenv("AUTH_TOKEN_EXPIRE_DAYS", "30"))
AUTH_BCRYPT_ROUNDS = int(os.getenv("AUTH_BCRYPT_ROUNDS", "12"))

USERS_DIR = paths.bind_storage_path(__name__, "USERS_DIR", "users")


def users_dir() -> Path:
    USERS_DIR.mkdir(parents=True, exist_ok=True)
    return USERS_DIR


def using_default_secret() -> bool:
    return AUTH_JWT_SECRET == _DEFAULT_SECRET


def ensure_secret_safety() -> None:
    """Startup guard: refuse to run with the dev default JWT secret when
    production mode is enabled (AUTH_MODE=1). In development (AUTH_MODE=0) the default
    secret only earns a warning so local development keeps working.

    Called from the app factory. Reads AUTH_MODE live from the environment
    (independent of the administrator guest policy), NOT the import-time
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
    log.warning("JWT 密钥回退到公共默认值（.runtime 不可写，无法生成本机随机"
                "开发密钥）。任何读过仓库的人此刻都能伪造 token；请排查 "
                ".runtime 权限或在 .env 显式配置 AUTH_JWT_SECRET。")
