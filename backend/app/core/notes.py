"""Per-student notes vault (M-Notes): Obsidian-style markdown knowledge base.

Storage layout (mirrors library.py / session.py conventions — JSON index +
per-note markdown files, atomic writes under file locks, path-traversal
guarded student keys):

  notes/<student_key>/vault.json                      仓库索引（全量重写）
  notes/<student_key>/notes/<note_id>.md              笔记正文（纯内容）
  notes/<student_key>/revisions/<note_id>/<file>      版本快照（每笔记上限 20）
  notes/<student_key>/agent/<note_id>.json            每笔记专属智能体状态
                                                      （模式 / 对话历史 / 计划批复）
  notes/<student_key>/threads/                        （遗留）旧线程模型；
                                                      首次加载某笔记历史时按
                                                      context.note_id 一次性迁入
  notes/<student_key>/uploads/                        笔记页上传附件（文本+原件）

设计要点：
  - 元数据（标题/文件夹/标签/来源/复习状态）只存索引；.md 文件是纯正文，
    导出时再拼 YAML frontmatter（Obsidian 可直接导入）。
  - wiki 链接 ``[[标题]]`` / ``[[标题|别名]]`` 按标题解析（重名取最近更新），
    解析不到的是"未解析链接"（图上的幽灵节点，可一键建笔记）。
  - 每次写入（用户保存 / Agent 直写 / 版本恢复）都追加修订快照，
    revision 号即乐观并发版本：base_revision 不匹配抛 StaleRevisionError。
  - 笔记智能体按笔记隔离：agent/<note_id>.json 各自独立（模式、对话历史、
    待批复计划），note_id 为空的仓库级对话存 agent/_vault.json。
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Iterator

from .atomic import atomic_write_text, file_lock
from .notes_templates import BUILT_IN_TEMPLATES

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_NOTES_DIR = _PROJECT_ROOT / "notes"

_MAX_TITLE = 120
_MAX_FOLDER_NAME = 60
_MAX_TAGS = 12
_MAX_TAG_LEN = 24
_MAX_CONTENT_CHARS = 400_000          # ~一本小册子的容量上限，防滥用
_MAX_REVISIONS = 20
_MAX_AGENT_MESSAGES = 200             # 每笔记智能体历史上限，超出截头
_MAX_CUSTOM_TEMPLATES = 20

_WIKILINK_RE = re.compile(r"\[\[([^\[\]|]+)(?:\|([^\[\]]+))?\]\]")
# 资源 id 不含标点：遇空白、括号/引号及中英文标点即截断，
# 防止把正文旁注（如「（第一次想明白了）。」）或「、」分隔的下一条链接吞进 resource_id。
_RES_STOP = r"\s)\]}>),;:!?\"'、。，；：？！（）《》〈【】〔〕“”‘’…—"
_RESOURCE_LINK_RE = re.compile(
    rf"(?P<url>(?:note://[^{_RES_STOP}]+|conversation://(?:session|notes)/[^{_RES_STOP}]+))")
_TAG_RE = re.compile(r"(^|\s)#([A-Za-z0-9_\-\u4e00-\u9fff]{1,24})")
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


class StaleRevisionError(Exception):
    """乐观并发冲突：笔记已被他人（或 Agent）先行修改。"""

    def __init__(self, note: dict[str, Any], content: str) -> None:
        super().__init__("笔记已被更新，请先查看最新内容")
        self.note = note
        self.content = content


def _default_student_id() -> str:
    from ..agents.student_model.store import DEFAULT_STUDENT_ID
    return DEFAULT_STUDENT_ID


def _key(student_id: str) -> str:
    """Filesystem-safe student key (traversal guard); "" maps to the guest."""
    bare = Path(student_id or "").name
    return bare or _default_student_id()


def _vault_dir(student_id: str) -> Path:
    return _NOTES_DIR / _key(student_id)


def _index_path(student_id: str) -> Path:
    return _vault_dir(student_id) / "vault.json"


def _notes_dir(student_id: str) -> Path:
    return _vault_dir(student_id) / "notes"


def _revisions_dir(student_id: str, note_id: str) -> Path:
    return _vault_dir(student_id) / "revisions" / Path(note_id).name


def _threads_dir(student_id: str) -> Path:
    return _vault_dir(student_id) / "threads"


def _thread_index_path(student_id: str) -> Path:
    return _threads_dir(student_id) / "index.json"


def _legacy_thread_path(student_id: str) -> Path:
    return _threads_dir(student_id) / "agent.json"


def _thread_tombstones_path(student_id: str) -> Path:
    return _threads_dir(student_id) / "deleted.json"


def _thread_path(student_id: str, thread_id: str = "default") -> Path:
    safe = Path(thread_id or "default").name
    return _threads_dir(student_id) / f"{safe}.json"


def _suggestions_path(student_id: str) -> Path:
    return _vault_dir(student_id) / "suggestions.json"


def _slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", (text or ""), flags=re.UNICODE).strip()
    slug = re.sub(r"[\s_-]+", "_", slug)
    return slug[:30] or "note"


def new_note_id(title: str) -> str:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return f"note_{stamp}_{_slugify(title)}"


def _now() -> float:
    return time.time()


def hashlib_sha1(text: str) -> str:
    import hashlib
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def word_count(text: str) -> int:
    """CJK 友好的字数：去空白后的字符数。"""
    return len(re.sub(r"\s", "", text or ""))


def sanitize_tags(tags: Any) -> list[str]:
    if not isinstance(tags, (list, tuple)):
        return []
    out: list[str] = []
    for t in tags:
        name = str(t or "").strip().lstrip("#")[:_MAX_TAG_LEN]
        if name and name not in out:
            out.append(name)
        if len(out) >= _MAX_TAGS:
            break
    return out


# --- wiki 链接解析 -----------------------------------------------------------


def strip_code(content: str) -> str:
    """链接/标签扫描前剔除代码块与行内代码，避免误解析。"""
    text = _CODE_FENCE_RE.sub("", content or "")
    return _INLINE_CODE_RE.sub("", text)


def parse_wikilinks(content: str) -> list[tuple[str, str]]:
    """[(标题, 别名)]，按出现顺序（别名缺省为标题）。"""
    out: list[tuple[str, str]] = []
    for m in _WIKILINK_RE.finditer(strip_code(content)):
        title = m.group(1).strip()
        if not title:
            continue
        alias = (m.group(2) or "").strip() or title
        out.append((title, alias))
    return out


def parse_resource_links(content: str) -> list[dict[str, str]]:
    """扫描稳定资源链接，保留正文但返回可解析的资源类型与 ID。"""
    out: list[dict[str, str]] = []
    for match in _RESOURCE_LINK_RE.finditer(strip_code(content)):
        url = match.group("url")
        if url.startswith("note://"):
            out.append({"type": "note", "resource_id": url[7:], "url": url})
        elif url.startswith("conversation://session/"):
            out.append({"type": "session", "resource_id": url.split("/", 3)[-1], "url": url})
        else:
            out.append({"type": "notes_thread", "resource_id": url.split("/", 3)[-1], "url": url})
    return out


def dedupe_knowledge_cards(content: str) -> str:
    """Remove duplicate generated knowledge-card blocks by stable fingerprint."""
    lines = str(content or "").splitlines()
    seen: set[str] = set()
    out: list[str] = []
    card: list[str] = []
    fp = ""

    def flush() -> None:
        nonlocal card, fp
        if not card:
            return
        if fp and fp in seen:
            while out and not out[-1].strip():
                out.pop()
        else:
            if fp:
                seen.add(fp)
            out.extend(card)
        card, fp = [], ""

    for line in lines:
        if line.startswith("> [知识卡] 来源："):
            flush()
            card = [line]
            continue
        if card:
            card.append(line)
            match = re.search(r"knowledge-card:([a-f0-9]{8,64})", line)
            if match:
                fp = match.group(1)
            if not line.startswith(">") and line.strip():
                trailing = card.pop()
                flush()
                out.append(trailing)
            continue
        out.append(line)
    flush()
    return "\n".join(out).rstrip() + ("\n" if content.endswith("\n") else "")


def parse_inline_tags(content: str) -> list[str]:
    """正文内联 #标签（派生信号，不落索引）。"""
    seen: list[str] = []
    for m in _TAG_RE.finditer(content or ""):
        name = m.group(2)
        if name and name not in seen:
            seen.append(name)
    return seen


# --- 仓库 -------------------------------------------------------------------


class NoteVault:
    """一个学生的笔记仓库：文件夹 + 笔记元数据 + 自定义模板。"""

    def __init__(self, student_id: str) -> None:
        self.student_id = student_id
        self.folders: list[dict[str, Any]] = []
        self.notes: list[dict[str, Any]] = []
        self.custom_templates: list[dict[str, Any]] = []
        # note_id -> (content mtime, content)
        self._content_cache: dict[str, tuple[float, str]] = {}

    # --- lookups ---

    def find_note(self, note_id: str) -> dict[str, Any] | None:
        return next((n for n in self.notes if n.get("id") == note_id), None)

    def find_folder(self, folder_id: str) -> dict[str, Any] | None:
        return next((f for f in self.folders if f.get("id") == folder_id), None)

    def folder_note_count(self, folder_id: str) -> int:
        return sum(1 for n in self.notes if n.get("folder_id") == folder_id)

    def note_path(self, note_id: str) -> Path:
        return _notes_dir(self.student_id) / f"{Path(note_id).name}.md"

    # --- folders ---

    def create_folder(self, name: str, parent_id: str = "") -> dict[str, Any]:
        if parent_id and self.find_folder(parent_id) is None:
            raise ValueError("父文件夹不存在")
        folder = {
            "id": "nf" + uuid.uuid4().hex[:8],
            "name": (name or "").strip()[:_MAX_FOLDER_NAME] or "未命名文件夹",
            "parent_id": parent_id or "",
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.folders.append(folder)
        return folder

    def folder_by_name(self, name: str) -> dict[str, Any] | None:
        return next((f for f in self.folders if f.get("name") == name), None)

    def ensure_folder(self, name: str, parent_id: str = "") -> dict[str, Any]:
        return self.folder_by_name(name) or self.create_folder(name, parent_id)

    def rename_folder(self, folder_id: str, name: str) -> bool:
        folder = self.find_folder(folder_id)
        if folder is None:
            return False
        folder["name"] = (name or "").strip()[:_MAX_FOLDER_NAME] or folder["name"]
        folder["updated_at"] = _now()
        return True

    def folder_descendants(self, folder_id: str) -> set[str]:
        descendants: set[str] = set()
        frontier = [folder_id]
        while frontier:
            parent = frontier.pop()
            for folder in self.folders:
                fid = str(folder.get("id") or "")
                if folder.get("parent_id", "") == parent and fid not in descendants:
                    descendants.add(fid)
                    frontier.append(fid)
        return descendants

    def move_folder(self, folder_id: str, parent_id: str) -> bool:
        folder = self.find_folder(folder_id)
        if folder is None or (parent_id and self.find_folder(parent_id) is None):
            return False
        if parent_id == folder_id or parent_id in self.folder_descendants(folder_id):
            raise ValueError("不能将文件夹移动到自身或其子文件夹")
        folder["parent_id"] = parent_id or ""
        folder["updated_at"] = _now()
        return True

    def delete_folder(self, folder_id: str) -> dict[str, int] | None:
        """安全删除：笔记和直接子文件夹上移到被删文件夹的父级。"""
        folder = self.find_folder(folder_id)
        if folder is None:
            return None
        parent_id = str(folder.get("parent_id") or "")
        moved_notes = moved_folders = 0
        now = _now()
        for note in self.notes:
            if note.get("folder_id") == folder_id:
                note["folder_id"] = parent_id
                note["updated_at"] = now
                moved_notes += 1
        for child in self.folders:
            if child.get("parent_id", "") == folder_id:
                child["parent_id"] = parent_id
                child["updated_at"] = now
                moved_folders += 1
        self.folders = [f for f in self.folders if f.get("id") != folder_id]
        return {"moved_notes": moved_notes, "moved_folders": moved_folders}

    # --- notes ---

    def create_note(self, title: str, content: str = "", folder_id: str = "",
                    template_id: str = "", tags: Any = None,
                    source: dict[str, Any] | None = None,
                    review_enabled: bool = False,
                    status: str = "active", author: str = "user",
                    note_id: str | None = None) -> dict[str, Any]:
        note_id = note_id or new_note_id(title)
        while self.find_note(note_id) is not None:
            note_id = f"{new_note_id(title)}_{uuid.uuid4().hex[:4]}"
        if folder_id and self.find_folder(folder_id) is None:
            folder_id = ""
        now = _now()
        meta: dict[str, Any] = {
            "id": note_id,
            "title": (title or "").strip()[:_MAX_TITLE] or "未命名笔记",
            "folder_id": folder_id or "",
            "tags": sanitize_tags(tags),
            "template_id": template_id or "",
            "status": status if status in ("draft", "active", "archived") else "active",
            "revision": 1,
            "source": dict(source or {}),
            "review": {
                "enabled": bool(review_enabled),
                "next_review_at": 0.0,
                "easiness": 2.5,
                "interval": 0,
                "repetitions": 0,
            },
            "created_at": now,
            "updated_at": now,
            "created_by": author,
            "word_count": word_count(content),
        }
        self.notes.append(meta)
        self._write_content(note_id, content)
        self._snapshot_revision(meta, content, author=author, summary="创建笔记")
        return meta

    def read_note(self, note_id: str) -> str:
        if self.find_note(note_id) is None:
            return ""
        return self._read_content(note_id)

    def _read_content(self, note_id: str) -> str:
        hit = self._content_cache.get(note_id)
        path = self.note_path(note_id)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return ""
        if hit is not None and hit[0] == mtime:
            return hit[1]
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            return ""
        self._content_cache[note_id] = (mtime, text)
        return text

    def _write_content(self, note_id: str, content: str) -> None:
        path = self.note_path(note_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, content[:_MAX_CONTENT_CHARS])
        try:
            self._content_cache[note_id] = (path.stat().st_mtime,
                                            content[:_MAX_CONTENT_CHARS])
        except OSError:
            pass

    def _snapshot_revision(self, meta: dict[str, Any], content: str, *,
                           author: str, summary: str) -> None:
        """追加修订快照（写入后的内容），保留最近 _MAX_REVISIONS 份。"""
        rev = int(meta.get("revision") or 1)
        rdir = _revisions_dir(self.student_id, meta["id"])
        rdir.mkdir(parents=True, exist_ok=True)
        fname = f"{rev:04d}_{int(_now())}_{Path(author or 'user').name}.md"
        atomic_write_text(rdir / fname, content)
        try:
            snapshots = sorted(p for p in rdir.iterdir() if p.is_file())
            for stale in snapshots[:-_MAX_REVISIONS]:
                stale.unlink(missing_ok=True)
        except OSError:
            pass

    def write_note(self, note_id: str, content: str, *, author: str = "user",
                   base_revision: int | None = None,
                   summary: str = "编辑笔记") -> dict[str, Any]:
        """写正文（乐观并发 + 修订快照）。冲突抛 StaleRevisionError。"""
        meta = self.find_note(note_id)
        if meta is None:
            raise FileNotFoundError("笔记不存在")
        if base_revision is not None and int(base_revision) != int(meta.get("revision") or 1):
            raise StaleRevisionError(self.note_summary(meta), self._read_content(note_id))
        self._write_content(note_id, content)
        meta["revision"] = int(meta.get("revision") or 1) + 1
        meta["updated_at"] = _now()
        meta["word_count"] = word_count(content)
        self._snapshot_revision(meta, content, author=author, summary=summary)
        return meta

    def rename_note(self, note_id: str, title: str, *,
                    update_links: bool = True) -> dict[str, Any]:
        """重命名；默认同步改写全仓库指向旧标题的 wiki 链接（Obsidian 行为）。"""
        meta = self.find_note(note_id)
        if meta is None:
            raise FileNotFoundError("笔记不存在")
        new_title = (title or "").strip()[:_MAX_TITLE]
        if not new_title:
            raise ValueError("标题不能为空")
        old_title = str(meta.get("title") or "")
        changed_links = 0
        if update_links and old_title and old_title != new_title:
            pattern = re.compile(
                r"\[\[" + re.escape(old_title) + r"(\|[^\[\]]*)?\]\]")
            for other in self.notes:
                if other["id"] == note_id:
                    continue
                text = self._read_content(other["id"])
                if not text or "[[" not in text:
                    continue
                rewritten, n = pattern.subn(
                    lambda m: f"[[{new_title}{m.group(1) or ''}]]", text)
                if n:
                    other["revision"] = int(other.get("revision") or 1) + 1
                    other["updated_at"] = _now()
                    self._write_content(other["id"], rewritten)
                    self._snapshot_revision(
                        other, rewritten, author="user",
                        summary=f"链接改写：{old_title} → {new_title}")
                    changed_links += n
        meta["title"] = new_title
        meta["updated_at"] = _now()
        return {**self.note_summary(meta), "links_rewritten": changed_links}

    def move_note(self, note_id: str, folder_id: str) -> bool:
        meta = self.find_note(note_id)
        if meta is None:
            return False
        if folder_id and self.find_folder(folder_id) is None:
            return False
        meta["folder_id"] = folder_id or ""
        meta["updated_at"] = _now()
        return True

    def set_note_meta(self, note_id: str, *, tags: Any = None,
                      status: str = "") -> dict[str, Any] | None:
        meta = self.find_note(note_id)
        if meta is None:
            return None
        if tags is not None:
            meta["tags"] = sanitize_tags(tags)
        if status in ("draft", "active", "archived"):
            meta["status"] = status
        meta["updated_at"] = _now()
        return self.note_summary(meta)

    def remove_note(self, note_id: str) -> bool:
        """彻底移除（正文 + 修订 + 元数据 + 专属智能体状态）。归档请走 trash.archive_note。"""
        meta = self.find_note(note_id)
        if meta is None:
            return False
        self.notes = [n for n in self.notes if n.get("id") != note_id]
        self._content_cache.pop(note_id, None)
        try:
            self.note_path(note_id).unlink(missing_ok=True)
        except OSError:
            pass
        shutil.rmtree(_revisions_dir(self.student_id, note_id), ignore_errors=True)
        delete_agent_history(self.student_id, note_id)
        return True

    # --- revisions ---

    def list_revisions(self, note_id: str) -> list[dict[str, Any]] | None:
        if self.find_note(note_id) is None:
            return None
        rdir = _revisions_dir(self.student_id, note_id)
        out: list[dict[str, Any]] = []
        if rdir.is_dir():
            for p in rdir.iterdir():
                if not p.is_file() or p.name.startswith("."):
                    continue
                m = re.match(r"^(\d+)_(\d+)_([^\.]+)\.md$", p.name)
                if not m:
                    continue
                try:
                    words = word_count(p.read_text(encoding="utf-8",
                                                   errors="ignore"))
                except OSError:
                    words = 0
                out.append({
                    "revision": int(m.group(1)),
                    "ts": float(m.group(2)),
                    "author": m.group(3),
                    "word_count": words,
                })
        out.sort(key=lambda r: r["revision"], reverse=True)
        return out

    def read_revision(self, note_id: str, revision: int) -> str | None:
        if self.find_note(note_id) is None:
            return None
        rdir = _revisions_dir(self.student_id, note_id)
        if not rdir.is_dir():
            return None
        prefix = f"{int(revision):04d}_"
        for p in sorted(rdir.iterdir()):
            if p.is_file() and p.name.startswith(prefix):
                try:
                    return p.read_text(encoding="utf-8")
                except OSError:
                    return None
        return None

    def restore_revision(self, note_id: str, revision: int) -> dict[str, Any]:
        content = self.read_revision(note_id, revision)
        if content is None:
            raise FileNotFoundError("版本不存在")
        return self.write_note(note_id, content, author="user",
                               summary=f"恢复自版本 {int(revision)}")

    # --- 视图 / 派生信号 ---

    def note_summary(self, meta: dict[str, Any]) -> dict[str, Any]:
        return {k: meta.get(k) for k in (
            "id", "title", "folder_id", "tags", "template_id", "status",
            "revision", "source", "review", "created_at", "updated_at",
            "created_by", "word_count")}

    def summaries(self, *, status: str = "") -> list[dict[str, Any]]:
        out = [self.note_summary(n) for n in self.notes
               if not status or n.get("status") == status]
        out.sort(key=lambda n: float(n.get("updated_at") or 0), reverse=True)
        return out

    def _title_index(self) -> dict[str, dict[str, Any]]:
        """标题 -> 笔记元数据（重名取最近更新）。"""
        idx: dict[str, dict[str, Any]] = {}
        for n in sorted(self.notes,
                        key=lambda x: float(x.get("updated_at") or 0)):
            title = str(n.get("title") or "").strip()
            if title:
                idx[title] = n
        return idx

    def _resource_link(self, link: dict[str, str]) -> dict[str, Any]:
        kind = link["type"]
        rid = Path(link["resource_id"]).name
        base: dict[str, Any] = {"type": kind, "resource_id": rid,
                                "url": link["url"], "status": "missing",
                                "resolved": False, "title": rid}
        if kind == "note":
            note = self.find_note(rid)
            if note is not None:
                folder = self.find_folder(str(note.get("folder_id") or ""))
                base.update({"status": "resolved", "resolved": True,
                             "title": note.get("title") or rid,
                             "folder_id": note.get("folder_id") or "",
                             "folder_name": (folder or {}).get("name", ""),
                             "updated_at": note.get("updated_at") or 0})
        elif kind == "session":
            try:
                from .session import load_session
                session = load_session(rid)
                if session is not None and (getattr(session, "student_id", "") or _default_student_id()) == self.student_id:
                    base.update({"status": "resolved", "resolved": True,
                                 "title": getattr(session, "title", "") or rid,
                                 "message_count": len(getattr(session, "messages", []) or []),
                                 "updated_at": getattr(session, "updated_at", 0) or 0})
            except Exception:
                pass
        else:
            # 旧线程模型已下线（2026-09 每笔记专属智能体重构）；
            # conversation://notes/<id> 链接不再可解析，按缺失展示。
            pass
        if not base["resolved"]:
            try:
                from .trash import list_items
                resource_type = "notes_note" if kind == "note" else ("session" if kind == "session" else "notes_thread")
                deleted = next((item for item in list_items(self.student_id)
                                if str(item.get("original_id") or "") == rid
                                and item.get("resource_type") == resource_type), None)
                if deleted is not None:
                    base["status"] = "deleted"
                    base["title"] = deleted.get("title") or base["title"]
            except Exception:
                pass
        return base

    def resolve_links(self, content: str) -> dict[str, Any]:
        """解析 wiki 与稳定资源链接；旧 resolved/unresolved 字段保持兼容。"""
        idx = self._title_index()
        resolved: list[dict[str, Any]] = []
        unresolved: list[str] = []
        seen_titles: set[str] = set()
        for title, _alias in parse_wikilinks(content):
            target = idx.get(title)
            if target is not None:
                if title not in seen_titles:
                    seen_titles.add(title)
                    resolved.append({"title": title, "note_id": target["id"]})
            elif title not in unresolved:
                unresolved.append(title)
        resources: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for link in parse_resource_links(content):
            if link["url"] not in seen_urls:
                seen_urls.add(link["url"])
                resources.append(self._resource_link(link))
        return {"resolved": resolved, "unresolved": unresolved, "resources": resources}

    def backlinks(self, note_id: str) -> list[dict[str, Any]]:
        """反向链接：哪些笔记的正文链接到了本笔记。"""
        meta = self.find_note(note_id)
        if meta is None:
            return []
        title = str(meta.get("title") or "").strip()
        if not title:
            return []
        pattern = re.compile(r"\[\[" + re.escape(title) + r"(\|[^\[\]]*)?\]\]")
        out: list[dict[str, Any]] = []
        for other in self.notes:
            if other["id"] == note_id:
                continue
            text = self._read_content(other["id"])
            if text and pattern.search(strip_code(text)):
                out.append(self.note_summary(other))
        out.sort(key=lambda n: float(n.get("updated_at") or 0), reverse=True)
        return out

    def tag_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for n in self.notes:
            for t in n.get("tags") or []:
                counts[t] = counts.get(t, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def link_graph(self) -> dict[str, Any]:
        """笔记关系图：笔记 + 被引用的会话/教材 + 幽灵节点。

        只放「被笔记引用」的外部资源：会话经正文 conversation://session/...
        资源链接引入，教材经 source.textbook_ids 引入；未被引用的会话/教材
        以及笔记助手线程不进图。
        """
        idx = self._title_index()
        folder_names = {str(f.get("id") or ""): str(f.get("name") or "") for f in self.folders}
        nodes: list[dict[str, Any]] = [
            {"id": n["id"], "title": n.get("title", ""), "kind": "note",
             "folder_id": n.get("folder_id", ""),
             "folder_name": folder_names.get(str(n.get("folder_id") or ""), ""),
             "tags": list(n.get("tags") or []), "ghost": False,
             "status": "resolved", "updated_at": n.get("updated_at") or 0}
            for n in self.notes]
        # 教材节点：派生自笔记 source.textbook_ids（生成时记录的教材来源），
        # 教材索引不可用时静默降级——图谱其余部分不受影响。
        textbooks: dict[str, dict[str, Any]] = {}
        try:
            from .textbook import load_textbooks
            textbooks = {str(t.get("id") or ""): t
                         for t in load_textbooks(self.student_id)
                         if str(t.get("id") or "").strip()}
        except Exception:
            pass
        edges: list[dict[str, Any]] = []
        node_ids = {str(n["id"]) for n in nodes}
        ghosts: dict[str, str] = {}
        for note in self.notes:
            content = self._read_content(note["id"])
            for title, _alias in parse_wikilinks(content):
                target = idx.get(title)
                if target is not None and target["id"] != note["id"]:
                    edges.append({"source": note["id"], "target": target["id"],
                                  "title": title, "resolved": True, "kind": "note"})
                elif target is None:
                    if title not in ghosts:
                        ghosts[title] = f"ghost:{hashlib_sha1(title)}"
                        nodes.append({"id": ghosts[title], "title": title, "kind": "ghost",
                                      "folder_id": "", "tags": [], "ghost": True,
                                      "status": "unresolved", "updated_at": 0})
                    edges.append({"source": note["id"], "target": ghosts[title],
                                  "title": title, "resolved": False, "kind": "unresolved"})
            for raw in parse_resource_links(content):
                if raw["type"] == "notes_thread":
                    continue  # 助手线程不进关系图
                resource = self._resource_link(raw)
                target_id = f"{resource['type']}:{resource['resource_id']}"
                if resource["type"] == "note" and resource["resolved"]:
                    target_id = resource["resource_id"]
                elif target_id not in node_ids:
                    nodes.append({"id": target_id, "title": resource["title"],
                                  "kind": resource["type"], "folder_id": resource.get("folder_id", ""),
                                  "folder_name": resource.get("folder_name", ""), "tags": [],
                                  "ghost": not resource["resolved"], "status": resource["status"],
                                  "message_count": resource.get("message_count", 0),
                                  "updated_at": resource.get("updated_at", 0),
                                  "resource_id": resource["resource_id"]})
                    node_ids.add(target_id)
                if target_id != note["id"]:
                    edges.append({"source": note["id"], "target": target_id,
                                  "title": resource["title"], "resolved": resource["resolved"],
                                  "kind": resource["type"], "status": resource["status"]})
            source = note.get("source") or {}
            seen_textbooks: set[str] = set()
            for raw_tid in source.get("textbook_ids") or []:
                tid = str(raw_tid).strip()
                if not tid or tid in seen_textbooks:
                    continue
                seen_textbooks.add(tid)
                record = textbooks.get(tid)
                tb_title = str((record or {}).get("title") or tid)
                tb_node_id = f"textbook:{tid}"
                if tb_node_id not in node_ids:
                    nodes.append({"id": tb_node_id, "resource_id": tid,
                                  "title": tb_title, "kind": "textbook",
                                  "folder_id": "", "tags": [],
                                  "ghost": record is None,
                                  "status": "resolved" if record is not None else "missing",
                                  "updated_at": (record or {}).get("updated_at", 0)})
                    node_ids.add(tb_node_id)
                edges.append({"source": note["id"], "target": tb_node_id,
                              "title": tb_title, "resolved": record is not None,
                              "kind": "textbook",
                              "status": "resolved" if record is not None else "missing"})
        return {"nodes": nodes, "edges": edges}

    def vault_outline(self, limit: int = 60) -> list[dict[str, Any]]:
        """供提示词注入的仓库概览（标题 + 文件夹 + 标签）。"""
        folder_names = {f.get("id"): f.get("name", "") for f in self.folders}
        out = []
        for n in sorted(self.notes,
                        key=lambda x: float(x.get("updated_at") or 0),
                        reverse=True)[:limit]:
            out.append({
                "title": n.get("title", ""),
                "folder": folder_names.get(n.get("folder_id"), ""),
                "tags": list(n.get("tags") or []),
            })
        return out

    def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """标题 > 标签 > 全文的简单加权检索。"""
        q = (query or "").strip().lower()
        if not q:
            return []
        scored: list[tuple[int, dict[str, Any]]] = []
        for n in self.notes:
            title = str(n.get("title") or "").lower()
            tags = [str(t).lower() for t in (n.get("tags") or [])]
            score = 0
            if q in title:
                score += 10 + (10 if title.startswith(q) else 0)
            if any(q in t for t in tags):
                score += 6
            if score == 0 and q in self._read_content(n["id"]).lower():
                score += 3
            if score:
                scored.append((score, self.note_summary(n)))
        scored.sort(key=lambda kv: (-kv[0],
                                    -float(kv[1].get("updated_at") or 0)))
        return [n for _s, n in scored[:limit]]

    # --- custom templates ---

    def add_custom_template(self, name: str, content: str) -> dict[str, Any]:
        if len(self.custom_templates) >= _MAX_CUSTOM_TEMPLATES:
            raise ValueError("自定义模板数量已达上限")
        tpl = {
            "id": "ct_" + uuid.uuid4().hex[:8],
            "name": (name or "").strip()[:_MAX_TITLE] or "未命名模板",
            "content": (content or "")[:_MAX_CONTENT_CHARS],
            "created_at": _now(),
        }
        self.custom_templates.append(tpl)
        return tpl

    def remove_custom_template(self, template_id: str) -> bool:
        before = len(self.custom_templates)
        self.custom_templates = [t for t in self.custom_templates
                                 if t.get("id") != template_id]
        return len(self.custom_templates) < before

    # --- 序列化 ---

    def to_persistable(self) -> dict[str, Any]:
        return {
            "student_id": self.student_id,
            "folders": self.folders,
            "notes": self.notes,
            "custom_templates": self.custom_templates,
            "version": 2,
            "updated_at": _now(),
        }

    @classmethod
    def from_dict(cls, student_id: str, raw: dict[str, Any]) -> "NoteVault":
        vault = cls(student_id)
        vault.folders = list(raw.get("folders") or [])
        for folder in vault.folders:
            folder.setdefault("parent_id", "")
        vault.notes = list(raw.get("notes") or [])
        vault.custom_templates = list(raw.get("custom_templates") or [])
        return vault


_SEED_FOLDER_NAMES: tuple[str, ...] = tuple(dict.fromkeys(
    t.folder_hint for t in BUILT_IN_TEMPLATES.values() if t.folder_hint))


def load_vault(student_id: str) -> NoteVault:
    """加载仓库；首次访问播种默认文件夹。损坏索引降级为空仓库。"""
    vault = NoteVault(student_id)
    path = _index_path(student_id)
    try:
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                vault = NoteVault.from_dict(student_id, raw)
    except Exception:
        vault = NoteVault(student_id)
    if not vault.folders and not vault.notes:
        for name in _SEED_FOLDER_NAMES:
            vault.folders.append({
                "id": "nf" + uuid.uuid4().hex[:8],
                "name": name,
                "parent_id": "",
                "created_at": _now(),
                "updated_at": _now(),
            })
        try:
            save_vault(vault)
        except Exception:
            pass
    return vault


def save_vault(vault: NoteVault) -> None:
    _vault_dir(vault.student_id).mkdir(parents=True, exist_ok=True)
    path = _index_path(vault.student_id)
    with file_lock(path):
        atomic_write_text(path, json.dumps(
            vault.to_persistable(), ensure_ascii=False))


def vault_summary(vault: NoteVault) -> dict[str, Any]:
    """GET /notes/vault 的投影。"""
    graph = vault.link_graph()
    link_count = sum(1 for e in graph["edges"] if e.get("resolved"))
    # 未解析面板的语义是「还没有对应笔记的 wikilink，点击创建」；
    # 会话/教材等资源 ghost 属于关系图视图，不进该列表。
    unresolved = sorted({e["title"] for e in graph["edges"]
                         if e.get("kind") == "unresolved"})
    due = [n for n in vault.notes
           if (n.get("review") or {}).get("enabled")
           and 0 < float((n.get("review") or {}).get("next_review_at") or 0) <= _now()]
    return {
        "folders": [
            {**f, "note_count": vault.folder_note_count(f.get("id", ""))}
            for f in vault.folders],
        "notes": vault.summaries(),
        "tags": vault.tag_counts(),
        "custom_templates": vault.custom_templates,
        "stats": {
            "note_count": len(vault.notes),
            "folder_count": len(vault.folders),
            "link_count": link_count,
            "unresolved_links": unresolved,
            "due_review_count": len(due),
            "due_review_ids": [n["id"] for n in due],
        },
    }


# --- 笔记智能体（每笔记专属） ----------------------------------------------------
# 2026-09 重构：对话历史从「线程模型」改为「每笔记一份专属状态」——每个笔记
# 拥有独立的模式（ask/plan/authorize）、对话历史与待批复计划（一次性批复
# 状态机 pending→approved→executed / rejected），智能体只能写当前绑定的笔记。
# 旧线程文件（threads/*.json）在首次加载某笔记历史时按 context.note_id
# 一次性迁入，之后只读不再写入；建议队列（suggestions.json，协作模式）已
# 随协作模式一并移除。

AGENT_MODES = ("ask", "plan", "authorize")
_LEGACY_AGENT_MODES = {"suggest": "plan", "collab": "plan",
                       "cowrite": "authorize", "auto": "authorize"}
PLAN_STATUSES = ("pending", "approved", "rejected", "executed")


def normalize_agent_mode(mode: str) -> str:
    """归一化智能体模式；旧四模式值映射到语义最近的新三模式，未知回落 ask。"""
    value = str(mode or "").strip().lower()
    value = _LEGACY_AGENT_MODES.get(value, value)
    return value if value in AGENT_MODES else "ask"


def _agent_dir(student_id: str) -> Path:
    return _vault_dir(student_id) / "agent"


def _agent_state_path(student_id: str, note_id: str) -> Path:
    # note_id 为空 = 仓库级对话（未打开具体笔记时的助手）。笔记 id 均为
    # note_<stamp>_<slug> 形态，_vault 前缀不会与之冲突。
    safe = Path(note_id or "").name or "_vault"
    return _agent_dir(student_id) / f"{safe}.json"


def _idle_working() -> dict[str, Any]:
    return {"stage": "idle", "tool": "", "started_at": 0, "can_stop": False}


def _new_agent_state(student_id: str, note_id: str) -> dict[str, Any]:
    now = _now()
    return {"student_id": student_id, "note_id": note_id, "mode": "ask",
            "messages": [], "pending_plan": None,
            "working": _idle_working(),
            "created_at": now, "updated_at": now}


def _legacy_thread_messages_for_note(student_id: str,
                                     note_id: str) -> list[dict[str, Any]]:
    """从旧线程文件收集归属于该笔记的消息（一次性迁移读取）。

    旧模型里 note_id 只是消息 context 元数据、线程与笔记是 N:N；新模型里
    每笔记的智能体专有历史，因此按 context.note_id 过滤、按 ts 排序合并。
    scope=vault 的无主消息不迁移（不属于任何笔记的专属智能体）。
    """
    out: list[dict[str, Any]] = []
    if not note_id:
        return out
    tdir = _threads_dir(student_id)
    if not tdir.is_dir():
        return out
    for path in sorted(tdir.glob("*.json")):
        if path.name in {"index.json", "deleted.json"}:
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        for m in raw.get("messages") or []:
            if not isinstance(m, dict):
                continue
            ctx = m.get("context") or {}
            if str(ctx.get("note_id") or "") != note_id:
                continue
            out.append({
                "role": m.get("role") if m.get("role") in ("user", "assistant") else "user",
                "content": str(m.get("content") or ""),
                "context": dict(ctx), "ts": float(m.get("ts") or 0),
            })
    out.sort(key=lambda m: m["ts"])
    return out


def _write_agent_state(student_id: str, state: dict[str, Any]) -> None:
    state["updated_at"] = _now()
    path = _agent_state_path(student_id, str(state.get("note_id") or ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        atomic_write_text(path, json.dumps(state, ensure_ascii=False))


def load_agent_history(student_id: str, note_id: str = "") -> dict[str, Any]:
    """读取某笔记的专属智能体状态；文件不存在时建新并懒迁移旧线程。"""
    note_id = str(note_id or "").strip()
    state: dict[str, Any] | None = None
    path = _agent_state_path(student_id, note_id)
    try:
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                state = raw
    except Exception:
        state = None
    if state is None:
        state = _new_agent_state(student_id, note_id)
        migrated = _legacy_thread_messages_for_note(student_id, note_id)
        if migrated:
            state["messages"] = migrated[-_MAX_AGENT_MESSAGES:]
            last_mode = next(
                (str((m.get("context") or {}).get("mode") or "")
                 for m in reversed(migrated)
                 if (m.get("context") or {}).get("mode")), "")
            state["mode"] = normalize_agent_mode(last_mode)
            _write_agent_state(student_id, state)  # 落盘即完成迁移标记
    state.setdefault("note_id", note_id)
    state["mode"] = normalize_agent_mode(str(state.get("mode") or ""))
    state.setdefault("messages", [])
    state["messages"] = state["messages"][-_MAX_AGENT_MESSAGES:]
    state.setdefault("pending_plan", None)
    state.setdefault("working", _idle_working())
    return state


def agent_history_view(student_id: str, note_id: str = "") -> dict[str, Any]:
    state = load_agent_history(student_id, note_id)
    return {"note_id": state.get("note_id") or str(note_id or ""),
            "mode": state.get("mode") or "ask",
            "messages": state.get("messages") or [],
            "pending_plan": state.get("pending_plan"),
            "working": state.get("working") or {},
            "created_at": state.get("created_at") or 0,
            "updated_at": state.get("updated_at") or 0}


def append_agent_message(student_id: str, note_id: str, role: str,
                         content: str, context: dict[str, Any] | None = None
                         ) -> dict[str, Any]:
    """追加一条该笔记智能体的消息（上限 _MAX_AGENT_MESSAGES，超出截头）。"""
    state = load_agent_history(student_id, note_id)
    state["messages"].append({
        "role": role if role in ("user", "assistant") else "user",
        "content": str(content or ""), "context": dict(context or {}),
        "ts": _now(),
    })
    state["messages"] = state["messages"][-_MAX_AGENT_MESSAGES:]
    _write_agent_state(student_id, state)
    return state


def set_agent_working(student_id: str, note_id: str, *, stage: str,
                      tool: str = "", can_stop: bool = False,
                      run_id: str = "") -> None:
    state = load_agent_history(student_id, note_id)
    state["working"] = {"stage": stage, "tool": tool,
                        "started_at": (_now() if stage != "idle" else 0),
                        "can_stop": bool(can_stop), "run_id": run_id}
    _write_agent_state(student_id, state)


def set_agent_mode(student_id: str, note_id: str, mode: str) -> dict[str, Any]:
    """设置该笔记智能体的模式（服务端枚举校验，堵客户端自报越权）。"""
    state = load_agent_history(student_id, note_id)
    state["mode"] = normalize_agent_mode(mode)
    _write_agent_state(student_id, state)
    return state


def set_pending_plan(student_id: str, note_id: str,
                     plan: dict[str, Any] | None) -> dict[str, Any]:
    """写入/覆盖该笔记的待批复计划（plan 模式解析出计划卡后调用）。"""
    state = load_agent_history(student_id, note_id)
    state["pending_plan"] = plan
    _write_agent_state(student_id, state)
    return state


def update_pending_plan_status(student_id: str, note_id: str, status: str,
                               **extra: Any) -> dict[str, Any] | None:
    """推进计划批复状态机（pending→approved→executed / rejected）。"""
    if status not in PLAN_STATUSES:
        return None
    state = load_agent_history(student_id, note_id)
    plan = state.get("pending_plan")
    if not isinstance(plan, dict):
        return None
    plan["status"] = status
    for key, value in extra.items():
        if value is not None:
            plan[key] = value
    _write_agent_state(student_id, state)
    return plan


def clear_agent_history(student_id: str, note_id: str = "") -> None:
    """清空该笔记智能体的对话历史与待批复计划（保留模式选择）。"""
    state = load_agent_history(student_id, note_id)
    state["messages"] = []
    state["pending_plan"] = None
    state["working"] = _idle_working()
    _write_agent_state(student_id, state)


def delete_agent_history(student_id: str, note_id: str) -> bool:
    """笔记被删除/归档时清理其专属智能体状态。"""
    if not note_id:
        return False
    try:
        _agent_state_path(student_id, note_id).unlink(missing_ok=True)
        return True
    except OSError:
        return False



# --- 助手附件（笔记页上传的资料/图片） ------------------------------------------


_MAX_UPLOAD_FILES = 40


def _uploads_dir(student_id: str) -> Path:
    return _vault_dir(student_id) / "uploads"


def _uploads_manifest(student_id: str) -> Path:
    return _vault_dir(student_id) / "uploads.json"


def uploads_vector_scope(student_id: str) -> str:
    """笔记附件在向量库中的 scope（hybrid 检索轨道按它过滤）。"""
    return f"notes:{_key(student_id)}"


def load_uploads_store(student_id: str):
    """重建附件 KnowledgeStore：清单只存元数据，chunks 从 uploads/<fid>.txt
    惰性重建（与会话知识库同一模式）。"""
    from .knowledge_store import KnowledgeStore
    from .retriever import chunk_text

    store = KnowledgeStore(upload_dir=_uploads_dir(student_id))
    manifest = _uploads_manifest(student_id)
    if not manifest.exists():
        return store
    try:
        files = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        files = []
    if not isinstance(files, list):
        files = []
    for f in files[-_MAX_UPLOAD_FILES:]:
        meta = dict(f)
        store.files.append(meta)
        fp = store.upload_dir / f"{meta.get('id', '')}.txt"
        if fp.exists():
            text = fp.read_text(encoding="utf-8")
            store.chunks.extend(chunk_text(
                text, source=str(meta.get("filename", "")),
                file_id=str(meta.get("id", ""))))
    return store


def add_upload_file(student_id: str, file_id: str, filename: str, text: str,
                    *, raw: bytes | None = None, orig_ext: str = "",
                    metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """写入一份附件（提取文本 + 可选原件），并原子更新清单。返回文件元数据。"""
    from .knowledge_store import KnowledgeStore

    store = KnowledgeStore(upload_dir=_uploads_dir(student_id))
    meta = store.add_file(file_id, filename, text, raw=raw,
                          orig_ext=orig_ext, metadata=metadata or {})
    path = _uploads_manifest(student_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            items = []
        if not isinstance(items, list):
            items = []
        items.append({k: v for k, v in meta.items() if k != "chunks"})
        atomic_write_text(path, json.dumps(items[-_MAX_UPLOAD_FILES:],
                                           ensure_ascii=False))
    return meta


# --- 导出 ---------------------------------------------------------------------


def _iso(ts: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError):
        return ""


def _yaml_quote(value: str) -> str:
    text = str(value or "").replace('"', '\\"')
    return f'"{text}"'


def export_markdown(meta: dict[str, Any], content: str) -> str:
    """带 YAML frontmatter 的单篇导出（Obsidian 可直接导入）。"""
    lines = ["---",
             f"title: {_yaml_quote(meta.get('title', ''))}",
             f"tags: [{', '.join(_yaml_quote(t) for t in (meta.get('tags') or []))}]"]
    if meta.get("template_id"):
        lines.append(f"template: {_yaml_quote(meta.get('template_id'))}")
    lines += [f"created: {_yaml_quote(_iso(meta.get('created_at')))}",
              f"updated: {_yaml_quote(_iso(meta.get('updated_at')))}",
              "---", ""]
    return "\n".join(lines) + (content or "")


def _export_filename(meta: dict[str, Any]) -> str:
    title = _slugify(str(meta.get("title") or "note"))
    return f"{title}.md"


def export_zip(vault: NoteVault, folder_id: str = "",
               note_ids: list[str] | None = None) -> Path:
    """导出为 zip（整库或按文件夹/指定笔记），返回临时文件路径（调用方清理）。"""
    if note_ids is not None:
        wanted = set(note_ids)
        selected = [n for n in vault.notes if n["id"] in wanted]
    elif folder_id:
        selected = [n for n in vault.notes if n.get("folder_id") == folder_id]
    else:
        selected = list(vault.notes)
    folder_names = {f.get("id"): _slugify(str(f.get("name") or "folder"))
                    for f in vault.folders}
    used: set[str] = set()

    def unique(name: str) -> str:
        base, dot, ext = name.rpartition(".")
        candidate, i = name, 1
        while candidate in used:
            i += 1
            candidate = f"{base}_{i}{dot}{ext}"
        used.add(candidate)
        return candidate

    fd, tmp = tempfile.mkstemp(prefix="notes_export_", suffix=".zip")
    import os as _os
    _os.close(fd)
    tmp_path = Path(tmp)
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for meta in selected:
            arcdir = folder_names.get(meta.get("folder_id"), "")
            name = _export_filename(meta)
            arcname = f"{arcdir}/{name}" if arcdir else name
            zf.writestr(unique(arcname),
                        export_markdown(meta, vault._read_content(meta["id"])))
    return tmp_path


def iter_all_note_ids(student_id: str) -> Iterator[str]:
    for n in load_vault(student_id).notes:
        yield str(n.get("id") or "")
