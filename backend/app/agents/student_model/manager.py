"""StudentModel facade: the single entry point the rest of the app uses.

Wires together store (persistence) + graph + mastery + memory + events +
adaptation into one object with a small surface:

    sm = get_student_model(student_id)
    sm.record_events(events)         # fold a turn's events in, persist
    snapshot = sm.snapshot(...)      # StudentSnapshot (supervisor-ready)
    strategy = sm.adapt(concept,...) # TeachingStrategy (planner-ready)
    sm.record_quiz_result(...)       # convenience for the quiz endpoint

All methods degrade gracefully: a student-model failure never propagates into
a chat turn (callers wrap in try/except, but we also guard internally). The
module is toggled by STUDENT_MODEL_MODE (default on); when off, snapshot()
returns the lightweight V2 view and adapt() returns a no-op strategy, so the
Supervisor keeps working exactly as before.
"""
from __future__ import annotations

import os
import time
from typing import Any

from .adaptation import TeachingStrategy, adapt
from .events import EventCollector, EventProcessor
from .mastery import MasteryTracker
from .skill_graph import SkillGraph, SkillNode
from .state import (ConceptRecord, ConceptState, EventType, LearningEvent,
                    StudentProfile)
from .store import (DEFAULT_STUDENT_ID, StudentStateBlob, append_events,
                    load_blob, save_blob)


def is_enabled() -> bool:
    """Whether the student-model layer is active (default on)."""
    return os.getenv("STUDENT_MODEL_MODE", "1") not in ("0", "false", "False", "off")


class StudentModel:
    """Per-student intelligence: profile + mastery + memory, persisted."""

    def __init__(self, student_id: str = DEFAULT_STUDENT_ID) -> None:
        self.student_id = student_id
        self._loaded = False
        self.profile: StudentProfile = StudentProfile(id=student_id)
        self.mastery: MasteryTracker = MasteryTracker()
        self.memory: dict[str, ConceptRecord] = {}
        self.graph: SkillGraph = SkillGraph()
        self._blob: StudentStateBlob | None = None

    # --- lifecycle ------------------------------------------------------
    def load(self) -> "StudentModel":
        """Load persisted state; idempotent."""
        if self._loaded:
            return self
        try:
            blob = load_blob(self.student_id)
            self._blob = blob
            self.profile = blob.profile
            self.mastery = MasteryTracker(blob.mastery)
            self.memory = {
                k: v if isinstance(v, ConceptRecord) else ConceptRecord.from_dict(v)
                for k, v in blob.memory.items()
            }
        except Exception:
            # corrupt/missing -> start fresh, never break a turn
            self._blob = StudentStateBlob(profile=StudentProfile(id=self.student_id))
        self._merge_knowledge_graph()
        self._loaded = True
        return self

    def _merge_knowledge_graph(self) -> None:
        """M5.8: SkillGraph draws its full node set + prerequisite edges from
        the M5 knowledge ontology when that layer is on (single source of
        truth for learning order; skill_graph_seed remains the fallback when
        M5 is off). Incremental and additive only: legacy seed ids overlap
        with identical fields, so merging ADDS the ontology's nodes (incl.
        this student's M5.7 custom graphs) and turns on strict matching so
        off-syllabus concepts still get precise floating nodes. Never raises.
        """
        try:
            from ..knowledge import get_knowledge_service, is_enabled as _kn_on
            from ..knowledge.schema import EdgeType
            if not _kn_on():
                return
            g = get_knowledge_service().graph_for(self.student_id)
            prereqs: dict[str, list[str]] = {}
            for e in g.edges:
                if e.type == EdgeType.PREREQUISITE:
                    prereqs.setdefault(e.target, []).append(e.source)
            added = False
            for n in g.nodes.values():
                if n.kind == "chapter":
                    continue
                existing = self.graph.nodes.get(n.id)
                if existing is not None:
                    # widen the legacy seed node's match surface with M5's
                    # curated aliases (bridge.node_aliases' promise): without
                    # them a pack node named exactly "摩擦力" would out-score
                    # the legacy "摩擦力与受力分析"(alias 摩擦力) and split
                    # BKT attribution off the stable legacy id
                    if n.aliases:
                        merged_aliases = list(dict.fromkeys(
                            [*existing.aliases, *n.aliases]))
                        if merged_aliases != existing.aliases:
                            existing.aliases = merged_aliases
                    if n.level and not existing.level:
                        existing.level = n.level
                    continue
                self.graph.nodes[n.id] = SkillNode(
                    id=n.id, name=n.name, subject=n.subject,
                    prerequisites=[p for p in prereqs.get(n.id, [])
                                   if p in g.nodes],
                    difficulty=n.difficulty, aliases=list(n.aliases),
                    level=n.level)
                added = True
            if added:
                self.graph.strict_match = True
                self.graph.invalidate_traversal_cache()
        except Exception:
            pass

    def _persist(self) -> None:
        try:
            blob = StudentStateBlob(
                profile=self.profile,
                mastery=self.mastery.to_dict(),
                memory=self.memory,  # ConceptRecord objects; store serializes
            )
            self.profile.updated_at = time.time()
            save_blob(self.student_id, blob)
        except Exception:
            pass

    # --- writing --------------------------------------------------------
    def record_events(self, events: list[LearningEvent]) -> None:
        """Fold events into profile/mastery/memory and persist.

        Defensive: any failure is swallowed. The events log is appended first
        (best-effort black box) so evidence is never lost even if processing
        throws.
        """
        if not events:
            return
        try:
            append_events(self.student_id, events)
        except Exception:
            pass
        try:
            self.load()
            self._merge_knowledge_graph()   # pick up M5.7 custom graphs built since load
            proc = EventProcessor(self.profile, self.mastery, self.memory, self.graph)
            proc.process_all(events)
            self.profile.last_active = time.time()
            self._persist()
        except Exception:
            pass

    def record_quiz_result(self, *, concept: str, correct: bool,
                           skill_id: str = "", knowledge_point: str = "",
                           subject: str = "", note: str = "",
                           verdict: str = "", confidence: float | None = None,
                           attempt_id: str = "",
                           structured: dict | None = None) -> None:
        """Convenience: record a single quiz-graded event (quiz endpoint).

        W2: ``verdict`` distinguishes partial from binary wrong for the BKT
        step (A04); ``confidence`` is the evidence gate's capped max_confidence
        (A05); ``attempt_id`` makes re-processing the SAME submission a no-op
        — the same evidence must not influence M2 twice (A05).
        W3/D06: ``structured`` rides the event payload as additive keys
        (criterion_results / observed_capabilities / assistance / rubric_id)
        for the v2 capability projection; the legacy BKT handler ignores it."""
        if attempt_id and self._attempt_recorded(attempt_id):
            return
        col = EventCollector()
        col.quiz_graded(concept, correct, skill_id=skill_id,
                        knowledge_point=knowledge_point, subject=subject,
                        note=note, verdict=verdict, confidence=confidence,
                        attempt_id=attempt_id, structured=structured)
        self.record_events(col.drain())

    def _attempt_recorded(self, attempt_id: str) -> bool:
        """Whether a quiz_graded event with this attempt id already exists."""
        try:
            from .store import read_events
            for ev in read_events(self.student_id):
                if (ev.type == EventType.QUIZ_GRADED
                        and str(ev.payload.get("attempt_id") or "") == attempt_id):
                    return True
        except Exception:
            return False
        return False

    def update_learning_style(self, *, preference: str = "",
                              explanation_depth: str = "") -> bool:
        """learning_style 的唯一生产写入入口（P0: style_inference 调用）。

        事件管线（EventProcessor）不承载风格翻转——它不是学业事件，而是
        M8 体验反馈的折叠结果。有变化才落盘；返回是否发生了翻转。
        """
        ls = self.profile.learning_style
        changed = False
        if preference and preference != ls.preference:
            ls.preference = preference
            changed = True
        if explanation_depth and explanation_depth != ls.explanation_depth:
            ls.explanation_depth = explanation_depth
            changed = True
        if changed:
            self.profile.updated_at = time.time()
            self._persist()
        return changed

    def note_concept_taught(self, *, concept: str, skill_id: str = "",
                            subject: str = "", brief: str = "") -> None:
        col = EventCollector()
        col.concept_taught(concept, skill_id=skill_id, subject=subject, brief=brief)
        self.record_events(col.drain())

    # --- reading --------------------------------------------------------
    def mastery_view(self) -> dict[str, dict[str, Any]]:
        return self.mastery.to_dict()

    def weak_skills(self, *, limit: int = 5) -> list[str]:
        """Skills the student has seen but not mastered, weakest first."""
        seen = []
        for sid, m in self.mastery.records.items():
            if m.attempts > 0 and m.p_known < 0.6:
                seen.append((sid, m.p_known, m.last_review))
        seen.sort(key=lambda x: (x[1], -x[2]))
        return [sid for sid, _, _ in seen[:limit]]

    def strong_skills(self, *, limit: int = 5) -> list[str]:
        seen = []
        for sid, m in self.mastery.records.items():
            if m.p_known >= 0.7:
                seen.append((sid, m.p_known))
        seen.sort(key=lambda x: -x[1])
        return [sid for sid, _ in seen[:limit]]

    def snapshot(self, *, grade: str = "", current_subject: str = "",
                 has_materials: bool = False, material_count: int = 0,
                 material_names: list[str] | None = None,
                 recent_quiz_count: int = 0) -> dict[str, Any]:
        """Build a StudentSnapshot dict (supervisor-ready, V3-extended).

        Adds the V3 fields (goals / weak_skills / strong_skills / mastery_map /
        learning_style / recent_mistakes / unfinished_prereqs) on top of the
        V2 lightweight view. Returns a plain dict so agents/state.py can stay
        decoupled from this package (it just reads the keys).
        """
        self.load()
        grade = grade or self.profile.grade or "本科"
        weak = self.weak_skills()
        strong = self.strong_skills()
        # map skill ids -> short mastery summaries for the prompt
        mastery_map: dict[str, float] = {}
        for sid, m in list(self.mastery.records.items())[:20]:
            mastery_map[sid] = round(m.p_known, 2)
        recent_mistakes: list[str] = []
        for m in list(self.mastery.records.values()):
            recent_mistakes.extend(m.mistakes[-1:])
        # top unfinished prerequisites for the current subject target
        prereqs: list[str] = []
        if current_subject:
            try:
                nxt = self.graph.next_learnable(current_subject, self.mastery_view(), limit=1)
                if nxt:
                    unmet = self.graph.unmet_prerequisites(nxt[0].id, self.mastery_view())
                    prereqs = [n.name for n in unmet[:3]]
            except Exception:
                pass
        return {
            "grade": grade,
            "has_materials": has_materials,
            "material_count": material_count,
            "material_names": list(material_names or []),
            "recent_quiz_count": recent_quiz_count,
            "recent_weak_points": [self.graph.get(w).name if self.graph.get(w) else w
                                   for w in weak],
            "conversation_topic_hint": None,
            # --- V3 additions ---
            "goals": list(self.profile.goals[-6:]),
            "current_subject": current_subject,
            "weak_skills": weak,
            "strong_skills": strong,
            "mastery_map": mastery_map,
            "learning_style": self.profile.learning_style.to_dict(),
            "recent_mistakes": recent_mistakes[-4:],
            "unfinished_prereqs": prereqs,
        }

    def adapt(self, concept: str, subject: str, *, intent: str = "explain",
              grade: str = "") -> TeachingStrategy:
        """Produce a TeachingStrategy for the given teaching target.

        M3 routing: when TEACHING_ENGINE_MODE is on (default), delegate to the
        TeachingEngine (the richer mode-based engine with cross-turn memory).
        Otherwise fall back to the legacy adaptation.adapt() rule path. Both
        return a TeachingStrategy with the same field surface.
        """
        self.load()
        grade = grade or self.profile.grade or "本科"
        try:
            from ..teaching_engine import is_enabled as te_enabled
            if te_enabled():
                # assemble a TeachingContext from this student's live state and
                # hand it to the engine. The engine is PURE-READ over us.
                from ..teaching_engine import (TeachingContext,
                                               get_teaching_manager,
                                               previous_mode_for)
                target = self.graph.match_concept(concept) if concept else None
                mview = self.mastery.to_dict()
                mastery_p = 0.0
                unmet, unmet_names = [], []
                if target is not None:
                    rec = self.mastery.get(target.id)
                    mastery_p = rec.p_known if rec else 0.0
                    unmet = self.graph.unmet_prerequisites(target.id, mview)
                    unmet.sort(key=lambda n: float((mview.get(n.id) or {}).get("p_known", 0)))
                    unmet_names = [n.name for n in unmet[:3]]
                miscon = []
                mt = []
                crec = self.memory.get(target.id) if target else None
                if crec:
                    miscon = list(crec.misconceptions[-2:])
                    mt = list(getattr(crec, "mistake_types", [])[-2:])
                mistakes = []
                mrec = self.mastery.get(target.id) if target else None
                if mrec:
                    mistakes = list(mrec.mistakes[-3:])
                ckey = target.id if target else (concept or "")
                pm, po, turns = previous_mode_for(self.student_id, ckey)
                ctx = TeachingContext(
                    concept=concept, subject=subject, task_type=intent, grade=grade,
                    mastery=mastery_p, unmet_prereqs=unmet, unmet_prereq_names=unmet_names,
                    mistakes=mistakes, misconceptions=miscon, mistake_types=mt,
                    learning_style=self.profile.learning_style.to_dict(),
                    previous_mode=pm, previous_outcome=po, turns_on_concept=turns)
                return get_teaching_manager().adapt(ctx, student_id=self.student_id)
            return adapt(concept, subject, grade, self.profile, self.mastery,
                         self.memory, self.graph, intent=intent)
        except Exception:
            return TeachingStrategy(target_concept=concept, rationale="适配降级")


# --- process-level cache (single student first) ---------------------------

_CACHE: dict[str, StudentModel] = {}


def get_student_model(student_id: str = DEFAULT_STUDENT_ID) -> StudentModel:
    """Return a cached StudentModel for the student (loads on first access)."""
    sm = _CACHE.get(student_id)
    if sm is None:
        sm = StudentModel(student_id).load()
        _CACHE[student_id] = sm
    return sm


def record_quiz_result(*, concept: str, correct: bool, session_id: str = "",
                       skill_id: str = "", knowledge_point: str = "",
                       subject: str = "", note: str = "",
                       student_id: str = "",
                       verdict: str = "", confidence: float | None = None,
                       attempt_id: str = "",
                       structured: dict | None = None) -> None:
    """Process-level convenience used by the quiz grading endpoint.

    M0：student_id 为身份命名空间（/quiz/grade 经 resolve_student_id 透传），
    缺省回退 DEFAULT_STUDENT_ID（游客）。Returns without raising on any
    failure so grading never breaks.

    W2/A04/A05：verdict（partial 不做二元负向 BKT 更新）、confidence（证据
    门 cap 后的 max_confidence 审计）与 attempt_id（同一作答幂等，不重复
    影响 M2）为可选增量参数，旧调用方不受影响。W3/D06：structured 为事件
    payload 的增量键（v2 投影输入），legacy 处理器忽略。
    """
    try:
        if not is_enabled():
            return
        sm = get_student_model(student_id or DEFAULT_STUDENT_ID)
        sm.record_quiz_result(concept=concept, correct=correct, skill_id=skill_id,
                              knowledge_point=knowledge_point, subject=subject,
                              note=note, verdict=verdict, confidence=confidence,
                              attempt_id=attempt_id, structured=structured)
    except Exception:
        pass
