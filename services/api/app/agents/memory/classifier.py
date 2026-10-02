"""Memory classifier: decide what to remember from a turn (rule-based, no LLM).

This is the per-turn classification layer -- it runs on EVERY turn that has
learning events, so it MUST be fast and deterministic (zero LLM). Rules are
always-on and cover the common cases. An optional LLM fallback exists but is
gated by a frequency threshold (not called every turn).

Output: a list of ClassificationResult describing what memory items to create.
Only items passing the importance threshold are written; the rest are dropped
(we do NOT save everything).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .schema import Importance, MemoryScope


@dataclass
class ClassificationResult:
    """One classified memory item to be written."""
    memory_type: str           # "episodic" | "semantic"
    event_type: str            # M2 EventType value (for episodic)
    summary: str               # narrative text
    concept: str = ""
    subject: str = ""
    score: float | None = None
    emotion: str = ""          # confident / confused / frustrated
    importance: float = Importance.NORMAL.value
    scope: MemoryScope = MemoryScope.GLOBAL
    semantic_category: str = ""  # for semantic memory only
    semantic_fact: str = ""      # for semantic memory only

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_type": self.memory_type,
            "event_type": self.event_type,
            "summary": self.summary,
            "concept": self.concept,
            "subject": self.subject,
            "score": self.score,
            "emotion": self.emotion,
            "importance": self.importance,
            "scope": self.scope.value,
            "semantic_category": self.semantic_category,
            "semantic_fact": self.semantic_fact,
        }


def _infer_emotion(score: float | None) -> str:
    """Rule-based emotion inference from a quiz score."""
    if score is None:
        return ""
    if score >= 0.85:
        return "confident"
    if score <= 0.4:
        return "frustrated"
    return "neutral"


def _infer_scope(concept: str, subject: str) -> MemoryScope:
    """Infer the scope of a memory item from concept/subject presence."""
    if concept:
        return MemoryScope.CONCEPT
    if subject:
        return MemoryScope.SUBJECT
    return MemoryScope.GLOBAL


def classify_turn(*, event_type: str, concept: str = "", subject: str = "",
                   score: float | None = None, note: str = "",
                   user_message: str = "", brief: str = "") -> list[ClassificationResult]:
    """Classify a single turn's learning signals into memory items.

    Rule-based, deterministic, zero LLM. Returns a list of ClassificationResult
    (possibly empty). The caller (consume_turn) writes episodic items immediately
    and queues semantic candidates for consolidation.
    """
    results: list[ClassificationResult] = []
    concept = (concept or "").strip()
    subject = (subject or "").strip()
    note = (note or "").strip()
    msg = (user_message or "").strip()
    scope = _infer_scope(concept, subject)

    # --- episodic from assessment events ---
    if event_type == "quiz_graded":
        emotion = _infer_emotion(score)
        importance = Importance.HIGH.value if (score is not None and
                                               (score >= 0.85 or score <= 0.4)) \
            else Importance.NORMAL.value
        score_str = f"{int(score * 100)}分" if score is not None else "未评分"
        summary = f"完成「{concept or '测试'}」{score_str}，状态{emotion}" if emotion \
            else f"完成「{concept or '测试'}」{score_str}"
        if note:
            summary += f"（{note[:40]}）"
        results.append(ClassificationResult(
            memory_type="episodic", event_type=event_type, summary=summary,
            concept=concept, subject=subject, score=score, emotion=emotion,
            importance=importance, scope=scope))

    # --- episodic from concept-taught events ---
    elif event_type == "concept_taught":
        summary_brief = brief[:40] if brief else "讲解了一个知识点"
        summary = f"学习了「{concept or summary_brief}」"
        if subject:
            summary += f"（{subject}）"
        results.append(ClassificationResult(
            memory_type="episodic", event_type=event_type, summary=summary,
            concept=concept, subject=subject, importance=Importance.LOW.value,
            scope=scope))

    # NOTE: goals and preferences are NOT classified here. They are M2
    # StudentProfile's domain (handled by EventProcessor._on_goal_set +
    # learning_style). M6 Semantic facts come exclusively from consolidation
    # (M6.5), which extracts behavioral/cognitive patterns from episodic
    # evidence -- never direct from a single user statement.
    return results


def classify_events(events: list[dict[str, Any]],
                    user_message: str = "") -> list[ClassificationResult]:
    """Classify a batch of LearningEvent dicts (from the Supervisor's 6b step).

    Each event dict has: type, ts, payload. Maps to classify_turn calls.
    """
    all_results: list[ClassificationResult] = []
    for ev in events:
        etype = str(ev.get("type", ""))
        payload = ev.get("payload") or {}
        p_concept = str(payload.get("concept") or payload.get("knowledge_point") or "")
        p_subject = str(payload.get("subject") or "")
        p_correct = payload.get("correct")
        p_note = str(payload.get("note") or "")
        p_brief = str(payload.get("brief") or "")
        score = None
        if p_correct is not None:
            score = 1.0 if p_correct else 0.0
        results = classify_turn(
            event_type=etype, concept=p_concept, subject=p_subject,
            score=score, note=p_note, user_message=user_message, brief=p_brief)
        all_results.extend(results)
    return all_results
