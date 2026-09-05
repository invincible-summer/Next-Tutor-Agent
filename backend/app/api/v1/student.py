"""Student Model & Teaching Engine projection API (M2/M3 observability).

Read-only projections of the student-intelligence internals for the frontend:
the long-term learner profile, per-skill BKT mastery joined with the concept
memory state, the cross-turn teaching log, and the advisory learning path.
Mirrors the /assessment endpoints' graceful-degradation contract.

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


def _concept_state_map(sm: "_sm.StudentModel") -> dict[str, Any]:
    """{skill_id: ConceptRecord} from the student's concept memory (M2).

    The memory dict is keyed loosely (skill id or bare concept); the record's
    own skill_id is the reliable join key for the mastery view.
    """
    out: dict[str, Any] = {}
    try:
        for rec in sm.memory.values():
            if getattr(rec, "skill_id", ""):
                out[rec.skill_id] = rec
    except Exception:
        pass
    return out


def _knowledge_names(student_id: str) -> dict[str, str]:
    """{node/skill id: human concept name} for the learning ledger.

    Ledger knowledge_points may be filed as raw graph node ids (callers pass
    ctx/goal concept ids); resolve them for display the same way /mastery
    does. Empty dict on any failure — unresolved values pass through.
    """
    names: dict[str, str] = {}
    try:
        if not _sm.is_enabled():
            return names
        sm = _sm.get_student_model(student_id)
        for nid, node in sm.graph.nodes.items():
            if getattr(node, "name", ""):
                names[nid] = node.name
        for rec in sm.memory.values():
            sid = getattr(rec, "skill_id", "")
            concept = getattr(rec, "concept", "")
            if sid and concept:
                names.setdefault(sid, concept)
    except Exception:
        pass
    return names


def _current_difficulty(sm: "_sm.StudentModel", mview: dict[str, Any],
                        lp: "_te.LearningPath", student_id: str) -> int:
    """The 1..5 dynamic-difficulty dial for the head of the learning path.

    Seeds from the first next-to-learn skill's mastery and steps by its recent
    assessed outcomes (difficulty.py); falls back to the mean mastery seed.
    """
    try:
        from app.agents.teaching_engine.difficulty import (compute_difficulty,
                                                           seed_from_mastery)
        if lp.next_nodes:
            sid = lp.next_nodes[0].skill_id
            mastery_p = float((mview.get(sid) or {}).get("p_known", 0.0))
            recent = _te.load_teaching_log(student_id).get(sid) or []
            return compute_difficulty(mastery_p, recent)
        ps = [float((m or {}).get("p_known", 0.0)) for m in mview.values()]
        return seed_from_mastery(sum(ps) / len(ps) if ps else 0.0)
    except Exception:
        return 3


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


@router.get("/mastery")
def student_mastery(student_id: str = Depends(resolve_student_id)) -> dict:
    """Per-skill mastery (M2 BKT) joined with the concept memory state
    (introduced / partial / understood / misconception). Weakest first, so the
    frontend can render the "哪里薄弱" panel directly."""
    if not _sm.is_enabled():
        return {"status": "disabled"}
    try:
        sm = _sm.get_student_model(student_id)
        states = _concept_state_map(sm)
        skills: list[dict[str, Any]] = []
        for sid, m in sm.mastery_view().items():
            rec = states.get(sid)
            try:
                node = sm.graph.get(sid)
            except Exception:
                node = None
            concept = (rec.concept if rec is not None and rec.concept
                       else node.name if node is not None
                       else sid.rsplit(".", 1)[-1])
            skills.append({
                "skill_id": sid,
                "concept": concept,
                "subject": sid.split(".", 1)[0] if "." in sid else "",
                "p_known": (m or {}).get("p_known", 0.0),
                "state": rec.state.value if rec is not None else "unknown",
                "attempts": (m or {}).get("attempts", 0),
                "correct": (m or {}).get("correct", 0),
                "last_review": (m or {}).get("last_review", 0.0),
                "mistakes": list((m or {}).get("mistakes", []) or []),
                # W3/§9.4 兼容增键：p_known 是未校准的贝叶斯估计，不是掌握
                # 概率声明；能力结论以 evidence-profile 的证据投影为准。
                "source": "m2_bkt",
                "estimate_kind": "uncalibrated_bayesian",
            })
        skills.sort(key=lambda s: (s["p_known"], s["skill_id"]))
        return {"status": "ok", "skills": skills, "count": len(skills)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/evidence-profile")
def student_evidence_profile(concept: str = Query(""),
                             student_id: str = Depends(resolve_student_id)) -> dict:
    """W3/F05 evidence profile: what the student has DEMONSTRATED (per concept
    × capability dimension), which attempts support it, and what has never
    been observed — derived on read from the events log + ledger (no second
    mastery store; §8.2 CapabilityProjection rebuildable by construction)."""
    if not _sm.is_enabled():
        return {"status": "disabled"}
    try:
        projection = _sm.project_capabilities(student_id,
                                              concept=concept.strip())
        concepts = projection.get("concepts") or []
        if not concepts:
            return {"status": "empty", "concepts": [], "count": 0,
                    "dimensions": projection.get("dimensions", [])}
        return {"status": "ok", "concepts": concepts, "count": len(concepts),
                "dimensions": projection.get("dimensions", [])}
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

    数据源是各会话 quiz_history 的判分写回；按需实时聚合（不物化副本，
    判分会就地更新会话文件，物化会过期）。前端测评中心分页展示。
    """
    try:
        from app.core.error_notebook import collect_error_notebook
        items = collect_error_notebook(student_id, limit=limit)
        return {"status": "ok", "items": items, "count": len(items)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/learning-records")
def student_learning_records(
    student_id: str = Depends(resolve_student_id),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """学习账本（L1 档案层）：独立于对话的学习结果全量记录，新→旧分页。

    每条含题干/题型/难度/知识点/作答/判分/时间与来源状态（active/
    independent/deleted——来源对话删除后记录仍保留）。这是"最近作答"
    与记忆中心学习档案区的数据源。知识点若以图谱节点 id 入账，此处解析
    为人读概念名（解析失败时原样返回）。
    """
    try:
        from app.core import learning_records as lr
        records = lr.list_records(student_id)
        names = _knowledge_names(student_id)
        total = len(records)
        window = records[offset:offset + limit]
        items = [{
            "record_id": r.get("record_id", ""),
            "session_id": r.get("session_id", ""),
            "source_kind": r.get("source_kind", ""),
            "source_status": r.get("source_status", ""),
            "knowledge_point": names.get(r.get("knowledge_point", "")) or r.get("knowledge_point", ""),
            "subject": r.get("subject", ""),
            "bloom_level": r.get("bloom_level", ""),
            "stem": r.get("stem", ""),
            "type": r.get("type", ""),
            "difficulty": r.get("difficulty", ""),
            "student_answer": r.get("student_answer", ""),
            "verdict": r.get("verdict", ""),
            "score": r.get("score"),
            "created_at": r.get("created_at", 0),
            "updated_at": r.get("updated_at", 0),
        } for r in window]
        return {"status": "ok", "items": items, "count": len(items),
                "total": total, "offset": offset, "limit": limit}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/bloom-profile")
def student_bloom_profile(student_id: str = Depends(resolve_student_id)) -> dict:
    """布鲁姆认知档案（L1 档案层）：从学习账本确定性聚合的每概念/每层级
    表现 + 薄弱项。单一真相源：M4 出题、对话内出题、M9 建议与画像页都读
    这份数据（经 core/bloom_profile 模块）。只读，零 LLM。"""
    try:
        from app.core.bloom_profile import profile_for
        return {"status": "ok", **profile_for(student_id)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _personalized_next(sm: "_sm.StudentModel", student_id: str,
                       mview: dict[str, Any], *, limit: int = 6,
                       grade: str | None = None
                       ) -> list[dict[str, Any]]:
    """Constructive, personalized next-step suggestions (replaces the old
    global next_learnable(None) call, which -- over the 1400-node ontology
    with a sparse mastery view -- always returned the same difficulty-1
    nodes, e.g. 函数定义域/原子结构, forever).

    Priority (deduped, filled up to `limit`):
      1. M9 weekly-plan concepts not yet mastered      -> reason 学习计划
      2. what a recently taught concept unlocks next   -> reason 承接「X」
      3. next learnable inside the M9 goal's subjects  -> reason 目标学科
      4. stage-appropriate foundations (student grade) -> reason 打基础/拓展

    Deterministic, no LLM, never raises. A skill with any attempt record is
    left to the review list rather than re-suggested as "next".
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    graph = sm.graph

    def _p(sid: str) -> float:
        m = mview.get(sid) or {}
        return float(m.get("p_known", 0.0)) if isinstance(m, dict) else 0.0

    def _fresh_ready(nid: str) -> bool:
        """Prereqs met, not mastered, and never attempted (attempted-but-
        unmastered belongs to the review list, not to next steps)."""
        if _p(nid) > 0.0:
            return False
        try:
            return not graph.unmet_prerequisites(nid, mview)
        except Exception:
            return False

    def _push(node: Any, reason: str) -> bool:
        if node is None or node.id in seen or len(out) >= limit:
            return False
        if not _fresh_ready(node.id):
            return False
        seen.add(node.id)
        out.append({"name": node.name, "skill_id": node.id,
                    "difficulty": node.difficulty, "reason": reason})
        return True

    # 学段优先取调用方传入的账户资料（StudentModel 侧默认本科、空串会被
    # 持久层 coerce 回本科，注册默认「自动」的用户在 sm.profile 里读不到
    # 空串）；未传时回落 sm.profile（guest/直接调用）。
    grade = ((grade if grade is not None else getattr(sm.profile, "grade", "")) or "").strip()
    # 学段为「自动」（空串）时按本科推荐：与前端知识页默认学段口径一致，
    # 避免自动学段用户拿到跨学段混杂的推荐条。
    if not grade:
        grade = "本科"

    # 1. M9 weekly plan (the student's own plan comes first)
    orch_state = None
    try:
        from app.agents import learning_orchestration as _lo
        if _lo.is_enabled():
            orch_state = _lo.get_orchestration_service()._load(student_id)
            for week in sorted(getattr(orch_state, "plan", []) or [],
                               key=lambda w: getattr(w, "week_index", 0)):
                focus = (getattr(week, "focus", "") or "").strip()
                for c in getattr(week, "concepts", []) or []:
                    _push(graph.get(getattr(c, "concept_id", "")),
                          f"学习计划·{focus}" if focus else "学习计划")
    except Exception:
        orch_state = None

    # 2. continuation of recently taught concepts (M3 teaching log)
    try:
        from app.agents.teaching_engine.teaching_log import load_teaching_log
        log = load_teaching_log(student_id)
        recent = sorted(log.items(),
                        key=lambda kv: -(kv[1][-1].ts if kv[1] else 0.0))[:3]
        for key, _entries in recent:
            if len(out) >= limit:
                break
            node = graph.get(key) or graph.match_concept(key, threshold=0.6)
            if node is None:
                continue
            for did in graph.descendants_of(node.id):
                if _push(graph.get(did), f"承接「{node.name}」"):
                    break   # one constructive continuation per recent thread
    except Exception:
        pass

    # 3. M9 goal subjects
    try:
        goal = getattr(orch_state, "goal", None) if orch_state else None
        for subj in list(getattr(goal, "subjects", []) or [])[:2]:
            for n in graph.next_learnable(subj, mview, limit=8):
                # stage-appropriate first inside the goal subject
                if grade and getattr(n, "level", "") not in ("", grade):
                    continue
                _push(n, f"目标学科·{subj}")
    except Exception:
        pass

    # 4. fallback: stage-appropriate foundations, then anything ready
    try:
        pool = graph.next_learnable(None, mview, limit=40)
        stage = [n for n in pool
                 if not grade or getattr(n, "level", "") in ("", grade)]
        for n in stage:
            _push(n, f"{grade}基础" if grade else "循序渐进")
        for n in pool:   # still short -> broaden beyond the stage
            _push(n, "拓展学习")
    except Exception:
        pass
    return out


@router.get("/learning-path")
def student_learning_path(student_id: str = Depends(resolve_student_id),
                          user: User | None = Depends(optional_user)) -> dict:
    """The advisory learning path (M3 curriculum + M9 plan/goal + M2 mastery):
    what to learn next (personalized: plan > continue recent > goal subjects
    > stage foundations), what to review (middling mastery, going stale), and
    the current 1..5 difficulty dial."""
    if not _te.is_enabled():
        return {"status": "disabled"}
    try:
        sm = _sm.get_student_model(student_id)
        mview = sm.mastery_view()
        # 学段以账户资料为准（登录态）；「自动」（空串）在 _personalized_next
        # 里按本科回落，与前端知识页默认学段一致。游客沿用 sm.profile。
        nxt = _personalized_next(sm, student_id, mview, limit=6,
                                 grade=user.profile.grade if user else None)
        # review candidates: seen skills with middling mastery
        revs: list[dict[str, Any]] = []
        for sid, m in sm.mastery.records.items():
            try:
                if m.attempts > 0 and 0.3 <= m.p_known < 0.8:
                    node = sm.graph.get(sid)
                    revs.append({"name": node.name if node else sid,
                                 "skill_id": sid, "mastery": m.p_known,
                                 "last_review": m.last_review,
                                 "difficulty": node.difficulty if node else 3})
            except Exception:
                continue
        lp = _te.get_teaching_manager().plan_curriculum(
            next_learnable=[], review_candidates=revs)
        rationale_parts = []
        if nxt:
            rationale_parts.append(
                f"下一步建议学「{nxt[0]['name']}」（{nxt[0]['reason']}）"
                + (f"等{len(nxt)}项" if len(nxt) > 1 else ""))
        if lp.rationale and lp.rationale != "暂无可规划路径":
            rationale_parts.append(lp.rationale)
        return {"status": "ok",
                "next_to_learn": nxt,
                "review": [n.to_dict() for n in lp.review_nodes],
                "difficulty": _current_difficulty(sm, mview, lp, student_id),
                "rationale": "；".join(rationale_parts) or "暂无可规划路径"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
