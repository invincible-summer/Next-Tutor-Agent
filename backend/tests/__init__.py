"""Test package bootstrap: local runs default to the keyless CI environment.

Cloud CI checkouts carry no root ``.env``, so the suite must never depend on
real credentials. Before any app module is imported, drop every variable a
developer's ``.env`` (or shell) provides on the LLM/credential plane and stop
``app.core.config`` / ``app.identity.config`` from re-loading ``.env``. A
hermeticity leak then fails loudly on the local run first, instead of
surfacing as a multi-minute CI hang (see the voice busy-rejection incident:
a real ``get_llm()`` raised "Missing credentials", the turn died, and the
test blocked forever waiting for a ``busy`` event that could never come).
"""
from __future__ import annotations

import os
from pathlib import Path

_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"

# Real-credential / network-endpoint variables: scrubbed wherever they came
# from (shell export or .env) so tests can never reach a paid API by accident.
_ALWAYS_POP = {
    "OPENAI_API_KEY", "OPENAI_ADMIN_KEY", "OPENAI_BASE_URL",
    "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
    # 课堂外部服务凭证（阶段 C/F）：azure 语音、联网检索、图库
    "AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION", "AZURE_SPEECH_ENDPOINT",
    "TAVILY_API_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY",
}


def _force_keyless() -> None:
    keys = set(_ALWAYS_POP)
    if _ROOT_ENV.exists():
        # Full CI parity: cloud runners don't have the file at all, so every
        # key it defines (feature toggles included) must fall back to code
        # defaults during tests.
        for line in _ROOT_ENV.read_text(encoding="utf-8").splitlines():
            name = line.split("=", 1)[0].strip()
            if name.startswith("export "):
                name = name[len("export "):].strip()
            if name and not name.startswith("#"):
                keys.add(name)
    for key in keys:
        os.environ.pop(key, None)
    # config modules skip load_dotenv() under this flag.
    os.environ["EDU_TEST_KEYLESS"] = "1"


_force_keyless()

# LLM 运行参数策略文件默认指向仓库内 chat_history/settings/llm_policy.json：
# 本机管理页写过的值会泄漏进测试默认值（CI 无此文件，本地与 CI 行为分叉）。
# 重定向进测试进程生命周期的临时目录（TemporaryDirectory 析构时清理），
# 与 storage sandbox 的 patch 互不干扰（sandbox 在测试内再改写并还原）。
import tempfile as _tempfile

_POLICY_DIR = _tempfile.TemporaryDirectory(prefix="edu_test_llm_policy_")
try:
    from app.core import llm_policy as _llm_policy
    _llm_policy.POLICY_FILE = Path(_POLICY_DIR.name) / "llm_policy.json"
    _llm_policy.reset_policy_cache()
except Exception:  # pragma: no cover - app 导入失败由具体测试兜底报错
    pass
