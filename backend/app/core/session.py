"""TutorSession: conversational state across turns + history persistence.

Holds messages, student grade, knowledge store, quiz history, and trace ids.
Persisted as JSON per session so a user can resume a conversation. Multi-turn
memory is the L3 dynamic layer; old messages are trimmed to manage context.
"""
from __future__ import annotations

import json
import time
import re
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .atomic import atomic_write_text, file_lock
from .config import settings
from .knowledge_store import KnowledgeStore

# chat_history lives at the project root (parent of backend/), so it is
# independent of the backend cwd. Mirror config.py's project-root resolution.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SESSIONS_DIR = _PROJECT_ROOT / "chat_history"
_MAX_SLUG = 30
_FILENAME_PREFIX = "chat_"


def _slugify(text: str) -> str:
    """Filename-safe slug preserving CJK + alnum (no ascii-folding).

    Mirrors Paper_Agent's history_store slug: ids stay human-readable and
    stable across renames (rename mutates the title only, never the id/file).
    """
    text = (text or "").strip()
    kept = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    kept = re.sub(r"\s+", "_", kept).strip("_")
    return kept[:_MAX_SLUG] or "untitled"


def _resolve(session_id: str, ext: str = ".json") -> Path:
    """Resolve a bare session id to a Path under _SESSIONS_DIR.

    Path-traversal guard (Paper_Agent history_store _resolve): strip any
    directory component with .name so a crafted id like '../etc/passwd'
    cannot escape _SESSIONS_DIR. Also tolerates a trailing extension.
    """
    bare = Path(session_id).name
    if bare.endswith(ext):
        bare = bare[: -len(ext)]
    return _SESSIONS_DIR / f"{bare}{ext}"


def session_path(session_id: str) -> Path:
    """Public path helper so external load-modify-save callers can hold the
    SAME per-file lock key that load/save_session use internally."""
    return _resolve(session_id)


def new_session_id(topic: str) -> str:
    """Stable, timestamped id created eagerly at first turn.

    Paper_Agent-style: human-readable datetime prefix for ordering + slug
    suffix for readability. Assigned once and never changed (renames only
    touch the in-file title), so the transcript file + recall_history share
    it from turn 1 onward.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{_FILENAME_PREFIX}{ts}_{_slugify(topic)}"


def derive_title(messages: list[dict[str, Any]], title: str = "") -> str:
    """Best-effort human title (Paper_Agent derive_title). Falls back to the
    first user message when no title was set."""
    if title:
        return title.strip()
    for m in messages or []:
        if isinstance(m, dict) and m.get("role") == "user":
            content = str(m.get("content", "")).strip()
            if content:
                return content.split("\n", 1)[0][:40]
    return "未命名对话"


def add_trace_id(session_id: str, trace_id: str) -> None:
    """Append a trace_id to a session's trace_ids list (Paper_Agent pattern).

    Lightweight: reads only the trace_ids field + rewrites the small list,
    avoiding a full to_persistable rewrite. Idempotent. No-op if the session
    file does not exist (it will be written in full by the next save_session).
    """
    if not trace_id:
        return
    path = _resolve(session_id)
    if not path.exists():
        return
    with file_lock(path):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        ids = list(d.get("trace_ids") or [])
        if trace_id not in ids:
            ids.append(trace_id)
            d["trace_ids"] = ids
            atomic_write_text(path, json.dumps(d, ensure_ascii=False, indent=2))
@dataclass
class TutorSession:
    session_id: str = ""
    grade: str = ""  # P1: "" = 自动（模型按内容自适应学段）；旧会话已存学段值原样保留
    output_language: str | None = None  # None=auto | zh | en (forced)
    workspace_id: str = ""  # workspace (gongzuo xuexi qu) this session belongs to
    student_id: str = ""  # M0: the authenticated user's student namespace key
    compaction: dict | None = None  # {summary, compacted_upto, created_at, summary_tokens}
    supervisor_state: dict | None = None  # V2: cross-turn Supervisor memory (TaskState)
    context_card: dict | None = None  # session-only projection; not a second mastery store
    # W3/D11: 课堂小结与恢复锚点（确定性骨架 + active 模式润色）；会话内
    # 投影，重建自 quiz_history/context_card，不是第二份掌握度存储。
    learning_summary: dict | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    quiz_history: list[dict[str, Any]] = field(default_factory=list)
    trace_ids: list[str] = field(default_factory=list)
    pending_material_file_ids: list[str] = field(default_factory=list)
    title: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    knowledge: KnowledgeStore = field(default_factory=KnowledgeStore)

    def round_count(self) -> int:
        """One agent reply = one round. Upload-only user messages (file
        attachments without an assistant reply) never count."""
        return sum(1 for m in self.messages if m.get("role") == "assistant")

    def context_summary(self) -> str:
        parts = []
        if self.grade:
            parts.append(f"学段={self.grade}")
        if self.knowledge.has_knowledge():
            parts.append(f"已上传资料 {len(self.knowledge.files)} 份 / {len(self.knowledge.chunks)} 片段")
        if self.quiz_history:
            parts.append(f"已出题 {len(self.quiz_history)} 套")
        parts.append(f"对话 {self.round_count()} 轮")
        return " | ".join(parts) if parts else "新会话"

    def to_persistable(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "grade": self.grade,
            "output_language": self.output_language,
           "workspace_id": self.workspace_id,
            "student_id": self.student_id,
           "compaction": self.compaction,
            "supervisor_state": self.supervisor_state,
            "context_card": self.context_card,
            "learning_summary": self.learning_summary,
            "messages": self.messages,
            "quiz_history": self.quiz_history,
            "trace_ids": self.trace_ids,
            "pending_material_file_ids": self.pending_material_file_ids,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "knowledge_files": self.knowledge.file_list(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TutorSession":
        s = cls(
            session_id=d.get("session_id", ""),
            grade=d.get("grade", ""),  # P1: 新会话默认空（自动）；旧会话存值原样保留
            output_language=d.get("output_language"),
           workspace_id=d.get("workspace_id", ""),
            student_id=d.get("student_id", ""),
           compaction=d.get("compaction"),
            supervisor_state=d.get("supervisor_state"),
            context_card=d.get("context_card"),
            learning_summary=d.get("learning_summary"),
            messages=d.get("messages", []),
            quiz_history=d.get("quiz_history", []),
            trace_ids=d.get("trace_ids", []),
            pending_material_file_ids=d.get("pending_material_file_ids", []),
            title=d.get("title", ""),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
        )
        for f in d.get("knowledge_files", []):
            s.knowledge.files.append(f)
            fp = s.knowledge.upload_dir / f"{f['id']}.txt"
            if fp.exists():
                from .structured_chunker import chunks_from_meta
                text = fp.read_text(encoding="utf-8")
                s.knowledge.chunks.extend(
                    chunks_from_meta(text, source=f["filename"],
                                     file_id=f["id"], meta=f))
        return s


def save_session(session: TutorSession) -> str:
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    session.updated_at = time.time()
    if not session.session_id:
        session.session_id = new_session_id(session.title or "untitled")
    if not session.title:
        session.title = derive_title(session.messages, session.title)
    path = _resolve(session.session_id)
    is_new = not path.exists()
    with file_lock(path):
        atomic_write_text(path, json.dumps(session.to_persistable(), ensure_ascii=False, indent=2))
    # M6 prompt-memory session window is global across ordinary/workspace
    # conversations. Register at the first durable session write; the helper is
    # best-effort and deliberately does not affect persistence if unavailable.
    if session.student_id and is_new:
        try:
            from ..agents.memory.prompt_memory import register_session
            boundary = register_session(session.student_id, session.session_id,
                                        session.workspace_id)
            if boundary.get("needs_compaction"):
                try:
                    import asyncio
                    from ..agents.memory import get_memory_service
                    from .llm_async import get_llm
                    loop = asyncio.get_running_loop()
                    loop.create_task(get_memory_service().maybe_compact_prompt_memory(
                        session.student_id, llm=get_llm()))
                except RuntimeError:
                    pass
        except Exception:
            pass
        if session.workspace_id:
            try:
                import asyncio
                from .workspace_memory import compact_workspace_memory_on_new_session
                loop = asyncio.get_running_loop()
                loop.create_task(compact_workspace_memory_on_new_session(
                    session.workspace_id, session.session_id))
            except RuntimeError:
                pass
            except Exception:
                pass
    return session.session_id


def load_session(session_id: str) -> TutorSession | None:
    path = _resolve(session_id)
    if not path.exists():
        return None
    return TutorSession.from_dict(json.loads(path.read_text(encoding="utf-8")))


# 会话摘要缓存：path -> (mtime, summary)。列表接口每次调用都要读全部
# 会话文件（含完整消息体），文件未变时复用上次解析结果，O(文件数) stat
# 代替 O(总字节) 读+解析。dict 读写 GIL 原子，竞态最坏是重复解析一次。
_session_summary_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _session_summary(p) -> dict[str, Any] | None:
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    key = str(p)
    cached = _session_summary_cache.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    item = {
        "session_id": d.get("session_id", p.stem),
        "workspace_id": d.get("workspace_id", ""),
        "student_id": d.get("student_id", ""),
        "grade": d.get("grade", ""),
        "title": d.get("title", "") or "未命名对话",
        "message_count": len(d.get("messages", [])),
        # 轮数口径：一次 agent 回复算一轮（纯上传消息不计）。
        "round_count": sum(1 for m in d.get("messages", [])
                           if m.get("role") == "assistant"),
        "quiz_count": len(d.get("quiz_history", [])),
        "file_count": len(d.get("knowledge_files", [])),
        "updated_at": d.get("updated_at", 0),
    }
    _session_summary_cache[key] = (mtime, item)
    return item


def list_sessions() -> list[dict[str, Any]]:
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    seen: set[str] = set()
    for p in sorted(_SESSIONS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        item = _session_summary(p)
        if item is None:
            continue
        seen.add(str(p))
        out.append(item)
    # 清理已删除会话的残留缓存项，防无界增长。
    if len(seen) != len(_session_summary_cache):
        for key in [k for k in _session_summary_cache if k not in seen]:
            _session_summary_cache.pop(key, None)
    return out


def delete_session(session_id: str) -> bool:
    path = _resolve(session_id)
    knowledge_files: list[dict[str, Any]] = []
    with file_lock(path):
        if not path.exists():
            return False
        try:
            knowledge_files = list(json.loads(path.read_text(encoding="utf-8")).get(
                "knowledge_files", []))
        except Exception:
            knowledge_files = []
        path.unlink()
    # Session-private materials (extracted text + originals/OCR images) must not
    # survive deletion or become visible through another session.
    try:
        upload_dir = KnowledgeStore().upload_dir
        for meta in knowledge_files:
            fid = Path(str(meta.get("id", ""))).name
            if not fid:
                continue
            candidates = [upload_dir / f"{fid}.txt"]
            orig_ext = str(meta.get("orig_ext") or "")
            if orig_ext:
                candidates.append(upload_dir / f"{fid}.orig{orig_ext}")
            for fp in candidates:
                if fp.exists():
                    fp.unlink()
    except Exception:
        pass
    # Drop the session's vectors (best-effort; the vector track may be off).
    try:
        from . import vector_store
        vector_store.delete_scope(f"session:{session_id}")
    except Exception:
        pass
    return True


def rename_session(session_id: str, title: str) -> bool:
    path = _resolve(session_id)
    with file_lock(path):
        if not path.exists():
            return False
        d = json.loads(path.read_text(encoding="utf-8"))
        d["title"] = title
        atomic_write_text(path, json.dumps(d, ensure_ascii=False, indent=2))
    return True


def set_session_grade(session_id: str, grade: str) -> bool:
    """Targeted atomic write of the session's grade (P1: 会话内切换学段持久化).

    Mirrors ``rename_session``: rewrites only the grade field, never re-chunking
    the whole session (a full load/save would rebuild BM25 chunks for no reason).
    Returns False when the session file is missing.
    """
    path = _resolve(session_id)
    with file_lock(path):
        if not path.exists():
            return False
        d = json.loads(path.read_text(encoding="utf-8"))
        d["grade"] = grade
        atomic_write_text(path, json.dumps(d, ensure_ascii=False, indent=2))
    return True
