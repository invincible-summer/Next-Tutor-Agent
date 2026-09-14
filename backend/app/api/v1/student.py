"""Student Model & Teaching Engine projection API (M2/M3 observability).

Read-only projections for the frontend: the long-term learner profile
(身份/学段/偏好), the cross-turn teaching log, the advisory learning path
(G4：next/review 由 M5 图谱 × 统一评价投影推导，无数值掌握), and the
journal-backed error notebook. Mirrors the /assessment endpoints'
graceful-degradation contract.

All endpoints are READ-ONLY: they call only load/get/view primitives, never
record_*/persist. Every handler degrades to a clear status
(ok | disabled | empty | error) and never raises into a request. Uses the
DEFAULT_STUDENT_ID (single-student system, same as M2-M8).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.agents import student_model as _sm
from app.agents import teaching_engine as _te
from app.agents.student_model import store as _sm_store
from app.agents.student_model.store import DEFAULT_STUDENT_ID
from app.identity.deps import optional_user, resolve_student_id
from app.identity.models import User

router = APIRouter(prefix="/student", tags=["student"])


def _evaluation_view(student_id: str) -> dict[str, Any]:
    """G4：统一评价只读投影 {concept_id: {"state": ...}}（全工作区当前
    判断聚合；多区同概念保留最不利状态）。失败返回 {}。"""
    try:
        from app.agents.student_model.evaluation.store import get_journal
        state = get_journal(student_id).state()
        out: dict[str, Any] = {}
        for (_ws, _key), jid in state.concept_current.items():
            j = state.judgments.get(jid)
            if j is None:
                continue
            st = j.state.value if hasattr(j.state, "value") else str(j.state)
            cid = j.concept_ref.concept_id
            prev = out.get(cid, {}).get("state", "")
            if prev != "supported_in_scope":
                out[cid] = {"state": st}
        return out
    except Exception:
        return {}


def _graph_for(student_id: str):
    try:
        from app.agents.knowledge.manager import get_knowledge_service
        return get_knowledge_service().graph_for(student_id)
    except Exception:
        return None


def _next_learnable(g, view: dict[str, Any], student_id: str, *,
                    limit: int = 6) -> list[dict[str, Any]]:
    """G4：M5 图谱 frontier × 统一评价——前置均支持且自身未支持的概念
    （学段科目优先）。图缺失返回 []。"""
    if g is None:
        return []
    try:
        from app.agents.knowledge.schema import EdgeType
        prereq: dict[str, list[str]] = {}
        for e in g.edges:
            if e.type == EdgeType.PREREQUISITE:
                prereq.setdefault(e.target, []).append(e.source)

        def _supported(cid: str) -> bool:
            rec = view.get(cid) or {}
            return isinstance(rec, dict) and \
                str(rec.get("state", "")) == "supported_in_scope"

        subjects: set[str] = set()
        try:
            subjects = {s for s in
                        _sm.get_student_model(student_id).profile.subjects
                        if s}
        except Exception:
            pass
        out: list[dict[str, Any]] = []
        for nid, node in g.nodes.items():
            if getattr(node, "kind", "concept") != "concept":
                continue
            if subjects and node.subject not in subjects:
                continue
            if _supported(nid):
                continue
            if any(not _supported(p) for p in prereq.get(nid, [])):
                continue
            out.append({"name": node.name, "skill_id": nid,
                        "difficulty": int(getattr(node, "difficulty", 3) or 3),
                        "reason": "前置已具备，尚未观察到你在这方面的表现"})
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


def _current_difficulty(head_cid: str, student_id: str) -> int:
    """G4：1..5 难度旋钮只受近期已评估作答影响（中性起点，plan §13）。"""
    try:
        from app.agents.teaching_engine.difficulty import compute_difficulty
        recent = _te.load_teaching_log(student_id).get(head_cid) or []
        return compute_difficulty(0.5, recent)
    except Exception:
        return 2


@router.get("/profile")
def student_profile(student_id: str = Depends(resolve_student_id)) -> dict:
    """The long-term learner profile (M2 StudentProfile): grade, subjects,
    learning style, goals, weak/strong points, activity timestamps. This is
    the data behind the frontend "学生画像" header."""
    if not _sm.is_enabled():
        return {"status": "disabled"}
    try:
        if not _sm_store._resolve(student_id).exists():
            return {"status": "empty", "profile": None}
        sm = _sm.get_student_model(student_id)
        return {"status": "ok", "profile": sm.profile.to_dict()}
    except Exception as e:
        return {"status": "error", "message": str(e)}






@router.get("/teaching-log")
def student_teaching_log(
    student_id: str = Depends(resolve_student_id),
    limit_per_concept: int = Query(default=5, ge=1, le=50),
) -> dict:
    """The cross-turn teaching memory (M3): per concept, the current teaching
    mode / outcome and the most recent (mode, outcome, ts, note) turns. This
    is what lets the engine say "上次讲到这里，今天深入一步"."""
    if not _te.is_enabled():
        return {"status": "disabled"}
    try:
        log = _te.load_teaching_log(student_id)
        concepts: dict[str, Any] = {}
        for key, entries in log.items():
            last = entries[-1] if entries else None
            concepts[key] = {
                "current_mode": last.mode if last else "",
                "current_outcome": last.outcome if last else "unknown",
                "last_ts": last.ts if last else 0.0,
                # newest-first within a concept (UI-friendly)
                "entries": [e.to_dict() for e in reversed(entries)][:limit_per_concept],
            }
        return {"status": "ok", "concepts": concepts}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/error-notebook")
def student_error_notebook(
    student_id: str = Depends(resolve_student_id),
    limit: int = Query(default=200, ge=1, le=200),
) -> dict:
    """错题本（P3）：跨会话聚合 verdict ∈ {wrong, partial} 的题目，新→旧。

    G4：数据源改为 learning-evidence journal 投影（available 的
    assessment 来源且当前判定 wrong/partial）；按需实时聚合，不物化副本。
    前端测评中心分页展示。
    """
    try:
        from app.agents.student_model.evaluation.projections import (
            wrong_answer_items)
        items = wrong_answer_items(student_id, limit=limit)
        return {"status": "ok", "items": items, "count": len(items)}
    except Exception as e:
        return {"status": "error", "message": str(e)}






@router.get("/learning-path")
def student_learning_path(student_id: str = Depends(resolve_student_id),
                          user: User | None = Depends(optional_user)) -> dict:
    """The advisory learning path（G4：M5 图谱 × 统一评价投影）:
    what to learn next (frontier: 前置已支持、自身未支持), what to review
    (已观察待解决：fragile/conflicting/emerging), and the current 1..5
    difficulty dial（仅受近期作答影响）."""
    if not _te.is_enabled():
        return {"status": "disabled"}
    try:
        view = _evaluation_view(student_id)
        g = _graph_for(student_id)
        # review candidates: 已观察待解决概念（统一评价语义）
        revs: list[dict[str, Any]] = []
        for cid, rec in view.items():
            st = str((rec or {}).get("state", ""))
            if st not in ("fragile", "conflicting", "emerging"):
                continue
            node = g.nodes.get(cid) if g is not None else None
            revs.append({"name": node.name if node is not None else cid,
                         "skill_id": cid, "state": st,
                         "difficulty": int(getattr(node, "difficulty", 3) or 3)
                         if node is not None else 3})
        nxt = _next_learnable(g, view, student_id, limit=6)
        lp = _te.get_teaching_manager().plan_curriculum(
            next_learnable=[], review_candidates=revs)
        rationale_parts = []
        if nxt:
            rationale_parts.append(
                f"下一步建议学「{nxt[0]['name']}」（{nxt[0]['reason']}）"
                + (f"等{len(nxt)}项" if len(nxt) > 1 else ""))
        if lp.rationale and lp.rationale != "暂无可规划路径":
            rationale_parts.append(lp.rationale)
        head = (nxt[0]["skill_id"] if nxt else
                revs[0]["skill_id"] if revs else "")
        return {"status": "ok",
                "next_to_learn": nxt,
                "review": [n.to_dict() for n in lp.review_nodes],
                "difficulty": _current_difficulty(head, student_id),
                "rationale": "；".join(rationale_parts) or "暂无可规划路径"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
