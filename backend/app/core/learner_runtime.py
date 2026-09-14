"""评价运行时 composition root（plan §6.2 / §15.1）。

把 workspace/textbook/M5 图谱的只读适配器注入评价核心，避免评价包 import
具体 IO 模块形成循环（评价→M5→评价）。进程级单例 + 沙箱可重置。
"""
from __future__ import annotations

import threading
from typing import Any

from app.agents.knowledge import store as kg_store
from app.agents.student_model.evaluation import projections
from app.agents.student_model.evaluation.jobs import JobScheduler
from app.agents.student_model.evaluation.scope import ScopeResolver, set_scope_resolver
from app.agents.student_model.evaluation.store import reset_journal_cache
from app.core import workspace as ws_core
from app.core import textbook as tb_core
from app.core.config import settings
from app.core.atomic import file_lock


class _WorkspaceAdapter:
    def load(self, workspace_id: str) -> dict[str, Any] | None:
        ws = ws_core.load_workspace(workspace_id)
        if ws is None:
            return None
        data = ws.to_dict() if hasattr(ws, "to_dict") else dict(ws.__dict__)
        data.setdefault("workspace_id", workspace_id)
        return data

    def stamp(self, student_id: str) -> tuple:
        try:
            items = ws_core.list_workspaces()
        except Exception:
            items = []
        own = [w for w in items if w.get("student_id") == student_id]
        return tuple(sorted(
            (str(w.get("workspace_id") or ""), str(w.get("updated_at") or ""))
            for w in own))


class _TextbookAdapter:
    def resolve_textbook_file(
            self, student_id: str,
            file_id: str) -> tuple[dict[str, Any], str] | None:
        meta, owner = ws_core.resolve_textbook_file(student_id, file_id)
        if meta is None or not owner:
            return None
        record = tb_core.textbook_for_file(owner, file_id)
        if record is None:
            return None
        return record, owner


class _GraphAdapter:
    def load(self, owner_namespace: str,
             topic_key: str) -> dict[str, Any] | None:
        return kg_store.load_custom_graph(owner_namespace, topic_key)

    def stamp(self, owner_namespace: str) -> tuple:
        return kg_store.list_custom_stamp(owner_namespace)


_LOCK = threading.RLock()
_SCHEDULER: JobScheduler | None = None
_RUNNER: Any | None = None
_WORKSPACE_LOCKS: dict[tuple[str, str], Any] = {}


def workspace_lock(student_id: str, workspace_id: str):
    """R05：每 (user, workspace) 一把 asyncio 锁——同区评价提交串行，
    不同区/不同用户并行（§6.5）。journal 文件锁只保证单事务原子性，
    不保证"pack 构建 → 模型 → 提交"整段的基线一致性。"""
    import asyncio
    key = (student_id, workspace_id or "-")
    with _LOCK:
        lock = _WORKSPACE_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _WORKSPACE_LOCKS[key] = lock
        return lock


def build_default_scope_resolver() -> ScopeResolver:
    return ScopeResolver(_WorkspaceAdapter(), _TextbookAdapter(),
                         _GraphAdapter())


def get_scheduler() -> JobScheduler:
    global _SCHEDULER
    with _LOCK:
        if _SCHEDULER is None:
            _SCHEDULER = JobScheduler(
                lease_seconds=settings.learner_eval_lease_seconds)
        return _SCHEDULER


def get_evaluation_runner():
    """EvaluationLLMRunner 进程级单例（客户端在应用 shutdown 关闭）。"""
    global _RUNNER
    with _LOCK:
        if _RUNNER is None:
            from app.agents.student_model.evaluation.llm import (
                EvaluationLLMRunner)
            _RUNNER = EvaluationLLMRunner(
                concurrency=settings.learner_evaluation_concurrency)
        return _RUNNER


def set_evaluation_runner(runner: Any | None) -> None:
    """测试注入 fake runner（沙箱重置时同样清空）。"""
    global _RUNNER
    with _LOCK:
        _RUNNER = runner


def evaluation_enabled() -> bool:
    return settings.learner_evaluation_mode == "active"


def reset_learner_runtime() -> None:
    """沙箱/登出场景：清进程级缓存（journal/scope/scheduler/runner 重建）。"""
    global _SCHEDULER, _RUNNER
    with _LOCK:
        _SCHEDULER = None
        _RUNNER = None
        _WORKSPACE_LOCKS.clear()
    reset_journal_cache()
    set_scope_resolver(None)
