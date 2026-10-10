"""测试存储沙箱基类：把全部存储根重定向进 TemporaryDirectory。

历史教训：各测试自行 patch 存储常量子集，漏掉的模块（prompt_memory、会话/
转写目录）直接把合成 ID 写进生产 students/、chat_history/、traces/
（数千孤儿文件的来源）。任何要落盘的测试一律继承 StorageSandboxTestCase，
不要自己拼 patch 清单。

路径所有权现在集中在 app/core/paths.py：所有存储模块通过
``paths.bind_storage_path`` 注册其模块级路径常量，本沙箱只需
``paths.set_runtime_root(tmp)`` 一次性重定向全部注册绑定。新增每用户存储
根时：①在归属模块用 ``bind_storage_path`` 绑定；②在
app/core/orphan_cleanup.py 的 _collect_orphans 登记扫描类别（AGENTS.md
测试规范）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


class _PolicyCacheReset:
    """策略缓存随沙箱生命周期重置（start/stop 与 patcher 同接口）。"""

    def __init__(self, module) -> None:
        self._module = module

    def start(self) -> None:
        self._module.reset_policy_cache()

    def stop(self) -> None:
        self._module.reset_policy_cache()


class _RuntimeRootOverride:
    """把 runtime 数据根重定向到指定目录（stop 恢复原值）。"""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._prev: Path | None = None

    def start(self) -> None:
        from app.core import paths
        self._prev = paths.current_override_root()
        paths.set_runtime_root(self._root)

    def stop(self) -> None:
        from app.core import paths
        paths.set_runtime_root(self._prev)


def patch_all_storage_roots(root: Path) -> list:
    """把全部存储根重定向到 <root>/ 标准布局并启动，返回 patch 列表。

    供自带 fixture 的测试文件复用（StorageSandboxTestCase 内部也用它）。
    调用方负责在 tearDown 里逆序 stop。同时重置 vector_store 与
    StudentModel 缓存——它们持有旧路径，不重置会把沙箱写穿到生产目录。
    """
    from app.agents.student_model import manager as sm_manager
    from app.core import vector_store
    from app.core import learner_evaluation_policy, llm_policy
    patches = [
        _RuntimeRootOverride(root),
        # 阶段C：策略文件是全局设置（不入账号清理），但测试必须落沙箱
        _PolicyCacheReset(learner_evaluation_policy),
        # LLM 运行参数（「运行参数」面板）：懒加载缓存随沙箱重置。
        _PolicyCacheReset(llm_policy),
    ]
    for p in patches:
        p.start()
    vector_store._reset()
    sm_manager._CACHE.clear()
    return patches


def reset_shared_caches() -> None:
    """tearDown 用：清掉可能指向已删除临时目录的进程级缓存。"""
    from app.core.guest_runtime import purge_all
    purge_all()
    from app.agents.student_model import manager as sm_manager
    from app.agents.student_model.evaluation import store as eval_journal_store
    from app.core import vector_store
    from app.core import learner_runtime
    sm_manager._CACHE.clear()
    vector_store._reset()
    eval_journal_store.reset_journal_cache()
    learner_runtime.reset_learner_runtime()
    # 站内检索（§20.4）按 student_id+query 缓存结果 30s：跨沙箱会命中
    # 上一用例的实体数据，同键用例必须重置。
    from app.agents.site_assistant import search as site_search
    site_search._cache.clear()
    # 课堂 TTS（阶段 F）：电话 provider 缓存、共享 semaphore、voices 缓存
    # 与课堂音频引擎都持有进程级状态，跨沙箱必须重置。
    from app.voice.tts import service as tts_service
    tts_service.reset_tts_service()
    try:
        from app.classroom import audio as classroom_audio
        classroom_audio.reset_audio_engine()
    except ImportError:
        pass
    # Temporal lane（ADR-0013）：进程级 client 缓存可能指向上一用例的
    # test server / 地址，跨用例必须丢弃。
    try:
        from app.workflows import runtime as workflow_runtime
        workflow_runtime.reset_client_cache()
    except ImportError:
        pass


class StorageSandboxTestCase(unittest.TestCase):
    """全部存储根 + AUTH_MODE=1 + Chroma 重定向。tearDown 自动回收临时目录。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="edu_sandbox_")
        root = Path(self._tmp.name)
        self.root = root
        for sub in ("users", "students", "chat_history", "chat_history/library",
                    "chat_history/library/data", "chat_history/trash",
                    "chat_history/trash/items", "chat_history/workspaces",
                    "chat_history/classroom", "notes", "knowledge",
                    "knowledge/custom", "traces", "uploads", "illustrations", "diagram_assets"):
            (root / sub).mkdir(parents=True, exist_ok=True)

        from app.identity import config as id_config

        self._env_old = os.environ.get("AUTH_MODE")
        os.environ["AUTH_MODE"] = "1"
        self._patches = patch_all_storage_roots(root)
        id_patches = [
            patch.object(id_config, "AUTH_JWT_SECRET", "sandbox-test-secret-for-isolated-tests-only"),
        ]
        for p in id_patches:
            p.start()
        self._patches += id_patches

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        if self._env_old is None:
            os.environ.pop("AUTH_MODE", None)
        else:
            os.environ["AUTH_MODE"] = self._env_old
        reset_shared_caches()
        self._tmp.cleanup()


def authenticated_client(app, owner: str):
    """Authenticated route fixture; call only inside an active storage sandbox."""
    from fastapi.testclient import TestClient
    from app.identity.store import get_by_id, create_user
    from app.identity.security import create_token
    if get_by_id(owner) is None:
        create_user(owner + "@test.local", "", "unused", user_id=owner)
    return TestClient(app, headers={"Authorization": "Bearer " + create_token(owner)})
