"""Learning episodes: the launch binding between a plan task and a chat
session (W4/A12, updatePlan.md §8.2/§8.5).

A LearningEpisode is a recoverable learning segment bound to ONE daily task.
Launching a task from the plan creates (or resumes) an episode plus a
pre-created chat session carrying the ``task_binding``, so answers graded in
that session attribute to exactly that task (只更新绑定任务) instead of
concept-name matching across today's tasks. Relaunching an uncompleted task
is idempotent: the same episode and session come back.

Storage: ``students/<id>.learning_episodes.json`` (§8.5 explicitly sanctions
this file as a rebuildable projection, not an authoritative ledger). It lives
under the students/ root with the ``<owner>.`` prefix, so the generic account
purge (core/account_data.purge_account) and the orphan sweep
(core/orphan_cleanup._collect_orphans) already cover it — pinned by
regression tests, no category additions needed.

Fail-open like every storage module: a corrupt/missing file is "no episodes
yet" and never breaks a turn or a grading call.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .atomic import atomic_write_text, file_lock

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_STUDENTS_DIR = _PROJECT_ROOT / "students"

EPISODE_ACTIVE = "active"
EPISODE_COMPLETED = "completed"
EPISODE_ABANDONED = "abandoned"
# active -> completed/abandoned are the only transitions; terminal episodes
# are kept read-only for audit (resume relaunches never resurrect them — a
# completed task launches a fresh episode via the task being re-created).
_EPISODE_STATUSES = (EPISODE_ACTIVE, EPISODE_COMPLETED, EPISODE_ABANDONED)
_MAX_EPISODES = 200  # FIFO trim by updated_at; episodes are a projection


@dataclass
class LearningEpisode:
    """One task-scoped learning segment (§8.2, minimal W4 slice)."""
    episode_id: str = ""
    task_id: str = ""
    goal_id: str = ""
    session_id: str = ""
    concept_id: str = ""
    title: str = ""
    status: str = EPISODE_ACTIVE
    revision: int = 1
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"episode_id": self.episode_id, "task_id": self.task_id,
                "goal_id": self.goal_id, "session_id": self.session_id,
                "concept_id": self.concept_id, "title": self.title,
                "status": self.status, "revision": self.revision,
                "created_at": self.created_at, "updated_at": self.updated_at}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "LearningEpisode":
        d = d or {}
        status = str(d.get("status", EPISODE_ACTIVE))
        return cls(
            episode_id=str(d.get("episode_id", "")),
            task_id=str(d.get("task_id", "")),
            goal_id=str(d.get("goal_id", "")),
            session_id=str(d.get("session_id", "")),
            concept_id=str(d.get("concept_id", "")),
            title=str(d.get("title", "")),
            status=status if status in _EPISODE_STATUSES else EPISODE_ACTIVE,
            revision=int(d.get("revision", 1)),
            created_at=float(d.get("created_at", time.time())),
            updated_at=float(d.get("updated_at", time.time())))


def _safe(value: str) -> str:
    return Path(str(value or "")).name


def _path(student_id: str) -> Path:
    return _STUDENTS_DIR / f"{_safe(student_id)}.learning_episodes.json"


def _load_all(student_id: str) -> dict[str, dict[str, Any]]:
    path = _path(student_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("episodes"), dict):
            return {str(k): v for k, v in data["episodes"].items()
                    if isinstance(v, dict)}
    except Exception:
        pass
    return {}


def _save_all(student_id: str, episodes: dict[str, dict[str, Any]]) -> bool:
    try:
        _STUDENTS_DIR.mkdir(parents=True, exist_ok=True)
        if len(episodes) > _MAX_EPISODES:
            keep = sorted(episodes.items(), key=lambda kv: float(kv[1].get("updated_at", 0)),
                          reverse=True)[:_MAX_EPISODES]
            episodes = dict(keep)
        path = _path(student_id)
        with file_lock(path):
            atomic_write_text(path, json.dumps(
                {"version": 1, "episodes": episodes}, ensure_ascii=False))
        return True
    except Exception:
        return False


def get_episode(student_id: str, episode_id: str) -> LearningEpisode | None:
    """Read one episode. None when missing/corrupt; never raises."""
    try:
        raw = _load_all(student_id).get(episode_id)
        return LearningEpisode.from_dict(raw) if raw else None
    except Exception:
        return None


def find_active_for_task(student_id: str, task_id: str) -> LearningEpisode | None:
    """The active episode bound to a task (relaunch resumes it)."""
    try:
        for raw in _load_all(student_id).values():
            ep = LearningEpisode.from_dict(raw)
            if ep.task_id == task_id and ep.status == EPISODE_ACTIVE:
                return ep
    except Exception:
        pass
    return None


def create_episode(student_id: str, *, task_id: str, goal_id: str = "",
                   session_id: str = "", concept_id: str = "",
                   title: str = "") -> LearningEpisode:
    """Create and persist a new active episode for a task.

    Callers wanting relaunch-idempotency should check find_active_for_task
    first (the launch endpoint does); this always appends a fresh episode so
    a re-created task gets a clean segment. Never raises.
    """
    ep = LearningEpisode(
        episode_id="ep_" + uuid.uuid4().hex[:12], task_id=task_id,
        goal_id=goal_id, session_id=session_id, concept_id=concept_id,
        title=title[:40])
    try:
        episodes = _load_all(student_id)
        episodes[ep.episode_id] = ep.to_dict()
        _save_all(student_id, episodes)
    except Exception:
        pass
    return ep


def set_episode_status(student_id: str, episode_id: str, status: str) -> bool:
    """Advance an ACTIVE episode to completed/abandoned (bumps revision).

    Terminal episodes stay untouched (idempotent no-op returning False) —
    history is audit, not mutable state. Never raises.
    """
    try:
        if status not in (EPISODE_COMPLETED, EPISODE_ABANDONED):
            return False
        episodes = _load_all(student_id)
        raw = episodes.get(episode_id)
        if not raw:
            return False
        ep = LearningEpisode.from_dict(raw)
        if ep.status != EPISODE_ACTIVE:
            return False
        ep.status = status
        ep.revision += 1
        ep.updated_at = time.time()
        episodes[episode_id] = ep.to_dict()
        return _save_all(student_id, episodes)
    except Exception:
        return False
