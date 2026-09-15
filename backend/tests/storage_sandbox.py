"""测试存储沙箱基类：把全部存储根常量重定向进 TemporaryDirectory。

历史教训：各测试自行 patch 存储常量子集，漏掉的模块（prompt_memory、会话/
转写目录）直接把合成 ID 写进生产 students/、chat_history/、backend/traces/
（数千孤儿文件的来源）。任何要落盘的测试一律继承 StorageSandboxTestCase，
不要自己拼 patch 清单。

新增每用户存储根时：①在此登记 patch；②在 app/core/orphan_cleanup.py 的
_collect_orphans 登记扫描类别（AGENTS.md 测试规范）。
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


def patch_all_storage_roots(root: Path) -> list:
    """把全部存储根常量 patch 到 <root>/ 标准布局并启动，返回 patch 列表。

    供自带 fixture 的测试文件复用（StorageSandboxTestCase 内部也用它）。
    调用方负责在 tearDown 里逆序 stop。同时重置 vector_store 与
    StudentModel 缓存——它们持有旧路径，不重置会把沙箱写穿到生产目录。
    """
    from app.agents.evaluation import store as eval_store
    from app.agents.knowledge import store as graph_store
    from app.agents.learning_orchestration import store as orch_store
    from app.agents.memory import prompt_memory
    from app.agents.memory import store as memory_store
    from app.agents.student_model import manager as sm_manager
    from app.agents.student_model import store as sm_store
    from app.agents.student_model.evaluation import store as eval_journal_store
    from app.agents.teaching_engine import guidance_store, teaching_log
    from app.agents.ux_intelligence import store as ux_store
    from app.core import context, learning_episodes, library, notes
    from app.core import learner_evaluation_policy
    from app.core import session, textbook, trash, usage_docs, workspace
    from app.core import vector_store
    from app.core.config import settings
    patches = [
        patch.object(trash, "_TRASH_DIR", root / "chat_history" / "trash"),
        # 阶段C：策略文件是全局设置（不入账号清理），但测试必须落沙箱
        patch.object(learner_evaluation_policy, "POLICY_FILE",
                     root / "chat_history" / "settings" /
                     "learner_evaluation_policy.json"),
        _PolicyCacheReset(learner_evaluation_policy),
        patch.object(trash, "_GLOBAL_POLICY",
                     root / "chat_history" / "trash" / "policy.json"),
        patch.object(session, "_SESSIONS_DIR", root / "chat_history"),
        patch.object(context, "_TRANSCRIPT_DIR", root / "chat_history"),
        patch.object(library, "_LIBRARY_DIR", root / "chat_history" / "library"),
        patch.object(textbook, "_LIBRARY_DIR", root / "chat_history" / "library"),
        patch.object(workspace, "_WORKSPACES_DIR", root / "chat_history" / "workspaces"),
        patch.object(graph_store, "_KG_DIR", root / "knowledge"),
        # _KG_FILE 是导入期冻结常量（store 全程直接引用它），只 patch
        # _KG_DIR 重定向不了 learned-edge 读写——漏掉它会写穿到生产
        # knowledge/graph.json。
        patch.object(graph_store, "_KG_FILE", root / "knowledge" / "graph.json"),
        patch.object(graph_store, "_CUSTOM_DIR", root / "knowledge" / "custom"),
        patch.object(prompt_memory, "_STUDENTS_DIR", root / "students"),
        patch.object(prompt_memory, "_POLICY_PATH",
                     root / "students" / "prompt_memory_policy.json"),
        patch.object(memory_store, "_STUDENTS_DIR", root / "students"),
        patch.object(learning_episodes, "_STUDENTS_DIR", root / "students"),
        patch.object(sm_store, "_STUDENTS_DIR", root / "students"),
        patch.object(teaching_log, "_STUDENTS_DIR", root / "students"),
        patch.object(guidance_store, "_STUDENTS_DIR", root / "students"),
        patch.object(eval_store, "_STUDENTS_DIR", root / "students"),
        patch.object(eval_journal_store, "STUDENTS_DIR", root / "students"),
        patch.object(orch_store, "_STUDENTS_DIR", root / "students"),
        patch.object(ux_store, "_STUDENTS_DIR", root / "students"),
        patch.object(notes, "_NOTES_DIR", root / "notes"),
        patch.object(usage_docs, "_DOCS_FILE",
                     root / "chat_history" / "settings" / "usage_docs.json"),
        patch.object(settings, "trace_dir", str(root / "traces")),
        patch.object(settings, "chroma_dir", str(root / "vector_db")),
    ]
    for p in patches:
        p.start()
    vector_store._reset()
    sm_manager._CACHE.clear()
    return patches


def reset_shared_caches() -> None:
    """tearDown 用：清掉可能指向已删除临时目录的进程级缓存。"""
    from app.agents.student_model import manager as sm_manager
    from app.agents.student_model.evaluation import store as eval_journal_store
    from app.core import vector_store
    from app.core import learner_runtime
    sm_manager._CACHE.clear()
    vector_store._reset()
    eval_journal_store.reset_journal_cache()
    learner_runtime.reset_learner_runtime()


class StorageSandboxTestCase(unittest.TestCase):
    """全部存储根 + AUTH_MODE=1 + Chroma 重定向。tearDown 自动回收临时目录。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="edu_sandbox_")
        root = Path(self._tmp.name)
        self.root = root
        for sub in ("users", "students", "chat_history", "chat_history/library",
                    "chat_history/library/data", "chat_history/trash",
                    "chat_history/trash/items", "chat_history/workspaces",
                    "notes", "knowledge", "knowledge/custom", "traces",
                    "uploads"):
            (root / sub).mkdir(parents=True, exist_ok=True)

        from app.identity import config as id_config
        from app.identity import store as id_store

        self._env_old = os.environ.get("AUTH_MODE")
        os.environ["AUTH_MODE"] = "1"
        self._patches = patch_all_storage_roots(root)
        id_patches = [
            patch.object(id_config, "AUTH_JWT_SECRET", "sandbox-test-secret"),
            patch.object(id_store, "_ACCOUNTS_FILE", root / "users" / "accounts.json"),
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
            os.environ[self._env_old] = self._env_old
        reset_shared_caches()
        self._tmp.cleanup()
