"""Compact session-only projection over existing education state stores."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OpenLoop:
    id: str
    kind: str
    description: str
    status: str = "pending"
    source_ref: str = ""
    created_turn: int = 0

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "OpenLoop":
        return cls(id=str(d.get("id", "")), kind=str(d.get("kind", "")),
                   description=str(d.get("description", "")),
                   status=str(d.get("status", "pending")),
                   source_ref=str(d.get("source_ref", "")),
                   created_turn=int(d.get("created_turn", 0)))


@dataclass
class SessionLearningCard:
    version: str = "1.0"
    session_goal: str = ""
    current_subject: str = ""
    active_concepts: list[str] = field(default_factory=list)
    current_mode: str = ""
    explanation_depth: str = ""
    recent_mistakes: list[str] = field(default_factory=list)
    misconceptions: list[str] = field(default_factory=list)
    completed_steps: list[str] = field(default_factory=list)
    remaining_steps: list[str] = field(default_factory=list)
    active_skill_ids: list[str] = field(default_factory=list)
    pending_assessment: bool = False
    unanswered_question_ids: list[str] = field(default_factory=list)
    latest_verdicts: list[str] = field(default_factory=list)
    open_loops: list[OpenLoop] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["open_loops"] = [loop.to_dict() for loop in self.open_loops]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "SessionLearningCard":
        d = d or {}
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        known["open_loops"] = [OpenLoop.from_dict(x) for x in d.get("open_loops", [])
                               if isinstance(x, dict)]
        return cls(**known)

    def upsert_loop(self, loop: OpenLoop) -> None:
        for i, current in enumerate(self.open_loops):
            if current.id == loop.id:
                self.open_loops[i] = loop
                return
        self.open_loops.append(loop)
        self.open_loops = self.open_loops[-8:]

    def render(self, max_chars: int = 1800) -> str:
        lines = ["[当前会话学习卡 · 只读投影]"]
        if self.session_goal: lines.append(f"目标：{self.session_goal}")
        if self.active_concepts: lines.append("当前知识点：" + "、".join(self.active_concepts[-4:]))
        if self.current_mode: lines.append(f"教学模式：{self.current_mode}；讲解深度：{self.explanation_depth or '自适应'}")
        if self.misconceptions: lines.append("需要纠正：" + "；".join(self.misconceptions[-3:]))
        if self.recent_mistakes: lines.append("近期错点：" + "；".join(self.recent_mistakes[-3:]))
        if self.completed_steps: lines.append("已完成：" + "；".join(self.completed_steps[-4:]))
        if self.remaining_steps: lines.append("仍需处理：" + "；".join(self.remaining_steps[-4:]))
        pending = [x.description for x in self.open_loops if x.status == "pending"]
        if pending: lines.append("未完成事项：" + "；".join(pending[-4:]))
        if self.latest_verdicts: lines.append("最近测评证据：" + "；".join(self.latest_verdicts[-4:]))
        lines.append("注意：该卡片只是现有学生模型、测评和会话状态的工作投影，不代表新增掌握度证据。")
        return "\n".join(lines)[:max_chars]


def reconcile_quiz_history(card: SessionLearningCard,
                           quiz_history: list[dict[str, Any]]) -> None:
    unanswered: list[str] = []
    verdicts: list[str] = []
    for qh in quiz_history[-3:]:
        if not isinstance(qh, dict): continue
        for q in qh.get("questions") or []:
            if not isinstance(q, dict): continue
            qid = str(q.get("id", "")) or str(q.get("stem", ""))[:24]
            result = q.get("result")
            if isinstance(result, dict) and result.get("verdict"):
                verdicts.append(f"{qid}:{result['verdict']}")
            else:
                unanswered.append(qid)
    card.unanswered_question_ids = list(dict.fromkeys(unanswered))[-10:]
    card.latest_verdicts = verdicts[-8:]
    card.pending_assessment = bool(card.unanswered_question_ids)
    if not card.pending_assessment:
        for loop in card.open_loops:
            if loop.kind == "student_response" and loop.status == "pending":
                loop.status = "resolved"
    card.updated_at = time.time()
