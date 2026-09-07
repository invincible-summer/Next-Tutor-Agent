"""Sidebar composite endpoint: sessions + workspaces + per-workspace details.

P2 性能：前端侧边栏原为 listSessions → listWorkspaces → N×getWorkspace
三级瀑布，首屏渲染被最慢一环卡住、请求数随工作区数线性增长。本端点服务
端一次拼装同一快照（读取路径复用 /workspaces 与 /workspaces/{id} 的实现），
响应带弱 ETag——数据未变时 304，浏览器缓存直接复用，省序列化与传输。

旧端点全部保留（前端其余调用点与外部脚本不受影响）。
"""
from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, Header, Response
from fastapi.responses import JSONResponse

from app.agents.student_model.store import DEFAULT_STUDENT_ID
from app.core.session import list_sessions
from app.core.workspace import ensure_library_folder, load_workspace
from app.identity.deps import resolve_student_id

from app.api.v1.workspace import _visible_summaries, _ws_detail

router = APIRouter(prefix="/sidebar", tags=["sidebar"])


def _etag_of(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return 'W/"' + hashlib.md5(raw.encode("utf-8")).hexdigest() + '"'


@router.get("")
def sidebar_snapshot(student_id: str = Depends(resolve_student_id),
                     if_none_match: str | None = Header(default=None)):
    """Atomic sidebar snapshot: {sessions, workspaces, details} + ETag."""
    sessions = [s for s in list_sessions()
                if (s.get("student_id") or DEFAULT_STUDENT_ID) == student_id]
    workspaces = _visible_summaries(student_id)
    details: dict[str, dict] = {}
    for w in workspaces:
        try:
            ws = load_workspace(w["workspace_id"])
            if ws is None:
                continue
            ensure_library_folder(ws)  # 与 GET /workspaces/{id} 行为一致（缺失时补专属夹）
            details[w["workspace_id"]] = _ws_detail(ws)
        except Exception:
            continue  # 单个工作区详情失败不拖垮整个快照（前端按缺失处理）
    payload = {"sessions": sessions, "workspaces": workspaces, "details": details}
    etag = _etag_of(payload)
    if if_none_match is not None and etag in if_none_match:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse(payload, headers={"ETag": etag})
