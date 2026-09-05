"""Event-driven student-model updates.

This is the heart of "student intelligence accrues cheaply": the Supervisor
and the quiz-grading endpoint emit LearningEvents as side-effects of normal
turns; a rule-based EventProcessor (NO per-turn LLM) folds each event into
profile / mastery / memory. A scheduled LLM consolidation can later rewrite
the profile summary, but the per-turn path stays deterministic and offline.

Event payloads (contract):
  quiz_graded:    {skill_id?, concept, correct(bool), knowledge_point?, note?}
  concept_taught: {skill_id?, concept, subject?, brief?}
  weakness_signaled: {concept, subject?, source("student"|"system"), note?}
  goal_set:       {goal, subject?}
  mastery_reset:  {skill_id}

The processor is idempotent in spirit: replaying the events log rebuilds the
same state. It never raises -- a malformed event is skipped, never aborts the
turn (protects the chat stream).
"""
from __future__ import annotations

from typing import Any

from .mastery import MasteryTracker
from .skill_graph import SkillGraph
from .state import (ConceptRecord, ConceptState, EventType, LearningEvent,
                    StudentProfile, cap_list)


class EventCollector:
    """Accumulates LearningEvents for one turn, then flushes them.

    Kept separate from the processor so a turn can batch-emit several events
    (e.g. one per graded question) and commit them once. The collector is a
    plain list wrapper -- no I/O.
    """
    def __init__(self) -> None:
        self.events: list[LearningEvent] = []

    def add(self, etype: EventType, payload: dict[str, Any] | None = None) -> LearningEvent:
        ev = LearningEvent(type=etype, payload=dict(payload or {}))
        self.events.append(ev)
        return ev

    def quiz_graded(self, concept: str, correct: bool, *, skill_id: str = "",
                    knowledge_point: str = "", subject: str = "", note: str = "",
                    verdict: str = "", confidence: float | None = None,
                    attempt_id: str = "",
                    structured: dict[str, Any] | None = None) -> None:
        payload = {
            "concept": concept, "correct": bool(correct),
            "skill_id": skill_id, "knowledge_point": knowledge_point,
            "subject": subject, "note": note,
        }
        # W2 additive audit keys: three-level verdict (partial is not a binary
        # wrong for the BKT step), gate-capped grading confidence, and the
        # attempt id used for idempotent re-submission handling.
        if verdict:
            payload["verdict"] = verdict
        if confidence is not None:
            payload["confidence"] = round(max(0.0, min(1.0, confidence)), 3)
        if attempt_id:
            payload["attempt_id"] = attempt_id
        # W3/D06 additive keys (v2 capability-projection input, §8.6.2):
        # criterion results + observed dimensions + assistance. The BKT
        # handler ignores them; nothing here may change legacy semantics.
        if isinstance(structured, dict):
            for key in ("criterion_results", "observed_capabilities",
                        "assistance", "rubric_id"):
                value = structured.get(key)
                if value:
                    payload[key] = value
        self.add(EventType.QUIZ_GRADED, payload)

    def concept_taught(self, concept: str, *, skill_id: str = "",
                       subject: str = "", brief: str = "") -> None:
        self.add(EventType.CONCEPT_TAUGHT, {
            "concept": concept, "skill_id": skill_id,
            "subject": subject, "brief": brief,
        })

    def weakness(self, concept: str, *, subject: str = "",
                 source: str = "system", note: str = "") -> None:
        self.add(EventType.WEAKNESS_SIGNALED, {
            "concept": concept, "subject": subject, "source": source, "note": note,
        })

    def goal(self, goal_text: str, *, subject: str = "") -> None:
        self.add(EventType.GOAL_SET, {"goal": goal_text, "subject": subject})

    def reset(self, skill_id: str) -> None:
        self.add(EventType.MASTERY_RESET, {"skill_id": skill_id})

    def drain(self) -> list[LearningEvent]:
        out, self.events = self.events, []
        return out


class EventProcessor:
    """Folds LearningEvents into profile / mastery / memory (rule-based).

    Holds references to the three mutable stores and mutates them in place.
    Pure functions of (event, state) -> state; deterministic + idempotent.
    """
    def __init__(self, profile: StudentProfile, mastery: MasteryTracker,
                 memory: dict[str, ConceptRecord], graph: SkillGraph) -> None:
        self.profile = profile
        self.mastery = mastery
        self.memory = memory
        self.graph = graph

    def process(self, event: LearningEvent) -> None:
        try:
            handler = self._HANDLERS.get(event.type)
            if handler:
                handler(self, event)
            self.profile.events_processed += 1
        except Exception:
            # never abort: a bad event is logged-and-skipped
            return

    def process_all(self, events: list[LearningEvent]) -> None:
        for ev in events:
            self.process(ev)

    # --- per-type handlers ----------------------------------------------

    def _resolve_skill(self, event: LearningEvent) -> tuple[str, str, str]:
        """Return (skill_id, concept, subject) for an event, auto-creating a
        graph node when the concept is unseeded."""
        p = event.payload
        concept = str(p.get("concept") or p.get("knowledge_point") or "").strip()
        subject = str(p.get("subject") or "")
        skill_id = str(p.get("skill_id") or "").strip()
        if not skill_id and concept:
            node = self.graph.ensure_node_for(concept, subject)
            skill_id = node.id
            if not concept:
                concept = node.name
        return skill_id, concept, subject

    def _on_quiz_graded(self, event: LearningEvent) -> None:
        p = event.payload
        skill_id, concept, subject = self._resolve_skill(event)
        correct = bool(p.get("correct", False))
        note = str(p.get("note") or "")
        # W2/A04: partial is NOT a binary wrong. updatePlan.md §8.6.3 forbids
        # both a full negative BKT update and an uncalibrated linear blend, so
        # a partial verdict leaves legacy BKT untouched while the observation
        # still counts in the running tally / misconception stats (the M3
        # REMEDIATION root signal, DESIGN §13).
        partial = str(p.get("verdict") or "") == "partial"
        if skill_id and not partial:
            self.mastery.record_observation(skill_id, correct, note=note or "")
        rec = self._touch_memory(skill_id or concept, concept, subject)
        rec.attempts += 1
        if correct:
            rec.correct += 1
        rec.last_review = event.ts
        # state reclassification based on the running tally
        rec.state = self._classify_state(rec)
        if not correct and note:
            rec.misconceptions = cap_list(rec.misconceptions + [note], 6)
            # Phase 2: classify the error so the teaching engine can target the
            # root cause. Pure rule-based; None when unclassifiable.
            try:
                from ..teaching_engine import diagnose
                mtype = diagnose(note, concept=concept, subject=subject)
                if mtype is not None:
                    rec.mistake_types = cap_list(rec.mistake_types + [mtype.value], 6)
            except Exception:
                pass
            # consistent wrong/partial -> weak point (partial is the REMEDIATION
            # root signal and must not be lost)
            self._push_weak(concept or skill_id)
        else:
            # consistently right -> strong point
            if rec.state == ConceptState.UNDERSTOOD:
                self._push_strong(concept or skill_id)
        if subject:
            self._push_subject(subject)

    def _on_concept_taught(self, event: LearningEvent) -> None:
        p = event.payload
        skill_id, concept, subject = self._resolve_skill(event)
        brief = str(p.get("brief") or "").strip()
        rec = self._touch_memory(skill_id or concept, concept, subject)
        if rec.state in (ConceptState.UNKNOWN,):
            rec.state = ConceptState.INTRODUCED
        if brief:
            rec.evidence = cap_list(rec.evidence + [f"已讲解：{brief[:60]}"], 6)
        rec.last_review = event.ts
        if subject:
            self._push_subject(subject)

    def _on_weakness_signaled(self, event: LearningEvent) -> None:
        p = event.payload
        concept = str(p.get("concept") or "").strip()
        subject = str(p.get("subject") or "")
        note = str(p.get("note") or "")
        if concept:
            self._push_weak(concept)
        skill_id, c, s = self._resolve_skill(event)
        rec = self._touch_memory(skill_id or concept, c or concept, subject)
        if rec.state == ConceptState.UNKNOWN:
            rec.state = ConceptState.PARTIAL
        if note:
            rec.evidence = cap_list(rec.evidence + [f"薄弱信号：{note[:60]}"], 6)
        if subject:
            self._push_subject(subject)

    def _on_goal_set(self, event: LearningEvent) -> None:
        p = event.payload
        goal_text = str(p.get("goal") or "").strip()
        subject = str(p.get("subject") or "")
        if goal_text and goal_text not in self.profile.goals:
            self.profile.goals = cap_list(self.profile.goals + [goal_text], 12)
        if subject:
            self._push_subject(subject)

    def _on_mastery_reset(self, event: LearningEvent) -> None:
        skill_id = str(event.payload.get("skill_id") or "")
        if skill_id:
            self.mastery.reset(skill_id)
            for key in (skill_id,):
                if key in self.memory:
                    self.memory[key].state = ConceptState.UNKNOWN
                    self.memory[key].attempts = 0
                    self.memory[key].correct = 0

    # --- helpers --------------------------------------------------------

    def _touch_memory(self, key: str, concept: str, subject: str) -> ConceptRecord:
        key = key or concept or "unknown"
        rec = self.memory.get(key)
        if rec is None:
            rec = ConceptRecord(skill_id=key, concept=concept)
            self.memory[key] = rec
        return rec

    @staticmethod
    def _classify_state(rec: ConceptRecord) -> ConceptState:
        """Map (attempts, correct) onto a ConceptState.

        Heuristic thresholds: needs >=2 attempts to leave PARTIAL; >=2 and
        >=80% correct -> UNDERSTOOD; >=2 and <=20% -> MISCONCEPTION.
        """
        if rec.attempts == 0:
            return rec.state if rec.state != ConceptState.UNKNOWN else ConceptState.INTRODUCED
        rate = rec.correct / rec.attempts
        if rec.attempts < 2:
            return ConceptState.PARTIAL
        if rate >= 0.8:
            return ConceptState.UNDERSTOOD
        if rate <= 0.2:
            return ConceptState.MISCONCEPTION
        return ConceptState.PARTIAL

    def _push_subject(self, subject: str) -> None:
        subject = subject.strip()
        if subject and subject not in self.profile.subjects:
            self.profile.subjects = cap_list(self.profile.subjects + [subject], 10)

    def _push_weak(self, concept: str) -> None:
        c = concept.strip()
        if c and c not in self.profile.weak_points:
            self.profile.weak_points = cap_list(self.profile.weak_points + [c], 20)

    def _push_strong(self, concept: str) -> None:
        c = concept.strip()
        if c and c not in self.profile.strong_points:
            self.profile.strong_points = cap_list(self.profile.strong_points + [c], 20)


# dispatch table (built after the class so methods are bound)
EventProcessor._HANDLERS = {
    EventType.QUIZ_GRADED: EventProcessor._on_quiz_graded,
    EventType.CONCEPT_TAUGHT: EventProcessor._on_concept_taught,
    EventType.WEAKNESS_SIGNALED: EventProcessor._on_weakness_signaled,
    EventType.GOAL_SET: EventProcessor._on_goal_set,
    EventType.MASTERY_RESET: EventProcessor._on_mastery_reset,
}
