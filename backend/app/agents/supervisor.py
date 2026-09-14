"""Supervisor Agent (V2 orchestrator): the central controller.

Pipeline per turn:
  understand -> snapshot -> plan -> (route inside executor) -> execute -> update

It owns the cross-turn TaskState (current goal / completed / remaining) and
the lightweight StudentSnapshot derived from existing V1 signals. It delegates
the actual ReAct loop to executor.execute() and persists the turn when done.

Design (B-scheme = explicit orchestration on top of the V1 single agent):
  - The plan is advisory + constraining: executor narrows the visible tool set
    and injects a plan recap. The LLM still drives tool calls within the plan.
  - Every stage degrades gracefully: understanding/LLM-plan failures fall back
    to rules, executor failures propagate as SSE error events. The turn never
    crashes the SSE stream.
  - SSE event surface is identical to V1 chat_turn, so chat.py needs no change
    beyond selecting the entry point (env SUPERVISOR_MODE).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncGenerator, Callable

from ..core.config import settings
from ..core.context import (SOFT_BUDGET_TOKENS,
                            append_transcript, build_context, compact_history,
                            estimate_tokens, history_tokens)
from ..core.llm_async import AsyncLLMClient, get_llm
from ..core.session import TutorSession, save_session
from ..core.trace import Trace
from ..prompts.tutor import TUTOR_SYSTEM, grade_preamble, skill_cards_preamble
from .executor import _lite_tool_calls as _lite_tool_calls_fn, execute
from .planner import make_plan
from .state import (StudentSnapshot, TaskPlan, TaskState, TaskType,
                    TaskUnderstanding, PlanStep)
from .task_understanding import understand

# cap execution history entries kept on the TaskState; older ones live in the
# transcript (recoverable).
_EXEC_HISTORY_CAP = 6

# strong refs to fire-and-forget background tasks (asyncio only weak-refs
# tasks, so an unreferenced consolidation task could be GC'd mid-flight).
_BG_TASKS: set[asyncio.Task] = set()


# --- student snapshot derivation -------------------------------------------

def derive_snapshot(session: TutorSession) -> StudentSnapshot:
    """Build a student snapshot.

    V2 base (always): grade / materials / quiz count / topic hint from V1
    signals. V3 extension (when the Student Model module is enabled): fuse in
    goals / learning_style / recent_weak_points (quiz wrong/partial in this
    session) / topic hint. G4：数值能力字段已删，评价语义经
    understanding.evaluation_context 注入。The Student
    Model path is fully guarded -- any failure leaves the V2 base untouched.
    """
    # Merge workspace shared files so the snapshot (and thus the planner)
    # knows workspace materials exist -- otherwise the planner skips the
    # knowledge_search step and the router hides the tool entirely.
    from ..core.workspace import merged_knowledge_files
    files, names = merged_knowledge_files(session)
    names = names[:5]
    weak: list[str] = []
    for qh in (session.quiz_history or [])[-3:]:
        for q in (qh.get("questions") or []) if isinstance(qh, dict) else []:
            if not isinstance(q, dict):
                continue
            # Only graded wrong/partial answers count as weak points —
            # unanswered or correct questions say nothing about weakness.
            res = q.get("result")
            if not isinstance(res, dict) or \
                    str(res.get("verdict")) not in ("wrong", "partial"):
                continue
            kp = (q.get("knowledge_point") or "")
            if kp and kp not in weak:
                weak.append(kp)
    topic = None
    if session.compaction and isinstance(session.compaction, dict):
        topic = (session.compaction.get("summary") or "").split("\n")[0][:80] or None
    snap = StudentSnapshot(
        grade=session.grade,
        has_materials=bool(files),
        material_count=len(files),
        material_names=names,
        recent_quiz_count=len(session.quiz_history or []),
        recent_weak_points=weak[:3],
        conversation_topic_hint=topic,
    )
    # V3: fuse Student Model data when enabled
    try:
        from .student_model import get_student_model, is_enabled
        from .student_model.store import DEFAULT_STUDENT_ID
        if is_enabled():
            _sid = getattr(session, 'student_id', '') or DEFAULT_STUDENT_ID
            sm = get_student_model(_sid)
            sm_snap = sm.snapshot(
                grade=session.grade,
                current_subject="",
                has_materials=snap.has_materials,
                material_count=snap.material_count,
                material_names=snap.material_names,
                recent_quiz_count=snap.recent_quiz_count,
            )
            snap.goals = sm_snap.get("goals", [])
            snap.learning_style = sm_snap.get("learning_style", {})
    except Exception:
        pass
    return snap


def _plan_learning_path(understanding, session, trace) -> str:
    """Phase 3: when the student asks for a learning plan, build a LearningPath
    from the skill graph + memory and render it as a soft directive.

    PURE-READ over student_model (graph + mastery getters only). Returns a
    [学生智能·学习路径] block, or "" when the engine is off / no path. Never
    raises.
    """
    try:
        from .teaching_engine import is_enabled as te_enabled
        if not te_enabled():
            return ""
        from .student_model import get_student_model
        from .teaching_engine import get_teaching_manager
        from .student_model.store import DEFAULT_STUDENT_ID
        _sid = getattr(session, 'student_id', '') or DEFAULT_STUDENT_ID
        sm = get_student_model(_sid)
        subject = understanding.subject or ""
        nxt: list[dict] = []     # G4：掌握度候选已删（路径非个性化）
        revs: list[dict] = []
        lp = get_teaching_manager().plan_curriculum(
            current_name=understanding.concept or subject or "",
            current_skill_id="", next_learnable=nxt, review_candidates=revs)
        trace.log("teaching_engine_curriculum",
                  next_count=len(lp.next_nodes), review_count=len(lp.review_nodes),
                  rationale=lp.rationale)
        lines = [f"[学生智能·学习路径] {lp.rationale}。"]
        if lp.next_nodes:
            lines.append("  可推进：" + " -> ".join(n.name for n in lp.next_nodes[:4]))
        if lp.review_nodes:
            lines.append("  需复习：" + "、".join(n.name for n in lp.review_nodes[:4]))
        return "\n".join(lines)
    except Exception as e:
        trace.log("teaching_engine_curriculum_error", message=str(e))
        return ""


class _PrecomputedSearchStore:
    """Duck-typed KnowledgeStore stand-in holding pre-fetched hybrid hits.

    M5's ContentResolver calls store.search(query_hint=concept) synchronously,
    but hybrid_search is async — so the supervisor's (async) directive step
    awaits the hybrid search up front and hands the resolver this sync view
    over the already-fetched hits. Same contract as the merged BM25 store:
    has_knowledge() + search(query, top_k) -> [{source, text, score, ...}].
    """

    def __init__(self, hits: list[dict[str, Any]]):
        self._hits = hits

    def has_knowledge(self) -> bool:
        return bool(self._hits)

    def search(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        return self._hits[:top_k]


class _GatedSearchStore:
    """P9：M5 知识指令的检索旁路同样过证据门。

    ContentResolver 此前直接消费未过滤的 BM25 命中（截 240 字符），乱码/
    无关命中会让指令声称「学生已上传资料提及此知识点」。小池（≤8）沿用
    query-aware 直通语义（M5 只向指令输出资料名，风险面小）。
    """

    def __init__(self, inner: Any):
        self._inner = inner

    def has_knowledge(self) -> bool:
        try:
            return bool(self._inner.has_knowledge())
        except Exception:
            return False

    def search(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        try:
            hits = list(self._inner.search(query, top_k=max(top_k * 4, top_k)) or [])
            if not hits:
                return []
            from ..core.evidence_gate import apply_evidence_gate
            gate = apply_evidence_gate(query, hits, top_k, allow_small_direct=True)
            return gate.selected
        except Exception:
            return []


def _journal_evaluation_view(student_id: str) -> dict | None:
    """G4：journal 统一评价投影 {concept_id: {"state": ...}}；失败 None。"""
    try:
        from .student_model.evaluation.store import get_journal
        state = get_journal(student_id).state()
        out = {}
        for (_ws, _k), jid in state.concept_current.items():
            j = state.judgments.get(jid)
            if j is None:
                continue
            out[j.concept_ref.concept_id] = {
                "state": j.state.value if hasattr(j.state, "value") else str(j.state)}
        return out or None
    except Exception:
        return None


async def _knowledge_directive_for_turn(understanding, session, trace) -> str:
    """M5: build the [知识智能·...] soft-directive block for this turn's concept.

    PURE-READ over the knowledge layer: graph + retriever + content resolver +
    the workspace/session knowledge store. Uses the Student Model's mastery view
    so missing-prereq detection reflects the student. Returns "" when M5 is off,
    the concept is outside the ontology, or nothing actionable was found -- so
    the turn is unchanged. Never raises.
    """
    try:
        from .knowledge import get_knowledge_service, is_enabled as ki_enabled
        if not ki_enabled():
            return ""
        concept = (understanding.concept or "").strip()
        if not concept or (understanding.intent
                           and understanding.intent.value == "chitchat"):
            return ""
        ks = get_knowledge_service()
        # G4：M5 前置补缺读统一评价投影（fragile/conflicting/emerging 的
        # 前置才提示补缺；未观察 ≠ 未掌握）。读失败则 None（图-only 降级）。
        evaluation_view = _journal_evaluation_view(
            getattr(session, "student_id", ""))
        # duck-typed material store for content grounding. Prefer hybrid
        # retrieval (BM25 + vector RRF) over the scoped session/workspace
        # stores when the embedding track is configured; otherwise reuse the
        # SAME merged BM25 store as knowledge_search. Fully guarded -- any
        # vector-track failure degrades to the BM25 path, never the turn.
        store = None
        try:
            from ..core.config import settings as _settings
            from ..core.embedding import get_embedding_client
            embed = get_embedding_client()
            if embed is not None and _settings.rag_hybrid:
                from ..core.workspace import scoped_knowledge_stores
                scoped = scoped_knowledge_stores(session)
                if scoped:
                    from ..core.hybrid import hybrid_search
                    hits = await hybrid_search(scoped, concept, top_k=3,
                                               embed_client=embed)
                    if hits:
                        from ..core.evidence_gate import apply_evidence_gate
                        gated = apply_evidence_gate(concept, hits, 3,
                                                    allow_small_direct=True)
                        hits = gated.selected
                    if hits:
                        store = _PrecomputedSearchStore(hits)
        except Exception:
            store = None
        if store is None:
            try:
                from ..core.workspace import merged_knowledge_store
                store = _GatedSearchStore(merged_knowledge_store(session))
            except Exception:
                store = None
        directive = ks.build_directive(concept=concept,
                                        evaluation_view=evaluation_view,
                                        knowledge_store=store, grade=session.grade,
                                        student_id=(getattr(session, "student_id", "") or ""))
        if directive:
            trace.log("knowledge_directive", concept=concept,
                      has_directive=True)
        return directive
    except Exception as e:
        trace.log("knowledge_directive_error", message=str(e))
        return ""


def _memory_directive_for_turn(understanding, session, trace) -> str:
    """M6: build the [记忆智能·...] soft-directive block for this turn's concept.

    PURE-READ over the memory layer: JIT retrieval of past episodic/semantic/
    procedural memories relevant to the current concept. Returns "" when M6 is
    off, there is no past memory yet, or nothing actionable was found -- so the
    turn is unchanged. Never raises.
    """
    try:
        from .memory import get_memory_service, is_enabled as mem_enabled
        if not mem_enabled():
            return ""
        # 用户级提示词画像在普通对话与工作区对话间全局统一计数与读取；
        # 工作区公共记忆另有隔离块，只在同一工作区内可见。
        concept = (understanding.concept or "").strip()
        subject = (understanding.subject or "").strip()
        if not concept and (understanding.intent
                            and understanding.intent.value == "chitchat"):
            return ""
        from .student_model.store import DEFAULT_STUDENT_ID
        _sid = getattr(session, 'student_id', '') or DEFAULT_STUDENT_ID
        ms = get_memory_service()
        directive = ms.build_directive(
            student_id=_sid, concept=concept, subject=subject)
        if directive:
            trace.log("memory_directive", concept=concept, has_directive=True)
        return directive
    except Exception as e:
        trace.log("memory_directive_error", message=str(e))
        return ""


def _memory_consolidate_turn(student_id, session_id, workspace_id,
                             understanding, user_message,
                             final_answer, final_tool_calls, evs,
                             strategy, trace) -> None:
    """M6: update bounded prompt memory and aggregate strategy outcomes.

    Legacy detailed episodic/semantic stores are compatibility-read-only and
    no longer grow from chat turns or schedule periodic consolidation.
    """
    try:
        from .memory import get_memory_service, is_enabled as mem_enabled
        if not mem_enabled():
            return
        ms = get_memory_service()
        events_data = [e.to_dict() for e in (evs or [])]
        strat_mode = ""
        strat_outcome = ""
        if strategy is not None and hasattr(strategy, "mode") and strategy.mode:
            strat_mode = strategy.mode.value
        if final_tool_calls:
            for tc in final_tool_calls:
                res = tc.get("result")
                if isinstance(res, dict) and "verdict" in res:
                    v = str(res.get("verdict")).lower()
                    strat_outcome = ("correct" if "correct" in v or v == "对"
                                     else "wrong" if "wrong" in v or v == "错"
                                     else "engaged")
                    break
        if not strat_outcome and strat_mode:
            strat_outcome = "engaged"
        stats = ms.consume_turn(
            student_id=student_id, session_id=session_id,
            workspace_id=workspace_id, events=events_data,
            user_message=user_message, answer=final_answer,
            strategy_mode=strat_mode, strategy_outcome=strat_outcome,
            subject=(understanding.subject or ""))
        if stats.get("procedural_updated") or stats.get("prompt_memory"):
            trace.log("memory_update", **stats)
    except Exception as e:
        trace.log("memory_update_error", message=str(e))


async def _adapt_for_turn(understanding, snapshot, session, trace, llm=None):
    """V3/M3: produce a TeachingStrategy for the turn target and render it as a
    soft recap string. Returns (strategy, recap_text). recap_text is '' when
    the Student Model is off or nothing actionable was found, so the executor's
    plan recap stays unchanged (V2 behavior). Never raises.

    M3 routing: when TEACHING_ENGINE_MODE is on (default), assemble a
    TeachingContext from live student state + the cross-turn teaching_log and
    let the TeachingEngine pick a TeachingMode (INTRODUCTION/EXPLANATION/
    REMEDIATION/PRACTICE/REVIEW/CHALLENGE). When off, fall back to the V3
    adaptation path exactly. Both paths render the same [学生智能·…] markers so
    the existing trace/tests keep working.

    W3/D08: TEACHING_DECISION_MODE adds one bounded LLM teaching decision on
    top of the rules strategy (trigger-gated; shadow records, active applies
    a validated constrained adjustment). rules (default) changes nothing.
    """
    try:
        from .student_model import get_student_model, is_enabled as sm_enabled
        from .student_model.store import DEFAULT_STUDENT_ID
        if not sm_enabled():
            return None, ""
        _sid = getattr(session, 'student_id', '') or DEFAULT_STUDENT_ID
        sm = get_student_model(_sid)
        concept = understanding.concept or ""
        subject = understanding.subject or ""
        intent = understanding.intent.value if understanding.intent else "explain"

        from .teaching_engine import is_enabled as te_enabled
        if te_enabled():
            strat = await _adapt_via_engine(sm, concept, subject, intent,
                                            session.grade, understanding,
                                            trace, sid=_sid, llm=llm)
        else:
            from .teaching_engine.state import TeachingStrategy
            strat = TeachingStrategy(target_concept=concept,
                                     rationale="适配降级")

        lines = _render_strategy(strat, concept, subject)
        trace.log("supervisor_adaptation",
                  mode=strat.mode.value if hasattr(strat, "mode") else "",
                  review_first=[getattr(n, "id", n) for n in strat.review_first],
                  depth=strat.explanation_depth,
                  quiz_difficulty=strat.suggested_quiz_difficulty,
                  rationale=strat.rationale)
        return strat, "\n".join(lines)
    except Exception as e:
        trace.log("supervisor_adaptation_error", message=str(e))
        return None, ""


def _arbitrate_directives(recap: str, strategy, sid: str) -> str:
    """P2: 多层软指令以同权 system 消息叠加，给模型一条显式取舍顺序，
    并收敛唯一能确定性检测的冲突（策略要深入推导 vs UX 画像要简洁）。

    此前只有「学生显式输出约束」声明了优先级；其余六层 [xx智能] 块冲突时
    由模型自由裁量，结果不稳定。Never raises——仲裁失败返回原 recap。
    """
    try:
        header = ("[指令仲裁] 以下各层建议若相互冲突，按此顺序取舍："
                  "学生显式输出约束 > 红线 > 教学策略（掌握度/难度/纠错）"
                  "> 表达与交互适配。")
        if strategy is not None \
                and getattr(strategy, "explanation_depth", "") == "deep":
            from .ux_intelligence import learner_profile as _lp
            style = _lp.get_profile(sid).style
            if getattr(getattr(style, "detail_level", None), "value", "") == "concise":
                deep_line = ("[学生智能·分层教学] 该生掌握度较好，"
                             "可加入深入推导与拓展联系。")
                if deep_line in recap:
                    recap = recap.replace(
                        deep_line,
                        "[学生智能·分层教学] 该生掌握度较好但偏好简洁："
                        "核心讲透，深入推导点到为止、按需展开。")
        return header + "\n" + recap
    except Exception:
        return recap


def _response_constraint_note(understanding: TaskUnderstanding) -> str:
    """Render explicit student presentation contracts at the prompt tail."""
    fmt = getattr(understanding, "response_format", "")
    allow_assessment = getattr(understanding, "allow_followup_assessment", True)
    lines: list[str] = []
    if fmt == "one_sentence":
        lines.append("只用一句完整、准确的话回答当前问题。")
    elif fmt == "concise":
        lines.append("保持简洁，只保留完成当前请求所必需的内容，不展开默认教学章节。")
    elif fmt == "table":
        lines.append("优先用简洁表格组织当前请求的内容。")
    elif fmt == "steps":
        lines.append("按清晰的步骤组织当前请求的内容。")
    if not allow_assessment:
        lines.append("本轮不要追加练习题、测验、收尾检测或‘做完再批改’邀请。")
    if not lines:
        return ""
    return ("[学生显式输出约束 · 优先级高于默认教学策略]\n"
            + "\n".join(lines)
            + "\n只完成学生明确请求，不因教学策略的 next_check 自动扩展任务。")


def _apply_response_constraints_to_plan(plan: TaskPlan,
                                        understanding: TaskUnderstanding,
                                        trace: Trace) -> TaskPlan:
    """Remove strategy-only assessment steps when the student asked not to expand."""
    if getattr(understanding, "allow_followup_assessment", True):
        return plan
    kept = [step for step in plan.steps if step.agent_role != "assessment"]
    if len(kept) == len(plan.steps):
        return plan
    if not kept:
        kept = [PlanStep(
            agent_role="teaching", task="只完成学生要求的简短回答。",
            skill_ids=["agent.skill.teaching.direct_explain"],
        )]
    constrained = TaskPlan(steps=kept, source=f"response_constrained:{plan.source}",
                           validated=plan.validated)
    trace.log("plan_response_constraint", removed_steps=len(plan.steps) - len(kept),
              response_format=getattr(understanding, "response_format", ""))
    return constrained


def _enrich_plan_with_strategy_check(plan: TaskPlan, strategy: Any,
                                     understanding: TaskUnderstanding,
                                     tools: list[Any], trace: Trace,
                                     *, grade: str = "本科",
                                     focus: str = "",
                                     material_grounding_required: bool = False) -> TaskPlan:
    """Project an actionable TeachingStrategy check into the Skill plan.

    M3 has long produced ``next_check`` guidance, but M10 only exposes Skills
    referenced by the plan. Without this bridge, the prompt simultaneously
    asked for a closing assessment and hid ``generate_quiz``. We add exactly
    one assessment step only when the strategy requests it and the executable
    tool is actually installed for this runtime.
    """
    if material_grounding_required and understanding.intent == TaskType.EXPLAIN \
            and not getattr(understanding, "response_format", ""):
        trace.log("skill_plan_enrichment_skipped",
                  reason="grounded_answer_first")
        return plan
    if (strategy is None or understanding.intent not in {
            TaskType.EXPLAIN, TaskType.REVIEW,
        } or not getattr(understanding, "allow_followup_assessment", True)):
        if (strategy is not None
                and not getattr(understanding, "allow_followup_assessment", True)):
            trace.log("skill_plan_enrichment_skipped",
                      reason="explicit_response_constraint",
                      response_format=getattr(understanding, "response_format", ""))
        return plan
    next_check = getattr(strategy, "next_check", None)
    concept = str(getattr(next_check, "concept", "") or "").strip()
    if not concept or not any(getattr(tool, "name", "") == "generate_quiz"
                              for tool in tools):
        return plan
    skill_id = "agent.skill.assessment.generate_practice"
    if any(skill_id in step.skill_ids for step in plan.steps):
        return plan
    if len(plan.steps) >= 4:
        trace.log("skill_plan_enrichment_skipped", reason="plan_step_limit",
                  skill_id=skill_id)
        return plan
    difficulty = getattr(strategy, "suggested_quiz_difficulty", "medium") or "medium"
    quiz_args: dict[str, Any] = {
        "topic": concept,
        "grade": grade or "本科",
        "difficulty": difficulty,
        "count": 1,
    }
    # The closing check must test THIS turn's focus (e.g. 滴定步骤), not just
    # the coarse session concept — otherwise every turn re-issues the same
    # canonical question.
    focus_hint = (focus or "").strip()[:60]
    if focus_hint:
        quiz_args["focus"] = focus_hint
    check_step = PlanStep(
        agent_role="assessment",
        task=(f"完成讲解后，围绕「{concept}」生成 1 道结构化收尾检测题，"
              f"建议难度 {difficulty}（下限，不得低于）：学生当轮明确要求"
              "更难或更简单时，以学生的要求为准调整 difficulty。"
              "让学生先作答再讲解。"),
        suggested_tools=["generate_quiz"],
        skill_ids=[skill_id],
        tool_args={"generate_quiz": quiz_args},
        auto_invoke=True,
    )
    enriched = TaskPlan(
        steps=[*plan.steps, check_step],
        source=f"strategy_enriched:{plan.source}",
        validated=plan.validated,
    )
    trace.log("skill_plan_enriched", reason="teaching_strategy_next_check",
              skill_id=skill_id, concept=concept, difficulty=difficulty,
              steps=[step.to_dict() for step in enriched.steps])
    return enriched


async def _adapt_via_engine(sm, concept, subject, intent, grade,
                            understanding, trace, sid: str, llm=None):
    """G4（plan §13.3）：TeachingContext 不携带掌握度；评价上下文经
    understanding.evaluation_context 只读注入。"""
    from .teaching_engine import (TeachingContext, get_teaching_manager,
                                  is_enabled as te_enabled,
                                  previous_mode_for)
    if not te_enabled():
        from .teaching_engine.state import TeachingStrategy
        return TeachingStrategy(target_concept=concept, rationale="适配降级")
    concept_key = concept or ""
    prev_mode, prev_outcome, turns = previous_mode_for(sid, concept_key)
    ctx = TeachingContext(
        concept=concept, subject=subject, task_type=intent, grade=grade,
        learning_style=sm.profile.learning_style.to_dict(),
        concept_key=concept_key,
        previous_mode=prev_mode, previous_outcome=prev_outcome,
        turns_on_concept=turns,
        evaluation_context=getattr(understanding, "evaluation_context",
                                   None) or {})
    trace.log("teaching_engine_context", concept=ctx.concept,
              previous_mode=ctx.previous_mode,
              turns_on_concept=ctx.turns_on_concept)
    strat = get_teaching_manager().adapt(ctx, student_id=sid)
    if getattr(strat, "target_skill_id", "") == "":
        strat.target_skill_id = concept_key
    # P4: 情绪弱信号进难度——学生最近明确说过「太难了/看不懂」（M8 规则分类
    # 的显式反馈）时，把收尾检测难度降一档。这是 supervisor 合成层的输入
    # 叠加（不是 M8 改教学计划，M3/M8 边界不动）；学业信号（quiz verdict
    # 拨盘）仍是主通道，本规则只在最新一条反馈是 too_hard 时生效。
    try:
        from .ux_intelligence import learner_profile as _ux_lp
        recent_fb = _ux_lp.get_profile(sid).recent_feedback
        last_fb = str(getattr(recent_fb[-1], "value", recent_fb[-1])
                      if recent_fb else "")
        if last_fb == "explanation_too_hard":
            _downgrade_strategy_difficulty(strat, trace)
    except Exception:
        pass
    # W3/D08: one bounded LLM teaching decision (trigger-gated). rules (the
    # default) never calls; shadow records the comparison; active applies a
    # validated constrained adjustment. Any failure keeps the rules strategy.
    await _apply_teaching_decision(ctx, strat, understanding, prev_outcome,
                                   trace, llm)
    return strat


async def _apply_teaching_decision(ctx, strat, understanding, prev_outcome,
                                   trace, llm) -> None:
    """W3/D08 adapter hook — never raises, never replaces the rules strategy."""
    try:
        from ..core.config import settings
        mode = settings.teaching_decision_mode
        if mode == "rules" or llm is None:
            return
        from .teaching_engine.decision_adapter import (
            apply_decision, decide_teaching, should_decide)
        constraints = {
            "response_format": getattr(understanding, "response_format", ""),
            "allow_followup_assessment": getattr(
                understanding, "allow_followup_assessment", True),
        }
        recent_mistakes = len(getattr(ctx, "mistakes", None) or [])
        misconceptions = len(getattr(ctx, "misconceptions", None) or [])
        if not should_decide(recent_outcome=(prev_outcome.value
                                             if hasattr(prev_outcome, "value")
                                             else str(prev_outcome or "")),
                             constraints=constraints,
                             misconceptions=misconceptions,
                             recent_mistakes=recent_mistakes):
            return
        decision = await decide_teaching(ctx, strat, llm=llm,
                                         recent_outcome=(prev_outcome.value
                                                         if hasattr(prev_outcome, "value")
                                                         else str(prev_outcome or "")),
                                         constraints=constraints)
        if decision is None:
            trace.log("teaching_decision", mode=mode, outcome="invalid_or_failed")
            return
        if mode == "shadow":
            trace.log("teaching_decision", mode=mode, outcome="recorded_only",
                      **decision.to_dict())
            return
        apply_decision(strat, decision)
        trace.log("teaching_decision", mode=mode, outcome="applied",
                  **decision.to_dict())
    except Exception as e:
        trace.log("teaching_decision_error", message=str(e))


def _downgrade_strategy_difficulty(strat, trace) -> None:
    """收尾检测建议难度降一档（hard→medium→easy），next_check 同步 -1。"""
    level = getattr(strat, "suggested_quiz_difficulty", "") or ""
    nxt = getattr(strat, "next_check", None)
    before = (level, getattr(nxt, "difficulty", None))
    lowered = {"hard": "medium", "medium": "easy"}.get(level)
    if lowered:
        strat.suggested_quiz_difficulty = lowered
        strat.exercise_level = lowered
    if nxt is not None and getattr(nxt, "difficulty", None):
        nxt.difficulty = max(1, int(nxt.difficulty) - 1)
    trace.log("strategy_difficulty_softened", reason="explicit_too_hard",
              before=before,
              after=(strat.suggested_quiz_difficulty,
                     getattr(nxt, "difficulty", None)))


def _render_strategy(strat, concept, subject) -> list[str]:
    """Render a TeachingStrategy into [学生智能·…] soft-directive lines.

    Shared by both the M3 and the legacy V3 path (the legacy TeachingStrategy
    lacks mode/focus/avoid/next_check, so those lines are skipped via hasattr).
    """
    lines: list[str] = []
    mode = getattr(strat, "mode", None)
    mode_v = mode.value if mode is not None else ""
    if mode_v:
        lines.append(f"[学生智能·教学策略] 模式={mode_v}：{strat.rationale}")
    if strat.review_first:
        names = "、".join(getattr(n, "name", str(n)) for n in strat.review_first)
        lines.append(f"[学生智能·前置补缺] 检测到「{concept or subject}」的前置知识"
                     f"「{names}」尚不牢固，讲解前请先用一两句话回顾。")
    if getattr(strat, "focus", None):
        lines.append("[学生智能·教学重点] " + "；".join(strat.focus[:3]))
    if getattr(strat, "avoid", None):
        lines.append("[学生智能·需要避免] " + "；".join(strat.avoid[:3]))
    if strat.explanation_depth == "basic":
        lines.append("[学生智能·分层教学] 该生当前掌握度偏低，请多举直观例子、减少抽象推导。")
    elif strat.explanation_depth == "deep":
        lines.append("[学生智能·分层教学] 该生掌握度较好，可加入深入推导与拓展联系。")
    if strat.misconceptions:
        lines.append("[学生智能·纠错] 注意纠正已有误解：" + "；".join(strat.misconceptions))
    if strat.recent_mistakes:
        lines.append("[学生智能·近期错点] 该生近期在这些点上出错：" + "；".join(strat.recent_mistakes[-2:]))
    nxt = getattr(strat, "next_check", None)
    if nxt and getattr(nxt, "concept", ""):
        lines.append(f"[学生智能·收尾检测] 讲解后出一道难度{nxt.difficulty}的「{nxt.concept}」检测题"
                     "（此为建议下限：学生当轮明确要求更难或更简单时，以学生要求为准）。")
    return lines





def _evaluation_directive_for_turn(understanding, session, trace) -> str:
    try:
        from .evaluation import get_evaluation_service, is_enabled as ev_enabled
        if not ev_enabled():
            return ""
        concept = (understanding.concept or "").strip()
        subject = (understanding.subject or "").strip()
        if not concept and understanding.intent and understanding.intent.value == "chitchat":
            return ""
        from .student_model.store import DEFAULT_STUDENT_ID
        es = get_evaluation_service()
        from .student_model.store import DEFAULT_STUDENT_ID
        _sid = getattr(session, 'student_id', '') or DEFAULT_STUDENT_ID
        directive = es.build_directive(student_id=_sid, concept=concept, subject=subject)
        if directive:
            trace.log("evaluation_directive", concept=concept, has_directive=True)
        return directive
    except Exception as e:
        trace.log("evaluation_directive_error", message=str(e))
        return ""


def _evaluation_record_turn(student_id, understanding, user_message, session, strategy, final_tool_calls, final_answer, trace) -> None:
    try:
        from .evaluation import get_evaluation_service, is_enabled as ev_enabled
        if not ev_enabled():
            return
        es = get_evaluation_service()
        concept = understanding.concept or ""
        subject = understanding.subject or ""
        intent = understanding.intent.value if understanding.intent else ""
        mode = ""
        outcome = "engaged"
        if strategy is not None and hasattr(strategy, "mode") and strategy.mode:
            mode = strategy.mode.value
        had_assessment = False
        n_questions = 0
        if final_tool_calls:
            for tc in final_tool_calls:
                res = tc.get("result")
                if isinstance(res, dict) and "verdict" in res:
                    had_assessment = True
                    v = str(res.get("verdict")).lower()
                    outcome = ("correct" if "correct" in v or v == "\u5bf9" else "wrong" if "wrong" in v or v == "\u9519" else "partial")
                    break
        stats = es.evaluate_turn(student_id=student_id, session_id=session.session_id, concept=concept, subject=subject, intent=intent, grade=session.grade, mode=mode, outcome=outcome, tool_calls=[tc.get("name") for tc in final_tool_calls], steps=len(final_tool_calls), tokens_used=0, n_questions=n_questions, had_assessment=had_assessment)
        if stats is not None:
            trace.log("evaluation_record", concept=concept, mode=mode, outcome=outcome, failure_type=stats.failure_type)
    except Exception as e:
        trace.log("evaluation_record_error", message=str(e))


def _ux_directive_for_turn(understanding, session, trace) -> str:
    """M8: UX-intelligence advisory (how to express the answer). PURE-READ.

    Injects a "[交互智能·...]" soft directive built from the student's UX
    profile (tone / detail / visual / pacing / patience), the most recent UX
    feedback, the read-only M2 academic style, and a once-per-milestone
    motivation nudge. Returns "" when M8 is off or nothing actionable. Never
    raises; mirrors _knowledge_directive_for_turn / _memory_directive_for_turn
    / _evaluation_directive_for_turn."""
    try:
        from .ux_intelligence import get_ux_service, is_enabled as ux_enabled
        if not ux_enabled():
            return ""
        from .student_model.store import DEFAULT_STUDENT_ID
        _sid = getattr(session, 'student_id', '') or DEFAULT_STUDENT_ID
        concept = (understanding.concept or "").strip()
        subject = (understanding.subject or "").strip()
        intent = understanding.intent.value if understanding.intent else "explain"
        ux = get_ux_service()
        directive = ux.build_directive(
            student_id=_sid, concept=concept, subject=subject,
            intent=intent, grade=session.grade)
        if directive:
            trace.log("ux_directive", concept=concept, has_directive=True)
        return directive
    except Exception as e:
        trace.log("ux_directive_error", message=str(e))
        return ""


def _ux_record_turn(student_id, understanding, user_message, session,
                    final_answer, final_tool_calls, trace) -> None:
    """M8: capture this turn's UX signals (WRITE). PURE-FUNCTION, zero LLM.

    Classifies any UX feedback in the user message, folds the answer length +
    feedback into the UX profile (re-deriving the interaction style), and
    appends UXEvents to the black-box log, and evaluates expression
    effectiveness (communication_score) folding it into presentation hints.
    Consumes no LLM and never writes any M2/M3/M5/M6/M7 state. Never raises;
    mirrors 6b-6e."""
    try:
        from .ux_intelligence import get_ux_service, is_enabled as ux_enabled
        if not ux_enabled():
            return
        ux = get_ux_service()
        concept = understanding.concept or ""
        subject = understanding.subject or ""
        intent = understanding.intent.value if understanding.intent else "explain"
        # derive a verdict from quiz tool results (same peek as 6c/6e/6g)
        verdict = ""
        if final_tool_calls:
            for tc in final_tool_calls:
                res = tc.get("result")
                if isinstance(res, dict) and "verdict" in res:
                    verdict = str(res.get("verdict", ""))
                    break
        ux.record_turn(
            student_id=student_id, session_id=session.session_id,
            concept=concept, subject=subject, user_message=user_message,
            answer=final_answer, grade=session.grade, intent=intent,
            verdict=verdict)
        trace.log("ux_record", concept=concept, intent=intent,
                  answer_len=len(final_answer or ""))
        # P0: 把 M8 累积的体验反馈折叠成 M2 learning_style（该字段的唯一
        # 生产写入路径——此前宣称自动推断但从未有写入方，读取端常年空转）。
        # 纯规则、零 LLM；有翻转才落盘。
        try:
            from .ux_intelligence import learner_profile as _ux_lp
            from .student_model import get_student_model
            from .student_model.style_inference import apply_style_inference
            flipped = apply_style_inference(
                get_student_model(student_id),
                _ux_lp.get_profile(student_id).recent_feedback)
            if flipped:
                trace.log("learning_style_inferred",
                          style=get_student_model(student_id)
                          .profile.learning_style.to_dict())
        except Exception:
            pass
    except Exception as e:
        trace.log("ux_record_error", message=str(e))


def _orchestration_directive_for_turn(understanding, session, trace) -> str:
    """M9: build the [编排智能·...] soft-directive block for this turn.

    PURE-READ over the learning-orchestration layer: loads the student's
    long-term plan, today's tasks, and SRS-due reviews and renders an advisory
    block so the answer can reference continuity. Returns "" when M9 is off,
    there is no goal/plan, or nothing actionable was found -- so the turn is
    unchanged. Never raises.
    """
    try:
        from .learning_orchestration import (get_orchestration_service,
                                             is_enabled)
        if not is_enabled():
            return ""
        concept = (understanding.concept or "").strip()
        subject = (understanding.subject or "").strip()
        intent = (understanding.intent.value
                  if understanding.intent else "explain")
        from .student_model.store import DEFAULT_STUDENT_ID
        _sid = getattr(session, 'student_id', '') or DEFAULT_STUDENT_ID
        lo = get_orchestration_service()
        directive = lo.build_directive(
           student_id=_sid, concept=concept, subject=subject,
           intent=intent)
        if directive:
            trace.log("orchestration_directive", concept=concept,
                      has_directive=True)
        return directive
    except Exception as e:
        trace.log("orchestration_directive_error", message=str(e))
        return ""


def _orchestration_record_turn(student_id, understanding, user_message,
                                session, final_answer, trace) -> None:
    """M9: capture this turn's orchestration signals + forward events to M6.

    Modification 1: M9 emits OrchestrationLearningEvents (milestone reached,
    streak achieved, goal-progress) which this hook forwards into M6's
    consume_turn event bus. M6 -- not M9 -- decides whether to persist them as
    episodic / semantic memory. M9 itself never writes M2/M3/M5/M6 storage.
    Never raises; mirrors 6b-6f.

    W4/A08: verdicts are no longer peeked from same-turn tool results. Quiz
    grading happens on the /quiz/* endpoints outside chat turns, so the peek
    was a dead path — and if a tool ever did return a verdict it would
    double-count against the committed-attempt feed (M9 record_quiz_evidence,
    attempt-idempotent). Recall quality reaches M9 only via that feed.
    """
    try:
        from .learning_orchestration import (get_orchestration_service,
                                             is_enabled)
        if not is_enabled():
            return
        lo = get_orchestration_service()
        concept = understanding.concept or ""
        subject = understanding.subject or ""
        intent = understanding.intent.value if understanding.intent else "explain"
        emitted = lo.record_turn(
            student_id=student_id, session_id=session.session_id,
            concept=concept, subject=subject, user_message=user_message,
            answer=final_answer, intent=intent)
        trace.log("orchestration_record", concept=concept, intent=intent,
                  emitted=len(emitted))
        # forward M9 events into M6's event bus (M6 owns the write decision)
        if emitted:
            _forward_orchestration_events_to_memory(
                student_id, emitted, subject, trace)
    except Exception as e:
        trace.log("orchestration_record_error", message=str(e))


def _forward_orchestration_events_to_memory(student_id, events, subject, trace):
    """Bridge M9 emitted events into M6's consume_turn (modification 1).

    Converts OrchestrationLearningEvents to plain event dicts and feeds them to
    MemoryService.consume_turn, which classifies them (episodic append + habit-
    pattern fold). M6 owns the persistence decision; M9 only emits. Never
    raises; no-op when M6 is off.
    """
    try:
        from .learning_orchestration import event_emitter as orch_ee
        from .memory import is_enabled as mem_on
        if not mem_on():
            return
        from .memory import get_memory_service
        event_dicts = orch_ee.to_event_dicts(events)
        if not event_dicts:
            return
        ms = get_memory_service()
        ms.consume_turn(
            student_id=student_id, events=event_dicts, subject=subject)
        trace.log("orchestration_events_forwarded", count=len(event_dicts))
    except Exception as e:
        trace.log("orchestration_events_forward_error", message=str(e))


# --- context assembly (mirrors chat_turn; inlined to avoid a circular import
#     on chat_agent, which dispatches to this module) ------------------------

def _attachment_context(session: TutorSession) -> str:
    from ..core.workspace import merged_knowledge_files
    files, _names = merged_knowledge_files(session)
    if not files:
        return ""
    lines = [f"[已上传资料 {len(files)} 份]"]
    for f in files[:5]:
        lines.append(f"  - {f['filename']} ({f['char_count']}字/{f.get('chunk_count',0)}片段)")
    lines.append("如需引用教材原文，用 knowledge_search 检索相关片段。")
    return "\n".join(lines)


def _visible_textbooks(session: TutorSession, merged_files: list[dict]) -> list[dict]:
    """P3: reverse-lookup which of this turn's visible files are registered textbooks.

    ``merged_files`` is the session+workspace file list (merged_knowledge_files).
    A file is a textbook when ``textbook_for_file`` finds a record for it. Returns
    the textbook records (最多 3 本供 preamble 渲染). Never raises: a textbook-store
    failure just yields no textbook block, leaving the preamble unchanged.
    """
    if not merged_files:
        return []
    try:
        from ..core.textbook import textbook_for_file, PUBLIC_STUDENT_ID
        sid = getattr(session, "student_id", "") or ""
        if not sid:
            return []
        out: list[dict] = []
        seen: set[str] = set()
        for f in merged_files:
            fid = f.get("id") or ""
            if not fid or fid in seen:
                continue
            # 自有反查，公用命名空间兜底（P6-B：公用教材所有人可见）。
            tb = textbook_for_file(sid, fid) or (
                textbook_for_file(PUBLIC_STUDENT_ID, fid)
                if sid != PUBLIC_STUDENT_ID else None)
            if tb is not None and tb["id"] not in seen:
                seen.add(tb["id"])
                out.append(tb)
            if len(out) >= 3:
                break
        return out
    except Exception:
        return []


def _workspace_memory_block(session: TutorSession) -> str:
    if not session.workspace_id:
        return ""
    from ..core.workspace import workspace_for_session
    ws = workspace_for_session(session)
    if ws and ws.public_memory.strip():
        # 注入防护：公共记忆是不可信二手摘要，包裹 <workspace_memory> 定界标记。
        return (f"\n[工作学习区公共记忆（跨对话共享，只读）]\n"
                f"<workspace_memory>{ws.public_memory}</workspace_memory>"
                "\n（注意：公共记忆是历史对话的二手摘要，可能滞后或与资料原文不符；"
                "当它与 knowledge_search 检索到的资料原文冲突时，一律以检索原文为准。）")
    return ""


async def _maybe_compact(session: TutorSession, preamble: str, llm: AsyncLLMClient,
                         trace: Trace) -> tuple[list[dict[str, Any]], bool]:
    """Run LLM compaction on session.messages if over budget. Returns
    (history_for_context, compaction_triggered). Mirrors chat_turn."""
    history = [dict(m) for m in session.messages]
    hist_tokens = history_tokens(history)
    recent_turns = settings.context_recent_full_turns
    if hist_tokens > SOFT_BUDGET_TOKENS and len(history) > recent_turns + 2:
        try:
            from ..core.quiz_attempts import quiz_digest_for_session
            compacted, summary = await compact_history(
                [TUTOR_SYSTEM, preamble] + history, llm,
                keep_recent=recent_turns,
                quiz_digest=quiz_digest_for_session(session))
            compacted = compacted[2:]  # strip the two head items re-added later
            if summary:
                import time as _t
                session.messages = compacted
                session.compaction = {
                    "summary": summary,
                    "compacted_upto": len(compacted),
                    "created_at": _t.time(),
                    "summary_tokens": estimate_tokens(summary),
                }
                trace.log("compaction", summary_tokens=estimate_tokens(summary),
                          kept_recent=recent_turns, budget=SOFT_BUDGET_TOKENS,
                          pre_tokens=hist_tokens)
                return compacted, True
        except Exception as e:
            trace.log("compaction_error", message=str(e))
    return history, False


def _persist_turn(session: TutorSession, user_msg: str, assistant_msg: str,
                  tool_calls: list[dict[str, Any]], trace: Trace) -> None:
    from ..core.memory_safety import memory_safe_text
    safe_user = memory_safe_text(user_msg)
    safe_answer = memory_safe_text(assistant_msg)
    entries = [{"role": "user", "content": safe_user}]
    if tool_calls:
        entries.append({"role": "assistant", "content": safe_answer,
                        "tool_calls": [{"name": tc.get("name")} for tc in tool_calls]})
    else:
        entries.append({"role": "assistant", "content": safe_answer})
    try:
        if session.session_id:
            append_transcript(session.session_id, len(session.messages), entries)
    except Exception as e:
        trace.log("transcript_error", message=str(e))


async def _maybe_update_workspace_memory(session: TutorSession, user_msg: str,
                                         assistant_msg: str,
                                         tool_calls: list[dict[str, Any]] | None) -> None:
    if not session.workspace_id:
        return
    async def _update() -> None:
        try:
            from ..core.workspace_memory import update_workspace_memory
            await update_workspace_memory(
                session.workspace_id, user_msg, assistant_msg, tool_calls,
                session_title=session.title or "")
        except Exception:
            pass
    # Public memory is a post-turn side effect. Schedule it after the answer
    # instead of adding its own LLM latency to the student's SSE response.
    task = asyncio.create_task(_update())
    task.add_done_callback(lambda _task: None)


def _update_task_state(session: TutorSession, plan: TaskPlan | None,
                       goal: str, intent: TaskType, tool_calls: list[dict[str, Any]],
                       trace: Trace) -> None:
    """Refresh the cross-turn TaskState on the session after a turn."""
    ts = TaskState.from_dict(session.supervisor_state)
    ts.current_goal = goal
    ts.task_type = intent.value
    ts.plan = plan.to_dict() if plan is not None else None
    # Mark plan steps whose capability actually ran this turn as completed;
    # others remain. This is a best-effort inference from tool_calls names.
    ran_roles = set()
    if tool_calls:
        from .router import tools_for_role
        called = {tc.get("name") for tc in tool_calls}
        for role in ("knowledge", "teaching", "assessment", "memory"):
            if tools_for_role(role) & called:
                ran_roles.add(role)
    completed: list[str] = []
    remaining: list[str] = []
    if plan and plan.steps:
        for s in plan.steps:
            (completed if s.agent_role in ran_roles or s.agent_role == "teaching"
             else remaining).append(f"{s.agent_role}:{s.task}")
    # teaching is considered addressed if the turn produced an answer at all
    ts.completed = completed[:_EXEC_HISTORY_CAP]
    ts.remaining = remaining[:_EXEC_HISTORY_CAP]
    import time as _t
    ts.execution_history.append({
        "ts": _t.time(), "goal": goal, "intent": intent.value,
        "tool_calls": [tc.get("name") for tc in tool_calls],
        "plan_source": plan.source if plan else None,
    })
    ts.execution_history = ts.execution_history[-_EXEC_HISTORY_CAP:]
    ts.updated_at = _t.time()
    session.supervisor_state = ts.to_dict()
    trace.log("supervisor_state_update",
              goal=goal, completed=len(ts.completed), remaining=len(ts.remaining))


def _recent_quiz_results(session: TutorSession) -> str:
    """One-line summary of the student's latest graded answers.

    Quiz grading happens on the question cards (outside the chat stream) and
    is written back onto session.quiz_history by the quiz API; rendering the
    per-question verdicts here lets the next turn answer "我的回答怎么样"
    truthfully. Zero LLM, deterministic. "" when nothing graded yet.
    """
    try:
        for qh in reversed(session.quiz_history or []):
            if not isinstance(qh, dict):
                continue
            marks: list[str] = []
            for i, q in enumerate(qh.get("questions") or [], 1):
                if not isinstance(q, dict):
                    continue
                res = q.get("result")
                if not isinstance(res, dict):
                    continue
                label = {"correct": "对", "partial": "部分对",
                         "wrong": "错"}.get(str(res.get("verdict", "")))
                if not label:
                    continue
                kp = str(q.get("knowledge_point") or "").strip()
                seg = f"第{i}题{label}" + (f"（{kp}）" if kp else "")
                ans = str(res.get("student_answer") or "").strip()[:30]
                if ans:
                    seg += f"：学生答「{ans}」"
                marks.append(seg)
            if marks:
                return "近期作答：" + "｜".join(marks[-6:])
    except Exception:
        pass
    return ""


def _status_recap(session: TutorSession) -> str:
    """V1-parity sticky note: step budget + grade + materials + quiz count.
    Kept so V2 does not drop the counters V1 injected via _todo_recap."""
    parts = [f"学段={session.grade}"]
    from ..core.workspace import merged_knowledge_files
    _mf, _mn = merged_knowledge_files(session)
    if _mf:
        parts.append(f"已上传{len(_mf)}份资料")
    if session.quiz_history:
        parts.append(f"已出{len(session.quiz_history)}套题")
    recent = _recent_quiz_results(session)
    if recent:
        parts.append(recent)
    from ..core.quiz_attempts import latest_quiz_digest
    digest = latest_quiz_digest(session)
    if digest:
        parts.append(digest)
    return " | ".join(parts)


def _prepare_session_learning_card(session: TutorSession, understanding,
                                   snapshot, plan: TaskPlan, goal: str,
                                   strategy, trace) -> str:
    """Build/update the bounded session projection from existing truth stores."""
    from ..core.session_learning_card import (OpenLoop, SessionLearningCard,
                                               reconcile_quiz_history)
    card = SessionLearningCard.from_dict(session.context_card)
    card.session_goal = goal or card.session_goal
    card.current_subject = understanding.subject or card.current_subject
    if understanding.concept and understanding.concept not in card.active_concepts:
        card.active_concepts.append(understanding.concept)
    card.active_concepts = card.active_concepts[-6:]
    card.active_skill_ids = list(dict.fromkeys(
        sid for step in plan.steps for sid in step.skill_ids))[-8:]
    if strategy is not None:
        card.current_mode = getattr(getattr(strategy, "mode", None), "value", "")
        card.explanation_depth = getattr(strategy, "explanation_depth", "")
        card.misconceptions = list(getattr(strategy, "misconceptions", []) or [])[-4:]
        card.recent_mistakes = list(getattr(strategy, "recent_mistakes", []) or [])[-4:]
    if not card.recent_mistakes:
        card.recent_mistakes = list(getattr(snapshot, "recent_mistakes", []) or [])[-4:]
    reconcile_quiz_history(card, session.quiz_history or [])
    if any(step.agent_role == "assessment" for step in plan.steps):
        concept = understanding.concept or understanding.subject or "当前知识点"
        loop_id = f"assessment:{concept}"
        if not any(loop.id == loop_id and loop.status == "resolved" for loop in card.open_loops):
            card.upsert_loop(OpenLoop(
                id=loop_id, kind="student_response",
                description=f"等待学生完成「{concept}」检测题并提交作答",
                created_turn=session.round_count()))
            card.pending_assessment = True
    card.updated_at = time.time()
    session.context_card = card.to_dict()
    rendered = card.render()
    trace.log("session_learning_card", phase="pre", chars=len(rendered),
              open_loops=sum(1 for x in card.open_loops if x.status == "pending"),
              active_skills=card.active_skill_ids)
    return rendered


def _finalize_session_learning_card(session: TutorSession, plan: TaskPlan,
                                    final_tool_calls: list[dict[str, Any]], trace) -> None:
    from ..core.session_learning_card import SessionLearningCard, reconcile_quiz_history
    card = SessionLearningCard.from_dict(session.context_card)
    for tc in final_tool_calls:
        if tc.get("name") not in {"generate_quiz", "fit_quiz"}:
            continue
        result = tc.get("result") or {}
        data = result.get("data", {}) if isinstance(result, dict) else {}
        for q in data.get("questions", []) if isinstance(data, dict) else []:
            if isinstance(q, dict):
                qid = str(q.get("id", "")) or str(q.get("stem", ""))[:24]
                if qid and qid not in card.unanswered_question_ids:
                    card.unanswered_question_ids.append(qid)
        if result.get("status") in {"success", "partial"}:
            ref = f"quiz_history:{len(session.quiz_history)}"
            if ref not in card.source_refs: card.source_refs.append(ref)
    reconcile_quiz_history(card, session.quiz_history or [])
    ts = TaskState.from_dict(session.supervisor_state)
    card.completed_steps = list(ts.completed)[-6:]
    card.remaining_steps = list(ts.remaining)[-6:]
    card.source_refs = card.source_refs[-12:]
    card.updated_at = time.time()
    session.context_card = card.to_dict()
    trace.log("session_learning_card", phase="post",
              unanswered=len(card.unanswered_question_ids),
              open_loops=sum(1 for x in card.open_loops if x.status == "pending"),
              completed=len(card.completed_steps), remaining=len(card.remaining_steps))


# --- main entry point -------------------------------------------------------

async def run(
    user_message: str,
    session: TutorSession,
    tools: list,
    llm: AsyncLLMClient | None = None,
    progress_cb: Callable[[str], Any] | None = None,
    lang: str = "zh",
    output_language: str | None = None,
   attachments: list[dict] | None = None,
   student_id: str = "",
) -> AsyncGenerator[dict[str, Any], None]:
    """Run one Supervisor turn, yielding SSE events (V1-compatible surface).

    Stages emit step events: understanding / planning, then executor forwards
    thinking/answer/tool_*/step. The supervisor persists the turn and emits the
    final done event (executor's internal done is consumed, not forwarded)."""
    llm = llm or get_llm()
    trace = Trace()
    # 阶段D：记录本回合使用的 prompt 版本（注册表 active 版本），让每次
    # 回答可溯源 prompt 版本（与 V1 chat_turn 的 turn_start 对齐）。
    from ..prompts.registry import active_versions
    from .skill_runtime.registry import registry as skill_registry
    session._turn_material_cache_enabled = True
    session.__dict__.pop("_turn_merged_knowledge_cache", None)
    # P6-C1：可见教材/文件清单进 trace——「工作区教材没生效」类问题的诊断面。
    try:
        from ..core.workspace import merged_knowledge_files as _mkf
        _vf, _vn = _mkf(session)
        _visible_files = [f.get("id", "") for f in _vf][:10]
    except Exception:
        _visible_files = []
    trace.log("turn_start", user_query=user_message, grade=session.grade,
              mode="supervisor", prompt_versions=active_versions(),
              skill_versions=skill_registry.active_versions(),
              workspace_id=getattr(session, "workspace_id", "") or "",
              visible_file_ids=_visible_files)

    # M0: resolve the student namespace key. Priority: explicit param > session
    # > DEFAULT_STUDENT_ID (guest). Bound to the session on first use so it
    # persists across turns and resumed conversations.
    from .student_model.store import DEFAULT_STUDENT_ID
    sid = student_id or session.student_id or DEFAULT_STUDENT_ID
    if not session.student_id:
        session.student_id = sid

    # --- 1. task understanding ---
    yield {"type": "step", "step": "understanding"}
    try:
        understanding = await understand(user_message, session, llm)
    except Exception as e:
        trace.log("supervisor_understand_error", message=str(e))
        understanding = TaskUnderstanding(intent=TaskType.EXPLAIN,
                                          concept=user_message[:30],
                                          requires_tools=False, source="fallback")
    trace.log("supervisor_understanding", **understanding.to_dict())

    # --- 2. student snapshot ---
    snapshot = derive_snapshot(session)
    trace.log("supervisor_snapshot", **snapshot.to_dict())

    # --- 2b. M10 Skill decision shadow ------------------------------------
    # Contract-aware selection is recorded before the legacy-compatible planner
    # runs.  In the default shadow mode it cannot change the answer, which lets
    # routing quality be evaluated safely from real traces before gated rollout.
    task_frame = None
    skill_decision = None
    if settings.skill_runtime_mode != "off":
        from .skill_runtime import build_task_frame, decide as decide_skill
        has_history = bool(session.messages or session.compaction or session.quiz_history)
        # P3: 本回合是否选用了教材（诊断信号，写 trace，不改门控）。
        from ..core.workspace import merged_knowledge_files as _mkf
        _dec_files, _ = _mkf(session)
        _has_textbook = bool(_visible_textbooks(session, _dec_files))
        task_frame = build_task_frame(
            user_message, understanding, snapshot, has_history=has_history,
            has_attachments=bool(attachments),
            has_textbook=_has_textbook,
            has_visible_materials=bool(_dec_files),
        )
        skill_decision = decide_skill(task_frame)
        trace.log("skill_task_frame", runtime_mode=settings.skill_runtime_mode, **task_frame.to_dict())
        trace.log("skill_decision", runtime_mode=settings.skill_runtime_mode,
                  **skill_decision.to_dict())

    # --- 3. planning ---
    yield {"type": "step", "step": "planning"}
    try:
        plan, goal = await make_plan(understanding, snapshot, session, llm)
    except Exception as e:
        trace.log("supervisor_plan_error", message=str(e))
        from .planner import _rule_plan, _goal_from
        plan = TaskPlan(steps=_rule_plan(understanding, snapshot).steps, source="fallback")
        goal = _goal_from(understanding)
    trace.log("supervisor_plan", source=plan.source, steps=[s.to_dict() for s in plan.steps],
              goal=goal)

    # --- 3b. V3 student-aware adaptation (soft strategy) ---
    strategy, adaptation_recap = await _adapt_for_turn(
        understanding, snapshot, session, trace, llm)

    # M10/M3 bridge: if the teaching strategy explicitly asks for a closing
    # check, make its assessment Skill part of the executable plan. This keeps
    # the dynamic Skill Card list aligned with the teaching directive.
    if settings.skill_runtime_mode != "off":
        plan = _enrich_plan_with_strategy_check(
            plan, strategy, understanding, tools, trace, grade=session.grade,
            focus=user_message,
            material_grounding_required=bool(
                task_frame and task_frame.material_grounding_required))

    # M10 gated mode: enforce declared preconditions after strategy enrichment,
    # so dynamically added assessment steps are validated too.
    if (settings.skill_runtime_mode == "gated" and task_frame is not None
            and skill_decision is not None):
        from .skill_runtime import gate_plan
        gate = gate_plan(plan, task_frame, skill_decision)
        plan = gate.plan
        trace.log("skill_gate", **gate.to_dict())

    # Explicit response constraints are user contracts and remain active even
    # when the Skill runtime is off or a planner proposed extra assessment.
    plan = _apply_response_constraints_to_plan(plan, understanding, trace)

    # --- 3c. Phase 3: learning-path planning (intent=plan only) ---
    if understanding.intent and understanding.intent.value == "plan":
        curriculum_recap = _plan_learning_path(understanding, session, trace)
        if curriculum_recap:
            adaptation_recap = (adaptation_recap + "\n" + curriculum_recap).strip()

    # --- 3d. M5: knowledge-intelligence soft directive (ontology hints) ---
    # A mandatory material-grounded turn already has a deterministic
    # knowledge_search preamble. Running M5's ontology/content resolver again
    # would duplicate retrieval work and add latency before the real answer.
    if task_frame is not None and task_frame.material_grounding_required:
        knowledge_recap = ""
        trace.log("knowledge_directive_skipped",
                  reason="mandatory_retrieval_already_planned")
    else:
        knowledge_recap = await _knowledge_directive_for_turn(understanding, session, trace)
        if knowledge_recap:
            adaptation_recap = (adaptation_recap + "\n" + knowledge_recap).strip()

    # --- 3e. M6: memory-intelligence JIT retrieval (past experience) ---
    memory_recap = _memory_directive_for_turn(understanding, session, trace)
    if memory_recap:
       adaptation_recap = (adaptation_recap + "\n" + memory_recap).strip()

    # --- 3f. M7: evaluation-intelligence advisory (past failure patterns) ---
    evaluation_recap = _evaluation_directive_for_turn(understanding, session, trace)
    if evaluation_recap:
       adaptation_recap = (adaptation_recap + "\n" + evaluation_recap).strip()

    # --- 3g. M8: UX-intelligence advisory (how to express) ---
    # PURE-READ: builds the "[交互智能·...]" block from the UX profile + the
    # most recent feedback + a once-per-milestone motivation nudge. Advisory
    # only; never alters content correctness. Mirrors 3d/3e/3f.
    ux_recap = _ux_directive_for_turn(understanding, session, trace)
    if ux_recap:
        adaptation_recap = (adaptation_recap + "\n" + ux_recap).strip()

    # --- 3h. M9: learning-orchestration advisory (long-term plan + today) ---
    # PURE-READ: builds the "[编排智能·...]" block from the student's goal,
    # milestones, today's tasks, and SRS-due reviews. Advisory only; never
    # alters content correctness. Mirrors 3d/3e/3f/3g.
    orchestration_recap = _orchestration_directive_for_turn(understanding, session, trace)
    if orchestration_recap:
        adaptation_recap = (adaptation_recap + "\n" + orchestration_recap).strip()

    # --- 3i. P2: 软指令仲裁——多层 advisory 同权叠加时的显式优先级 + 冲突收敛 ---
    if adaptation_recap:
        adaptation_recap = _arbitrate_directives(adaptation_recap, strategy, sid)

    # --- 4. context assembly (GSSC) ---
    chosen = (output_language or session.output_language or "").lower()
    forced = chosen in ("zh", "en")
    answer_lang = chosen if forced else "zh"
    from ..core.workspace import merged_knowledge_files
    _merged_files, file_names = merged_knowledge_files(session)
    # P3: 反查本回合可见教材（工作区/会话选入的 file_id → textbook 记录），
    # 注入 [当前教材] preamble 块，让回答优先依据所选教材、引用标注页码。
    _textbooks = _visible_textbooks(session, _merged_files)
    preamble = grade_preamble(session.grade, bool(_merged_files),
                              file_names, answer_lang=answer_lang, forced=forced,
                              textbooks=_textbooks)
    att = _attachment_context(session)
    if att:
        preamble += "\n" + att
    preamble += _workspace_memory_block(session)
    learning_card_note = _prepare_session_learning_card(
        session, understanding, snapshot, plan, goal, strategy, trace)
    if learning_card_note:
        preamble += "\n" + learning_card_note
    # W3/D11: 单次学习结束/暂离后回来 → 注入一行上次小结+恢复锚点。
    # 30 分钟内的连续提问不打断（同一次学习）。
    try:
        from .teaching_engine.session_summary import resume_preamble_line
        summary_note = resume_preamble_line(session)
        if summary_note:
            preamble += "\n" + summary_note
            trace.log("session_summary_resumed")
    except Exception:
        pass

    history, compacted = await _maybe_compact(session, preamble, llm, trace)
    trace.log("context", l3_tokens=history_tokens(history),
              history_msgs=len(history), compaction=compacted)
    messages = build_context(TUTOR_SYSTEM, preamble, history, user_message, _status_recap(session))
    _user_entry = {"role": "user", "content": user_message}
    if attachments:
        _user_entry["attachments"] = attachments
    session.messages.append(_user_entry)

    # V3: student-intelligence soft guidance as a system note (after user msg,
    # before execute). Advisory only; never blocks the LLM.
    if adaptation_recap:
        messages.append({"role": "system", "content": adaptation_recap})
    response_note = _response_constraint_note(understanding)
    if response_note:
        messages.append({"role": "system", "content": response_note})
        trace.log("response_constraints",
                  format=getattr(understanding, "response_format", ""),
                  allow_followup_assessment=getattr(
                      understanding, "allow_followup_assessment", True))

    # M10: inject only the skills referenced by this plan.  The full registry is
    # never dumped into the prompt, keeping tool choice focused and maintainable.
    planned_skill_ids = [sid for step in plan.steps for sid in step.skill_ids]
    skill_note = (skill_cards_preamble(planned_skill_ids)
                  if settings.skill_runtime_mode != "off" else "")
    if skill_note:
        messages.append({"role": "system", "content": skill_note})
        trace.log("skill_cards_injected", skill_ids=list(dict.fromkeys(planned_skill_ids)))

    from .reasoning_narrator import build_reasoning_events
    public_reasoning_events = build_reasoning_events(understanding, plan, strategy)
    public_reasoning = "\n\n".join(event.content for event in public_reasoning_events)
    for event in public_reasoning_events:
        trace.log("reasoning_summary", chars=len(event.content),
                  intent=understanding.intent.value, stage=event.stage,
                  level=event.level)
        yield {"type": "thinking", "content": event.content + "\n\n",
               "is_delta": True, "summary": True,
               "stage": event.stage, "level": event.level}

    # --- 5. execute (forward events, capture the result) ---
    final_answer = ""
    final_thinking = ""
    raw_thinking = ""
    final_tool_calls: list[dict[str, Any]] = []
    executor_done = False
    live_thinking_chars = 0  # live-display reasoning streamed this turn (non-summary)
    # Reconstruct V1's [{name, result}] tool-call list by pairing tool_start
    # with the tool_result that immediately follows it. executor emits them
    # strictly interleaved (start -> result -> start -> result ...), so the
    # last appended entry is always the one a tool_result belongs to. This
    # preserves quiz payloads in session.messages for history reload (V1 parity).
    async for ev in execute(messages, session, tools, plan, llm, trace, progress_cb,
                            search_queries=understanding.search_queries):
        if ev["type"] == "done":
            executor_done = True
            final_answer = ev.get("answer", "")
            final_thinking = public_reasoning
            # The raw provider reasoning rides the done event for stats; it is
            # never streamed or persisted directly (hidden-CoT stance).  The
            # real_summary level turns it into a student-readable digest below.
            raw_thinking = ev.get("thinking", "") or ""
            continue
        if ev["type"] == "tool_start":
            final_tool_calls.append({"name": ev.get("name"), "result": None})
        elif ev["type"] == "tool_result":
            if final_tool_calls:
                final_tool_calls[-1]["result"] = ev.get("result")
        elif ev["type"] == "thinking" and not ev.get("summary"):
            live_thinking_chars += len(ev.get("content") or "")
        yield ev

    if not executor_done:
        # executor returned without a done (shouldn't happen, but be safe):
        final_answer = final_answer or "（处理中断，请重试。）"

    # --- 5b. real_summary: digest the model's ACTUAL reasoning for the student ---
    # Template narration (understanding/planning) stays; this adds one bounded
    # LLM pass that compresses the raw reasoning into 3-5 readable sentences.
    # Gated to non-chitchat turns with real reasoning content; fail-open.
    # Skipped when live streaming already showed the full reasoning this turn
    # (REASONING_LIVE_MAX_CHARS>=0-on) — a second digest would duplicate it.
    if (executor_done and settings.reasoning_summary_level == "real_summary"
            and live_thinking_chars == 0
            and len(raw_thinking.strip()) >= 200
            and (not understanding.intent
                 or understanding.intent.value != "chitchat")):
        try:
            from .reasoning_summarizer import summarize_reasoning
            reflection = await summarize_reasoning(llm, raw_thinking)
        except Exception:
            reflection = ""
        if reflection:
            trace.log("reasoning_summary", chars=len(reflection),
                      intent=understanding.intent.value if understanding.intent else "",
                      stage="reflection", level="real_summary")
            yield {"type": "thinking", "content": reflection + "\n\n",
                   "is_delta": True, "summary": True,
                   "stage": "reflection", "level": "real_summary"}
            final_thinking = (final_thinking + "\n\n" + reflection).strip()

    # --- 6. persist + update state ---
    session.messages.append({"role": "assistant", "content": final_answer,
                             "thinking": final_thinking,
                             "toolCalls": [dict(tc) for tc in final_tool_calls]})
    _update_task_state(session, plan, goal, understanding.intent, final_tool_calls, trace)
    _finalize_session_learning_card(session, plan, final_tool_calls, trace)
    # Card answers recorded via /quiz/* while this turn was streaming must
    # survive this save (load-modify-save race guard).
    from ..core.quiz_attempts import merge_quiz_results_from_disk
    merge_quiz_results_from_disk(session)
    save_session(session)
    try:
        from .memory import get_memory_service
        from .memory.prompt_memory import load_state
        if load_state(sid).get("core_needs_llm"):
            task = asyncio.create_task(
                get_memory_service().maybe_compact_prompt_memory(sid, llm=get_llm()))
            _BG_TASKS.add(task)
            task.add_done_callback(_BG_TASKS.discard)
    except Exception:
        pass
    _persist_turn(session, user_message, final_answer, final_tool_calls, trace)
    await _maybe_update_workspace_memory(session, user_message, final_answer, final_tool_calls)
    from ..core.memory_safety import memory_safe_text
    safe_user_message = memory_safe_text(user_message)
    safe_final_answer = memory_safe_text(final_answer)

    # G4：M2 学生事件链已删除（评价经 journal 受理；教学日志在 6c）。

    # --- 6c. M3: record teaching_log turn (cross-turn strategy memory) ---
    # Closes the loop for the teaching engine: persists (mode, outcome) so the
    # next turn on the same concept can advance INTRODUCTION->EXPLANATION->...
    # Outcome is a best-effort read: CORRECT/WRONG if a quiz was graded inline,
    # else ENGAGED (taught, not assessed). Never breaks a turn.
    try:
        from .teaching_engine import is_enabled as te_enabled
        if te_enabled() and strategy is not None and understanding.intent \
                and understanding.intent.value != "chitchat":
            from .teaching_engine import TeachingOutcome, get_teaching_manager
            from .student_model.store import DEFAULT_STUDENT_ID
            outcome = TeachingOutcome.ENGAGED
            # peek at quiz tool results this turn for a CORRECT/WRONG signal
            for tc in final_tool_calls:
                res = tc.get("result")
                if isinstance(res, dict) and "verdict" in res:
                    v = str(res.get("verdict")).lower()
                    outcome = (TeachingOutcome.CORRECT if "correct" in v or v == "对"
                               else TeachingOutcome.WRONG if "wrong" in v or v == "错"
                               else TeachingOutcome.PARTIAL)
                    break
            ckey = (strategy.target_skill_id or understanding.concept or "")
            mode = strategy.mode if hasattr(strategy, "mode") else None
            if mode is not None and ckey:
                get_teaching_manager().record_turn(
                    sid, ckey, mode=mode, outcome=outcome,
                    note=(understanding.concept or "")[:40])
                trace.log("teaching_engine_log", concept=ckey,
                         mode=mode.value, outcome=outcome.value)
    except Exception as e:
        trace.log("teaching_engine_log_error", message=str(e))

    # --- 6c-bis. W3/D11: 课堂小结与恢复锚点（确定性骨架，每轮重建零 LLM）---
    # 润色仅在本轮有作答判定且 STRUCTURED_ASSESSMENT_MODE=active 时发生；
    # 无测评的会话如实写「已讲解，待验证」。
    try:
        from .teaching_engine.session_summary import (build_session_summary,
                                                      polish_summary)
        turn_had_assessment = any(
            isinstance(tc.get("result"), dict) and "verdict" in (tc.get("result") or {})
            for tc in final_tool_calls)
        summary = build_session_summary(session)
        if summary is not None:
            from ..core.config import settings as _settings
            if (turn_had_assessment
                    and _settings.structured_assessment_mode == "active"
                    and llm is not None):
                polished = await polish_summary(summary, llm)
                if polished is not None:
                    summary["summary_line"], summary["resume_line"] = polished
                    summary["polished"] = True
            session.learning_summary = summary
            # save_session 已在 6 步发生过；带上同款 load-modify-save 竞态守卫
            # 再落一次，把小结写进会话文件。
            from ..core.quiz_attempts import merge_quiz_results_from_disk
            merge_quiz_results_from_disk(session)
            save_session(session)
    except Exception as e:
        trace.log("session_summary_error", message=str(e))


    # --- 6d. M6: consolidate this turn's signals into long-term memory ---
    # Appends episodic memories immediately (zero LLM), folds procedural
    # strategy outcomes. Consumes the SAME events list as 6b (no recompute).
    # The periodic LLM consolidation runs separately (frequency-gated).
    evs: list = []      # G4：学生事件链已删；M6 只收作答事件（compat 空）
    try:
        _memory_consolidate_turn(
            sid, session.session_id, session.workspace_id,
            understanding, safe_user_message,
           safe_final_answer, final_tool_calls, evs, strategy, trace)
    except Exception as e:
       trace.log("memory_consolidate_hook_error", message=str(e))


    # --- 6e. M7: evaluate this turn (capture trace + learning gain) ---
    # PURE-OBSERVER: captures a TurnTrace (concept/mode/outcome/tools/gain),
    # runs the rule-based failure diagnosis, and bumps the advisor
    # frequency-gate counter. Periodic LLM advisory runs separately (gated).
    # Never writes back to M2/M3/M6. Never breaks a turn.
    try:
        _evaluation_record_turn(
            sid, understanding, safe_user_message, session,
            strategy, final_tool_calls, safe_final_answer, trace)
    except Exception as e:
        trace.log("evaluation_hook_error", message=str(e))
    # periodic LLM advisory (async, frequency-gated). Runs after the trace
    # is captured; defers when no LLM or the gate is closed. Never breaks a turn.
    try:
        from .evaluation import get_evaluation_service, is_enabled as ev_enabled
        if ev_enabled():
            proposal = await get_evaluation_service().maybe_advise(
                sid, llm=llm)
            if proposal is not None:
                trace.log("evaluation_advise", target=proposal.target,
                          confidence=proposal.confidence)
    except Exception as e:
        trace.log("evaluation_advise_error", message=str(e))
    # --- 6f. M8: capture this turn's UX signals (engagement + feedback) ---
    # PURE-FUNCTION, zero LLM: classifies any UX feedback in the user message,
    # folds the answer length into the UX profile, and appends UXEvents. Never
    # writes any M2/M3/M5/M6/M7 state. Mirrors 6b-6e.
    try:
        _ux_record_turn(sid, understanding, safe_user_message,
                        session, safe_final_answer, final_tool_calls, trace)
    except Exception as e:
        trace.log("ux_record_hook_error", message=str(e))
    # --- 6g. M9: capture orchestration signals (exposure + habit + progress) ---
    # PURE-FUNCTION, zero LLM: registers SRS exposure for the concept touched
    # this turn, refreshes habit stats from M6 (read-only), and checks
    # goal-progress checkpoints from M2 mastery (read-only). Recall QUALITY no
    # longer flows through here (W4/A08) — committed quiz verdicts reach M9
    # via record_quiz_evidence on the grading endpoints. Never writes any
    # M2/M3/M5/M6/M7/M8 state. Mirrors 6b-6f.
    try:
        _orchestration_record_turn(
            sid, understanding, safe_user_message, session,
            safe_final_answer, trace)
    except Exception as e:
        trace.log("orchestration_record_hook_error", message=str(e))
    # --- 7. final done event ---
    session.__dict__.pop("_turn_merged_knowledge_cache", None)
    session.__dict__.pop("_turn_material_cache_enabled", None)
    yield {"type": "done", "thinking": final_thinking, "answer": final_answer,
           "tool_calls": _lite_tool_calls_fn(final_tool_calls), "trace_id": trace.run_id,
           "trace_summary": trace.summary()}
