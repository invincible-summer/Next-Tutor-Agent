"""ScopeResolver：工作区 → 卷级授权范围 + revision（plan §5）。

流程严格按 §5.1：归属 404 先于任何检索/LLM；selected_file_ids 经
`resolve_textbook_file` 同等授权；只认注册教材；卷闭包抽节点；仅
kind=concept 可评价；跨教材同名不合并。评价核心不 import API 层——
workspace/textbook/graph 读取经 `ports.py` 协议注入（默认适配在
`core/learner_runtime.py`）。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from . import schema as S
from .ports import (GraphReader, TextbookReader, WorkspaceReader)
from app.agents.knowledge import scope_primitives as P


class ScopeNotFound(LookupError):
    """workspace 不存在 / 非本人：API 层统一 404（§5.1.1）。"""


@dataclass
class _CacheEntry:
    stamp_key: tuple
    scope: S.EvaluationScope


class ScopeResolver:
    def __init__(self, workspaces: WorkspaceReader, textbooks: TextbookReader,
                 graphs: GraphReader) -> None:
        self._workspaces = workspaces
        self._textbooks = textbooks
        self._graphs = graphs
        self._cache: dict[tuple[str, str], _CacheEntry] = {}
        self._lock = threading.RLock()

    def invalidate(self, student_id: str = "", workspace_id: str = "") -> None:
        with self._lock:
            if not student_id and not workspace_id:
                self._cache.clear()
                return
            for key in list(self._cache):
                if (not student_id or key[0] == student_id) and \
                        (not workspace_id or key[1] == workspace_id):
                    del self._cache[key]

    def resolve(self, student_id: str, workspace_id: str) -> S.EvaluationScope:
        """解析可信 scope。任何资源不存在/越权抛 ScopeNotFound → API 404。"""
        ws = self._workspaces.load(workspace_id)
        if ws is None or (ws.get("student_id") or "") != student_id:
            raise ScopeNotFound(f"workspace {workspace_id} not found")

        stamp_key = (tuple(ws.get("selected_file_ids") or []),
                     self._workspaces.stamp(student_id),
                     self._graphs.stamp(student_id),
                     self._graphs.stamp("public"))
        with self._lock:
            cached = self._cache.get((student_id, workspace_id))
            if cached is not None and cached.stamp_key == stamp_key:
                return cached.scope

        scope = self._resolve_uncached(student_id, ws)
        with self._lock:
            self._cache[(student_id, workspace_id)] = _CacheEntry(
                stamp_key=stamp_key, scope=scope)
        return scope

    # ------------------------------------------------------------------
    def _resolve_uncached(self, student_id: str,
                          ws: dict) -> S.EvaluationScope:
        selected_files: list[str] = [str(f) for f in
                                     (ws.get("selected_file_ids") or []) if f]
        # file_id -> (owner namespace, textbook record)
        resolved: dict[str, tuple[str, dict]] = {}
        for fid in selected_files:
            hit = self._textbooks.resolve_textbook_file(student_id, fid)
            if hit is None:
                continue  # 非教材文件不进评价范围（§5.1.2/7）
            record, owner = hit
            if record.get("status") not in (None, "", "ready"):
                continue
            resolved[fid] = (owner, record)

        # 教材分组：同一 record 的多个已选卷合成一个 VolumeSelection
        by_textbook: dict[tuple[str, str], dict] = {}
        for fid, (owner, record) in resolved.items():
            key = (owner, str(record.get("id") or ""))
            entry = by_textbook.setdefault(key, {
                "owner": owner, "record": record, "file_ids": []})
            entry["file_ids"].append(fid)

        volumes: list[S.VolumeSelection] = []
        allowed: list[S.ConceptRef] = []
        graph_revisions: list[S.GraphRevisionInfo] = []
        unresolved = 0
        for (owner, _tb), entry in sorted(by_textbook.items()):
            record = entry["record"]
            file_ids = list(dict.fromkeys(entry["file_ids"]))
            topic_key = str(record.get("topic_key") or "")
            payload = self._graphs.load(owner, topic_key) if topic_key else None
            graph_rev = (P.graph_content_revision(payload)
                         if payload else "gr_missing")
            graph_revisions.append(S.GraphRevisionInfo(
                graph_owner_namespace=owner,
                textbook_id=str(record.get("id") or ""),
                graph_revision=graph_rev))
            volumes.append(S.VolumeSelection(
                textbook_id=str(record.get("id") or ""),
                graph_owner_namespace=owner,
                topic_key=topic_key,
                file_ids=file_ids,
                graph_revision=graph_rev))
            if payload is None:
                unresolved += 1
                continue
            nodes, _edges = P.volume_scoped_subgraph(
                payload.get("nodes") or [], payload.get("edges") or [],
                set(file_ids))
            tb_id = str(record.get("id") or "")
            for n in nodes:
                if str(n.get("kind") or "") != "concept":
                    continue  # chapter/section 仅导航（§5.1.5）
                allowed.append(S.ConceptRef(
                    graph_owner_namespace=owner,
                    textbook_id=tb_id,
                    file_ids=file_ids,
                    concept_id=str(n.get("id") or ""),
                    concept_revision=P.concept_revision(tb_id, n),
                    display_name=str(n.get("name") or "")))

        scope_rev = P.scope_revision_for([
            {"owner": v.graph_owner_namespace, "textbook": v.textbook_id,
             "file_ids": sorted(v.file_ids), "graph_revision": v.graph_revision}
            for v in volumes])
        return S.EvaluationScope(
            workspace_id=str(ws.get("workspace_id") or ""),
            scope_revision=scope_rev,
            selected_volumes=volumes,
            allowed_concepts=allowed,
            graph_revisions=graph_revisions,
            unresolved_graph_count=unresolved,
        )


_resolver: ScopeResolver | None = None


def set_scope_resolver(resolver: ScopeResolver | None) -> None:
    global _resolver
    _resolver = resolver


def get_scope_resolver() -> ScopeResolver:
    global _resolver
    if _resolver is None:
        from app.core import learner_runtime
        _resolver = learner_runtime.build_default_scope_resolver()
    return _resolver
